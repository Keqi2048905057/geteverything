"""P0-1 契约锁定测试：只读 API 的鉴权现状（已知项，不改行为）。

`docs/DECISIONS.md` D 项预授权 = ✅（限文档与测试）：
**不改鉴权行为**，只把当前契约用测试锁定，并在 README / SECURITY 写明。

本文件是「防回归的说明书」，不是「期望的理想状态」：

* 如果将来有人给这些接口加上鉴权，这些用例会失败 —— 那是**好事**，
  说明必须同步更新 `docs/DECISIONS.md`、`SECURITY.md` 与 README 的鉴权列；
* 反之，如果这些接口意外变成「必须登录但没人知道」，这里也会失败。

同时锁定**写/执行类**接口必须登录（这条是硬边界，不能松）。
"""

import pytest

#: 当前仍然匿名可读的只读接口（已知项，见 DECISIONS-D）。
ANONYMOUS_READABLE = [
    "/api/tools",
    "/api/databases",
    "/api/results",
    "/api/export",
    "/api/exports",
]

#: 必须登录的接口（硬边界）。
ADMIN_ONLY = [
    ("GET", "/api/settings"),
    ("POST", "/api/settings"),
    ("GET", "/api/scopes"),
    ("POST", "/api/scopes"),
    ("POST", "/api/upload"),
    ("POST", "/api/run"),
    ("POST", "/api/tool/subfinder/run"),
    ("POST", "/api/jobs"),
    ("GET", "/api/jobs"),
    ("GET", "/api/jobs/job_x"),
    ("POST", "/api/jobs/job_x/cancel"),
    ("POST", "/api/jobs/job_x/retry"),
    ("GET", "/api/jobs/job_x/steps"),
    ("GET", "/api/jobs/job_x/events"),
    ("GET", "/api/jobs/job_x/artifacts"),
    ("GET", "/api/jobs/job_x/results"),
    ("GET", "/api/artifacts/art_x"),
]


def test_auth_session_itself_is_anonymous_by_design(client):
    """``/api/auth/session`` 必须匿名可读：前端靠它判断「我现在登录了没」。"""
    body = client.get("/api/auth/session").get_json()
    assert body["authenticated"] is False
    assert body["auth_header"] == "X-Local-Token"


@pytest.mark.parametrize("path", ANONYMOUS_READABLE)
def test_readonly_endpoints_are_currently_anonymous(client, path):
    """锁定 DECISIONS-D 的现状：只读接口匿名可读（有意保持，不是遗漏）。"""
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} 不再匿名可读：请同步更新 DECISIONS.md / SECURITY.md / README"
    assert resp.get_json() is not None


@pytest.mark.parametrize("method,path", ADMIN_ONLY)
def test_admin_only_endpoints_reject_anonymous(client, method, path):
    """写 / 执行类接口必须 401 —— 这是不可放松的边界。"""
    resp = client.open(path, method=method, json={})
    assert resp.status_code == 401, f"{method} {path} 竟然允许匿名访问"
    assert resp.get_json()["error_code"] == "unauthenticated"


def test_tools_payload_has_no_server_path(client):
    """/api/tools 匿名可读，但**不能**顺带泄露服务器路径。"""
    import json

    raw = json.dumps(client.get("/api/tools").get_json(), ensure_ascii=False)
    assert "C:\\" not in raw
    assert "local.db" not in raw


def test_results_payload_has_no_server_path(client):
    import json

    raw = json.dumps(client.get("/api/results").get_json(), ensure_ascii=False)
    assert "C:\\" not in raw
    assert "scan_results.db" not in raw


def test_page_scan_still_requires_login(client):
    """首页表单扫描也必须登录（P0 验收：无匿名扫描）。"""
    resp = client.post("/", data={"action": "scan", "domain": "example.test"})
    assert resp.status_code == 401


def test_page_chat_action_requires_login(client):
    """``action=chat`` 也必须登录 —— 它**不是**只读浏览。

    ``app.py`` 的 docstring 一直把 ``action=chat`` 归为「只读浏览」，
    ``docs/API.md`` 也写着「``action=chat`` 可匿名」。但这与事实不符：

    * chat 分支（``app.py`` 的 ``elif action == "chat"``）**没有**任何认证调用；
    * 它把消息交给 ``agent/service.handle_agent_message()``；
    * 而 Agent 的 ``_tool_subdomain`` / ``_tool_httpx`` 直接调
      ``tool_runner.run_tools()`` 与 ``HttpxRunner().run_scan()``，
      **两者都不查 ``GEF_ALLOW_REAL_SCAN``，也不查 Scope**
      （``docs/AGENT_ASYNC_IMPACT.md`` I-5 的实测证据）。

    也就是说：**一个未登录的 HTTP 请求可以走通「意图 → 确认执行 → 真实扫描」**，
    且不产生任何 ``job.created`` 审计。这与 ``core/auth.py`` 自己写下的
    「不能匿名扫描」直接冲突，因此这里钉住它。

    这不是新增边界，而是把既有边界补上 —— 与 ``action=scan`` 同级。
    """
    resp = client.post("/", data={"action": "chat", "agent_message": "你好"})
    assert resp.status_code == 401, "action=chat 竟然允许匿名访问（它能经 Agent 发起真实扫描）"
    assert resp.get_json()["error_code"] == "unauthenticated"


def test_page_chat_action_still_works_when_logged_in(admin_client):
    """反向守卫：加了认证之后，**管理员那条路必须还能走**。

    只测「匿名 401」是不够的 —— 那种断言对「把整个 chat 分支删掉」也会通过。
    这条用例钉住另一侧：管理员带 Token 提交同样的表单，必须拿到 200
    （页面级 chat 是「重渲染首页 + 把回复写进 session」，成功即 200）。

    ★ 实测提醒：这条用例会触发 werkzeug 的
    ``The 'session' cookie is too large ...`` 告警（``agent_history``/``agent_steps``
    把整段回复塞进签名 Cookie）。那是 `docs/CODEBASE_MAP.md` 第 6 节第 20 条
    记录的既有坑，**不是本用例引入的失败**；用例只断言状态码，不依赖 Cookie 是否留存。
    """
    resp = admin_client.post("/", data={"action": "chat", "agent_message": "你好"})
    assert resp.status_code == 200, "管理员走 chat 被误伤了：守卫不该连正路一起堵"
    assert resp.get_json() is None, "页面路由应返回 HTML，不是 JSON"


def test_anonymous_homepage_does_not_leak_authorized_assets(admin_client, client):
    """匿名首页**不得**下发授权资产（名称 / 目标 / 状态）。

    首页曾无条件执行 ``context["scopes"] = _load_scope_options()``，而
    Phase 1 又把 ``allowed_domains`` / ``allowed_cidrs`` 渲染进了资产卡片 ——
    等于把「这份授权叫什么、覆盖哪些目标」整份摊给匿名访客。

    资产页（``/assets``）一开始就是**按登录态过滤**的（``app.py`` 的
    ``scopes=_load_scope_options() if is_authenticated else []``），并有
    ``test_assets_api.py`` 的守卫。首页那条路漏了同一个判断，这里补齐并钉住。
    """
    resp = admin_client.post(
        "/api/scopes",
        json={
            "name": "培正学院公网资产",
            "allowed_domains": ["www.example.test"],
            "active_scan": False,
        },
    )
    assert resp.status_code == 201, resp.get_json()

    anon_home = client.get("/").get_data(as_text=True)
    for leaked in ("培正学院公网资产", "www.example.test"):
        assert leaked not in anon_home, f"匿名首页泄露了授权资产信息: {leaked}"
    # 「授权资产」这四个字本身可以出现在**提示文案**里（告诉用户登录后可见），
    # 但卡片与下拉框这两个**数据载体**必须一个都不渲染 —— 它们是数据泄漏的形状。
    assert 'class="scope-asset' not in anon_home, "匿名首页仍渲染授权资产卡片"
    assert 'id="scope_id"' not in anon_home, "匿名首页仍渲染授权资产下拉框"

    # 已登录时照常下发 —— 这条修的是「谁看得见」，不是「还显不显示」。
    admin_home = admin_client.get("/").get_data(as_text=True)
    assert "培正学院公网资产" in admin_home
    assert "www.example.test" in admin_home
    assert 'class="scope-asset' in admin_home
