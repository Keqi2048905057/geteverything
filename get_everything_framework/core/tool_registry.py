"""工具权限元数据与扫描策略模板（公网授权测试模式体验版方案第 5、8 节）。

## 为什么需要这一层

在引入本模块之前，「哪个工具能在公网目标上跑」这件事**没有任何地方表达**：
``RUNNER_REGISTRY`` 只回答「这个工具名认不认识」，``Scope.active_scan`` 只回答
「这个范围允不允许主动扫描」。于是只要 Scope 里放了公网域名，用户就能把
``nmap`` / ``dirsearch`` 这类会对外网目标产生高强度流量的工具一起勾上。

本模块把工具按**风险等级**与**是否允许打公网**显式建模，并给出三档扫描策略模板，
让「授权公网测试模式」可以在不碰 Runner、不碰 Scope/Policy 判定的前提下，
于 Application Service 层多出一道**工具白名单**闸门。

## 扫描节奏（Scan Profile 的第二维，下一阶段方案第 5、6 节 Phase 3）

「引入 Scan Profile」不能只等于「换个工具组合」：同一组工具在别人的资产上
可以打得多快，是使用者真正关心的第二个问题。因此每个模板额外带一档
``pace``（见 :mod:`core.pace`），由 :func:`resolve_strategy_tools` 的姊妹函数
``core.application.create_authorized_public_job`` 解析并落进任务审计，
最终由 ``jobs.executor`` 在执行期**真正执行**（降低并发 / 请求速率 / 步骤间隔），
而不是只在页面上写一句「低频」。

节奏**不是**安全闸门：Scope、``active_scan``、``GEF_ALLOW_REAL_SCAN``、
公网工具白名单各自独立判定，本模块不参与，也不放松其中任何一条。

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
from core.pace import (
    DEFAULT_PACE,
    PACE_LABELS,
    PACE_LEVELS,
    PACE_LIGHT,
    coerce_pace,
    resolve_pace,
)

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


# ── 工具分组：Tool Registry（下一阶段方案第 8、9 节） ──────
#
# ⚠️ 字段名刻意**不叫** ``category``。本仓库里 ``category`` 已被三种不同含义占用：
#
# 1. ``storage.TOOL_DATABASES[*]["category"]`` —— **观测类别**
#    （``subdomain`` / ``url`` / ``alive`` / ``web`` / ``port``），决定结果落哪张表；
# 2. ``modules.base.BaseRunner.category`` —— 上面那件事在运行器侧的同一份值；
# 3. ``api/tools.py`` 从运行器读出的 ``category`` —— 还是第 2 条。
#
# 方案第 9 节示例里的 ``category`` 指的是**工具能力分组**（recon / service …），
# 是第四种含义。再叫 ``category``，代码里就再也说不清「按分类查结果」与
# 「按分类选工具」的区别，因此这里叫 ``tool_group``。
#
# 分组**不是**权限：它只决定界面上把工具摆在哪一栏。能不能打公网仍然只看
# ``internet_allowed``，能不能跑仍然只看 Scope / Policy / 环境开关。

GROUP_RECON = "recon"
GROUP_SERVICE = "service"
GROUP_TECH = "tech"
GROUP_CONTENT = "content"
GROUP_VULN = "vuln"
GROUP_ASSIST = "assist"


@dataclass(frozen=True)
class ToolGroup:
    """工具能力分组（方案第 8 节「工具选择中心」的栏位）。

    Attributes:
        key: 分组标识，前端按它给工具归类（**服务端下发**，前端不写死）。
        name: 中文名。
        description: 这一栏在做什么，供界面在空分组时如实说明。
    """

    key: str
    name: str
    description: str

    def to_dict(self) -> dict:
        return {"key": self.key, "name": self.name, "description": self.description}


#: 分组顺序即界面顺序（方案第 8 节五栏 + 「内容发现」）。
#:
#: 方案第 8 节的表是**示例**，其中只有 5 栏，而本项目注册表里有 17 个 runner：
#: ``dirsearch`` / ``feroxbuster`` / ``gospider`` / ``katana`` / ``waybackurls``
#: 这五个既不是资产发现也不是服务识别，硬塞进任何一栏都会误导使用者，
#: 因此补一栏「内容发现」。方案里点名的两栏（技术识别、漏洞检测）**保留**：
#: 技术识别本阶段确实没有可接入的 runner，漏洞检测只有未接入的 ``nuclei``，
#: 两栏都如实呈现为「暂无可用工具」，而不是把工具挪进去凑数。
TOOL_GROUPS: tuple[ToolGroup, ...] = (
    ToolGroup(GROUP_RECON, "资产发现", "子域 / 资产枚举，回答「目标有哪些入口」。"),
    ToolGroup(GROUP_SERVICE, "服务识别", "存活、端口与服务指纹，回答「入口上开着什么」。"),
    ToolGroup(GROUP_TECH, "技术识别", "技术栈与组件识别。本阶段暂无已接入工具。"),
    ToolGroup(GROUP_CONTENT, "内容发现", "爬虫与目录枚举，回答「站点上还有什么路径」。"),
    ToolGroup(GROUP_VULN, "漏洞检测", "模板化漏洞扫描。本阶段只登记未接入的工具。"),
    ToolGroup(GROUP_ASSIST, "辅助能力", "为上面几栏提供输入（字典、解析、变体）。"),
)

#: 分组 key → 中文名（前端与错误消息共用一份）。
TOOL_GROUP_LABELS = {group.key: group.name for group in TOOL_GROUPS}


def list_tool_groups() -> list[ToolGroup]:
    """按固定顺序列出工具分组（API 与前端栏位共用）。"""
    return list(TOOL_GROUPS)


def get_tool_group(key: str) -> ToolGroup | None:
    """读取分组元数据；未登记返回 ``None``。"""
    for group in TOOL_GROUPS:
        if group.key == key:
            return group
    return None


@dataclass(frozen=True)
class ToolPolicy:
    """单个工具的注册与权限元数据（方案第 5 节字段表 + 第 9 节 Tool Registry）。

    Attributes:
        tool_name: 工具名，与 ``modules/registry.RUNNER_REGISTRY`` 的键一致。
        risk_level: ``low`` / ``medium`` / ``high``。
        internet_allowed: 是否允许用于**公网**目标。
        default_enabled: 是否在「自定义」策略里默认勾选。
        reason: 面向使用者的说明，用于解释为何不允许公网。
        description: 这个工具**做什么**（方案第 9 节 ``description``）。
            与 ``reason`` 是两个问题：``description`` 回答「它能干什么」，
            ``reason`` 回答「为什么现在不让它打公网」。
        tool_group: :data:`TOOL_GROUPS` 里的分组 key（方案第 9 节 ``category``；
            改名理由见本文件「工具分组」一节）。
    """

    tool_name: str
    risk_level: str
    internet_allowed: bool
    default_enabled: bool
    reason: str = ""
    # 新增字段一律**追加在 reason 之后**并带默认值：本表的构造点用的是位置参数，
    # 插在中间会静默改变已有条目的字段含义。
    description: str = ""
    tool_group: str = ""

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "risk_level": self.risk_level,
            "risk_label": RISK_LABELS.get(self.risk_level, self.risk_level),
            "internet_allowed": self.internet_allowed,
            "default_enabled": self.default_enabled,
            "reason": self.reason,
            # Tool Registry：让前端能动态渲染「这一栏、这个工具、干什么用的」，
            # 而不是把 17 个工具名与说明抄一遍到 JS 里。
            "description": self.description,
            "tool_group": self.tool_group,
            "tool_group_label": TOOL_GROUP_LABELS.get(self.tool_group, self.tool_group),
        }


#: 17 个已注册 runner 的权限元数据与注册信息（方案第 5 节 + 第 9 节 Tool Registry）。
#:
#: 公网白名单刻意只有两个（方案第 8 节）：它们都是**被动/轻量**探测 ——
#: ``subfinder`` 查的是公开数据源（证书透明度、被动 DNS 库），
#: ``httpx`` 是对「已经存在的 URL」发一次带超时的 GET。
#: 其余工具要么会爆破目录、要么会扫端口，全部留在公网白名单外。
#:
#: ``description`` / ``tool_group`` 与 ``internet_allowed`` **互不影响**：
#: 前两者只用于界面把工具摆对位置、说清用途；能不能打公网仍然只看
#: ``internet_allowed``，能不能跑仍然只看 Scope / Policy / 环境开关。
#: 把某个工具挪到别的分组**不会**让它变得可用，这一点有测试锁定。
TOOL_POLICIES: dict[str, ToolPolicy] = {
    "subfinder": ToolPolicy(
        "subfinder",
        RISK_LOW,
        True,
        True,
        description="子域名发现：查询证书透明度与被动 DNS 库，不向目标发包。",
        tool_group=GROUP_RECON,
    ),
    "httpx": ToolPolicy(
        "httpx",
        RISK_LOW,
        True,
        True,
        description="HTTP 服务探测：存活、状态码、标题、服务端指纹。",
        tool_group=GROUP_SERVICE,
    ),
    "dnsx": ToolPolicy(
        "dnsx",
        RISK_LOW,
        False,
        False,
        reason="DNS 批量解析会产生大量查询，本阶段不开放公网",
        description="批量 DNS 解析：A / CNAME / 泛解析判定。",
        tool_group=GROUP_RECON,
    ),
    "assetfinder": ToolPolicy(
        "assetfinder",
        RISK_LOW,
        False,
        False,
        reason="公开数据源查询，本阶段不开放公网",
        description="子域发现：只查公开数据源（crt.sh 等）。",
        tool_group=GROUP_RECON,
    ),
    "waybackurls": ToolPolicy(
        "waybackurls",
        RISK_LOW,
        False,
        False,
        reason="历史 URL 提取，本阶段不开放公网",
        description="历史 URL 提取：从 Wayback Machine 取回目标曾出现过的路径。",
        tool_group=GROUP_CONTENT,
    ),
    "amass": ToolPolicy(
        "amass",
        RISK_MEDIUM,
        False,
        False,
        reason="深度枚举会同时主动与被动词询目标，本阶段不开放公网",
        description="深度子域枚举：被动数据源 + 主动解析，覆盖面最广也最重。",
        tool_group=GROUP_RECON,
    ),
    "amass_intel": ToolPolicy(
        "amass_intel",
        RISK_MEDIUM,
        False,
        False,
        reason="ASN 情报收集，本阶段不开放公网",
        description="ASN / 组织情报：按组织名反查归属网段与资产。",
        tool_group=GROUP_RECON,
    ),
    "enscan": ToolPolicy(
        "enscan",
        RISK_MEDIUM,
        False,
        False,
        reason="企业信息收集依赖外部数据源 Cookie，本阶段不开放公网",
        description="企业信息收集：工商、备案（ICP）、子公司等。",
        tool_group=GROUP_RECON,
    ),
    "oneforall": ToolPolicy(
        "oneforall",
        RISK_MEDIUM,
        False,
        False,
        reason="综合型工具，内部会主动请求目标，本阶段不开放公网",
        description="综合子域收集：聚合多数据源并对结果做存活确认。",
        tool_group=GROUP_RECON,
    ),
    "gospider": ToolPolicy(
        "gospider",
        RISK_MEDIUM,
        False,
        False,
        reason="爬虫会持续抓取目标站点，本阶段不开放公网",
        description="站点爬虫：抓链接、JS 文件、表单与第三方资源。",
        tool_group=GROUP_CONTENT,
    ),
    "katana": ToolPolicy(
        "katana",
        RISK_MEDIUM,
        False,
        False,
        reason="爬虫会持续抓取目标站点，本阶段不开放公网",
        description="爬虫（可解析 JS 渲染路径）：枚举可访问的端点。",
        tool_group=GROUP_CONTENT,
    ),
    "naabu": ToolPolicy(
        "naabu",
        RISK_HIGH,
        False,
        False,
        reason="端口扫描，禁止用于公网目标",
        description="端口扫描：对目标网段探测开放端口。",
        tool_group=GROUP_SERVICE,
    ),
    "nmap": ToolPolicy(
        "nmap",
        RISK_HIGH,
        False,
        False,
        reason="端口与服务扫描，禁止用于公网目标",
        description="端口与服务扫描：含服务版本识别。",
        tool_group=GROUP_SERVICE,
    ),
    "dirsearch": ToolPolicy(
        "dirsearch",
        RISK_HIGH,
        False,
        False,
        reason="目录爆破，禁止用于公网目标",
        description="目录与文件枚举：按字典请求常见路径。",
        tool_group=GROUP_CONTENT,
    ),
    "feroxbuster": ToolPolicy(
        "feroxbuster",
        RISK_HIGH,
        False,
        False,
        reason="递归目录爆破，禁止用于公网目标",
        description="递归目录爆破：发现一层就继续往下钻。",
        tool_group=GROUP_CONTENT,
    ),
    "shuffledns": ToolPolicy(
        "shuffledns",
        RISK_HIGH,
        False,
        False,
        reason="DNS 字典爆破，禁止用于公网目标",
        description="DNS 字典爆破：用字典批量解析子域，流量取决于字典大小。",
        tool_group=GROUP_RECON,
    ),
    "alterx": ToolPolicy(
        "alterx",
        RISK_HIGH,
        False,
        False,
        reason="变体字典生成，属爆破链路前置步骤，本阶段不开放公网",
        description="变体字典生成：按已知域名拼出候选子域，供爆破类工具使用。",
        tool_group=GROUP_ASSIST,
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
        description="模板化漏洞扫描：按社区模板匹配已知漏洞指纹（尚未接入）。",
        tool_group=GROUP_VULN,
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


def list_all_tool_policies() -> list[ToolPolicy]:
    """列出**全部**登记在案的条目，含未接入 runner 的（如 ``nuclei``）。

    与 :func:`list_tool_policies` 的区别：后者只给「真能跑」的工具，是执行与
    校验用的名单；本函数给界面用 —— 界面必须能显示 ``nuclei`` 被限制，
    而不是装作它不存在（方案第 4 节的 ``nuclei(限制)``）。
    """
    return list_tool_policies() + [
        KNOWN_UNAVAILABLE_TOOLS[name] for name in sorted(KNOWN_UNAVAILABLE_TOOLS)
    ]


def group_tool_policies(
    policies: list[ToolPolicy] | None = None,
) -> list[dict]:
    """按 :data:`TOOL_GROUPS` 的顺序把工具装进各自的栏位。

    这是方案第 8 节「工具选择中心」在服务端的唯一实现：前端拿到结构后**直接渲染**，
    既不用自己写一份分组表，也不会因为漏改一处而静默少显示一个工具。

    空分组**照样返回**（``tools`` 为空列表）：方案点名的「技术识别」「漏洞检测」
    本阶段确实没有可跑的工具，如实呈现比悄悄藏掉更诚实 —— 藏掉会让使用者以为
    是自己没找到，而不是「本阶段不支持」。

    Args:
        policies: 要分组的条目；``None`` 表示 :func:`list_all_tool_policies`。

    Returns:
        list[dict]: 每项 ``{key, name, description, tools}``，顺序即界面顺序。
    """
    items = list_all_tool_policies() if policies is None else list(policies)

    buckets: dict[str, list[dict]] = {group.key: [] for group in TOOL_GROUPS}
    for policy in items:
        # 分组 key 未登记（或为空）的条目不能被**静默丢弃** —— 那会让工具从界面上
        # 凭空消失。归入 ``assist`` 之外的兜底栏位不优雅，这里改为抛错，
        # 由测试在改动时就拦住（见 tests/unit/test_tool_registry.py）。
        if policy.tool_group not in buckets:
            raise ValueError(
                f"工具 {policy.tool_name} 的分组 {policy.tool_group!r} 未登记，"
                f"合法取值: {', '.join(buckets)}"
            )
        buckets[policy.tool_group].append(policy.to_dict())

    return [
        {
            "key": group.key,
            "name": group.name,
            "description": group.description,
            "tools": buckets[group.key],
        }
        for group in TOOL_GROUPS
    ]


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
    """一档扫描策略模板（Scan Profile = 工具组合 + 节奏）。

    Attributes:
        key: 模板标识（请求里传这个值）。
        name: 中文名。
        description: 面向使用者的一句话说明。
        tools: 该模板选中的工具（``custom`` 为空，由用户自选）。
        restricted_tools: 模板提到但当前**不可用**的工具（如 ``nuclei``），
            只用于前端如实展示，绝不进入任务。
        risk_level: 模板整体风险等级（取其中最高的工具）。
        pace: 该模板的**缺省节奏**（见 :mod:`core.pace`）。请求可以把它
            收紧到 ``light``，但**不能**把它放松 —— 见
            :func:`resolve_strategy_pace`。
    """

    key: str
    name: str
    description: str
    tools: list[str] = field(default_factory=list)
    restricted_tools: list[str] = field(default_factory=list)
    risk_level: str = RISK_LOW
    pace: str = DEFAULT_PACE

    def to_dict(self) -> dict:
        pace = coerce_pace(self.pace)
        return {
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "tools": list(self.tools),
            "restricted_tools": list(self.restricted_tools),
            "risk_level": self.risk_level,
            "risk_label": RISK_LABELS.get(self.risk_level, self.risk_level),
            "pace": pace,
            "pace_label": PACE_LABELS.get(pace, pace),
        }


#: 三档模板（方案第 4 节；节奏维度见下一阶段方案第 5、6 节 Phase 3）。
#: 工具名**必须**在 :data:`TOOL_POLICIES` 里且 ``internet_allowed=True``，
#: 否则 :func:`resolve_strategy_tools` 会拒绝 ——
#: 这条不变量有测试锁定，避免以后有人往模板里塞一个禁止公网的工具。
#:
#: 三档模板的节奏**一律是** ``light``：公网授权测试打的是**别人的**资产，
#: 「拿到书面授权」不等于「可以施加任意流量」。这条默认值是硬约束的一部分，
#: 请求体只能把它收紧（本来就是最紧的一档），永远无法放松
#: —— 见 :func:`resolve_strategy_pace`。
#: ``normal`` 档因此只出现在**不带模板**的历史入口（``POST /api/jobs``、
#: 首页表单），那里的目标由使用者自己提供，行为与引入本模块之前逐字节一致。
STRATEGIES: dict[str, ScanStrategy] = {
    STRATEGY_ASSET_DISCOVERY: ScanStrategy(
        key=STRATEGY_ASSET_DISCOVERY,
        name="资产发现",
        description="被动子域枚举 + 存活探测。低频档，适合作为授权公网测试的第一步。",
        tools=["subfinder", "httpx"],
        risk_level=RISK_LOW,
        pace=PACE_LIGHT,
    ),
    STRATEGY_WEB_FINGERPRINT: ScanStrategy(
        key=STRATEGY_WEB_FINGERPRINT,
        name="Web 基础检查",
        description="对已知目标做存活与标题/状态码/服务端识别，不含漏洞扫描。低频档。",
        tools=["httpx"],
        restricted_tools=["nuclei"],
        risk_level=RISK_LOW,
        pace=PACE_LIGHT,
    ),
    STRATEGY_CUSTOM: ScanStrategy(
        key=STRATEGY_CUSTOM,
        name="自定义模式",
        description="由使用者自行勾选工具，仍受公网白名单与低频节奏限制。",
        tools=[],
        risk_level=RISK_LOW,
        pace=PACE_LIGHT,
    ),
}


def get_strategy(key: str) -> ScanStrategy | None:
    """读取策略模板；未登记返回 ``None``。"""
    return STRATEGIES.get(key)


def list_strategies() -> list[ScanStrategy]:
    """按固定顺序列出策略模板。"""
    order = (STRATEGY_ASSET_DISCOVERY, STRATEGY_WEB_FINGERPRINT, STRATEGY_CUSTOM)
    return [STRATEGIES[key] for key in order]


def resolve_strategy(key: str | None) -> ScanStrategy:
    """把外部传入的模板 key 解析成模板对象。

    「缺省即资产发现」与「未知即报错」这两条口径集中在这里，避免
    :func:`resolve_strategy_tools` 与 :func:`resolve_strategy_pace` 各写一份。

    Args:
        key: 模板标识；``None`` / 空串视为 ``asset_discovery``。

    Returns:
        ScanStrategy: 命中的模板。

    Raises:
        BadRequestError: 模板未登记（``details["supported"]`` 给出合法取值）。
    """
    resolved_key = (key or STRATEGY_ASSET_DISCOVERY).strip().lower() or STRATEGY_ASSET_DISCOVERY
    strategy = get_strategy(resolved_key)
    if strategy is None:
        raise BadRequestError(
            f"未知的扫描策略: {key!r}",
            details={"field": "strategy", "supported": [item.key for item in list_strategies()]},
        )
    return strategy


def resolve_strategy_pace(key: str | None, requested: object = None) -> str:
    """解析本次任务最终生效的**节奏**档位。

    模板自带一档缺省节奏（``asset_discovery`` 是 ``light``），请求体可以
    显式再指定一次。合并规则见 :func:`core.pace.resolve_pace`：**只能收紧**，
    因此「低频资产发现」不会被一次请求悄悄改回常规档。

    Args:
        key: 模板标识（``None`` 视为 ``asset_discovery``）。
        requested: 请求体里的 ``pace``；``None`` / 空串表示不干预。

    Returns:
        str: ``light`` 或 ``normal``。

    Raises:
        BadRequestError: 模板未知，或 ``requested`` 不是合法档位。
    """
    strategy = resolve_strategy(key)
    try:
        return resolve_pace(strategy.pace, requested)
    except ValueError as exc:
        raise BadRequestError(
            str(exc),
            details={"field": "pace", "supported": list(PACE_LEVELS)},
        ) from exc


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
    strategy = resolve_strategy(key)

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