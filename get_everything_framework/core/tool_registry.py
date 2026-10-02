"""工具权限元数据与扫描策略模板（公网授权测试模式体验版方案第 5、8 节）。

## 为什么需要这一层

在引入本模块之前，「哪个工具能在公网目标上跑」这件事**没有任何地方表达**：
``RUNNER_REGISTRY`` 只回答「这个工具名认不认识」，``Scope.active_scan`` 只回答
「这个范围允不允许主动扫描」。于是只要 Scope 里放了公网域名，用户就能把
``nmap`` / ``dirsearch`` 这类会对外网目标产生高强度流量的工具一起勾上。

本模块把工具按**风险等级**与**是否允许打公网**显式建模，并给出三档扫描策略模板，
让「授权公网测试模式」可以在不碰 Runner、不碰 Scope/Policy 判定的前提下，
于 Application Service 层多出一道**工具白名单**闸门。

## 与既有边界的关系

* 本模块**不判定 Scope**，也**不判定环境开关** —— 那两件事仍然只发生在
  ``core.policy`` 与 ``core.safety``；
* 本模块只回答一个问题：「这次请求选中的工具，是否允许用于公网目标」；
* 判定入口是 :func:`assert_tools_internet_allowed`，被
  ``core.application.create_authorized_public_job`` 调用，视图层不得自行比较。

## 第一阶段口径（方案第 8 节）

只放行 ``httpx`` 与 ``subfinder``；``nmap`` / ``dirsearch`` / 爆破类工具一律
``internet_allowed=False``，即使请求里显式指名也会在**创建任务之前**被拒。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.errors import BadRequestError

# ── 风险等级 ──────────────────────────────────────────────

RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"

RISK_LEVELS = (RISK_LOW, RISK_MEDIUM, RISK_HIGH)

# 风险等级的中文说明（前端与错误消息共用一份，避免两处漂移）。
RISK_LABELS = {
    RISK_LOW: "低（被动/轻量探测）",
    RISK_MEDIUM: "中（主动枚举，流量可控）",
    RISK_HIGH: "高（爆破/端口扫描，会对目标产生明显流量）",
}


@dataclass(frozen=True)
class ToolPolicy:
    """单个工具的权限元数据（方案第 5 节字段表）。

    Attributes:
        tool_name: 工具名，与 ``modules/registry.RUNNER_REGISTRY`` 的键一致。
        risk_level: ``low`` / ``medium`` / ``high``。
        internet_allowed: 是否允许用于**公网**目标。
        default_enabled: 是否在「自定义」策略里默认勾选。
        reason: 面向使用者的说明，用于解释为何不允许公网。
    """

    tool_name: str
    risk_level: str
    internet_allowed: bool
    default_enabled: bool
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "risk_level": self.risk_level,
            "risk_label": RISK_LABELS.get(self.risk_level, self.risk_level),
            "internet_allowed": self.internet_allowed,
            "default_enabled": self.default_enabled,
            "reason": self.reason,
        }


#: 17 个已注册 runner 的权限元数据。
#:
#: 公网白名单刻意只有两个（方案第 8 节）：它们都是**被动/轻量**探测 ——
#: ``subfinder`` 查的是公开数据源（证书透明度、被动 DNS 库），
#: ``httpx`` 是对「已经存在的 URL」发一次带超时的 GET。
#: 其余工具要么会爆破目录、要么会扫端口，全部留在公网白名单外。
TOOL_POLICIES: dict[str, ToolPolicy] = {
    "subfinder": ToolPolicy("subfinder", RISK_LOW, True, True),
    "httpx": ToolPolicy("httpx", RISK_LOW, True, True),
    "dnsx": ToolPolicy(
        "dnsx",
        RISK_LOW,
        False,
        False,
        reason="DNS 批量解析会产生大量查询，本阶段不开放公网",
    ),
    "assetfinder": ToolPolicy(
        "assetfinder",
        RISK_LOW,
        False,
        False,
        reason="公开数据源查询，本阶段不开放公网",
    ),
    "waybackurls": ToolPolicy(
        "waybackurls",
        RISK_LOW,
        False,
        False,
        reason="历史 URL 提取，本阶段不开放公网",
    ),
    "amass": ToolPolicy(
        "amass",
        RISK_MEDIUM,
        False,
        False,
        reason="深度枚举会同时主动与被动词询目标，本阶段不开放公网",
    ),
    "amass_intel": ToolPolicy(
        "amass_intel",
        RISK_MEDIUM,
        False,
        False,
        reason="ASN 情报收集，本阶段不开放公网",
    ),
    "enscan": ToolPolicy(
        "enscan",
        RISK_MEDIUM,
        False,
        False,
        reason="企业信息收集依赖外部数据源 Cookie，本阶段不开放公网",
    ),
    "oneforall": ToolPolicy(
        "oneforall",
        RISK_MEDIUM,
        False,
        False,
        reason="综合型工具，内部会主动请求目标，本阶段不开放公网",
    ),
    "gospider": ToolPolicy(
        "gospider",
        RISK_MEDIUM,
        False,
        False,
        reason="爬虫会持续抓取目标站点，本阶段不开放公网",
    ),
    "katana": ToolPolicy(
        "katana",
        RISK_MEDIUM,
        False,
        False,
        reason="爬虫会持续抓取目标站点，本阶段不开放公网",
    ),
    "naabu": ToolPolicy(
        "naabu",
        RISK_HIGH,
        False,
        False,
        reason="端口扫描，禁止用于公网目标",
    ),
    "nmap": ToolPolicy(
        "nmap",
        RISK_HIGH,
        False,
        False,
        reason="端口与服务扫描，禁止用于公网目标",
    ),
    "dirsearch": ToolPolicy(
        "dirsearch",
        RISK_HIGH,
        False,
        False,
        reason="目录爆破，禁止用于公网目标",
    ),
    "feroxbuster": ToolPolicy(
        "feroxbuster",
        RISK_HIGH,
        False,
        False,
        reason="递归目录爆破，禁止用于公网目标",
    ),
    "shuffledns": ToolPolicy(
        "shuffledns",
        RISK_HIGH,
        False,
        False,
        reason="DNS 字典爆破，禁止用于公网目标",
    ),
    "alterx": ToolPolicy(
        "alterx",
        RISK_HIGH,
        False,
        False,
        reason="变体字典生成，属爆破链路前置步骤，本阶段不开放公网",
    ),
}

#: 方案第 5 节提到但**本项目 runner 注册表里没有**的工具。
#: 单独登记是为了让「Web 信息收集」模板能如实展示它被限制，而不是装作它可用。
KNOWN_UNAVAILABLE_TOOLS: dict[str, ToolPolicy] = {
    "nuclei": ToolPolicy(
        "nuclei",
        RISK_MEDIUM,
        False,
        False,
        reason="模板化漏洞扫描：本阶段未接入 runner，且 internet_allowed=false",
    ),
}


def get_tool_policy(tool_name: str) -> ToolPolicy | None:
    """读取某个工具的权限元数据；未登记的工具返回 ``None``。

    ``None`` 的语义是「未知工具」——调用方必须按**不允许**处理，
    绝不能把「没登记」当成「没限制」。
    """
    return TOOL_POLICIES.get(tool_name)


def list_tool_policies() -> list[ToolPolicy]:
    """按工具名排序列出全部权限元数据（API 与前端下拉共用）。"""
    return [TOOL_POLICIES[name] for name in sorted(TOOL_POLICIES)]


def internet_allowed_tools() -> list[str]:
    """公网白名单（方案第 8 节第一阶段只放行的那些）。"""
    return sorted(
        name for name, policy in TOOL_POLICIES.items() if policy.internet_allowed
    )


def assert_tools_internet_allowed(tools: list[str]) -> None:
    """校验一组工具是否都允许用于公网目标。

    这是「授权公网测试模式」的**唯一**工具闸门，由 Application Service 调用；
    视图层不得自行实现这段比较。

    Args:
        tools: 已解析的工具名列表。

    Raises:
        BadRequestError: 名单为空、含未登记工具，或含不允许公网的工具。
    """
    if not tools:
        raise BadRequestError("必须提供至少一个工具", details={"field": "tools"})

    blocked: list[dict] = []
    unknown: list[str] = []

    for tool_name in tools:
        policy = get_tool_policy(tool_name)
        if policy is None:
            # 未登记的工具不能因为「没人给它写元数据」而被放行。
            unknown.append(tool_name)
            continue
        if not policy.internet_allowed:
            blocked.append(
                {
                    "tool_name": tool_name,
                    "risk_level": policy.risk_level,
                    "reason": policy.reason or "该工具不允许用于公网目标",
                }
            )

    if unknown:
        raise BadRequestError(
            f"以下工具没有公网权限元数据，禁止用于公网目标: {', '.join(unknown)}",
            details={"field": "tools", "unknown_tools": unknown},
        )
    if blocked:
        names = ", ".join(item["tool_name"] for item in blocked)
        raise BadRequestError(
            f"以下工具被禁止用于公网目标: {names}",
            details={
                "field": "tools",
                "blocked_tools": blocked,
                "internet_allowed_tools": internet_allowed_tools(),
            },
        )


# ── 扫描策略模板（方案第 4 节步骤 3） ─────────────────────

STRATEGY_ASSET_DISCOVERY = "asset_discovery"
STRATEGY_WEB_FINGERPRINT = "web_fingerprint"
STRATEGY_CUSTOM = "custom"


@dataclass(frozen=True)
class ScanStrategy:
    """一档扫描策略模板。

    Attributes:
        key: 模板标识（请求里传这个值）。
        name: 中文名。
        description: 面向使用者的一句话说明。
        tools: 该模板选中的工具（``custom`` 为空，由用户自选）。
        restricted_tools: 模板提到但当前**不可用**的工具（如 ``nuclei``），
            只用于前端如实展示，绝不进入任务。
        risk_level: 模板整体风险等级（取其中最高的工具）。
    """

    key: str
    name: str
    description: str
    tools: list[str] = field(default_factory=list)
    restricted_tools: list[str] = field(default_factory=list)
    risk_level: str = RISK_LOW

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "tools": list(self.tools),
            "restricted_tools": list(self.restricted_tools),
            "risk_level": self.risk_level,
            "risk_label": RISK_LABELS.get(self.risk_level, self.risk_level),
        }


#: 三档模板（方案第 4 节）。工具名**必须**在 :data:`TOOL_POLICIES` 里且
#: ``internet_allowed=True``，否则 :func:`resolve_strategy_tools` 会拒绝 ——
#: 这条不变量有测试锁定，避免以后有人往模板里塞一个禁止公网的工具。
STRATEGIES: dict[str, ScanStrategy] = {
    STRATEGY_ASSET_DISCOVERY: ScanStrategy(
        key=STRATEGY_ASSET_DISCOVERY,
        name="资产发现",
        description="被动子域枚举 + 存活探测，流量最轻，适合作为授权测试的第一步。",
        tools=["subfinder", "httpx"],
        risk_level=RISK_LOW,
    ),
    STRATEGY_WEB_FINGERPRINT: ScanStrategy(
        key=STRATEGY_WEB_FINGERPRINT,
        name="Web 信息收集",
        description="对已知目标做存活与标题/状态码/服务端识别。",
        tools=["httpx"],
        restricted_tools=["nuclei"],
        risk_level=RISK_LOW,
    ),
    STRATEGY_CUSTOM: ScanStrategy(
        key=STRATEGY_CUSTOM,
        name="自定义模式",
        description="由使用者自行勾选工具，仍受公网白名单限制。",
        tools=[],
        risk_level=RISK_LOW,
    ),
}


def get_strategy(key: str) -> ScanStrategy | None:
    """读取策略模板；未登记返回 ``None``。"""
    return STRATEGIES.get(key)


def list_strategies() -> list[ScanStrategy]:
    """按固定顺序列出策略模板。"""
    order = (STRATEGY_ASSET_DISCOVERY, STRATEGY_WEB_FINGERPRINT, STRATEGY_CUSTOM)
    return [STRATEGIES[key] for key in order]


def resolve_strategy_tools(key: str | None, tools: list[str] | None = None) -> list[str]:
    """把「策略模板 + 可选自定义工具」解析成最终工具列表。

    规则：

    * 模板缺省 → ``asset_discovery``（方案第 11 节验收用的就是这一档）；
    * ``custom`` → 必须显式给出工具，否则报错（不允许「空模板 + 空工具」落库）；
    * 非 ``custom`` → 以模板的工具为准；若调用方另外给了工具，必须与模板
      **完全一致**，否则报错（避免「选了资产发现却偷偷加了 nmap」）。

    Args:
        key: 模板标识，``None`` 视为 ``asset_discovery``。
        tools: 调用方显式给出的工具列表（可选）。

    Returns:
        list[str]: 最终工具名列表。

    Raises:
        BadRequestError: 模板不存在、自定义模式没给工具，或工具与模板不一致。
    """
    resolved_key = (key or STRATEGY_ASSET_DISCOVERY).strip().lower() or STRATEGY_ASSET_DISCOVERY
    strategy = get_strategy(resolved_key)
    if strategy is None:
        raise BadRequestError(
            f"未知的扫描策略: {key!r}",
            details={"field": "strategy", "supported": [item.key for item in list_strategies()]},
        )

    requested = [str(item).strip() for item in (tools or []) if str(item).strip()]

    if strategy.key == STRATEGY_CUSTOM:
        if not requested:
            raise BadRequestError(
                "自定义模式必须显式选择至少一个工具",
                details={"field": "tools", "internet_allowed_tools": internet_allowed_tools()},
            )
        return requested

    if requested and sorted(requested) != sorted(strategy.tools):
        raise BadRequestError(
            f"策略「{strategy.name}」只能使用 {', '.join(strategy.tools)}，"
            f"收到: {', '.join(requested)}",
            details={
                "field": "tools",
                "strategy": strategy.key,
                "strategy_tools": list(strategy.tools),
            },
        )
    return list(strategy.tools)