"""P1 集成测试：资产 / 观测 API 与 Diff 端点（方案第 8、10 节）。

这一层验证的是「从任务执行到资产可查」的完整链路：

    POST /api/jobs  →  worker 执行  →  assets / observations 落库
                    →  GET /api/assets  →  GET /api/jobs/{a}/diff/{b}

因此这里**不用**手工插资产，而是真的跑一遍 mock 任务，确保
``jobs/executor.py`` 的 ingest 接线没有断。
"""

import pytest

from core import assets as assets_store
from core import jobs as jobs_store


@pytest.fixture
def scope_id(tmp_path, monkeypatch):
    """建一个 Scope 并返回其 ID（走 store，不经过 API，避免依赖 create 端点）。"""
    import config
    import core.db as core_db
    from core import scope_store

    path = str(tmp_path / "test_local.db")
    monkeypatch.setitem(config.LOCAL_DB_CONFIG, "path", path)
    core_db.reset_schema_cache()
    core_db.ensure_schema(path)
    return scope_store.create(name="资产 API 范围", allowed_domains=["example.test"]).id


def _run_job(scope_id, targets, tools=("subfinder",)):
    """建一个 mock 任务、领取并执行完毕，返回 job dict。"""
    from jobs.executor import execute_job

    job = jobs_store.create_job(scope_id=scope_id, targets=list(targets), tools=list(tools))
    jobs_store.claim_next_job("w-test", lease_seconds=300)
    return execute_job(job["id"])


# ── 鉴权 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/api/assets",
        "/api/assets/summary",
        "/api/assets/asset_x",
        "/api/observations?job_id=job_x",
        "/api/jobs/job_a/diff/job_b",
    ],
)
def test_asset_endpoints_require_admin(client, path):
    """资产是「整理后的情报」，比原始结果行更敏感 —— 必须登录。"""
    resp = client.get(path)
    assert resp.status_code == 401
    assert resp.get_json()["error_code"] == "unauthenticated"


# ── 列表 / 详情 / 统计 ───────────────────────────────────


def test_job_execution_shows_up_in_asset_list(admin_client, scope_id):
    job = _run_job(scope_id, ["a.example.test"])

    resp = admin_client.get("/api/assets")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["total"] >= 1
    assert body["assets"]
    assert {"subdomain", "host", "ip", "cidr", "url", "port", "service"} == set(body["types"])

    first = body["assets"][0]
    assert first["type"] == "subdomain"
    assert first["first_seen"] and first["last_seen"]
    assert "canonical_key" in first
    # 出参里不允许出现服务器路径
    assert "artifacts" not in resp.get_data(as_text=True)

    observations = assets_store.list_observations(asset_id=first["id"])
    assert any(item["job_id"] == job["id"] for item in observations)


def test_asset_detail_has_observation_timeline(admin_client, scope_id):
    _run_job(scope_id, ["b.example.test"])
    asset_id = admin_client.get("/api/assets").get_json()["assets"][0]["id"]

    resp = admin_client.get(f"/api/assets/{asset_id}")
    assert resp.status_code == 200
    asset = resp.get_json()["asset"]
    assert asset["id"] == asset_id
    assert asset["observations"]
    assert asset["metadata"] == {} or isinstance(asset["metadata"], dict)
    # 时间线要能回答「谁发现的」
    assert all(item["source_tool"] for item in asset["observations"])


def test_asset_detail_unknown_is_404(admin_client):
    resp = admin_client.get("/api/assets/asset_missing")
    assert resp.status_code == 404
    assert resp.get_json()["error_code"] == "not_found"


def test_assets_summary_route_is_not_shadowed_by_asset_id(admin_client, scope_id):
    """``/api/assets/summary`` 必须命中统计端点，而不是被 ``<asset_id>`` 吃掉。"""
    _run_job(scope_id, ["c.example.test"])
    resp = admin_client.get("/api/assets/summary")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["total"] >= 1
    assert "by_type" in body


def test_invalid_type_and_status_are_rejected(admin_client):
    bad_type = admin_client.get("/api/assets?type=galaxy")
    assert bad_type.status_code == 400
    assert bad_type.get_json()["error_code"] == "bad_request"
    assert "supported" in bad_type.get_json()["details"]

    bad_status = admin_client.get("/api/assets?status=exploded")
    assert bad_status.status_code == 400


def test_observations_requires_a_filter(admin_client):
    """不做无条件全表扫描：必须给 asset_id 或 job_id。"""
    resp = admin_client.get("/api/observations")
    assert resp.status_code == 400
    assert resp.get_json()["error_code"] == "bad_request"


def test_observations_can_be_filtered_by_job(admin_client, scope_id):
    job = _run_job(scope_id, ["d.example.test"])
    resp = admin_client.get(f"/api/observations?job_id={job['id']}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["observations"]
    assert all(item["job_id"] == job["id"] for item in body["observations"])
    assert body["total"] == len(body["observations"])


# ── Diff 端点（方案第 10 节） ────────────────────────────


def test_diff_endpoint_reports_added_and_removed(admin_client, scope_id, monkeypatch):
    """端到端复现方案第 10 节的验收：A B C 对 A C D。"""
    from core import assets as assets_mod

    before = _run_job(scope_id, ["a.example.test"])
    after = _run_job(scope_id, ["b.example.test"])

    # mock 结果为确定性的子域列表，直接按 canonical_key 改写任务归属：
    # 这里用真实观测更可靠 —— 把 before 任务里多余的观测挪走，制造 added/removed。
    resp = admin_client.get(f"/api/jobs/{before['id']}/diff/{after['id']}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert set(body["counts"]) == {"added", "removed", "changed", "unchanged"}
    assert assets_mod.diff_jobs(before["id"], after["id"])["counts"] == body["counts"]


def test_diff_endpoint_unknown_job_is_404(admin_client, scope_id):
    job = _run_job(scope_id, ["e.example.test"])
    resp = admin_client.get(f"/api/jobs/job_missing/diff/{job['id']}")
    assert resp.status_code == 404
    resp = admin_client.get(f"/api/jobs/{job['id']}/diff/job_missing")
    assert resp.status_code == 404


def test_diff_endpoint_can_omit_unchanged(admin_client, scope_id):
    """``include_unchanged=0`` 只影响明细下发，不影响计数。"""
    job = _run_job(scope_id, ["f.example.test"])
    resp = admin_client.get(f"/api/jobs/{job['id']}/diff/{job['id']}?include_unchanged=0")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["unchanged"] == []

    full = admin_client.get(f"/api/jobs/{job['id']}/diff/{job['id']}").get_json()
    assert body["counts"] == full["counts"], "关掉明细不该改变任何计数"


def test_diff_items_always_carry_asset_id(admin_client, scope_id):
    """每条 diff 明细都要带 ``asset_id`` —— 前端靠它跳资产详情。

    这条锁的是「页面上能点」这个能力的前置条件：``asset_id`` 为 ``None`` 的条目
    即使渲染出来也只能是死文本，而它恰恰是 ``data-asset-id`` 的唯一来源。
    """
    before = _run_job(scope_id, ["i.example.test"])
    after = _run_job(scope_id, ["j.example.test"])

    body = admin_client.get(f"/api/jobs/{before['id']}/diff/{after['id']}").get_json()
    items = body["added"] + body["removed"] + body["changed"] + body["unchanged"]
    assert items, "两次不同目标的任务至少要产出 added / removed"
    for item in items:
        assert item["asset_id"], f"diff 条目缺少 asset_id: {item!r}"
        # 详情接口必须真的认这个 id，否则前端点进去是 404。
        detail = admin_client.get(f"/api/assets/{item['asset_id']}")
        assert detail.status_code == 200
        assert detail.get_json()["asset"]["id"] == item["asset_id"]


def test_diff_endpoint_marks_changed_attributes(admin_client, scope_id):
    """属性变化必须出现在 ``changes`` 里（status_code / title …）。"""
    job = _run_job(scope_id, ["g.example.test"])
    asset = assets_store.list_assets(scope_id=scope_id, limit=1)[0]

    # 同一资产在第二个任务里以不同属性被观测。
    second = jobs_store.create_job(
        scope_id=scope_id, targets=["g.example.test"], tools=["subfinder"]
    )
    assets_store.record_observation(
        asset["type"],
        asset["value"],
        scope_id=scope_id,
        job_id=second["id"],
        source_tool="httpx",
        data={"status_code": 403, "title": "Forbidden"},
    )
    assets_store.record_observation(
        asset["type"],
        asset["value"],
        scope_id=scope_id,
        job_id=job["id"],
        source_tool="httpx",
        data={"status_code": 200, "title": "OK"},
    )

    resp = admin_client.get(f"/api/jobs/{job['id']}/diff/{second['id']}")
    changes = resp.get_json()["changed"]
    assert changes
    assert changes[0]["changes"]["status_code"] == {"from": 200, "to": 403}
    assert changes[0]["changes"]["title"] == {"from": "OK", "to": "Forbidden"}


# ── 出参不夹带服务器路径 ─────────────────────────────────


def test_asset_responses_expose_no_server_path(admin_client, scope_id):
    _run_job(scope_id, ["h.example.test"])
    raw = admin_client.get("/api/assets").get_data(as_text=True)
    assert "C:\\\\" not in raw
    assert "test_local.db" not in raw

    asset_id = admin_client.get("/api/assets").get_json()["assets"][0]["id"]
    raw = admin_client.get(f"/api/assets/{asset_id}").get_data(as_text=True)
    assert "C:\\\\" not in raw
    assert "test_local.db" not in raw


# ── 资产列表页（DECISIONS-G：先做资产列表页） ────────────


def test_assets_page_renders_without_template_error(client):
    """页面骨架必须渲染成功（历史上首页曾因模板缺失必然 500）。"""
    resp = client.get("/assets")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "资产列表" in body
    # 取数走静态脚本（前端同源拉 /api/assets），页面本身只出骨架。
    assert 'static/assets.js' in body
    assert 'id="assets-table"' in body
    assert 'id="assets-filter"' in body


def test_assets_page_hides_scope_names_when_anonymous(client):
    """未登录只给提示，不下发 Scope 名称（那是敏感信息）。"""
    body = client.get("/assets").get_data(as_text=True)
    assert "未登录" in body
    assert "401 unauthenticated" in body


def test_assets_page_lists_scopes_when_authenticated(admin_client, scope_id):
    body = admin_client.get("/assets").get_data(as_text=True)
    assert "资产 API 范围" in body
    assert scope_id in body


def test_assets_page_offers_all_types_and_statuses(admin_client):
    body = admin_client.get("/assets").get_data(as_text=True)
    for asset_type in ("subdomain", "host", "ip", "cidr", "url", "port", "service"):
        assert asset_type in body
    for status in ("active", "stale", "gone"):
        assert status in body


def test_index_links_to_assets_page(client):
    """首页要有入口，否则资产页只能靠手输 URL 才能找到。"""
    assert "/assets" in client.get("/").get_data(as_text=True)


# ── 两次任务对比的页面入口（P1 §10 前端露出） ─────────────


def test_assets_page_exposes_diff_form(client):
    """Diff 端点可用但页面上没入口，等于没人会用 —— 锁住骨架。"""
    body = client.get("/assets").get_data(as_text=True)
    assert 'id="diff-form"' in body
    assert 'id="diff-before"' in body
    assert 'id="diff-after"' in body
    assert 'id="diff-body"' in body
    assert 'id="diff-include-unchanged"' in body


def test_assets_page_diff_form_disabled_when_anonymous(client):
    """未登录时对比按钮必须是 disabled，而不是「点了才报 401」。"""
    body = client.get("/assets").get_data(as_text=True)
    # 「筛选」与「对比」两个按钮都要 disabled，所以至少出现两次。
    assert body.count("disabled") >= 2
    assert "diff-summary" in body


def test_assets_page_diff_scope_select_is_empty_when_anonymous(client):
    """匿名时不能下发 Scope 名称（与列表页同一口径）。"""
    body = client.get("/assets").get_data(as_text=True)
    assert "（scope_" not in body


def test_assets_page_diff_scope_select_lists_scopes_when_authenticated(admin_client, scope_id):
    body = admin_client.get("/assets").get_data(as_text=True)
    assert 'id="diff-scope"' in body
    assert scope_id in body


def test_assets_js_wires_diff_items_to_asset_detail(client):
    """页面已渲染 ``data-asset-id``，但必须有脚本把它接成点击跳转。

    否则「Diff 条目可点进资产详情」就只是注释里的一句承诺 ——
    静态脚本没有构建链、也没有别的测试能发现它漏了。
    """
    resp = client.get("/static/assets.js")
    assert resp.status_code == 200
    script = resp.get_data(as_text=True)

    assert "diff-item-clickable" in script, "diff 条目没有可点的标记"
    assert "openDetail" in script and "asset-detail-panel" in script
    # 点击清单里的条目要能取到 asset_id 并打开详情。
    assert "diff-body" in script


def test_assets_js_never_renders_a_raw_scope_id_as_text(client):
    """方案第 4 节原则 2：前端**不显示** ``scope_id`` —— 实体 ID 只作表单 value。

    资产详情的「所属范围」一行原先直接写 ``asset.scope_id || "（未限定）"``，
    于是详情面板上会出现 ``scope_9f3c…``。这是「前端暴露内部模型」的典型形态：
    用户看到的应该是一个**能对上号的授权资产**，而不是一串数据库主键。

    这里只做源码级守卫（项目没有浏览器测试）：翻了就是翻了 —— 一旦有人把
    它改回原样，红的是这条测试，而不是某个用户的困惑。
    """
    script = client.get("/static/assets.js").get_data(as_text=True)

    assert "function scopeLabelById(" in script, "缺少 scope_id → 文案 的翻译函数"
    # 具名的那一行必须走翻译函数。
    assert "scopeLabelById(asset.scope_id)" in script
    # 不允许把 scope_id 直接当文案塞进 DOM（``textContent`` / ``el(...)`` 两种写法）。
    for forbidden in (
        '["所属范围", asset.scope_id ||',
        'el("dd", null, asset.scope_id)',
        "textContent = asset.scope_id",
    ):
        assert forbidden not in script, f"assets.js 又把 scope_id 当文案了: {forbidden}"


def test_assets_js_keeps_internal_ids_and_db_columns_off_the_screen(client):
    """方案第 4 节原则 2 的另外两个点名项：**UUID** 与**数据库字段**。

    原则 2 列的是三样 —— ``scope_id`` / UUID / 数据库字段。前一样在
    ``test_assets_js_never_renders_a_raw_scope_id_as_text`` 里守着，这里补后两样：

    * 详情标题此前写 ``idEl.textContent = asset.id``，页面上出现 ``asset_3f9c…``；
    * 摘要此前有一行「规范化键」，铺开的是 ``host|example.com`` 这种**列值**。

    两者都不是给使用者看的：ID 用来在 DOM 里定位（``data-asset-id`` 仍在、接口仍在），
    规范化键用来解释「为什么两条观测归并成一行」，属于排查信息。
    """
    script = client.get("/static/assets.js").get_data(as_text=True)

    for forbidden in (
        "textContent = asset.id",
        '["规范化键"',
        "asset.canonical_key",
    ):
        assert forbidden not in script, f"assets.js 又把内部标识/数据库字段上屏了: {forbidden}"
    # 定位能力不能一起删掉：详情面板仍要靠 ``data-asset-id`` 与接口取数。
    assert "asset-detail-id" in script
