"""公网授权测试模式：工具权限元数据与扫描策略模板（方案第 5、8 节）。

本文件锁死方案第 5 节的字段模型与第 8 节的「第一阶段只支持 httpx / subfinder」：

* 每个已注册 runner 都必须有权限元数据（**没有**元数据的工具一律视为禁止公网，
  这是本模块最重要的不变量 —— 「没登记」不能被当成「没限制」）；
* 公网白名单恰好是 ``httpx`` + ``subfinder``；
* 三档策略模板不会塞入被禁工具；
* 判定函数 :func:`assert_tools_internet_allowed` 的报错形状稳定。
"""

import pytest

from core.errors import BadRequestError
from core.tool_registry import (
    KNOWN_UNAVAILABLE_TOOLS,
    RISK_LEVELS,
    STRATEGIES,
    STRATEGY_ASSET_DISCOVERY,
    STRATEGY_CUSTOM,
    STRATEGY_WEB_FINGERPRINT,
    TOOL_POLICIES,
    assert_tools_internet_allowed,
    get_strategy,
    get_tool_policy,
    internet_allowed_tools,
    list_strategies,
    list_tool_policies,
    resolve_strategy_tools,
)


# ── 元数据完整性 ──────────────────────────────────────────


def test_every_registered_runner_has_policy_metadata():
    """注册表里的每个 runner 都必须在 ``TOOL_POLICIES`` 里有条目。

    缺条目时的行为是「禁止公网」（安全的方向），但这里仍然要求补齐 ——
    否则新增工具的人不会意识到自己需要做一次风险判断。
    """
    from modules.registry import get_supported_runners

    missing = [name for name in get_supported_runners() if name not in TOOL_POLICIES]
    assert missing == [], f"以下 runner 缺少工具权限元数据: {missing}"


def test_no_policy_for_unregistered_tool():
    """反向：元数据表里不得出现注册表中不存在的工具（避免过期条目）。"""
    from modules.registry import get_supported_runners

    registered = set(get_supported_runners())
    extra = [name for name in TOOL_POLICIES if name not in registered]
    assert extra == [], f"元数据表里有多余的工具: {extra}"


def test_policy_risk_levels_are_known_values():
    for policy in list_tool_policies():
        assert policy.risk_level in RISK_LEVELS, policy.tool_name
        assert policy.reason or policy.internet_allowed, (
            f"{policy.tool_name} 禁止公网却没有给出原因，使用者无法理解为何被拒"
        )


def test_tool_name_matches_dict_key():
    for key, policy in TOOL_POLICIES.items():
        assert policy.tool_name == key


def test_policy_to_dict_shape():
    payload = get_tool_policy("httpx").to_dict()
    assert set(payload) == {
        "tool_name",
        "risk_level",
        "risk_label",
        "internet_allowed",
        "default_enabled",
        "reason",
        # Tool Registry（方案第 9 节）：用途说明与能力分组。
        "description",
        "tool_group",
        "tool_group_label",
    }
    assert payload["internet_allowed"] is True
    assert payload["description"], "工具必须说明自己做什么"
    assert payload["tool_group"] == "service"
    assert payload["tool_group_label"] == "服务识别"


def test_unknown_tool_policy_is_none():
    assert get_tool_policy("definitely-not-a-tool") is None


# ── Tool Registry：用途说明与能力分组（方案第 8、9 节） ────


def test_every_policy_declares_a_registered_group():
    """每个条目（含未接入的）都必须落在已登记分组里。

    分组 key 未登记时 :func:`group_tool_policies` 会抛错而不是静默丢弃，
    这里把同一件事提前到元数据层面拦住：新增工具的人必须选一栏，
    不能让它从界面上凭空消失。
    """
    from core.tool_registry import TOOL_GROUP_LABELS, list_all_tool_policies

    bad = [
        policy.tool_name
        for policy in list_all_tool_policies()
        if policy.tool_group not in TOOL_GROUP_LABELS
    ]
    assert bad == [], f"以下工具的分组未登记: {bad}"


def test_every_policy_declares_a_description():
    """方案第 9 节要求每个工具给出 ``description``：界面靠它解释工具用途。"""
    from core.tool_registry import list_all_tool_policies

    missing = [policy.tool_name for policy in list_all_tool_policies() if not policy.description]
    assert missing == [], f"以下工具缺少 description: {missing}"


def test_tool_group_never_changes_permission():
    """分组只决定界面摆位，**不得**影响权限。

    这是防「把工具挪个栏位就以为它变得可用了」的回归：
    分组的增删改都不应该让公网白名单发生变化。
    """
    assert internet_allowed_tools() == ["httpx", "subfinder"]
    for name in internet_allowed_tools():
        assert get_tool_policy(name).internet_allowed is True


def test_group_tool_policies_covers_every_policy_exactly_once():
    """分组视图必须**不重不漏**地覆盖全部条目。"""
    from core.tool_registry import group_tool_policies, list_all_tool_policies

    groups = group_tool_policies()
    names = [tool["tool_name"] for group in groups for tool in group["tools"]]
    assert sorted(names) == sorted(policy.tool_name for policy in list_all_tool_policies())
    assert len(names) == len(set(names)), "同一个工具出现在多个分组里"


def test_group_tool_policies_keeps_empty_groups_in_plan_order():
    """方案点名的「技术识别 / 漏洞检测」本阶段没有可用工具，也必须如实返回。"""
    from core.tool_registry import group_tool_policies

    groups = group_tool_policies()
    assert [group["key"] for group in groups] == [
        "recon",
        "service",
        "tech",
        "content",
        "vuln",
        "assist",
    ]
    by_key = {group["key"]: group for group in groups}
    # 空分组照样在列表里（前端据此显示「本阶段暂无可用工具」）。
    assert by_key["tech"]["tools"] == []
    assert by_key["tech"]["description"], "空分组也要说明这一栏是干什么的"
    # 未接入的 nuclei 归在「漏洞检测」，而不是被藏起来。
    assert [tool["tool_name"] for tool in by_key["vuln"]["tools"]] == ["nuclei"]


def test_group_tool_policies_rejects_unregistered_group():
    """未登记的分组 key 必须抛错，不能被静默丢弃。"""
    from core.tool_registry import ToolPolicy, group_tool_policies

    stray = ToolPolicy("stray", "low", False, False, tool_group="not-a-group")
    with pytest.raises(ValueError, match="未登记"):
        group_tool_policies([stray])


def test_group_tool_policies_accepts_a_subset():
    """传入子集时只对子集分组，未用到的栏位留空。"""
    from core.tool_registry import group_tool_policies, list_tool_policies

    subset = [policy for policy in list_tool_policies() if policy.tool_name == "httpx"]
    groups = group_tool_policies(subset)
    by_key = {group["key"]: group for group in groups}
    assert [tool["tool_name"] for tool in by_key["service"]["tools"]] == ["httpx"]
    assert by_key["recon"]["tools"] == []


# ── 公网白名单：方案第 8 节 ────────────────────────────────


def test_internet_whitelist_is_exactly_httpx_and_subfinder():
    """方案第 8 节：第一阶段只支持 httpx 与 subfinder。"""
    assert internet_allowed_tools() == ["httpx", "subfinder"]


@pytest.mark.parametrize(
    "tool_name",
    ["nmap", "naabu", "dirsearch", "feroxbuster", "shuffledns", "amass", "katana", "gospider"],
)
def test_high_risk_and_active_tools_are_blocked(tool_name):
    """方案第 8 节明确「暂不开放 nmap / dirsearch / 爆破类工具」。"""
    policy = get_tool_policy(tool_name)
    assert policy is not None
    assert policy.internet_allowed is False
    assert policy.default_enabled is False


def test_nuclei_is_registered_as_restricted_but_unavailable():
    """方案第 4 节提到 ``nuclei(限制)``：本项目 runner 里没有它，必须如实登记。"""
    from modules.registry import get_supported_runners

    assert "nuclei" not in get_supported_runners()
    assert "nuclei" in KNOWN_UNAVAILABLE_TOOLS
    assert KNOWN_UNAVAILABLE_TOOLS["nuclei"].internet_allowed is False


# ── 判定函数 ──────────────────────────────────────────────


def test_assert_allows_whitelisted_tools():
    assert_tools_internet_allowed(["httpx"])
    assert_tools_internet_allowed(["subfinder", "httpx"])


@pytest.mark.parametrize("blocked", ["nmap", "dirsearch", "naabu"])
def test_assert_rejects_blocked_tool(blocked):
    with pytest.raises(BadRequestError) as excinfo:
        assert_tools_internet_allowed(["httpx", blocked])

    error = excinfo.value
    assert error.http_status == 400
    assert error.details["field"] == "tools"
    names = [item["tool_name"] for item in error.details["blocked_tools"]]
    assert blocked in names
    # 错误消息里要给出白名单，使用者才知道该换成什么。
    assert error.details["internet_allowed_tools"] == ["httpx", "subfinder"]
    # httpx 本身在白名单内，不该被一起报出来。
    assert "httpx" not in names


def test_assert_rejects_unknown_tool():
    """未登记工具必须被拒 —— 「没人写元数据」不等于「不受限」。"""
    with pytest.raises(BadRequestError) as excinfo:
        assert_tools_internet_allowed(["some-new-tool"])
    assert excinfo.value.details["unknown_tools"] == ["some-new-tool"]


def test_assert_rejects_empty_list():
    with pytest.raises(BadRequestError):
        assert_tools_internet_allowed([])


# ── 策略模板 ──────────────────────────────────────────────


def test_strategy_order_is_fixed():
    assert [item.key for item in list_strategies()] == [
        STRATEGY_ASSET_DISCOVERY,
        STRATEGY_WEB_FINGERPRINT,
        STRATEGY_CUSTOM,
    ]


def test_asset_discovery_template_matches_plan():
    """方案第 4 节：资产发现 = httpx + subfinder。"""
    strategy = get_strategy(STRATEGY_ASSET_DISCOVERY)
    assert strategy is not None
    assert sorted(strategy.tools) == ["httpx", "subfinder"]


def test_web_fingerprint_template_lists_nuclei_as_restricted_only():
    """方案第 4 节的 ``nuclei(限制)``：只登记展示，绝不进任务。"""
    strategy = get_strategy(STRATEGY_WEB_FINGERPRINT)
    assert strategy is not None
    assert strategy.tools == ["httpx"]
    assert strategy.restricted_tools == ["nuclei"]
    # 受限工具不能同时出现在实际工具里。
    assert not set(strategy.tools) & set(strategy.restricted_tools)


def test_every_template_tool_is_internet_allowed():
    """不变量：模板里的工具必须全部允许公网，否则模板本身就是一条越权捷径。"""
    for strategy in list_strategies():
        for tool_name in strategy.tools:
            policy = get_tool_policy(tool_name)
            assert policy is not None, f"{strategy.key} 引用了未登记工具 {tool_name}"
            assert policy.internet_allowed, f"{strategy.key} 的 {tool_name} 不允许公网"


def test_strategy_to_dict_shape():
    payload = get_strategy(STRATEGY_ASSET_DISCOVERY).to_dict()
    assert set(payload) == {
        "key",
        "name",
        "description",
        "tools",
        "restricted_tools",
        "risk_level",
        "risk_label",
        # Scan Profile 的第二维：节奏（下一阶段方案第 5、6 节 Phase 3）。
        # 它必须随模板一起下发，否则前端只能靠猜或者写死一份文案。
        "pace",
        "pace_label",
    }


# ── 模板 → 工具解析 ───────────────────────────────────────


def test_resolve_defaults_to_asset_discovery():
    assert resolve_strategy_tools(None) == ["subfinder", "httpx"]
    assert resolve_strategy_tools("") == ["subfinder", "httpx"]
    assert resolve_strategy_tools("asset_discovery") == ["subfinder", "httpx"]


def test_resolve_accepts_matching_tool_list():
    """显式传了与模板一致的工具（顺序无关）应被接受。"""
    assert resolve_strategy_tools(STRATEGY_ASSET_DISCOVERY, ["httpx", "subfinder"]) == [
        "subfinder",
        "httpx",
    ]


def test_resolve_rejects_tool_not_in_template():
    """「选了资产发现却偷偷加 nmap」必须被拒，而不是静默忽略多余工具。"""
    with pytest.raises(BadRequestError) as excinfo:
        resolve_strategy_tools(STRATEGY_ASSET_DISCOVERY, ["httpx", "nmap"])
    assert excinfo.value.details["strategy_tools"] == ["subfinder", "httpx"]


def test_resolve_rejects_unknown_strategy():
    with pytest.raises(BadRequestError) as excinfo:
        resolve_strategy_tools("turbo")
    assert excinfo.value.details["supported"] == [
        STRATEGY_ASSET_DISCOVERY,
        STRATEGY_WEB_FINGERPRINT,
        STRATEGY_CUSTOM,
    ]


def test_custom_strategy_requires_explicit_tools():
    with pytest.raises(BadRequestError):
        resolve_strategy_tools(STRATEGY_CUSTOM)
    with pytest.raises(BadRequestError):
        resolve_strategy_tools(STRATEGY_CUSTOM, [])


def test_custom_strategy_passes_tools_through():
    assert resolve_strategy_tools(STRATEGY_CUSTOM, ["httpx"]) == ["httpx"]


def test_strategy_keys_are_lowercase_and_stable():
    assert set(STRATEGIES) == {
        STRATEGY_ASSET_DISCOVERY,
        STRATEGY_WEB_FINGERPRINT,
        STRATEGY_CUSTOM,
    }