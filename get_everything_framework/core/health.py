"""健康检查数据采集。

方案第 5.1 节的 ``GET /health`` 要求：

* 不要求登录；
* 不泄露路径、密钥和完整命令；
* 能区分「Web 正常但 worker 未启动」。

因此这里只输出**状态字符串**（``ok`` / ``missing`` / ``stale`` …），
不外泄任何二进制绝对路径。

工具可用性通过 ``shutil.which`` 在 PATH 上探测；数据库通过 SQLite 只读
URI 连接，避免健康检查本身创建出数据库文件。
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import time

from config import (
    ALTERX_CONFIG,
    AMASS_CONFIG,
    AMASS_INTEL_CONFIG,
    ASSETFINDER_CONFIG,
    DIRSEARCH_CONFIG,
    DNSX_CONFIG,
    ENSCAN_CONFIG,
    FEROXBUSTER_CONFIG,
    GOSPIDER_CONFIG,
    HTTPX_CONFIG,
    KATANA_CONFIG,
    NAABU_CONFIG,
    NMAP_CONFIG,
    ONEFORALL_CONFIG,
    SHUFFLEDNS_CONFIG,
    SQLITE_CONFIG,
    SUBFINDER_CONFIG,
    WAYBACKURLS_CONFIG,
)
from config import OUTPUT_DIR

APP_VERSION = "local-0.1.0"

# 工具名 → 可执行文件名。与 modules/registry.py 的 17 个 runner 一一对应。
TOOL_BINARIES = {
    "alterx": ALTERX_CONFIG["path"],
    "amass": AMASS_CONFIG["path"],
    "amass_intel": AMASS_INTEL_CONFIG["path"],
    "assetfinder": ASSETFINDER_CONFIG["path"],
    "dirsearch": DIRSEARCH_CONFIG["path"],
    "dnsx": DNSX_CONFIG["path"],
    "enscan": ENSCAN_CONFIG["path"],
    "feroxbuster": FEROXBUSTER_CONFIG["path"],
    "gospider": GOSPIDER_CONFIG["path"],
    "httpx": HTTPX_CONFIG["path"],
    "katana": KATANA_CONFIG["path"],
    "naabu": NAABU_CONFIG["path"],
    "nmap": NMAP_CONFIG["path"],
    "oneforall": ONEFORALL_CONFIG["path"],
    "shuffledns": SHUFFLEDNS_CONFIG["path"],
    "subfinder": SUBFINDER_CONFIG["path"],
    "waybackurls": WAYBACKURLS_CONFIG["path"],
}

# worker 心跳超过该秒数视为「未运行中的 worker」。
WORKER_STALE_SECONDS = 30


def tool_status(tool_name: str) -> str:
    """单个工具的可用性：``available`` 或 ``missing``。"""
    binary = TOOL_BINARIES.get(tool_name)
    if not binary:
        return "unknown"
    if os.path.isabs(binary):
        return "available" if os.path.exists(binary) else "missing"
    return "available" if shutil.which(binary) else "missing"


def tools_health() -> dict:
    """全部注册工具的可用性快照。"""
    return {name: tool_status(name) for name in sorted(TOOL_BINARIES)}


def database_health(db_path: str | None = None) -> str:
    """数据库状态：``ok`` / ``missing`` / ``error``。

    使用只读模式打开，确保健康检查不会顺手建库。
    """
    path = db_path or SQLITE_CONFIG["path"]
    if not path or not os.path.exists(path):
        return "missing"
    uri = "file:" + path.replace("\\", "/").replace("?", "%3f").replace("#", "%23") + "?mode=ro"
    conn = None
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=2)
        conn.execute("SELECT 1").fetchone()
        return "ok"
    except sqlite3.Error:
        return "error"
    finally:
        # 只用 ``with sqlite3.connect(...)`` 不会关闭连接，健康检查会被
        # 反复调用，句柄泄漏最终会耗尽文件描述符。
        if conn is not None:
            conn.close()


def worker_health(heartbeat_path: str | None = None) -> str:
    """worker 状态：``ok`` / ``missing`` / ``stale``。

    M1 阶段尚无独立 worker 进程（M3 引入），因此默认返回 ``missing``，
    并通过心跳文件的存在与时间戳区分「从未启动」和「已停止」。

    Args:
        heartbeat_path: 心跳文件路径，默认 ``results/worker_heartbeat``。
    """
    path = heartbeat_path or os.path.join(OUTPUT_DIR, "worker_heartbeat")
    if not path or not os.path.exists(path):
        return "missing"
    try:
        age = time.time() - os.path.getmtime(path)
    except OSError:
        return "missing"
    return "ok" if age <= WORKER_STALE_SECONDS else "stale"


def collect_health() -> dict:
    """组装 ``/health`` 的响应体（不含路径与密钥）。"""
    db_status = database_health()
    worker = worker_health()
    tools = tools_health()
    available = sum(1 for status in tools.values() if status == "available")

    from core.safety import mode_report
    from core.security import security_report

    return {
        "ok": db_status != "error",
        "version": APP_VERSION,
        "database": db_status,
        "worker": worker,
        "queue": queue_health(),
        "tools_summary": {
            "total": len(tools),
            "available": available,
            "missing": len(tools) - available,
        },
        "tools": tools,
        "modes": mode_report(),
        "security": security_report(),
    }


def queue_health() -> dict:
    """队列概况（M3）。

    只返回计数，不返回任何任务内容——``/health`` 不需要登录，
    因此这里绝不能泄露目标域名之类的信息。

    另外：``/health`` 必须是无副作用的。应用库文件还不存在时直接返回
    ``missing``，绝不调用 ``ensure_schema()`` 顺手把库建出来。
    """
    try:
        from core import db as core_db
        from core import jobs as jobs_store

        if not os.path.exists(core_db.db_path()):
            return {"queued": 0, "running": 0, "total": 0, "status": "missing"}
        counts = jobs_store.queue_counts()
    except Exception:
        # 首次启动、表还没建等情况都不应让 /health 失败。
        return {"queued": 0, "running": 0, "total": 0, "status": "missing"}
    return {
        "queued": counts.get("queued", 0),
        "running": counts.get("running", 0),
        "total": counts.get("total", 0),
        "status": "ok",
    }
