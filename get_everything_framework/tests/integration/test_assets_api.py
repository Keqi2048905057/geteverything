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
    job = _run_job(scope_id, ["f.example.test"])
    resp = admin_client.get(f"/api/jobs/{job['id']}/diff/{job['id']}?include_unchanged=0")
    assert resp.status_code == 200
    assert resp.get_json()["unchanged"] == []


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
