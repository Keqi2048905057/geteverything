"""工具可勾选元数据与扫描策略模板（方案第 5、8 节）—— **测试阶段新口径**。

本文件锁死的是「这个工具现在能不能被勾选」，而不是「它能不能打公网」：

* 唯一口径是**已注册且已接入 runner**：``TOOL_POLICIES[name].selectable``；
* 旧的「公网白名单恰好 ``httpx`` + ``subfinder``」口径**已取消** ——
  ``internet_allowed`` 现在只是 ``selectable`` 的 deprecated 只读别名，
  它 ``True`` 只表示「可勾选」，**不再**表示「允许打公网」；
* 「可勾选 = 注册表里有」这条等价关系用 ``selectable_tools()`` 与
  ``modules.registry.get_supported_runners()`` 互钉，而不是写死 17 个名字；
* 未接入的工具（``nuclei``）照样 ``selectable=False``，在创建任务之前被拒；
* 三档策略模板不会塞入未接入工具；
* **仍然最重要的不变量**：每个已注册 runner 都必须有元数据，且
  「没登记」绝不能被当成「没限制」—— 未登记工具一律拒绝，判定函数
  :func:`assert_tools_selectable` 的报错形状稳定（deprecated 兼容键的值
  必须与新键逐字一致）。
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
    assert_tools_selectable,
    get_strategy,
    get_tool_policy,
    internet_allowed_tools,
    list_strategies,
    list_tool_policies,
    resolve_strategy_tools,
    selectable_tools,
)


# ── 元数据完整性 ──────────────────────────────────────────


def test_every_registered_runner_has_policy_metadata():
    """注册表里的每个 runner 都必须在 ``TOOL_POLICIES`` 里有条目。

    缺条目时的行为是「拒绝」（安全的方向，见 :func:`assert_tools_selectable`），
    但这里仍然要求补齐 —— 否则新增工具的人不会意识到自己需要做一次风险判断，
    而这个工具在界面上会以「未登记」的身份被拒，看起来像是功能坏了。
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
        # 不可勾选的工具**必须**给出 reason；反过来，可勾选的高风险工具也仍然
        # 要给出「会对目标产生什么流量」的提示（见本文件另一条参数化用例）。
        # 这里锁的是「不许把 reason 删空之后只靠 selectable 蒙混过去」。
        assert policy.reason or policy.selectable, (
            f"{policy.tool_name} 不可勾选却没有给出原因，使用者无法理解为何被拒"
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
        # 新口径字段：这个工具是否已注册且已接入，因而可被勾选。
        "selectable",
        # Deprecated 兼容键：旧前端仍会读它，**值恒等于 selectable**。
        # 它不再表示「允许打公网」—— 那个语义已取消，见模块 docstring。
        "internet_allowed",
        "default_enabled",
        "reason",
        # Tool Registry（方案第 9 节）：用途说明与能力分组。
        "description",
        "tool_group",
        "tool_group_label",
    }
    assert payload["selectable"] is True
    # 两个键必须同值：只改一个会让旧客户端读到与新版不一致的答案。
    assert payload["internet_allowed"] is payload["selectable"]
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
    """分组只决定界面摆位，**不得**影响可勾选性。

    这是防「把工具挪个栏位就以为它变得（不）可用了」的回归：
    分组的增删改都不应该让可勾选名单发生变化。

    新口径下这条守卫比原来更强 —— 名单不再写死 ``["httpx", "subfinder"]``，
    而是要求「已接入 = 全部已注册」，因此任何人往 ``TOOL_POLICIES`` 里加条目、
    或把某个条目挪到别的分组，都必须让这个等式继续成立。
    """
    from modules.registry import get_supported_runners

    assert selectable_tools() == sorted(TOOL_POLICIES)
    for name in TOOL_POLICIES:
        assert get_tool_policy(name).selectable is True, f"{name} 已注册却不可勾选"
    # 名单只从已注册工具里取：未接入的条目不会因为「挪个分组」被算进来。
    assert set(selectable_tools()) <= set(get_supported_runners())


def test_selectable_is_derived_from_the_registry():
    """「可勾选 = 注册表里有且已接入」这条唯一口径。

    ``selectable_tools()`` 必须与 ``modules.registry.get_supported_runners()``
    **完全相等**：既不能漏掉一个已接入的工具（使用者看不到它），
    也不能多出一个没接进 runner 的名字（点了会失败）。
    比写死 17 个名字更稳：新增 runner 时这条会自动跟着收紧。
    """
    from modules.registry import get_supported_runners

    assert set(selectable_tools()) == set(get_supported_runners())
    assert selectable_tools() == get_supported_runners()


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


# ── 可勾选名单：测试阶段新口径 ─────────────────────────────


def test_selectable_tools_covers_every_registered_runner():
    """旧的「公网白名单恰好 httpx + subfinder」口径**已取消**。

    现在可勾选名单 = 全部已注册 runner：工具是否可选不再取决于「公网 / 内网」，
    使用者自己负责目标的合法性；闸门只回答「这个工具是否已接入」。
    """
    from modules.registry import get_supported_runners

    registered = get_supported_runners()
    assert selectable_tools() == registered
    assert len(selectable_tools()) == len(registered) > 0


@pytest.mark.parametrize(
    "tool_name",
    ["nmap", "naabu", "dirsearch", "feroxbuster", "shuffledns", "amass", "katana", "gospider"],
)
def test_high_risk_tools_are_now_selectable_but_not_default_enabled(tool_name):
    """高风险工具曾经是**闸门**，现在只是**提示**（本轮保留的要求）。

    它们可以被勾选（``selectable=True``），但：

    * ``default_enabled=False`` —— 页面不替使用者先勾上，必须自己动手；
    * ``reason`` **非空** —— 这是「现在只是提示」这句话的载体。闸门拆掉之后，
      使用者唯一还能看到的警告就是这个字段；把它删空，高风险工具就变成了
      一个没有任何风险说明的普通复选框。
    """
    policy = get_tool_policy(tool_name)
    assert policy is not None
    assert policy.selectable is True, f"{tool_name} 已接入，应当可勾选"
    assert policy.default_enabled is False, f"{tool_name} 不该被默认勾上"
    assert policy.reason, f"{tool_name} 是高风险工具，必须保留风险提示文案"


def test_nuclei_is_registered_as_restricted_but_unavailable():
    """方案第 4 节提到 ``nuclei(限制)``：本项目 runner 里没有它，必须如实登记。"""
    from modules.registry import get_supported_runners

    assert "nuclei" not in get_supported_runners()
    assert "nuclei" in KNOWN_UNAVAILABLE_TOOLS
    assert KNOWN_UNAVAILABLE_TOOLS["nuclei"].selectable is False
    # 未接入必须把理由写给使用者看：界面靠它解释「为什么这个工具点不动」。
    # 「未接入」这三个字是使用者能看懂的唯一线索，不能只剩一个 False。
    assert "未接入" in KNOWN_UNAVAILABLE_TOOLS["nuclei"].reason


# ── 判定函数 ──────────────────────────────────────────────


def test_assert_allows_default_tools():
    """默认勾选的两个轻量工具当然应当通过闸门（最小正例）。"""
    assert_tools_selectable(["httpx"])
    assert_tools_selectable(["subfinder", "httpx"])


@pytest.mark.parametrize("tool_name", ["nmap", "dirsearch", "naabu", "feroxbuster", "katana"])
def test_assert_allows_every_registered_runner(tool_name):
    """已接入的工具（含高风险）现在**能通过**闸门，单独与混搭都不报错。

    过去这条位置上是 ``test_assert_rejects_blocked_tool`` —— 那时 ``nmap`` /
    ``dirsearch`` / ``naabu`` 会被拒。闸门口径变了，但守卫不能就这么消失：
    它现在钉的是「闸门不许**过度**拦截」—— 使用者勾了已接入的工具却被拒，
    与闸门形同虚设有同样的危害（前者让人以为功能坏了）。
    """
    assert_tools_selectable([tool_name])
    assert_tools_selectable(["httpx", tool_name])


def test_assert_rejects_the_unwired_tool(monkeypatch):
    """未接入的工具必须在**创建任务之前**被拒，并给出可操作的信息。

    这条替代了旧的 ``test_assert_rejects_blocked_tool``：旧版拒的是「不允许
    打公网」的工具，新版拒的是「已登记但没接进 runner」的工具。
    报错形状是前端与脚本的契约，因此逐项钉死：

    * 可用工具（``httpx``）**不得**被一起报出来 —— 它没问题；
    * ``unavailable_tools`` 只列真正卡住的那个工具；
    * deprecated 兼容键 ``blocked_tools`` / ``internet_allowed_tools``
      必须与新键**同值**（旧客户端读它们不能读到另一份答案）；
    * 下发的可勾选名单不含那个未接入的工具。

    ⚠️ **这里用替身条目而不是真的往请求里塞 ``nuclei``**，原因是当前源码
    （``core/tool_registry.py:get_tool_policy``）**只读 ``TOOL_POLICIES``**，
    而 ``nuclei`` 登记在 ``KNOWN_UNAVAILABLE_TOOLS`` 里 —— 于是
    ``assert_tools_selectable(["nuclei"])`` 走的是 ``unknown_tools`` 分支
    （「未登记，禁止使用」），``selectable=False`` 那条 ``unavailable_tools``
    分支**不可达**。两者都拒绝 ``nuclei``，安全性没有缺口，但报错文案对使用者
    是误导（它明明登记在案，却说「未登记」）。这属于源码问题，本轮不改源码，
    详见交付报告；测试用替身把该分支的形状钉住，等源码修好后换成真名即可。
    """
    from core.tool_registry import TOOL_POLICIES, ToolPolicy

    unwired = "stub-unwired-tool"
    monkeypatch.setitem(
        TOOL_POLICIES,
        unwired,
        ToolPolicy(
            unwired,
            "low",
            False,
            False,
            reason="未接入 runner，测试替身",
            description="替身：验证未接入分支的报错形状。",
            tool_group="recon",
        ),
    )

    with pytest.raises(BadRequestError) as excinfo:
        assert_tools_selectable(["httpx", unwired])

    error = excinfo.value
    assert error.http_status == 400
    assert error.details["field"] == "tools"

    unavailable = error.details["unavailable_tools"]
    assert [item["tool_name"] for item in unavailable] == [unwired]
    assert unavailable[0]["reason"], "被拒的工具必须给出原因"

    # Deprecated 兼容键：名字旧，值必须与新键逐字一致。
    assert error.details["blocked_tools"] == unavailable
    assert error.details["internet_allowed_tools"] == error.details["selectable_tools"]

    # 名单里给出全部可勾选工具，使用者才知道该换成什么。
    assert error.details["selectable_tools"] == selectable_tools()
    assert unwired not in error.details["selectable_tools"]
    assert "httpx" in error.details["selectable_tools"]
    # httpx 本身可用，不该被一起报出来。
    assert "httpx" not in [item["tool_name"] for item in unavailable]


def test_nuclei_is_rejected_as_unwired_not_as_unregistered():
    """``nuclei`` 被拒的理由必须是「**已登记但未接入**」，不是「未登记」。

    这两句话对使用者是**完全不同的结论**：

    * 「未登记」= 这个工具名不存在（拼错了？还是根本没这个东西？）；
    * 「未接入」= 它登记在案、界面上也看得见，只是本阶段没有 runner。

    界面正是靠后者显示「未接入」并置灰（``restricted_tools``），因此判定函数
    必须**先查** :data:`KNOWN_UNAVAILABLE_TOOLS` 再下「未登记」的结论。

    这条守的是一个曾经真实存在的结构性问题：``nuclei`` 住在
    ``KNOWN_UNAVAILABLE_TOOLS`` 里，而 ``get_tool_policy`` 只查 ``TOOL_POLICIES``，
    于是早期实现把它误报成「未登记」，错误消息与界面自相矛盾。
    """
    with pytest.raises(BadRequestError) as excinfo:
        assert_tools_selectable(["httpx", "nuclei"])

    error = excinfo.value
    assert error.http_status == 400
    assert [item["tool_name"] for item in error.details["unavailable_tools"]] == ["nuclei"]
    assert [item["tool_name"] for item in error.details["blocked_tools"]] == ["nuclei"]
    assert "unknown_tools" not in error.details, "nuclei 被误判成未登记的工具了"
    assert error.details["unavailable_tools"][0]["reason"], "未接入必须给出原因"
    # 无论是哪条分支，nuclei 都不在可勾选名单里。
    assert "nuclei" not in selectable_tools()
    assert "nuclei" not in error.details["selectable_tools"]


def test_assert_rejects_unknown_tool():
    """未登记工具必须被拒 —— 「没人写元数据」不等于「不受限」。

    这是本模块**最重要的不变量**：口径从「公网白名单」换成「是否已接入」之后，
    唯一没有换的就是这一条 —— 判定只认 ``TOOL_POLICIES``，查不到就拒绝，
    绝不能因为「这个名字没有元数据」而放行。
    """
    with pytest.raises(BadRequestError) as excinfo:
        assert_tools_selectable(["some-new-tool"])
    assert excinfo.value.http_status == 400
    assert excinfo.value.details["unknown_tools"] == ["some-new-tool"]


def test_assert_rejects_empty_list():
    with pytest.raises(BadRequestError) as excinfo:
        assert_tools_selectable([])
    assert excinfo.value.http_status == 400
    assert excinfo.value.details["field"] == "tools"


def test_deprecated_aliases_point_to_the_new_implementations(monkeypatch):
    """deprecated 别名只是**薄别名**，不是第二份实现。

    保留旧函数名是为了让尚未升级的调用方不至于 ``ImportError``；
    但两条路径必须给出**字面一致**的结果 —— 一旦别名自己长出一份判定，
    就会出现「同一个工具，两个入口两个答案」。

    未登记分支（``nuclei`` 当前就走这条）与未接入分支（用替身触发）**都**要比，
    否则别名只在一条分支上被证明等价，另一条分支悄悄分叉也测不出来。

    新代码一律用 ``selectable_tools`` / ``assert_tools_selectable``。
    """
    from core.tool_registry import TOOL_POLICIES, ToolPolicy

    assert internet_allowed_tools() == selectable_tools()
    assert assert_tools_internet_allowed is not None

    # ① 未登记分支：nuclei 目前落在 ``unknown_tools``（见上一条用例的说明）。
    with pytest.raises(BadRequestError) as new_exc:
        assert_tools_selectable(["nuclei"])
    with pytest.raises(BadRequestError) as old_exc:
        assert_tools_internet_allowed(["nuclei"])

    assert type(old_exc.value) is type(new_exc.value)
    assert old_exc.value.message == new_exc.value.message
    assert old_exc.value.details == new_exc.value.details

    # ② 未接入分支：换个替身条目，别名同样必须逐字一致。
    unwired = "stub-unwired-alias"
    monkeypatch.setitem(
        TOOL_POLICIES,
        unwired,
        ToolPolicy(unwired, "low", False, False, reason="未接入 runner（替身）", tool_group="recon"),
    )
    with pytest.raises(BadRequestError) as new_exc:
        assert_tools_selectable([unwired])
    with pytest.raises(BadRequestError) as old_exc:
        assert_tools_internet_allowed([unwired])

    assert "unavailable_tools" in new_exc.value.details, "替身应当触发未接入分支"
    assert old_exc.value.message == new_exc.value.message
    assert old_exc.value.details == new_exc.value.details


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


def test_every_template_tool_is_selectable():
    """不变量：模板里的工具必须**都已接入**，否则模板本身就是一条越权捷径。

    ``resolve_strategy_tools`` 会放行模板自带的工具列表，因此模板是唯一一处
    「绕过使用者勾选、直接把工具塞进任务」的入口。它一旦写了未接入的名字
    （例如往「Web 基础检查」里加 ``nuclei``），使用者点一下模板就能创建一个
    跑不起来的任务 —— 这条守卫就是拦这个的。
    """
    for strategy in list_strategies():
        for tool_name in strategy.tools:
            policy = get_tool_policy(tool_name)
            assert policy is not None, f"{strategy.key} 引用了未登记工具 {tool_name}"
            assert policy.selectable, f"{strategy.key} 的 {tool_name} 尚未接入"


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