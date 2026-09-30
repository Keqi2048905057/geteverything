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
from core import jobs as jobs_store
from core.errors import ErrorCode
from core.mock import run_mock
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


def _execute_real_step(step: dict, scope_id: str | None = None) -> dict:
    """real 步骤：调用真实 runner 的统一入口 ``run()``。

    真实执行前由 API 层完成 Scope 与环境开关校验；这里在执行**之前**再复检一次
    （方案第 5.3 节「每个 Step 执行前」），落盘原始证据并把结果归一化成
    ``job_steps`` 的形状。

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
        step_delay: 每个步骤之间人为 sleep 的秒数。

    Returns:
        dict: 执行后的 job。
    """
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
    step_statuses: list[str] = [
        step["status"] for step in jobs_store.list_steps(job_id) if step["status"] != jobs_store.STEP_PENDING
    ]
    cancelled = False

    for step in steps:
        if jobs_store.is_cancel_requested(job_id):
            cancelled = True
            break

        jobs_store.start_step(step["id"])
        if mode == "real":
            # real 步骤在执行前重新读一次 Scope 并复检目标（方案第 5.3 节）。
            outcome = _execute_real_step(step, scope_id)
        else:
            outcome = _execute_mock_step(step, scenario)

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
        return jobs_store.get_job(job_id)

    status, error_code = aggregate_status(step_statuses)
    jobs_store.finish_job(
        job_id,
        status=status,
        error_code=error_code,
        error_message=None if error_code is None else _status_message(status),
    )
    return jobs_store.get_job(job_id)


def _status_message(status: str) -> str:
    return {
        jobs_store.STATUS_FAILED: "所有步骤均失败",
        jobs_store.STATUS_TIMEOUT: "所有步骤均超时",
        jobs_store.STATUS_PARTIAL: "部分步骤失败",
    }.get(status, "")
