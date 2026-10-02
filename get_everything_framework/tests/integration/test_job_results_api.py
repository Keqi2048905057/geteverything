"""Phase 4「结果体验」集成测试：``GET /api/jobs/<job_id>/results``。

对应下一阶段方案第 6 节 Phase 4 原文要求展示的四段：
**发现资产 / 服务 / 技术栈 / 风险信息**。

这一层验证的是「从 Job 导向结果」这条链路真的通：

    POST /api/jobs → 执行 → assets / observations 落库
                  → GET /api/jobs/{id}/results → 四段结果

因此这里不用手工插资产也能拿到结果（mock 任务会经
``ingest_step_observations`` 的兜底类型落 subdomain）；需要
``status_code`` / ``title`` / ``webserver`` / ``tech`` 这类 httpx 属性时，
用 ``record_observation`` 追加一条真实形状的观测 —— 与
``test_assets_api.py::test_diff_endpoint_marks_changed_attributes`` 同一手法。

**本文件不发起任何真实扫描**：目标是 RFC 6761 保留域 ``example.test``，
执行全部走 mock 模式（``GEF_ALLOW_REAL_SCAN`` 由 ``conftest`` 钉死为 false）。
"""

import pytest

from core import assets as assets_store
from core import jobs as jobs_store


@pytest.fixture
def scope_id(tmp_path, monkeypatch):
    """建一个 Scope 并返回其 ID（走 store，不经过 API）。"""
    import config
    import core.db as core_db
    from core import scope_store

    path = str(tmp_path / "test_local.db")
    monkeypatch.setitem(config.LOCAL_DB_CONFIG, "path", path)
    core_db.reset_schema_cache()
    core_db.ensure_schema(path)
    return scope_store.create(name="结果体验范围", allowed_domains=["example.test"]).id


def _run_job(scope_id, targets, tools=("subfinder",)):
    """建一个 mock 任务、领取并执行完毕，返回 job dict。"""
    from jobs.executor import execute_job

    job = jobs_store.create_job(scope_id=scope_id, targets=list(targets), tools=list(tools))
    jobs_store.claim_next_job("w-results-test", lease_seconds=300)
    return execute_job(job["id"])


def _observe(scope_id, job_id, asset_type, value, data, *, tool="httpx"):
    """给某任务追加一条真实形状的观测（httpx 的 URL + 属性）。"""
    return assets_store.record_observation(
        asset_type,
        value,
        scope_id=scope_id,
        job_id=job_id,
        source_tool=tool,
        data=data,
    )


# ── 鉴权与 404 ────────────────────────────────────────────


def test_results_endpoint_requires_admin(client):
    """结果是「整理后的情报」，必须登录（与 /api/assets 同一口径）。"""
    resp = client.get("/api/jobs/job_x/results")
    assert resp.status_code == 401
    assert resp.get_json()["error_code"] == "unauthenticated"


def test_results_endpoint_unknown_job_is_404(admin_client):
    resp = admin_client.get("/api/jobs/job_missing/results")
    assert resp.status_code == 404
    assert resp.get_json()["error_code"] == "not_found"


def test_results_tail_does_not_shadow_the_diff_route(admin_client, scope_id):
    """``/results`` 与 ``/diff/<after>`` 段数不同，不能互相吃掉。

    Werkzeug 静态段优先，因此这两个尾段必须各自命中自己的视图。
    """
    job = _run_job(scope_id, ["a.example.test"])

    results = admin_client.get(f"/api/jobs/{job['id']}/results")
    assert results.status_code == 200
    assert results.get_json()["job_id"] == job["id"]

    diff = admin_client.get(f"/api/jobs/{job['id']}/diff/{job['id']}")
    assert diff.status_code == 200
    assert "counts" in diff.get_json()


# ── 出参形状 ──────────────────────────────────────────────


def test_results_payload_shape_is_stable(admin_client, scope_id):
    """四段 + counts + notes 都要在；形状稳定前端才能只写一份渲染函数。"""
    job = _run_job(scope_id, ["a.example.test"])

    body = admin_client.get(f"/api/jobs/{job['id']}/results").get_json()

    assert body["ok"] is True
    assert body["job_id"] == job["id"]
    assert body["mode"] == "mock"
    assert set(body["counts"]) == {"assets", "observations", "services", "technologies", "risk_hints"}
    for section in ("assets", "services", "technologies", "risk_hints"):
        assert set(body[section]) >= {"total", "items", "truncated"}, section
        assert isinstance(body[section]["items"], list)
    assert isinstance(body["notes"], list)


def test_results_only_include_this_jobs_observations(admin_client, scope_id):
    """**只看本次任务**：上一次任务看到的资产不能混进这次的结果。

    这是 Phase 4「从 Job 导向结果」的核心口径 —— 与 /diff 同源。
    """
    first = _run_job(scope_id, ["first.example.test"])
    second = _run_job(scope_id, ["second.example.test"])

    body = admin_client.get(f"/api/jobs/{second['id']}/results").get_json()
    values = {item["value"] for item in body["assets"]["items"]}

    assert values, "第二个任务竟然没有任何资产"
    assert all("first.example.test" not in str(value) for value in values), values
    assert first["id"] != second["id"]


def test_mock_mode_explains_itself_instead_of_looking_like_an_empty_scan(admin_client, scope_id):
    """mock 任务必须带一句「这是预期行为，不是采集失败」。

    否则页面上会渲染成「扫了但什么都没有」，而这与「工具失败」看起来一模一样 ——
    正是方案第 6.2 节要消灭的那类歧义。
    """
    job = _run_job(scope_id, ["a.example.test"])
    body = admin_client.get(f"/api/jobs/{job['id']}/results").get_json()

    assert any("mock" in note for note in body["notes"]), body["notes"]


def test_every_result_carries_the_no_vulnerability_scanning_disclaimer(admin_client, scope_id):
    """**本阶段最重要的一条**：免责说明必须每次都在。

    只跑 subfinder + httpx 时，「没有漏洞结论」是常态；页面若不说明，
    用户会把「风险提示：无」读成「这个站是安全的」。
    """
    job = _run_job(scope_id, ["a2.example.test"])
    body = admin_client.get(f"/api/jobs/{job['id']}/results").get_json()

    assert any("不做漏洞扫描" in note for note in body["notes"]), body["notes"]
    assert any("不等于" in note for note in body["notes"]), body["notes"]


# ── 四段内容 ──────────────────────────────────────────────


def test_discovered_assets_carry_type_label_and_source_tool(admin_client, scope_id):
    job = _run_job(scope_id, ["a.example.test"])
    body = admin_client.get(f"/api/jobs/{job['id']}/results").get_json()

    assets = body["assets"]
    assert assets["total"] >= 1
    assert assets["by_type"]
    assert assets["type_labels"]["subdomain"] == "子域"

    first = assets["items"][0]
    assert first["type"] == "subdomain"
    assert first["type_label"] == "子域"
    assert first["value"]
    assert "subfinder" in first["source_tools"]


def test_services_are_derived_from_http_observations(admin_client, scope_id):
    """httpx 的 URL 观测要变成「哪台主机上出现了什么协议 / 端口」。"""
    job = _run_job(scope_id, ["b.example.test"])
    _observe(scope_id, job["id"], "url", "https://b.example.test/", {"status_code": 200, "title": "OK"})

    body = admin_client.get(f"/api/jobs/{job['id']}/results").get_json()
    services = body["services"]["items"]

    assert services
    row = next(item for item in services if item["host"] == "b.example.test")
    assert row["scheme"] == "https"
    assert row["port"] == 443
    assert row["kind_label"] == "HTTP 端点"


def test_technologies_are_derived_from_httpx_attributes(admin_client, scope_id):
    job = _run_job(scope_id, ["c.example.test"])
    _observe(
        scope_id,
        job["id"],
        "url",
        "https://c.example.test/",
        {"status_code": 200, "webserver": "nginx/1.18.0", "tech": ["PHP", "jQuery"]},
    )

    body = admin_client.get(f"/api/jobs/{job['id']}/results").get_json()
    items = body["technologies"]["items"]
    by_name = {item["name"]: item for item in items}

    assert by_name["nginx/1.18.0"]["kind"] == "webserver"
    assert by_name["PHP"]["kind"] == "tech"
    assert by_name["PHP"]["hosts"] == ["c.example.test"]


def test_risk_hints_are_observable_facts_not_vulnerabilities(admin_client, scope_id):
    """风险提示来自观测事实，且必须写明「本框架不做漏洞扫描」。"""
    job = _run_job(scope_id, ["d.example.test"])
    _observe(scope_id, job["id"], "url", "http://d.example.test/", {"status_code": 200})

    body = admin_client.get(f"/api/jobs/{job['id']}/results").get_json()
    hints = {item["code"]: item for item in body["risk_hints"]["items"]}

    assert "plain_http" in hints
    assert hints["plain_http"]["level"] == "notice"
    assert hints["plain_http"]["level_label"] == "可留意"
    assert hints["plain_http"]["evidence"][0]["value"] == "http://d.example.test/"

    # 出参里不允许出现漏洞分级字段 —— 前端才不会再造一个「严重程度」。
    for hint in body["risk_hints"]["items"]:
        assert "severity" not in hint
        assert "cve" not in hint


def test_zero_hints_still_ships_the_not_a_clean_bill_notice(admin_client, scope_id):
    """零风险提示时，说明仍然必须下发。

    用 mock 的 ``empty`` 场景造一个「真的什么都没有」的任务
    （``no_results``：跑通但零发现）。这时四段全空，最容易渲染成
    「风险提示：无 → 这个站安全」——所以这一条必须钉住。
    """
    from jobs.executor import execute_job

    job = jobs_store.create_job(
        scope_id=scope_id, targets=["e.example.test"], tools=["subfinder"], scenario="empty"
    )
    jobs_store.claim_next_job("w-results-test", lease_seconds=300)
    execute_job(job["id"])

    body = admin_client.get(f"/api/jobs/{job['id']}/results").get_json()

    assert body["counts"] == {
        "assets": 0,
        "observations": 0,
        "services": 0,
        "technologies": 0,
        "risk_hints": 0,
    }
    assert any("不等于" in note for note in body["notes"]), body["notes"]
    assert any("不做漏洞扫描" in note for note in body["notes"]), body["notes"]


def test_unprobed_hosts_are_flagged_as_a_coverage_gap(admin_client, scope_id):
    """只跑 subfinder 时「有子域但没做 HTTP 探测」必须被说出来。

    这是「没看」与「没问题」的分界 —— 页面若只显示一份干净的子域列表，
    用户会以为这些主机已经被检查过了。
    """
    job = _run_job(scope_id, ["k.example.test"])
    body = admin_client.get(f"/api/jobs/{job['id']}/results").get_json()

    hints = {item["code"]: item for item in body["risk_hints"]["items"]}
    assert "unprobed_hosts" in hints
    assert hints["unprobed_hosts"]["count"] == body["assets"]["total"]


def test_failed_step_is_reported_as_incomplete_coverage(admin_client, scope_id):
    """覆盖不完整时，「没扫到」与「没有」必须能被区分开。

    用 mock 的 ``timeout`` 场景让步骤落到终态失败 —— mock 步骤**不经过**
    ``_execute_real_step``，所以那条路上的 Scope 复检在这里不可达
    （这也是为什么本用例不 monkeypatch ``validate_step_target``）。
    """
    from jobs.executor import execute_job

    job = jobs_store.create_job(
        scope_id=scope_id, targets=["f.example.test"], tools=["subfinder"], scenario="timeout"
    )
    jobs_store.claim_next_job("w-results-test", lease_seconds=300)
    execute_job(job["id"])

    assert jobs_store.list_steps(job["id"])[0]["status"] == jobs_store.STEP_TIMEOUT

    body = admin_client.get(f"/api/jobs/{job['id']}/results").get_json()
    hints = {item["code"]: item for item in body["risk_hints"]["items"]}

    assert "incomplete_coverage" in hints
    assert hints["incomplete_coverage"]["level"] == "attention"


# ── 只读性 ────────────────────────────────────────────────


def test_results_endpoint_is_read_only(admin_client, scope_id):
    """结果接口不得写任何东西：连调两次，库里的行数与审计都不变。"""
    from core import audit

    job = _run_job(scope_id, ["g.example.test"])
    before_assets = len(assets_store.list_assets(scope_id=scope_id, limit=1000))
    before_obs = len(assets_store.list_observations(job_id=job["id"], limit=2000))
    before_audit = len(audit.list_events(limit=500))
    before_events = len(jobs_store.list_events(job["id"]))

    for _ in range(2):
        assert admin_client.get(f"/api/jobs/{job['id']}/results").status_code == 200

    assert len(assets_store.list_assets(scope_id=scope_id, limit=1000)) == before_assets
    assert len(assets_store.list_observations(job_id=job["id"], limit=2000)) == before_obs
    assert len(audit.list_events(limit=500)) == before_audit
    assert len(jobs_store.list_events(job["id"])) == before_events


def test_results_response_exposes_no_server_path(admin_client, scope_id):
    job = _run_job(scope_id, ["h.example.test"])
    raw = admin_client.get(f"/api/jobs/{job['id']}/results").get_data(as_text=True)

    assert "C:\\\\" not in raw
    assert "test_local.db" not in raw


def test_results_observations_limit_is_clamped(admin_client, scope_id):
    """``observations_limit`` 有上限，且非法值回落而不是 400。"""
    job = _run_job(scope_id, ["i.example.test"])

    for value in ("0", "-5", "abc", "999999"):
        resp = admin_client.get(f"/api/jobs/{job['id']}/results?observations_limit={value}")
        assert resp.status_code == 200, value

    assert admin_client.get(f"/api/jobs/{job['id']}/results?scope_id=scope_other").status_code == 200


# ── 前端接线（静态源码守卫） ──────────────────────────────


def test_job_detail_frontend_renders_all_four_sections(admin_client):
    """``app.js`` 必须真的渲染四段，而不是只取到数据丢掉。

    项目没有浏览器测试（方案第 2.4 节：不引入前端框架/构建链），所以这里做
    源码级守卫；渲染路径另用一次性 DOM 桩人工核对过。
    """
    resp = admin_client.get("/static/app.js")
    assert resp.status_code == 200
    script = resp.get_data(as_text=True)

    assert "/results" in script, "app.js 没有请求结果接口"
    for section in ("assets", "services", "technologies", "risk_hints"):
        assert section in script, f"app.js 没有渲染 {section} 段"
    assert "job-results" in script

    css = admin_client.get("/static/app.css").get_data(as_text=True)
    assert ".job-results" in css
    assert ".result-note" in css


def test_job_detail_frontend_shows_the_notes(admin_client):
    """``notes`` 必须被渲染 —— 它承载「没有提示 ≠ 没有漏洞」。"""
    script = admin_client.get("/static/app.js").get_data(as_text=True)
    assert "data.notes" in script or "notes" in script
    assert "result-note" in script


def test_app_js_does_not_hardcode_risk_level_wording(admin_client):
    """风险级别的**中文文案**只能来自服务端（``level_label``），前端不得写死。

    与 Phase 3 的节奏说明同一条理由：写死就会漂移 ——
    后端改了措辞，页面还停在上一个版本。
    """
    script = admin_client.get("/static/app.js").get_data(as_text=True)

    assert "level_label" in script, "前端没有使用服务端下发的级别文案"
    for hardcoded in ("建议人工确认", "可留意"):
        assert hardcoded not in script, f"app.js 写死了风险级别文案: {hardcoded}"
