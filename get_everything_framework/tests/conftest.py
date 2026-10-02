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

# 下面两个开关必须**钉死**，不能 setdefault：它们是「本机 .env 会写什么」与
# 「测试要断言什么」之间的交界，一旦测试跟随开发者本机的 .env，
# 同一份代码在这台机器绿、在那台机器红，且红的原因与被测代码毫无关系。
#
# 实测到的两处（都是本机存在 .env 之后才暴露）：
#
# * ``GEF_ALLOW_REAL_SCAN`` —— 本机 .env 里为 true 时，
#   ``test_m2_security.py::test_health_exposes_mode_and_security_status``
#   断言的 ``real_scan_enabled is False`` 被顶掉；
# * ``GEF_LOG_FORMAT`` —— 本机 .env 里为 text 时，
#   ``test_observability.py::test_configure_logging_filters_by_level``
#   解析 JSON 失败（该用例断言的是 json 格式的默认行为）。
#
# 需要 real 模式的用例自己用 ``monkeypatch.setenv`` 打开（见
# ``test_m4_runner_result.py::real_mode``），用例结束会自动回滚到这里设的值。
# ``config.load_dotenv()`` 默认**不覆盖**已存在的环境变量，因此这里的赋值
# 足以让 .env 不再影响测试。
os.environ["GEF_ALLOW_REAL_SCAN"] = "false"
os.environ["GEF_LOG_FORMAT"] = "json"

# 扫描节奏（``core.pace``）里低频档的**步骤间隔**同样钉死为 0：
# 它是「礼貌地少发请求」这一产品行为，不该让整个测试套件为每个低频任务
# 多付 1.5 秒墙钟（``asset_discovery`` 这类模板的缺省档就是 ``light``）。
# 需要验证「低频确实会等」的用例自己 ``monkeypatch.setenv`` 一个小值
# （见 ``tests/unit/test_pace.py``），因此这里归零不会掩盖该行为。
os.environ["GEF_PACE_LIGHT_STEP_DELAY_SEC"] = "0"

TEST_ADMIN_TOKEN = "test-local-admin-token"


@pytest.fixture(autouse=True)
def _reset_observability_context():
    """每个用例前后清空结构化日志的关联上下文（方案第 19 节）。

    ``core.observability`` 用 ``contextvars`` 绑定 ``request_id`` / ``job_id`` /
    ``step_id`` / ``worker_id``，其生命周期本应由 ``bind()`` / ``shutdown()``
    成对管理。这层兜底保证：即便某个用例（或被测代码）漏了还原，也不会把
    worker 身份串到后续用例里去 —— 测试之间必须互相独立。
    """
    from core import observability

    for name in observability._CONTEXT_ORDER:
        observability._CONTEXT_VARS[name].set(None)
    yield
    for name in observability._CONTEXT_ORDER:
        observability._CONTEXT_VARS[name].set(None)


@pytest.fixture(autouse=True)
def _isolate_runtime_dirs(tmp_path, monkeypatch):
    """把所有**模块级**运行期目录与**本机应用库**钉到临时目录。

    ``AGENTS.md`` 硬约束：测试不得写仓库的 ``results/``、``exports/`` 与
    ``results/local.db``。``app_module`` 里已经逐项 patch 过，但**只用 ``local_db``
    或根本不用夹具的单元用例不经过那个夹具** —— 逐文件实测过三处泄漏：

    * ``tests/unit/test_agent_boundary.py`` 每跑一次往仓库 ``exports/`` 落一个空 CSV
      （``_tool_export_results`` → ``exporter.export_results()``，后者读的是
      **模块级** ``EXPORT_DIR``）；
    * ``tests/unit/test_security_baseline.py`` 的两个上传用例绕过 ``local_db``
      直接调 ``core_uploads.save_upload()``，而它内部走
      ``db.ensure_schema()`` + ``db.transaction()`` → 写的是**仓库**的
      ``results/local.db``（实测单跑一次该文件：``audit_events`` 309 → 312、
      ``uploads`` 309 → 312，每跑一次稳定 +3 行）；
    * ``tests/integration/test_m4_runner_result.py``、``test_observability_chain.py``、
      ``tests/unit/test_jobs_executor.py`` 会让真实 ``Worker`` 刷新仓库的
      ``results/worker_heartbeat``（``jobs/worker.py`` 从 ``config`` 导入了自己的
      ``OUTPUT_DIR`` 副本，patch ``core.health`` 对它无效）。

    这些目录都是「导入期绑定」的普通字符串，只能靠替换模块属性来重定向；
    两个库路径则是模块级字典，就地改 item 即可（``core.db`` 与 ``storage.py``
    拿到的是同一个对象）。这层 autouse 兜底让「某个夹具忘了 patch」不再可能把
    产物写进仓库 —— 实测全量跑完后 ``exports/``、``results/`` 的文件集合与
    ``local.db`` 的哈希完全不变。与 ``app_module`` / ``local_db`` 的 patch 叠加是
    安全的：``monkeypatch`` 按调用顺序回退还原。
    """
    import config
    import core.db as core_db
    import exporter as exporter_module
    import jobs.worker as worker_module
    from core import artifacts as core_artifacts
    from core import health as core_health
    from core import uploads as core_uploads

    monkeypatch.setitem(config.LOCAL_DB_CONFIG, "path", str(tmp_path / "test_local.db"))
    core_db.reset_schema_cache()

    monkeypatch.setattr(exporter_module, "EXPORT_DIR", str(tmp_path / "exports"), raising=False)
    monkeypatch.setattr(core_uploads, "UPLOAD_DIR", str(tmp_path / "uploads"), raising=False)
    monkeypatch.setattr(core_health, "OUTPUT_DIR", str(tmp_path / "results"), raising=False)
    monkeypatch.setattr(worker_module, "OUTPUT_DIR", str(tmp_path / "results"), raising=False)
    monkeypatch.setattr(
        core_artifacts, "ARTIFACT_DIR", str(tmp_path / "results" / "artifacts"), raising=False
    )


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
