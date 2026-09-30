"""本地应用数据库连接管理（M2 起）。

与 ``storage.py`` 的关系：

* ``storage.py`` 负责旧的「按工具建表」扫描结果库（``results/scan_results.db``），
  本机联调期间把它当作**只读历史数据**，不再往里加新表；
* 本模块负责新的本机应用库（默认 ``results/local.db``），承载
  ``scopes`` / ``audit_events`` / ``jobs`` 等 M2、M3 引入的实体。

数据库要求（方案第 7 节）：

* 开启 WAL；
* 设置 ``busy_timeout``；
* 所有写操作使用事务。

连接通过 :func:`connect` 获取，调用方负责 ``with`` 块或显式关闭；
:func:`transaction` 提供带自动提交/回滚的上下文管理器。
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager

from config import LOCAL_DB_CONFIG

# SQLite 默认写锁等待时间：本机多线程（waitress 8 线程）下必须显式设置，
# 否则并发写会立刻抛 "database is locked"。
BUSY_TIMEOUT_MS = 5000


def db_path() -> str:
    """当前应用库路径。"""
    return LOCAL_DB_CONFIG["path"]


def ensure_db_dir(path: str | None = None) -> str:
    """确保数据库所在目录存在，返回库路径。"""
    target = path or db_path()
    directory = os.path.dirname(os.path.abspath(target))
    if directory:
        os.makedirs(directory, exist_ok=True)
    return target


def connect(path: str | None = None) -> sqlite3.Connection:
    """建立一个新的 SQLite 连接（已应用 WAL 与 busy_timeout）。"""
    target = ensure_db_dir(path)
    conn = sqlite3.connect(target, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def transaction(path: str | None = None):
    """写操作事务上下文：正常退出提交，异常回滚。

    用法::

        with transaction() as conn:
            conn.execute("INSERT INTO scopes ...", (...))
    """
    conn = connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def init_schema(path: str | None = None) -> None:
    """创建本机应用库的全部表（幂等）。"""
    with transaction(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scopes (
                id               TEXT PRIMARY KEY,
                name             TEXT NOT NULL,
                allowed_domains  TEXT NOT NULL DEFAULT '[]',
                allowed_cidrs    TEXT NOT NULL DEFAULT '[]',
                excluded_domains TEXT NOT NULL DEFAULT '[]',
                active_scan      INTEGER NOT NULL DEFAULT 0,
                created_at       TEXT NOT NULL,
                created_by       TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type  TEXT NOT NULL,
                actor       TEXT NOT NULL DEFAULT 'local-admin',
                target_id   TEXT,
                detail_json TEXT NOT NULL DEFAULT '{}',
                created_at  TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_events_type ON audit_events(event_type)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_events_created ON audit_events(created_at)"
        )
        # M2 的受控上传记录：file_path 只能来自这里登记过的 upload_id。
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS uploads (
                id           TEXT PRIMARY KEY,
                original_name TEXT NOT NULL,
                stored_path  TEXT NOT NULL,
                size         INTEGER NOT NULL DEFAULT 0,
                sha256       TEXT,
                target_count INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT NOT NULL,
                created_by   TEXT
            )
            """
        )

        # ── M3 异步任务 ─────────────────────────────────────
        # jobs 本身就是队列：靠 status + created_at 取任务，靠 worker_id +
        # lease_until 做租约（方案第 7 节 jobs 字段表）。
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id               TEXT PRIMARY KEY,
                scope_id         TEXT NOT NULL,
                status           TEXT NOT NULL,
                mode             TEXT NOT NULL DEFAULT 'mock',
                targets_json     TEXT NOT NULL DEFAULT '[]',
                tools_json       TEXT NOT NULL DEFAULT '[]',
                upload_id        TEXT,
                scenario         TEXT,
                created_by       TEXT,
                created_at       TEXT NOT NULL,
                started_at       TEXT,
                finished_at      TEXT,
                progress         INTEGER NOT NULL DEFAULT 0,
                total_steps      INTEGER NOT NULL DEFAULT 0,
                done_steps       INTEGER NOT NULL DEFAULT 0,
                error_code       TEXT,
                error_message    TEXT,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                worker_id        TEXT,
                lease_until      TEXT,
                attempt          INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_scope ON jobs(scope_id)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS job_steps (
                id            TEXT PRIMARY KEY,
                job_id        TEXT NOT NULL,
                tool_name     TEXT NOT NULL,
                target        TEXT NOT NULL,
                status        TEXT NOT NULL DEFAULT 'pending',
                attempt       INTEGER NOT NULL DEFAULT 1,
                started_at    TEXT,
                finished_at   TEXT,
                exit_code     INTEGER,
                error_code    TEXT,
                error_message TEXT,
                artifact_id   TEXT,
                found_count   INTEGER NOT NULL DEFAULT 0,
                results_json  TEXT NOT NULL DEFAULT '[]'
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_job_steps_job ON job_steps(job_id, status)"
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS job_events (
                id          TEXT PRIMARY KEY,
                job_id      TEXT NOT NULL,
                event_type  TEXT NOT NULL,
                detail_json TEXT NOT NULL DEFAULT '{}',
                created_at  TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_job_events_job ON job_events(job_id, id)"
        )

        # ── M4 原始证据 ─────────────────────────────────────
        # stdout/stderr 原文落盘后在这里登记，支持「结果详情能看到原始证据」。
        # path 只是服务端内部信息，API 出参一律不带（见 core/artifacts.py）。
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                id            TEXT PRIMARY KEY,
                job_id        TEXT,
                step_id       TEXT,
                kind          TEXT NOT NULL,
                path          TEXT NOT NULL,
                sha256        TEXT,
                size          INTEGER NOT NULL DEFAULT 0,
                original_size INTEGER NOT NULL DEFAULT 0,
                truncated     INTEGER NOT NULL DEFAULT 0,
                tool_name     TEXT,
                target        TEXT,
                created_at    TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_job ON artifacts(job_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_step ON artifacts(step_id)")

        # ── P0-5 导出记录 ───────────────────────────────────
        # 导出文件本身落在 exports/ 目录；这里只登记元数据。
        # `path` 是服务端内部信息，API 出参一律不带（见 core/exports.py）。
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS exports (
                id          TEXT PRIMARY KEY,
                filename    TEXT NOT NULL,
                path        TEXT NOT NULL,
                format      TEXT NOT NULL,
                row_count   INTEGER NOT NULL DEFAULT 0,
                size        INTEGER NOT NULL DEFAULT 0,
                sha256      TEXT,
                created_at  TEXT NOT NULL,
                created_by  TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_exports_created ON exports(created_at)")

        _migrate_columns(conn)


# 增量列迁移：``CREATE TABLE IF NOT EXISTS`` 对已存在的表不会补列，
# 而本机联调是「同一个 results/local.db 一路用下去」，所以必须显式 ALTER。
_COLUMN_MIGRATIONS: dict[str, dict[str, str]] = {
    # M4：结构化观测结果与执行元数据落到步骤上，任务详情页才能显示
    # 状态码/标题/技术栈以及耗时与脱敏命令预览。
    "job_steps": {
        "observations_json": "TEXT NOT NULL DEFAULT '[]'",
        "duration_ms": "INTEGER",
        "command_preview": "TEXT",
        "parser_version": "TEXT",
    },
}


def _existing_columns(conn, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def _migrate_columns(conn) -> None:
    """为已存在的表补上新增列（幂等）。"""
    for table, columns in _COLUMN_MIGRATIONS.items():
        present = _existing_columns(conn, table)
        if not present:
            continue
        for column, ddl in columns.items():
            if column not in present:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


# 已经初始化过表结构的库路径（同一个进程内重复调用 init_schema 没有意义）。
_initialized: set[str] = set()


def ensure_schema(path: str | None = None) -> None:
    """确保表结构存在，且每个库路径只真正建表一次。

    这是给调用方（审计、Scope、上传等）用的便捷入口：它们不必关心
    「表建了没」，直接写自己的数据即可。
    """
    target = os.path.abspath(path or db_path())
    if target in _initialized:
        return
    init_schema(path)
    _initialized.add(target)


def reset_schema_cache() -> None:
    """清空建表缓存（测试切换库路径时使用）。"""
    _initialized.clear()
