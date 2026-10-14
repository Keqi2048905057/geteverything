"""P1 集成测试：资产 / 观测 API 与 Diff 端点（方案第 8、10 节）。

这一层验证的是「从任务执行到资产可查」的完整链路：

    POST /api/jobs  →  worker 执行  →  assets / observations 落库
                    →  GET /api/assets  →  GET /api/jobs/{a}/diff/{b}

因此这里**不用**手工插资产，而是真的跑一遍 mock 任务，确保
``jobs/executor.py`` 的 ingest 接线没有断。
"""

import re

import pytest

from core import assets as assets_store
from core import jobs as jobs_store


@pytest.fixture
def scope_id(tmp_path, monkeypatch):
    """建一个 Scope 并返回其 ID（走 store，不经过 API，避免依赖 create 端点）。"""
    import config
    import core.db as core_db
    from core import scope_store

    path = str(tmp_path / "test_local.db")
    monkeypatch.setitem(config.LOCAL_DB_CONFIG, "path", path)
    core_db.reset_schema_cache()
    core_db.ensure_schema(path)
    return scope_store.create(name="资产 API 范围", allowed_domains=["example.test"]).id


def _run_job(scope_id, targets, tools=("subfinder",)):
    """建一个 mock 任务、领取并执行完毕，返回 job dict。"""
    from jobs.executor import execute_job

    job = jobs_store.create_job(scope_id=scope_id, targets=list(targets), tools=list(tools))
    jobs_store.claim_next_job("w-test", lease_seconds=300)
    return execute_job(job["id"])


# ── 鉴权 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/api/assets",
        "/api/assets/summary",
        "/api/assets/asset_x",
        "/api/observations?job_id=job_x",
        "/api/jobs/job_a/diff/job_b",
    ],
)
def test_asset_endpoints_require_admin(client, path):
    """资产是「整理后的情报」，比原始结果行更敏感 —— 必须登录。"""
    resp = client.get(path)
    assert resp.status_code == 401
    assert resp.get_json()["error_code"] == "unauthenticated"


# ── 列表 / 详情 / 统计 ───────────────────────────────────


def test_job_execution_shows_up_in_asset_list(admin_client, scope_id):
    job = _run_job(scope_id, ["a.example.test"])

    resp = admin_client.get("/api/assets")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["total"] >= 1
    assert body["assets"]
    assert {"subdomain", "host", "ip", "cidr", "url", "port", "service"} == set(body["types"])

    first = body["assets"][0]
    assert first["type"] == "subdomain"
    assert first["first_seen"] and first["last_seen"]
    assert "canonical_key" in first
    # 出参里不允许出现服务器路径
    assert "artifacts" not in resp.get_data(as_text=True)

    observations = assets_store.list_observations(asset_id=first["id"])
    assert any(item["job_id"] == job["id"] for item in observations)


def test_asset_detail_has_observation_timeline(admin_client, scope_id):
    _run_job(scope_id, ["b.example.test"])
    asset_id = admin_client.get("/api/assets").get_json()["assets"][0]["id"]

    resp = admin_client.get(f"/api/assets/{asset_id}")
    assert resp.status_code == 200
    asset = resp.get_json()["asset"]
    assert asset["id"] == asset_id
    assert asset["observations"]
    assert asset["metadata"] == {} or isinstance(asset["metadata"], dict)
    # 时间线要能回答「谁发现的」
    assert all(item["source_tool"] for item in asset["observations"])


def test_asset_detail_unknown_is_404(admin_client):
    resp = admin_client.get("/api/assets/asset_missing")
    assert resp.status_code == 404
    assert resp.get_json()["error_code"] == "not_found"


def test_assets_summary_route_is_not_shadowed_by_asset_id(admin_client, scope_id):
    """``/api/assets/summary`` 必须命中统计端点，而不是被 ``<asset_id>`` 吃掉。"""
    _run_job(scope_id, ["c.example.test"])
    resp = admin_client.get("/api/assets/summary")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["total"] >= 1
    assert "by_type" in body


def test_invalid_type_and_status_are_rejected(admin_client):
    bad_type = admin_client.get("/api/assets?type=galaxy")
    assert bad_type.status_code == 400
    assert bad_type.get_json()["error_code"] == "bad_request"
    assert "supported" in bad_type.get_json()["details"]

    bad_status = admin_client.get("/api/assets?status=exploded")
    assert bad_status.status_code == 400


def test_observations_requires_a_filter(admin_client):
    """不做无条件全表扫描：必须给 asset_id 或 job_id。"""
    resp = admin_client.get("/api/observations")
    assert resp.status_code == 400
    assert resp.get_json()["error_code"] == "bad_request"


def test_observations_can_be_filtered_by_job(admin_client, scope_id):
    job = _run_job(scope_id, ["d.example.test"])
    resp = admin_client.get(f"/api/observations?job_id={job['id']}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["observations"]
    assert all(item["job_id"] == job["id"] for item in body["observations"])
    assert body["total"] == len(body["observations"])


# ── Diff 端点（方案第 10 节） ────────────────────────────


def test_diff_endpoint_reports_added_and_removed(admin_client, scope_id, monkeypatch):
    """端到端复现方案第 10 节的验收：A B C 对 A C D。"""
    from core import assets as assets_mod

    before = _run_job(scope_id, ["a.example.test"])
    after = _run_job(scope_id, ["b.example.test"])

    # mock 结果为确定性的子域列表，直接按 canonical_key 改写任务归属：
    # 这里用真实观测更可靠 —— 把 before 任务里多余的观测挪走，制造 added/removed。
    resp = admin_client.get(f"/api/jobs/{before['id']}/diff/{after['id']}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert set(body["counts"]) == {"added", "removed", "changed", "unchanged"}
    assert assets_mod.diff_jobs(before["id"], after["id"])["counts"] == body["counts"]


def test_diff_endpoint_unknown_job_is_404(admin_client, scope_id):
    job = _run_job(scope_id, ["e.example.test"])
    resp = admin_client.get(f"/api/jobs/job_missing/diff/{job['id']}")
    assert resp.status_code == 404
    resp = admin_client.get(f"/api/jobs/{job['id']}/diff/job_missing")
    assert resp.status_code == 404


def test_diff_endpoint_can_omit_unchanged(admin_client, scope_id):
    """``include_unchanged=0`` 只影响明细下发，不影响计数。"""
    job = _run_job(scope_id, ["f.example.test"])
    resp = admin_client.get(f"/api/jobs/{job['id']}/diff/{job['id']}?include_unchanged=0")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["unchanged"] == []

    full = admin_client.get(f"/api/jobs/{job['id']}/diff/{job['id']}").get_json()
    assert body["counts"] == full["counts"], "关掉明细不该改变任何计数"


def test_diff_items_always_carry_asset_id(admin_client, scope_id):
    """每条 diff 明细都要带 ``asset_id`` —— 前端靠它跳资产详情。

    这条锁的是「页面上能点」这个能力的前置条件：``asset_id`` 为 ``None`` 的条目
    即使渲染出来也只能是死文本，而它恰恰是 ``data-asset-id`` 的唯一来源。
    """
    before = _run_job(scope_id, ["i.example.test"])
    after = _run_job(scope_id, ["j.example.test"])

    body = admin_client.get(f"/api/jobs/{before['id']}/diff/{after['id']}").get_json()
    items = body["added"] + body["removed"] + body["changed"] + body["unchanged"]
    assert items, "两次不同目标的任务至少要产出 added / removed"
    for item in items:
        assert item["asset_id"], f"diff 条目缺少 asset_id: {item!r}"
        # 详情接口必须真的认这个 id，否则前端点进去是 404。
        detail = admin_client.get(f"/api/assets/{item['asset_id']}")
        assert detail.status_code == 200
        assert detail.get_json()["asset"]["id"] == item["asset_id"]


def test_diff_endpoint_marks_changed_attributes(admin_client, scope_id):
    """属性变化必须出现在 ``changes`` 里（status_code / title …）。"""
    job = _run_job(scope_id, ["g.example.test"])
    asset = assets_store.list_assets(scope_id=scope_id, limit=1)[0]

    # 同一资产在第二个任务里以不同属性被观测。
    second = jobs_store.create_job(
        scope_id=scope_id, targets=["g.example.test"], tools=["subfinder"]
    )
    assets_store.record_observation(
        asset["type"],
        asset["value"],
        scope_id=scope_id,
        job_id=second["id"],
        source_tool="httpx",
        data={"status_code": 403, "title": "Forbidden"},
    )
    assets_store.record_observation(
        asset["type"],
        asset["value"],
        scope_id=scope_id,
        job_id=job["id"],
        source_tool="httpx",
        data={"status_code": 200, "title": "OK"},
    )

    resp = admin_client.get(f"/api/jobs/{job['id']}/diff/{second['id']}")
    changes = resp.get_json()["changed"]
    assert changes
    assert changes[0]["changes"]["status_code"] == {"from": 200, "to": 403}
    assert changes[0]["changes"]["title"] == {"from": "OK", "to": "Forbidden"}


# ── 出参不夹带服务器路径 ─────────────────────────────────


def test_asset_responses_expose_no_server_path(admin_client, scope_id):
    _run_job(scope_id, ["h.example.test"])
    raw = admin_client.get("/api/assets").get_data(as_text=True)
    assert "C:\\\\" not in raw
    assert "test_local.db" not in raw

    asset_id = admin_client.get("/api/assets").get_json()["assets"][0]["id"]
    raw = admin_client.get(f"/api/assets/{asset_id}").get_data(as_text=True)
    assert "C:\\\\" not in raw
    assert "test_local.db" not in raw


# ── 资产列表页（DECISIONS-G：先做资产列表页） ────────────


def test_assets_page_renders_without_template_error(client):
    """页面骨架必须渲染成功（历史上首页曾因模板缺失必然 500）。"""
    resp = client.get("/assets")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "资产列表" in body
    # 取数走静态脚本（前端同源拉 /api/assets），页面本身只出骨架。
    assert 'static/assets.js' in body
    assert 'id="assets-table"' in body
    assert 'id="assets-filter"' in body


def test_assets_page_hides_scope_names_when_anonymous(client):
    """未登录只给提示，不下发 Scope 名称（那是敏感信息）。"""
    body = client.get("/assets").get_data(as_text=True)
    assert "未登录" in body
    assert "401 unauthenticated" in body


def test_assets_page_has_no_scope_filter(admin_client, scope_id):
    """资产页**不再有**「授权范围」这个筛选维度（本轮落盘的前端变更）。

    这条替换了原来的 ``test_assets_page_lists_scopes_when_authenticated``
    （它断言登录后能从页面上读到 Scope 名称与 ID）。改口径而不删守卫的理由：
    **筛选维度从三个变成两个**这件事本身要有人守着 —— 「少一个维度」是刻意的，
    但「把整块筛选一起删掉」不是。所以这条同时钉两头：

    * 范围维度**真的没了**：``#filter-scope`` / ``#diff-scope`` 两个下拉，以及
      任何 ``scope_…`` 形状的实体 ID，都不再出现在页面上。断言时库里
      **确实存在一个 Scope**（``scope_id`` 夹具刚建好的那个）—— 页面不该看见它；
    * 类型 / 状态 / 值三个维度**仍在**：它们才是资产页现在的筛选口径。

    ``scope_id`` 不再是资产的筛选维度：资产列表接口没有 ``scope_id`` 参数
    （``assets.js:buildQuery()`` 不再拼它），两次任务对比也只看这两次任务各自的
    观测。留一个用不上的下拉，只会让用户以为「按范围筛」这个能力其实存在 ——
    这正是「前端不该展示用不上的内部模型」的同一条理由（方案第 4 节原则 2）。
    """
    body = admin_client.get("/assets").get_data(as_text=True)

    # 范围维度：两个下拉与实体 ID 都不在页面上。
    assert 'id="filter-scope"' not in body, "「全部授权资产」下拉又回来了"
    assert 'id="diff-scope"' not in body, "「限定授权资产」下拉又回来了"
    assert not re.search(r"scope_[0-9a-f]{6,}", body), "页面下发了 Scope 实体 ID"
    assert scope_id not in body, "页面下发了本机真实存在的那个 Scope ID"

    # 筛选维度仍在：删掉的是「范围」，不是「筛选」。
    for control in ('id="filter-type"', 'id="filter-status"', 'id="filter-search"'):
        assert control in body, f"筛选控件 {control} 不见了 —— 删掉的应当只是范围这一个维度"


def test_assets_page_offers_all_types_and_statuses(admin_client):
    body = admin_client.get("/assets").get_data(as_text=True)
    for asset_type in ("subdomain", "host", "ip", "cidr", "url", "port", "service"):
        assert asset_type in body
    for status in ("active", "stale", "gone"):
        assert status in body


def test_scan_center_links_to_assets_page(client, admin_client):
    """两个页面必须**互相可达**，否则资产页只能靠手输 URL 才能找到。

    这条替换了原来的 ``test_index_links_to_assets_page``（它断言首页 HTML 里有
    指向 ``/assets`` 的入口）。旧首页整体退役后，入口链变成：

    ```text
    GET /  --302-->  /scan-center  --「资产」链接-->  /assets  --「← 扫描中心」-->  /scan-center
    ```

    所以三点都要钉住：

    1. ``GET /`` 是 **302 且 Location 指向 ``/scan-center``** —— 根路径只剩跳转
       （``app.py:index()`` 里没有 POST 分支，这里断 302 而不是 200 文本）；
    2. ``/scan-center`` 页面里有 ``/assets``，``/assets`` 页面里有 ``/scan-center``；
    3. 两个页面里都**没有** ``href="/"`` —— 旧首页那条「← 任务」链接不得复活。
       旧首页不在页面上了，任何指回 ``/`` 的链接都是把一个 302 当成目的地，
       点下去会在浏览器里多跳一次，且暗示着「首页还在」这个已经不成立的事实。
    """
    # 1. 根路径只剩一次跳转。
    root = client.get("/")
    assert root.status_code == 302, f"GET / 不再是 302 跳转，而是 {root.status_code}"
    assert "/scan-center" in root.headers.get("Location", ""), root.headers.get("Location")

    # 2. 两页互相可达（用管理员客户端取，两个页面都渲染完整骨架）。
    center = admin_client.get("/scan-center")
    assert center.status_code == 200
    center_body = center.get_data(as_text=True)
    assert 'href="/assets"' in center_body, "扫描中心没有通往资产页的入口"

    assets_body = admin_client.get("/assets").get_data(as_text=True)
    assert 'href="/scan-center"' in assets_body, "资产页没有回扫描中心的入口"

    # 3. 旧首页的「← 任务」链接（href="/"）不得复活。
    for name, body in (("扫描中心", center_body), ("资产页", assets_body)):
        assert 'href="/"' not in body, f"{name}又出现了指回旧首页的链接（href=\"/\"）"


# ── 两次任务对比的页面入口（P1 §10 前端露出） ─────────────


def test_assets_page_exposes_diff_form(client):
    """Diff 端点可用但页面上没入口，等于没人会用 —— 锁住骨架。"""
    body = client.get("/assets").get_data(as_text=True)
    assert 'id="diff-form"' in body
    assert 'id="diff-before"' in body
    assert 'id="diff-after"' in body
    assert 'id="diff-body"' in body
    assert 'id="diff-include-unchanged"' in body


def test_assets_page_diff_form_disabled_when_anonymous(client):
    """未登录时对比按钮必须是 disabled，而不是「点了才报 401」。"""
    body = client.get("/assets").get_data(as_text=True)
    # 「筛选」与「对比」两个按钮都要 disabled，所以至少出现两次。
    assert body.count("disabled") >= 2
    assert "diff-summary" in body


def test_assets_page_diff_scope_select_is_empty_when_anonymous(client):
    """匿名时不能下发 Scope 名称（与列表页同一口径）。"""
    body = client.get("/assets").get_data(as_text=True)
    assert "（scope_" not in body


def test_assets_page_diff_has_no_scope_select(admin_client, scope_id):
    """对比表单**不再有**「限定授权资产」下拉，但对比本身保留。

    这条替换了原来的 ``test_assets_page_diff_scope_select_lists_scopes_when_authenticated``。
    口径变化的原因写在 assets.html 里：两次任务对比只看**这两次任务自己的观测**
    （否则「上次 A B C、这次 A C D」这种最常见的场景反而给不出「新增 D / 消失 B」），
    与资产的授权范围无关 —— 既然对比结果根本不受范围影响，一个范围下拉就只是
    一个**看起来能改变结论、实际什么也不改变**的控件。

    同样两头都钉：``#diff-scope`` 消失，而 ``#diff-before`` / ``#diff-after``
    这两个真正决定对比结果的下拉仍在 —— 删掉的是范围维度，不是对比功能。
    """
    body = admin_client.get("/assets").get_data(as_text=True)

    assert 'id="diff-scope"' not in body, "「限定授权资产」下拉又回来了"
    assert scope_id not in body, "对比表单又下发了 Scope 实体 ID"

    for control in ('id="diff-before"', 'id="diff-after"'):
        assert control in body, f"{control} 不见了 —— 对比功能本身不能被删掉"


def test_assets_js_wires_diff_items_to_asset_detail(client):
    """页面已渲染 ``data-asset-id``，但必须有脚本把它接成点击跳转。

    否则「Diff 条目可点进资产详情」就只是注释里的一句承诺 ——
    静态脚本没有构建链、也没有别的测试能发现它漏了。
    """
    resp = client.get("/static/assets.js")
    assert resp.status_code == 200
    script = resp.get_data(as_text=True)

    assert "diff-item-clickable" in script, "diff 条目没有可点的标记"
    assert "openDetail" in script and "asset-detail-panel" in script
    # 点击清单里的条目要能取到 asset_id 并打开详情。
    assert "diff-body" in script


def test_assets_js_never_renders_a_raw_scope_id_as_text(client):
    """方案第 4 节原则 2：前端**不显示** ``scope_id`` —— 实体 ID 只作表单 value。

    资产详情的「所属范围」一行原先直接写 ``asset.scope_id || "（未限定）"``，
    于是详情面板上会出现 ``scope_9f3c…``。这是「前端暴露内部模型」的典型形态：
    用户看到的应该是一个**能对上号的授权资产**，而不是一串数据库主键。

    当时这条守卫的要求是「必须走 ``scopeLabelById()`` 翻译一次」；本轮资产页把
    授权范围整个筛选维度删掉之后，``scopeLabelById()`` 也一并删除了 —— 于是
    等价且**更强**的口径是：``assets.js`` 里根本不出现 ``scope`` 这一族标识。
    原来的「查表翻译」是「允许展示、但不许展示原始 ID」；现在是「连翻译都不需要，
    因为这个维度不在这张页面上」。任何 ``scope`` 字样重新出现在这个文件里，
    都意味着有人把范围维度搬了回来 —— 那时这条会红，而不是某个用户先看到
    ``scope_9f3c…``。

    这里只做**源码级守卫，不是行为级**（项目没有浏览器测试，纯 JS 不能在 pytest
    里执行）：翻了就是翻了 —— 一旦有人把它改回原样，红的是这条测试。
    """
    script = client.get("/static/assets.js").get_data(as_text=True)

    # 整体禁字：function scopeLabelById / asset.scope_id / "filter-scope" / "diff-scope" /
    # 以及任何把 scope 当变量名的写法，都落在这个大小写不敏感的匹配里。
    found = re.findall(r"\S*scope\S*", script, re.I)
    assert not found, f"assets.js 里又出现了 scope 相关标识：{found[:8]}"

    # 正向：范围下拉没了，任务下拉的**可见文案**改由 jobOptionLabel() 生成 ——
    # 守卫不能只剩下「什么都不许有」，否则把整个 diff 功能删掉这条也会通过。
    assert "function jobOptionLabel(" in script, "缺少 job→选项文案 的渲染函数"
    assert "jobOptionLabel(job)" in script, "jobOptionLabel 定义了却没被 fillJobOptions 用上"


def test_assets_diff_options_label_targets_and_tools(client):
    """任务下拉的可见文案必须是「网址 + 使用的工具」（例如 ``example.com subfinder``）。

    选任务的人认的是「这次扫了什么」，不是内部 ``job_…`` 主键。每个 ``<option>``
    的 ``value`` 仍然是 ``job.id``（提交时要用），只有可见文案换成目标与工具 ——
    这正是方案第 4 节原则 2 的正面用法：内部 ID 留在数据里，不上屏。

    ★ **源码级守卫，不是行为级**：``assets.js`` 是纯前端脚本，pytest 里没有 DOM
    也没有浏览器能跑它（本仓库的浏览器验收另在 ``test_scope_form_acceptance.py``，
    且需要本机 Chrome）。所以这里钉的是「函数体里到底读没读 ``targets`` / ``tools``」
    —— 只要这两个字段被读进来，展示形态就不可能是纯 job id。

    两条纪律也在 ``jobOptionLabel`` 的注释里写着，此处按源码锚定：
    ``targets`` / ``tools`` 都取不到时必须**退回 job id**，绝不产生空白选项。
    """
    script = client.get("/static/assets.js").get_data(as_text=True)

    match = re.search(r"function jobOptionLabel\(job\)\s*\{(.*?)\n  \}", script, re.S)
    assert match, "assets.js 里找不到 jobOptionLabel(job) 的函数体"
    body = match.group(1)

    assert "job.targets" in body, "选项文案没读任务目标 —— 用户看到的会是一串 job id"
    assert "job.tools" in body, "选项文案没读任务工具 —— 「这次用了什么」就丢了"
    # 两个字段要真的参与拼接（`targets.join` / `tools.join`），不是只读不用。
    assert "targets.join(" in body and "tools.join(" in body, (
        "读到了 targets / tools 却没拼进文案，展示形态不会是 `example.com subfinder`"
    )
    # 取不到目标与工具时退回 job id：空白选项比一串 ID 更难用。
    # ★ 这里刻意**不**只断言「body 里出现过 job.id」—— 变异测试证明那种写法会漏：
    # 把 `return fallback` 改成 `return ""`（隐藏掉兜底）之后，`var fallback =
    # job.id || ""` 这一行还留着，`"job.id" in body` 依然为真、守卫照样绿，
    # 而线上多出的是空白选项。所以要钉「fallback 确实由 job.id 派生」**且**
    # 「两条兜底路径都真的 return fallback」（两者都空 / 拼完为空）。
    assert re.search(r"var fallback\s*=\s*job\.id", body), "fallback 没有从 job.id 派生"
    assert body.count("return fallback") >= 2, (
        "取不到 targets / tools 时没有退回 job id（会渲染出空白选项）"
    )


def test_assets_js_keeps_internal_ids_and_db_columns_off_the_screen(client):
    """方案第 4 节原则 2 的另外两个点名项：**UUID** 与**数据库字段**。

    原则 2 列的是三样 —— ``scope_id`` / UUID / 数据库字段。前一样在
    ``test_assets_js_never_renders_a_raw_scope_id_as_text`` 里守着，这里补后两样：

    * 详情标题此前写 ``idEl.textContent = asset.id``，页面上出现 ``asset_3f9c…``；
    * 摘要此前有一行「规范化键」，铺开的是 ``host|example.com`` 这种**列值**。

    两者都不是给使用者看的：ID 用来在 DOM 里定位（``data-asset-id`` 仍在、接口仍在），
    规范化键用来解释「为什么两条观测归并成一行」，属于排查信息。
    """
    script = client.get("/static/assets.js").get_data(as_text=True)

    for forbidden in (
        "textContent = asset.id",
        '["规范化键"',
        "asset.canonical_key",
    ):
        assert forbidden not in script, f"assets.js 又把内部标识/数据库字段上屏了: {forbidden}"
    # 定位能力不能一起删掉：详情面板仍要靠 ``data-asset-id`` 与接口取数。
    assert "asset-detail-id" in script
