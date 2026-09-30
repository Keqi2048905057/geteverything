"""导出记录登记与安全读取（P0-5）。

方案第 3.4 节与 P0-5 的要求：

* **API 绝不返回服务器绝对路径**（``{"path": "C:\\\\..."}`` 是被点名的反例）；
* 导出文件必须能直接下载；
* 导出记录可追溯（谁在什么时候导了什么、多少行、多大、sha256）。

因此本模块把「export_id → 磁盘文件」的映射关在服务端：

* :func:`register_export` 把刚生成的文件登记成一行记录，返回 ``export_id``；
* :func:`get_export` 读取登记行；:func:`resolve_export_file` 只给**内部**
  下载路由用，返回真实路径；
* :func:`to_public_dict` 是唯一对外形状 —— 里面**没有** ``path``。

``exporter.py`` 与 Agent 仍按老签名拿到路径字符串（内部使用），
API 层负责把它换成 ``export_id`` 再出参。
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone

from core import db
from core.ids import new_export_id

#: 允许下载的导出格式（白名单，防止 ``../..`` 之类拼接）。
ALLOWED_FORMATS = ("csv", "json")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256_of(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def register_export(
    path: str,
    *,
    fmt: str,
    row_count: int = 0,
    created_by: str = "local-admin",
) -> dict:
    """登记一个已生成的导出文件，返回公开形状（**不含路径**）。

    Args:
        path: 导出文件的磁盘路径（仅服务端使用）。
        fmt: ``csv`` / ``json``。
        row_count: 导出的记录条数。
        created_by: 触发者标识。

    Returns:
        dict: ``{"export_id", "filename", "format", "row_count", "size",
        "sha256", "created_at", "download_url"}``。

    Raises:
        ValueError: 格式不在白名单内。
    """
    normalized = (fmt or "").strip().lower()
    if normalized not in ALLOWED_FORMATS:
        raise ValueError(f"不支持的导出格式: {fmt!r}")

    export_id = new_export_id()
    filename = os.path.basename(path)
    size = os.path.getsize(path) if os.path.isfile(path) else 0
    record = {
        "id": export_id,
        "filename": filename,
        "path": path,
        "format": normalized,
        "row_count": int(row_count or 0),
        "size": size,
        "sha256": _sha256_of(path),
        "created_at": _utcnow(),
        "created_by": created_by,
    }

    db.ensure_schema()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO exports (id, filename, path, format, row_count, size, sha256, created_at, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record["id"],
                record["filename"],
                record["path"],
                record["format"],
                record["row_count"],
                record["size"],
                record["sha256"],
                record["created_at"],
                record["created_by"],
            ),
        )
    return to_public_dict(record)


def get_export(export_id: str) -> dict | None:
    """按 ID 读取导出记录（内部形状，含 ``path``）。"""
    if not export_id or "/" in export_id or "\\" in export_id:
        return None

    db.ensure_schema()
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM exports WHERE id = ?", (export_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "id": row["id"],
        "filename": row["filename"],
        "path": row["path"],
        "format": row["format"],
        "row_count": row["row_count"],
        "size": row["size"],
        "sha256": row["sha256"],
        "created_at": row["created_at"],
        "created_by": row["created_by"],
    }


def list_exports(limit: int = 50) -> list[dict]:
    """按时间倒序列出导出记录（公开形状）。"""
    limit = max(1, min(int(limit or 50), 500))
    db.ensure_schema()
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM exports ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
    finally:
        conn.close()
    return [
        to_public_dict(
            {
                "id": row["id"],
                "filename": row["filename"],
                "path": row["path"],
                "format": row["format"],
                "row_count": row["row_count"],
                "size": row["size"],
                "sha256": row["sha256"],
                "created_at": row["created_at"],
                "created_by": row["created_by"],
            }
        )
        for row in rows
    ]


def resolve_export_file(export_id: str) -> tuple[str, str] | None:
    """把 ``export_id`` 解析成 ``(绝对路径, 下载文件名)``，不存在返回 ``None``。

    只给内部下载路由使用；**不得**把这个路径写进任何 JSON 响应。
    数据库里的路径如果被外部改动过（例如指向仓库外），这里再校验一次：
    必须是存在的普通文件，否则视为失效。
    """
    record = get_export(export_id)
    if record is None:
        return None
    path = os.path.abspath(record["path"])
    if not os.path.isfile(path):
        return None
    return path, record["filename"]


def to_public_dict(record: dict) -> dict:
    """把导出记录转换成对外形状 —— 刻意不含 ``path``。"""
    return {
        "export_id": record["id"],
        "filename": record["filename"],
        "format": record["format"],
        "row_count": record["row_count"],
        "size": record["size"],
        "sha256": record["sha256"],
        "created_at": record["created_at"],
        "download_url": f"/api/export/{record['id']}/download",
    }
