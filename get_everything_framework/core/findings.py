"""任务结果派生层（下一阶段体验优化方案 Phase 4「结果体验」，纯函数）。

**本模块回答一个问题**：这个任务到底看到了什么？

它把 :mod:`core.assets` 两层原始数据（``assets`` + ``observations``）整理成
四段人类可读的结果 —— 正是方案第 6 节 Phase 4 原文要求的那四段：

* **发现资产** —— 按类型分组的资产清单；
* **服务**     —— 「哪台主机上出现了什么协议 / 端口」；
* **技术栈**   —— Web 服务器 / 页面识别出的组件 / CDN；
* **风险提示** —— **可观察事实**，供人工决定要不要进一步看。

── 关于「风险信息」的口径（改代码前必须读完这一段） ────────────────

本框架**不做漏洞扫描**，这不是「暂时没做」，而是当前代码库的事实：

* ``core/tool_registry.py:KNOWN_UNAVAILABLE_TOOLS`` 里的 ``nuclei`` 不在
  ``RUNNER_REGISTRY`` 中，公网工具白名单只有 ``subfinder`` + ``httpx``；
* 全仓没有任何 CVE / CVSS / 严重级别（severity）数据表、字段或解析器。

因此这里**不可能**给出漏洞结论，本模块也刻意不产出任何「危险度」字段：
:data:`RISK_LEVELS` 只有 ``info`` / ``notice`` / ``attention``，语义是
「**值不值得人工看一眼**」而不是「有多危险」。``risk_hints`` 里每一条都是
**从已有观测里读出来的事实**（明文 HTTP、5xx、默认欢迎页标题……），
不是判定结果。所以：

* **没有风险提示 ≠ 没有漏洞** —— 只代表「这次采集到的数据里没有可解释的异常」；
* 只跑 ``subfinder`` + ``httpx`` 时，覆盖范围本身就很窄。

:data:`DISCLAIMER_NOTICE` 与 :data:`MOCK_NOTICE` 两句必须随结果一起下发，
界面要显示出来；否则「风险提示：无」会被读成「这个站是安全的」。

**边界**：本模块是纯函数 —— 不碰 sqlite、不碰 Flask、不读配置、不发网络请求。
别名表复用 :data:`core.assets.ATTRIBUTE_ALIASES`（只 import 那一个常量），
不在这里维护第二份：两张表一旦漂移，「同一个 nginx 在 diff 里叫 webserver、
在结果页叫 tech」这类无法解释的差异就会复活，而 ``core/assets.py`` 的注释
已经记过一次同样的 bug。
"""

from __future__ import annotations

from typing import Any, Iterable
from urllib.parse import urlsplit

from core.assets import ATTRIBUTE_ALIASES

# ── 截断上限（一次任务不允许把响应撑成几十 MB） ─────────────

MAX_ASSET_ITEMS = 200
MAX_SERVICE_ITEMS = 200
MAX_TECHNOLOGY_ITEMS = 50
MAX_RISK_HINT_ITEMS = 20
MAX_EVIDENCE_PER_HINT = 5

# ── 显示文案：一律由服务端下发 ─────────────────────────────
#
# 为什么文案要放在服务端而不是前端：Phase 3 已经吃过一次亏 ——
# `scan_center.js` 里写死一份节奏说明，后端改了措辞页面还停在上一个版本
# （`test_scan_center_js_does_not_hardcode_pace_wording` 就是为它立的）。
# 同一条理由在这里成立：类型名 / 类别名 / 提示级别名都只在这里定义一份，
# 前端拿到的是 `*_label` 字段，不维护第二份对照表。

#: 资产类型 → 中文显示名（键名来自 :data:`core.canonical.ASSET_TYPES`）。
ASSET_TYPE_LABELS = {
    "subdomain": "子域",
    "host": "主机",
    "ip": "IP",
    "cidr": "网段",
    "url": "URL",
    "port": "端口",
    "service": "服务",
}

#: 服务条目的类别 → 中文显示名。
SERVICE_KIND_LABELS = {
    "http": "HTTP 端点",
    "tcp": "TCP 端口",
    "service": "服务标识",
}

#: 技术栈条目的类别 → 中文显示名。``webserver`` 与 ``tech`` 必须分开：
#: 前者是「谁在应答 HTTP」，后者是「页面里识别出的组件」，合并会让人以为
#: ``nginx`` 与 ``Nginx`` 是两条不同的发现。
TECHNOLOGY_KIND_LABELS = {
    "webserver": "Web 服务器",
    "tech": "组件 / 框架",
    "cdn": "CDN",
}


def asset_type_label(asset_type: Any) -> str:
    """资产类型的中文显示名（未知类型原样返回，不吞掉信息）。"""
    key = _text(asset_type)
    return ASSET_TYPE_LABELS.get(key, key or "—")


# ── 风险提示的「级别」 ────────────────────────────────────
#
# 刻意不用 low / medium / high 这类**危险度**词：本框架没有漏洞扫描，
# 用危险度分级等于在暗示「我们评估过危险程度」。这三个值是
# 「要不要人工看一眼」的三档。
RISK_LEVELS = ("info", "notice", "attention")

RISK_LEVEL_LABELS = {
    "info": "信息",
    "notice": "可留意",
    "attention": "建议人工确认",
}

#: 步骤的**终态失败**状态。``pending`` / ``running`` 不在其中 ——
#: 任务还在跑时把「未完成」报成「覆盖不完整」是错的。
STEP_FAILURE_STATUSES = ("failed", "timeout")

#: 响应 ``title`` 命中这些特征时视为「中间件默认欢迎页」（小写子串匹配）。
DEFAULT_PAGE_MARKERS = (
    "iis windows server",
    "welcome to nginx",
    "apache2 ubuntu default page",
    "apache http server test page",
    "it works!",
    "default web site page",
    "welcome to openresty",
)

#: 响应 ``title`` 命中这些特征时视为「目录列表」（小写子串匹配）。
DIRECTORY_LISTING_MARKERS = ("index of /", "directory listing for")

#: **常驻**免责声明：它说的是本框架的**能力边界**，而不是这次任务的结果，
#: 因此每一次结果都带上（不是「零提示时才补一句」）。
#:
#: 为什么要常驻而不是只在零提示时下发：真实链路里零提示几乎从不出现 ——
#: 只跑 subfinder + httpx 时，光是「有子域没做 HTTP 探测」就会产生一条
#: ``unprobed_hosts``。若只在零提示时才算，那么「有 3 条风险提示」的那次
#: 反而拿不到「这些不是漏洞」的说明，而那恰恰是最需要它的时候。
#:
#: 写在常量里而不是散在前端，是为了让 API、页面与测试引用**同一份**文案，
#: 避免三处各说一套。
DISCLAIMER_NOTICE = (
    "本框架不做漏洞扫描（没有 nuclei，也没有 CVE / 严重级别数据）。"
    "下面每一条风险提示都是从本次采集到的数据里读出来的可观察事实，"
    "不是漏洞结论 —— 因此「没有提示」不等于目标没有问题，"
    "而「有提示」也不等于发现了漏洞。"
)

#: mock（本地验证）模式专属说明。必须说清「哪些段是空的、为什么」，
#: 否则页面会渲染成「扫了但什么都没有」，而这与「工具失败」看起来一模一样。
MOCK_NOTICE = (
    "本次是 mock（本地验证）模式：不联网、不调用任何工具。结果行仍会落成资产，"
    "但没有结构化观测属性，所以「服务 / 技术栈 / 风险提示」这三段不会有内容。"
    "这是预期行为，不是采集失败。"
)

#: 面向用户的文案里**不允许**出现 Markdown 强调标记（``**``）。
#:
#: 前端用 ``textContent`` 渲染（不拼 innerHTML，避免 XSS），因此 ``**`` 会
#: 原样显示成星号 —— 页面上会出现「本框架**不做漏洞扫描**」这种半成品排版。
#: 强调只写在 docstring / 注释里，运行时字符串一律是纯文本。
#: ``test_findings.py`` 会遍历全部运行时文案把这条钉住。
MARKDOWN_MARKER = "**"


def _text(value: Any) -> str:
    """任意值 → 去空白字符串（``None`` → ``""``）。"""
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return ", ".join(piece for piece in (_text(item) for item in value) if piece)
    return str(value).strip()


def _as_list(value: Any) -> list[str]:
    """把 ``tech`` / ``cdn`` 这类字段统一成字符串列表。

    它们可能是列表（httpx 原生 JSON）也可能是逗号分隔的字符串
    （``"Nginx, PHP"``）。不切分就会把「Nginx, PHP」当成一个技术栈名字。
    """
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        return [piece for piece in (_text(item) for item in value) if piece]
    return [piece for piece in (part.strip() for part in _text(value).split(",")) if piece]


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def canonical_attributes(data: Any) -> dict[str, Any]:
    """按 :data:`core.assets.ATTRIBUTE_ALIASES` 归一键名（每个键取第一个非空值）。

    与 ``core.assets._canonical_attributes`` 同一口径：别名同时出现时取
    **第一个非空值**，而不是让后面的静默覆盖前面的。
    """
    canonical: dict[str, Any] = {}
    if not isinstance(data, dict):
        return canonical
    for key, value in data.items():
        name = ATTRIBUTE_ALIASES.get(str(key).lower())
        if name is None:
            continue
        if value is None or value == "" or value == [] or value == {}:
            continue
        if name not in canonical:
            canonical[name] = value
    return canonical


def _section(items: list, total: int, *, extra: dict | None = None) -> dict:
    """四段结果的**统一形状**：``{total, items, truncated, ...}``。

    统一形状是刻意的：前端只写一次渲染函数，测试也只锁一种结构；
    各段自己长一个样子是「看一眼就知道会漂移」的设计。
    """
    payload: dict[str, Any] = {"total": total, "items": items, "truncated": total > len(items)}
    if extra:
        payload.update(extra)
    return payload


class _Fact:
    """一条观测被读成「事实」之后的形状（模块内部使用）。

    ``observations`` 表**没有** ``value`` 列 —— 值存在资产行上（这是两层模型的
    结果：``Asset`` 存身份，``Observation`` 存「什么时候、被谁、看到了什么属性」）。
    因此这里必须把资产行一起收进来：只看观测行会拿到一串空值，
    服务段与技术栈段会静默变成空列表，而不是报错。
    """

    __slots__ = (
        "asset_id",
        "asset_type",
        "value",
        "url",
        "host",
        "status_code",
        "title",
        "webserver",
        "technologies",
        "cdn",
        "source_tool",
        "observed_at",
    )

    def __init__(self, observation: dict, asset: dict | None = None) -> None:
        raw = observation.get("data")
        data = raw if isinstance(raw, dict) else {}
        canonical = canonical_attributes(data)

        self.asset_id = _text(observation.get("asset_id"))
        self.asset_type = _text((asset or {}).get("type"))
        # 值优先取观测行（未来若观测自带 value 就用它），否则回落到资产行。
        self.value = _text(observation.get("value") or (asset or {}).get("value"))
        self.url = self.value if self.value.lower().startswith(("http://", "https://")) else ""
        self.host = _text(urlsplit(self.url).hostname) if self.url else ""
        self.status_code = _int_or_none(canonical.get("status_code"))
        self.title = _text(canonical.get("title"))
        self.webserver = _text(canonical.get("webserver"))
        self.technologies = _as_list(canonical.get("technologies"))
        # ``cdn`` 刻意不在 ATTRIBUTE_ALIASES 里（它不参与 diff），
        # 但它确实是技术栈的一部分，所以这里从原始 data 单独读一次。
        self.cdn = _text(data.get("cdn"))
        self.source_tool = _text(observation.get("source_tool"))
        self.observed_at = _text(observation.get("observed_at"))

    def evidence(self) -> dict:
        return {
            "value": self.value,
            "asset_id": self.asset_id or None,
            "source_tool": self.source_tool or None,
            "observed_at": self.observed_at or None,
        }


# ── 第 1 段：发现资产 ─────────────────────────────────────


def _asset_items(assets: list[dict], facts: list[_Fact]) -> tuple[list[dict], dict[str, int]]:
    """发现资产清单 + 按类型计数。"""
    tools: dict[str, list[str]] = {}
    for fact in facts:
        if not fact.asset_id or not fact.source_tool:
            continue
        bucket = tools.setdefault(fact.asset_id, [])
        if fact.source_tool not in bucket:
            bucket.append(fact.source_tool)

    by_type: dict[str, int] = {}
    items: list[dict] = []
    for asset in assets:
        asset_id = _text(asset.get("id") or asset.get("asset_id"))
        asset_type = _text(asset.get("type"))
        if asset_type:
            by_type[asset_type] = by_type.get(asset_type, 0) + 1
        items.append(
            {
                "asset_id": asset_id,
                "type": asset_type,
                "type_label": asset_type_label(asset_type),
                "value": asset.get("value"),
                "status": asset.get("status"),
                "confidence": asset.get("confidence"),
                "first_seen": asset.get("first_seen"),
                "last_seen": asset.get("last_seen"),
                "source_tools": tools.get(asset_id, []),
            }
        )
    items.sort(key=lambda item: (item["type"], str(item["value"] or "")))
    return items, by_type


# ── 第 2 段：服务 ─────────────────────────────────────────


def _service_items(assets: list[dict], facts: list[_Fact]) -> list[dict]:
    """「哪台主机上出现了什么协议 / 端口」。

    两条来源，都来自**已经采集到的**数据，不做任何端口猜测或存活性推断：

    * URL 观测（``httpx``）—— ``scheme + host + port``，缺省端口按协议补齐；
    * ``port`` / ``service`` 类型资产（``naabu`` / ``nmap`` 等；
      **它们不在公网白名单里**，但本机任务可能出现）。
    """
    rows: dict[str, dict] = {}

    def _touch(key: str, **fields: Any) -> None:
        row = rows.get(key)
        if row is None:
            kind = fields.get("kind") or "tcp"
            row = {
                "key": key,
                "label": fields.get("label") or key,
                "kind": kind,
                "kind_label": SERVICE_KIND_LABELS.get(kind, kind),
                "host": fields.get("host") or "",
                "port": fields.get("port"),
                "scheme": fields.get("scheme") or "",
                "sources": [],
                "asset_ids": [],
            }
            rows[key] = row
        tool = fields.get("source_tool")
        if tool and tool not in row["sources"]:
            row["sources"].append(tool)
        asset_id = fields.get("asset_id")
        if asset_id and asset_id not in row["asset_ids"]:
            row["asset_ids"].append(asset_id)

    for fact in facts:
        if not fact.url:
            continue
        parts = urlsplit(fact.url)
        scheme = (parts.scheme or "").lower()
        host = (parts.hostname or "").lower()
        if not scheme or not host:
            continue
        port = parts.port
        if port is None:
            port = {"http": 80, "https": 443}.get(scheme)
        if port is None:
            continue
        key = f"{scheme}://{host}:{port}"
        _touch(
            key,
            label=f"{scheme.upper()} · {host}:{port}",
            kind="http",
            host=host,
            port=port,
            scheme=scheme,
            source_tool=fact.source_tool,
            asset_id=fact.asset_id,
        )

    for asset in assets:
        asset_type = _text(asset.get("type"))
        value = _text(asset.get("value"))
        asset_id = _text(asset.get("id"))
        if not value:
            continue
        if asset_type == "port":
            host, _, port_text = value.rpartition(":")
            _touch(
                f"tcp://{value}",
                label=f"TCP · {value}",
                kind="tcp",
                host=host,
                port=_int_or_none(port_text),
                asset_id=asset_id,
            )
        elif asset_type == "service":
            _touch(
                f"service://{value}",
                label=f"服务 · {value}",
                kind="service",
                asset_id=asset_id,
            )

    return sorted(rows.values(), key=lambda row: (row["kind"], row["label"]))


# ── 第 3 段：技术栈 ───────────────────────────────────────


def _technology_items(facts: list[_Fact]) -> list[dict]:
    """Web 服务器 / 页面识别出的组件 / CDN。

    ``webserver`` 与 ``technologies`` 是两件事，所以用 ``kind`` 分开：
    前者是「谁在应答 HTTP」，后者是「页面里识别出的组件」，``cdn`` 是
    「前面站着谁」。合并展示会让人以为 ``nginx`` 与 ``Nginx`` 是两条不同发现。
    """
    rows: dict[tuple[str, str], dict] = {}

    def _touch(name: str, kind: str, fact: _Fact) -> None:
        key = (kind, name)
        row = rows.get(key)
        if row is None:
            row = {"name": name, "kind": kind, "kind_label": TECHNOLOGY_KIND_LABELS.get(kind, kind),
                   "count": 0, "hosts": [], "sources": []}
            rows[key] = row
        row["count"] += 1
        if fact.host and fact.host not in row["hosts"]:
            row["hosts"].append(fact.host)
        if fact.source_tool and fact.source_tool not in row["sources"]:
            row["sources"].append(fact.source_tool)

    for fact in facts:
        for name in _as_list(fact.webserver):
            _touch(name, "webserver", fact)
        for name in fact.technologies:
            _touch(name, "tech", fact)
        for name in _as_list(fact.cdn):
            _touch(name, "cdn", fact)

    return sorted(rows.values(), key=lambda row: (-int(row["count"]), row["kind"], row["name"].lower()))


# ── 第 4 段：风险提示（可观察事实，不是漏洞判定） ──────────


def _hint(code: str, level: str, title: str, detail: str) -> dict:
    return {
        "code": code,
        "level": level,
        "level_label": RISK_LEVEL_LABELS.get(level, level),
        "title": title,
        "detail": detail,
        "count": 0,
        "evidence": [],
    }


def _add_evidence(hint: dict, value: Any, *, asset_id: Any = None,
                  source_tool: Any = None, observed_at: Any = None) -> None:
    hint["count"] += 1
    if len(hint["evidence"]) < MAX_EVIDENCE_PER_HINT:
        hint["evidence"].append(
            {
                "value": value,
                "asset_id": _text(asset_id) or None,
                "source_tool": _text(source_tool) or None,
                "observed_at": _text(observed_at) or None,
            }
        )


def _risk_hint_items(facts: list[_Fact], assets: list[dict], steps: list[dict]) -> list[dict]:
    """从**已有观测**里读出「值得人工看一眼」的事实（不是漏洞判定）。"""
    hints: dict[str, dict] = {}

    def bucket(code: str, level: str, title: str, detail: str) -> dict:
        hint = hints.get(code)
        if hint is None:
            hint = _hint(code, level, title, detail)
            hints[code] = hint
        return hint

    for fact in facts:
        if fact.url.lower().startswith("http://"):
            _add_evidence(
                bucket(
                    "plain_http",
                    "notice",
                    "存在未加密的 HTTP 端点",
                    "该端点是 http://（明文）。链路上的内容可被读取 —— 若同一主机另有 "
                    "HTTPS 端点，建议对比后再决定是否需要保留明文入口。",
                ),
                fact.value,
                asset_id=fact.asset_id,
                source_tool=fact.source_tool,
                observed_at=fact.observed_at,
            )

        code = fact.status_code
        if code is not None:
            if code >= 500:
                _add_evidence(
                    bucket(
                        "server_error",
                        "attention",
                        "目标自身返回 5xx",
                        "服务端报错。这通常是目标侧状况（配置或后端故障），值得看一眼；"
                        "它不是本框架发现的漏洞。",
                    ),
                    fact.value,
                    asset_id=fact.asset_id,
                    source_tool=fact.source_tool,
                    observed_at=fact.observed_at,
                )
            elif code in (401, 403):
                _add_evidence(
                    bucket(
                        "access_control_present",
                        "info",
                        "端点受访问控制保护",
                        "响应 401 / 403，说明它挡住了。本框架不做认证绕过、口令猜测"
                        "或越权测试，这里只记录「它挡住了」这一事实。",
                    ),
                    fact.value,
                    asset_id=fact.asset_id,
                    source_tool=fact.source_tool,
                    observed_at=fact.observed_at,
                )
            elif 300 <= code < 400:
                _add_evidence(
                    bucket(
                        "redirect_not_followed",
                        "info",
                        "存在未跟随的跳转",
                        "httpx 默认不跟随跳转，因此这里看到的不是最终页面："
                        "标题与状态码都只属于这一跳。",
                    ),
                    fact.value,
                    asset_id=fact.asset_id,
                    source_tool=fact.source_tool,
                    observed_at=fact.observed_at,
                )

        title = fact.title.lower()
        if title:
            if any(marker in title for marker in DIRECTORY_LISTING_MARKERS):
                _add_evidence(
                    bucket(
                        "directory_listing",
                        "attention",
                        "页面标题像目录列表",
                        "标题形如 “Index of /”。目录列表通常不算漏洞，但会暴露文件结构，"
                        "值得确认它是有意开放还是配置遗留。",
                    ),
                    fact.value,
                    asset_id=fact.asset_id,
                    source_tool=fact.source_tool,
                    observed_at=fact.observed_at,
                )
            elif any(marker in title for marker in DEFAULT_PAGE_MARKERS):
                _add_evidence(
                    bucket(
                        "default_page",
                        "notice",
                        "页面标题像中间件默认欢迎页",
                        "常见于中间件安装后的默认页面。它本身不是漏洞，"
                        "但说明该节点的 Web 配置可能尚未完成。",
                    ),
                    fact.value,
                    asset_id=fact.asset_id,
                    source_tool=fact.source_tool,
                    observed_at=fact.observed_at,
                )

        # 版本号横幅（``nginx/1.18.0``）。这是信息暴露，不是漏洞 ——
        # 本框架**不会**据此匹配任何 CVE。
        if "/" in fact.webserver:
            version = fact.webserver.split("/", 1)[1]
            if any(ch.isdigit() for ch in version):
                _add_evidence(
                    bucket(
                        "version_banner",
                        "info",
                        "服务端响应暴露了版本号",
                        "响应里带着中间件版本号（例如 nginx/1.18.0）。这属于信息暴露；"
                        "本框架不做版本对照，也不会据此给出任何结论。",
                    ),
                    fact.value,
                    asset_id=fact.asset_id,
                    source_tool=fact.source_tool,
                    observed_at=fact.observed_at,
                )

    # 覆盖缺口 ①：采集到主机名，却没有对应的 HTTP 观测。
    # 这是「这次没看」，不是「它没问题」—— 两者在新手眼里长得一模一样。
    web_observed = {fact.asset_id for fact in facts if fact.url}
    unprobed = [
        asset
        for asset in assets
        if _text(asset.get("type")) in {"subdomain", "host", "ip"}
        and _text(asset.get("id")) not in web_observed
    ]
    if unprobed:
        hint = bucket(
            "unprobed_hosts",
            "notice",
            "有主机未做存活 / Web 探测",
            "这些主机是本次采集到的，但没有对应的 HTTP 观测。"
            "只代表「这次没看」，不代表「它没有问题」。",
        )
        for asset in unprobed:
            _add_evidence(hint, asset.get("value"), asset_id=asset.get("id"))

    # 覆盖缺口 ②：有步骤**终态失败**。pending / running 不算 ——
    # 任务还在跑时把「未完成」报成「覆盖不完整」是错的。
    failed = [step for step in steps if _text(step.get("status")) in STEP_FAILURE_STATUSES]
    if failed:
        hint = bucket(
            "incomplete_coverage",
            "attention",
            "本次执行有步骤失败",
            "覆盖不完整时，「没扫到」与「没有」无法区分 —— "
            "先看任务详情里的错误码，再决定是否重试。",
        )
        for step in failed:
            _add_evidence(
                hint,
                f"{_text(step.get('tool_name'))} → {_text(step.get('target'))}",
                source_tool=step.get("tool_name"),
            )

    # 级别高的在前，同级按证据条数多的在前；最后用 code 兜底，保证顺序稳定。
    return sorted(
        hints.values(),
        key=lambda hint: (-RISK_LEVELS.index(hint["level"]), -int(hint["count"]), hint["code"]),
    )


# ── 对外入口 ──────────────────────────────────────────────


def summarize(
    assets: Iterable[dict] | None = None,
    observations: Iterable[dict] | None = None,
    *,
    mode: str | None = None,
    steps: Iterable[dict] | None = None,
) -> dict:
    """把一次任务的资产与观测整理成四段结果（纯函数，不读写数据库）。

    Args:
        assets: 该任务观测到过的资产行（``core.assets.list_job_assets``）。
        observations: 该任务的观测行（``core.assets.list_observations(job_id=...)``）。
        mode: ``mock`` / ``real``。只用于决定要不要带上 :data:`MOCK_NOTICE`
            —— mock 模式**不产生观测**，这一点必须明说，而不是让前端渲染成
            「扫了但什么都没有」。
        steps: 该任务的步骤行，用于识别「终态失败 → 覆盖不完整」。

    Returns:
        dict: ``{"counts", "assets", "services", "technologies", "risk_hints", "notes"}``。
        后四段形状统一为 ``{"total", "items", "truncated"[..., "by_type"]}``；
        ``counts`` 里的数字都是**截断前**的真实数量。

    ``assets`` 段额外带 ``by_type``（类型 → 计数）与 ``type_labels``
    （类型 → 中文名），``notes`` 是需要**原样展示给用户**的说明句 ——
    它承载「没有提示 ≠ 没有漏洞」这条结论，前端不得省略。
    """
    asset_list = [item for item in (assets or []) if isinstance(item, dict)]
    observation_list = [item for item in (observations or []) if isinstance(item, dict)]
    step_list = [item for item in (steps or []) if isinstance(item, dict)]

    # 观测行不仅没有 ``value``，也没有 ``type`` —— 两者都在资产行上。
    # 不建这个索引的话，URL 观测会被当成「没有值」直接跳过。
    by_id = {_text(item.get("id") or item.get("asset_id")): item for item in asset_list}

    facts = [_Fact(observation, by_id.get(_text(observation.get("asset_id")))) for observation in observation_list]
    asset_items, by_type = _asset_items(asset_list, facts)
    service_items = _service_items(asset_list, facts)
    technology_items = _technology_items(facts)
    hint_items = _risk_hint_items(facts, asset_list, step_list)

    notes: list[str] = [DISCLAIMER_NOTICE]
    if mode == "mock":
        notes.append(MOCK_NOTICE)

    return {
        "counts": {
            "assets": len(asset_items),
            "observations": len(observation_list),
            "services": len(service_items),
            "technologies": len(technology_items),
            "risk_hints": len(hint_items),
        },
        "assets": _section(
            asset_items[:MAX_ASSET_ITEMS],
            len(asset_items),
            extra={"by_type": by_type, "type_labels": dict(ASSET_TYPE_LABELS)},
        ),
        "services": _section(service_items[:MAX_SERVICE_ITEMS], len(service_items)),
        "technologies": _section(technology_items[:MAX_TECHNOLOGY_ITEMS], len(technology_items)),
        "risk_hints": _section(hint_items[:MAX_RISK_HINT_ITEMS], len(hint_items)),
        "notes": notes,
    }