"""M2 集成测试（三）：首页表单走与 API 相同的 Scope 校验。

首页曾经是「直接调 run_tools」的后门，绕过所有 Scope 与认证检查。
本文件锁死修复后的行为：
* 未登录 → 401；
* 没有 Scope → 页面上不出现可提交的扫描表单，并给出创建指引；
* 目标越界 → 页面回显 Scope 校验失败，不执行任何真实扫描。
"""


def _create_scope_via_api(admin_client, **overrides):
    payload = {"name": "首页测试范围", "allowed_domains": ["example.test"], "active_scan": False}
    payload.update(overrides)
    resp = admin_client.post("/api/scopes", json=payload)
    assert resp.status_code == 201
    return resp.get_json()["scope"]["id"]


def test_page_requires_login_for_scan_action(client):
    resp = client.post("/", data={"action": "scan", "domain": "example.test"})
    assert resp.status_code == 401
    assert resp.get_json()["error_code"] == "unauthenticated"


def test_page_without_scope_shows_creation_hint(admin_client):
    home = admin_client.get("/").get_data(as_text=True)
    assert "还没有任何授权范围" in home
    assert "/api/scopes" in home


def test_page_with_scope_shows_form(admin_client):
    scope_id = _create_scope_via_api(admin_client)
    home = admin_client.get("/").get_data(as_text=True)
    assert "创建扫描任务" in home
    assert scope_id in home


def test_page_scan_creates_async_job(admin_client):
    """M3 起：首页提交 = 创建异步任务，立即返回 queued，不再同步等待。"""
    scope_id = _create_scope_via_api(admin_client)
    resp = admin_client.post(
        "/",
        data={"action": "scan", "domain": "example.test", "scope_id": scope_id},
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "任务已创建" in body
    assert scope_id in body
    # 任务必须真的落到库里，而不是只在页面回显
    jobs = admin_client.get("/api/jobs").get_json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["status"] == "queued"
    assert jobs[0]["mode"] == "mock"


def test_page_scan_does_not_wait_for_worker(admin_client):
    """没有 worker 时，首页提交也必须立刻返回（页面不阻塞）。"""
    import time

    scope_id = _create_scope_via_api(admin_client)
    started = time.perf_counter()
    for _ in range(5):
        admin_client.post("/", data={"action": "scan", "domain": "example.test", "scope_id": scope_id})
    elapsed = time.perf_counter() - started

    assert elapsed < 5.0, f"创建 5 个任务耗时 {elapsed:.2f}s，首页疑似仍在同步执行"
    assert admin_client.get("/api/jobs").get_json()["counts"]["queued"] == 5


def test_page_scan_rejects_out_of_scope_target(admin_client):
    scope_id = _create_scope_via_api(admin_client)
    resp = admin_client.post(
        "/",
        data={"action": "scan", "domain": "evil.test", "scope_id": scope_id},
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Scope 校验失败" in body


def test_page_scan_requires_scope_selection(admin_client):
    _create_scope_via_api(admin_client)
    resp = admin_client.post("/", data={"action": "scan", "domain": "example.test"})
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # 缺 scope_id 时给出与 API 一致的说明
    assert "scope_id" in body
