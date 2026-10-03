"""统一 Application Service 入口（总方案第 5.2 节 / DSH 方案第 6 节）。

## 为什么要有这一层

方案要求每一次「执行类业务动作」都走同一条链：

```text
调用方（HTTP 路由 / 页面 / Agent）
  ↓
Application Service     ← 本模块
  ↓
Policy / Scope          core/policy.py
  ↓
Job                     core/jobs.py（单表即队列）
  ↓
Worker / Runner         jobs/worker.py → jobs/executor.py → modules/
```

在引入本模块之前，「创建扫描任务」这件事的编排代码**写死在 HTTP 路由里**
（``api/jobs.py:create_job``）：解析目标 → 校验工具 → 限流 → Policy 判定 →
模式解析 → 落库 → 审计 → 结构化日志，全部内联在视图函数中。
``app.py:index()`` 为了实现同一件事，只能 ``from api.jobs import _resolve_targets``
反向导入一个私有函数，再把 Policy 判定抄了第二遍。

后果有两个，都是真实存在的：

1. **Agent 那条路绕过了整条 Job 链**（P0-6）。``agent/action.py`` 直接调
   ``tool_runner.run_tools`` 与 ``HttpxRunner.run_scan``，于是同一次「子域名收集」
   在主链上是一个可审计、可取消、可重试、可复检 Scope 的 Job，在 Agent 链上
   却是一次同步函数调用 —— 没有 job 记录、没有 step 快照、没有 artifact、
   没有审计事件，且 Scope 只判了创建时那一次。
2. **同一套判定有两份实现**，改一处漏一处的风险随字段增加而线性上升。

## 本模块的边界（刻意收窄）

* **不碰 Flask**。请求解析、认证、HTTP 状态码仍由 ``api/`` 与 ``app.py`` 负责；
  这里只接收已经解析好的标量/列表参数，返回结构化结果或抛
  ``core.errors`` 里的业务异常。
* **不做 Scope 判定**。一律转交 ``core.policy.validate_job_targets``，
  本模块不自己实现任何白名单比较。
* **不改数据结构**。只调用 ``core.jobs`` 已有的写入函数。

## 已收拢的业务动作

* :func:`create_scan_job` —— 创建扫描任务（当前唯一一个；总方案 5.2 节列举的
  ``create_scope`` / ``create_export`` / ``request_cancel`` / ``retry_job``
  尚未收拢，按「不要一次性无边界重写」的原则分批做）。
* :func:`resolve_targets` —— 目标来源解析（显式列表 + 受控 ``upload_id``）。
* :func:`create_authorized_public_job` —— **授权公网测试模式**下创建任务的唯一入口
  （公网授权测试模式体验版方案第 4、6 节）。它在 :func:`create_scan_job` 之前
  叠加「项目 / 项目内 Scope / 公网工具白名单」三道闸门，然后原样委派，
  因此 Policy / Scope / mode 判定仍然只有一份实现。

## 各处共享的同一条入口

```text
POST /api/jobs      → api/jobs.py:create_job      ┐
首页表单 POST /      → app.py:index               ├→ create_scan_job()
Agent（P0-6 迁移中） → agent/action.py            ┘

POST /api/public-jobs → api/public_scan.py        ┐
扫描中心页面 POST /     → app.py:scan_center       ├→ create_authorized_public_job()
                                                  ┘   → create_scan_job()
```
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from config import SCAN_LIMITS
from core import audit, jobs as jobs_store, observability, projects, uploads
from core.errors import BadRequestError
from core.job_limits import (
    FIELD_RATE_LIMIT,
    FIELD_TIMEOUT_SECONDS,
    JobLimits,
    resolve_limits,
)
from core.mock import SCENARIOS, normalize_scenario
from core.pace import DEFAULT_PACE, PACE_LABELS, PACE_LEVELS, coerce_pace, resolve_pace
from core.policy import validate_job_targets
from core.projects import Project
from core.safety import MODE_MOCK, MODE_REAL, resolve_mode
from core.scope import Scope
from core.tool_registry import (
    STRATEGY_ASSET_DISCOVERY,
    assert_tools_internet_allowed,
    resolve_strategy_pace,
    resolve_strategy_tools,
)


def split_str_list(value: Any, field: str) -> list[str]:
    """把请求字段规范化为字符串列表（去空白、丢空项）。

    沿用 ``api/jobs.py`` 原有口径，逐字保留错误消息，避免上层响应文案漂移。

    Raises:
        BadRequestError: ``value`` 既不是字符串也不是数组。
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if not isinstance(value, (list, tuple)):
        raise BadRequestError(f"{field} 必须是字符串数组")
    return [str(item).strip() for item in value if str(item).strip()]


def resolve_targets(
    *,
    targets: Any = None,
    upload_id: Any = None,
) -> tuple[list[str], str | None]:
    """解析扫描目标：显式 ``targets`` 列表，或受控 ``upload_id`` 上传记录。

    文件目标**只能**来自受控 ``upload_id``（P0-3）：``upload_id`` 由
    ``core/uploads.py`` 校验并按 UUID 目录换取归一化清单，调用方拼不出任意路径。

    Returns:
        tuple[list[str], str | None]: ``(去重保序的目标列表, upload_id)``。

    Raises:
        BadRequestError: ``upload_id`` 非法、不存在或文件已丢失。
    """
    upload = str(upload_id).strip() if upload_id else None
    resolved = split_str_list(targets, "targets")

    if upload:
        resolved.extend(uploads.load_targets(upload))

    seen: set[str] = set()
    unique: list[str] = []
    for target in resolved:
        if target not in seen:
            unique.append(target)
            seen.add(target)
    return unique, upload


@dataclass(frozen=True)
class JobSubmission:
    """一次成功提交的结果（供各调用方拼自己的响应/回复）。

    Attributes:
        job: ``core.jobs`` 返回的 job 字典（创建时或命中幂等键时那一个）。
        scope: 已通过校验的 Scope 对象。
        mode: 最终生效的模式（``mock`` / ``real``）。
        tools: 实际入库的工具名列表。
        targets: 实际入库的、已过 Scope 校验的目标列表。
        reused: 是否命中幂等键（``True`` 表示没有新建任务）。
        idempotency_key: 规范化后的幂等键，未提供时为 ``None``。
        pace: 最终生效的扫描节奏（``light`` / ``normal``，见 :mod:`core.pace`）。
        operator: 最终生效的操作者标识（Phase 3，空值退化为 ``local-admin``）。
        strategy: 生效的策略模板 key（仅公网入口会给，老入口为 ``None``）。
        project_id: 该任务依据的授权项目 ID（仅公网入口会给）。
        authorization_note: 授权说明快照（仅公网入口会给）。
        authorization_confirmed: 使用者是否勾选过授权确认（仅公网入口会给）。
        limits: 单任务的限速 / 超时（``core.job_limits.JobLimits``）。

    ``to_dict()`` **刻意只回 job 本身那几项**：``operator`` / ``limits`` /
    ``authorization_note`` 属于审计上下文，由 ``GET /api/jobs/<id>`` 提供。
    ``POST /api/jobs`` 的响应形状是既有调用方的契约（``test_public_scan_mode.py``
    明确断言旧响应里不得出现公网专属字段），所以新增上下文一律**不进**这里。
    """

    job: dict
    scope: Scope
    mode: str
    tools: list[str]
    targets: list[str]
    reused: bool
    idempotency_key: str | None = None
    pace: str = DEFAULT_PACE
    operator: str = jobs_store.DEFAULT_OPERATOR
    strategy: str | None = None
    project_id: str | None = None
    authorization_note: str | None = None
    authorization_confirmed: bool | None = None
    limits: JobLimits = JobLimits()

    def to_dict(self) -> dict:
        """``POST /api/jobs`` 的响应体（202 Accepted）。"""
        pace = coerce_pace(self.pace)
        return {
            "ok": True,
            "job_id": self.job["id"],
            "status": self.job["status"],
            "mode": self.job["mode"],
            "total_steps": self.job["total_steps"],
            "scope_id": self.job["scope_id"],
            "reused": self.reused,
            # 把「这次任务按什么节奏跑」如实回给调用方：页面要显示它，
            # 脚本要能断言它，而执行期是**从库里读回**同一个值（不做第二份判定）。
            "pace": pace,
            "pace_label": PACE_LABELS.get(pace, pace),
        }


def create_scan_job(
    *,
    scope_id: str | None,
    targets: Any = None,
    tools: Any = None,
    upload_id: Any = None,
    mode: str | None = None,
    scenario: str | None = None,
    idempotency_key: Any = None,
    created_by: str = "local-admin",
    pace: Any = None,
    operator: Any = None,
    project_id: str | None = None,
    strategy: str | None = None,
    authorization_note: str | None = None,
    authorization_confirmed: bool | None = None,
    rate_limit: Any = None,
    timeout_seconds: Any = None,
) -> JobSubmission:
    """创建扫描任务的**唯一**编排入口。

    校验顺序（与历史上 ``api/jobs.py:create_job`` 逐条一致，不重排）：
    目标非空 → 工具非空 → 幂等键合法 → 节奏合法 → 限速/超时合法 →
    工具受支持 → 目标数上限 → Scope/Policy → 模式开关 → mock 场景名 →
    落库 → 审计 + 结构化日志。

    Scope 判定**只发生一次**，且发生在这里：调用方（HTTP / 页面 / Agent）
    一律不得自己实现或跳过它。

    Args:
        scope_id: 授权范围 ID。缺失 → 400，不存在/目标越界 → 403（整体拒绝）。
        targets: 显式目标列表（字符串或逗号分隔字符串）。
        tools: 工具名列表（字符串或逗号分隔字符串）。
        upload_id: 受控上传 ID；与 ``targets`` 可同时给出（结果合并去重）。
        mode: ``mock`` / ``real``，缺省 ``mock``；``real`` 需双开关。
        scenario: 仅 mock 有效的场景名。
        idempotency_key: 可选幂等键（同一键只允许一个未终结任务）。
        created_by: 创建者标识（``jobs`` 表的列）。
        pace: 扫描节奏（``light`` / ``normal``）。缺省 ``normal``，即与引入
            Scan Profile 之前**逐字节一致**的历史行为；调用方若传非法值则
            400（不静默回退 —— 写了拼错的档位却拿到常规档是最危险的错法）。
        operator: 操作者标识（Phase 3）。空值由 ``core.jobs`` 退化为
            ``local-admin``；它不是权限开关，只是问责留痕。
        project_id: 该任务依据的授权项目 ID（只有公网入口会给）。
        strategy: 生效的策略模板 key（只有公网入口会给）。
        authorization_note: 授权说明快照（只有公网入口会给）。
        authorization_confirmed: 使用者是否勾选过授权确认（只有公网入口会给）。
        rate_limit: 每秒请求上限（可选，只能收紧）。见 :mod:`core.job_limits`。
        timeout_seconds: 单步超时秒数（可选，只能收紧）。

    Returns:
        JobSubmission: 含 job / scope / 实际入库的 tools 与 targets。

    Raises:
        BadRequestError: 参数缺失或格式非法（含非法档位、越界限速/超时）。
        InvalidTargetError: 目标既不是合法域名也不是 IP/CIDR。
        ScopeViolationError: Scope 缺失、不存在、目标越界，或 real 模式未开开关。
    """
    resolved_targets, resolved_upload = resolve_targets(targets=targets, upload_id=upload_id)
    if not resolved_targets:
        raise BadRequestError("必须提供 targets 或 upload_id")

    selected_tools = split_str_list(tools, "tools")
    if not selected_tools:
        raise BadRequestError("必须提供至少一个工具", details={"field": "tools"})

    # 幂等键先判：与历史顺序一致（参数格式错误优先于「工具不受支持」报出）。
    try:
        key = jobs_store.normalize_idempotency_key(idempotency_key)
    except ValueError as exc:
        raise BadRequestError(str(exc), details={"field": "idempotency_key"}) from exc

    # 节奏：缺省 ``normal``（= 历史行为），非法值 400。
    # 放在工具校验之前判，与幂等键同理：参数格式错误应当先于「工具不支持」报出。
    #
    # 用 ``resolve_pace`` 而不是宽松的 ``coerce_pace``：请求体里写了拼错的档位
    # （例如 ``"low"``）必须报错，而不是静默落成 ``normal`` —— 那样使用者会以为
    # 自己已经用了最保守的一档。读**库里的**历史值才用宽松版本（见 core/jobs.py）。
    # ``create_authorized_public_job`` 已经把模板档位与请求档位合并过，
    # 这里再走一次是幂等的（合法档位合并后仍是它自己），不存在第二份判定。
    try:
        resolved_pace = resolve_pace(DEFAULT_PACE, pace)
    except ValueError as exc:
        raise BadRequestError(
            str(exc),
            details={"field": "pace", "supported": list(PACE_LEVELS)},
        ) from exc

    # 限速 / 超时：与档位同一个位置判，理由相同 —— 参数写错必须先报出来，
    # 不能静默夹到边界（写了 rate_limit=99999 却拿到 100，与写了拼错的档位
    # 却拿到常规档是同一种危险错法）。见 core/job_limits.py。
    try:
        resolved_limits = resolve_limits(rate_limit=rate_limit, timeout_seconds=timeout_seconds)
    except ValueError as exc:
        # ``core.job_limits`` 的报错文案**一定带字段名**（两种错误都是），
        # 因此这里按文案定位：定位不到时归给 ``rate_limit`` 只是兜底，
        # 正常路径不会走到（那条不变量有单测锁着，见 test_format_errors_name_the_offending_field）。
        message = str(exc)
        field = FIELD_TIMEOUT_SECONDS if FIELD_TIMEOUT_SECONDS in message else FIELD_RATE_LIMIT
        raise BadRequestError(message, details={"field": field}) from exc

    # 操作者：空值退化为缺省标识（不留 ``null``），非法长度 400。
    # 与 ``idempotency_key`` 一样是「格式错误」，因此同样放在工具校验之前。
    try:
        resolved_operator = jobs_store.normalize_operator(operator)
    except ValueError as exc:
        raise BadRequestError(str(exc), details={"field": "operator"}) from exc

    # 延迟导入：``core/`` 是纯领域层，不在 import 期依赖顶层编排模块
    # （``tool_runner`` → ``modules/`` → ``core.errors``，避免潜在环）。
    from tool_runner import load_tools

    try:
        selected_tools = load_tools(selected_tools)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc

    max_targets = SCAN_LIMITS["max_targets_per_job"]
    if len(resolved_targets) > max_targets:
        raise BadRequestError(
            f"单个任务最多 {max_targets} 个目标，当前 {len(resolved_targets)} 个",
            details={"max_targets_per_job": max_targets},
        )

    # ── Policy / Scope：唯一判定点（缺失 400，不存在或越界 403，整体拒绝）──
    scope, validated_targets = validate_job_targets(scope_id, resolved_targets)

    resolved_mode = resolve_mode(mode)
    if resolved_mode == MODE_REAL:
        # 双开关的第二道：环境开关已在 resolve_mode 内确认，这里确认 Scope。
        scope.require_active_scan()

    chosen_scenario: str | None = None
    if resolved_mode == MODE_MOCK:
        if scenario is not None and str(scenario).strip().lower() not in SCENARIOS:
            raise BadRequestError(
                f"scenario 仅支持 {', '.join(SCENARIOS)}",
                details={"field": "scenario"},
            )
        chosen_scenario = normalize_scenario(scenario) if scenario is not None else None

    job, reused = jobs_store.create_job_with_status(
        scope_id=scope.id,
        targets=validated_targets,
        tools=selected_tools,
        mode=resolved_mode,
        upload_id=resolved_upload,
        scenario=chosen_scenario,
        created_by=created_by,
        idempotency_key=key,
        pace=resolved_pace,
        operator=resolved_operator,
        project_id=project_id,
        strategy=strategy,
        authorization_note=authorization_note,
        authorization_confirmed=authorization_confirmed,
        limits=resolved_limits,
    )

    # 审计 detail：与 ``job.created`` 事件同源的「谁、依据什么、跑了什么」。
    # 规划方案第 7 节点名的四要素 —— ``operator`` · ``target`` · ``timestamp`` ·
    # ``scope_id`` —— 全部落在这里：``target`` 是 ``targets``（完整清单，审计
    # 数据有意留存），``timestamp`` 由 ``audit_events.created_at`` 提供。
    detail: dict = {
        "scope_id": scope.id,
        "mode": resolved_mode,
        "tools": selected_tools,
        "targets": validated_targets,
        "total_steps": job["total_steps"],
        "pace": resolved_pace,
        "operator": resolved_operator,
    }
    if project_id:
        # 只有公网入口会给项目：老入口的审计记录里不该凭空多出一个 null 键。
        detail["project_id"] = project_id
    if strategy:
        detail["strategy"] = strategy
    if authorization_note:
        detail["authorization_note"] = authorization_note
    if authorization_confirmed is not None:
        detail["authorization_confirmed"] = bool(authorization_confirmed)
    detail.update(resolved_limits.to_detail())
    if reused:
        # 命中已有任务不是一次「新建」：只记一条「重复请求被折叠」的可追溯记录
        # （事件类型仍是 job_created，target_id 指向那个已存在的任务）。
        detail["idempotency_key"] = key
        detail["reused"] = True
    audit.record(audit.EVENT_JOB_CREATED, target_id=job["id"], detail=detail)

    # 方案第 19 节：任务创建也进结构化日志。request_id 由 app.py 的
    # before_request 绑定（服务层不感知 Flask）；**只记工具名与数量，不记目标列表**
    # （完整目标在 audit_events 与 job 快照里，那是有意留存的审计数据）。
    # ``operator`` 是标识而不是自由文本，允许进日志；授权说明与限速值不进
    # （前者可能是敏感凭据描述，后者在审计表里可查）。见
    # tests/integration/test_observability_chain.py 的反向守卫。
    observability.log_event(
        observability.EVENT_JOB_CREATED,
        job_id=job["id"],
        scope_id=scope.id,
        mode=resolved_mode,
        tools=selected_tools,
        target_count=len(validated_targets),
        total_steps=job["total_steps"],
        reused=reused,
        operator=resolved_operator,
    )

    return JobSubmission(
        job=job,
        scope=scope,
        mode=resolved_mode,
        tools=selected_tools,
        targets=validated_targets,
        reused=reused,
        idempotency_key=key,
        pace=resolved_pace,
        operator=resolved_operator,
        strategy=strategy,
        project_id=project_id,
        authorization_note=authorization_note,
        authorization_confirmed=authorization_confirmed,
        limits=resolved_limits,
    )


# ── 授权公网测试模式（公网授权测试模式体验版方案第 4、6 节） ──────────


@dataclass(frozen=True)
class AuthorizedJobSubmission:
    """一次「授权公网测试」提交的结果。

    在 :class:`JobSubmission` 之外多带项目与策略信息，让页面不必再回查一次；
    真正的任务字段仍然是同一个 ``job`` 字典。

    Attributes:
        submission: 底层 Job 提交结果（复用同一条编排）。
        project: 该项目（授权证据的组织单位）。
        strategy: 生效的策略模板 key。
        tools: 最终工具列表（已过公网白名单）。
        targets: 已过 Scope 校验的目标。
        pace: 最终生效的节奏（模板缺省与请求合并后的结果，只能收紧）。
    """

    submission: JobSubmission
    project: Project
    strategy: str
    tools: list[str]
    targets: list[str]
    pace: str = DEFAULT_PACE

    def to_dict(self) -> dict:
        """``POST /api/public-jobs`` 的响应体（沿用 202 Accepted）。

        在 :meth:`JobSubmission.to_dict` 的基础上追加公网专属字段。
        ``authorization`` 只回**是否确认过**与备注长度级别的元数据，
        不回备注原文：原文在 ``GET /api/jobs/<id>`` 与审计里可查，没有必要
        在每次提交的响应里再散一份。
        """
        payload = self.submission.to_dict()
        payload.update(
            {
                "project_id": self.project.id,
                "project_name": self.project.name,
                "strategy": self.strategy,
                "authorized_public": True,
                "operator": self.submission.operator,
                "authorization_confirmed": self.submission.authorization_confirmed,
                "limits": self.submission.limits.to_dict(),
            }
        )
        return payload


def create_authorized_public_job(
    *,
    project_id: str | None,
    scope_id: str | None,
    targets: Any = None,
    upload_id: Any = None,
    strategy: str | None = None,
    tools: Any = None,
    pace: Any = None,
    mode: str | None = None,
    scenario: str | None = None,
    idempotency_key: Any = None,
    created_by: str = "local-admin",
    operator: Any = None,
    authorization_confirmed: Any = None,
    rate_limit: Any = None,
    timeout_seconds: Any = None,
) -> AuthorizedJobSubmission:
    """创建「授权公网测试」任务 —— 公网模式下创建任务的**唯一**入口。

    与 :func:`create_scan_job` 的关系是**叠加而非并列**：本函数在它之前多做
    三道公网专属闸门，然后**原样调用**它，因此 Policy / Scope / mode 判定
    仍然只发生在那一个地方，不存在第二条实现。

    公网专属闸门（按顺序，任一条不过就在**创建任务之前**失败）：

    1. ``project_id`` 必填且项目必须存在（404 记录在案）；
    2. ``scope_id`` 必须**已关联到该项目** —— 光有 Scope 不算授权证据；
    3. 策略模板解析出的工具必须全部在公网白名单内
       （:func:`core.tool_registry.assert_tools_internet_allowed`）；
    4. 节奏档位由模板缺省与请求合并得出，且**只能收紧**
       （:func:`core.tool_registry.resolve_strategy_pace`）。
       「低频资产发现」因此不会被一次请求改回常规档。

    Phase 3 增补的审计上下文（第 14 节：操作者记录 / 授权备注 / 扫描策略 /
    限速配置 / 超时配置）：``operator`` 与 ``authorization_confirmed`` 透传下去，
    授权备注取**项目上那一份的当前值**作为快照 —— 项目说明事后被改，
    这条任务的依据仍是创建当时那一份。

    模式语义（刻意如此，不要改成「静默降级」）：缺省 ``real``。
    如果环境开关 ``GEF_ALLOW_REAL_SCAN`` 没开，这里会**明确报 403**，
    而不是悄悄退化成 mock —— 「以为打了真实目标、其实拿到假数据」是比
    报错严重得多的误导。

    Args:
        project_id: 授权测试项目 ID，必填。
        scope_id: 项目下已关联的 Scope ID，必填。
        targets: 显式目标列表。
        upload_id: 受控上传 ID（与 ``targets`` 可同时给出）。
        strategy: 策略模板 key，缺省 ``asset_discovery``。
        tools: 自定义工具列表（仅 ``custom`` 模板允许）。
        pace: 请求体显式指定的节奏（可选）。只能把模板档位**收紧**到
            ``light``，不能放松；非法值 400。
        mode: ``real`` / ``mock``；缺省 ``real``。
        scenario: 仅 mock 有效的场景名。
        idempotency_key: 可选幂等键。
        created_by: 创建者标识（``jobs`` 表的列）。
        operator: 操作者标识（Phase 3）。空值退化为 ``local-admin``。
        authorization_confirmed: 页面上的授权确认复选框。**如实记录**是否
            勾选，但**不作为闸门** —— 授权由 Scope / Policy / 环境开关判定，
            一个可被脚本置真的复选框不该成为安全边界。
        rate_limit: 每秒请求上限（可选，只能收紧）。
        timeout_seconds: 单步超时秒数（可选，只能收紧）。

    Returns:
        AuthorizedJobSubmission: 含 job / project / strategy / tools / targets。

    Raises:
        BadRequestError: 项目或 Scope 相关参数缺失、策略非法、工具越权、
            ``rate_limit`` / ``timeout_seconds`` 越界。
        NotFoundError: 项目不存在。
        ScopeViolationError: 环境开关未开，或目标越界。
    """
    project = projects.require(str(project_id or "").strip() or "")

    scope_ref = str(scope_id or "").strip()
    if not scope_ref:
        raise BadRequestError(
            "必须提供 scope_id：授权公网测试必须指定项目下的授权范围",
            details={"field": "scope_id"},
        )
    if scope_ref not in project.scope_ids:
        # 「Scope 存在」不等于「这个项目授权了它」。两者都要成立才放行。
        raise BadRequestError(
            f"Scope {scope_ref} 未关联到项目 {project.id}："
            "请先在项目下添加该授权范围，再创建公网测试任务",
            details={
                "field": "scope_id",
                "project_id": project.id,
                "attached_scope_ids": list(project.scope_ids),
            },
        )

    # 工具闸门：先按模板解析，再验公网白名单（顺序不能反 ——
    # 「模板里塞了 nmap」必须在创建任务之前被拒）。
    resolved_strategy = (strategy or "").strip() or None
    resolved_tools = resolve_strategy_tools(resolved_strategy, split_str_list(tools, "tools"))
    assert_tools_internet_allowed(resolved_tools)

    # 节奏闸门：模板自带一档（当前三档模板全是 ``light``），请求体只能收紧。
    # 放在工具闸门之后：工具越权是更根本的问题，应当先报出来。
    resolved_pace = resolve_strategy_pace(resolved_strategy, pace)

    # 授权确认：如实**记录**，但不作为闸门。理由写在参数文档里 ——
    # 一个 JSON 布尔值不构成安全边界，把它当闸门只会制造「已授权」的错觉。
    confirmed: bool | None = None
    if authorization_confirmed is not None:
        confirmed = bool(authorization_confirmed)

    # 缺省 real：公网授权测试的语义就是真实扫描，不静默降级。
    submission = create_scan_job(
        scope_id=scope_ref,
        targets=targets,
        tools=resolved_tools,
        upload_id=upload_id,
        mode=mode or MODE_REAL,
        scenario=scenario,
        idempotency_key=idempotency_key,
        created_by=created_by,
        pace=resolved_pace,
        operator=operator,
        # 项目与策略在这里落成审计上下文：它们是「公网入口」独有的信息，
        # 因此只有这条链会传（老入口 ``POST /api/jobs`` 传的是 ``None``）。
        project_id=project.id,
        strategy=resolved_strategy or STRATEGY_ASSET_DISCOVERY,
        # 授权说明取**项目上那一份的当前值**当快照：项目说明事后被改，
        # 这条任务所依据的仍然是创建当时那一份原文。
        authorization_note=project.authorization_note,
        authorization_confirmed=confirmed,
        rate_limit=rate_limit,
        timeout_seconds=timeout_seconds,
    )

    return AuthorizedJobSubmission(
        submission=submission,
        project=project,
        strategy=resolved_strategy or STRATEGY_ASSET_DISCOVERY,
        tools=resolved_tools,
        targets=list(submission.targets),
        pace=resolved_pace,
    )
