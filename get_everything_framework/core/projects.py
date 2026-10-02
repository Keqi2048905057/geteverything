"""授权测试项目（公网授权测试模式体验版方案第 4 节步骤 1、2）。

## 定位

「项目」是**授权证据的组织单位**：一次授权测试对应一个项目，项目下挂着由
它派生出来、并被显式列入白名单的 Scope。它回答的是「这批公网目标是谁授权的、
授权说明是什么、谁负责」，而**不**回答「能不能扫」——后者仍然是
``core.policy`` 与 ``core.scope`` 的职责。

## 为什么单独建表而不是给 ``scopes`` 加列

方案第 10 节明确「本阶段禁止修改数据库核心结构」，且 ``docs/DECISIONS.md``
的 E 项口径是「只允许新增表 + 新增迁移脚本，**不得改动现有表结构**」。
因此这里采用**两张纯新增表**：

* ``projects``        —— 项目的名称 / 授权说明 / 负责人；
* ``project_scopes``  —— 项目与 Scope 的多对多关联。

``scopes`` 表一个字段都没动，旧库与既有数据完全不受影响；回滚只需
``DROP TABLE project_scopes; DROP TABLE projects;``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from core import db
from core.errors import BadRequestError, NotFoundError
from core.ids import new_project_id

#: 单个项目最多关联多少个 Scope（防御性上限，避免一次请求展开过多目标）。
MAX_SCOPES_PER_PROJECT = 50

#: 授权说明最短长度。空说明等于没有授权证据，必须拦住。
MIN_AUTHORIZATION_NOTE_LENGTH = 4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Project:
    """一个授权测试项目。

    Attributes:
        id: ``proj_<uuid4hex>``。
        name: 项目名称。
        authorization_note: 授权说明（谁授权、范围、凭据形式）。
        owner: 负责人。
        created_at: 创建时间（UTC ISO）。
        created_by: 创建者标识。
        scope_ids: 关联的 Scope ID 列表。
    """

    id: str
    name: str
    authorization_note: str
    owner: str
    created_at: str
    created_by: str
    scope_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "authorization_note": self.authorization_note,
            "owner": self.owner,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "scope_ids": list(self.scope_ids),
            "scope_count": len(self.scope_ids),
        }


def _normalize_text(value, field_name: str, *, required: bool, max_length: int = 500) -> str:
    """规范化自由文本字段（项目名 / 授权说明 / 负责人）。"""
    if value is None:
        text = ""
    elif isinstance(value, (list, dict)):
        raise BadRequestError(f"{field_name} 必须是字符串", details={"field": field_name})
    else:
        text = str(value).strip()
    if required and not text:
        raise BadRequestError(f"{field_name} 为必填项", details={"field": field_name})
    if len(text) > max_length:
        raise BadRequestError(
            f"{field_name} 最长 {max_length} 个字符", details={"field": field_name}
        )
    return text


def _normalize_scope_ids(scope_ids) -> list[str]:
    """规范化 Scope ID 列表（去重保序、限制数量）。"""
    if scope_ids is None:
        return []
    if isinstance(scope_ids, str):
        items = [item.strip() for item in scope_ids.split(",")]
    elif isinstance(scope_ids, (list, tuple)):
        items = [str(item).strip() for item in scope_ids]
    else:
        raise BadRequestError("scope_ids 必须是字符串数组", details={"field": "scope_ids"})

    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        if item and item not in seen:
            unique.append(item)
            seen.add(item)

    if len(unique) > MAX_SCOPES_PER_PROJECT:
        raise BadRequestError(
            f"单个项目最多关联 {MAX_SCOPES_PER_PROJECT} 个 Scope，当前 {len(unique)} 个",
            details={"field": "scope_ids", "max_scopes_per_project": MAX_SCOPES_PER_PROJECT},
        )
    return unique


def _scope_ids_of(conn, project_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT scope_id FROM project_scopes WHERE project_id = ? ORDER BY rowid",
        (project_id,),
    ).fetchall()
    return [row["scope_id"] for row in rows]


def _row_to_project(row, scope_ids: list[str]) -> Project:
    return Project(
        id=row["id"],
        name=row["name"],
        authorization_note=row["authorization_note"] or "",
        owner=row["owner"] or "",
        created_at=row["created_at"],
        created_by=row["created_by"],
        scope_ids=scope_ids,
    )


def _verify_scopes_exist(scope_ids: list[str]) -> None:
    """提前确认每个 Scope 真实存在，避免建出「挂着不存在 Scope 的项目」。

    这里只做「存在性」检查，**不做**任何 Scope 内目标的合法性判定
    —— 那是 ``core.policy`` 的职责，本模块不越界。
    """
    from core import scope_store

    for scope_id in scope_ids:
        if scope_store.get(scope_id) is None:
            raise BadRequestError(
                f"Scope 不存在: {scope_id}",
                details={"field": "scope_ids", "scope_id": scope_id},
            )


def create(
    *,
    name,
    authorization_note,
    owner="",
    scope_ids=None,
    created_by: str = "local-admin",
) -> Project:
    """创建授权测试项目（可同时关联若干已存在的 Scope）。

    Args:
        name: 项目名称，必填。
        authorization_note: 授权说明，必填且至少
            :data:`MIN_AUTHORIZATION_NOTE_LENGTH` 个字符 —— 空说明等于没有授权证据。
        owner: 负责人，可空。
        scope_ids: 关联的 Scope ID 列表，可空（稍后用
            :func:`attach_scope` 追加）。
        created_by: 创建者标识。

    Returns:
        Project: 落库后的项目。

    Raises:
        BadRequestError: 字段缺失/超长，或关联了不存在的 Scope。
    """
    project_name = _normalize_text(name, "name", required=True, max_length=120)
    note = _normalize_text(
        authorization_note, "authorization_note", required=True, max_length=1000
    )
    if len(note) < MIN_AUTHORIZATION_NOTE_LENGTH:
        raise BadRequestError(
            f"authorization_note 至少 {MIN_AUTHORIZATION_NOTE_LENGTH} 个字符："
            "授权说明是公网扫描的授权证据，不能留空",
            details={"field": "authorization_note"},
        )
    owner_text = _normalize_text(owner, "owner", required=False, max_length=120)
    resolved_scopes = _normalize_scope_ids(scope_ids)

    _verify_scopes_exist(resolved_scopes)

    project = Project(
        id=new_project_id(),
        name=project_name,
        authorization_note=note,
        owner=owner_text,
        created_at=_now(),
        created_by=created_by,
        scope_ids=resolved_scopes,
    )

    db.ensure_schema()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO projects (id, name, authorization_note, owner, created_at, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                project.id,
                project.name,
                project.authorization_note,
                project.owner,
                project.created_at,
                project.created_by,
            ),
        )
        for scope_id in resolved_scopes:
            conn.execute(
                "INSERT INTO project_scopes (project_id, scope_id, created_at) VALUES (?, ?, ?)",
                (project.id, scope_id, project.created_at),
            )
    return project


def get(project_id: str) -> Project | None:
    """按 ID 读取项目，不存在返回 ``None``。"""
    db.ensure_schema()
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            return None
        return _row_to_project(row, _scope_ids_of(conn, project_id))
    finally:
        conn.close()


def require(project_id: str) -> Project:
    """读取项目；不存在时抛 :class:`NotFoundError`（404）。"""
    project = get(project_id)
    if project is None:
        raise NotFoundError(f"项目不存在: {project_id}", details={"project_id": project_id})
    return project


def list_all(limit: int = 200) -> list[Project]:
    """按创建时间倒序列出项目。"""
    limit = max(1, min(int(limit or 200), 1000))
    db.ensure_schema()
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM projects ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_project(row, _scope_ids_of(conn, row["id"])) for row in rows]
    finally:
        conn.close()


def attach_scope(project_id: str, scope_id: str) -> Project:
    """把一个已存在的 Scope 追加到项目下（重复关联是幂等的）。

    Raises:
        NotFoundError: 项目不存在。
        BadRequestError: Scope 不存在，或已超出单项目 Scope 上限。
    """
    project = require(project_id)
    scope_ref = str(scope_id or "").strip()
    if not scope_ref:
        raise BadRequestError("scope_id 为必填项", details={"field": "scope_id"})

    _verify_scopes_exist([scope_ref])

    db.ensure_schema()
    with db.transaction() as conn:
        already = conn.execute(
            "SELECT 1 FROM project_scopes WHERE project_id = ? AND scope_id = ?",
            (project.id, scope_ref),
        ).fetchone()
        if already is not None:
            # 幂等：重复关联不报错、不产生第二行。
            return project
        current = _scope_ids_of(conn, project.id)
        if len(current) >= MAX_SCOPES_PER_PROJECT:
            raise BadRequestError(
                f"单个项目最多关联 {MAX_SCOPES_PER_PROJECT} 个 Scope",
                details={"project_id": project.id, "max_scopes_per_project": MAX_SCOPES_PER_PROJECT},
            )
        conn.execute(
            "INSERT INTO project_scopes (project_id, scope_id, created_at) VALUES (?, ?, ?)",
            (project.id, scope_ref, _now()),
        )
    return require(project_id)


def find_by_scope(scope_id: str) -> Project | None:
    """反查某个 Scope 挂在哪个项目下（取最早关联的那个）。

    任务创建时用它做「是否属于授权项目」的展示与审计，**不作为权限判断**
    —— 权限判断只看 Scope 与 Policy。
    """
    ref = str(scope_id or "").strip()
    if not ref:
        return None
    db.ensure_schema()
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT p.* FROM projects p JOIN project_scopes ps ON ps.project_id = p.id "
            "WHERE ps.scope_id = ? ORDER BY ps.rowid LIMIT 1",
            (ref,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_project(row, _scope_ids_of(conn, row["id"]))
    finally:
        conn.close()


def scoped_target_hint(project: Project) -> dict:
    """项目的只读摘要（给前端渲染用，不含任何目标清单）。

    刻意**不**下发各 Scope 的 ``allowed_domains``：目标清单属于敏感信息，
    需要时单独调 ``GET /api/scopes/<id>``。
    """
    return {
        "id": project.id,
        "name": project.name,
        "owner": project.owner,
        "scope_count": len(project.scope_ids),
        "scope_ids": list(project.scope_ids),
    }


# 供测试与调用方复用（避免 import 私有名）。
__all__ = [
    "MAX_SCOPES_PER_PROJECT",
    "MIN_AUTHORIZATION_NOTE_LENGTH",
    "Project",
    "attach_scope",
    "create",
    "find_by_scope",
    "get",
    "list_all",
    "require",
    "scoped_target_hint",
]