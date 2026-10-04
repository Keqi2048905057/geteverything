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


@pytest.mark.parametrize(
    "action",
    [
        "scan",  # 已知扫描动作
        "chat",  # 已知 Agent 动作
        "",  # 表单给了 action 但值为空（`request.form.get("action", "scan")` 返回空串）
        "unknown",  # 从未登记的动作
        "definitely-not-an-action",  # 未来可能出现的新动作名
    ],
)
def test_any_page_post_action_requires_login(client, action):
    """**任意** `action` 值都必须先登录 —— 这条锁的是「守卫在分发之前」这个形状。

    ★ 为什么不用「断言 `_require_admin_for_page()` 出现在 `action` 分支之前」的
    源码字符串守卫：那种守卫锚在**写法**上（谁重排一下代码就红），而这里锚在
    **行为**上 —— 它不问守卫写在哪一行，只问「换一个 action 值，还拦不拦得住」。

    ★ 为什么必须用**未知** action 值：`"scan"`/`"chat"` 只覆盖今天存在的动作。
    历史缺陷正是「守卫按动作名白名单护，漏一个动作就漏一个洞」；用 `unknown`
    与 `definitely-not-an-action` 取值，等于断言 **「未来新增的动作默认也是安全的」**
    —— 只要有人把守卫塞回 `if action in _SCAN_ACTIONS:` 里面，这两条立刻变红
    （旧代码里它们会落到 `else: job_error = "未知操作"` 并返回 **200**）。

    ★ 反面也在：`else` 分支的存在说明「未知动作」本身是有处理路径的，
    这正是它能被当成回归探针的原因。

    ★ 与上面两条具名用例的**分工**：那两条钉「scan / chat 这两个**已知**动作的
    认证口径」（并写下它们各自的缺陷史），本条钉「**动作名不是安全边界**」这个更一般的形状。
    `scan` / `chat` 两条在这里重复出现是**故意**的 —— 万一有人单独改了其中一个，
    两条用例都会红，不会出现「只有具名用例红、参数化清单还是绿」的错觉。
    """
    resp = client.post("/", data={"action": action, "domain": "example.test"})
    assert resp.status_code == 401, f"action={action!r} 未登录也能过：守卫又被放回按动作名白名单里了"
    assert resp.get_json()["error_code"] == "unauthenticated"


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


def test_anonymous_homepage_does_not_leak_scan_summary(admin_client, client, store):
    """匿名首页**不得**下发扫描汇总与目标明细（结果库统计）。

    与上一条是**同一类**漏洞、但**不同来源**：上一条是「谁在授权」，
    这一条是「扫过什么、扫出多少」。首页的「汇总」面板读的是
    ``store.get_global_summary()`` / ``get_domain_summary()``，
    而 ``app.py:build_page_context()`` 当时**没有任何登录态判断** ——
    与 ``scopes``（``app.py:320``）/ ``recent_jobs``（``app.py:323``）
    那两处的写法不一致。

    匿名访客因此能读到：跑过几次、覆盖几个目标、命中多少条；
    并且 ``/?domain=<目标>`` 这一条 GET 还会把「该目标扫过几次、用了哪些工具、
    多少条结果」直接渲染出来 —— 目标名本身也就跟着回显了。

    这条钉住三件事：① 匿名不出现汇总标签；② 匿名不出现目标名；
    ③ 管理员路径照常（修的是可见性，不是把功能删了）。
    """
    store.save_results(
        "leaktarget.test",
        "subfinder",
        ["a.leaktarget.test", "b.leaktarget.test", "c.leaktarget.test"],
    )

    # ① 汇总面板的三个标签属于**数据载体**，匿名一个都不该出现。
    anon_home = client.get("/").get_data(as_text=True)
    for label in ("扫描运行次数", "已有目标数", "结果总数"):
        assert label not in anon_home, f"匿名首页仍然渲染汇总数字: {label}"

    # ② 带上 domain 也不行：目标名与「当前目标」区块都必须消失。
    anon_focus = client.get("/?domain=leaktarget.test").get_data(as_text=True)
    assert "leaktarget.test" not in anon_focus, "匿名首页通过 ?domain= 回显了目标名"
    assert "当前目标" not in anon_focus, "匿名首页仍然渲染目标明细区块"

    # ③ 已登录时照常下发。
    admin_home = admin_client.get("/").get_data(as_text=True)
    for label in ("扫描运行次数", "已有目标数", "结果总数"):
        assert label in admin_home, f"管理员首页丢了汇总面板: {label}"
    admin_focus = admin_client.get("/?domain=leaktarget.test").get_data(as_text=True)
    assert "当前目标" in admin_focus
    assert "leaktarget.test" in admin_focus


#: ``build_page_context()`` 里按登录态过滤的 5 个 session 键（§3.16.5 第 1 条，
#: 用户在 §3.16.8 第 2 问上确认「一并过滤」）。
AGENT_SESSION_KEYS = [
    "agent_history",
    "agent_steps",
    "pending_plan",
    "uploaded_targets",
    "agent_context",
]


def test_agent_session_keys_are_not_handed_to_anonymous_visitors(app_module, admin_client, client, monkeypatch):
    """匿名首页**不得**拿到那 5 个 Agent 会话键的**内容**；管理员照常。

    这 5 个键装的是 Agent 对话历史、待确认计划、上传目标与上下文。它们此前
    **不构成泄漏**：``web/`` 下对这 5 个名字的引用是 0 命中（既没有渲染点、
    也没有 JS 读它们）。但「不下发」比「不渲染」可靠 —— 少下发一个键，就少一次
    「将来加了个渲染点、忘了它没过滤」的机会。所以按 ``is_authenticated`` 过滤。

    ★ 怎么测才有意义：这 5 个键**没有渲染点**，所以不能断言「页面上看不到」——
    那种断言在改动前后都通过，等于什么都没锁。能观察到的差别只有一处：
    **上下文里这个键的值**。于是这里给 ``app.build_page_context`` 装一个**探针**：
    它照常调用真函数并原样返回，只是顺手把返回的字典记下来。这样被观察的对象
    就是模板**真正拿到**的那份上下文，而不是另造一个近似场景。

    ★ 匿名侧**必须自带内容**，否则这条断言是空转：新建的匿名 ``client`` 会话本来
    就是空的，「匿名上下文里没有 agent_history」在修复前后都成立。所以先用
    ``session_transaction()`` 往匿名客户端里塞满这 5 个键 —— 这**不是**人为构造的
    场景，而是一个真实状态：``core/auth.logout()`` 只 ``pop("local_admin")``，
    **不清** ``agent_history`` 等键，于是「登录过 → 退出 → 仍带着那份 cookie 浏览」
    的访客，会话里就有这些内容，而请求本身是匿名的。旧的 ``build_page_context()``
    在这种请求上会把内容原样下发。

    ★ 反向断言（管理员必须拿到）不是可有可无的：只钉「匿名为空」的话，
    把整个键删掉、或者让 ``build_page_context()`` 永远返回空，都能通过。
    """
    probe_history = [{"role": "user", "content": "probe-leak-marker"}]
    probe_plan = {"plan_id": "plan_probe", "message": "probe-leak-marker"}
    probe_targets = ["probe-leak-marker.test"]
    probe_context = {"note": "probe-leak-marker"}

    def _seed(test_client):
        """给这个客户端塞满 5 个键（不影响它是否已登录）。"""
        with test_client.session_transaction() as sess:
            sess["agent_history"] = list(probe_history)
            sess["agent_steps"] = [{"step": "probe-leak-marker"}]
            sess["pending_plan"] = dict(probe_plan)
            sess["uploaded_targets"] = list(probe_targets)
            sess["agent_context"] = dict(probe_context)

    seen = []
    real = app_module.build_page_context

    def _spy(*args, **kwargs):
        context = real(*args, **kwargs)
        # ③ 顺手再问一次「漏传 is_authenticated 会怎样」：默认值是 False（失败关闭），
        #    所以它必须与匿名侧一样空。同一次调用、同一个会话，差别只在那个参数。
        without_flag = real(*args, **{key: value for key, value in kwargs.items() if key != "is_authenticated"})
        seen.append((context, without_flag))
        return context

    monkeypatch.setattr(app_module, "build_page_context", _spy)

    # ① 匿名首页 —— 会话里有内容，请求本身未登录。
    _seed(client)
    assert client.get("/").status_code == 200
    assert len(seen) == 1, seen
    anon_context, anon_without_flag = seen[0]

    for key in AGENT_SESSION_KEYS:
        assert not anon_context[key], f"匿名上下文下发了 {key} 的内容: {anon_context[key]!r}"
        assert not anon_without_flag[key], f"漏传 is_authenticated 时下发了 {key} —— 默认值不再是失败关闭"
    # 键**在**（不是被删掉）：模板拿到空列表 / None，而不是 Undefined。
    assert "agent_history" in anon_context and "pending_plan" in anon_context
    assert anon_context["agent_history"] == [] and anon_context["agent_steps"] == []
    assert anon_context["pending_plan"] is None and anon_context["uploaded_targets"] is None
    # 连内容本身都不能出现在上下文里（防止有人「只把 history 清掉、其余照发」）。
    assert "probe-leak-marker" not in repr(anon_context), "匿名上下文里还残留着会话内容"

    # ② 管理员首页：同一批键必须有内容 —— 修的是「谁看得见」，不是把功能删了。
    seen.clear()
    _seed(admin_client)
    assert admin_client.get("/").status_code == 200
    assert len(seen) == 1, seen
    admin_context, admin_without_flag = seen[0]

    assert admin_context["agent_history"] == probe_history, "管理员丢了 Agent 对话历史"
    assert admin_context["agent_steps"] == [{"step": "probe-leak-marker"}]
    assert admin_context["pending_plan"] == probe_plan
    assert admin_context["uploaded_targets"] == probe_targets
    assert admin_context["agent_context"] == probe_context
    # ③ 的另一半：同一个带内容的会话，只要漏传标志，立刻退回空 —— 失败关闭。
    for key in AGENT_SESSION_KEYS:
        assert not admin_without_flag[key], f"漏传 is_authenticated 时下发了 {key} —— 默认值不再是失败关闭"
