"""异步任务（job）数据层（M3）。

方案第 5.3、6.1、7 节要求：

* ``POST /api/jobs`` 立即返回 ``job_id`` 与 ``status=queued``，不允许同步等待；
* 状态机：``queued / running / succeeded / partial / failed / timeout / cancelled / interrupted``；
* Web 进程与 worker 进程互不阻塞，靠数据库队列通信；
* worker 重启后 queued/running 任务不会静默消失。

设计要点：

* **单表即队列**：``jobs`` 自己就是队列，靠 ``status + created_at`` 取任务，
  用 ``BEGIN IMMEDIATE`` 保证同一个 job 只会被一个 worker 领到；
* **租约（lease）**：worker 领到任务后写 ``worker_id`` 与 ``lease_until``，
  进程被 kill 后租约过期，重启时由 :func:`recover_stale_jobs` 标成
  ``interrupted``（需要人工 retry），而不是消失；
* **步骤快照**：创建 job 时就把 ``工具 × 目标`` 展开成 ``job_steps``，
  进度因此是可计算的，重启后也能看出「跑到哪一步了」。

本模块不 import Flask，worker 与 API 共用。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from core import db
from core.errors import ErrorCode
from core.ids import new_job_event_id, new_job_id, new_step_id
from core.job_limits import JobLimits, limits_of_detail
from core.pace import coerce_pace

#: ``job.created`` 事件 detail 里承载「公网授权测试」上下文的键（Phase 3）。
#:
#: 这些键**都不是** ``jobs`` 表的列 —— 加列属于 DB 结构变更，而 Phase 3
#: 明确不接受任何结构变更（``docs/DECISIONS.md`` §1 E「限纯增量」）。
#: 它们与 ``pace`` 走同一条路：写进不可改的 ``job_events``，执行期与页面
#: 各自按事件读回。``docs/CODEBASE_MAP.md`` §9.25 记录了这条取舍。
EVENT_KEY_OPERATOR = "operator"
EVENT_KEY_PROJECT_ID = "project_id"
EVENT_KEY_STRATEGY = "strategy"
EVENT_KEY_AUTHORIZATION_NOTE = "authorization_note"
EVENT_KEY_AUTHORIZATION_CONFIRMED = "authorization_confirmed"

#: 操作者标识的最大长度（与 ``core.projects`` 的自由文本上限同量级）。
MAX_OPERATOR_CHARS = 120

#: 授权说明快照的最大长度。取 ``core.projects`` 里 ``authorization_note`` 的
#: 上限（500）：快照**不得**成为绕过项目字段校验的第二条写入口。
MAX_AUTHORIZATION_NOTE_CHARS = 500

#: 没有身份体系时的操作者标识（与 ``jobs.created_by`` 列的历史默认值一致）。
DEFAULT_OPERATOR = "local-admin"

# ── 状态机（方案第 6.1 节） ───────────────────────────────

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"
STATUS_CANCELLED = "cancelled"
STATUS_INTERRUPTED = "interrupted"

ALL_STATUSES = (
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    STATUS_PARTIAL,
    STATUS_FAILED,
    STATUS_TIMEOUT,
    STATUS_CANCELLED,
    STATUS_INTERRUPTED,
)

#: 终态：不会再被 worker 领取。
TERMINAL_STATUSES = (
    STATUS_SUCCEEDED,
    STATUS_PARTIAL,
    STATUS_FAILED,
    STATUS_TIMEOUT,
    STATUS_CANCELLED,
)

#: 可以从这些状态重新排队（retry / 恢复）。
RETRYABLE_STATUSES = (
    STATUS_INTERRUPTED,
    STATUS_FAILED,
    STATUS_TIMEOUT,
    STATUS_CANCELLED,
    STATUS_PARTIAL,
)

#: 合法状态跃迁表（方案第 7 节：不允许 ``queued → succeeded`` 这类跳步）。
#:
#: * ``queued`` 只能被 worker 领成 ``running``，或被直接取消成 ``cancelled``；
#: * 只有 ``running`` 能落到执行结果类终态（``succeeded`` / ``partial`` /
#:   ``failed`` / ``timeout`` / ``cancelled`` / ``interrupted``）；
#: * 终态之间不能互相跳，只能经 retry 回到 ``queued``；
#: * ``succeeded`` 是绝对终态——成功的任务不该被重跑。
#:
#: 同名状态视为**幂等**（重复写同一个终态不算非法跃迁，worker 双写时不会炸）。
ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    STATUS_QUEUED: (STATUS_RUNNING, STATUS_CANCELLED),
    STATUS_RUNNING: (
        STATUS_SUCCEEDED,
        STATUS_PARTIAL,
        STATUS_FAILED,
        STATUS_TIMEOUT,
        STATUS_CANCELLED,
        STATUS_INTERRUPTED,
    ),
    STATUS_INTERRUPTED: (STATUS_QUEUED,),
    STATUS_FAILED: (STATUS_QUEUED,),
    STATUS_TIMEOUT: (STATUS_QUEUED,),
    STATUS_CANCELLED: (STATUS_QUEUED,),
    STATUS_PARTIAL: (STATUS_QUEUED,),
    STATUS_SUCCEEDED: (),
}


def can_transition(from_status: str, to_status: str) -> bool:
    """该状态跃迁是否合法。

    ``from_status == to_status`` 视为幂等，返回 ``True``。
    未知状态一律返回 ``False``（宁可拒绝，也不要放进一个状态机外的值）。
    """
    if from_status == to_status:
        return from_status in ALL_STATUSES
    return to_status in ALLOWED_TRANSITIONS.get(from_status, ())

# ── 步骤状态 ──────────────────────────────────────────────

STEP_PENDING = "pending"
STEP_RUNNING = "running"
STEP_SUCCEEDED = "succeeded"
STEP_FAILED = "failed"
STEP_TIMEOUT = "timeout"
STEP_SKIPPED = "skipped"

# ── 事件类型 ──────────────────────────────────────────────

EVENT_JOB_CREATED = "job.created"
EVENT_JOB_STARTED = "job.started"
EVENT_JOB_PROGRESS = "job.progress"
EVENT_JOB_FINISHED = "job.finished"
EVENT_JOB_CANCEL_REQUESTED = "job.cancel_requested"
EVENT_JOB_CANCELLED = "job.cancelled"
EVENT_JOB_RETRY_REQUESTED = "job.retry_requested"
EVENT_JOB_INTERRUPTED = "job.interrupted"
EVENT_STEP_STARTED = "step.started"
EVENT_STEP_FINISHED = "step.finished"

#: P1（方案第 8 节）：某一步的结构化观测已落进 assets / observations。
#: 记这条事件是为了让「资产页为什么少了几条」可以在任务详情里直接看到原因。
EVENT_ASSETS_INGESTED = "step.assets_ingested"

#: worker 默认租约时长（秒）。超过这个时间没续租，视为 worker 已死。
DEFAULT_LEASE_SECONDS = 60

#: 单个任务允许的最大尝试次数（方案第 7 节「max attempts」）。
#: 达到上限后 retry 会被拒绝，避免手工反复重试把队列刷爆。
MAX_ATTEMPTS = 5

#: 重试退避（方案第 7 节「Retry 必须有上限和退避」）：
#: 第 N 次尝试前的等待 = ``min(BASE * 2 ** (N-1), MAX)``。
#: 指数退避的作用是：工具刚因为网络抖动失败时，不要让 worker 立刻把它
#: 再领起来重跑一遍——那会把「上游故障」放大成「本地也一起雪崩」。
RETRY_BACKOFF_BASE_SECONDS = 5
RETRY_BACKOFF_MAX_SECONDS = 300

#: 幂等键长度上限。超长的键多半是调用方把一个 hash 之外的东西（如整份请求体）
#: 塞进来了，属于用法错误，直接拒绝比存下来更好。
MAX_IDEMPOTENCY_KEY_LENGTH = 200


def retry_backoff_seconds(attempt: int) -> int:
    """第 ``attempt`` 次尝试前的退避秒数（从 1 开始计数）。

    第 1 次（首次执行）没有退避，返回 0；之后 5s / 10s / 20s / 40s …
    封顶 :data:`RETRY_BACKOFF_MAX_SECONDS`。
    """
    if attempt <= 1:
        return 0
    return min(RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 2)), RETRY_BACKOFF_MAX_SECONDS)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    return _utcnow().isoformat(timespec="seconds")


def _future(seconds: int) -> str:
    return (_utcnow() + timedelta(seconds=seconds)).isoformat(timespec="seconds")


# ── 行 → 字典 ─────────────────────────────────────────────


def _job_to_dict(row) -> dict:
    # 旧库可能还没有 P0-7 新增的两列（``ensure_schema`` 会补，但只读路径
    # 在补列之前也可能被调用），因此按列名存在性读取，缺列时退化为 None。
    keys = row.keys()
    return {
        "id": row["id"],
        "scope_id": row["scope_id"],
        "status": row["status"],
        "mode": row["mode"],
        "targets": json.loads(row["targets_json"] or "[]"),
        "tools": json.loads(row["tools_json"] or "[]"),
        "upload_id": row["upload_id"],
        "scenario": row["scenario"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "progress": row["progress"],
        "total_steps": row["total_steps"],
        "done_steps": row["done_steps"],
        "error_code": row["error_code"],
        "error_message": row["error_message"],
        "cancel_requested": bool(row["cancel_requested"]),
        "worker_id": row["worker_id"],
        "lease_until": row["lease_until"],
        "attempt": row["attempt"],
        # P0-7a / P0-7b（用户预授权，DECISIONS 3.1 纯增量补列）。
        "idempotency_key": row["idempotency_key"] if "idempotency_key" in keys else None,
        "next_attempt_at": row["next_attempt_at"] if "next_attempt_at" in keys else None,
    }


def _step_to_dict(row) -> dict:
    keys = row.keys()
    return {
        "id": row["id"],
        "job_id": row["job_id"],
        "tool_name": row["tool_name"],
        "target": row["target"],
        "status": row["status"],
        "attempt": row["attempt"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "exit_code": row["exit_code"],
        "error_code": row["error_code"],
        "error_message": row["error_message"],
        "artifact_id": row["artifact_id"],
        "found_count": row["found_count"],
        "results": json.loads(row["results_json"] or "[]"),
        # M4：结构化观测与执行元数据（旧库缺列时退化为空/None）。
        "observations": json.loads(row["observations_json"] or "[]") if "observations_json" in keys else [],
        "duration_ms": row["duration_ms"] if "duration_ms" in keys else None,
        "command_preview": row["command_preview"] if "command_preview" in keys else None,
        "parser_version": row["parser_version"] if "parser_version" in keys else None,
    }


def _event_to_dict(row) -> dict:
    try:
        detail = json.loads(row["detail_json"] or "{}")
    except (TypeError, ValueError):
        detail = {}
    return {
        "id": row["id"],
        "job_id": row["job_id"],
        "event_type": row["event_type"],
        "detail": detail,
        "created_at": row["created_at"],
    }


def _fetchone(sql: str, params: tuple = ()):
    conn = db.connect()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _fetchall(sql: str, params: tuple = ()) -> list:
    conn = db.connect()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


# ── 事件 ──────────────────────────────────────────────────


def record_event(conn, job_id: str, event_type: str, detail: dict | None = None) -> None:
    """在**已有事务**里写一条 job 事件。

    单独调用请用 :func:`add_event`。
    """
    conn.execute(
        "INSERT INTO job_events (id, job_id, event_type, detail_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (new_job_event_id(), job_id, event_type, json.dumps(detail or {}, ensure_ascii=False), _now()),
    )


def add_event(job_id: str, event_type: str, detail: dict | None = None) -> None:
    """写一条 job 事件（自带事务）。"""
    db.ensure_schema()
    with db.transaction() as conn:
        record_event(conn, job_id, event_type, detail)


# ── 创建 ──────────────────────────────────────────────────


def normalize_idempotency_key(value) -> str | None:
    """规范化幂等键：空值返回 ``None``，非字符串或超长抛 ``ValueError``。

    Rails 的 ``Idempotency-Key`` 与 Stripe 的做法一致：键由**调用方**提供，
    服务端只保证「同一个键不会同时有两个未终结的任务」。

    Raises:
        ValueError: 键不是字符串，或长度超过 :data:`MAX_IDEMPOTENCY_KEY_LENGTH`。
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("idempotency_key 必须是字符串")
    key = value.strip()
    if not key:
        return None
    if len(key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise ValueError(f"idempotency_key 最长 {MAX_IDEMPOTENCY_KEY_LENGTH} 个字符")
    return key


def normalize_operator(value) -> str:
    """规范化操作者标识：空值退化为 :data:`DEFAULT_OPERATOR`，非法抛 ``ValueError``。

    本仓库**没有**身份体系（认证是一个布尔态的本地管理员 Token），因此这个字段
    是「自称」而不是「已验证的身份」—— 它记进审计的价值在于**问责留痕**：
    任务上写着谁提交的，事后能对上人。这一层刻意不把它当权限用。

    与 :func:`normalize_idempotency_key` 的差别：那里空值合法（返回 ``None``），
    这里空值要退化成缺省标识 —— 事件 detail 里出现 ``operator: null``
    会让「谁提交的」这个问题永远没有答案。

    Raises:
        ValueError: 不是字符串，或长度超过 :data:`MAX_OPERATOR_CHARS`。
    """
    if value is None:
        return DEFAULT_OPERATOR
    if isinstance(value, bool):
        # ``True`` 之类「看起来像字符串」的值一律拒绝，避免审计里出现假操作者。
        raise ValueError("operator 必须是字符串")
    if isinstance(value, (list, tuple, dict, set)):
        raise ValueError("operator 必须是字符串")
    text = str(value).strip()
    if not text:
        return DEFAULT_OPERATOR
    if len(text) > MAX_OPERATOR_CHARS:
        raise ValueError(f"operator 最长 {MAX_OPERATOR_CHARS} 个字符")
    return text


def normalize_authorization_note(value) -> str | None:
    """规范化授权说明**快照**：空值返回 ``None``，非法抛 ``ValueError``。

    长度上限与 ``core.projects`` 的 ``authorization_note`` 字段一致（500）：
    这里存的是那次任务所依据的授权说明的**副本**，不是第二份可写字段 ——
    项目上的授权说明改了，历史任务的依据仍应保留原文。

    Raises:
        ValueError: 不是字符串，或长度超过 :data:`MAX_AUTHORIZATION_NOTE_CHARS`。
    """
    if value is None:
        return None
    if isinstance(value, (bool, list, tuple, dict, set)):
        raise ValueError("authorization_note 必须是字符串")
    text = str(value).strip()
    if not text:
        return None
    if len(text) > MAX_AUTHORIZATION_NOTE_CHARS:
        raise ValueError(f"authorization_note 最长 {MAX_AUTHORIZATION_NOTE_CHARS} 个字符")
    return text


def _created_detail(
    *,
    scope_id: str,
    mode: str,
    targets: list[str],
    tools: list[str],
    total_steps: int,
    upload_id: str | None,
    scenario: str | None,
    idempotency_key: str | None,
    pace: str,
    operator: str,
    project_id: str | None,
    strategy: str | None,
    authorization_note: str | None,
    authorization_confirmed: bool | None,
    limits: JobLimits,
) -> dict:
    """拼出 ``job.created`` 事件的 detail —— 任务创建时的**完整事实快照**。

    这是 Phase 3 五个字段（操作者 / 授权备注 / 扫描策略 / 限速 / 超时）唯一的
    落库位置。为什么不是 ``jobs`` 表的列：加列属于 DB 结构变更，
    ``docs/DECISIONS.md`` §1 E 要求纯增量、§2 把「需要改动数据库结构」
    挡回预授权流程；而 ``pace`` 已经证明事件 detail + ``*_of_job()`` 读回
    这条路可行（``core/jobs.py:pace_of_job``）。

    三条纪律：

    * **只在这里拼一次**。调用方（``api/public_scan.py`` → ``core/application.py``
      → 这里）不得各自往 detail 里塞键，否则「同一份事实两种形状」必然漂移。
    * **绝不让 ``targets`` 之外的目标信息泄漏进结构化日志**。detail 是审计数据
      （有意留存完整目标），而 ``observability.log_event`` 只记数量。
    * ``upload_id`` / ``scenario`` / ``idempotency_key`` / ``project_id`` /
      ``strategy`` / ``authorization_note`` 为空时仍然写入 ``None`` ——
      它们的「空」本身是有信息量的（老入口就没给 project_id，这与
      「公网入口给了但项目被删了」不同）。
    """
    detail: dict = {
        "scope_id": scope_id,
        "mode": mode,
        "targets": list(targets),
        "tools": list(tools),
        "total_steps": total_steps,
        "upload_id": upload_id,
        "scenario": scenario,
        "idempotency_key": idempotency_key,
        "pace": pace,
        EVENT_KEY_OPERATOR: operator,
        EVENT_KEY_PROJECT_ID: project_id,
        EVENT_KEY_STRATEGY: strategy,
        EVENT_KEY_AUTHORIZATION_NOTE: authorization_note,
        EVENT_KEY_AUTHORIZATION_CONFIRMED: authorization_confirmed,
    }
    detail.update(limits.to_detail())
    return detail


def create_job(
    *,
    scope_id: str,
    targets: list[str],
    tools: list[str],
    mode: str = "mock",
    upload_id: str | None = None,
    scenario: str | None = None,
    created_by: str = "local-admin",
    idempotency_key: str | None = None,
    pace: str | None = None,
    operator: str | None = None,
    project_id: str | None = None,
    strategy: str | None = None,
    authorization_note: str | None = None,
    authorization_confirmed: bool | None = None,
    limits: JobLimits | None = None,
) -> dict:
    """创建任务并展开步骤快照，返回 queued 状态的 job（不含「是否命中幂等键」）。

    幂等语义与 :func:`create_job_with_status` 完全相同；这个包装只是为了让
    既有调用方（测试与 CLI）继续拿到单个 dict。
    """
    job, _ = create_job_with_status(
        scope_id=scope_id,
        targets=targets,
        tools=tools,
        mode=mode,
        upload_id=upload_id,
        scenario=scenario,
        created_by=created_by,
        idempotency_key=idempotency_key,
        pace=pace,
        operator=operator,
        project_id=project_id,
        strategy=strategy,
        authorization_note=authorization_note,
        authorization_confirmed=authorization_confirmed,
        limits=limits,
    )
    return job


def create_job_with_status(
    *,
    scope_id: str,
    targets: list[str],
    tools: list[str],
    mode: str = "mock",
    upload_id: str | None = None,
    scenario: str | None = None,
    created_by: str = "local-admin",
    idempotency_key: str | None = None,
    pace: str | None = None,
    operator: str | None = None,
    project_id: str | None = None,
    strategy: str | None = None,
    authorization_note: str | None = None,
    authorization_confirmed: bool | None = None,
    limits: JobLimits | None = None,
) -> tuple[dict, bool]:
    """创建任务并展开步骤快照，返回 ``(job, reused)``。

    幂等（P0-7a）：传入 ``idempotency_key`` 时，若已存在同键的**未终结**任务，
    直接返回**那一个**任务（``reused=True``）而不是再建一个。判定与插入在同一个
    ``BEGIN IMMEDIATE`` 事务内，两个并发请求不会各自插出一条。

    「未终结」= ``queued`` / ``running``：任务一旦落到终态，同一个键可以再次
    创建。否则「重试失败的任务」会被幂等键永久挡住。

    Args:
        scope_id: 必填，任务必须关联授权范围。
        targets: 已经过 Scope 校验的目标列表。
        tools: 工具名列表。
        mode: ``mock`` / ``real``。
        upload_id: 目标来源的受控上传 ID（可空）。
        scenario: mock 场景名（仅 mock 模式有效）。
        created_by: 创建者标识（``jobs`` 表的列，本仓库历来只写 ``local-admin``）。
        idempotency_key: 可选的幂等键（同一键只允许一个未终结任务）。
        pace: 扫描节奏档位（``light`` / ``normal``）。见 :mod:`core.pace`。
        operator: 操作者标识（Phase 3「操作者记录」）。空值退化为
            :data:`DEFAULT_OPERATOR`；它写进 ``job.created`` 事件而不是列。
        project_id: 该任务所依据的授权项目 ID（可空，仅公网入口会给）。
        strategy: 生效的策略模板 key（可空，仅公网入口会给）。
        authorization_note: 授权说明的**快照**（创建那一刻的原文）。项目上的说明
            事后被改，历史任务的依据仍应是当时那一份。
        authorization_confirmed: 使用者是否勾选了「我确认该目标属于授权范围」。
            记下它是为了让「这次任务是谁、依据哪份授权、有没有确认过」可查；
            它**不是**权限开关 —— 权限由 Scope / Policy / 环境开关判定。
        limits: :class:`core.job_limits.JobLimits`，单任务的限速与超时（只能收紧）。

    Returns:
        tuple[dict, bool]: 新建或命中的 job，以及「是否命中已有任务」。

    Raises:
        ValueError: targets 或 tools 为空，或幂等键 / 操作者 / 授权说明非法。
    """
    if not targets:
        raise ValueError("targets 不能为空")
    if not tools:
        raise ValueError("tools 不能为空")

    key = normalize_idempotency_key(idempotency_key)
    # 节奏**只写进事件详情**，不加列、不改表结构（DECISIONS §1 E 限纯增量）。
    # 「任务当时是什么节奏」是审计事实，放 job_events 比放 jobs 列更合适：
    # 事件不可改，且 Phase 3 明确不接受任何 DB 结构变更。
    job_pace = coerce_pace(pace)
    # Phase 3 的五个字段走**同一条**零 schema 路径（详见模块顶部 EVENT_KEY_* ）。
    job_operator = normalize_operator(operator)
    job_note = normalize_authorization_note(authorization_note)
    job_limits = limits if isinstance(limits, JobLimits) else JobLimits()

    job_id = new_job_id()
    created_at = _now()
    total_steps = len(targets) * len(tools)

    db.ensure_schema()
    hit_id: str | None = None
    reused = False
    with db.transaction() as conn:
        if key:
            existing = conn.execute(
                "SELECT id FROM jobs WHERE idempotency_key = ? AND status IN (?, ?) "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (key, STATUS_QUEUED, STATUS_RUNNING),
            ).fetchone()
            if existing is not None:
                # 命中已有任务：不插入、不写 created 事件。
                # 「查」与「插」同在 BEGIN IMMEDIATE 事务内，两个并发请求
                # 不会各自插出一条同键任务。
                hit_id = existing["id"]
                reused = True

        if hit_id is None:
            conn.execute(
                """
                INSERT INTO jobs (
                    id, scope_id, status, mode, targets_json, tools_json, upload_id, scenario,
                    created_by, created_at, started_at, finished_at, progress,
                    total_steps, done_steps, error_code, error_message,
                    cancel_requested, worker_id, lease_until, attempt,
                    idempotency_key, next_attempt_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 0, ?, 0, NULL, NULL, 0, NULL, NULL, 1, ?, NULL)
                """,
                (
                    job_id,
                    scope_id,
                    STATUS_QUEUED,
                    mode,
                    json.dumps(list(targets), ensure_ascii=False),
                    json.dumps(list(tools), ensure_ascii=False),
                    upload_id,
                    scenario,
                    created_by,
                    created_at,
                    total_steps,
                    key,
                ),
            )
            # 步骤快照：工具 × 目标，创建时就定下来，进度才是可计算的。
            for tool_name in tools:
                for target in targets:
                    conn.execute(
                        """
                        INSERT INTO job_steps (
                            id, job_id, tool_name, target, status, attempt,
                            started_at, finished_at, exit_code, error_code, error_message,
                            artifact_id, found_count, results_json
                        ) VALUES (?, ?, ?, ?, ?, 1, NULL, NULL, NULL, NULL, NULL, NULL, 0, '[]')
                        """,
                        (new_step_id(), job_id, tool_name, target, STEP_PENDING),
                    )
            record_event(
                conn,
                job_id,
                EVENT_JOB_CREATED,
                _created_detail(
                    scope_id=scope_id,
                    mode=mode,
                    targets=targets,
                    tools=tools,
                    total_steps=total_steps,
                    upload_id=upload_id,
                    scenario=scenario,
                    idempotency_key=key,
                    pace=job_pace,
                    operator=job_operator,
                    project_id=project_id,
                    strategy=strategy,
                    authorization_note=job_note,
                    authorization_confirmed=authorization_confirmed,
                    limits=job_limits,
                ),
            )
            hit_id = job_id

    return get_job_or_raise(hit_id), reused


# ── 读取 ──────────────────────────────────────────────────


def get_job(job_id: str) -> dict | None:
    """按 ID 读取任务，不存在返回 ``None``。"""
    db.ensure_schema()
    row = _fetchone("SELECT * FROM jobs WHERE id = ?", (job_id,))
    return _job_to_dict(row) if row else None


def get_job_or_raise(job_id: str) -> dict:
    """按 ID 读取任务，读不到就抛 ``ValueError``。

    给「刚刚才写过这个 job」的写路径用（:func:`create_job`、``jobs.executor``）：
    那里 ``None`` 属于不可能状态，直接抛错比把 ``| None`` 一路往上传染
    更清楚 —— 调用方也就不必对「刚建好的任务」再做一次 None 判断。
    """
    job = get_job(job_id)
    if job is None:  # pragma: no cover - 刚写入的行读不到，只可能是库被换掉了
        raise ValueError(f"任务不存在: {job_id}")
    return job


def list_jobs(limit: int = 50, status: str | None = None) -> list[dict]:
    """按创建时间倒序列出任务，可按状态过滤。"""
    limit = max(1, min(int(limit or 50), 500))
    db.ensure_schema()
    if status:
        rows = _fetchall(
            "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (status, limit),
        )
    else:
        rows = _fetchall("SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,))
    return [_job_to_dict(row) for row in rows]


def list_steps(job_id: str) -> list[dict]:
    """列出任务的步骤快照。

    按 ``rowid`` 而不是 ``id`` 排序：``id`` 是随机 UUID，排序结果是无意义的；
    ``rowid`` 才是真实的插入顺序（也就是「工具 × 目标」的展开顺序）。
    """
    db.ensure_schema()
    rows = _fetchall("SELECT * FROM job_steps WHERE job_id = ? ORDER BY rowid ASC", (job_id,))
    return [_step_to_dict(row) for row in rows]


def list_events(job_id: str, limit: int = 200) -> list[dict]:
    """列出任务事件（正序，便于还原时间线）。

    同样按 ``rowid`` 排序：事件的 ``created_at`` 只精确到秒，
    同一秒内的多条事件必须靠插入顺序区分。
    """
    limit = max(1, min(int(limit or 200), 1000))
    db.ensure_schema()
    rows = _fetchall(
        "SELECT * FROM job_events WHERE job_id = ? ORDER BY rowid ASC LIMIT ?", (job_id, limit)
    )
    return [_event_to_dict(row) for row in rows]


def created_detail_of_job(job_id: str) -> dict:
    """读回任务创建时写下的 ``job.created`` 事件 detail（读不到返回 ``{}``）。

    Phase 3 的五个字段（``operator`` / ``project_id`` / ``strategy`` /
    ``authorization_note`` / ``rate_limit`` / ``timeout_seconds``）与 ``pace``
    同住这一个事件里。所有 ``*_of_job()`` 读函数都走它，因此「某个键读不到时
    怎么办」只在这个函数里回答一次。

    为什么读**第一条** ``job.created``：参见 :func:`pace_of_job`。
    """
    db.ensure_schema()
    row = _fetchone(
        "SELECT detail_json FROM job_events WHERE job_id = ? AND event_type = ? "
        "ORDER BY rowid ASC LIMIT 1",
        (job_id, EVENT_JOB_CREATED),
    )
    if row is None:
        return {}
    try:
        detail = json.loads(row["detail_json"] or "{}")
    except (TypeError, ValueError):
        return {}
    return detail if isinstance(detail, dict) else {}


def pace_of_job(job_id: str) -> str:
    """读回任务创建时记录的扫描节奏（见 :mod:`core.pace`）。

    节奏**不是** ``jobs`` 表的列：加列属于 DB 结构变更，而 Phase 3 明确不接受
    任何结构变更（``docs/DECISIONS.md`` §1 E「限纯增量」）。它写在
    ``job.created`` 事件的 ``detail`` 里，这里按事件读回。

    读不到时返回 :data:`core.pace.DEFAULT_PACE`（``normal``）—— 这与 Phase 3
    之前的行为**逐字节一致**：老任务没有这个字段，本来就该按常规档跑。

    为什么要在**执行时**读回，而不是在内存里从创建请求一路传下来：
    worker 是**另一个进程**，它只从数据库拿任务；把节奏放进内存等于
    「重启 worker 就丢节奏」，而审计与复现都要求它是可查的。
    """
    return coerce_pace(created_detail_of_job(job_id).get("pace"))


def operator_of_job(job_id: str) -> str:
    """读回任务的操作者标识（Phase 3「操作者记录」）。

    读不到（老任务）或写坏了时返回 :data:`DEFAULT_OPERATOR` —— 与 ``jobs``
    表的 ``created_by`` 历史默认值一致，因此「老任务」与「新任务但没给操作者」
    在页面上是同一个答案，不会出现空字符串这种第三种状态。

    它**只是自称**：本仓库没有身份体系，认证是一个布尔态的本地管理员 Token。
    记它的价值在问责留痕（事后能对上人），不在权限判定。
    """
    try:
        return normalize_operator(created_detail_of_job(job_id).get(EVENT_KEY_OPERATOR))
    except ValueError:
        return DEFAULT_OPERATOR


def strategy_of_job(job_id: str) -> str | None:
    """读回任务生效的策略模板 key；老入口（``POST /api/jobs``）创建的任务为 ``None``。

    ``None`` 与 ``""`` 都归成 ``None``：这两者在「这条任务没有策略模板」上
    是同一件事，留两个空值只会让页面多写一个分支。
    """
    value = created_detail_of_job(job_id).get(EVENT_KEY_STRATEGY)
    text = str(value).strip() if value is not None else ""
    return text or None


def project_id_of_job(job_id: str) -> str | None:
    """读回任务所依据的授权项目 ID；老入口创建的任务为 ``None``。"""
    value = created_detail_of_job(job_id).get(EVENT_KEY_PROJECT_ID)
    text = str(value).strip() if value is not None else ""
    return text or None


def authorization_of_job(job_id: str) -> dict:
    """读回「这次任务的授权依据」：备注快照 + 使用者是否勾选过确认。

    Returns:
        dict: 恒有两个键 ``note`` / ``confirmed``；``note`` 为 ``None`` 表示
        创建时没有留下授权说明，``confirmed`` 为 ``None`` 表示创建时没有
        这个字段（老任务），``False`` 才是「明确没确认」。
    """
    detail = created_detail_of_job(job_id)
    note = detail.get(EVENT_KEY_AUTHORIZATION_NOTE)
    confirmed = detail.get(EVENT_KEY_AUTHORIZATION_CONFIRMED)
    return {
        "note": str(note).strip() or None if note is not None else None,
        "confirmed": confirmed if isinstance(confirmed, bool) else None,
    }


def limits_of_job(job_id: str) -> JobLimits:
    """读回任务创建时指定的限速 / 超时（见 :mod:`core.job_limits`）。

    宽松读回（``core.job_limits.limits_of_detail``）：脏数据退化成「没指定」，
    于是最坏情况也只是回到工具自身配置的默认值 —— 收紧方向的字段读丢时，
    绝不能反过来放松成「无限制」。
    """
    return limits_of_detail(created_detail_of_job(job_id))


def get_job_detail(job_id: str) -> dict | None:
    """任务详情 = job + steps + events + 创建时的策略上下文。

    ``pace`` / ``operator`` / ``strategy`` / ``project_id`` / ``authorization`` /
    ``limits`` 都**不是** ``jobs`` 表的列，而是从 ``job.created`` 事件读回的
    （见 :func:`created_detail_of_job`）。加进来是为了回答用户真正会问的问题：
    「这条任务是按什么节奏、以谁的名义、依据哪份授权、跑得有多克制」。
    """
    job = get_job(job_id)
    if job is None:
        return None
    job["steps"] = list_steps(job_id)
    job["events"] = list_events(job_id)
    job["pace"] = pace_of_job(job_id)
    job["operator"] = operator_of_job(job_id)
    job["strategy"] = strategy_of_job(job_id)
    job["project_id"] = project_id_of_job(job_id)
    job["authorization"] = authorization_of_job(job_id)
    job["limits"] = limits_of_job(job_id).to_dict()
    return job


def queue_counts() -> dict:
    """各状态任务数量，供 ``/health`` 与页面展示。"""
    db.ensure_schema()
    rows = _fetchall("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")
    counts = {status: 0 for status in ALL_STATUSES}
    for row in rows:
        counts[row["status"]] = row["n"]
    counts["total"] = sum(counts[status] for status in ALL_STATUSES)
    return counts


# ── 认领与租约 ────────────────────────────────────────────


def claim_next_job(worker_id: str, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> dict | None:
    """原子地领取下一个 queued 任务并置为 running。

    ``BEGIN IMMEDIATE`` + ``UPDATE ... WHERE status='queued'`` 双重保护：
    即使误开了多个 worker，同一个任务也只会被一个进程领到。

    领取条件还包含**退避窗口**（P0-7b）：``next_attempt_at`` 为空或已过期的
    任务才可领。退避中的任务仍在 ``queued``（因此 ``/health`` 的排队计数
    会把它们算进去），只是暂时取不到——这样「任务在队列里但还没到时候」
    与「任务丢了」是两件可区分的事。

    Returns:
        dict | None: 领到的 job（``status=running``）；没有可领任务时返回 ``None``。
    """
    db.ensure_schema()
    now = _now()
    with db.transaction() as conn:
        # 取出待领任务：``created_at`` 只精确到秒，同一秒内创建的任务必须靠
        # ``rowid``（真实插入顺序）决定先后，否则 FIFO 会退化成随机顺序。
        row = conn.execute(
            "SELECT id FROM jobs WHERE status = ? AND (next_attempt_at IS NULL OR next_attempt_at <= ?) "
            "ORDER BY created_at ASC, rowid ASC LIMIT 1",
            (STATUS_QUEUED, now),
        ).fetchone()
        if row is None:
            return None

        job_id = row["id"]
        started_at = now
        cursor = conn.execute(
            """
            UPDATE jobs
               SET status = ?, worker_id = ?, lease_until = ?, started_at = COALESCE(started_at, ?),
                   next_attempt_at = NULL
             WHERE id = ? AND status = ?
            """,
            (STATUS_RUNNING, worker_id, _future(lease_seconds), started_at, job_id, STATUS_QUEUED),
        )
        if cursor.rowcount != 1:
            # 被别的 worker 抢先了（正常竞争，不是错误）。
            return None

        record_event(conn, job_id, EVENT_JOB_STARTED, {"worker_id": worker_id})

    return get_job(job_id)


def renew_lease(job_id: str, worker_id: str, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> bool:
    """续租。worker 在执行过程中定期调用，证明自己还活着。"""
    db.ensure_schema()
    with db.transaction() as conn:
        cursor = conn.execute(
            "UPDATE jobs SET lease_until = ? WHERE id = ? AND worker_id = ? AND status = ?",
            (_future(lease_seconds), job_id, worker_id, STATUS_RUNNING),
        )
        return cursor.rowcount == 1


def recover_stale_jobs(now: str | None = None) -> list[str]:
    """把租约过期的 running 任务标成 interrupted。

    这是「worker 被 kill 后任务不会静默消失」的关键：任务不会自己变成功
    也不会一直卡在 running，而是明确进入 ``interrupted`` 等待人工 retry。

    Args:
        now: 参考时间（ISO 字符串），默认取当前 UTC 时间。

    Returns:
        list[str]: 被标记的任务 ID 列表。
    """
    db.ensure_schema()
    now = now or _now()
    recovered: list[str] = []
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT id, worker_id FROM jobs WHERE status = ? AND (lease_until IS NULL OR lease_until < ?)",
            (STATUS_RUNNING, now),
        ).fetchall()
        for row in rows:
            _mark_interrupted(conn, row["id"], row["worker_id"])
            recovered.append(row["id"])
    return recovered


def _mark_interrupted(conn, job_id: str, previous_worker_id: str | None) -> None:
    """在已有事务里把任务置为 interrupted，并把 running 步骤拉回 pending。"""
    now = _now()
    conn.execute(
        """
        UPDATE jobs
           SET status = ?, finished_at = ?, worker_id = NULL, lease_until = NULL,
               error_code = ?, error_message = ?
         WHERE id = ? AND status = ?
        """,
        (
            STATUS_INTERRUPTED,
            now,
            ErrorCode.INTERRUPTED,
            "worker 中断，任务未完成，可调用 retry 重新排队",
            job_id,
            STATUS_RUNNING,
        ),
    )
    conn.execute(
        "UPDATE job_steps SET status = ?, finished_at = ? WHERE job_id = ? AND status = ?",
        (STEP_PENDING, now, job_id, STEP_RUNNING),
    )
    record_event(conn, job_id, EVENT_JOB_INTERRUPTED, {"previous_worker_id": previous_worker_id})


def release_job(job_id: str, worker_id: str | None = None) -> bool:
    """worker 优雅退出时，把自己的任务立刻标成 interrupted。

    与 :func:`recover_stale_jobs` 的差别：这里**不等租约过期**，
    因为进程马上要退出了。带 ``worker_id`` 时会校验归属，
    避免误改已经被别人接管的任务。

    Returns:
        bool: 是否确实改动了一个 running 任务。
    """
    db.ensure_schema()
    with db.transaction() as conn:
        if worker_id:
            row = conn.execute(
                "SELECT worker_id FROM jobs WHERE id = ? AND status = ?", (job_id, STATUS_RUNNING)
            ).fetchone()
            if row is None or row["worker_id"] != worker_id:
                return False
        _mark_interrupted(conn, job_id, worker_id)
        return True


# ── 取消与重试 ────────────────────────────────────────────


def request_cancel(job_id: str) -> dict | None:
    """请求取消任务。

    * ``queued``  —— 立即置为 cancelled（worker 领不到）；
    * ``running`` —— 打 ``cancel_requested`` 标记，由 worker 在步骤边界收尾；
    * 终态      —— 保持原状，返回当前状态（幂等）。

    Returns:
        dict | None: 操作后的 job；不存在返回 ``None``。
    """
    db.ensure_schema()
    with db.transaction() as conn:
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None

        status = row["status"]
        now = _now()
        if status == STATUS_QUEUED:
            conn.execute(
                "UPDATE jobs SET status = ?, finished_at = ?, progress = 100 WHERE id = ?",
                (STATUS_CANCELLED, now, job_id),
            )
            conn.execute(
                "UPDATE job_steps SET status = ?, finished_at = ? WHERE job_id = ? AND status = ?",
                (STEP_SKIPPED, now, job_id, STEP_PENDING),
            )
            record_event(conn, job_id, EVENT_JOB_CANCELLED, {"from": status})
        elif status == STATUS_RUNNING:
            conn.execute("UPDATE jobs SET cancel_requested = 1 WHERE id = ?", (job_id,))
            record_event(conn, job_id, EVENT_JOB_CANCEL_REQUESTED, {})
        # 终态：什么都不做（幂等）

    return get_job(job_id)


def is_cancel_requested(job_id: str) -> bool:
    """worker 在步骤边界检查是否收到取消请求。"""
    row = _fetchone("SELECT cancel_requested FROM jobs WHERE id = ?", (job_id,))
    return bool(row and row["cancel_requested"])


def retry_job(job_id: str) -> dict | None:
    """把可重试的终态任务重新排回 queued，并重置未成功的步骤。

    退避（P0-7b）：重新排队时写入 ``next_attempt_at``（第 N 次尝试的退避见
    :func:`retry_backoff_seconds`）。任务**立刻**变成 ``queued``（界面能马上
    看到），但 worker 要等到退避窗口过去才会领它——否则「重试」会变成
    「对着同一台上游故障连打」，把一个远端抖动放大成本地队列雪崩。

    Returns:
        dict | None: 操作后的 job；不存在返回 ``None``。

    Raises:
        ValueError: 任务当前状态不允许重试（如正在 running 或已 succeeded），
            或已达到 :data:`MAX_ATTEMPTS` 上限。
    """
    db.ensure_schema()
    with db.transaction() as conn:
        row = conn.execute("SELECT status, attempt FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None

        status = row["status"]
        if status not in RETRYABLE_STATUSES:
            raise ValueError(f"当前状态 {status} 不允许 retry")
        if not can_transition(status, STATUS_QUEUED):
            raise ValueError(f"非法的状态跃迁: {status} → {STATUS_QUEUED}")
        if row["attempt"] >= MAX_ATTEMPTS:
            raise ValueError(f"已达到最大重试次数（{MAX_ATTEMPTS}），拒绝继续 retry")

        next_attempt = row["attempt"] + 1
        backoff = retry_backoff_seconds(next_attempt)

        conn.execute(
            """
            UPDATE jobs
               SET status = ?, cancel_requested = 0, started_at = NULL, finished_at = NULL,
                   progress = 0, done_steps = 0, error_code = NULL, error_message = NULL,
                   worker_id = NULL, lease_until = NULL, attempt = ?, next_attempt_at = ?
             WHERE id = ?
            """,
            (STATUS_QUEUED, next_attempt, _future(backoff) if backoff else None, job_id),
        )
        # 已经成功的步骤不重跑，其余回到 pending。
        conn.execute(
            "UPDATE job_steps SET status = ?, attempt = attempt + 1, started_at = NULL, finished_at = NULL, "
            "error_code = NULL, error_message = NULL WHERE job_id = ? AND status != ?",
            (STEP_PENDING, job_id, STEP_SUCCEEDED),
        )
        record_event(
            conn,
            job_id,
            EVENT_JOB_RETRY_REQUESTED,
            {"from": status, "attempt": next_attempt, "backoff_seconds": backoff},
        )

    return get_job(job_id)


# ── 执行期写入（由 worker 调用） ──────────────────────────


def pending_steps(job_id: str) -> list[dict]:
    """尚未成功执行的步骤（retry 后只重跑这些）。"""
    db.ensure_schema()
    rows = _fetchall(
        "SELECT * FROM job_steps WHERE job_id = ? AND status IN (?, ?) ORDER BY rowid ASC",
        (job_id, STEP_PENDING, STEP_RUNNING),
    )
    return [_step_to_dict(row) for row in rows]


def start_step(step_id: str) -> None:
    """标记步骤开始执行。"""
    with db.transaction() as conn:
        conn.execute(
            "UPDATE job_steps SET status = ?, started_at = ? WHERE id = ?",
            (STEP_RUNNING, _now(), step_id),
        )


def finish_step(
    step_id: str,
    *,
    status: str,
    found_count: int = 0,
    results: list[str] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    exit_code: int | None = None,
    artifact_id: str | None = None,
    observations: list[dict] | None = None,
    duration_ms: int | None = None,
    command_preview: str | None = None,
    parser_version: str | None = None,
) -> None:
    """写入步骤结果。

    M4 起除 M3 的字段外，还写入结构化观测（``observations_json``）与执行
    元数据（``duration_ms`` / ``command_preview`` / ``parser_version``），
    任务详情页据此展示状态码、标题、技术栈与原始证据入口。
    """
    with db.transaction() as conn:
        conn.execute(
            """
            UPDATE job_steps
               SET status = ?, finished_at = ?, found_count = ?, results_json = ?,
                   error_code = ?, error_message = ?, exit_code = ?,
                   artifact_id = COALESCE(?, artifact_id),
                   observations_json = ?, duration_ms = ?, command_preview = ?,
                   parser_version = ?
             WHERE id = ?
            """,
            (
                status,
                _now(),
                int(found_count or 0),
                json.dumps(list(results or []), ensure_ascii=False),
                error_code,
                error_message,
                exit_code,
                artifact_id,
                json.dumps(list(observations or []), ensure_ascii=False),
                duration_ms,
                command_preview,
                parser_version,
                step_id,
            ),
        )


def update_progress(job_id: str, done_steps: int, total_steps: int) -> int:
    """写回进度（0..100 整数），返回进度值。"""
    progress = int(round(done_steps * 100 / total_steps)) if total_steps else 100
    with db.transaction() as conn:
        conn.execute(
            "UPDATE jobs SET done_steps = ?, progress = ? WHERE id = ?",
            (done_steps, progress, job_id),
        )
    return progress


def finish_job(
    job_id: str,
    *,
    status: str,
    error_code: str | None = None,
    error_message: str | None = None,
    release_worker: bool = True,
) -> None:
    """把任务置为终态。

    Raises:
        ValueError: ``status`` 不是终态，或从当前状态跃迁到该终态非法
            （方案第 7 节：不允许 ``queued → succeeded`` 这类跳步）。
    """
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"{status} 不是终态")

    db.ensure_schema()
    with db.transaction() as conn:
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise ValueError(f"任务不存在: {job_id}")
        current = row["status"]
        if not can_transition(current, status):
            raise ValueError(f"非法的状态跃迁: {current} → {status}")

        now = _now()
        if release_worker:
            conn.execute(
                """
                UPDATE jobs
                   SET status = ?, finished_at = ?, progress = 100, done_steps = total_steps,
                       error_code = ?, error_message = ?, worker_id = NULL, lease_until = NULL
                 WHERE id = ?
                """,
                (status, now, error_code, error_message, job_id),
            )
        else:
            conn.execute(
                """
                UPDATE jobs
                   SET status = ?, finished_at = ?, progress = 100, done_steps = total_steps,
                       error_code = ?, error_message = ?
                 WHERE id = ?
                """,
                (status, now, error_code, error_message, job_id),
            )
        record_event(
            conn,
            job_id,
            EVENT_JOB_FINISHED,
            {"status": status, "error_code": error_code},
        )


def mark_remaining_steps_skipped(job_id: str) -> None:
    """取消时把还没跑的步骤标成 skipped。"""
    with db.transaction() as conn:
        conn.execute(
            "UPDATE job_steps SET status = ?, finished_at = ? WHERE job_id = ? AND status IN (?, ?)",
            (STEP_SKIPPED, _now(), job_id, STEP_PENDING, STEP_RUNNING),
        )


def reset_running_steps_on_start(job_id: str) -> None:
    """worker 重新接手（retry 后）时，把残留的 running 步骤拉回 pending。"""
    with db.transaction() as conn:
        conn.execute(
            "UPDATE job_steps SET status = ?, started_at = NULL WHERE job_id = ? AND status = ?",
            (STEP_PENDING, job_id, STEP_RUNNING),
        )
