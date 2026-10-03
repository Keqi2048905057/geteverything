"""工具参数标准化（方案第 14 节 Phase 2「Job tools 参数标准化」）。

本文件锁死三件此前**真实存在**的缺陷，它们都不是理论问题：

1. ``load_tools()`` 在拿到空工具列表时回落到 ``SCAN_CONFIG["enabled_runners"]``
   （当前是 ``["amass"]``）——「用户没选工具」变成「系统自己挑一个重的去扫」
   （``docs/CODEBASE_MAP.md`` BUG 索引第 7 条）；
2. ``POST /api/run`` 把 ``"subfinder,httpx"`` 整串包成**一个**工具名，于是
   必然报「存在不支持的工具」——同一个请求体从 ``/api/jobs`` 进得来、
   从 ``/api/run`` 进不来；
3. 两条链都不去重，而 ``total_steps = len(targets) * len(tools)``，
   同一个工具写两遍会让任务凭空多出一倍步骤。

同时锁死「写错的工具名报错、不被静默替换」与「未知工具按 registry 拒绝」。
"""

import pytest

from tool_runner import load_tools, normalize_tool_names


# ── normalize_tool_names：唯一的参数规范化实现 ────────────


def test_normalize_splits_comma_separated_string():
    assert normalize_tool_names("subfinder,httpx") == ["subfinder", "httpx"]


def test_normalize_strips_whitespace_and_drops_empty_items():
    assert normalize_tool_names(" subfinder , , httpx ,") == ["subfinder", "httpx"]
    assert normalize_tool_names([" subfinder ", "", "  "]) == ["subfinder"]


def test_normalize_dedupes_preserving_order():
    """去重必须保序：顺序决定步骤顺序，乱序会让「第几步跑了什么」对不上。"""
    assert normalize_tool_names(["httpx", "subfinder", "httpx"]) == ["httpx", "subfinder"]
    assert normalize_tool_names("subfinder,subfinder,httpx") == ["subfinder", "httpx"]


def test_normalize_none_and_empty_are_empty_list():
    assert normalize_tool_names(None) == []
    assert normalize_tool_names([]) == []
    assert normalize_tool_names("") == []


def test_normalize_accepts_tuple_and_coerces_non_str_items():
    assert normalize_tool_names(("subfinder", "httpx")) == ["subfinder", "httpx"]
    assert normalize_tool_names([1, 2]) == ["1", "2"]


@pytest.mark.parametrize("bad", [123, {"tools": []}, object()])
def test_normalize_rejects_non_string_non_list(bad):
    with pytest.raises(ValueError, match="tools 必须是"):
        normalize_tool_names(bad)


# ── load_tools：空 vs 未指定的区别 ────────────────────────


def test_load_tools_deduplicates_and_keeps_order():
    assert load_tools(["httpx", "subfinder", "httpx"]) == ["httpx", "subfinder"]
    assert load_tools("subfinder,httpx") == ["subfinder", "httpx"]


def test_load_tools_rejects_unknown_tool():
    with pytest.raises(ValueError, match="存在不支持的工具"):
        load_tools(["definitely-not-a-tool"])
    with pytest.raises(ValueError, match="存在不支持的工具"):
        load_tools("subfinder,nuclei")


def test_load_tools_falls_back_to_config_only_when_unspecified():
    """``None`` = 没指定（回落配置），``[]`` = 明确不要（不回落）。

    这是本文件最重要的一条：两者的区别就是「系统有没有替用户挑工具」。
    """
    from config import SCAN_CONFIG

    fallback = load_tools(None)
    assert fallback == SCAN_CONFIG["enabled_runners"]

    # 显式空列表**不得**回落 —— 回落到 ["amass"] 就是越权执行了用户没选的工具。
    assert load_tools([]) == []
    assert load_tools("") == []


def test_load_tools_fallback_result_is_itself_normalized():
    """回落值同样要过一遍规范化（配置里写重复/带空格也不能漏）。"""
    import config
    import tool_runner

    original = config.SCAN_CONFIG.get("enabled_runners")
    try:
        config.SCAN_CONFIG["enabled_runners"] = [" subfinder ", "subfinder", "httpx"]
        assert tool_runner.load_tools(None) == ["subfinder", "httpx"]
    finally:
        config.SCAN_CONFIG["enabled_runners"] = original


# ── HTTP 入口：/api/run 与 /api/jobs 口径必须一致 ──────────


def _scope(admin_client):
    resp = admin_client.post(
        "/api/scopes",
        json={
            "name": "工具参数测试范围",
            "allowed_domains": ["example.test"],
            "allowed_cidrs": [],
            "active_scan": False,
        },
    )
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["scope"]["id"]


def test_run_accepts_comma_separated_tools(admin_client):
    """``"subfinder,httpx"`` 必须被拆成两个工具，而不是一个不存在的工具名。"""
    scope_id = _scope(admin_client)
    resp = admin_client.post(
        "/api/run",
        json={
            "domain": "example.test",
            "tools": "subfinder,httpx",
            "scope_id": scope_id,
            "mode": "mock",
        },
    )
    assert resp.status_code == 200, resp.get_json()
    assert sorted(resp.get_json()["tools"]) == ["httpx", "subfinder"]


def test_run_dedupes_repeated_tools(admin_client):
    """同一个工具写两遍只算一个 —— 否则 mock 会返回两份重复 outcome。"""
    scope_id = _scope(admin_client)
    resp = admin_client.post(
        "/api/run",
        json={
            "domain": "example.test",
            "tools": ["subfinder", "subfinder", "subfinder"],
            "scope_id": scope_id,
            "mode": "mock",
        },
    )
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["tools"] == ["subfinder"]
    assert len(body["outcomes"]) == 1


def test_run_rejects_empty_tools_instead_of_falling_back(admin_client):
    """``tools: []`` / ``tools: ""`` / 完全不给 → 400，**不得**回落到配置默认值。

    历史行为是回落成 ``["amass"]`` 并真的去扫；这里只要出现 200 就说明
    「用户没选工具，系统自己挑了一个」这条路径又回来了。
    """
    scope_id = _scope(admin_client)
    for payload in (
        {"tools": []},
        {"tools": ""},
        {"tools": "  ,  "},
        {},
    ):
        resp = admin_client.post(
            "/api/run",
            json={"domain": "example.test", "scope_id": scope_id, "mode": "mock", **payload},
        )
        assert resp.status_code == 400, (payload, resp.get_json())
        assert resp.get_json()["error_code"] == "bad_request"


def test_run_unknown_tool_is_still_rejected(admin_client):
    """规范化不得放松 registry 校验：``nuclei`` 仍然进不来。"""
    scope_id = _scope(admin_client)
    resp = admin_client.post(
        "/api/run",
        json={"domain": "example.test", "tools": ["nuclei"], "scope_id": scope_id, "mode": "mock"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error_code"] == "bad_request"


def test_jobs_and_run_agree_on_tool_parsing(admin_client):
    """同一个 ``"subfinder,httpx"`` 从两条链进来必须得到同一组工具。"""
    scope_id = _scope(admin_client)

    run_body = admin_client.post(
        "/api/run",
        json={
            "domain": "example.test",
            "tools": "subfinder,httpx",
            "scope_id": scope_id,
            "mode": "mock",
        },
    ).get_json()

    jobs_resp = admin_client.post(
        "/api/jobs",
        json={"scope_id": scope_id, "targets": ["example.test"], "tools": "subfinder,httpx"},
    )
    assert jobs_resp.status_code == 202, jobs_resp.get_json()

    from core import jobs as jobs_store

    job = jobs_store.get_job(jobs_resp.get_json()["job_id"])
    assert sorted(run_body["tools"]) == sorted(job["tools"])
    # 去重口径也一致：2 个工具 × 1 个目标 = 2 步（不是 4 步）。
    assert job["total_steps"] == 2


@pytest.mark.parametrize(
    "empty",
    [{"tools": []}, {"tools": ""}, {"tools": "  ,  "}],
    ids=["empty-list", "empty-string", "blank-string"],
)
def test_jobs_does_not_fold_an_explicit_empty_selection_into_the_alias(admin_client, empty):
    """**明确给了空选择**时，别名 ``tool`` 不得顶上来（与 ``/api/run`` 同口径）。

    历史写法是 ``tools=payload.get("tools") or payload.get("tool")``：``or`` 会把
    ``[]`` / ``""`` / ``"  ,  "`` 一律折叠成假值，于是别名 ``tool`` 接管，
    请求体 ``{"tools": [], "tool": "subfinder"}`` 从 ``/api/jobs`` 进来落库成
    ``tools=['subfinder']`` 并**真的去扫**，而同一个请求体从 ``/api/run`` 进来是 400。
    这正是「同一个请求体从两条链进来得到两种解释」，方向与 BUG 索引第 7 条相反、
    成因相同。判据必须是「有没有给这个键」，不是「这个键的值真不真」。
    """
    scope_id = _scope(admin_client)

    from core import jobs as jobs_store

    resp = admin_client.post(
        "/api/jobs",
        json={"scope_id": scope_id, "targets": ["example.test"], "tool": "subfinder", **empty},
    )
    assert resp.status_code == 400, (empty, resp.get_json())
    assert resp.get_json()["error_code"] == "bad_request"
    # 更要紧的是**没有落库**：400 但留下一条 queued 任务同样是越权执行。
    assert jobs_store.list_jobs() == []

    run_resp = admin_client.post(
        "/api/run",
        json={"domain": "example.test", "scope_id": scope_id, "mode": "mock", "tool": "subfinder", **empty},
    )
    assert run_resp.status_code == 400, (empty, run_resp.get_json())


def test_jobs_still_accepts_the_single_tool_alias(admin_client):
    """修 `or` 折叠**不得**顺手删掉别名：只给 ``tool`` 时仍要正常创建。"""
    scope_id = _scope(admin_client)

    from core import jobs as jobs_store

    resp = admin_client.post(
        "/api/jobs",
        json={"scope_id": scope_id, "targets": ["example.test"], "tool": "subfinder"},
    )
    assert resp.status_code == 202, resp.get_json()
    assert jobs_store.get_job(resp.get_json()["job_id"])["tools"] == ["subfinder"]
