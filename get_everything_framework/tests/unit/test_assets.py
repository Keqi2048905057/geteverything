"""P1 单元测试：资产 / 观测数据层与 Diff Engine（方案第 8、10 节）。

覆盖两层模型的关键承诺：

* 同一资产跨工具**只落一行** `assets`，但每次观测都在 `observations` 留痕；
* `first_seen` 永不被覆盖，`last_seen` 每次观测都推进；
* 能回答「谁发现的 / 何时发现的 / 当时的属性」；
* Diff 的验收例子「A B C」对「A C D」必须给出 ``added=D / removed=B``；
* 属性变化（status_code / title / server / technology）必须能识别；
* **不删除任何数据**（资产消失只改状态）。
"""

import pytest

from core import assets, db


@pytest.fixture
def scope_id(local_db):
    from core import scope_store

    return scope_store.create(name="资产测试范围", allowed_domains=["example.test"]).id


def _observe(value, **kwargs):
    return assets.record_observation(kwargs.pop("type", "subdomain"), value, **kwargs)


# ── 两层模型：唯一资产 + 观测时间线 ──────────────────────


def test_same_asset_across_tools_is_one_row(scope_id):
    """方案第 8 节第一条：同一资产跨工具不能无限复制。"""
    first = _observe("a.example.test", scope_id=scope_id, source_tool="subfinder", job_id="job_1")
    second = _observe("A.EXAMPLE.TEST", scope_id=scope_id, source_tool="httpx", job_id="job_2")

    assert first["asset_created"] is True
    assert second["asset_created"] is False
    assert first["asset_id"] == second["asset_id"]
    assert assets.count_assets(scope_id=scope_id) == 1

    # 但观测有两条 —— 「保留观测时间线」。
    timeline = assets.list_observations(asset_id=first["asset_id"])
    assert {item["source_tool"] for item in timeline} == {"subfinder", "httpx"}


def test_first_seen_never_overwritten_and_last_seen_advances(scope_id):
    first = _observe("b.example.test", scope_id=scope_id, observed_at="2026-01-01T00:00:00+00:00")
    second = _observe("b.example.test", scope_id=scope_id, observed_at="2026-02-01T00:00:00+00:00")

    asset = assets.get_asset(first["asset_id"])
    assert asset["first_seen"] == "2026-01-01T00:00:00+00:00"
    assert asset["last_seen"] == "2026-02-01T00:00:00+00:00"
    assert second["value"] == "b.example.test"


def test_observation_keeps_who_when_and_what(scope_id):
    """方案第 8 节：能知道是谁发现的 / 何时发现 / 当时的属性。"""
    result = _observe(
        "c.example.test",
        type="host",
        scope_id=scope_id,
        job_id="job_42",
        run_id="run_1",
        source_tool="subfinder",
        parser_version="1.0",
        raw_artifact_id="art_1",
        observed_at="2026-03-03T00:00:00+00:00",
        data={"status_code": 200, "title": "C"},
    )
    observation = assets.list_observations(asset_id=result["asset_id"])[0]

    assert observation["source_tool"] == "subfinder"
    assert observation["job_id"] == "job_42"
    assert observation["run_id"] == "run_1"
    assert observation["observed_at"] == "2026-03-03T00:00:00+00:00"
    assert observation["parser_version"] == "1.0"
    assert observation["raw_artifact_id"] == "art_1"
    assert observation["data"] == {"status_code": 200, "title": "C"}


def test_different_scope_means_different_asset(scope_id):
    """收紧 Scope 不该影响另一个 Scope 的资产列表，因此 scope 参与唯一性。"""
    other = scope_id + "_other"
    first = _observe("d.example.test", scope_id=scope_id)
    second = _observe("d.example.test", scope_id=other)

    assert first["asset_id"] != second["asset_id"]
    assert assets.count_assets(scope_id=scope_id) == 1
    assert assets.count_assets(scope_id=other) == 1


def test_metadata_only_fills_missing_keys(scope_id):
    """人工订正过的属性不该被后续重跑冲掉。"""
    _observe("e.example.test", scope_id=scope_id, metadata={"owner": "team-a", "env": "prod"})
    _observe("e.example.test", scope_id=scope_id, metadata={"owner": "team-b"})

    asset = assets.get_asset_by_key("subdomain|e.example.test", scope_id=scope_id)
    assert asset["metadata"]["owner"] == "team-a"
    assert asset["metadata"]["env"] == "prod"


# ── 规范化与拒绝 ─────────────────────────────────────────


def test_invalid_value_is_rejected_with_clear_error(scope_id):
    with pytest.raises(assets.InvalidAsset):
        _observe("bad space.com", scope_id=scope_id)


def test_batch_records_valid_items_and_reports_skipped(scope_id):
    """单条脏数据不能让整批采集失败（工具输出里混脏数据是常态）。"""
    written, skipped = assets.record_observations(
        [
            {"type": "subdomain", "value": "f.example.test"},
            {"type": "subdomain", "value": "bad space.com"},
            {"type": "ip", "value": "10.0.0.1"},
        ],
        scope_id=scope_id,
        job_id="job_batch",
    )
    assert len(written) == 2
    assert len(skipped) == 1
    assert "bad space.com" == skipped[0]["value"]


def test_batch_item_without_type_or_value_is_skipped(scope_id):
    written, skipped = assets.record_observations([{"type": "ip"}], scope_id=scope_id)
    assert written == []
    assert skipped[0]["error"] == "缺少 type 或 value"


# ── 列表 / 筛选 / 统计 ───────────────────────────────────


def test_list_assets_filters_by_type_status_and_search(scope_id):
    _observe("g.example.test", scope_id=scope_id)
    _observe("10.1.1.1", type="ip", scope_id=scope_id)

    assert [item["value"] for item in assets.list_assets(scope_id=scope_id, asset_type="ip")] == ["10.1.1.1"]
    assert len(assets.list_assets(scope_id=scope_id, asset_type="subdomain")) == 1
    assert assets.list_assets(scope_id=scope_id, status=assets.STATUS_ACTIVE)
    assert assets.list_assets(scope_id=scope_id, status=assets.STATUS_GONE) == []

    found = assets.list_assets(scope_id=scope_id, search="g.example")
    assert [item["value"] for item in found] == ["g.example.test"]


def test_search_escapes_wildcards(scope_id):
    """``_`` 是 SQL 通配符：不转义会让 ``a_c`` 意外匹配 ``abc``。"""
    _observe("abc.example.test", scope_id=scope_id)
    _observe("a_c.example.test", scope_id=scope_id)

    found = assets.list_assets(scope_id=scope_id, search="a_c")
    assert [item["value"] for item in found] == ["a_c.example.test"]


def test_asset_summary_counts_by_type(scope_id):
    _observe("h.example.test", scope_id=scope_id)
    _observe("i.example.test", scope_id=scope_id)
    _observe("10.2.2.2", type="ip", scope_id=scope_id)

    summary = assets.asset_summary(scope_id=scope_id)
    assert summary["by_type"] == {"subdomain": 2, "ip": 1}
    assert summary["total"] == 3


def test_get_asset_unknown_returns_none(local_db):
    assert assets.get_asset("asset_missing") is None


# ── 状态迁移不删数据 ─────────────────────────────────────


def test_mark_stale_keeps_rows_and_timeline(scope_id):
    old = _observe("j.example.test", scope_id=scope_id, observed_at="2026-01-01T00:00:00+00:00")
    fresh = _observe("k.example.test", scope_id=scope_id, observed_at="2026-06-01T00:00:00+00:00")

    marked = assets.mark_stale_assets(scope_id, last_seen_before="2026-03-01T00:00:00+00:00")
    assert marked == [old["asset_id"]]

    assert assets.get_asset(old["asset_id"])["status"] == assets.STATUS_STALE
    assert assets.get_asset(fresh["asset_id"])["status"] == assets.STATUS_ACTIVE
    # 行还在，观测时间线也还在 —— 方案禁止删历史数据。
    assert assets.count_assets(scope_id=scope_id) == 2
    assert assets.list_observations(asset_id=old["asset_id"])


# ── Diff（方案第 10 节验收） ─────────────────────────────


def _scan(job_id, scope_id, values):
    for value in values:
        _observe(value, scope_id=scope_id, job_id=job_id, source_tool="subfinder")


def test_diff_plan_acceptance_added_removed_unchanged(scope_id):
    """方案第 10 节验收：第一次 A B C，第二次 A C D → added=D / removed=B。"""
    _scan("job_before", scope_id, ["a.example.test", "b.example.test", "c.example.test"])
    _scan("job_after", scope_id, ["a.example.test", "c.example.test", "d.example.test"])

    result = assets.diff_jobs("job_before", "job_after")
    assert [item["value"] for item in result["added"]] == ["d.example.test"]
    assert [item["value"] for item in result["removed"]] == ["b.example.test"]
    assert sorted(item["value"] for item in result["unchanged"]) == ["a.example.test", "c.example.test"]
    assert result["changed"] == []
    assert result["counts"] == {"added": 1, "removed": 1, "changed": 0, "unchanged": 2}


def test_diff_detects_attribute_changes(scope_id):
    """方案第 10 节：changed 至少要能指出 status_code / title / server / technology。"""
    _observe(
        "l.example.test",
        type="url",
        scope_id=scope_id,
        job_id="job_b1",
        data={"status_code": 200, "title": "Login", "server": "nginx", "technology": ["php"]},
    )
    _observe(
        "l.example.test",
        type="url",
        scope_id=scope_id,
        job_id="job_a1",
        data={"status_code": 403, "title": "Forbidden", "server": "nginx", "technology": ["php", "laravel"]},
    )

    result = assets.diff_jobs("job_b1", "job_a1")
    assert result["counts"]["changed"] == 1
    changes = result["changed"][0]["changes"]
    assert changes["status_code"] == {"from": 200, "to": 403}
    assert changes["title"] == {"from": "Login", "to": "Forbidden"}
    assert changes["technology"] == {"from": ["php"], "to": ["php", "laravel"]}
    # server 没变，不该出现在 changes 里。
    assert "server" not in changes


def test_diff_ignores_volatile_attributes(scope_id):
    """耗时、时间戳这类一次性字段不能算变化，否则每次扫描都是 changed。"""
    _observe(
        "m.example.test",
        scope_id=scope_id,
        job_id="job_b2",
        data={"status_code": 200, "duration_ms": 12, "checked_at": "2026-01-01"},
    )
    _observe(
        "m.example.test",
        scope_id=scope_id,
        job_id="job_a2",
        data={"status_code": 200, "duration_ms": 99, "checked_at": "2026-02-02"},
    )

    result = assets.diff_jobs("job_b2", "job_a2")
    assert result["counts"] == {"added": 0, "removed": 0, "changed": 0, "unchanged": 1}


def test_diff_can_omit_unchanged_details(scope_id):
    """关掉明细只影响**下发**，不影响**计数**。

    「未变 1 条」与「未变 0 条」必须能区分：前者说明这次真的扫到了、
    只是没变化；后者可能是这次什么都没扫到。把计数一起抹成 0，
    恰好毁掉 diff 最有用的一条信息。
    """
    _scan("job_b3", scope_id, ["n.example.test"])
    _scan("job_a3", scope_id, ["n.example.test"])

    result = assets.diff_jobs("job_b3", "job_a3", include_unchanged=False)
    assert result["unchanged"] == [], "关掉明细后不应下发 unchanged 列表"
    assert result["counts"]["unchanged"] == 1, "计数仍应反映真实未变数量"

    # 带明细时两者一致。
    full = assets.diff_jobs("job_b3", "job_a3", include_unchanged=True)
    assert len(full["unchanged"]) == full["counts"]["unchanged"] == 1


def test_diff_treats_canonical_equivalents_as_unchanged(scope_id):
    """不同写法必须归一到同一资产，否则 diff 满屏假新增。"""
    _observe("https://Example.com/", type="url", scope_id=scope_id, job_id="job_b4")
    _observe("HTTPS://example.COM:443", type="url", scope_id=scope_id, job_id="job_a4")

    result = assets.diff_jobs("job_b4", "job_a4")
    assert result["counts"] == {"added": 0, "removed": 0, "changed": 0, "unchanged": 1}


def test_diff_with_unknown_jobs_is_empty_not_error(local_db):
    result = assets.diff_jobs("job_none_1", "job_none_2")
    assert result["counts"] == {"added": 0, "removed": 0, "changed": 0, "unchanged": 0}


def test_diff_respects_scope_filter(scope_id):
    """限定 Scope 后，范围外的资产**不得**被算进 added。

    第一个任务在 ``scope_id`` 里看到 ``o``，第二个任务在**另一个** Scope 里
    看到 ``p``。限定 ``scope_id`` 比较时：``p`` 根本不在这个范围里，
    不能报成 added；``o`` 在这个范围里且第二次没再出现 → 报 removed。
    """
    other = scope_id + "_other"
    _scan("job_b5", scope_id, ["o.example.test"])
    _scan("job_a5", other, ["p.example.test"])

    result = assets.diff_jobs("job_b5", "job_a5", scope_id=scope_id)
    assert [item["value"] for item in result["added"]] == []
    assert [item["value"] for item in result["removed"]] == ["o.example.test"]
    assert result["counts"] == {"added": 0, "removed": 1, "changed": 0, "unchanged": 0}


def test_diff_without_scope_filter_sees_both_assets(scope_id):
    """同一对任务不加范围过滤时，两条资产都应参与比较。"""
    other = scope_id + "_other"
    _scan("job_b5", scope_id, ["o.example.test"])
    _scan("job_a5", other, ["p.example.test"])

    result = assets.diff_jobs("job_b5", "job_a5")
    assert [item["value"] for item in result["added"]] == ["p.example.test"]
    assert [item["value"] for item in result["removed"]] == ["o.example.test"]


def test_diff_uses_latest_observation_per_asset(scope_id):
    """同一任务里同一资产被观测多次：以最后一条属性为准。"""
    _observe("q.example.test", type="url", scope_id=scope_id, job_id="job_b6", data={"status_code": 200})
    _observe("q.example.test", type="url", scope_id=scope_id, job_id="job_a6", data={"status_code": 200})
    _observe("q.example.test", type="url", scope_id=scope_id, job_id="job_a6", data={"status_code": 500})

    result = assets.diff_jobs("job_b6", "job_a6")
    assert result["changed"][0]["changes"]["status_code"] == {"from": 200, "to": 500}


# ── DECISIONS-E 的迁移口径 ───────────────────────────────


def test_new_tables_do_not_touch_legacy_schema(tmp_path):
    """只新增表：既有表（scopes / jobs / job_steps …）一个都不少、也没多改。"""
    path = str(tmp_path / "schema.db")
    db.init_schema(path)

    tables = {row["name"] for row in db.query("SELECT name FROM sqlite_master WHERE type='table'", path=path)}
    for expected in ("scopes", "audit_events", "uploads", "jobs", "job_steps", "job_events", "artifacts", "exports"):
        assert expected in tables
    assert {"assets", "observations"} <= tables

    # jobs 表只被「纯增量补列」改过（P0-7a / P0-7b，用户逐项预授权）：
    # 既有列一个都不能少，新增列必须可空，且不得改写任何既有列及其数据。
    job_columns = {row["name"] for row in db.query("PRAGMA table_info(jobs)", path=path)}
    assert "attempt" in job_columns
    assert {"idempotency_key", "next_attempt_at"} <= job_columns

    notnull = {
        row["name"]: row["notnull"] for row in db.query("PRAGMA table_info(jobs)", path=path)
    }
    assert notnull["idempotency_key"] == 0
    assert notnull["next_attempt_at"] == 0


def test_assets_table_matches_plan_columns(tmp_path):
    """方案第 8 节点名的列必须在（防止实现与方案脱节）。"""
    path = str(tmp_path / "schema2.db")
    db.init_schema(path)

    asset_columns = {row["name"] for row in db.query("PRAGMA table_info(assets)", path=path)}
    assert {
        "id",
        "scope_id",
        "canonical_key",
        "type",
        "value",
        "first_seen",
        "last_seen",
        "status",
        "confidence",
        "metadata_json",
    } <= asset_columns

    observation_columns = {row["name"] for row in db.query("PRAGMA table_info(observations)", path=path)}
    assert {
        "id",
        "asset_id",
        "job_id",
        "run_id",
        "source_tool",
        "observed_at",
        "parser_version",
        "raw_artifact_id",
        "data_json",
    } <= observation_columns


def test_canonical_key_is_unique(scope_id, local_db):
    """去重的落点是数据库唯一约束，不只是代码里的 SELECT。"""
    import sqlite3

    _observe("r.example.test", scope_id=scope_id)
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO assets (id, scope_id, canonical_key, type, value, first_seen, last_seen) "
                "VALUES ('asset_dup', ?, 'subdomain|r.example.test', 'subdomain', 'r.example.test', '', '')",
                (scope_id,),
            )


# ── 从 job 步骤落观测（方案第 8 节的落地路径） ───────────


def _step(tool="httpx", target="s.example.test", job_id="job_x", step_id="step_x"):
    return {"id": step_id, "job_id": job_id, "tool_name": tool, "target": target}


def test_category_to_type_maps_legacy_categories():
    """旧 category（web/alive/dns/url/port…）必须能翻译成资产类型。"""
    assert assets.asset_type_for_category("web") == "url"
    assert assets.asset_type_for_category("alive") == "host"
    assert assets.asset_type_for_category("dns") == "host"
    assert assets.asset_type_for_category("url") == "url"
    assert assets.asset_type_for_category("subdomain") == "subdomain"
    assert assets.asset_type_for_category("port") == "port"
    assert assets.asset_type_for_category("  WEB  ") == "url"


def test_category_to_type_rejects_unknown_instead_of_guessing():
    """猜错类型会制造假重复，宁可跳过。"""
    assert assets.asset_type_for_category("mystery") is None
    assert assets.asset_type_for_category("") is None
    assert assets.asset_type_for_category(None) is None


def test_ingest_step_observations_writes_assets(scope_id):
    outcome = {
        "observations": [
            {"category": "web", "value": "https://s.example.test/", "data": {"status_code": 200, "title": "S"}},
            {"category": "subdomain", "value": "api.s.example.test", "data": {}},
        ],
        "results": ["https://s.example.test/", "api.s.example.test"],
        "artifact_id": "art_9",
    }
    report = assets.ingest_step_observations(
        _step(job_id="job_ing"), outcome, scope_id=scope_id
    )

    assert report["written"] == 2
    assert report["skipped"] == 0

    url_asset = assets.get_asset_by_key("url|https://s.example.test/", scope_id=scope_id)
    assert url_asset["type"] == "url"
    observation = assets.list_observations(asset_id=url_asset["id"])[0]
    assert observation["source_tool"] == "httpx"
    assert observation["job_id"] == "job_ing"
    assert observation["step_id"] == "step_x"
    assert observation["data"]["status_code"] == 200
    assert observation["raw_artifact_id"] == "art_9"


def test_ingest_step_observations_falls_back_to_string_results(scope_id):
    """没有结构化观测时，只对「明确知道字符串是什么」的工具兜底。"""
    outcome = {"observations": [], "results": ["a.s.example.test", "b.s.example.test"]}
    report = assets.ingest_step_observations(
        _step(tool="subfinder"), outcome, scope_id=scope_id
    )
    assert report["written"] == 2
    assert assets.count_assets(scope_id=scope_id, asset_type="subdomain") == 2


def test_ingest_step_observations_skips_unknown_string_tools(scope_id):
    """httpx 的字符串形态不确定（可能是 URL 也可能是 host），不能瞎猜成 subdomain。"""
    outcome = {"observations": [], "results": ["https://s.example.test/"]}
    report = assets.ingest_step_observations(_step(tool="httpx"), outcome, scope_id=scope_id)

    assert report["written"] == 0
    assert report["skipped"] == 1
    assert any("无法确定资产类型" in reason for reason in report["reasons"])
    assert assets.count_assets(scope_id=scope_id) == 0


def test_ingest_step_observations_counts_unknown_category_as_skipped(scope_id):
    outcome = {"observations": [{"category": "mystery", "value": "x", "data": {}}]}
    report = assets.ingest_step_observations(_step(), outcome, scope_id=scope_id)

    assert report["written"] == 0
    assert report["skipped"] == 1
    assert any("未知 category" in reason for reason in report["reasons"])


def test_ingest_step_observations_never_raises(scope_id, monkeypatch):
    """派生产物失败不能让一个已经跑完的任务变成失败。"""

    def _boom(*args, **kwargs):
        raise RuntimeError("模拟落观测时数据库炸了")

    monkeypatch.setattr(assets, "record_observations", _boom)
    report = assets.ingest_step_observations(
        _step(), {"observations": [{"category": "web", "value": "https://x.test/"}], "results": []},
        scope_id=scope_id,
    )
    assert report["written"] == 0
    assert any("模拟落观测" in reason for reason in report["reasons"])


def test_ingest_ignores_malformed_observation_items(scope_id):
    outcome = {
        "observations": [
            "not-a-dict",
            {"category": "web"},
            {"value": "https://ok.test/", "category": "web"},
        ],
        "results": [],
    }
    report = assets.ingest_step_observations(_step(), outcome, scope_id=scope_id)
    assert report["written"] == 1
    assert report["skipped"] == 2


def test_ingest_does_not_write_path_like_values(scope_id):
    """回归：``E:\\...`` 这类本地路径不该被当成主机名收进资产。"""
    outcome = {
        "observations": [
            {"category": "url", "value": "C:\\Users\\x\\out.txt"},
            {"category": "web", "value": "https://good.test/"},
        ],
        "results": [],
    }
    report = assets.ingest_step_observations(_step(), outcome, scope_id=scope_id)
    assert report["written"] == 1
    assert assets.list_assets(scope_id=scope_id)[0]["value"] == "https://good.test/"
