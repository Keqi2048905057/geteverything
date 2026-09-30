"""
资产收集工具 — API 接口层

Blueprint 注册入口，所有 API 路由统一通过 /api 前缀挂载。

目录结构:
    api/
    ├── __init__.py       # Blueprint 注册入口 (本文件)
    ├── auth.py           # /api/auth/*                        — 本地管理员登录/登出/会话
    ├── health.py         # /health                            — 健康检查（无需登录）
    ├── tools.py          # /api/tools, /api/databases       — 工具列表 & 数据库信息
    ├── scan.py           # /api/run, /api/tool/<name>/run   — 扫描执行（需登录）
    ├── results.py        # /api/results, /api/tool/<name>/results, /api/export — 结果查询 & 导出
    ├── upload.py         # /api/upload                      — Agent 目标上传
    └── settings.py       # /api/settings                    — 系统配置（需登录）

调用流程:
    Frontend → API 路由 → core.auth 认证 → tool_runner / storage → SQLite 数据库
"""

from flask import Blueprint

api_bp = Blueprint("api", __name__, url_prefix="/api")

from api import tools     # noqa: E402, F401
from api import auth      # noqa: E402, F401
from api import scopes    # noqa: E402, F401
from api import jobs      # noqa: E402, F401
from api import scan      # noqa: E402, F401
from api import results   # noqa: E402, F401
from api import upload    # noqa: E402, F401
from api import settings  # noqa: E402, F401

# health 使用独立的 health_bp（挂在根路径 /health，而不是 /api/health），
# 由 app.py 显式注册；此处仅保证模块可被导入。
from api import health    # noqa: E402, F401
