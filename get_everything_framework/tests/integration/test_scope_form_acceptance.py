"""``#scope-form`` 的 ``novalidate`` 收口：四条验收（用户指定，2026-10-04）。

背景：``#scope-form`` 里**同时**装着两个 ``required`` 下拉（``#job-project`` /
``#job-scope``）与「添加授权范围」的 ``type=submit`` 按钮。原生约束校验跑在
``submit`` 事件**之前**，于是全新用户（两下拉都只有占位项）点按钮时
``valueMissing=true``，浏览器直接拦下 —— ``scan_center.js:bindScopeForm()`` 的
submit 回调**一次都不执行**，**第一个授权范围在界面上根本建不出来**
（实测三状态：状态 1/2 的 ``submit`` 触发 0 次，状态 3 才 1 次）。

修法是给表单加 ``novalidate``，只关掉**浏览器原生**校验，不动服务端。
但「关掉校验」这四个字本身需要一个验收来兜底：**怎么证明它没有把校验一起关掉？**

按用户指定的四条逐一钉住 —— 每条都用**能真正观察到该行为的那一层**：

=====  ==========================================  ============================
条     验的是什么                                  在哪一层测（为什么）
=====  ==========================================  ============================
①      空表单点击 → JS 给出明确错误                  真实浏览器跑真实 scan_center.js
②      非法目标 → JS 转发 / 服务端拒绝                 两层各一条（见下）
③      合法目标 → 正常提交（建库 + 关联到项目）         服务端两步调用链
④      绕过 JS 直接调 API → 服务端仍然拒绝             Flask（含「一个 Scope 都没建」）
=====  ==========================================  ============================

**②为什么分两条**：JS 那层只能证明「把服务端的拒绝理由显示出来」（它不判定
合法性 —— 前端不是安全边界，见 ``scan_center.js`` 顶部约定）；合法性只由服务端
判。两条合起来才是完整的「非法目标被拒绝」：JS 不吞掉拒绝理由 + 服务端真的拒绝。

**①为什么必须真跑浏览器**：被测的就是「浏览器原生校验与 JS 回调的先后顺序」，
这一条在源代码里读不出来。CI（ubuntu + windows，无 Chrome）上它**跳过**，
本机 Windows 上真跑；同时另有一条不依赖浏览器的**源代码契约**兜底
（``test_scope_form_js_error_paths_are_wired``），保证 CI 也不会完全失去这条守卫。

**本文件不发起任何真实外部扫描**，不发网络请求，不用仓库里的任何数据库
（``admin_client`` 夹具已把两个库与三处运行期目录钉到 ``tmp_path``）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCAN_CENTER_JS = PROJECT_ROOT / "web" / "static" / "scan_center.js"
SCAN_CENTER_HTML = PROJECT_ROOT / "web" / "templates" / "scan_center.html"

#: ①/②/③ 用到的两个 JS 文案（``scan_center.js:bindScopeForm``）。
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


# ── ②-服务端 / ③ / ④：Flask 层（CI 上照常跑） ──────────────────


def test_legitimate_scope_submits_and_attaches_to_project(admin_client):
    """③ 合法目标 → 正常提交：Scope 建出来、**并真的挂到了项目下**。

    这条走的是 ``scan_center.js:bindScopeForm`` 提交时的**同一串两步调用**：

    ```text
    POST /api/scopes                       → 201，拿到 scope_id
    POST /api/projects/<id>/scopes         → 201，把 scope_id 关联到项目
    ```

    只断言第一步 201 是不够的 —— 那证明不了「用户在步骤 2 的下拉里能选到它」。
    第二步失败时前端会显示「范围已创建，但关联到项目失败」，也就是**孤儿范围**：
    范围存在、却不出现在任何项目的选择里，正是 §3.17.1 那个死胡同的另一种形态。
    所以这里三处都断言：创建 201、关联 201、以及列表里真的看得见。
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

    # 步骤 2 的下拉读的就是这两条接口：项目里必须能查到它，范围必须还在列表里。
    detail = admin_client.get(f"/api/projects/{project_id}").get_json()["project"]
    assert detail["scope_ids"] == [scope["id"]]
    listing = admin_client.get("/api/scopes").get_json()["scopes"]
    assert [item["id"] for item in listing] == [scope["id"]]
    assert listing[0]["allowed_domains"] == ["www.example.test"]


@pytest.mark.parametrize("payload,expected_code", ILLEGAL_SCOPE_PAYLOADS)
def test_direct_api_bypass_is_still_rejected_by_the_server(admin_client, payload, expected_code):
    """④ 绕过 JS 直接打 ``POST /api/scopes`` → 服务端仍然拒绝，且**一个 Scope 都没建**。

    ``novalidate`` 关掉的只是浏览器原生校验。这条把「前端不是安全边界」变成
    可执行的：不经过 ``bindScopeForm``、不经过任何前端代码，直接发请求。

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
    assert body.get("error_message"), "拒绝理由不能是空的 —— 前端要把这句显示给用户"
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
    """④（续）绕过 JS **并且**绕过登录：匿名直接打 ``POST /api/scopes`` → 401。

    「绕过前端」有两种绕法：绕过 JS 的校验，和绕过 JS 的登录态。前者靠服务端
    校验兜住（上一条），后者靠 ``require_admin()`` 兜住 —— ``api/scopes.py:70``
    就在函数体第一行。这里两条都验，否则「绕过 JS」这个说法只被验了一半。

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


# ── 不依赖浏览器时的源代码契约 ────────────────────────────────


def test_scope_form_js_error_paths_are_wired():
    """①/② 的无浏览器兜底：两条 JS 文案与它们要写入的元素都还在、且可达。

    浏览器用例（下面那条）在 CI 上必然跳过，所以这里留一条**不依赖 Chrome** 的
    契约，保证 CI 也不会完全失去这条守卫。它刻意**只锚在「接线是否完整」**上，
    不重复 ``test_public_scan_mode.py`` 里那条 ``novalidate`` 正则守卫：

    * 两个 ``select`` 仍带 ``required``（缺陷不是它们造成的，缺的是 ``novalidate``）；
    * ``scan_center.js`` 里两条错误文案仍在，且都写在 ``setText("scope-feedback"...)``
      上 —— 文案换了元素、或者函数改名，这里立刻红；
    * ``#scope-feedback`` 在模板里真的存在（否则 ``setText`` 会静默什么都不做，
      用户点了按钮还是「没有任何反应」—— 与缺陷本身同一个症状）。

    ★ 这条**不能**证明「回调真的会执行」—— 那正是浏览器那条的职责。
    两条一起看才有意义：这条保证代码在，那条保证代码跑得到。
    """
    script = SCAN_CENTER_JS.read_text(encoding="utf-8")
    template = SCAN_CENTER_HTML.read_text(encoding="utf-8")

    for message in (MSG_NO_PROJECT, MSG_NO_TARGETS):
        assert message in script, f"scan_center.js 里少了错误文案：{message}"
        assert f'setText("scope-feedback", "{message}"' in script, (
            f"{message!r} 不再写进 #scope-feedback —— 用户就看不到任何反馈了"
        )

    assert 'id="scope-feedback"' in template, "模板里没有 #scope-feedback，setText 会静默失败"

    tag = re.search(r'<form[^>]*id="scope-form"[^>]*>', template)
    assert tag, "模板里找不到 #scope-form"
    assert "novalidate" in tag.group(0), "novalidate 没了 → 回调一次都不会执行（§3.17.1 复发）"


# ── ①/②-JS：真实浏览器真跑 scan_center.js ─────────────────────

_CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def _find_chrome() -> str | None:
    """找一个可用的 Chrome/Chromium；找不到返回 ``None``（用例据此跳过）。"""
    for candidate in _CHROME_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


#: 在真实浏览器里驱动**真实** ``#scope-form`` + **真实** ``scan_center.js`` 的页面。
#:
#: 三个要点（都是上一版探针踩过或差点踩到的）：
#:
#: 1. **表单是页面里那一份，不是每状态重建的**。上一版探针用
#:    ``stage.innerHTML = REAL_FORM`` 逐状态重建表单，那样测到的是浏览器原生校验，
#:    ``bindScopeForm`` 的监听器根本不在新元素上 —— 这正是「测了个假对象」。
#:    这里表单直接写在 body 里，真实脚本绑的就是它，之后的改动都是**改活 DOM**。
#: 2. **脚本内容内联**，不写 ``<script src="file://...">``：file:// 之间有
#:    opaque origin，Chrome 对 file→file 的资源加载有额外策略；内联等于绕过它，
#:    且内联的仍是**从磁盘读出来的那一份原文**（测试里直接 ``read_text``）。
#: 3. **``fetch`` 被替换成受控桩**：GET 一律 401（让 ``loadMetadata()`` 走它自己
#:    那条「未登录就安静跳过」的分支，不产生未处理的 rejection），POST 记录请求
#:    并返回服务端**真实形状**的 400 体。于是 ③ 那条能断言「JS 把服务端的拒绝理由
#:    显示出来了」，而「这个 payload 合法不合法」仍由 Flask 用例判定。
_HARNESS = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>scope-form acceptance</title></head>
<body>
<div id="strategy-list"></div>
__REAL_FORM__
<pre id="log"></pre>
<script>
// 受控 fetch：GET → 401（loadMetadata 会安静跳过），POST → 记录 + 400。
window.__requests = [];
window.fetch = function (url, opts) {
  var method = (opts && opts.method) || "GET";
  if (method === "POST") {
    window.__requests.push({ url: String(url), body: (opts && opts.body) || null });
    return Promise.resolve({
      ok: false, status: 400,
      json: function () {
        return Promise.resolve({
          ok: false, error_code: "invalid_target",
          error_message: "不允许使用全放行通配符，请显式列出允许的域名"
        });
      }
    });
  }
  return Promise.resolve({
    ok: false, status: 401,
    json: function () { return Promise.resolve({ ok: false, error_code: "unauthenticated" }); }
  });
};
</script>
<script>
__REAL_JS__
</script>
<script>
document.addEventListener("DOMContentLoaded", function () {
  var form = document.getElementById("scope-form");
  var feedback = document.getElementById("scope-feedback");
  var button = document.querySelector('#scope-add button[type="submit"]');
  var project = document.getElementById("job-project");
  var scope = document.getElementById("job-scope");
  var domains = document.getElementById("scope-domains");

  // 注册顺序在真实脚本之后（DOMContentLoaded 按注册顺序触发）：
  // 真实 handler 先跑（它会 preventDefault），这里再记录「submit 真的触发了」。
  var submitEvents = 0;
  form.addEventListener("submit", function () { submitEvents++; });

  function snapshot(label) {
    return {
      label: label,
      submit_events: submitEvents,
      feedback: feedback ? feedback.textContent : null,
      feedback_is_error: feedback ? feedback.className.indexOf("alert-error") !== -1 : null,
      requests: window.__requests.slice(),
      form_novalidate: form.hasAttribute("novalidate"),
      checkValidity: form.checkValidity(),
      invalid: Array.prototype.map.call(form.querySelectorAll(":invalid"), function (el) {
        return el.tagName.toLowerCase() + (el.id ? "#" + el.id : "");
      })
    };
  }

  function option(select, value, text) {
    var o = document.createElement("option");
    o.value = value; o.textContent = text;
    select.appendChild(o);
    return o;
  }

  var out = [];

  // 状态 1：全新用户 —— 两下拉只有占位项、没填任何范围。正是缺陷现场。
  button.click();
  out.push(snapshot("1-empty-form"));

  // 状态 2：建过项目、还没建范围，也没填域名。
  option(project, "proj_real", "培正学院授权测试");
  project.value = "proj_real";
  button.click();
  out.push(snapshot("2-project-but-no-targets"));

  // 状态 3：填了非法的全放行通配符 → 必须被**转发出去**，并把服务端的理由显示出来。
  domains.value = "*";
  button.click();
  setTimeout(function () {
    out.push(snapshot("3-illegal-wildcard-forwarded"));
    document.getElementById("log").textContent = "RESULT=" + JSON.stringify(out);
  }, 120);
});
</script>
</body></html>
"""


def _render_scope_form(admin_client) -> str:
    """从**真实渲染**的 ``/scan-center`` 里原样截取 ``#scope-form`` 那一段。"""
    resp = admin_client.get("/scan-center")
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    assert "步骤 1 · 输入目标" in page, "这不是管理员渲染的扫描中心页，结论作废"
    match = re.search(r'<form class="form" id="scope-form".*?</form>', page, re.S)
    assert match, "渲染结果里找不到 #scope-form"
    form_html = match.group(0)
    assert "添加授权范围" in form_html, "截到的不是那个带提交按钮的表单"
    return form_html


@pytest.mark.slow
@pytest.mark.skipif(_find_chrome() is None, reason="本机没有 Chrome/Chromium，浏览器层验收无法执行")
def test_scope_form_novalidate_acceptance_in_real_browser(admin_client, tmp_path):
    """①/②-js/③-js 在**真实浏览器**里跑**真实** ``scan_center.js``。

    三个状态逐条对应验收：

    ======  ================================================================
    状态    断言
    ======  ================================================================
    1       空表单点「添加授权范围」→ ``submit`` **真的触发**（``novalidate`` 生效）、
            且 ``#scope-feedback`` 出现「还没有授权项目，请先在上面的表单里创建一个。」
    2       有项目、没填域名 → 文案换成「至少填一个授权域名或授权网段。」
    3       填了 ``*`` → 请求**真的发出去了**（``allowed_domains:["*"]`` 原样在 body 里），
            且服务端的拒绝理由被显示到 ``#scope-feedback`` 上（不吞错）
    ======  ================================================================

    ★ 状态 1 的 ``submit_events`` 是这条用例的**核心判据**：加 ``novalidate`` 之前它
    恒为 0（§3.17.1 的实测表），也就是「回调根本没被调用」的全部证据。只断言
    「错误文案出现在页面上」是不够的 —— 文案可能来自别处；断言「submit 触发次数」
    才直接对准「原生校验有没有把事件拦掉」这个机制。

    ★ 状态 1 同时断言 ``checkValidity() is False`` 与 ``:invalid`` 里含 ``#job-project``：
    这说明**原生校验仍然认为这个表单是无效的**，我们只是让事件绕过了它 ——
    与「把 ``required`` 删掉」是两件不同的事（那种修法会让浏览器什么都不拦，
    这条断言会红）。

    ★ 状态 3 断言的是**转发与显示**，不是合法性判定：``fetch`` 是受控桩，
    它的 400 是桩给的。合法性由 ``test_direct_api_bypass_is_still_rejected_by_the_server``
    用真服务端判定。两条分工写在这里，避免后来者误以为这条在测服务端。
    """
    chrome = _find_chrome()
    assert chrome, "跳过判据与执行判据不一致"

    harness = _HARNESS.replace("__REAL_FORM__", _render_scope_form(admin_client))
    harness = harness.replace("__REAL_JS__", SCAN_CENTER_JS.read_text(encoding="utf-8"))
    page = tmp_path / "scope_form_acceptance.html"
    page.write_text(harness, encoding="utf-8")

    proc = subprocess.run(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--dump-dom",
            "--virtual-time-budget=4000",
            page.resolve().as_uri(),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    dom = proc.stdout or ""
    match = re.search(r"RESULT=(\[.*?\])</pre>", dom, re.S)
    assert match, (
        f"Chrome 没吐回 RESULT —— 页面里的 JS 可能在真实脚本之后就没跑起来。\nstderr 尾部: {(proc.stderr or '')[-800:]}"
    )
    rows = json.loads(match.group(1))
    assert [row["label"] for row in rows] == [
        "1-empty-form",
        "2-project-but-no-targets",
        "3-illegal-wildcard-forwarded",
    ], rows

    first, second, third = rows

    # ── ① 空表单：事件真的触发 + JS 给出明确错误 ──
    # 先断言「事件触发次数」，再断言 novalidate 属性在不在：这两条的顺序是刻意的。
    # 反了的话，一旦有人删掉 novalidate，用例会先报「属性不见了」——那只是**症状**；
    # 这条判据真正要抓的是它的**后果**（回调一次都不执行、按钮点了没反应），
    # 报出来的失败信息应当直接是后果。
    assert first["submit_events"] == 1, (
        f"状态 1 的 submit 触发 {first['submit_events']} 次 —— novalidate 失效，"
        "全新用户点「添加授权范围」不会有任何反应（§3.17.1 复发）"
    )
    assert first["form_novalidate"] is True, "表单没有 novalidate，状态 1 的事件会被原生校验拦掉"
    assert first["feedback"] == MSG_NO_PROJECT, first
    assert first["feedback_is_error"] is True, "错误文案必须带 alert-error 样式"
    # 原生校验仍然认为它无效：证明我们绕的是「事件」，不是「校验本身」。
    assert first["checkValidity"] is False, "状态 1 竟然通过了原生校验，与 required 下拉的存在矛盾"
    assert any("job-project" in name for name in first["invalid"]), first["invalid"]

    # ── ②-js 有项目、没填域名：文案换成「至少填一个…」，且不发请求 ──
    assert second["submit_events"] == 2, second["submit_events"]
    assert second["feedback"] == MSG_NO_TARGETS, second
    assert second["requests"] == [], "还没填任何范围就把请求发出去了"

    # ── ③-js 非法目标被**原样转发**，服务端理由被显示出来（不吞错）──
    assert third["submit_events"] == 3, third["submit_events"]
    assert len(third["requests"]) == 1, third["requests"]
    sent = json.loads(third["requests"][0]["body"])
    assert sent["allowed_domains"] == ["*"], f"JS 没有原样转发用户输入：{sent}"
    assert third["requests"][0]["url"] == "/api/scopes", third["requests"]
    assert third["feedback"] is not None and "添加失败" in third["feedback"], third["feedback"]
    assert "不允许使用全放行通配符，请显式列出允许的域名" in third["feedback"], (
        "服务端的拒绝理由被吞掉了 —— 用户只会看到「添加失败」四个字"
    )
    assert third["feedback_is_error"] is True
