"""pytest 全局夹具与路径准备。

约定：项目以源码目录方式运行，测试需要能直接 import 顶层模块
（app / config / storage / tool_runner 等），因此这里显式把项目根
加入 sys.path，保证从仓库根或项目根运行 pytest 都能正常收集。

同时提供 M1 起的公共夹具：

* ``store``   —— 指向临时目录的 ``ScanResultStore``，绝不写仓库里的
  ``results/scan_results.db``（AGENTS.md 硬约束：该库只读）；
* ``client``  —— Flask 测试客户端，且把库指向上述临时实例；
* ``admin_client`` —— 已带本地管理员 Token 头的客户端；
* ``admin_token``  —— 固定测试 Token，避免用例依赖随机值。
"""

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 测试进程统一使用一个固定的强 SECRET_KEY，避免每个用例都触发
# 「未配置强 SECRET_KEY」的 RuntimeWarning 淹没输出。
# 针对弱密钥/临时密钥本身的断言，在用例内通过 monkeypatch 覆盖 Config，
# 不受这里影响。
os.environ.setdefault("SECRET_KEY", "test-only-" + "a1b2c3d4" * 6)

TEST_ADMIN_TOKEN = "test-local-admin-token"


@pytest.fixture
def admin_token(monkeypatch):
    """固定的管理员 Token（通过 monkeypatch 注入 Config，不落盘）。"""
    from config import Config

    monkeypatch.setattr(Config, "LOCAL_ADMIN_TOKEN", TEST_ADMIN_TOKEN, raising=False)
    return TEST_ADMIN_TOKEN


@pytest.fixture
def store(tmp_path):
    """独立的临时 SQLite 存储实例。"""
    from storage import ScanResultStore

    return ScanResultStore(db_path=str(tmp_path / "test_scan_results.db"))


@pytest.fixture
def local_db(tmp_path, monkeypatch):
    """把本机应用库指向临时文件，并返回其路径。

    M3 的 job / worker 测试需要直接操作这个库；单独抽出来是因为有些用例
    不需要整个 Flask app。
    """
    import config
    import core.db as core_db

    path = str(tmp_path / "test_local.db")
    monkeypatch.setitem(config.LOCAL_DB_CONFIG, "path", path)
    core_db.reset_schema_cache()
    core_db.ensure_schema(path)
    return path


@pytest.fixture
def app_module(store, tmp_path, monkeypatch):
    """导入 app 并把两个数据库都指向临时目录。

    * ``storage.SQLITE_CONFIG`` —— 旧的扫描结果库（Web 层直接实例化 Store）；
    * ``config.LOCAL_DB_CONFIG`` —— 本机应用库（scopes / audit_events / uploads / jobs）。

    两者都必须改，否则测试会往仓库的 ``results/`` 里写文件
    （AGENTS.md 硬约束：``results/scan_results.db`` 只读）。

    注意 ``LOCAL_DB_CONFIG`` 是模块级字典，``core.db`` 通过
    ``from config import LOCAL_DB_CONFIG`` 拿到的是同一个对象，
    因此就地改 item 即可让 ``core.db.db_path()`` 生效。
    """
    import config
    import core.db as core_db
    import exporter as exporter_module
    import storage
    from core import artifacts as core_artifacts
    from core import health as core_health
    from core import uploads as core_uploads

    scan_db = str(tmp_path / "test_scan_results.db")
    local_db = str(tmp_path / "test_local.db")
    upload_dir = str(tmp_path / "uploads")
    output_dir = str(tmp_path / "results")

    monkeypatch.setitem(storage.SQLITE_CONFIG, "path", scan_db)
    monkeypatch.setitem(config.LOCAL_DB_CONFIG, "path", local_db)
    # uploads.py 在导入时绑定了 UPLOAD_DIR 字符串，必须直接替换模块属性。
    monkeypatch.setattr(core_uploads, "UPLOAD_DIR", upload_dir, raising=False)
    # worker 心跳文件（/health 读它）也必须落在临时目录，否则会污染仓库 results/。
    monkeypatch.setattr(core_health, "OUTPUT_DIR", output_dir, raising=False)
    # M4 的原始证据目录同理：绝不能把 stdout/stderr 写进仓库 results/artifacts。
    monkeypatch.setattr(core_artifacts, "ARTIFACT_DIR", str(tmp_path / "results" / "artifacts"), raising=False)
    # P0-5 的导出文件同理：导出目录也要落临时目录，不能写仓库 exports/。
    monkeypatch.setattr(exporter_module, "EXPORT_DIR", str(tmp_path / "exports"), raising=False)

    core_db.reset_schema_cache()
    core_db.ensure_schema(local_db)

    import app as app_module

    app_module.app.config.update(TESTING=True)
    return app_module


@pytest.fixture
def client(app_module):
    """匿名 Flask 测试客户端。"""
    return app_module.app.test_client()


@pytest.fixture
def admin_client(app_module, admin_token):
    """已认证的 Flask 测试客户端（带 X-Local-Token 请求头）。"""
    client = app_module.app.test_client()
    client.environ_base["HTTP_X_LOCAL_TOKEN"] = admin_token
    return client
