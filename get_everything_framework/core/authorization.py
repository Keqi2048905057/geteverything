"""授权匹配（下一阶段方案第 4、6 节 Phase 2：「选择授权」这一步）。

## 要解决的问题

「输入公网目标 → 创建任务」这条路上，用户最容易卡住的不是技术，而是**不知道
自己缺了哪一道闸门**。当前实现下，目标越界、范围没开真实扫描、环境总开关没开
这三件事都表现为同一个 ``403 scope_violation``，用户只能靠读错误消息反推。

本模块把这三件事**在提交之前**摊开：给定一个目标，直接回答

```text
这个目标落在哪些已授权范围内？
每个范围能不能真实扫描？
环境总开关现在是什么状态？
如果都不能，是缺范围、缺 active_scan、还是缺环境开关？
```

## 边界（重要，不要越界）

* **只读**。本模块不创建任务、不写库、不发网络请求，连审计都不写
  （试算是查询，不是业务动作）；
* **不是第二条 Policy 判定**。匹配一律调用 :meth:`core.scope.Scope.match_target`
  —— 那正是 ``Scope.validate_target``（因而也是 ``core.policy``）内部用的同一个
  方法，因此「试算说可以」与「真提交能过」不可能不一致；
* **不做 DNS 解析**。``core.policy.validate_resolved_address`` 那道执行期校验
  依赖真实解析结果，试算阶段刻意不碰网络（离线必须可用）。因此本模块的结论
  是「按 Scope 的字面授权范围能过」，不是「一定能扫成功」—— 返回结构里
  ``resolved_check_deferred`` 明确标注这一点，不假装它已经全查过了。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core import projects, scope_store
from core.errors import InvalidTargetError
from core.scope import (
    MATCH_ALLOWED,
    MATCH_EXCLUDED,
    MATCH_OUT_OF_SCOPE,
    Scope,
    normalize_target,
)
from core.safety import MODE_REAL, real_scan_enabled

#: 试算结论：目标落在这个范围内，且该范围允许真实扫描。
STATUS_READY = "ready"
#: 试算结论：目标落在这个范围内，但该范围未开 ``active_scan``。
STATUS_SCOPE_INACTIVE = "scope_inactive"
#: 试算结论：目标**命中排除列表**（排除优先，即使也在允许范围内）。
STATUS_EXCLUDED = "excluded"
#: 试算结论：目标不在这个范围内。
STATUS_OUT_OF_SCOPE = "out_of_scope"


@dataclass(frozen=True)
class AuthorizationMatch:
    """一个目标在**一个** Scope 上的匹配结果。

    Attributes:
        scope_id / scope_name: 命中的授权范围。
        project_id / project_name: 该范围挂在哪几个项目下（可能多个，取第一个用于展示，
            ``project_ids`` 给全）。没挂项目时为 ``None`` / ``""``。
        verdict: ``allowed`` / ``excluded`` / ``out_of_scope``（来自 ``Scope.match_target``）。
        status: 面向使用者的结论（见本模块 ``STATUS_*``）。
        active_scan: 该范围是否允许真实扫描。
        allowed_domains / allowed_cidrs: 该范围的授权目标清单（管理员本就能通过
            ``GET /api/scopes`` 看到，这里只是省一次请求）。
    """

    scope_id: str
    scope_name: str
    verdict: str
    status: str
    active_scan: bool
    allowed_domains: list[str] = field(default_factory=list)
    allowed_cidrs: list[str] = field(default_factory=list)
    excluded_domains: list[str] = field(default_factory=list)
    project_id: str | None = None
    project_name: str = ""
    project_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "scope_id": self.scope_id,
            "scope_name": self.scope_name,
            "verdict": self.verdict,
            "status": self.status,
            "active_scan": self.active_scan,
            "allowed_domains": list(self.allowed_domains),
            "allowed_cidrs": list(self.allowed_cidrs),
            "excluded_domains": list(self.excluded_domains),
            "project_id": self.project_id,
            "project_name": self.project_name,
            "project_ids": list(self.project_ids),
        }


@dataclass(frozen=True)
class TargetCheck:
    """一个目标的完整试算结果。

    Attributes:
        raw: 用户原始输入。
        normalized: 规范化后的值（域名 / IP）。
        kind: ``domain`` / ``ip``。
        valid: 目标格式是否合法。``False`` 时 ``error_message`` 有原因。
        matches: 所有命中 ``allowed`` 的范围（不含 ``out_of_scope`` 的，避免刷屏）。
        candidates: 被检查过的范围总数（用于区分「没建过范围」与「范围都不覆盖」）。
        real_scan_enabled: 环境总开关的当前状态。
        resolved_check_deferred: 恒为 ``True`` —— 执行期的 DNS 解析校验不在此阶段做。
    """

    raw: str
    normalized: str = ""
    kind: str = ""
    valid: bool = True
    error_message: str = ""
    matches: list[AuthorizationMatch] = field(default_factory=list)
    candidates: int = 0
    real_scan_enabled: bool = False
    resolved_check_deferred: bool = True

    # ── 派生结论 ─────────────────────────────────────────

    @property
    def eligible(self) -> list[AuthorizationMatch]:
        """真正**放行**了该目标的范围（``verdict == allowed``）。

        注意与 ``matches`` 的区别：``matches`` 还把「命中排除列表」的范围也算进来
        （这是有价值的诊断信息 —— 用户需要知道「这个域名是你自己在排除列表里」），
        但那些范围**不构成授权**，判定「能不能扫」时绝不能算进去。
        """
        return [item for item in self.matches if item.verdict == MATCH_ALLOWED]

    @property
    def ready(self) -> bool:
        """是否至少有一个范围既能覆盖目标、又允许真实扫描、且环境开关已开。"""
        return self.real_scan_enabled and any(
            item.status == STATUS_READY for item in self.matches
        )

    @property
    def blocker(self) -> str:
        """一句话说清「为什么现在还不能扫」。``ready`` 时为 ``""``。

        判定顺序刻意从「最上游的缺失」往后排，用户一次就能看到该补哪一步：
        格式 → 有没有范围 → 有没有覆盖 → 覆盖它的范围开没开 → 环境开关。
        """
        if not self.valid:
            return "invalid_target"
        if not self.candidates:
            return "no_scope"

        # 只看**放行**的那些范围：命中排除列表的范围不算授权。
        eligible = self.eligible
        if not eligible:
            return "not_authorized"
        if not any(item.active_scan for item in eligible):
            return "scope_inactive"
        if not self.real_scan_enabled:
            return "env_disabled"
        return ""

    def to_dict(self) -> dict:
        return {
            "raw": self.raw,
            "normalized": self.normalized,
            "kind": self.kind,
            "valid": self.valid,
            "error_message": self.error_message,
            "matches": [item.to_dict() for item in self.matches],
            "eligible_scope_ids": [item.scope_id for item in self.eligible],
            "candidates": self.candidates,
            "real_scan_enabled": self.real_scan_enabled,
            "resolved_check_deferred": self.resolved_check_deferred,
            "ready": self.ready,
            "blocker": self.blocker,
        }


def _project_index() -> dict[str, tuple[str, str, list[str]]]:
    """一次读出 ``scope_id → (项目 ID, 项目名, 全部项目 ID)`` 索引。

    覆盖范围只读一次再复用：逐个 Scope 反查会让「试算 20 个目标 × 50 个范围」
    变成 1000 次查询。一个 Scope 可能被多个项目关联，这里保留最早关联的那个作为
    展示用的主归属，``project_ids`` 给全。
    """
    index: dict[str, tuple[str, str, list[str]]] = {}
    try:
        items = projects.list_all(limit=1000)
    except Exception:  # noqa: BLE001 - 试算不该因为项目表读取失败而整体失败
        return index

    for project in items:
        for scope_id in project.scope_ids:
            if scope_id not in index:
                index[scope_id] = (project.id, project.name, [])
            index[scope_id][2].append(project.id)
    return index


def _match_scope(
    scope: Scope,
    target_value: str,
    project_index: dict[str, tuple[str, str, list[str]]],
) -> AuthorizationMatch | None:
    """把单个 Scope 对单个目标的判定结果包成 :class:`AuthorizationMatch`。

    返回 ``None`` 表示目标不在这个范围内（``out_of_scope``）—— 调用方据此过滤，
    避免把用户建过的所有范围都倒出来。
    """
    try:
        _, verdict = scope.match_target(target_value)
    except InvalidTargetError:
        # 目标格式已经在外层校验过；这里再抛说明调用顺序错了，按「不匹配」处理。
        return None

    if verdict == MATCH_OUT_OF_SCOPE:
        return None

    if verdict == MATCH_EXCLUDED:
        status = STATUS_EXCLUDED
    elif scope.active_scan:
        status = STATUS_READY
    else:
        status = STATUS_SCOPE_INACTIVE

    project_id, project_name, project_ids = project_index.get(scope.id, (None, "", []))

    return AuthorizationMatch(
        scope_id=scope.id,
        scope_name=scope.name,
        verdict=verdict,
        status=status,
        active_scan=bool(scope.active_scan),
        allowed_domains=list(scope.allowed_domains),
        allowed_cidrs=list(scope.allowed_cidrs),
        excluded_domains=list(scope.excluded_domains),
        project_id=project_id,
        project_name=project_name,
        project_ids=list(project_ids),
    )


def check_target(raw: str, *, scopes: list[Scope] | None = None) -> TargetCheck:
    """试算单个目标：它落在哪些已授权范围内，现在能不能扫。

    Args:
        raw: 用户输入的原始目标（域名 / IP / CIDR / URL 都可以，会先规范化）。
        scopes: 可注入的范围列表（测试用）；缺省读全部已建范围。

    Returns:
        TargetCheck: 含匹配结果与「卡在哪一道闸门」的结论。

    本函数**不抛异常**：目标格式非法时返回 ``valid=False`` 的结果，让调用方
    一次性把多个目标的结果都展示出来，而不是第一个坏目标就中断。
    """
    enabled = real_scan_enabled()

    try:
        normalized = normalize_target(raw)
    except InvalidTargetError as exc:
        return TargetCheck(
            raw=str(raw), valid=False, error_message=exc.message, real_scan_enabled=enabled
        )

    scope_list = scopes if scopes is not None else scope_store.list_all(limit=1000)
    project_index = _project_index()

    matches: list[AuthorizationMatch] = []
    for scope in scope_list:
        item = _match_scope(scope, normalized.value, project_index)
        if item is not None:
            matches.append(item)

    # 已授权的排前面，被排除的排最后 —— 用户先看能用的。
    order = {STATUS_READY: 0, STATUS_SCOPE_INACTIVE: 1, STATUS_EXCLUDED: 2}
    matches.sort(key=lambda item: order.get(item.status, 9))

    return TargetCheck(
        raw=str(raw),
        normalized=normalized.value,
        kind=normalized.kind,
        valid=True,
        matches=matches,
        candidates=len(scope_list),
        real_scan_enabled=enabled,
    )


def check_targets(raws: list[str], *, scopes: list[Scope] | None = None) -> list[TargetCheck]:
    """批量试算（保持输入顺序）。"""
    return [check_target(raw, scopes=scopes) for raw in raws]


def default_mode_for(checks: list[TargetCheck]) -> str:
    """根据试算结果给出建议模式。

    只要有**任意一个**目标能走真实扫描就建议 ``real``；全部走不通则建议 ``mock``
    （让用户至少能把流程演练一遍，而不是对着三个红灯发愣）。

    注意这只是**建议**：真正生效的模式仍由 ``create_authorized_public_job``
    与 ``core.safety.resolve_mode`` 决定，本函数不参与任何判定。
    """
    if checks and all(item.ready for item in checks):
        return MODE_REAL
    return "mock"


__all__ = [
    "STATUS_EXCLUDED",
    "STATUS_OUT_OF_SCOPE",
    "STATUS_READY",
    "STATUS_SCOPE_INACTIVE",
    "AuthorizationMatch",
    "TargetCheck",
    "check_target",
    "check_targets",
    "default_mode_for",
]
