"""Phase 4「结果体验」派生层单测：``core/findings.py``。

这一层是**纯函数**（不碰 sqlite / Flask / 配置 / 网络），所以用例不需要任何
夹具：喂进资产行与观测行，断言四段结果的形状与口径。

测试数据全部用 RFC 6761 保留域 ``example.test`` 与 RFC 5737 文档保留段，
与 ``tests/integration/test_public_scan_mode.py`` 同一约定 ——
即使某个用例写错了也不会指向任何真实资产。
"""

import pytest

from core import findings


def _observation(value, data=None, *, asset_id="asset_1", tool="httpx", observed_at="2026-10-02T10:00:00+00:00"):
    # ``id`` 用递增序号而不是 ``hash()``：hash 随机化会让 id 每次运行都不同，
    # 一旦哪天有断言读到它就变成偶发失败。
    _observation.counter += 1
    return {
        "id": f"obs_{_observation.counter}",
        "asset_id": asset_id,
        "value": value,
        "data": data or {},
        "source_tool": tool,
        "observed_at": observed_at,
    }


_observation.counter = 0


def _asset(asset_id, asset_type, value, **overrides):
    payload = {
        "id": asset_id,
        "type": asset_type,
        "value": value,
        "status": "active",
        "confidence": "high",
        "first_seen": "2026-10-02T10:00:00+00:00",
        "last_seen": "2026-10-02T10:00:00+00:00",
    }
    payload.update(overrides)
    return payload


# ── 形状与空输入 ──────────────────────────────────────────


def test_summarize_with_no_input_is_all_empty_but_still_explains_itself():
    """没有任何数据时**不能**只是四个空段：必须解释「空的含义」。"""
    result = findings.summarize([], [])

    assert result["counts"] == {
        "assets": 0,
        "observations": 0,
        "services": 0,
        "technologies": 0,
        "risk_hints": 0,
    }
    for section in ("assets", "services", "technologies", "risk_hints"):
        assert result[section]["total"] == 0
        assert result[section]["items"] == []
        assert result[section]["truncated"] is False
    assert findings.DISCLAIMER_NOTICE in result["notes"]


def test_summarize_tolerates_none_and_non_dict_items():
    """``None`` 与形状不对的元素不能让派生层崩 —— 数据来自工具输出。"""
    result = findings.summarize(None, [None, "x", 3])
    assert result["counts"]["observations"] == 0


def test_every_section_has_the_same_shape():
    """四段形状必须统一，否则前端要写四份渲染函数。"""
    assets = [_asset("asset_1", "subdomain", "a.example.test")]
    result = findings.summarize(assets, [])

    for section in ("assets", "services", "technologies", "risk_hints"):
        assert set(result[section]) >= {"total", "items", "truncated"}, section


# ── 第 1 段：发现资产 ─────────────────────────────────────


def test_assets_are_listed_with_their_type_label_and_discovering_tools():
    assets = [
        _asset("asset_1", "subdomain", "a.example.test"),
        _asset("asset_2", "url", "https://a.example.test/"),
    ]
    observations = [
        _observation("a.example.test", {}, asset_id="asset_1", tool="subfinder"),
        _observation("https://a.example.test/", {"status_code": 200}, asset_id="asset_2"),
    ]

    result = findings.summarize(assets, observations)

    assert result["counts"]["assets"] == 2
    assert result["assets"]["by_type"] == {"subdomain": 1, "url": 1}
    assert result["assets"]["type_labels"]["subdomain"] == "子域"

    by_id = {item["asset_id"]: item for item in result["assets"]["items"]}
    assert by_id["asset_1"]["type_label"] == "子域"
    assert by_id["asset_1"]["source_tools"] == ["subfinder"]
    assert by_id["asset_2"]["source_tools"] == ["httpx"]


def test_asset_type_label_falls_back_to_the_raw_type():
    """未知类型原样返回 —— 显示不出中文也比显示成「—」丢掉信息好。"""
    assert findings.asset_type_label("something-new") == "something-new"
    assert findings.asset_type_label(None) == "—"


# ── 第 2 段：服务 ─────────────────────────────────────────


def test_http_url_observations_become_services_with_default_ports():
    """``https://host`` 的端口是 443 —— 缺省端口按协议补齐，不做端口猜测。"""
    assets = [_asset("asset_1", "url", "https://a.example.test/")]
    observations = [_observation("https://a.example.test/", {"status_code": 200})]

    services = findings.summarize(assets, observations)["services"]["items"]

    assert len(services) == 1
    assert services[0]["scheme"] == "https"
    assert services[0]["host"] == "a.example.test"
    assert services[0]["port"] == 443
    assert services[0]["kind"] == "http"
    assert services[0]["kind_label"] == "HTTP 端点"


def test_explicit_port_is_kept_and_plain_http_uses_80():
    assets = [
        _asset("asset_1", "url", "https://a.example.test:8443/"),
        _asset("asset_2", "url", "http://b.example.test/"),
    ]
    observations = [
        _observation("https://a.example.test:8443/", {"status_code": 200}, asset_id="asset_1"),
        _observation("http://b.example.test/", {"status_code": 301}, asset_id="asset_2"),
    ]

    services = {row["label"]: row for row in findings.summarize(assets, observations)["services"]["items"]}

    assert services["HTTPS · a.example.test:8443"]["port"] == 8443
    assert services["HTTP · b.example.test:80"]["port"] == 80


def test_port_and_service_assets_are_included():
    """``naabu`` / ``nmap`` 的 ``ip:port`` 与 ``service`` 资产也要进服务段。"""
    assets = [
        _asset("asset_1", "port", "192.0.2.10:8080"),
        _asset("asset_2", "service", "http"),
    ]

    services = findings.summarize(assets, [])["services"]["items"]
    by_kind = {row["kind"]: row for row in services}

    assert by_kind["tcp"]["port"] == 8080
    assert by_kind["tcp"]["host"] == "192.0.2.10"
    assert by_kind["service"]["kind_label"] == "服务标识"


def test_same_endpoint_from_two_tools_is_one_service_row():
    """同一端点被两个工具看到，服务段必须只有一行、两个来源。"""
    assets = [_asset("asset_1", "url", "https://a.example.test/")]
    observations = [
        _observation("https://a.example.test/", {}, asset_id="asset_1", tool="httpx"),
        _observation("https://a.example.test/", {}, asset_id="asset_1", tool="other"),
    ]

    services = findings.summarize(assets, observations)["services"]["items"]
    assert len(services) == 1
    assert services[0]["sources"] == ["httpx", "other"]


# ── 第 3 段：技术栈 ───────────────────────────────────────


def test_webserver_tech_and_cdn_are_separated_by_kind():
    """``webserver`` 与 ``tech`` 必须分开 —— 一个是「谁在应答」，一个是「页面里的组件」。"""
    assets = [_asset("asset_1", "url", "https://a.example.test/")]
    observations = [
        _observation(
            "https://a.example.test/",
            {"webserver": "nginx", "tech": ["PHP", "jQuery"], "cdn": "cloudflare"},
        )
    ]

    items = findings.summarize(assets, observations)["technologies"]["items"]
    by_name = {item["name"]: item for item in items}

    assert by_name["nginx"]["kind"] == "webserver"
    assert by_name["nginx"]["kind_label"] == "Web 服务器"
    assert by_name["PHP"]["kind"] == "tech"
    assert by_name["cloudflare"]["kind"] == "cdn"
    assert by_name["nginx"]["hosts"] == ["a.example.test"]


def test_comma_separated_tech_string_is_split():
    """httpx 的 ``tech`` 可能是 ``"Nginx, PHP"`` 这种字符串，不能整条当一个技术栈。"""
    assets = [_asset("asset_1", "url", "https://a.example.test/")]
    observations = [_observation("https://a.example.test/", {"tech": "Nginx, PHP"})]

    names = {item["name"] for item in findings.summarize(assets, observations)["technologies"]["items"]}
    assert names == {"Nginx", "PHP"}


def test_technologies_use_the_shared_alias_table():
    """``server`` / ``web_server`` / ``technology`` 必须与 diff 归一化到同一批 canonical key。

    否则「diff 里叫 webserver、结果页里不显示」这类分裂会复活 ——
    ``core/assets.py`` 的注释已经记过一次同样的 bug。
    """
    assets = [_asset("asset_1", "url", "https://a.example.test/")]
    observations = [
        _observation("https://a.example.test/", {"server": "Apache"}),
        _observation("https://b.example.test/", {"web_server": "IIS"}, asset_id="asset_2"),
        _observation("https://c.example.test/", {"technology": "Tomcat"}, asset_id="asset_3"),
    ]

    items = findings.summarize(assets, observations)["technologies"]["items"]
    by_name = {item["name"]: item for item in items}

    assert set(by_name) == {"Apache", "IIS", "Tomcat"}
    # ``server`` / ``web_server`` → webserver；``technology`` → technologies。
    # 与 ATTRIBUTE_ALIASES 完全一致，结果页与 diff 不会各说一套。
    assert by_name["Apache"]["kind"] == "webserver"
    assert by_name["IIS"]["kind"] == "webserver"
    assert by_name["Tomcat"]["kind"] == "tech"


# ── 第 4 段：风险提示（可观察事实，不是漏洞判定） ──────────


def test_plain_http_produces_a_notice_hint():
    assets = [_asset("asset_1", "url", "http://a.example.test/")]
    observations = [_observation("http://a.example.test/", {"status_code": 200})]

    hints = findings.summarize(assets, observations)["risk_hints"]["items"]
    hint = next(item for item in hints if item["code"] == "plain_http")

    assert hint["level"] == "notice"
    assert hint["level_label"] == "可留意"
    assert hint["count"] == 1
    assert hint["evidence"][0]["value"] == "http://a.example.test/"


def test_five_xx_is_attention_and_401_is_only_info():
    assets = [
        _asset("asset_1", "url", "https://a.example.test/"),
        _asset("asset_2", "url", "https://b.example.test/admin"),
    ]
    observations = [
        _observation("https://a.example.test/", {"status_code": 503}, asset_id="asset_1"),
        _observation("https://b.example.test/admin", {"status_code": 401}, asset_id="asset_2"),
    ]

    hints = {item["code"]: item for item in findings.summarize(assets, observations)["risk_hints"]["items"]}

    assert hints["server_error"]["level"] == "attention"
    assert hints["access_control_present"]["level"] == "info"
    # 401 的说明必须写明「本框架不做认证绕过」，否则会被读成「发现未授权访问」。
    assert "不做" in hints["access_control_present"]["detail"]


def test_directory_listing_and_default_page_titles_are_recognized():
    assets = [
        _asset("asset_1", "url", "https://a.example.test/files/"),
        _asset("asset_2", "url", "https://b.example.test/"),
    ]
    observations = [
        _observation("https://a.example.test/files/", {"title": "Index of /files"}, asset_id="asset_1"),
        _observation("https://b.example.test/", {"title": "Welcome to nginx!"}, asset_id="asset_2"),
    ]

    hints = {item["code"]: item for item in findings.summarize(assets, observations)["risk_hints"]["items"]}

    assert hints["directory_listing"]["level"] == "attention"
    assert hints["default_page"]["level"] == "notice"


def test_version_banner_is_info_only():
    assets = [_asset("asset_1", "url", "https://a.example.test/")]
    observations = [_observation("https://a.example.test/", {"webserver": "nginx/1.18.0"})]

    hints = {item["code"]: item for item in findings.summarize(assets, observations)["risk_hints"]["items"]}
    assert hints["version_banner"]["level"] == "info"
    # 必须写明「不做版本对照」，否则这条会被读成 CVE 线索。
    assert "不做版本对照" in hints["version_banner"]["detail"]


def test_webserver_without_version_does_not_raise_the_banner_hint():
    assets = [_asset("asset_1", "url", "https://a.example.test/")]
    observations = [_observation("https://a.example.test/", {"webserver": "nginx"})]

    hints = {item["code"] for item in findings.summarize(assets, observations)["risk_hints"]["items"]}
    assert "version_banner" not in hints


def test_unprobed_hosts_are_reported_as_a_coverage_gap():
    """采集到主机却没有 HTTP 观测 = 「这次没看」，必须与「没有问题」区分开。"""
    assets = [
        _asset("asset_1", "subdomain", "a.example.test"),
        _asset("asset_2", "subdomain", "b.example.test"),
        _asset("asset_3", "url", "https://c.example.test/"),
    ]
    observations = [_observation("https://c.example.test/", {}, asset_id="asset_3")]

    hints = {item["code"]: item for item in findings.summarize(assets, observations)["risk_hints"]["items"]}
    assert hints["unprobed_hosts"]["count"] == 2
    assert {item["value"] for item in hints["unprobed_hosts"]["evidence"]} == {
        "a.example.test",
        "b.example.test",
    }


@pytest.mark.parametrize("status", ["failed", "timeout"])
def test_failed_steps_report_incomplete_coverage(status):
    result = findings.summarize(
        [], [], steps=[{"tool_name": "httpx", "target": "a.example.test", "status": status}]
    )
    hints = {item["code"]: item for item in result["risk_hints"]["items"]}
    assert hints["incomplete_coverage"]["level"] == "attention"


@pytest.mark.parametrize("status", ["pending", "running", "succeeded", ""])
def test_unfinished_or_successful_steps_do_not_report_incomplete_coverage(status):
    """任务还在跑时把「未完成」报成「覆盖不完整」是错的。"""
    result = findings.summarize(
        [], [], steps=[{"tool_name": "httpx", "target": "a.example.test", "status": status}]
    )
    assert "incomplete_coverage" not in {item["code"] for item in result["risk_hints"]["items"]}


def test_hints_are_ordered_by_level_then_evidence_count():
    assets = [
        _asset("asset_1", "url", "https://a.example.test/"),
        _asset("asset_2", "url", "http://b.example.test/"),
        _asset("asset_3", "url", "http://c.example.test/"),
    ]
    observations = [
        _observation("https://a.example.test/", {"status_code": 500}, asset_id="asset_1"),
        _observation("http://b.example.test/", {"status_code": 200}, asset_id="asset_2"),
        _observation("http://c.example.test/", {"status_code": 200}, asset_id="asset_3"),
    ]

    hints = findings.summarize(assets, observations)["risk_hints"]["items"]
    assert hints[0]["code"] == "server_error"  # attention 必须排在最前
    assert hints[0]["level"] == "attention"
    assert hints[1]["code"] == "plain_http"
    assert hints[1]["count"] == 2


def test_no_hints_is_not_a_clean_bill_of_health():
    """**这是本阶段最重要的一条断言**：没有提示 ≠ 没有漏洞。

    本框架不做漏洞扫描（无 nuclei、无 CVE / 严重级别数据），
    所以免责说明必须**每次**下发，而不是「零提示时才补一句」——
    真实链路里零提示几乎从不出现（「有子域没做 HTTP 探测」就会产生一条），
    那样反而最需要说明的那次拿不到它。
    """
    assets = [_asset("asset_1", "url", "https://a.example.test/")]
    observations = [_observation("https://a.example.test/", {"status_code": 200, "webserver": "nginx"})]

    result = findings.summarize(assets, observations)

    assert result["risk_hints"]["total"] == 0
    assert findings.DISCLAIMER_NOTICE in result["notes"]
    assert "不做漏洞扫描" in findings.DISCLAIMER_NOTICE
    assert "不等于" in findings.DISCLAIMER_NOTICE


def test_disclaimer_is_present_even_when_there_are_hints():
    """有提示时同样要带免责说明 —— 「有提示」不等于「发现了漏洞」。"""
    assets = [_asset("asset_1", "url", "http://a.example.test/")]
    observations = [_observation("http://a.example.test/", {"status_code": 500})]

    result = findings.summarize(assets, observations)

    assert result["risk_hints"]["total"] > 0
    assert findings.DISCLAIMER_NOTICE in result["notes"]


def test_no_vulnerability_severity_concept_exists():
    """本模块**不得**引入 CVE / 危险度分级 —— 分级会暗示「评估过危险程度」。"""
    assert findings.RISK_LEVELS == ("info", "notice", "attention")
    for forbidden in ("low", "medium", "high", "critical"):
        assert forbidden not in findings.RISK_LEVELS


def test_summarize_never_emits_a_severity_or_cve_field():
    """出参里不允许出现 severity / cve 这类字段（前端才不会再造一个漏洞分级）。"""
    assets = [_asset("asset_1", "url", "https://a.example.test/")]
    observations = [_observation("https://a.example.test/", {"status_code": 500})]

    result = findings.summarize(assets, observations)
    for hint in result["risk_hints"]["items"]:
        assert "severity" not in hint
        assert "cve" not in hint


# ── 模式说明与截断 ────────────────────────────────────────


def test_mock_mode_says_so_instead_of_looking_like_an_empty_scan():
    """mock 不产生观测。必须明说「这是预期行为」，不能渲染成「扫了但什么都没有」。"""
    result = findings.summarize([], [], mode="mock")

    assert findings.MOCK_NOTICE in result["notes"]
    assert "mock" in findings.MOCK_NOTICE


def test_real_mode_does_not_add_the_mock_notice():
    result = findings.summarize([], [], mode="real")
    assert findings.MOCK_NOTICE not in result["notes"]


def test_truncated_flag_and_counts_reflect_the_true_total(monkeypatch):
    """``counts`` / ``total`` 必须是**截断前**的真实数量，``truncated`` 明确标注。"""
    monkeypatch.setattr(findings, "MAX_ASSET_ITEMS", 2)
    assets = [_asset(f"asset_{index}", "subdomain", f"h{index}.example.test") for index in range(5)]

    result = findings.summarize(assets, [])

    assert result["counts"]["assets"] == 5
    assert result["assets"]["total"] == 5
    assert len(result["assets"]["items"]) == 2
    assert result["assets"]["truncated"] is True


def test_evidence_per_hint_is_capped_but_count_is_not(monkeypatch):
    monkeypatch.setattr(findings, "MAX_EVIDENCE_PER_HINT", 2)
    assets = [_asset(f"asset_{index}", "url", f"http://h{index}.example.test/") for index in range(5)]
    observations = [
        _observation(f"http://h{index}.example.test/", {}, asset_id=f"asset_{index}") for index in range(5)
    ]

    hint = next(
        item for item in findings.summarize(assets, observations)["risk_hints"]["items"]
        if item["code"] == "plain_http"
    )
    assert hint["count"] == 5
    assert len(hint["evidence"]) == 2


# ── 纯函数边界 ────────────────────────────────────────────


def test_runtime_copy_contains_no_markdown_markers():
    """面向用户的文案里不得出现 ``**`` —— 前端用 ``textContent`` 渲染。

    不拼 innerHTML 是为了不引入 XSS 面，代价就是 ``**`` 会原样显示成星号：
    页面上会出现「本框架**不做漏洞扫描**」这种半成品排版。强调只写在
    docstring / 注释里。
    """
    assets = [
        _asset("asset_1", "url", "http://a.example.test/"),
        _asset("asset_2", "subdomain", "b.example.test"),
    ]
    observations = [_observation("http://a.example.test/", {"status_code": 500}, asset_id="asset_1")]

    result = findings.summarize(assets, observations, mode="mock")

    def _strings(node):
        if isinstance(node, str):
            yield node
        elif isinstance(node, dict):
            for value in node.values():
                yield from _strings(value)
        elif isinstance(node, list):
            for value in node:
                yield from _strings(value)

    for text in _strings(result):
        assert findings.MARKDOWN_MARKER not in text, f"运行时文案里出现了 Markdown 标记: {text!r}"


def test_module_does_not_touch_database_flask_or_network():
    """纯函数是刻意的：派生层可单测、可复用，也不该悄悄写库。

    这条守的是「有人图省事在里面直接查库」——那会让派生层只有在
    完整应用上下文里才可测，也会让「结果页」变成写路径。
    """
    import inspect

    source = inspect.getsource(findings)

    for forbidden in ("import sqlite3", "from core import db", "flask", "requests", "subprocess"):
        assert forbidden not in source, f"core/findings.py 不再是纯函数: {forbidden}"