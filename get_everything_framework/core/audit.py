"""审计事件（M2）。

方案第 5.6 节要求设置写入必须写审计日志；第 7 节要求有 ``audit_events`` 表。
本机联调版只记录「谁在什么时候对哪个对象做了什么」，不记录密钥明文。
"""

from __future__ import annotations

from datetime import datetime, timezone

from core import db

# 事件类型（字符串常量，便于日志检索与测试断言）
EVENT_SETTINGS_UPDATED = "settings.updated"
EVENT_SCOPE_CREATED = "scope.created"
EVENT_UPLOAD_CREATED = "upload.created"
EVENT_LOGIN_SUCCEEDED = "auth.login_succeeded"
EVENT_LOGIN_FAILED = "auth.login_failed"
# M3：任务生命周期（job_events 记细粒度过程，audit_events 记「谁动了什么」）
EVENT_JOB_CREATED = "job.created"
EVENT_JOB_CANCELLED = "job.cancelled"
EVENT_JOB_RETRY_REQUESTED = "job.retry_requested"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record(event_type: str, *, actor: str = "local-admin", target_id: str | None = None, detail: dict | None = None):
    """写入一条审计事件。

    Args:
        event_type: :data:`EVENT_*` 常量之一。
        actor: 触发者标识，本机联调版固定为本地管理员。
        target_id: 关联对象 ID（scope_id / job_id / upload_id 等）。
        detail: 附加信息，**禁止**放入密钥明文。
    """
    import json

    db.ensure_schema()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO audit_events (event_type, actor, target_id, detail_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (event_type, actor, target_id, json.dumps(detail or {}, ensure_ascii=False), _now()),
        )


def list_events(limit: int = 50) -> list[dict]:
    """按时间倒序读取最近的审计事件。"""
    import json

    limit = max(1, min(int(limit or 50), 500))
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id, event_type, actor, target_id, detail_json, created_at "
            "FROM audit_events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    events = []
    for row in rows:
        try:
            detail = json.loads(row["detail_json"])
        except (TypeError, ValueError):
            detail = {}
        events.append(
            {
                "id": row["id"],
                "event_type": row["event_type"],
                "actor": row["actor"],
                "target_id": row["target_id"],
                "detail": detail,
                "created_at": row["created_at"],
            }
        )
    return events
