"""原始执行证据（artifact）的落盘与登记（M4）。

方案第 6.2 / M4 要求：

* 保存 stdout/stderr artifact；
* 「结果详情能看到原始证据」——但要遵守第 8.2 节「禁止把工具 stderr
  原样暴露给前端」。

因此本模块刻意分成两件事：

1. **落盘**：把子进程的 stdout/stderr **原样**写进
   ``results/artifacts/<artifact_id>.out`` / ``.err``，并在 ``artifacts``
   表里登记 ``sha256`` / ``size`` / 是否截断。原始文件只在本机磁盘上，
   ``.gitignore`` 已忽略 ``results/``，永不入 Git。
2. **读取**：对外的 :func:`read_artifact` 只返回**截断 + 脱敏**后的内容，
   并显式告知是否被截断，避免把整份工具输出或 API Key 甩给前端。

仅当内容非空时才落盘：空 stdout 是很常见的正常情况，没必要产生空文件。
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone

from config import OUTPUT_DIR, SCAN_LIMITS
from core import db
from core.ids import new_artifact_id
from core.runner_result import scrub_text

# 产物目录：与 ``results/`` 同级，测试里会被 monkeypatch 到临时目录。
ARTIFACT_DIR = os.path.join(OUTPUT_DIR, "artifacts")

KIND_STDOUT = "stdout"
KIND_STDERR = "stderr"
# 工具用 ``-o <file>`` 写盘时，真正的结果文件本身也是证据：
# 只看 stdout 会得到空，排查「跑通了但没数据」时反而缺了最关键的一份。
KIND_OUTPUT = "output"
KINDS = (KIND_STDOUT, KIND_STDERR, KIND_OUTPUT)

# 单次读取给前端的最大字节数（默认 64 KB，防止一次性把 10 MB 证据塞进响应）。
DEFAULT_READ_LIMIT = 64 * 1024

_TRUNCATION_NOTICE = "\n\n[!] 内容超过 max_artifact_bytes，已被截断。\n"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def artifacts_dir() -> str:
    """产物目录（不存在则创建）。"""
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    return ARTIFACT_DIR


def artifact_path(artifact_id: str, kind: str = KIND_STDOUT) -> str:
    """产物文件路径（内部使用，不对外暴露）。"""
    suffix = {KIND_STDERR: "err", KIND_OUTPUT: "result"}.get(kind, "out")
    return os.path.join(artifacts_dir(), f"{artifact_id}.{suffix}")


def save_artifact(
    content: str | bytes | None,
    *,
    kind: str = KIND_STDOUT,
    job_id: str | None = None,
    step_id: str | None = None,
    tool_name: str | None = None,
    target: str | None = None,
    max_bytes: int | None = None,
) -> dict | None:
    """把一段原始输出落盘并登记，返回登记行。

    Args:
        content: 原始文本或字节；``None`` / 空内容直接返回 ``None``（不落盘）。
        kind: ``stdout`` 或 ``stderr``。
        job_id / step_id / tool_name / target: 溯源信息。
        max_bytes: 覆盖 :data:`config.SCAN_LIMITS` 的 ``max_artifact_bytes``。

    Returns:
        dict | None: 登记行；无内容时为 ``None``。
    """
    if kind not in KINDS:
        raise ValueError(f"未知的 artifact kind: {kind!r}")

    if content is None:
        return None
    if isinstance(content, bytes):
        raw = content
    else:
        raw = content.encode("utf-8", errors="replace")
    if not raw:
        return None

    limit = int(max_bytes if max_bytes is not None else SCAN_LIMITS["max_artifact_bytes"])
    original_size = len(raw)
    truncated = original_size > limit
    if truncated:
        # 截断时打上明确标记，避免后来者把「不完整的证据」当成全部。
        raw = raw[:limit] + _TRUNCATION_NOTICE.encode("utf-8")

    artifact_id = new_artifact_id()
    path = artifact_path(artifact_id, kind)
    with open(path, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())

    digest = hashlib.sha256(raw).hexdigest()
    created_at = _utcnow()

    db.ensure_schema()
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO artifacts
                (id, job_id, step_id, kind, path, sha256, size, original_size,
                 truncated, tool_name, target, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                job_id,
                step_id,
                kind,
                path,
                digest,
                len(raw),
                original_size,
                1 if truncated else 0,
                tool_name,
                target,
                created_at,
            ),
        )

    return {
        "id": artifact_id,
        "job_id": job_id,
        "step_id": step_id,
        "kind": kind,
        "path": path,
        "sha256": digest,
        "size": len(raw),
        "original_size": original_size,
        "truncated": truncated,
        "tool_name": tool_name,
        "target": target,
        "created_at": created_at,
    }


def get_artifact(artifact_id: str) -> dict | None:
    """按 ID 读取登记行（含 ``path``，仅供服务端内部使用）。"""
    db.ensure_schema()
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return _row_to_dict(row)


def list_artifacts(job_id: str | None = None, step_id: str | None = None, limit: int = 100) -> list[dict]:
    """列出产物登记行（结果详情页的「原始证据」列表）。

    返回值**不带** ``path``：路径是服务端实现细节，前端只需要 ``id``。
    """
    db.ensure_schema()
    sql = "SELECT * FROM artifacts"
    params: list = []
    where: list[str] = []
    if job_id:
        where.append("job_id = ?")
        params.append(job_id)
    if step_id:
        where.append("step_id = ?")
        params.append(step_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at ASC, rowid ASC LIMIT ?"
    params.append(int(limit))

    conn = db.connect()
    try:
        rows = conn.execute(sql, tuple(params)).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(row, include_path=False) for row in rows]


def read_artifact(
    artifact_id: str,
    *,
    limit: int = DEFAULT_READ_LIMIT,
    scrub: bool = True,
) -> dict | None:
    """读取产物内容用于展示。

    * 返回 ``text``（已按 ``limit`` 截断）、``size``、``sha256``、``truncated``；
    * ``scrub=True`` 时对内容做凭据脱敏（方案第 8.2 节：不把 stderr 原样给前端）；
    * **不返回服务器文件路径**。

    Returns:
        dict | None: 登记行不存在时返回 ``None``。
    """
    meta = get_artifact(artifact_id)
    if meta is None:
        return None

    path = meta.get("path")
    text = ""
    read_truncated = False
    missing = False
    if not path or not os.path.exists(path):
        missing = True
    else:
        with open(path, "rb") as handle:
            raw = handle.read(int(limit) + 1)
        if len(raw) > limit:
            raw = raw[:limit]
            read_truncated = True
        text = raw.decode("utf-8", errors="replace")

    payload = {
        "id": meta["id"],
        "job_id": meta.get("job_id"),
        "step_id": meta.get("step_id"),
        "kind": meta.get("kind"),
        "tool_name": meta.get("tool_name"),
        "target": meta.get("target"),
        "sha256": meta.get("sha256"),
        "size": meta.get("size"),
        "original_size": meta.get("original_size"),
        "truncated": bool(meta.get("truncated")) or read_truncated,
        "missing": missing,
        "created_at": meta.get("created_at"),
        # 原文证据动辄几十 KB；脱敏不能顺带截断（`scrub_command` 是命令预览用的
        # 300 字符规则，套在证据上会把内容砍到只剩头一行）。
        "text": scrub_text(text) if (scrub and text) else text,
    }
    return payload


def _row_to_dict(row, *, include_path: bool = True) -> dict:
    payload = {
        "id": row["id"],
        "job_id": row["job_id"],
        "step_id": row["step_id"],
        "kind": row["kind"],
        "sha256": row["sha256"],
        "size": row["size"],
        "original_size": row["original_size"],
        "truncated": bool(row["truncated"]),
        "tool_name": row["tool_name"],
        "target": row["target"],
        "created_at": row["created_at"],
    }
    if include_path:
        payload["path"] = row["path"]
    return payload
