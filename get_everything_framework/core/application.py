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

## 各处共享的同一条入口

```text
POST /api/jobs      → api/jobs.py:create_job      ┐
首页表单 POST /      → app.py:index               ├→ create_scan_job()
Agent（P0-6 迁移中） → agent/action.py            ┘
```
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from config import SCAN_LIMITS
from core import audit, jobs as jobs_store, observability, uploads
from core.errors import BadRequestError
from core.mock import SCENARIOS, normalize_scenario
from core.policy import validate_job_targets
from core.safety import MODE_MOCK, MODE_REAL, resolve_mode
from core.scope import Scope


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
    """

    job: dict
    scope: Scope
    mode: str
    tools: list[str]
    targets: list[str]
    reused: bool
    idempotency_key: str | None = None

    def to_dict(self) -> dict:
        """``POST /api/jobs`` 的响应体（202 Accepted）。"""
        return {
            "ok": True,
            "job_id": self.job["id"],
            "status": self.job["status"],
            "mode": self.job["mode"],
            "total_steps": self.job["total_steps"],
            "scope_id": self.job["scope_id"],
            "reused": self.reused,
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
) -> JobSubmission:
    """创建扫描任务的**唯一**编排入口。

    校验顺序（与历史上 ``api/jobs.py:create_job`` 逐条一致，不重排）：
    目标非空 → 工具非空 → 幂等键合法 → 工具受支持 → 目标数上限 →
    Scope/Policy → 模式开关 → mock 场景名 → 落库 → 审计 + 结构化日志。

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
        created_by: 创建者标识。

    Returns:
        JobSubmission: 含 job / scope / 实际入库的 tools 与 targets。

    Raises:
        BadRequestError: 参数缺失或格式非法。
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
    )

    detail: dict = {
        "scope_id": scope.id,
        "mode": resolved_mode,
        "tools": selected_tools,
        "targets": validated_targets,
        "total_steps": job["total_steps"],
    }
    if reused:
        # 命中已有任务不是一次「新建」：只记一条「重复请求被折叠」的可追溯记录
        # （事件类型仍是 job_created，target_id 指向那个已存在的任务）。
        detail["idempotency_key"] = key
        detail["reused"] = True
    audit.record(audit.EVENT_JOB_CREATED, target_id=job["id"], detail=detail)

    # 方案第 19 节：任务创建也进结构化日志。request_id 由 app.py 的
    # before_request 绑定（服务层不感知 Flask）；**只记工具名与数量，不记目标列表**
    # （完整目标在 audit_events 与 job 快照里，那是有意留存的审计数据）。
    observability.log_event(
        observability.EVENT_JOB_CREATED,
        job_id=job["id"],
        scope_id=scope.id,
        mode=resolved_mode,
        tools=selected_tools,
        target_count=len(validated_targets),
        total_steps=job["total_steps"],
        reused=reused,
    )

    return JobSubmission(
        job=job,
        scope=scope,
        mode=resolved_mode,
        tools=selected_tools,
        targets=validated_targets,
        reused=reused,
        idempotency_key=key,
    )
