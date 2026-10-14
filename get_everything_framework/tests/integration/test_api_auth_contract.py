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


def test_retired_home_page_has_no_post_actions_anymore(client, app_module):
    """``POST /`` 的**全部**动作已随首页模板一起退役：任意 action 一律 **405**。

    本用例合并了原来的四条（它们全部钉在旧首页的 ``POST /`` 动作上）：

    * ``test_page_scan_still_requires_login`` —— 首页表单扫描必须登录（原断言 401）；
    * ``test_page_chat_action_requires_login`` —— ``action=chat`` 也必须登录（原断言 401）；
    * ``test_page_chat_action_still_works_when_logged_in`` —— 管理员走 chat 仍须能用（原断言 200）；
    * ``test_any_page_post_action_requires_login``（5 条参数化）—— 任意 action 都必须先登录。

    ----

    **历史说明（保留原文，因为它解释了为什么曾经要写四条用例）**

    守卫曾按**动作名白名单**来护：``app.py:index()`` 里
    ``_require_admin_for_page()`` 嵌在 ``if action in _SCAN_ACTIONS:``（只有
    ``{"scan"}``）内部，于是

    * ``action=scan`` 拦得住；
    * ``action=chat`` **裸奔** —— 它把消息交给
      ``agent/service.handle_agent_message()``，而 Agent 的 ``_tool_subdomain`` /
      ``_tool_httpx`` 直调 ``tool_runner.run_tools()`` 与 ``HttpxRunner().run_scan()``，
      **两者都不查 ``GEF_ALLOW_REAL_SCAN``，也不查 Scope**
      （``docs/AGENT_ASYNC_IMPACT.md`` I-5 的实测证据）。也就是说：一个未登录的
      HTTP 请求可以走通「意图 → 确认执行 → 真实扫描」，且不产生任何
      ``job.created`` 审计；
    * 未知 action 落到 ``else: job_error = "未知操作"`` 并返回 **200** —— 这正是
      「漏一个动作就漏一个洞」的形状，也是当时必须拿 ``unknown`` /
      ``definitely-not-an-action`` 当回归探针的原因：守卫只要被塞回白名单里，
      这两条取值立刻变红。

    当时的修法是把守卫**前置到 action 分发之前**，并用 5 条行为级参数化（其中 3 个
    是未知动作）钉住「动作名不是安全边界」。那种守卫是对的，但它守的仍是一个
    **存在的**页面接口。

    ----

    **现在这个洞整体不存在了，因为路由没了**

    测试阶段改造把 ``web/templates/index.html`` 删除、``index()`` 改成只有
    ``GET`` + ``redirect(url_for("scan_center"))``。于是 ``POST /`` 由 Flask 在
    **路由层**拒绝：该 URL 上没有 POST 处理器 → **405 Method Not Allowed**。

    * 既不是 200（未知动作再也回不出「未知操作」页面），
    * 也不是 401（不再有一条「先查登录态」的幽灵页面接口），
    * 而是「这条路根本不通」。

    这比前置守卫更彻底：守卫是**行为**正确（有人写漏就错），405 是**形状**不存在
    （没有分支可漏）。**但这条用例仍然必要**：它守的是「将来有人把 ``POST /``
    加回来」。加回来的那一刻，下面 5 个取值全部变红 —— 包括三个未知动作，
    所以「偷偷加个只认 scan 的分支」也藏不住。
    """
    for action in (
        "scan",  # 旧首页的扫描动作
        "chat",  # 旧首页的 Agent 动作（历史缺陷主角）
        "",  # 表单给了 action 但值为空
        "unknown",  # 从未登记的动作
        "definitely-not-an-action",  # 未来可能出现的新动作名
    ):
        resp = client.post("/", data={"action": action, "domain": "example.test"})
        assert resp.status_code == 405, (
            f"action={action!r} 拿到了 {resp.status_code}：POST / 上又有处理器了 —— "
            "旧首页的页面动作（含未登记动作）正在复活。"
            "405 才是「路由整体不存在」，200/401 都说明有人把它加了回来。"
        )
        assert resp.status_code not in (200, 401), (
            f"action={action!r} 回到了 {resp.status_code}："
            "200 = 未知动作又有处理路径，401 = 又成了一条待守卫的幽灵页面接口"
        )

    # ── 源码侧的守卫：上面钉的是行为，这里钉「后门形状不存在」 ──
    import ast
    import inspect

    source = inspect.getsource(app_module)
    for gone in (
        "_SCAN_ACTIONS",  # 动作白名单本身（历史缺陷的载体）
        "_require_admin_for_page",  # 按动作名护的页面守卫
    ):
        assert gone not in source, f"app.py 里又出现了已删除的首页动作载体: {gone}"

    # ``handle_agent_message`` 用**可执行引用**判定，而不是纯字符串命中：
    # ``app.py`` 的模块 docstring 故意留着这个名字，用来记录
    # 「``agent/`` 模块保留在仓库里、只是本轮不再提供旧首页的 Agent Chat UI」
    # 这条决策。字面命中会把那条注释也算成后门，属于锚在写法上的守卫；
    # 下面这行问的是「有没有代码真的在调它」—— 那才是页面级 Agent 入口。
    referenced = {
        node.id for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Name)
    } | {node.attr for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Attribute)}
    assert "handle_agent_message" not in referenced, (
        "app.py 里又有代码在调 handle_agent_message：页面级 Agent 入口正在复活"
    )
    assert "handle_agent_message" not in inspect.getsource(app_module.index), (
        "index() 里又接上了 Agent 对话"
    )

    # ``index()`` 只能有 GET：单独看这个视图函数自己的源码，不许出现 POST 方法
    # 声明，也不许出现「读表单 action」这一步（那是分发的第一句）。
    # ★ 刻意**不**把 ``request.form`` 的断言放到整个 ``app.py`` 上：登录页
    #   （``login_page``）合法地读 ``request.form["token"]``，整文件断言会误伤。
    # ★ 也**不**在整段函数源码上做字面命中：``index()`` 的 docstring 里**刻意**
    #   写着「没有 POST 分支」「``action=scan`` / ``action=chat`` 随之消失」这些
    #   口径说明 —— 字面命中会把注释算成后门（锚在写法上）。所以先用 ast 把
    #   docstring 摘掉，只对**代码体**做断言。
    assert 'methods=["POST"]' not in source, "app.py 里又出现了 POST 页面路由"

    index_source = inspect.getsource(app_module.index)
    index_node = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "index"
    )
    # ``ast.get_docstring`` 返回的是**去缩进**后的文本，而 ``inspect.getsource``
    # 里它是按原样缩进的 —— 直接 ``source.replace(docstring, "")`` 一次都换不掉
    # （实测：``index()`` 的 docstring 里写着「``action=scan`` 随之消失」这句口径
    # 说明，于是 ``"action"`` 字面命中，用例把注释当成了后门）。
    # 正确做法是从 AST 拿**去掉 docstring 之后的那些语句**的源码片段。
    body_statements = list(index_node.body)
    if body_statements and isinstance(body_statements[0], ast.Expr) and isinstance(
        body_statements[0].value, ast.Constant
    ):
        body_statements = body_statements[1:]
    index_body = "\n".join(
        ast.get_source_segment(source, statement) or "" for statement in body_statements
    )
    assert index_body.strip(), "index() 的代码体是空的：拿不到可断言的源码片段"
    for forbidden in (
        'methods=["POST"]',
        "request.form",
        "request.method",
        "render_template",
        "session",
        # 「action」是旧首页分发的第一句（`request.form.get("action", "scan")`）；
        # 这里不查裸字符串 "scan"：跳转目标 ``scan_center`` 本身含这个词。
        "action",
    ):
        assert forbidden not in index_body, (
            f"index() 的代码体里又出现了 {forbidden!r}：根路径重新变成一条有动作的页面路由了"
        )
    assert "redirect" in index_body and "scan_center" in index_body, (
        "index() 的代码体里没有跳转：它应当只有 return redirect(url_for('scan_center'))"
    )
    # 正向把「只有 GET」也钉住：将来把装饰器整行删掉（变成默认 GET）也算改口径。
    assert index_source.lstrip().startswith('@app.route("/", methods=["GET"])'), (
        f"index() 的路由声明变了：{index_source.splitlines()[0]!r}"
    )


def test_anonymous_homepage_does_not_leak_authorized_assets(admin_client, client):
    """根路径**跟随后**不得出现授权资产（名称 / 目标 / 资产卡片载体）。

    原名 ``test_anonymous_homepage_does_not_leak_authorized_assets``，当时钉的是
    「首页 ``build_page_context()`` 无条件执行 ``context["scopes"] = _load_scope_options()``，
    把『这份授权叫什么、覆盖哪些目标』整份摊给匿名访客」，并附一条反向断言
    「管理员首页照常有资产卡片」。

    ★ 口径变了，这条守的东西**没有作废，反而更强**：修的不再是「谁看得见」，
    而是**数据载体整体消失** ——

    * 首页模板已删除，``_load_scope_options()`` / ``build_page_context()`` 已删除，
      ``/scan-center`` 也**没有授权面板**（测试阶段改造删掉了它与步骤 2）；
    * 于是「资产卡片」``class="scope-asset`` 与下拉框 ``id="scope_id"`` 在**整站**
      都不再存在 —— 匿名看不到，管理员也看不到，因为没有这条渲染路径了。

    ★ 为什么反向断言从「管理员必须看到」改成「管理员也看不到」：旧写法对
    「把资产卡片整块删掉」会变红，于是它实际上在**要求**管理员页面上留着这个
    数据载体。口子已经从「渲染前过滤」退到「根本不渲染」，正确的反向守卫因此
    变成「全站都没有这个载体」—— 详见 ``test_m2_page_scan.py`` 里对
    ``index.html`` 不存在与 `app.py` 无 ``_load_scope_options`` 的源码守卫。

    ★ 跟随后端跳转（``follow_redirects=True``）才是有意义的观察面：``GET /``
    只是 302，它的响应体里当然不会有任何东西；真正会下发给使用者的页面是
    ``/scan-center``。这也顺带钉住「``GET /`` 不会自己渲染模板」。
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

    anon_home = client.get("/", follow_redirects=True).get_data(as_text=True)
    admin_home = admin_client.get("/", follow_redirects=True).get_data(as_text=True)

    for who, page in (("匿名", anon_home), ("管理员", admin_home)):
        for leaked in ("培正学院公网资产", "www.example.test"):
            assert leaked not in page, f"{who}根路径页面上出现了授权资产信息: {leaked}"
        # 卡片与下拉框这两个**数据载体**必须一个都不渲染 —— 它们是数据泄漏的形状。
        assert 'class="scope-asset' not in page, f"{who}根路径页面仍渲染授权资产卡片"
        assert 'id="scope_id"' not in page, f"{who}根路径页面仍渲染授权资产下拉框"
        assert 'name="scope_id"' not in page, f"{who}根路径页面仍能提交 scope_id"

    # 反向守卫（新口径）：管理员走的是扫描中心，而扫描中心**没有授权面板** ——
    # 这不是「管理员被误伤」，是那个面板本身已经退役。
    assert "已登录" in admin_home, "管理员跟随跳转后没落在扫描中心（应显示已登录）"
    assert "创建任务" in admin_home, "管理员扫描中心丢了主面板，上面几条「没泄漏」会变成空转"


def test_anonymous_homepage_does_not_leak_scan_summary(admin_client, client, store):
    """根路径**跟随后**不得下发扫描汇总与目标明细（结果库统计）。

    原名 ``test_anonymous_homepage_does_not_leak_scan_summary``：首页的「汇总」
    面板读 ``store.get_global_summary()`` / ``get_domain_summary()``，而当时
    ``app.py:build_page_context()`` 对这两个取数点**没有任何登录态判断**（与
    ``scopes`` / ``recent_jobs`` 两处的写法不一致），于是匿名访客能读到
    「跑过几次、覆盖几个目标、命中多少条」，``/?domain=<目标>`` 还会把目标名
    直接回显。

    ★ 现在修的是**数据载体整体消失**，不是「谁看得见」：

    * ``build_page_context()`` 与首页模板都已删除，``GET /`` 只跳转；
    * ``/scan-center`` 的骨架与统计一律**只在已登录时下发**，而它下发的任务列表
      里**不含**结果库汇总，也不认 ``?domain=`` 这个参数（它只认 ``?job_id=``）。

    因此这里断言：匿名与管理员跟随后，都**看不到**那三个汇总标签、``当前目标``
    区块、以及目标名。管理员侧**不再**断言「必须有汇总面板」—— 那个面板随首页
    一起退役了，继续要求它存在等于把已删除的渲染路径钉回来。
    """
    store.save_results(
        "leaktarget.test",
        "subfinder",
        ["a.leaktarget.test", "b.leaktarget.test", "c.leaktarget.test"],
    )

    for who, test_client in (("匿名", client), ("管理员", admin_client)):
        # ① 汇总面板的三个标签属于**数据载体**，两边都不该出现。
        page = test_client.get("/", follow_redirects=True).get_data(as_text=True)
        for label in ("扫描运行次数", "已有目标数", "结果总数"):
            assert label not in page, f"{who}根路径页面仍然渲染汇总数字: {label}"

        # ② 带上 domain 也不行：目标名与「当前目标」区块都必须消失。
        focused = test_client.get("/?domain=leaktarget.test", follow_redirects=True).get_data(
            as_text=True
        )
        assert "leaktarget.test" not in focused, f"{who}根路径通过 ?domain= 回显了目标名"
        assert "当前目标" not in focused, f"{who}根路径仍然渲染目标明细区块"

    # ③ 反向守卫：管理员跟随后确实落在扫描中心（而不是一个看不见内容的空响应）——
    #    否则上面那几条「没泄漏」可能只是因为整页 500 / 空白，属于假通过。
    admin_home = admin_client.get("/", follow_redirects=True).get_data(as_text=True)
    assert "扫描中心" in admin_home, "管理员跟随跳转后没落在扫描中心，上面的断言可能是空转"
    assert "未登录" not in admin_home, "管理员落在扫描中心却显示未登录"


#: 旧 ``build_page_context()`` 里按登录态过滤的 5 个 session 键（§3.16.5 第 1 条，
#: 用户在 §3.16.8 第 2 问上确认「一并过滤」）。该函数现已**整体删除**，
#: 清单保留在这里作为「哪些键曾经是下发对象」的历史记录与黑名单来源。
AGENT_SESSION_KEYS = [
    "agent_history",
    "agent_steps",
    "pending_plan",
    "uploaded_targets",
    "agent_context",
]


def test_no_page_context_hands_session_content_to_templates(app_module):
    """``app.py`` 里**不再有**把 Session 内容下发给模板的代码。

    原名 ``test_agent_session_keys_are_not_handed_to_anonymous_visitors``：
    当时给 ``app.build_page_context`` 装探针，断言匿名拿到空的 5 个键、管理员
    拿到有内容的 5 个键。``build_page_context()`` 本轮已**整体删除**，探针没有了
    可挂的对象 —— 于是这条改用**不存在性**守卫。

    ----

    **历史说明（保留，因为它解释了这 5 个键当年为什么值得专门写一条用例）**

    这 5 个键装的是 Agent 对话历史 / 待确认计划 / 上传目标 / 上下文：

    ``agent_history`` · ``agent_steps`` · ``pending_plan`` · ``uploaded_targets`` ·
    ``agent_context``

    它们此前**不构成泄漏**：``web/`` 下对这 5 个名字的引用是 0 命中（既没有渲染点、
    也没有 JS 读它们）。但「不下发」比「不渲染」可靠 —— 少下发一个键，就少一次
    「将来加了个渲染点、忘了它没过滤」的机会。当时的处置是**按
    ``is_authenticated`` 过滤**：匿名下发空值、管理员下发真值。

    ★ 那条用例当时为什么必须自带内容、必须装探针：这 5 个键没有渲染点，所以
    「页面上看不到」在改动前后都成立，等于什么都没锁；能观察到的差别只有**上下文里
    这个键的值**。而匿名侧必须先用 ``session_transaction()`` 塞满内容，否则
    「匿名上下文里没有 agent_history」永远是空转 —— ``core/auth.logout()`` 只
    ``pop("local_admin")``、**不清**这些键，所以「登录过 → 退出 → 仍带着那份
    cookie 浏览」的访客会话里真的有内容，而请求本身是匿名的。

    ----

    **现在：连下发通道都没有了，比过滤更彻底**

    过滤是「下发前问一句登录态」——它仍然依赖**每一个取数点都记得问**
    （这正是历史上 ``scopes`` 问了、``get_global_summary()`` 三处没问的原因）。
    现在首页模板与 ``build_page_context()`` 一起删除，Session 内容不再有任何
    通往模板的路径：没有键、没有探针、没有「忘了过滤」的位置。

    ★ 反向守卫的替代：这条用例从「匿名必须空」变成「通道不存在」，因此还需要
    另一侧的守卫兜住「页面本身别漏」。那一侧由
    ``test_public_scan_mode.py::test_scan_center_page_renders_for_anonymous``
    与 ``::test_anonymous_scan_center_does_not_leak_backend_jargon`` 承担 ——
    匿名 ``/scan-center`` 返回 200 但**不下发任何骨架元素**（``#strategy-list``
    等一个都不出现），只给一句「未登录时相关接口返回 401 unauthenticated」。
    两条一起才完整：**没有下发通道** + **唯一主页面默认失败关闭**。
    """
    import inspect

    # ① 那台「把上下文喂给模板」的机器已经不存在了。用属性断言而不是源码字符串：
    #    源码里可能残留注释提到这个名字，属性不存在才是真的不可调用。
    assert not hasattr(app_module, "build_page_context"), (
        "app.py 里又有 build_page_context 了：Session 内容重新有了一条通往模板的下发通道"
    )

    source = inspect.getsource(app_module)
    # ② 它的 5 个键与取 ``session`` 的写法一个都不许出现。
    for forbidden in ("session.get(", *AGENT_SESSION_KEYS):
        assert forbidden not in source, (
            f"app.py 里又出现了 {forbidden!r}：页面上下文下发通道正在重建"
        )
