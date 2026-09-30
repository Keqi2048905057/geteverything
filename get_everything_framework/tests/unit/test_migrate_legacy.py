"""P1 单元测试：旧库 → 统一资产模型的迁移（方案第 12 节；DECISIONS-F）。

DECISIONS-F 的约束是「**只写迁移脚本 + 用临时库验证，不执行真实迁移**」，
本文件就是那个「临时库验证」：

* 旧库用 ``sqlite3`` 现搭一个**最小**结构（只建 3 张工具表 + 泛型表），
  不依赖仓库里那份 253 KB 的真实库；
* 迁移后比对旧库的**字节内容**，证明它是只读的；
* 重跑一次，证明迁移是幂等的（观测 ID 确定性）。
"""

from __future__ import annotations

import hashlib
import sqlite3

import pytest

from core import assets as assets_store
from core import migrate

# 只映射四张表：两张 subdomain（用来验证「跨表重复发现归并成一条资产」）、
# 一张 web（要经 CATEGORY_TO_TYPE 翻译成 url）、一张 port。
TABLE_MAP = {
    "subfinder": {"table": "subfinder_results", "column": "subdomain", "category": "subdomain"},
    "amass": {"table": "amass_results", "column": "subdomain", "category": "subdomain"},
    "httpx": {"table": "httpx_results", "column": "endpoint", "category": "web"},
    "naabu": {"table": "naabu_results", "column": "port_result", "category": "port"},
}


def _build_legacy(path: str, rows_by_table: dict[str, list[tuple]]) -> str:
    """按旧库的真实建表模板搭一个最小旧库。"""
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE scan_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT NOT NULL, "
        "tool_name TEXT NOT NULL, result_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)"
    )
    for tool, meta in TABLE_MAP.items():
        conn.execute(
            f"CREATE TABLE {meta['table']} (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, "
            f"domain TEXT NOT NULL, {meta['column']} TEXT NOT NULL, raw_result TEXT NOT NULL, "
            f"created_at TEXT NOT NULL, UNIQUE(domain, {meta['column']}))"
        )
    conn.execute(
        "CREATE TABLE tool_results (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, "
        "domain TEXT NOT NULL, tool_name TEXT NOT NULL, category TEXT NOT NULL, value TEXT NOT NULL, "
        "created_at TEXT NOT NULL, UNIQUE(domain, tool_name, category, value))"
    )

    for tool, rows in rows_by_table.items():
        if tool == "tool_results":
            conn.executemany(
                "INSERT INTO tool_results (run_id, domain, tool_name, category, value, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
            continue
        column = TABLE_MAP[tool]["column"]
        conn.executemany(
            f"INSERT INTO {TABLE_MAP[tool]['table']} (run_id, domain, {column}, raw_result, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [(run_id, domain, value, value, created_at) for run_id, domain, value, created_at in rows],
        )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def legacy_db(tmp_path):
    """一个含 5 条正常数据 + 2 条脏数据的最小旧库。"""
    return _build_legacy(
        str(tmp_path / "legacy.db"),
        {
            "subfinder": [
                (1, "example.test", "a.example.test", "2026-01-01T00:00:00Z"),
                (2, "example.test", "b.example.test", "2026-02-01T00:00:00Z"),
                (3, "example.test", "bad space.com", "2026-02-02T00:00:00Z"),  # 非法值
            ],
            "httpx": [
                (4, "example.test", "https://Example.test:443/", "2026-03-01T00:00:00Z"),
            ],
            "naabu": [
                (5, "example.test", "10.0.0.1:80", "2026-03-02T00:00:00Z"),
            ],
            "tool_results": [
                (6, "example.test", "weirdtool", "mystery", "whatever", "2026-03-03T00:00:00Z"),  # 未知 category
            ],
        },
    )


@pytest.fixture
def target_db(tmp_path, monkeypatch):
    """把新库指向临时文件（迁移脚本会显式传 path，这里只是保险）。"""
    import config
    import core.db as core_db

    path = str(tmp_path / "target.db")
    monkeypatch.setitem(config.LOCAL_DB_CONFIG, "path", path)
    core_db.reset_schema_cache()
    core_db.ensure_schema(path)
    return path


def _digest(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


# ── dry-run 计划 ─────────────────────────────────────────


def test_plan_is_read_only_and_reports_counts(legacy_db, target_db):
    before = _digest(legacy_db)

    plan = migrate.plan_migration(legacy_db, table_map=TABLE_MAP)

    assert plan["counts"]["total"] == 6
    # 可迁移 4 条：subfinder 的 a / b、httpx 的 URL、naabu 的 ip:port。
    # 跳过的 2 条是 `bad space.com`（值非法）与 mystery（category 未知）。
    assert plan["counts"]["planned"] == 4
    assert plan["counts"]["skipped"] == 2
    assert _digest(legacy_db) == before, "dry-run 竟然改了旧库"
    # 旧库一行都不该进新库。
    assert assets_store.count_assets() == 0


def test_plan_skips_dirty_rows_with_reasons(legacy_db):
    plan = migrate.plan_migration(legacy_db, table_map=TABLE_MAP)

    reasons = " ".join(item["reason"] for item in plan["skipped"])
    assert len(plan["skipped"]) == 2
    assert "bad space.com" in {item["value"] for item in plan["skipped"]}
    assert "未知 category" in reasons or "mystery" in reasons


def test_category_is_translated_to_asset_type(legacy_db):
    """``web`` 必须落成 ``url``，不能当成非法类型丢掉。"""
    plan = migrate.plan_migration(legacy_db, table_map=TABLE_MAP)
    types = {item["asset_type"] for item in plan["items"]}
    assert "web" not in types
    assert types == {"subdomain", "url", "port"}


def test_legacy_timestamp_is_normalized(legacy_db):
    """旧库的 ``...Z`` 必须转成新库的 ``+00:00``，否则时间列排序会错。"""
    plan = migrate.plan_migration(legacy_db, table_map=TABLE_MAP)
    stamps = {item["observed_at"] for item in plan["items"]}
    assert all(stamp.endswith("+00:00") for stamp in stamps)
    assert not any(stamp.endswith("Z") for stamp in stamps)


def test_observation_id_is_deterministic(legacy_db):
    first = migrate.plan_migration(legacy_db, table_map=TABLE_MAP)["items"]
    second = migrate.plan_migration(legacy_db, table_map=TABLE_MAP)["items"]
    assert [item["observation_id"] for item in first] == [item["observation_id"] for item in second]
    assert all(item["observation_id"].startswith(migrate.LEGACY_OBSERVATION_PREFIX) for item in first)


def test_plan_uses_real_tool_map_by_default(legacy_db):
    """不传 table_map 时应回落到 ``storage.TOOL_DATABASES``。"""
    plan = migrate.plan_migration(legacy_db)
    assert plan["counts"]["total"] == 6
    # subfinder / httpx / naabu 都在真实映射里，泛型表也会被读到。
    assert plan["counts"]["planned"] >= 3


def test_missing_legacy_db_raises_migration_error(tmp_path):
    with pytest.raises(migrate.MigrationError):
        migrate.plan_migration(str(tmp_path / "nope.db"))


# ── 真正写入 ─────────────────────────────────────────────


def test_apply_writes_assets_and_keeps_legacy_untouched(legacy_db, target_db):
    before = _digest(legacy_db)
    plan = migrate.plan_migration(legacy_db, table_map=TABLE_MAP)

    result = migrate.apply_migration(plan, path=target_db)

    assert result["written"] == plan["counts"]["planned"]
    assert result["failed"] == 0
    assert _digest(legacy_db) == before, "迁移改了旧库"

    # 4 条被写入（1 条脏 subdomain + 1 条未知 category 被跳过）。
    assert assets_store.count_assets() == plan["counts"]["planned"]

    # canonical 归一化生效：``https://Example.test:443/`` 落成 ``https://example.test/``。
    url_asset = assets_store.get_asset_by_key("url|https://example.test/")
    assert url_asset is not None
    assert url_asset["value"] == "https://example.test/"

    observation = assets_store.list_observations(asset_id=url_asset["id"])[0]
    assert observation["source_tool"] == "httpx"
    # 溯源信息保留在 data 里，方便回溯「这条来自旧库哪张表的哪次 run」。
    assert observation["data"]["legacy_table"] == "httpx_results"
    assert observation["data"]["legacy_run_id"] == 4
    # 旧库年代没有 Scope 概念：不能事后编一个。
    assert url_asset["scope_id"] is None


def test_apply_is_idempotent(legacy_db, target_db):
    """重跑不产生重复时间线 —— 否则资产页会被迁出来的副本淹没。"""
    plan = migrate.plan_migration(legacy_db, table_map=TABLE_MAP)

    first = migrate.apply_migration(plan, path=target_db)
    counts_after_first = assets_store.count_assets()
    observations_after_first = len(assets_store.list_observations(limit=100))

    second = migrate.apply_migration(plan, path=target_db)

    assert second["written"] == 0
    assert second["already"] == first["written"]
    assert assets_store.count_assets() == counts_after_first
    assert len(assets_store.list_observations(limit=100)) == observations_after_first


def test_apply_survives_single_bad_item(legacy_db, target_db, monkeypatch):
    """单条写失败不能让整次迁移中断。"""
    plan = migrate.plan_migration(legacy_db, table_map=TABLE_MAP)
    original = assets_store.record_observation
    calls = {"n": 0}

    def _flaky(asset_type, value, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("模拟第二条写入失败")
        return original(asset_type, value, **kwargs)

    monkeypatch.setattr(assets_store, "record_observation", _flaky)
    result = migrate.apply_migration(plan, path=target_db)

    assert result["failed"] == 1
    assert result["written"] == plan["counts"]["planned"] - 1
    assert any("模拟第二条写入失败" in reason for reason in result["reasons"])


def test_first_seen_uses_the_earliest_observation_across_tables(legacy_db, target_db):
    """同一资产被两张表先后发现时，``first_seen`` 必须取**全局最早**那次。

    这是排序修复的验收：``subfinder``（2026-01）与 ``httpx``（2026-03）都
    ``plan_migration`` 会按 ``observed_at`` 全局升序写入，所以资产行是在
    subfinder 那条上创建出来的，``first_seen`` 才会是 2026-01。
    """
    plan = migrate.plan_migration(legacy_db, table_map=TABLE_MAP)
    stamps = [item["observed_at"] for item in plan["items"]]
    assert stamps == sorted(stamps), "计划没有按时间升序"

    migrate.apply_migration(plan, path=target_db)
    # a.example.test 只有 subfinder 一条，用它验证时间戳格式与来源。
    asset = assets_store.get_asset_by_key("subdomain|a.example.test")
    assert asset["first_seen"] == "2026-01-01T00:00:00+00:00"
    assert asset["last_seen"] == "2026-01-01T00:00:00+00:00"
    assert asset["status"] == assets_store.STATUS_ACTIVE


def test_same_asset_from_two_tables_keeps_one_row_and_two_observations(tmp_path, target_db):
    """跨表重复发现 → 一行资产 + 两条观测，且 first_seen 取更早那条。"""
    legacy = _build_legacy(
        str(tmp_path / "dupe.db"),
        {
            "subfinder": [(1, "example.test", "shared.example.test", "2026-05-01T00:00:00Z")],
            "amass": [(2, "example.test", "shared.example.test", "2026-01-01T00:00:00Z")],
        },
    )
    plan = migrate.plan_migration(legacy, table_map=TABLE_MAP)
    result = migrate.apply_migration(plan, path=target_db)
    assert result["written"] == 2

    asset = assets_store.get_asset_by_key("subdomain|shared.example.test")
    assert asset is not None
    # amass 那条（1 月）虽然按 table_map 顺序排在后面，但时间更早。
    assert asset["first_seen"] == "2026-01-01T00:00:00+00:00"
    assert asset["last_seen"] == "2026-05-01T00:00:00+00:00"

    timeline = assets_store.list_observations(asset_id=asset["id"])
    assert {item["source_tool"] for item in timeline} == {"subfinder", "amass"}
    assert assets_store.count_assets(asset_type="subdomain") == 1


def test_migration_does_not_touch_existing_new_tables(legacy_db, target_db):
    """DECISIONS-E/F：只往 assets/observations 里追加，既有表一个列都不动。"""
    from core import db

    before = {row["name"] for row in db.query("PRAGMA table_info(jobs)", path=target_db)}

    plan = migrate.plan_migration(legacy_db, table_map=TABLE_MAP)
    migrate.apply_migration(plan, path=target_db)

    after = {row["name"] for row in db.query("PRAGMA table_info(jobs)", path=target_db)}
    assert before == after


def test_generic_table_rows_are_migrated_with_their_own_tool_name(legacy_db, target_db):
    """泛型表的行按行内 ``tool_name`` 溯源，而不是按表名。"""
    import sqlite3

    conn = sqlite3.connect(legacy_db)
    conn.execute(
        "INSERT INTO tool_results (run_id, domain, tool_name, category, value, created_at) "
        "VALUES (7, 'example.test', 'gospider', 'url', 'https://example.test/a', '2026-04-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    plan = migrate.plan_migration(legacy_db, table_map=TABLE_MAP)
    migrate.apply_migration(plan, path=target_db)

    asset = assets_store.get_asset_by_key("url|https://example.test/a")
    observation = assets_store.list_observations(asset_id=asset["id"])[0]
    assert observation["source_tool"] == "gospider"
    assert observation["data"]["legacy_table"] == "tool_results"


def test_missing_tool_tables_are_skipped(legacy_db, tmp_path):
    """旧库是渐进长出来的：表不存在时跳过，而不是报错。"""
    partial = _build_legacy(str(tmp_path / "partial.db"), {"subfinder": []})
    conn = sqlite3.connect(partial)
    conn.execute("DROP TABLE httpx_results")
    conn.commit()
    conn.close()

    plan = migrate.plan_migration(partial, table_map=TABLE_MAP)
    assert plan["counts"]["total"] == 0


# ── CLI 入口 ─────────────────────────────────────────────


def _load_cli():
    """把 ``scripts/migrate_legacy_results.py`` 当模块加载（scripts 不是包）。"""
    import importlib.util

    from pathlib import Path as _Path

    path = _Path(__file__).resolve().parents[2] / "scripts" / "migrate_legacy_results.py"
    spec = importlib.util.spec_from_file_location("gef_migrate_cli", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_cli_dry_run_writes_nothing(legacy_db, target_db, monkeypatch, capsys):
    cli = _load_cli()
    monkeypatch.setattr(cli, "_default_paths", lambda: (legacy_db, target_db))

    assert cli.main(["--legacy", legacy_db, "--target", target_db]) == 0
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert assets_store.count_assets() == 0


def test_cli_apply_writes_and_is_rerunnable(legacy_db, target_db, capsys):
    cli = _load_cli()

    assert cli.main(["--legacy", legacy_db, "--target", target_db, "--apply"]) == 0
    assert assets_store.count_assets() > 0
    written_first = assets_store.count_assets()

    assert cli.main(["--legacy", legacy_db, "--target", target_db, "--apply"]) == 0
    assert assets_store.count_assets() == written_first


def test_cli_refuses_when_source_equals_target(legacy_db, capsys):
    cli = _load_cli()
    assert cli.main(["--legacy", legacy_db, "--target", legacy_db, "--apply"]) == 2
    assert "拒绝执行" in capsys.readouterr().err


def test_cli_reports_missing_legacy_db(tmp_path, target_db, capsys):
    cli = _load_cli()
    missing = str(tmp_path / "missing.db")
    assert cli.main(["--legacy", missing, "--target", target_db]) == 2
    assert "旧库不存在" in capsys.readouterr().err


def test_cli_limit_caps_items(legacy_db, target_db):
    cli = _load_cli()
    assert cli.main(["--legacy", legacy_db, "--target", target_db, "--apply", "--limit", "1"]) == 0
    assert len(assets_store.list_observations(limit=100)) == 1
