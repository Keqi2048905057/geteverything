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


# ── 下一阶段规划方案 Phase 1：首页「授权资产」展示 ──────────


def test_page_shows_authorized_assets_as_human_readable_cards(admin_client):
    """方案第 4 节「原则 2」：首页显示的是**授权资产**，不是 ``scope_…``。

    用户看到的三件事：这份授权叫什么、覆盖哪些目标、现在是什么状态。
    这三条都得在页面上，否则「已授权」只是一个后端概念。
    """
    scope_id = _create_scope_via_api(
        admin_client,
        name="培正学院公网资产",
        allowed_domains=["www.example.test"],
        active_scan=True,
    )
    home = admin_client.get("/").get_data(as_text=True)

    assert "授权资产" in home
    assert "培正学院公网资产" in home
    assert "www.example.test" in home
    assert "已授权" in home
    # 卡片区是服务端渲染的骨架，不依赖任何脚本；ID 只在表单 value 里。
    assert 'class="scope-asset' in home
    assert f'value="{scope_id}"' in home, "实体 ID 必须仍然作为表单 value 提交"


def test_page_does_not_render_raw_scope_ids_as_labels(admin_client):
    """「隐藏 Scope ID」的可执行口径：实体 ID 绝不进**可见文案**。

    历史上首页把下拉框写成 ``培正学院公网资产（scope_9f3c…）`` —— 用户被迫
    理解一个内部标识。这条断言钉住它不再复活（三种最容易漏回的写法）。
    """
    scope_id = _create_scope_via_api(admin_client, name="培正学院公网资产")
    home = admin_client.get("/").get_data(as_text=True)

    for leaked in (f"（{scope_id}）", f"({scope_id})", f"Scope：{scope_id}"):
        assert leaked not in home, f"内部 ID 又进了可见文案: {leaked}"
    # 后台术语也不该是首页的主概念。
    assert "授权范围（Scope）" not in home


def test_page_marks_scopes_without_active_scan(admin_client):
    """没开 ``active_scan`` 的范围必须**如实**标成「仅被动」，不能也写「已授权」。"""
    _create_scope_via_api(admin_client, name="只读范围", active_scan=False)
    home = admin_client.get("/").get_data(as_text=True)
    assert "仅被动" in home
    assert "已授权" not in home
