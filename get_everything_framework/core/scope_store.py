"""Scope 持久化（M2）。

表结构见 :func:`core.db.init_schema`。所有写操作走事务，读操作直接用连接。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from core import db
from core.ids import new_scope_id
from core.scope import Scope


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_scope(row) -> Scope:
    return Scope(
        id=row["id"],
        name=row["name"],
        allowed_domains=json.loads(row["allowed_domains"] or "[]"),
        allowed_cidrs=json.loads(row["allowed_cidrs"] or "[]"),
        excluded_domains=json.loads(row["excluded_domains"] or "[]"),
        active_scan=bool(row["active_scan"]),
        created_at=row["created_at"],
        created_by=row["created_by"],
    )


def create(
    *,
    name: str,
    allowed_domains: list[str] | None = None,
    allowed_cidrs: list[str] | None = None,
    excluded_domains: list[str] | None = None,
    active_scan: bool = False,
    created_by: str = "local-admin",
) -> Scope:
    """创建并持久化一个 Scope。

    ``core.scope.Scope`` 的构造函数会完成全部合法性校验，非法输入直接抛
    :class:`core.errors.InvalidTargetError`，因此本函数不做二次校验。
    """
    scope = Scope(
        id=new_scope_id(),
        name=name,
        allowed_domains=list(allowed_domains or []),
        allowed_cidrs=list(allowed_cidrs or []),
        excluded_domains=list(excluded_domains or []),
        active_scan=active_scan,
        created_at=_now(),
        created_by=created_by,
    )

    db.ensure_schema()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO scopes (id, name, allowed_domains, allowed_cidrs, excluded_domains, "
            "active_scan, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                scope.id,
                scope.name,
                json.dumps(scope.allowed_domains, ensure_ascii=False),
                json.dumps(scope.allowed_cidrs, ensure_ascii=False),
                json.dumps(scope.excluded_domains, ensure_ascii=False),
                1 if scope.active_scan else 0,
                scope.created_at,
                scope.created_by,
            ),
        )
    return scope


def get(scope_id: str) -> Scope | None:
    """按 ID 读取 Scope，不存在返回 ``None``。"""
    db.ensure_schema()
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM scopes WHERE id = ?", (scope_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_scope(row) if row else None


def list_all(limit: int = 200) -> list[Scope]:
    """按创建时间倒序列出 Scope。"""
    limit = max(1, min(int(limit or 200), 1000))
    db.ensure_schema()
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM scopes ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_scope(row) for row in rows]


def require(scope_id: str) -> Scope:
    """读取 Scope；不存在时抛 :class:`ScopeViolationError`。

    没有 Scope 就不允许创建扫描任务（方案第 2.3 节第 2 条），
    因此 M3 创建 job 时会用这个函数做强校验。
    """
    from core.errors import ScopeViolationError

    scope = get(scope_id)
    if scope is None:
        raise ScopeViolationError(
            "指定的 Scope 不存在，禁止创建扫描任务",
            details={"scope_id": scope_id},
        )
    return scope
