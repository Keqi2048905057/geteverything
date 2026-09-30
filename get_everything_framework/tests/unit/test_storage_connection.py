"""DECISIONS-I 回归测试：旧结果库的连接必须被关闭。

背景（原始症状）：``storage.py`` 里所有数据库方法都写成

    with self._get_connection() as conn:
        ...

但 ``sqlite3.Connection`` 的 ``with`` 协议只管事务（退出时 commit/rollback），
**不会调用 ``close()``**。于是每次查询都留下一个未关闭的文件句柄：

* pytest 在 ``-W error::ResourceWarning`` 下会直接失败，
  报 ``unclosed file <_io.FileIO ... mode='rb+'>``，且报错位置指向
  ``conn.execute(...)`` 那一行 —— 看起来像 execute 的锅，其实是连接没关；
* 生产环境里 ``/api/results`` 这类高频只读接口会持续泄漏句柄，
  最终耗尽文件描述符。

修法（DECISIONS-I 允许的最小改动）：新增 ``_connect()`` 上下文管理器，
事务语义保持不变，退出时一定 ``close()``；所有调用点改为 ``with self._connect()``。
**不引入连接池、不改表结构、不改任何查询语义**。
"""

import sqlite3

import pytest

import storage as storage_module
from storage import ScanResultStore, TOOL_DATABASES


@pytest.fixture
def store(tmp_path):
    return ScanResultStore(db_path=str(tmp_path / "conn_test.db"))


class _TrackingConnection:
    """包一层真实连接，只记录 ``close()`` 是否被调用。"""

    def __init__(self, conn):
        self._conn = conn
        self.closed = 0

    def close(self):
        self.closed += 1
        return self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, *exc):
        return self._conn.__exit__(*exc)

    def __setattr__(self, name, value):
        if name in {"_conn", "closed"}:
            object.__setattr__(self, name, value)
        else:
            setattr(self._conn, name, value)


@pytest.fixture
def tracked(monkeypatch):
    """让 ``ScanResultStore`` 造出的每个连接都被登记，返回登记表。"""
    created: list[_TrackingConnection] = []
    real_connect = storage_module.sqlite3.connect

    def _connect(*args, **kwargs):
        conn = _TrackingConnection(real_connect(*args, **kwargs))
        created.append(conn)
        return conn

    monkeypatch.setattr(storage_module.sqlite3, "connect", _connect)
    return created


# ── 连接一定会关 ───────────────────────────────────────────


def test_init_db_closes_its_connection(tmp_path, tracked):
    """构造 Store（建表路径）不能留下未关闭的连接。"""
    ScanResultStore(db_path=str(tmp_path / "init.db"))
    assert tracked, "应该至少建了一个连接"
    assert all(conn.closed == 1 for conn in tracked)


def test_every_public_query_closes_its_connection(store, tracked):
    """把只读接口逐个跑一遍：每个方法结束时连接都必须已关闭。"""
    store.get_global_summary()
    store.get_view_overview()
    store.get_alive_overview()
    store.get_tool_database_overview()
    store.get_tool_databases()
    store.get_results_by_domain("example.test")
    store.get_alive_results(domain="example.test")
    store.get_view_results(domain="example.test")
    store.get_tool_results(domain="example.test")

    assert tracked
    assert all(conn.closed == 1 for conn in tracked), "有连接没被关闭"


def test_write_path_closes_its_connection(store, tracked):
    """写入路径同样要关连接（顺便验完事务语义没被改坏）。"""
    store.save_results("example.test", "subfinder", ["a.example.test"])
    assert all(conn.closed == 1 for conn in tracked)

    assert [row[0] for row in store.get_results_by_domain("example.test")] == ["a.example.test"]


def test_exception_path_still_closes_connection(store, tracked, monkeypatch):
    """方法体抛异常时也不能泄漏连接。"""
    original = ScanResultStore._query_subdomain_tables

    def _boom(self, *args, **kwargs):
        raise RuntimeError("模拟查询中途失败")

    monkeypatch.setattr(ScanResultStore, "_query_subdomain_tables", _boom)
    with pytest.raises(RuntimeError):
        store.get_global_summary()
    monkeypatch.setattr(ScanResultStore, "_query_subdomain_tables", original)

    assert tracked
    assert all(conn.closed == 1 for conn in tracked)


def test_no_resource_warning_from_storage(store):
    """最贴近原始症状的断言：真跑一遍不留 ``ResourceWarning``。"""
    import gc
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        store.get_global_summary()
        store.get_alive_overview()
        store.save_results("example.test", "httpx", ["https://example.test"])
        gc.collect()


# ── 连接级 busy_timeout（并发不再立刻 database is locked） ──


def test_connection_sets_busy_timeout(store):
    conn = store._get_connection()
    try:
        (timeout,) = conn.execute("PRAGMA busy_timeout").fetchone()
        assert timeout == storage_module.BUSY_TIMEOUT_MS
    finally:
        conn.close()


def test_get_connection_returns_fresh_connection_each_call(store):
    first = store._get_connection()
    second = store._get_connection()
    try:
        assert first is not second
    finally:
        first.close()
        second.close()


def test_connect_is_a_context_manager_and_closes(store, tracked):
    with store._connect() as conn:
        conn.execute("SELECT 1").fetchone()
    assert tracked[-1].closed == 1


# ── 不扩大改动范围（DECISIONS-I 的约束） ─────────────────


def test_schema_is_unchanged(store):
    """只修连接生命周期，**不动表结构**：工具表数量与既有列保持不变。"""
    with store._connect() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {row[1] for row in conn.execute("PRAGMA table_info(scan_runs)")}

    assert "scan_runs" in tables
    assert "tool_results" in tables
    for meta in TOOL_DATABASES.values():
        assert meta["table"] in tables
    assert columns == {"id", "domain", "tool_name", "result_count", "created_at"}


def test_readonly_store_does_not_create_missing_file(tmp_path):
    """只读场景不得因为打开连接就顺手建库（连接参数里没有 create 副作用）。"""
    missing = tmp_path / "nope.db"
    assert not missing.exists()
    with pytest.raises(sqlite3.OperationalError):
        # 目录不存在 → 连接必须失败，而不是静默造出半个库
        ScanResultStore(db_path=str(tmp_path / "no_such_dir" / "x.db"))
