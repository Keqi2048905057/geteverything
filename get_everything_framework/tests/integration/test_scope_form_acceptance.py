"""扫描中心「授权范围表单」的守卫（原 ``#scope-form`` novalidate 验收，2026-10-04）。

本文件的前身钉的是 ``#scope-form`` 的 ``novalidate`` 与 JS 错误路径。本轮测试阶段
改造把它整份搬到了新口径上：``#scope-form`` 已经**从页面上删掉**，但那些坑与实测
证据作为历史记录留在下面 —— 它们是「为什么这些元素不许复活」的理由。

## 一、历史缺陷：两个 required 下拉把第一个授权范围挡在门外

``#scope-form`` 里**同时**装着两个 ``required`` 下拉（``#job-project`` /
``#job-scope``）与「添加授权范围」的 ``type=submit`` 按钮。原生约束校验跑在
``submit`` 事件**之前**，于是全新用户（两下拉都只有占位项）点按钮时
``valueMissing=true``，浏览器直接拦下 —— ``scan_center.js:bindScopeForm()`` 的
submit 回调**一次都不执行**，**第一个授权范围在界面上根本建不出来**。

实测（headless Chrome，三状态，从**真实渲染**的 ``/scan-center`` 里原样截取该表单）：

==========  ============  ==============  ==================================
状态        submit 触发   checkValidity   ``:invalid``
==========  ============  ==============  ==================================
1（全空）   0 次          False           ``#job-project``、``#job-scope``
2（有项目） 0 次          False           ``#job-scope``
3（有范围） 1 次          True            （无）
==========  ============  ==============  ==================================

状态 1 正是「全新用户第一次进来」—— 也就是最需要这个按钮的那一次。当时的修法是给
表单加 ``novalidate``：只关掉**浏览器原生**校验，不动服务端。

**表单整体删除之后，这个坑也就随之不存在了** —— 没有表单，就没有「原生校验先于
submit 事件」这个先后顺序。但守卫**不能因此消失**：将来若有人把授权范围表单加回来，
同一个坑会立刻原样重现（``required`` 下拉 + ``type=submit`` 按钮这个组合本身就带着
这个陷阱）。所以口径从「这个表单带了 ``novalidate`` 吗」改成**正向断言它不复活**：
元素与绑定函数一个都不许回来。见 ``test_scope_form_does_not_exist_anymore``。

## 二、随表单一起退役的验收 ① ②，以及仍然有效的服务端 ③ ④

四条原验收里，前两条的被测对象**整体或部分**是那个表单：

* **① 空表单点击 → JS 给出明确错误**：对象是 ``#scope-form`` 的 submit 回调与
  ``#scope-feedback``，随表单一起消失；
* **② 非法目标 → JS 转发 / 服务端拒绝**：它当时刻意分成两层测 ——
  「JS 不吞掉拒绝理由」那一半消失，「服务端真的拒绝」那一半保留在 ④ 里。

授权范围的**界面入口**删了，``/api/scopes`` 与 ``/api/projects`` 这两组接口本身
**一条都没删**（显式传 ``scope_id`` 时白名单校验照旧生效），因此后两条原封不动：

=====  ==========================================  ============================
条     验的是什么                                  在哪一层测（为什么）
=====  ==========================================  ============================
③      合法目标 → 正常提交（建库 + 关联到项目）         服务端两步调用链
④      绕过 JS 直接调 API → 服务端仍然拒绝             Flask（含「一个 Scope 都没建」）
=====  ==========================================  ============================

**「前端不是安全边界」这条约定没有随表单一起退役**：界面上的创建入口没了，接口仍在，
所以 ④ 那条守卫反而更值得留着 —— 现在它是这组接口**唯一**的验收人
（原来还有 ② 的 JS 那一半在侧面看着，现在没有前端代码能分担任何东西了）。

**本文件不发起任何真实外部扫描**，不发网络请求，不用仓库里的任何数据库
（``admin_client`` 夹具已把两个库与三处运行期目录钉到 ``tmp_path``）。

## 三、关于本文件里的浏览器用例（已移除，理由在此）

前身还有一条 ``@pytest.mark.skipif(_find_chrome() is None, ...)`` 的真实浏览器用例，
在无 Chrome 的 CI 上**恒为 skipped**。它的被测对象（``#scope-form`` 的
``novalidate`` 与 submit 触发次数）已随表单消失，本机有 Chrome 时它会**执行并失败**
（不是跳过），所以它不能以原样保留。这里把它改写成**不依赖 Chrome 的源码级守卫**
（``test_scan_center_js_wires_the_three_steps``），于是：

* 本机（有 Chrome）skipped 计数不变（0 → 0），因为原来它就是真跑的；
* 无 Chrome 的环境上本文件少 1 条 skipped —— 这不是偷偷塞一条 skip 凑数，而是
  **把一条恒跳过的用例换成恒执行的用例**；这正是 ``AGENTS.md`` 说的
  「``passed`` 与 ``collected`` 必须一起看」那个信号在往好的方向动。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCAN_CENTER_JS = PROJECT_ROOT / "web" / "static" / "scan_center.js"
SCAN_CENTER_HTML = PROJECT_ROOT / "web" / "templates" / "scan_center.html"

#: 前身那两条 JS 文案（``scan_center.js:bindScopeForm``）。表单删了，文案也不会
#: 再出现 —— 保留在这里是为了让上面的历史章节与代码里的字符串一一对得上，
#: 后来者读到时不必去翻 Git 历史。
MSG_NO_PROJECT = "还没有授权项目，请先在上面的表单里创建一个。"
MSG_NO_TARGETS = "至少填一个授权域名或授权网段。"

#: ④ 的四组非法输入 → 服务端**实测**的 error_code（探针：
#: ``%TEMP%\\gef_novalidate_server.py``，逐条量过状态码与新建 Scope 数）。
#:
#: 写成 `(payload, error_code, 用例 id)` 的**朴素元组**再包成 `pytest.param`：
#: `pytest.param(...)` 返回的是 `ParameterSet`，直接 `for a, b in PARAMS` 会抛
#: `ValueError: too many values to unpack`（要取 `.values`）。下面那条
#: 「四组理由互不相同」的用例也需要裸数据，所以真相只有这一份。
ILLEGAL_SCOPE_CASES = [
    ({"name": "授权范围"}, "bad_request", "empty-allow-rules"),
    ({"name": "授权范围", "allowed_domains": ["*"]}, "invalid_target", "allow-all-wildcard"),
    (
        {"name": "授权范围", "allowed_domains": ["not a domain"]},
        "invalid_target",
        "domain-with-space",
    ),
    ({"name": "授权范围", "allowed_cidrs": ["0.0.0.0/0"]}, "invalid_target", "unbounded-cidr"),
]

ILLEGAL_SCOPE_PAYLOADS = [pytest.param(payload, code, id=case_id) for payload, code, case_id in ILLEGAL_SCOPE_CASES]


# ── ③ / ④：Flask 层（接口仍在，照常跑） ────────────────────────


def test_legitimate_scope_submits_and_attaches_to_project(admin_client):
    """③ 合法目标 → 正常提交：Scope 建出来、**并真的挂到了项目下**。

    这条走的是 ``scan_center.js:bindScopeForm`` 当初提交时的**同一串两步调用**：

    ```text
    POST /api/scopes                       → 201，拿到 scope_id
    POST /api/projects/<id>/scopes         → 201，把 scope_id 关联到项目
    ```

    只断言第一步 201 是不够的 —— 那证明不了「范围真的进了某个项目」。
    第二步失败时范围就成了**孤儿范围**：范围存在、却不出现在任何项目里，
    正是 §3.17.1 那个死胡同的另一种形态（界面上有过提示「范围已创建，但关联到
    项目失败」）。所以这里三处都断言：创建 201、关联 201、以及列表里真的看得见。

    ★ 界面入口本轮删掉了（扫描中心不再有范围表单），但这两个接口仍然对外可用：
    显式传 ``scope_id`` 的白名单校验照旧生效。这条用例现在守的是「接口本身没有
    被误删、关联语义没有退化」，与界面上有没有那个表单无关。
    """
    project = admin_client.post(
        "/api/projects",
        json={
            "name": "培正学院授权测试",
            "authorization_note": "2026-10-02 校方信息中心书面授权，仅被动信息收集",
            "owner": "张三",
        },
    )
    assert project.status_code == 201, project.get_json()
    project_id = project.get_json()["project"]["id"]
    assert project.get_json()["project"]["scope_ids"] == []

    created = admin_client.post(
        "/api/scopes",
        json={
            "name": "培正学院授权测试 · www.example.test",
            "allowed_domains": ["www.example.test"],
            "allowed_cidrs": [],
            "active_scan": True,
        },
    )
    assert created.status_code == 201, created.get_json()
    scope = created.get_json()["scope"]
    assert scope["id"].startswith("scope_")
    assert scope["allowed_domains"] == ["www.example.test"]

    attached = admin_client.post(f"/api/projects/{project_id}/scopes", json={"scope_id": scope["id"]})
    assert attached.status_code == 201, attached.get_json()
    assert attached.get_json()["project"]["scope_ids"] == [scope["id"]]

    # 项目里必须能查到它，范围必须还在列表里：关联不是单方面的写入。
    detail = admin_client.get(f"/api/projects/{project_id}").get_json()["project"]
    assert detail["scope_ids"] == [scope["id"]]
    listing = admin_client.get("/api/scopes").get_json()["scopes"]
    assert [item["id"] for item in listing] == [scope["id"]]
    assert listing[0]["allowed_domains"] == ["www.example.test"]


@pytest.mark.parametrize("payload,expected_code", ILLEGAL_SCOPE_PAYLOADS)
def test_direct_api_bypass_is_still_rejected_by_the_server(admin_client, payload, expected_code):
    """④ 绕过前端直接打 ``POST /api/scopes`` → 服务端仍然拒绝，且**一个 Scope 都没建**。

    前身这条守的是「``novalidate`` 关掉的只是浏览器原生校验」，所以故意不经过
    ``bindScopeForm``、不经过任何前端代码，直接发请求。界面上的创建入口本轮删掉
    之后，这条守卫**更重要了**：它现在是这组接口唯一还在的验收人 ——
    没有前端表单能替它挡下任何东西，能不能拒绝完全取决于服务端。

    ★ 关键断言是**最后那句「库里的 Scope 数没变」**，不是状态码。
    只看 400 的话，「先建了再报错」也能通过；数一遍才排得掉「先写入、后报错」
    这类形状（``api/scopes.py`` 是在 ``scope_store.create()`` **抛异常**时中止的，
    所以本来就不该有半条记录 —— 这里把它钉住）。

    ★ 四组输入覆盖**两条不同的拒绝路径**、**四条不同的拒绝理由**，不是同一句的重复：
    空 allow 规则走 API 层 ``_payload_to_scope_kwargs``（``bad_request``），
    其余三组走模型层 ``core/scope.py`` 的 ``Scope.__post_init__``（``invalid_target``）——
    而模型层这三组的文案**彼此也不同**（通配符 / 非法域名 / 放行网段各一句）。
    只拦一层、忘了另一层，立刻变红。下面那条
    ``test_illegal_scope_payloads_are_rejected_for_four_distinct_reasons``
    把「理由彼此不同」也变成机器判据，不靠这段散文。
    """
    from core import scope_store

    before = len(scope_store.list_all(limit=500))
    resp = admin_client.post("/api/scopes", json=payload)
    after = len(scope_store.list_all(limit=500))

    assert resp.status_code == 400, resp.get_json()
    body = resp.get_json()
    assert body["ok"] is False
    assert body["error_code"] == expected_code, body
    assert body.get("error_message"), "拒绝理由不能是空的 —— 界面要把这句显示给用户"
    assert after == before, f"被拒绝的输入竟然建出了 {after - before} 个 Scope：{payload}"


def test_illegal_scope_payloads_are_rejected_for_four_distinct_reasons(admin_client):
    """④ 的四组非法输入**不是同一句错误的四份拷贝** —— 逐条比对实际文案。

    参数化用例只钉 ``error_code``，而 ``invalid_target`` 一个码对应三组输入。
    万一模型层被改成「无论什么非法输入都回同一句」，那三组会**照样全绿**，
    参数化也就退化成了「同一件事测三遍」。这条把「理由确实不同」变成判据：

    * 四条输入 → **四条互不相同**的 ``error_message``（空 allow 规则 / 通配符 /
      非法域名 / 放行网段 各一句）；
    * 其中模型层的三组 → ``invalid_target``；API 层那组 → ``bad_request``。

    ★ 只断言「互不相同」是不够的：把四条全换成乱码也能满足。所以同时钉住
    「**关键短语**在里面」—— 那是用户后来在界面上会读到的东西，
    文案被改写得更好可以，但「通配符」「网段」这两类理由不能悄悄消失。
    """
    seen: dict[str, str] = {}
    for payload, _code, _case_id in ILLEGAL_SCOPE_CASES:
        resp = admin_client.post("/api/scopes", json=payload)
        assert resp.status_code == 400, (payload, resp.get_json())
        seen[repr(payload)] = resp.get_json()["error_message"]

    messages = list(seen.values())
    assert len(set(messages)) == len(messages), f"有两组输入给出了同一句理由：{seen}"

    wildcard = [v for k, v in seen.items() if "'*'" in k]
    cidr = [v for k, v in seen.items() if "0.0.0.0/0" in k]
    empty = [v for k, v in seen.items() if "allowed_domains" not in k and "allowed_cidrs" not in k]
    assert wildcard and "通配符" in wildcard[0], seen
    assert cidr and "网段" in cidr[0], seen
    assert empty and "allowed_domains" in empty[0], seen


def test_direct_api_bypass_is_still_rejected_for_anonymous(client, admin_client):
    """④（续）绕过前端**并且**绕过登录：匿名直接打 ``POST /api/scopes`` → 401。

    「绕过前端」有两种绕法：绕过前端的校验，和绕过前端的登录态。前者靠服务端
    校验兜住（上一条），后者靠 ``require_admin()`` 兜住 —— ``api/scopes.py:70``
    就在函数体第一行。这里两条都验，否则「绕过前端」这个说法只被验了一半。

    ★ 匿名那条请求的 payload 是**完全合法**的：它证明拦住它的是登录态，
    而不是目标非法 —— 一个非法 payload 拿到 401 说明不了任何事。
    """
    from core import scope_store

    before = len(scope_store.list_all(limit=500))
    resp = client.post(
        "/api/scopes",
        json={"name": "匿名范围", "allowed_domains": ["www.example.test"], "active_scan": True},
    )
    after = len(scope_store.list_all(limit=500))

    assert resp.status_code == 401, resp.get_json()
    assert resp.get_json()["error_code"] == "unauthenticated"
    assert after == before, "匿名请求竟然写进了 Scope"

    # 反向：同一个 payload 换成管理员就必须成功 —— 否则这条用例对
    # 「把整个接口删掉」也会通过。
    ok = admin_client.post(
        "/api/scopes",
        json={"name": "匿名范围", "allowed_domains": ["www.example.test"], "active_scan": True},
    )
    assert ok.status_code == 201, ok.get_json()


# ── 表单整体退役：正向断言它不复活 ──────────────────────────────


def test_scope_form_does_not_exist_anymore():
    """``#scope-form`` 一族元素与它们的 JS 绑定整体消失，且**不许复活**。

    这条替换了前身的 ``test_scope_form_js_error_paths_are_wired``（它断言两条
    JS 错误文案仍写在 ``setText("scope-feedback", …)`` 上、且 ``#scope-form`` 带
    ``novalidate``）。被测对象随表单一起删除了，但守卫强度不能降级 —— 口径改成
    「这些元素与函数一个都不许回来」。

    **为什么必须留着这条**：那个坑不在「某个属性写错了」，而在**元素组合**本身 ——
    ``#scope-form`` 一旦同时挂着 ``required`` 下拉（``#job-project`` / ``#job-scope``）
    与 ``type=submit`` 按钮，原生约束校验就会跑在 ``submit`` 事件之前，全新用户点
    按钮时 ``valueMissing=true``，回调一次都不执行（模块 docstring 里有三状态实测表：
    状态 1 / 2 的 submit 触发次数都是 **0**）。谁把表单加回来，谁就**原样**继承这个坑；
    那一刻红的应当是这条测试，而不是某个用户「点了按钮没有任何反应」。

    两头都钉：模板里没有那些 ``id``，脚本里没有那些绑定函数 —— 只删 HTML 而留着
    ``bindScopeForm()``（或反过来）都只是半成品，所以两边都要断言。
    """
    template = SCAN_CENTER_HTML.read_text(encoding="utf-8")
    script = SCAN_CENTER_JS.read_text(encoding="utf-8")

    # 模板侧：表单、它的两个 required 下拉、反馈元素、项目清单都不在。
    for element_id in (
        "scope-form",
        "project-form",
        "scope-add",
        "scope-domains",
        "scope-cidrs",
        "scope-active",
        "job-project",
        "job-scope",
        "job-consent",
        "scope-feedback",
        "project-list",
    ):
        assert f'id="{element_id}"' not in template, (
            f"扫描中心模板里又出现了 #{element_id} —— 授权范围表单/项目面板复活了；"
            "若确实要加回来，必须同时补上 novalidate 与 §3.17.1 那三状态实测（模块 docstring）"
        )

    # 连带 ``consent-*`` 这类前缀命名的元素（勾选框在旧版是按前缀取的）。
    assert not re.search(r'id="consent-', template), "同意勾选框（consent-*）又回到了模板里"

    # 前身那条 JS 文案连同它们要写入的元素一起消失：表单没了，文案留着只会是死代码。
    for message in (MSG_NO_PROJECT, MSG_NO_TARGETS):
        assert message not in script, f"授权范围表单的错误文案又回到了 scan_center.js：{message}"

    # 脚本侧：绑定函数与它们的辅助函数一个都不许回来。
    # 用**词边界**匹配（而不是 `in`），这样连注释里提到这些名字都会被抓到 ——
    # 注释里出现它们，说明有人正在把这条流程往回搬。
    for name in (
        "bindScopeForm",
        "bindProjectForm",
        "bindProjectSelectSync",
        "prefillScopeTargets",
        "deriveScopeName",
        "syncJobScopes",
        "fillProjectSelects",
        "renderProjects",
    ):
        assert not re.search(rf"\b{name}\b", script), (
            f"scan_center.js 里又出现了 {name} —— 授权范围表单的绑定复活了"
        )


def test_scan_center_js_wires_the_three_steps():
    """扫描中心三步各自的绑定必须**都在** —— 缺一个，那一步就是死的。

    这条替换了前身那条真实浏览器用例（``test_scope_form_novalidate_acceptance_in_real_browser``）。
    原来它靠 headless Chrome 观察「submit 触发次数」，用来证明 ``novalidate`` 真的
    让回调跑起来了；现在被测对象（``#scope-form``）不存在了，那条用例在本机有 Chrome 时
    会**执行并失败**，所以改写成不依赖浏览器的源码级契约（见模块 docstring 第三节）。

    **为什么不能只删不补**：那条浏览器用例是当时唯一能证明「回调真的会被执行」的
    守卫。删掉它之后，必须留一条能证明「绑定确实接上了」的替代品，否则三步表单可以
    整体退化成一堆没接线的元素，而所有测试照绿 —— 用户看到的是「点了没反应」。

    它是什么、不是什么：

    * **是**：``DOMContentLoaded`` 回调里确实调用了四个绑定函数，且四个都在文件里
      有定义（只调未定义、或只定义未调用，都会红）；
    * **不是**：行为级验证。真实浏览器里跑一遍（原生校验与事件顺序、``fetch`` 真的
      发出去）属于浏览器层，本仓库目前没有常驻的浏览器用例 —— 这里明确说明，
      避免后来者把它当成「前端行为已经被端到端验证过了」。
    """
    script = SCAN_CENTER_JS.read_text(encoding="utf-8")

    match = re.search(r'document\.addEventListener\("DOMContentLoaded",\s*function\s*\(\)\s*\{(.*?)\n  \}\);', script, re.S)
    assert match, "scan_center.js 里找不到 DOMContentLoaded 的初始化回调"
    block = match.group(1)

    wired = re.findall(r"\b(bindCheckForm|bindToolList|bindJobForm|loadMetadata)\s*\(", block)
    assert wired == ["bindCheckForm", "bindToolList", "bindJobForm", "loadMetadata"], (
        f"三步的初始化接线不完整：实际调用了 {wired}（期望依次为 "
        "bindCheckForm / bindToolList / bindJobForm / loadMetadata）"
    )

    # 四个函数必须真的有定义，否则调用只是 ReferenceError。
    for name in ("bindCheckForm", "bindToolList", "bindJobForm", "loadMetadata"):
        assert re.search(rf"function {name}\s*\(", script), f"{name}() 被调用了却没有定义"

    # 初始化里**不许**出现已删除的那一批绑定（与上一条互为反向）。
    for name in ("bindScopeForm", "bindProjectForm", "syncJobScopes", "fillProjectSelects", "renderProjects"):
        assert not re.search(rf"\b{name}\b", block), f"初始化回调里又调用了已删除的 {name}"
