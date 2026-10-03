"""任务执行逻辑（M3 建立，M4 接入统一结果模型）。

把 ``job_steps`` 逐个跑掉，并把每一步的结果、进度、错误码写回数据库。
本模块不关心「谁在跑」（worker 进程还是测试协程），因此可以在单元测试里
直接同步调用 :func:`execute_job`。

执行约定：

* 每个步骤之间检查一次取消标记，保证 cancel 能在一个步骤边界生效；
* 每个步骤结束后重算进度并写库，刷新页面即可看到最新进度；
* mock 模式绝不调用真实 runner（方案第 2.3 节第 9 条）；
* real 模式必须已经过 Scope + 环境开关双重校验（在 API 层完成）。

M4 起 real 步骤改走 runner 的统一入口 :meth:`modules.base.BaseRunner.run`：

* 失败 / 超时 / 零结果分开记录（方案 M4 验收项）；
* stdout/stderr 落盘为 artifact，并把 ``artifact_id`` 写进 ``job_steps``；
* 结构化观测（httpx 的状态码/标题/技术栈等）写进 ``observations_json``。
"""

from __future__ import annotations

import os
import time

from core import artifacts as artifacts_store
from core import assets as assets_store
from core import jobs as jobs_store
from core import observability
from core.errors import ErrorCode
from core.job_limits import JobLimits, apply_to_runner as apply_limits_to_runner
from core.mock import run_mock
from core.pace import DEFAULT_PACE, apply_to_runner, step_delay_for
from core.runner_result import PARSER_VERSION

# 步骤结果状态 → 任务聚合状态权重
_FAILED_STEP_STATUSES = {jobs_store.STEP_FAILED, jobs_store.STEP_TIMEOUT}

# mock 场景名 → 步骤状态
_MOCK_STATUS_TO_STEP = {
    "success": jobs_store.STEP_SUCCEEDED,
    "partial": jobs_store.STEP_SUCCEEDED,
    "failed": jobs_store.STEP_FAILED,
    "timeout": jobs_store.STEP_TIMEOUT,
}


def _execute_mock_step(step: dict, scenario: str | None) -> dict:
    """mock 步骤：产出确定性的假数据。"""
    outcome = run_mock(step["tool_name"], step["target"], scenario)
    return {
        "step_status": _MOCK_STATUS_TO_STEP.get(outcome.status, jobs_store.STEP_FAILED),
        "found_count": outcome.found_count,
        "results": outcome.results,
        "observations": [],
        "error_code": outcome.error_code,
        "error_message": outcome.error_message,
        "exit_code": 0 if outcome.error_code is None else 1,
        "artifact_id": None,
        "duration_ms": None,
        "command_preview": None,
        "parser_version": None,
    }


def _failed_outcome(error_code: str, message: str) -> dict:
    """构造一个「未执行即失败」的步骤结果。

    工具未注册、执行前 Scope 复检失败等都属于这一类：没有 exit_code、没有证据，
    但错误码必须明确（方案第 6.2 节：失败不能被吞成空结果）。
    """
    return {
        "step_status": jobs_store.STEP_FAILED,
        "found_count": 0,
        "results": [],
        "observations": [],
        "error_code": error_code,
        "error_message": message,
        "exit_code": None,
        "artifact_id": None,
        "duration_ms": None,
        "command_preview": None,
        "parser_version": None,
    }


def _execute_real_step(
    step: dict,
    scope_id: str | None = None,
    pace: str = DEFAULT_PACE,
    limits: JobLimits | None = None,
) -> dict:
    """real 步骤：调用真实 runner 的统一入口 ``run()``。

    真实执行前由 API 层完成 Scope 与环境开关校验；这里在执行**之前**再复检一次
    （方案第 5.3 节「每个 Step 执行前」），落盘原始证据并把结果归一化成
    ``job_steps`` 的形状。

    ``pace``（见 :mod:`core.pace`）在**构造之后**写进 runner 的 ``config`` 副本
    （:func:`core.pace.apply_to_runner`）：低频档因此真的会变成 ``-t 5 -rl 3``
    这样的命令行参数，而不是页面上的一个标签。

    ``limits``（见 :mod:`core.job_limits`）是 Phase 3 的「限速配置 / 超时配置」，
    在 **``pace`` 之后**用 ``min`` 合并，因此它的方向只能是更保守：

    * 低频档已经写下 ``rate_limit=10`` 时，请求里写 ``rate_limit=50`` 不会把它
      顶回去（:func:`core.job_limits.apply_to_runner` 取较小值）；
    * ``timeout_seconds`` 映射到工具配置的 ``process_timeout``，
      ``modules/base.py:_timeout_seconds()`` 最后还会与
      ``SCAN_LIMITS["process_timeout"]`` 取一次较小值。

    为什么覆盖 ``config`` 而不是换一条构造路径：``build_runner(tool_name)``
    是测试替换真实 Runner 的接缝（``monkeypatch.setattr``）。改成
    ``build_scoped_runner(name, pace)`` 会让所有假 Runner 失效、真的去执行外部
    命令 —— 那正是「测试期不许打真实目标」这条硬约束最容易被绕过的地方。

    ``run()`` 内部已经接住 ``SystemExit`` / ``FileNotFoundError`` /
    ``TimeoutError`` 等异常（否则会直接把 worker 进程带走），因此这里
    只需要处理「执行前复检失败」与「工具未注册」两种前置情况。
    """
    from modules.registry import build_runner

    tool_name = step["tool_name"]
    target = step["target"]

    # ── 执行前的 Scope 复检（方案第 5.3 节「每个 Step 执行前」） ──
    # Job 创建到真正执行之间，Scope 可能已被删除或收紧。worker 必须重新读一次
    # Scope 并重校验，而不是相信创建时的快照；越界目标绝不允许进入 Runner。
    from core.errors import AppError
    from core.policy import validate_step_target

    try:
        target = validate_step_target(scope_id, target)
    except AppError as exc:
        # Scope 缺失（400）与目标越界（403）在执行期同样是「不许执行」。
        return _failed_outcome(ErrorCode.SCOPE_VIOLATION, f"执行前 Scope 复检失败：{exc.message}")
    except Exception as exc:  # noqa: BLE001 - 复检本身出错时按未知错误处理，不放行
        return _failed_outcome(ErrorCode.UNKNOWN_ERROR, f"执行前 Scope 复检异常：{exc}")

    if tool_name not in _known_tools():
        return _failed_outcome(ErrorCode.TOOL_NOT_FOUND, f"工具未注册: {tool_name}")

    try:
        runner = build_runner(tool_name)
        # 节奏在真正发起请求的这一层生效。覆盖失败（例如假 Runner 没有 config）
        # 绝不能影响执行本身，因此单独兜底。
        try:
            apply_to_runner(runner, pace)
        except Exception:  # noqa: BLE001 - 降速是策略，不是执行前提
            pass
        # Phase 3：单任务的限速 / 超时（只能收紧）。放在 ``pace`` 之后 ——
        # 两者都是「更保守者胜」，顺序不影响结果，但读起来是「先按档位、
        # 再按本次任务的显式数字」，与页面上的呈现顺序一致。
        try:
            apply_limits_to_runner(runner, limits)
        except Exception:  # noqa: BLE001 - 收紧是策略，不是执行前提
            pass
        result = runner.run(target)
    except Exception as exc:  # noqa: BLE001 - 兜底：任何异常都不能带走 worker
        return _failed_outcome(ErrorCode.UNKNOWN_ERROR, f"{type(exc).__name__}: {exc}")

    outcome = result.to_step_outcome()

    # 原始证据落盘。三类都要存：
    #   stdout —— 工具正常打到标准输出的内容；
    #   stderr —— 失败原因（脱敏后才会给前端看）；
    #   output —— `-o` 指向的结果文件本身。
    # 很多工具（subfinder / dnsx / httpx）结果只写在文件里、stdout 为空，
    # 只存 stdout 会让「跑通了但没数据」缺少最关键的一份证据。
    last_execution = getattr(runner, "last_execution", {}) or {}
    output_file = _output_file_of(runner)
    sources = (
        ("stdout", last_execution.get("stdout")),
        ("stderr", last_execution.get("stderr") or getattr(result, "stderr_preview", None)),
        ("output", _read_text_file(output_file)),
    )
    for kind, content in sources:
        saved = artifacts_store.save_artifact(
            content,
            kind=kind,
            job_id=step.get("job_id"),
            step_id=step["id"],
            tool_name=tool_name,
            target=target,
        )
        if saved and outcome["artifact_id"] is None:
            # 首个成功落盘的证据作为该步骤的主 artifact_id。
            outcome["artifact_id"] = saved["id"]

    outcome.setdefault("parser_version", PARSER_VERSION)
    return outcome


# 结果文件只读这么多字节作为证据，避免把超大文件整份读进内存。
MAX_RESULT_EVIDENCE_BYTES = 1024 * 1024


def _output_file_of(runner) -> str | None:
    """安全地取出 runner 上次执行的结果文件路径。"""
    getter = getattr(runner, "output_file_from_execution", None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:  # noqa: BLE001 - 证据采集不允许影响任务结果
        return None


def _read_text_file(path: str | None) -> str | None:
    """读取结果文件内容（不存在/不可读时返回 ``None``）。"""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_RESULT_EVIDENCE_BYTES)
    except OSError:
        return None
    if not raw:
        return None
    return raw.decode("utf-8", errors="replace")


_TOOL_CACHE: list[str] | None = None


def _known_tools() -> list[str]:
    """注册表中的工具名（缓存，避免每步都重建 runner）。"""
    global _TOOL_CACHE
    if _TOOL_CACHE is None:
        try:
            from modules.registry import get_supported_runners

            _TOOL_CACHE = list(get_supported_runners())
        except Exception:  # pragma: no cover - 注册表不可用时退化为空
            _TOOL_CACHE = []
    return _TOOL_CACHE


def aggregate_status(step_statuses: list[str], *, cancelled: bool = False) -> tuple[str, str | None]:
    """把若干步骤状态聚合为任务状态。

    Returns:
        tuple[str, str | None]: ``(job_status, error_code)``。
    """
    if cancelled:
        return jobs_store.STATUS_CANCELLED, None

    if not step_statuses:
        return jobs_store.STATUS_SUCCEEDED, None

    failures = [status for status in step_statuses if status in _FAILED_STEP_STATUSES]
    if not failures:
        return jobs_store.STATUS_SUCCEEDED, None
    if len(failures) == len(step_statuses):
        # 全失败：超时与普通失败要分开报（方案 M4 验收项）。
        if all(status == jobs_store.STEP_TIMEOUT for status in step_statuses):
            return jobs_store.STATUS_TIMEOUT, ErrorCode.TIMEOUT
        return jobs_store.STATUS_FAILED, ErrorCode.UNKNOWN_ERROR
    return jobs_store.STATUS_PARTIAL, ErrorCode.PARTIAL_SUCCESS


def execute_job(job_id: str, *, renew=None, step_delay: float = 0.0) -> dict:
    """执行一个已被 worker 领取的任务。

    Args:
        job_id: 任务 ID。
        renew: 可选的续租回调，每完成一个步骤调用一次（``callable() -> None``）。
        step_delay: 每个步骤之间人为 sleep 的秒数（``--step-delay``）。

    Returns:
        dict: 执行后的 job。

    扫描节奏（``core.pace``）**不从这个函数传进来**，而是按 ``job_id`` 从库里
    读回（:func:`core.jobs.pace_of_job`）—— worker 是独立进程，且任务可能被
    retry 或在另一个 worker 上重启，节奏必须是任务自身的属性而不是调用参数。

    方案第 19 节：整段执行都绑定 ``job_id`` 关联上下文，于是执行期内任何一层
    （包括 runner 与派生资产落盘）打的结构化事件都自动带上 job_id，不需要
    逐层透传参数。
    """
    with observability.bind(job_id=job_id):
        return _execute_job(job_id, renew=renew, step_delay=step_delay)


def _execute_job(job_id: str, *, renew=None, step_delay: float = 0.0) -> dict:
    if step_delay < 0:
        raise ValueError("step_delay 不能为负数")
    job = jobs_store.get_job(job_id)
    if job is None:
        raise ValueError(f"任务不存在: {job_id}")

    # retry 之后可能残留 running 步骤，先归位。
    jobs_store.reset_running_steps_on_start(job_id)
    steps = jobs_store.pending_steps(job_id)
    total = len(jobs_store.list_steps(job_id))
    done = total - len(steps)

    mode = job["mode"]
    scenario = job["scenario"]
    # 执行期复检要用 job 上的 scope_id：步骤快照里没有这一列。
    scope_id = job.get("scope_id")

    # ── 扫描节奏（Scan Profile 的第二维，见 core.pace） ──
    # **从库里读回**，不从内存里的创建请求传下来：worker 是另一个进程，
    # 拿不到那次请求的任何内存状态；而且任务被 retry / worker 重启后，
    # 节奏必须仍然是原来那一档（审计与复现都要求它是可查的事实）。
    pace = jobs_store.pace_of_job(job_id)
    # Phase 3「限速配置 / 超时配置」：与 pace 同一个理由从库里读回 —— worker 是
    # 另一个进程，重启后仍必须按同一条任务当时的数字跑。
    limits = jobs_store.limits_of_job(job_id)
    # 低频档的「礼貌间隔」只发生在**真实**步骤之间。mock 不产生任何外部流量，
    # 对它等待只会让演练变慢，不会让任何人少收到一个请求。
    #
    # 刻意**不**与 ``--step-delay`` 合并成 ``max()``：那一个参数是运维在命令行上
    # 显式要求的降速，语义与「这个 Profile 是低频档」不同，两者同时出现时
    # 叠加是更诚实的结果（使用者自己看得见两处设置）。缺省路径下两者都是 0，
    # 因此不带 Profile 的任务一次 sleep 都不会多出来。
    pace_step_delay = step_delay_for(pace) if mode == "real" else 0.0

    # 方案第 19 节：任务开始事件。job_id 绑到本次执行，步骤事件自动继承。
    # 带上 pace：排障时「这个任务为什么这么慢」应当一眼可见，而不是去猜。
    observability.log_event(
        observability.EVENT_JOB_STARTED,
        job_id=job_id,
        mode=mode,
        total_steps=total,
        pending_steps=len(steps),
        pace=pace,
    )

    step_statuses: list[str] = [
        step["status"] for step in jobs_store.list_steps(job_id) if step["status"] != jobs_store.STEP_PENDING
    ]
    cancelled = False
    # 本**轮执行**里是否已经真跑完过至少一步。只用来判断「该不该在两步之间等」：
    # retry 之后 pending_steps 只剩没跑的步骤，第一步入场等待没有意义。
    executed_any = False

    for step in steps:
        if jobs_store.is_cancel_requested(job_id):
            cancelled = True
            break

        # ── 步骤之间的礼貌间隔（低频档） ──
        # 与 ``--step-delay`` 同一个位置、同一层：必须在**真正发起请求之前**，
        # 且不能放进 ``modules/base.py`` —— 那条路绕不到 ``_execute_stdout``、
        # ``shuffledns`` / ``enscan`` 的自定义流程，mock 也根本不过去。
        #
        # 长等待之前先续租：间隔若长过租约，任务会被别的 worker 判成过期而
        # 标成 interrupted。续租**只在真的会等**的时候调用，因此缺省路径
        # （不做任何等待）的回调次数与 Phase 3 之前完全一致。
        if pace_step_delay and executed_any:
            if renew is not None:
                renew()
            deadline = time.monotonic() + pace_step_delay
            while True:
                # 分片睡：等待期间取消仍然最多晚 1 秒生效，
                # 而不是让「取消」被压在一整段长间隔之后。
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if jobs_store.is_cancel_requested(job_id):
                    cancelled = True
                    break
                time.sleep(min(1.0, remaining))
            if cancelled:
                break

        jobs_store.start_step(step["id"])
        executed_any = True
        step_started = time.perf_counter()
        # 方案第 19 节：把 step_id 绑到当前执行流，于是这一步内任何一层
        # （runner / 脱敏 / artifact 落盘）打的结构化事件都自动带 step_id。
        with observability.bind(step_id=step["id"]):
            if mode == "real":
                # real 步骤在执行前重新读一次 Scope 并复检目标（方案第 5.3 节）。
                outcome = _execute_real_step(step, scope_id, pace, limits)
            else:
                outcome = _execute_mock_step(step, scenario)

        duration_ms = outcome.get("duration_ms")
        if duration_ms is None:
            # mock 步骤本身不产出真实耗时（M4 契约：mock 不写假数据，库里
            # duration_ms 必须保持 NULL）。但结构化事件里的 duration_ms 是
            # **真实墙钟测量**，不是伪造值，所以事件里用测出来的值补上。
            duration_ms = int((time.perf_counter() - step_started) * 1000)

        jobs_store.finish_step(
            step["id"],
            status=outcome["step_status"],
            found_count=outcome["found_count"],
            results=outcome["results"],
            error_code=outcome["error_code"],
            error_message=outcome["error_message"],
            exit_code=outcome["exit_code"],
            artifact_id=outcome.get("artifact_id"),
            observations=outcome.get("observations"),
            duration_ms=outcome.get("duration_ms"),
            command_preview=outcome.get("command_preview"),
            parser_version=outcome.get("parser_version"),
        )
        jobs_store.add_event(
            job_id,
            jobs_store.EVENT_STEP_FINISHED,
            {
                "step_id": step["id"],
                "tool_name": step["tool_name"],
                "target": step["target"],
                "status": outcome["step_status"],
                "found_count": outcome["found_count"],
                "error_code": outcome["error_code"],
            },
        )
        # 方案第 19 节的示范事件：一行 JSON，带 job_id / step_id / tool / status /
        # duration_ms。**不记完整目标列表**（target 只记当前这一步的目标）。
        observability.log_event(
            observability.EVENT_JOB_STEP_FINISHED,
            level="INFO" if outcome["step_status"] == jobs_store.STEP_SUCCEEDED else "WARNING",
            job_id=job_id,
            step_id=step["id"],
            tool=step["tool_name"],
            target=step["target"],
            status=outcome["step_status"],
            found_count=outcome["found_count"],
            error_code=outcome["error_code"],
            duration_ms=duration_ms,
            artifact_id=outcome.get("artifact_id"),
        )

        # P1（方案第 8 节）：把这一步的结构化观测落进 assets / observations。
        # **派生产物**——它失败绝不能影响任务结果（原始结果早已写进
        # job_steps 与 artifacts），所以 ingest 内部自行吞掉异常，
        # 失败原因只作为事件记下来，便于排查「资产页为什么少了几条」。
        ingest = assets_store.ingest_step_observations(step, outcome, scope_id=scope_id)
        if ingest["written"] or ingest["skipped"]:
            jobs_store.add_event(
                job_id,
                jobs_store.EVENT_ASSETS_INGESTED,
                {
                    "step_id": step["id"],
                    "tool_name": step["tool_name"],
                    "written": ingest["written"],
                    "skipped": ingest["skipped"],
                    "reasons": ingest["reasons"][:5],
                },
            )

        step_statuses.append(outcome["step_status"])

        done += 1
        progress = jobs_store.update_progress(job_id, done, total)
        jobs_store.add_event(job_id, jobs_store.EVENT_JOB_PROGRESS, {"progress": progress, "done_steps": done})

        if renew is not None:
            renew()
        if step_delay:
            time.sleep(step_delay)

    if cancelled:
        jobs_store.mark_remaining_steps_skipped(job_id)
        jobs_store.finish_job(job_id, status=jobs_store.STATUS_CANCELLED)
        jobs_store.add_event(job_id, jobs_store.EVENT_JOB_CANCELLED, {"reason": "user_requested"})
        observability.log_event(
            observability.EVENT_JOB_FINISHED,
            level="WARNING",
            job_id=job_id,
            status=jobs_store.STATUS_CANCELLED,
            done_steps=done,
            total_steps=total,
        )
        return jobs_store.get_job_or_raise(job_id)

    status, error_code = aggregate_status(step_statuses)
    jobs_store.finish_job(
        job_id,
        status=status,
        error_code=error_code,
        error_message=None if error_code is None else _status_message(status),
    )
    observability.log_event(
        observability.EVENT_JOB_FINISHED,
        level="INFO" if status == jobs_store.STATUS_SUCCEEDED else "WARNING",
        job_id=job_id,
        status=status,
        error_code=error_code,
        done_steps=done,
        total_steps=total,
    )
    return jobs_store.get_job_or_raise(job_id)


def _status_message(status: str) -> str:
    return {
        jobs_store.STATUS_FAILED: "所有步骤均失败",
        jobs_store.STATUS_TIMEOUT: "所有步骤均超时",
        jobs_store.STATUS_PARTIAL: "部分步骤失败",
    }.get(status, "")
