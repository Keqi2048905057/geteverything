"""旧首页退役后的守卫：扫描后门不复存在，但安全边界一条都没少。

**本文件原来测什么（历史）**：M2 起，``web/templates/index.html`` 的表单扫描
（``POST /`` + ``action=scan``）与 API 走同一套 Scope 校验 —— 未登录 401、没有
Scope 时给出创建指引、目标越界时页面回显「Scope 校验失败」、授权资产渲染成人读
卡片、实体 ID 不进可见文案……

**现在测什么（测试阶段改造，不要改回去）**：旧首页连同 ``POST /`` 的全部动作已被
整体删除，上面那些断言对象**一个都不存在了**（模板没了、路由没了、授权面板没了）。
本文件因此改写成「退役守卫」，而不是删掉断言 —— 逐条对应关系如下：

====================  ==========================================================
原守卫                现守卫
====================  ==========================================================
未登录 POST / → 401   ``POST /`` 任意 action 一律 **405**，且**不创建任何任务**
首页渲染/表单可用      ``web/templates/index.html`` **文件不存在**
（模板源码守卫）       ``app.py`` 里不再承载任何创建任务的编排
扫描成功后回显         ``GET /`` 只跳转（302 → ``/scan-center``），不渲染
Scope 越界 → 页面回显  Scope 校验**没有消失**：``/api/public-jobs`` 越界仍 **403**
====================  ==========================================================

★ 一条总原则：**旧首页退役 ≠ 安全边界退役**。上一轮删掉的是「页面承载扫描动作」
这条路，不是 Scope / 排除列表 / SSRF / 限速这些判定本身。因此本文件既钉「后门
不存在」，也钉「判定还在跑」—— 少了后半边，删代码就能让一套守卫集体变绿。

★ 本文件不发起任何真实扫描：默认目标域用 RFC 6761 保留域 ``example.test``，
提交一律走 ``mode=mock``。
"""

import inspect
from pathlib import Path

from core import jobs as jobs_store

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: 旧首页表单能提交的全部 action 取值（含空串、未登记动作、未来动作名）。
RETIRED_ACTIONS = [
    "scan",  # 旧首页的扫描动作
    "chat",  # 旧首页的 Agent 动作
    "",  # 表单给了 action 但值为空
    "unknown",  # 从未登记的动作
    "definitely-not-an-action",  # 未来可能出现的新动作名
]


def _create_scope_via_api(admin_client, **overrides):
    """建一个授权范围（只走 API —— 页面侧已经没有任何创建入口）。"""
    payload = {"name": "首页测试范围", "allowed_domains": ["example.test"], "active_scan": False}
    payload.update(overrides)
    resp = admin_client.post("/api/scopes", json=payload)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["scope"]["id"]


# ── 守卫 1：POST / 不再存在，且不产生任何副作用 ─────────────


def test_retired_page_actions_are_405_and_create_no_job(client, admin_client):
    """``POST /`` 对任意 action 一律 405，且**一个任务都不许被创建**。

    旧首页的扫描动作是「页面表单 → 直接创建任务」的一条编排。删掉模板之后，
    这条编排也必须一起没有：

    * **405** 说明该 URL 上没有 POST 处理器（Flask 路由层拒绝），既不是
      「200 + 未知操作」也不是「401 + 先查登录态」；
    * 但「405」本身只证明状态码，不证明没有副作用 —— 万一有人把创建逻辑
      挂在 ``before_request`` 或错误处理器里，405 照样返回而任务已经落库。
      所以这里**同时**断言任务表为空：**匿名与管理员两条路都试一遍**。

    ★ 为什么管理员也要试：旧代码里真正会创建任务的是**已登录**那一路
    （匿名被守卫拦住）。只测匿名等于只测「拦得住」，测不出「路还在」。
    """
    assert jobs_store.list_jobs() == [], "前置条件：本用例开始前不应有任何任务"

    for who, test_client in (("匿名", client), ("管理员", admin_client)):
        for action in RETIRED_ACTIONS:
            resp = test_client.post(
                "/", data={"action": action, "domain": "example.test", "targets": "example.test"}
            )
            assert resp.status_code == 405, (
                f"{who} POST / action={action!r} 返回 {resp.status_code}："
                "旧首页的页面动作复活了（405 = 路由整体不存在）"
            )
            assert resp.status_code not in (200, 401), (
                f"{who} action={action!r} 回到 {resp.status_code}："
                "200 = 页面动作有处理路径，401 = 又成了一条待守卫的幽灵接口"
            )

    # 副作用守卫：上面 10 次提交没有留下任何任务。
    assert jobs_store.list_jobs() == [], "POST / 虽然返回 405，却创建了任务"
    # 再从 HTTP 侧确认一次（管理员才有权读；读的就是同一张表，防止夹具口径差异）。
    counts = admin_client.get("/api/jobs").get_json()["counts"]
    assert counts["total"] == 0, f"POST / 之后任务计数不为 0: {counts}"


# ── 守卫 2：模板文件不存在（删模板与删路由必须同时做） ──────


def test_home_page_template_file_is_gone():
    """``web/templates/index.html`` 必须**不存在**。

    ★ 为什么模板与路由必须一起删：只删路由、留着模板 → 文件没人引用，下一个人
    会以为「首页还在，只是暂时没挂上去」，很容易顺手接回来；只删模板、留着
    ``POST /`` → 那是一个**幽灵路由**：路由表里看得见、点进去 ``TemplateNotFound``
    500，更糟的是它可能已经被前端或脚本当成接口在用。两者同时删，才没有中间态。

    ★ 模板目录里应只剩下当前在用的页面：``scan_center.html`` / ``assets.html`` /
    ``login.html``。这条顺带钉住「不要偷偷加回一个 index.html」。
    """
    templates = PROJECT_ROOT / "web" / "templates"
    assert not (templates / "index.html").exists(), (
        "web/templates/index.html 又出现了：旧首页模板正在复活（它与 POST / 是一对）"
    )

    names = sorted(path.name for path in templates.glob("*.html"))
    assert "index.html" not in names, f"模板目录里又出现了 index.html: {names}"
    for expected in ("scan_center.html", "assets.html", "login.html"):
        assert expected in names, f"当前在用的模板丢了: {expected}（现有 {names}）"


# ── 守卫 3：app.py 不再承载任何「页面创建任务」的编排 ────────


def test_app_module_has_no_page_level_job_orchestration(app_module):
    """``app.py`` 里不再有创建任务的编排：任务创建的唯一入口是 ``/api/…``。

    删的是**这条编排**，不是能力本身：

    * ``create_scan_job`` / ``create_open_public_job`` 这类真正建任务的服务
      必须待在 ``core.application``，由 ``api/`` 层的视图调用
      （``test_public_scan_mode.py::test_public_job_orchestration_is_in_application_service``
      与 ``::test_public_job_api_never_calls_runners_directly`` 守着这条边界）；
    * ``app.py`` 一旦出现 ``from api.jobs import``，说明页面层又开始直接串联
      任务创建 —— 那正是旧首页表单扫描的形状。

    ★ 为什么同时钉 ``build_page_context`` 与 ``_load_scope_options``：它们是旧
    首页的上下文装配函数。留下函数、不留调用点，是「幽灵路由」的表亲：下一个人
    看到函数还在，会以为有一条被临时关掉的页面路径。
    """
    source = inspect.getsource(app_module)

    for forbidden in (
        "create_scan_job",  # 任务创建服务（必须待在 core.application）
        "build_page_context",  # 旧首页的上下文装配
        "_load_scope_options",  # 旧首页的授权资产取数
        "from api.jobs import",  # 页面层直接串联任务 API
    ):
        assert forbidden not in source, (
            f"app.py 里又出现了 {forbidden!r}：页面层重新承载了创建任务的编排"
        )


# ── 守卫 4：GET / 只跳转、不渲染 ────────────────────────────


def test_root_path_only_redirects(client, admin_client):
    """``GET /`` 只做 302 → ``/scan-center``，不渲染任何模板。

    ★ 对匿名与管理员都跑一遍：旧首页是**按登录态渲染两份不同内容**的页面，
    如果只测匿名，一个「管理员才渲染」的残留分支会完全看不见。

    ★ 断言 302 而不是「跟随后 200」：跟着跳转的话，这条对「根路径自己渲染一个
    空壳首页」也会通过 —— 那就等于没锁住退役。

    ★ 正文断言的是「**没有渲染本站模板**」，不是「正文为空」：Flask 的
    ``redirect()`` 自带一小段 ``Redirecting...`` 桩页面（``Content-Type: text/html``），
    拿 ``resp.get_data().strip() == ""`` 去卡会把 Flask 的默认行为当成违规。
    真正要钉的是：这份正文里不得出现本站页面的任何载体（旧首页的「Agent 对话」、
    「创建扫描任务」、以及 ``scan_center.html`` 的步骤标题）。
    """
    for who, test_client in (("匿名", client), ("管理员", admin_client)):
        resp = test_client.get("/")
        assert resp.status_code == 302, f"{who} GET / 返回 {resp.status_code}：根路径又在渲染了"
        location = resp.headers["Location"]
        assert location.endswith("/scan-center"), f"{who} GET / 跳到 {location!r}，不是扫描中心"
        body = resp.get_data(as_text=True)
        for marker in ("Agent 对话", "创建扫描任务", "确认目标", "选择工具", "登录"):
            assert marker not in body, f"{who} 302 响应里出现了本站页面载体: {marker!r}"


# ── 守卫 5：Scope 校验没有随首页一起退役 ────────────────────


def test_scope_validation_survives_the_page_retirement(admin_client):
    """旧首页退役 ≠ 安全边界退役：越界目标依然 403，且不落任务。

    旧文件用「页面回显 Scope 校验失败」来证明越界被拦。页面没了，但**判定还在**
    —— 而且判定本来就不该住在页面里。这条改用唯一的任务创建入口
    ``POST /api/public-jobs`` 复验：

    * 只允许 ``example.test`` 的 Scope + 目标 ``evil.test`` → **403
      ``scope_violation``**；
    * 任务表必须仍然为空（拒绝要发生在创建之前，而不是创建后回滚）；
    * **反向对照**：同一个 Scope 下的 ``example.test`` 用 ``mock`` 模式提交 → 202
      且任务确实落库。少了这一半，「把所有请求都拒掉」也会让上面两条变绿。

    ★ 这条用例是本文件的**锚**：其余四条钉「什么东西没了」，只有它钉「什么东西
    必须有」。删代码的人能让前者全绿，但删不动后者。
    """
    assert jobs_store.list_jobs() == []
    scope_id = _create_scope_via_api(admin_client, allowed_domains=["example.test"])

    # ① 越界：403，且不创建任务。
    resp = admin_client.post(
        "/api/public-jobs",
        json={"scope_id": scope_id, "targets": ["evil.test"]},
    )
    assert resp.status_code == 403, (
        f"越界目标没有被拦：{resp.status_code} —— 首页退役时把 Scope 校验一起带走了？"
    )
    assert resp.get_json()["error_code"] == "scope_violation"
    assert jobs_store.list_jobs() == [], "越界请求被拒了，却仍然创建了任务"

    # ② 反向对照：范围内目标可以提交，任务真的落库（拒绝不是「一概拒绝」）。
    ok = admin_client.post(
        "/api/public-jobs",
        json={"scope_id": scope_id, "targets": ["example.test"], "mode": "mock"},
    )
    assert ok.status_code == 202, f"范围内目标被误伤：{ok.status_code} {ok.get_json()}"
    created = jobs_store.list_jobs()
    assert len(created) == 1, created
    assert created[0]["status"] == "queued"
    assert created[0]["mode"] == "mock"
    assert created[0]["targets"] == ["example.test"]
