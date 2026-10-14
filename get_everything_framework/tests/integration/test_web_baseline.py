"""M1 集成测试：首页退役后的根路由、登录页、/health、未登录拒绝扫描。

全部走 Flask 测试客户端，不启动真实服务器、不写仓库数据库。

**口径变更（测试阶段改造，不要改回去）**：原首页 ``web/templates/index.html``
连同它的 ``POST /`` 表单动作（``action=scan`` / ``action=chat``）已整体退役：

* ``GET /`` 只做 ``302 → /scan-center``，不渲染任何模板；
* ``POST /`` 由 Flask 回 **405 Method Not Allowed**（该 URL 上根本不存在 POST
  处理器），不再是「一个 401 的幽灵页面接口」；
* 登录成功后直接落在 ``/scan-center``，「已登录」徽标由扫描中心渲染。

★ 本文件对 ``client.get("/")`` **不加** ``follow_redirects=True``：Flask 测试
客户端默认 ``follow_redirects=False``，所以断言拿到的是 302 本身。一旦有人把
跳转改回渲染，下面的用例立刻变红 —— 这正是它要守的东西。
"""

import json


def test_root_redirects_to_scan_center(client):
    """旧首页退役：``GET /`` 只做 302 到 ``/scan-center``，不再渲染任何模板。

    原名 ``test_index_renders_without_template_error``，当时钉的是「首页不再
    ``TemplateNotFound``」（断言 200 + 正文含「本机联调版」）。首页整体退役后，
    那个断言对象已经不存在了 —— 但它守的那件事（「根路径必须能用，不能是一个
    500 / 模板找不到的死路由」）**没有作废**，只是换成了新口径：根路径现在
    把使用者送到唯一的主页面。

    ★ 断言 302 而不是「跟随后 200」：跟着跳转的话，这条用例对「根路径自己
    渲染一个空壳首页 + 顺便做个链接」也会通过，等于没锁住「退役」这件事。
    """
    resp = client.get("/")
    assert resp.status_code == 302, "GET / 又渲染东西了：首页退役后它只应当跳转"
    assert resp.headers["Location"].endswith("/scan-center"), (
        f"GET / 的跳转目标不是扫描中心：{resp.headers.get('Location')!r}"
    )
    # 响应体是 werkzeug 为 302 自动生成的「Redirecting…」提示页（实测 211 字节），
    # 所以**不能**断言 body 为空；能断言的是「它没有渲染任何业务模板」——
    # 旧首页的正文特征一个都不该出现。
    body = resp.get_data(as_text=True)
    for home_marker in ("本机联调版", "Agent 对话", "创建扫描任务", "任务已创建"):
        assert home_marker not in body, f"302 响应里出现了旧首页正文: {home_marker}"


def test_login_page_renders(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "管理员 Token" in resp.get_data(as_text=True)


def test_health_needs_no_login_and_hides_paths(client):
    """M1 验收：/health 返回数据库与 worker 状态，且不泄露路径。"""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["version"].startswith("local-")
    assert data["database"] in {"ok", "missing", "error"}
    assert data["worker"] in {"ok", "missing", "stale"}
    assert "tools" in data and "tools_summary" in data

    raw = json.dumps(data, ensure_ascii=False)
    assert "E:\\" not in raw and "E:/" not in raw
    assert "nvarchar" not in raw
    assert "dev-secret-key" not in raw


def test_health_reports_worker_missing_by_default(client):
    """M1 阶段没有独立 worker 进程，必须能被区分出来。"""
    data = client.get("/health").get_json()
    assert data["worker"] in {"missing", "stale"}


def test_anonymous_scan_is_rejected(client):
    """M1 验收：未登录不能执行扫描。"""
    resp = client.post("/api/run", json={"domain": "example.test", "tools": ["subfinder"]})
    assert resp.status_code == 401
    body = resp.get_json()
    assert body["error_code"] == "unauthenticated"


def test_anonymous_single_tool_scan_is_rejected(client):
    resp = client.post("/api/tool/subfinder/run", json={"domain": "example.test"})
    assert resp.status_code == 401


def test_anonymous_settings_read_and_write_are_rejected(client):
    """M1 验收：未登录不能读取或写入设置（含密钥）。"""
    assert client.get("/api/settings").status_code == 401
    assert client.post("/api/settings", json={"llm_api_key": "x"}).status_code == 401
    assert client.get("/api/settings/enscan").status_code == 401
    assert client.post("/api/settings/enscan", json={"enscan_aqc_cookie": "x"}).status_code == 401


def test_anonymous_index_post_scan_is_rejected(client):
    """首页表单扫描这条后门已随模板一起消失：``POST /`` 现在是 **405**。

    原名 ``test_anonymous_index_post_scan_is_rejected``，当时钉的是「首页表单
    扫描也要登录（401 JSON）」。首页退役后这个断言对象没了，但它守的事**升级**了：
    不是「这条后门有人把守」，而是「这条后门不存在」。

    ★ 为什么 405 比 401 更好：401 意味着「路由在、处理器在，只是先查了一下
    登录态」—— 它是一条**幽灵接口**，任何一处守卫写漏（例如把
    ``_require_admin_for_page()`` 塞回 ``if action in _SCAN_ACTIONS:`` 里面，
    或者新加一个没登记的 action）都会立刻变成可用的洞，而且这类洞**不会让功能
    测试变红**（功能本身是好的）。405 是 Flask 在**路由层**给出的回答：该 URL
    上根本没有 POST 处理器，没有分支、没有表单解析、没有 action 分发，也就没有
    「漏一个动作就漏一个洞」的余地。守卫从「行为正确」前进到「形状不存在」。
    """
    resp = client.post("/", data={"action": "scan", "domain": "example.test"})
    assert resp.status_code == 405, (
        f"POST / 竟然还有处理器（返回 {resp.status_code}）：首页表单扫描这条后门复活了。"
        "405 意味着路由整体消失，比「一个 401 的幽灵接口」更彻底。"
    )
    # 不是 401：不能再拿「401」当作「这里已经没有页面接口」的证据。
    assert resp.status_code != 401, "POST / 又变成了一个 401 的幽灵页面接口"


def test_wrong_token_is_rejected(client):
    resp = client.post(
        "/api/run",
        json={"domain": "example.test", "tools": ["subfinder"]},
        headers={"X-Local-Token": "definitely-wrong"},
    )
    assert resp.status_code == 401


def test_login_flow_grants_session(admin_client, admin_token):
    """登录页 POST 成功后会建立会话，并**直接落在扫描中心**。

    原断言是 ``Location`` 以 ``/`` 结尾、随后 ``GET /`` 的正文含「已登录」。
    首页退役后这两处都改了：登录成功的跳转目标变成 ``/scan-center``，
    「已登录」徽标改由 ``scan_center.html`` 渲染（``index.html`` 已删除）。
    断言的是**同一个契约**（登进去就落在可用页面上、且会话真的建立起来了），
    只是落点从首页换成了唯一的主页面。
    """
    client = admin_client
    resp = client.post("/login", data={"token": admin_token}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/scan-center"), (
        f"登录成功后没有落在扫描中心：{resp.headers.get('Location')!r}"
    )
    # 不再跳回已退役的 ``/``（那会多一次 302 中转，且语义是「回首页」）。
    assert resp.headers["Location"] != "/", "登录成功后又被送回了已退役的首页"

    center = client.get("/scan-center")
    assert center.status_code == 200
    assert "已登录" in center.get_data(as_text=True), "会话没建立：扫描中心仍显示未登录"


def test_login_flow_rejects_bad_token(client):
    resp = client.post("/login", data={"token": "nope"})
    assert resp.status_code == 200
    assert "Token 无效" in resp.get_data(as_text=True)


def test_auth_session_and_logout(client, admin_token):
    status = client.get("/api/auth/session").get_json()
    assert status["authenticated"] is False
    assert status["auth_header"] == "X-Local-Token"

    login = client.post("/api/auth/login", json={"token": admin_token})
    assert login.status_code == 200
    assert client.get("/api/auth/session").get_json()["authenticated"] is True

    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/session").get_json()["authenticated"] is False


def test_admin_token_can_read_settings(admin_client):
    """带上有效 Token 后，设置接口返回脱敏配置。"""
    resp = admin_client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert "settings" in body
