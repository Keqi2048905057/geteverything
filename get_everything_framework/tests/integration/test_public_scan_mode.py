"""扫描中心 / 公网入口：端到端集成测试（测试阶段口径）。

## 本轮口径（已落盘，不要改回去）

授权业务整体取消，取而代之的是一个**默认关闭**的开关 ``GEF_OPEN_SCAN_MODE``
（见 ``core/safety.py:open_scan_enabled``）。本文件覆盖的就是这套新口径：

```text
开放模式   —— 开关关闭时缺 scope_id → 400 并点名环境变量；开启时用 open_scope()
Policy     —— 显式给 scope_id 时白名单**完全照旧**（越界 403、排除优先）
Check      —— /api/public-jobs/check 是「目标格式预检」，不再有任何授权结论
Job        —— 任务进入 Job 队列；project_id / project_name / authorized_public 恒为 None
Tool       —— 只有未接入（nuclei）与未登记的工具不可提交，nmap 等已可自由勾选
页面       —— 扫描中心是三步（确认目标 / 选择工具 / 创建任务），无授权面板
Worker     —— 任务正常执行
```

被取消的三道闸门（项目存在 → Scope 已关联到项目 → 公网工具白名单）在本文件里
都有**反向守卫**：谁把它们加回来，对应用例就会红。**没有取消的**：目标格式与
排除列表校验、SSRF 防护、限速 / 超时 / 并发、审计与结构化日志。

## 不发起任何真实外部扫描

**本文件不发起任何真实外部扫描。** 所有 real 模式用例都把
``modules.registry.build_runner`` 换成假 runner（``fake_real_runner`` 夹具），
或显式使用 ``mode="mock"``；验证的是「闸门 + 执行链 + 落库」，而不是
「工具真的打了公网」。这与 ``AGENTS.md`` 的硬约束一致：
不扫描任何未授权的外部目标，默认只用 mock runner 与 ``127.0.0.1``。

目标域名统一用 RFC 6761 保留域 ``example.test`` 及其子域 —— 即使某个用例
意外走通了真实执行路径，打出去的也不是任何人的资产。

## 环境开关必须钉死

``tests/conftest.py`` 已经把 ``GEF_ALLOW_REAL_SCAN=false`` 钉死，但**没有**钉
``GEF_OPEN_SCAN_MODE``：本机 ``.env`` 里那一行会通过 ``config.load_dotenv()``
生效。因此本文件用一个 autouse 夹具把它钉成 ``false``，
需要开放模式的用例自己 ``monkeypatch.setenv("GEF_OPEN_SCAN_MODE", "true")`` ——
否则同一份代码在一台机器绿、在另一台机器红，红的理由还与被测代码无关。
"""

import inspect

import pytest

from core import audit, jobs as jobs_store, projects, scope_store

ADMIN_ONLY_ENDPOINTS = [
    ("get", "/api/projects"),
    ("post", "/api/projects"),
    ("get", "/api/projects/proj_x"),
    ("post", "/api/projects/proj_x/scopes"),
    ("post", "/api/public-jobs"),
    ("post", "/api/public-jobs/check"),
    ("get", "/api/scan-center"),
]

#: 预检响应的**完整**键集合。多一个键就会红 —— 这就是「不多说一句」的可执行版本。
CHECK_KEYS = {"raw", "normalized", "kind", "valid", "verdict", "excluded", "message"}


@pytest.fixture(autouse=True)
def _pin_open_scan_mode_off(monkeypatch):
    """把 ``GEF_OPEN_SCAN_MODE`` 钉成关闭（本机 ``.env`` 里那一行不作数）。

    ``tests/conftest.py`` 钉了 ``GEF_ALLOW_REAL_SCAN`` 与 ``GEF_LOG_FORMAT``，
    但**没有**钉这个开关；而本机 ``.env`` 里写着 ``GEF_OPEN_SCAN_MODE=true``，
    它经 ``config.load_dotenv()`` 进入测试进程。不钉的话：

    * 「缺 scope_id 应当 400」会变成「拿到开放范围、然后被 real 开关 403」；
    * 关闭态的守卫整条失效，且红/绿取决于开发者本机的 ``.env``。

    需要开放模式的用例自己 ``monkeypatch.setenv("GEF_OPEN_SCAN_MODE", "true")``，
    用例结束自动回滚到这里的 false。
    """
    from core.safety import OPEN_SCAN_ENV

    monkeypatch.setenv(OPEN_SCAN_ENV, "false")


def _make_scope(admin_client, *, domains=("example.test",), active_scan=True, **overrides):
    payload = {"name": "授权范围", "allowed_domains": list(domains), "active_scan": active_scan}
    payload.update(overrides)
    resp = admin_client.post("/api/scopes", json=payload)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["scope"]["id"]


def _make_project(admin_client, *, scope_ids=(), **overrides):
    payload = {
        "name": "培正学院授权测试",
        "authorization_note": "2026-10-02 校方信息中心书面授权，仅被动信息收集",
        "owner": "张三",
        "scope_ids": list(scope_ids),
    }
    payload.update(overrides)
    resp = admin_client.post("/api/projects", json=payload)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["project"]


def _public_job(admin_client, project_id=None, scope_id=None, **overrides):
    """提交 ``POST /api/public-jobs``。

    ``project_id`` / ``scope_id`` 在测试阶段都只是**可选**的兼容字段：
    项目不再参与任何判定，``scope_id`` 留空即走开放模式（需 ``GEF_OPEN_SCAN_MODE``）。
    """
    payload = {
        "project_id": project_id,
        "scope_id": scope_id,
        "targets": ["www.example.test"],
    }
    payload.update(overrides)
    return admin_client.post("/api/public-jobs", json=payload)


def _run_once():
    """跑一轮 worker：把当前队列任务全部处理掉（不进入常驻循环）。"""
    from jobs.worker import Worker

    with Worker(worker_id="public-scan-test", verbose=False) as worker:
        processed = 0
        while worker.tick() is not None:
            processed += 1
            if processed > 50:  # 防御：正常用例不会跑到这里
                break
        return processed


class _FakeRunner:
    """假 runner：不执行任何外部命令，只回一个结构化结果。"""

    def __init__(self, result):
        self._result = result
        self.tool_name = "subfinder"
        self.category = "subdomain"
        self.last_execution = {"stdout": "a.www.example.test\n", "stderr": None}

    def run(self, target):
        return self._result


@pytest.fixture
def fake_real_runner(monkeypatch):
    """把 real 执行链上的 runner 换成假的，并打开环境开关。"""
    from core.runner_result import Observation, RunnerResult

    result = RunnerResult.ok(
        [Observation(category="subdomain", value="a.www.example.test", data={})],
        exit_code=0,
        duration_ms=7,
        command_preview="subfinder -d www.example.test",
    )
    monkeypatch.setattr("modules.registry.build_runner", lambda name: _FakeRunner(result))
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")
    return result


# ── 认证（方案第 2.3 节：公网入口同样必须登录） ─────────────


@pytest.mark.parametrize("method,path", ADMIN_ONLY_ENDPOINTS)
def test_public_scan_endpoints_require_admin(client, method, path):
    resp = getattr(client, method)(path)
    assert resp.status_code == 401
    assert resp.get_json()["error_code"] == "unauthenticated"


# ── Scope：公网域名创建成功（方案第 9 节） ─────────────────


def test_public_domain_scope_can_be_created(admin_client):
    """方案第 9 节 Scope 类：授权公网域名能建成 Scope，且 `active_scan` 如实保存。

    这里刻意用方案第 4/11 节原文的示例域名，作为「方案要求的场景真的能建起来」的证据。
    **它只是一个字符串**：写进的是本用例的临时库（`app_module` 夹具把
    `LOCAL_DB_CONFIG["path"]` 指到 `tmp_path`），而且**本文件从来不会拿它去建任务** ——
    所有任务用的目标都是 RFC 6761 保留域 `example.test` 及其子域。
    也就是说：即便有人误把测试指向真实服务，这条用例也不会对任何真实资产发出请求。
    """
    scope_id = _make_scope(admin_client, domains=["www.peizheng.edu.cn"], active_scan=True)
    body = admin_client.get(f"/api/scopes/{scope_id}").get_json()["scope"]
    assert body["allowed_domains"] == ["www.peizheng.edu.cn"]
    assert body["active_scan"] is True


def test_public_url_target_is_normalized_to_its_host(admin_client):
    """方案第 6 节：用户会直接粘 URL（带协议或不带协议）—— 都要能走通。

    与 `test_public_ip_target_is_checked_against_allowed_cidrs` 同一条思路，
    补的是**第四种输入**：URL。带协议的 URL 一直支持；**没写协议**的
    （浏览器地址栏里复制出来的那种）此前会掉进 CIDR 分支，报
    「非法的 CIDR: www.example.test/a/b」—— 把一条完全正常的输入说成网段写错。

    这里同时守住「规范化之后落到同一个目标」：带协议、不带协议、带路径三种写法
    必须指向同一个主机，否则**预检给出的结论**会随写法而变。

    测试阶段口径：预检只回答「格式对不对 / 规范化成什么」，因此断言的是
    ``valid`` / ``normalized`` / ``kind`` / ``verdict``，不再有 ``ready``
    这个**条目级**字段（``ready`` 是 ``summary`` 里的计数）。
    """
    scope_id = _make_scope(admin_client, domains=["example.test"], active_scan=True)

    for raw in ("www.example.test", "www.example.test/a/b", "https://www.example.test/a/b?q=1"):
        check = admin_client.post(
            "/api/public-jobs/check",
            json={"targets": [raw]},
        ).get_json()["checks"][0]
        assert check["valid"] is True, (raw, check)
        assert check["normalized"] == "www.example.test", (raw, check)
        assert check["kind"] == "domain", (raw, check)
        assert check["verdict"] == "allowed", (raw, check)
        assert check["excluded"] is False, (raw, check)

        resp = admin_client.post(
            "/api/public-jobs",
            json={
                "scope_id": scope_id,
                "targets": [raw],
                "strategy": "asset_discovery",
                "mode": "mock",
            },
        )
        assert resp.status_code == 202, (raw, resp.get_json())
        # 落库的是**规范化后**的主机，不是用户粘进来的那串 URL。
        job = jobs_store.get_job(resp.get_json()["job_id"])
        assert job["targets"] == ["www.example.test"], (raw, job["targets"])


def test_public_ip_target_is_checked_against_allowed_cidrs(admin_client):
    """方案第 4 节把目标类型写成「域名 / IP / CIDR」—— 三条路径都要能走通。

    域名那条由上面的用例覆盖（以及 `test_scope.py` 的匹配规则单测），
    这条补的是**公网入口链路上的 IP**：Scope 只给 ``allowed_cidrs``（不给域名），
    IP 目标必须落在网段内才放行、网段外一律 403。

    网段用 **RFC 5737 文档保留段**（``192.0.2.0/24`` 是 TEST-NET-1，
    ``198.51.100.0/24`` 是 TEST-NET-2）—— 按 RFC 它们**不会**被分配给任何真实主机，
    因此这两条断言不涉及任何人的资产（与用 ``example.test`` 是同一个思路）。
    """
    scope_id = _make_scope(admin_client, domains=[], allowed_cidrs=["192.0.2.0/24"])
    project = _make_project(admin_client, scope_ids=[scope_id])

    inside = _public_job(admin_client, project["id"], scope_id, targets=["192.0.2.10"], mode="mock")
    assert inside.status_code == 202, inside.get_json()

    outside = _public_job(admin_client, project["id"], scope_id, targets=["198.51.100.7"], mode="mock")
    assert outside.status_code == 403
    assert outside.get_json()["error_code"] == "scope_violation"
    assert "网段" in outside.get_json()["error_message"]

    # 越界的那些一个都不许落库：只应有「网段内」那一条任务。
    assert len(jobs_store.list_jobs()) == 1


# ── 项目：授权证据的组织单位 ───────────────────────────────


def test_project_create_and_attach_scope_via_api(admin_client):
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])
    assert project["id"].startswith("proj_")
    assert project["scope_ids"] == [scope_id]

    extra_scope = _make_scope(admin_client, domains=["other.example.test"])
    resp = admin_client.post(f"/api/projects/{project['id']}/scopes", json={"scope_id": extra_scope})
    assert resp.status_code == 201
    assert resp.get_json()["project"]["scope_ids"] == [scope_id, extra_scope]


def test_project_is_listed_and_audited(admin_client):
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])

    listed = admin_client.get("/api/projects").get_json()["projects"]
    assert [item["id"] for item in listed] == [project["id"]]

    events = [event for event in audit.list_events(limit=50) if event["event_type"] == audit.EVENT_PROJECT_CREATED]
    entry = next(event for event in events if event["target_id"] == project["id"])
    assert entry["detail"]["scope_ids"] == [scope_id]
    assert "书面授权" in entry["detail"]["authorization_note"]


def test_project_requires_authorization_note(admin_client):
    resp = admin_client.post("/api/projects", json={"name": "没有授权说明"})
    assert resp.status_code == 400
    assert resp.get_json()["error_code"] == "bad_request"


def test_get_missing_project_is_404(admin_client):
    resp = admin_client.get("/api/projects/proj_missing")
    assert resp.status_code == 404
    assert resp.get_json()["error_code"] == "not_found"


# ── 扫描中心元数据 ─────────────────────────────────────────


def test_scan_center_metadata_exposes_strategies_and_selectable_tools(admin_client):
    """扫描中心元数据：策略模板 + **全部已接入工具都可勾选**。

    本轮口径变更的实证在这里：

    * ``selectable_tools`` 不再是一份「公网白名单」（过去只有 ``httpx`` /
      ``subfinder``），而是**全部已注册 runner**。因此断言方式是
      「与 ``modules.registry.get_supported_runners()`` 集合相等」——
      写死 17 个名字会随注册表变化而失效，那样守卫反而会被人删掉；
    * ``internet_allowed_tools`` 作为 deprecated 兼容键**与它同值**（旧客户端
      不会炸，但新代码不得再据此判断「能不能打公网」）；
    * ``nmap`` 这类高风险工具 ``selectable is True`` —— 这是「工具开放」的实证，
      同时风险等级仍然如实标注（``high``）；
    * ``projects`` 键**已移除**（授权项目业务取消）；
    * ``nuclei`` 未接入 runner，必须如实出现在 ``restricted_tools`` 里。
    """
    from core.tool_registry import selectable_tools as registry_selectable_tools
    from modules.registry import get_supported_runners

    body = admin_client.get("/api/scan-center").get_json()

    assert [item["key"] for item in body["strategies"]] == [
        "asset_discovery",
        "web_fingerprint",
        "custom",
    ]
    discovery = next(item for item in body["strategies"] if item["key"] == "asset_discovery")
    assert sorted(discovery["tools"]) == ["httpx", "subfinder"]

    # 「工具开放」的可执行口径：可勾选名单 = 全部已注册 runner（两处取数一致）。
    assert sorted(body["selectable_tools"]) == sorted(get_supported_runners())
    assert body["selectable_tools"] == registry_selectable_tools()
    # Deprecated 兼容键：同值，不得各写一份。
    assert body["internet_allowed_tools"] == body["selectable_tools"]

    by_name = {item["tool_name"]: item for item in body["tools"]}
    assert by_name["nmap"]["selectable"] is True
    assert by_name["nmap"]["risk_level"] == "high"
    # 未接入的 nuclei 仍然不可选 —— 这是唯一的工具闸门。
    assert by_name.get("nuclei") is None, "nuclei 不得混进「能跑的工具」清单"

    # 授权项目业务取消，元数据里也不再下发项目清单。
    assert "projects" not in body
    # nuclei 未接入 runner，但必须如实出现，前端才能解释「为什么不让选」。
    assert [item["tool_name"] for item in body["restricted_tools"]] == ["nuclei"]


def test_scan_center_does_not_leak_targets(admin_client):
    """元数据接口下发项目名与授权说明，但**绝不**下发目标清单。"""
    scope_id = _make_scope(admin_client, domains=["secret.example.test"])
    _make_project(admin_client, scope_ids=[scope_id])

    raw = admin_client.get("/api/scan-center").get_data(as_text=True)
    assert "secret.example.test" not in raw


# ── Phase 2：只读试算接口（下一阶段方案第 4、6 节） ──────────


def test_check_endpoint_is_read_only(admin_client):
    """试算不落任务、不写审计 —— 它是查询，不是业务动作。"""
    scope_id = _make_scope(admin_client)
    _make_project(admin_client, scope_ids=[scope_id])
    before_jobs = len(jobs_store.list_jobs())
    before_events = len(audit.list_events(limit=500))

    resp = admin_client.post("/api/public-jobs/check", json={"targets": ["www.example.test"]})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["ok"] is True

    assert len(jobs_store.list_jobs()) == before_jobs
    assert len(audit.list_events(limit=500)) == before_events


def test_check_endpoint_reports_ready_for_a_valid_target(admin_client):
    """合法目标 → ``valid`` / ``verdict=allowed`` / ``ready`` 计数 1 / 建议 real。

    **注意这里断言的不再是「有没有授权」**：``ready`` 只是「格式合法且未被排除」
    的个数。因此这条用例刻意**不建任何 Scope / 项目** —— 预检的结论与授权无关，
    这正面钉住了「授权业务已取消」。

    ``suggested_mode`` 反过来也一样：全部合法就是 ``real``（可以提交），
    与 ``GEF_ALLOW_REAL_SCAN`` 无关 —— 预检不承诺任何执行期结论。
    """
    before_jobs = len(jobs_store.list_jobs())
    resp = admin_client.post(
        "/api/public-jobs/check",
        json={"targets": ["www.example.test"]},
    )
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    check = body["checks"][0]

    assert check["valid"] is True
    assert check["verdict"] == "allowed"
    assert check["excluded"] is False
    assert check["normalized"] == "www.example.test"
    assert check["kind"] == "domain"
    assert body["summary"] == {"total": 1, "valid": 1, "invalid": 0, "excluded": 0, "ready": 1}
    assert body["suggested_mode"] == "real"
    # Deprecated 回显位恒为 None：授权业务已取消，这里没有「命中哪份授权」可说。
    assert body["scope_id"] is None
    assert body["project_id"] is None
    # 预检不落任务。
    assert len(jobs_store.list_jobs()) == before_jobs


def test_check_endpoint_verdict_is_independent_of_any_scope_or_project(admin_client):
    """**有没有建过 Scope / 项目，预检结论完全一样** —— 正面钉住「不再看授权」。

    这是本轮取消授权业务在预检接口上的可执行版本：把同一个目标在三种状态下各检
    一次（什么都没有 → 有一个覆盖它的范围 → 有一个不覆盖它的范围），
    三次结论必须逐字段相同。若谁把白名单判定加回预检，第三次立刻会红。
    """
    target = {"targets": ["www.example.test"]}

    def _check():
        return admin_client.post("/api/public-jobs/check", json=target).get_json()["checks"][0]

    bare = _check()

    covering = _make_scope(admin_client, domains=["example.test"], active_scan=True)
    _make_project(admin_client, scope_ids=[covering])
    with_covering = _check()

    unrelated = _make_scope(admin_client, domains=["other.example.test"], active_scan=True)
    _make_project(admin_client, scope_ids=[unrelated])
    with_unrelated = _check()

    def _verdict_shape(check):
        # ``message`` 允许随「在不在某个已建范围内」变化（那是如实提示），
        # 但**判定**必须一模一样。
        return {key: check[key] for key in ("normalized", "kind", "valid", "verdict", "excluded")}

    assert _verdict_shape(bare) == _verdict_shape(with_covering) == _verdict_shape(with_unrelated)
    assert bare["valid"] is True and bare["verdict"] == "allowed"


def test_check_endpoint_marks_an_invalid_target_without_blocking_the_batch(admin_client):
    """坏目标只影响它自己 —— 批量预检不该被第一个格式错误中断。

    同时钉住非法目标的**字段形状**：``normalized`` / ``kind`` 为 ``None``、
    ``verdict="invalid"``，且 ``summary.invalid`` 如实计数。
    """
    body = admin_client.post(
        "/api/public-jobs/check",
        json={"targets": ["www.example.test", "not a host"]},
    ).get_json()

    assert [item["valid"] for item in body["checks"]] == [True, False]
    bad = body["checks"][1]
    assert bad["verdict"] == "invalid"
    assert bad["normalized"] is None
    assert bad["kind"] is None
    assert bad["excluded"] is False
    assert bad["message"], "非法目标必须给出说明，而不是一个空字段"
    assert body["summary"] == {"total": 2, "valid": 1, "invalid": 1, "excluded": 0, "ready": 1}
    # 有非法目标时不建议 real。
    assert body["suggested_mode"] == "mock"


def test_check_endpoint_marks_an_invalid_target_as_mock_only(admin_client):
    """整批都非法 → ``ready=0`` / ``suggested_mode=mock``。"""
    body = admin_client.post(
        "/api/public-jobs/check", json={"targets": ["not a host"]}
    ).get_json()
    assert body["checks"][0]["valid"] is False
    assert body["summary"]["ready"] == 0
    assert body["suggested_mode"] == "mock"


def test_check_endpoint_excludes_only_when_the_scope_id_is_explicit(admin_client):
    """命中排除列表 → ``excluded`` / ``verdict=excluded``（排除优先仍然生效）。

    排除列表来自**使用者自己显式传进来的** ``scope_id``：预检不再做白名单判定，
    但「排除优先」这条安全约束（``core/scope.py`` 的硬规则）一条没少。
    这里刻意在同一批里既有被排除的、也有正常的，验证计数与判定各自独立。
    """
    scope_id = _make_scope(
        admin_client,
        domains=["example.test"],
        excluded_domains=["secret.example.test"],
    )

    body = admin_client.post(
        "/api/public-jobs/check",
        json={
            "targets": ["secret.example.test", "www.example.test"],
            "scope_id": scope_id,
        },
    ).get_json()

    excluded, allowed = body["checks"]
    assert excluded["excluded"] is True
    assert excluded["verdict"] == "excluded"
    assert excluded["valid"] is True, "格式是合法的 —— 它只是被排除列表挡住"
    assert "排除" in excluded["message"]

    assert allowed["excluded"] is False
    assert allowed["verdict"] == "allowed"

    assert body["summary"] == {"total": 2, "valid": 2, "invalid": 0, "excluded": 1, "ready": 1}
    # 有被排除的目标 → ready < total → 建议 mock（预检不承诺它会被执行）。
    assert body["suggested_mode"] == "mock"


def test_check_endpoint_exclusion_is_not_used_as_a_whitelist(admin_client):
    """**不在**某个已建范围的白名单内，不构成「不能提交」。

    这是「取消的是授权，不是排除列表」的另一半：范围里只写 ``example.test``，
    而目标写成 ``outside.test`` —— 预检必须仍然 ``valid`` / ``allowed``，
    只是消息里如实提示它不在已建范围内。谁把白名单判定加回来，这条会红。
    """
    scope_id = _make_scope(admin_client, domains=["example.test"])

    check = admin_client.post(
        "/api/public-jobs/check",
        json={"targets": ["outside.test"], "scope_id": scope_id},
    ).get_json()["checks"][0]

    assert check["valid"] is True
    assert check["verdict"] == "allowed"
    assert check["excluded"] is False
    assert "不影响提交" in check["message"]


def test_check_endpoint_reports_no_scope_when_nothing_built(admin_client):
    """一个 Scope 都没建过时，预检照样给出完整结论（不再有 ``blocker=no_scope``）。

    这条替换原来的「``blocker == 'no_scope'``」：授权业务取消后，
    「没建过范围」不再是任何一种阻塞，预检的结论与建过范围时完全一致。
    """
    body = admin_client.post(
        "/api/public-jobs/check", json={"targets": ["www.example.test"]}
    ).get_json()
    check = body["checks"][0]
    assert check["valid"] is True
    assert check["verdict"] == "allowed"
    assert "blocker" not in check
    assert "candidates" not in check
    assert body["suggested_mode"] == "real"


def test_check_endpoint_accepts_comma_separated_string(admin_client):
    """接受逗号分隔字符串，与 ``create_scan_job`` 同一口径。"""
    scope_id = _make_scope(admin_client)
    _make_project(admin_client, scope_ids=[scope_id])

    body = admin_client.post(
        "/api/public-jobs/check", json={"targets": "a.example.test, b.example.test"}
    ).get_json()
    assert [item["raw"] for item in body["checks"]] == ["a.example.test", "b.example.test"]


def test_check_endpoint_requires_targets(admin_client):
    resp = admin_client.post("/api/public-jobs/check", json={"targets": []})
    assert resp.status_code == 400
    assert resp.get_json()["details"]["field"] == "targets"


def test_check_endpoint_requires_json_object(admin_client):
    resp = admin_client.post("/api/public-jobs/check", data="not json")
    assert resp.status_code == 400
    assert resp.get_json()["error_code"] == "bad_request"


def test_check_endpoint_does_not_promise_more_than_it_verifies(admin_client):
    """预检必须**只说自己真查过的事**，不能给任何授权结论。

    历史口径下这条钉的是 ``check["resolved_check_deferred"] is True`` ——
    那时预检是「授权试算」，需要声明「DNS 解析这道执行期校验还没做」。

    授权业务取消后预检只剩下格式判定，``resolved_check_deferred`` 随之消失，
    但**这条守卫要守的东西更强了**：现在直接钉死 ``checks`` 项的**键集合**。
    只要有人把授权结论（``eligible_scope_ids`` / ``blocker`` / ``matches`` /
    ``status`` / ``active_scan`` …）加回来，键集合立刻不相等，用例变红。

    这比断言某个布尔字段更难绕过：加字段、改字段名都会被抓到。
    """
    scope_id = _make_scope(admin_client)
    _make_project(admin_client, scope_ids=[scope_id])

    check = admin_client.post(
        "/api/public-jobs/check", json={"targets": ["www.example.test"]}
    ).get_json()["checks"][0]
    assert set(check) == {"raw", "normalized", "kind", "valid", "verdict", "excluded", "message"}
    # 逐个再点名一次历史上存在过的授权结论键，防的是「换个名字加回来」。
    for gone in ("eligible_scope_ids", "blocker", "matches", "candidates", "status", "active_scan"):
        assert gone not in check, f"预检又返回了授权结论字段: {gone}"


# ── Policy / Tool：闸门（方案第 9 节「未授权域名拒绝」「禁止工具无法提交」） ──


def test_unattached_scope_is_accepted_after_job_creation(admin_client):
    """**本轮取消的那道闸门**：Scope 不再需要「挂到某个授权项目下」。

    历史口径是「Scope 存在，但没关联到这个项目下 → 400」，理由是「授权证据
    必须能追溯到某个项目」。测试阶段项目业务整体取消，这条要求随之消失：
    一个**不挂任何项目**的 Scope，只要覆盖目标，就足以创建任务（202）。

    这条是反向守卫 —— 谁把「必须挂项目」加回来，它就会红。
    """
    scope_id = _make_scope(admin_client)  # 刻意不关联任何项目
    assert projects.find_by_scope(scope_id) is None, "前置条件：这个范围确实没挂项目"

    resp = admin_client.post(
        "/api/public-jobs",
        json={"scope_id": scope_id, "targets": ["www.example.test"], "mode": "mock"},
    )
    assert resp.status_code == 202, resp.get_json()
    body = resp.get_json()
    assert body["scope_id"] == scope_id, "显式给的 scope 仍然被如实采用"
    assert body["project_id"] is None, "项目已不参与这条链路"
    assert len(jobs_store.list_jobs()) == 1


def test_missing_project_id_is_ignored_and_creates_the_job(admin_client):
    """不存在的 ``project_id`` **不再 404** —— 项目已不参与这条链路。

    历史口径是「项目不存在 → 404，且一条任务都不落库」。现在 ``project_id``
    只是被接受的兼容字段：既不校验，也不落库。这条用例同时证明两件事 ——
    它**不影响创建**（202），且任务**确实落库**（不是被静默丢弃）。
    """
    scope_id = _make_scope(admin_client)

    resp = _public_job(admin_client, "proj_missing", scope_id, mode="mock")
    assert resp.status_code == 202, resp.get_json()
    body = resp.get_json()
    assert body["project_id"] is None
    assert body["project_name"] is None
    assert body["authorized_public"] is None

    jobs = jobs_store.list_jobs()
    assert len(jobs) == 1, "任务必须真的落库，而不是被静默忽略"
    assert jobs_store.project_id_of_job(body["job_id"]) is None


def test_missing_scope_id_is_400(admin_client):
    """缺 ``scope_id`` 且开放模式未开 → 400，且**明确点名**要设哪个环境变量。

    （开放模式本身的用例见本节末尾的「开放扫描模式」一节。）
    """
    from core.safety import OPEN_SCAN_ENV

    project = _make_project(admin_client)
    resp = admin_client.post(
        "/api/public-jobs", json={"project_id": project["id"], "targets": ["www.example.test"]}
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error_code"] == "bad_request"
    assert body["details"]["field"] == "scope_id"
    assert body["details"]["env"] == OPEN_SCAN_ENV
    assert OPEN_SCAN_ENV in body["error_message"]
    assert jobs_store.list_jobs() == []


def test_out_of_scope_target_is_403(admin_client):
    """方案第 11 节：未授权目标返回 403。"""
    scope_id = _make_scope(admin_client, domains=["example.test"])
    project = _make_project(admin_client, scope_ids=[scope_id])

    resp = _public_job(admin_client, project["id"], scope_id, targets=["evil.test"])
    assert resp.status_code == 403
    assert resp.get_json()["error_code"] == "scope_violation"
    assert jobs_store.list_jobs() == []


def test_unwired_tool_cannot_be_submitted(admin_client):
    """工具闸门的**新语义**：只拒「未接入 runner」的工具（nuclei）。

    历史口径拒的是 ``nmap`` / ``dirsearch`` 这类「不允许打公网」的工具；
    测试阶段只剩一种拒绝理由 —— 未接入。``nuclei`` 登记在案但本阶段没有 runner，
    因此即使请求里显式指名，也必须在**创建任务之前**被 400 拒掉。

    守卫强度与原来一致：错误字段是 ``tools``、被点名的工具在
    ``unavailable_tools`` 里、且**库里一条任务都不能多**。
    （``blocked_tools`` 是 deprecated 兼容键，值应与 ``unavailable_tools`` 相同。）

    显式 ``mode="mock"``：本用例验的是工具闸门，不涉及真实执行链。
    """
    scope_id = _make_scope(admin_client)

    resp = _public_job(
        admin_client,
        None,
        scope_id,
        strategy="custom",
        tools=["nuclei"],
        mode="mock",
    )
    assert resp.status_code == 400, resp.get_json()
    body = resp.get_json()
    assert body["error_code"] == "bad_request"
    assert body["details"]["field"] == "tools"
    names = [item["tool_name"] for item in body["details"]["unavailable_tools"]]
    assert "nuclei" in names
    assert body["details"]["blocked_tools"] == body["details"]["unavailable_tools"]
    assert "nuclei" not in body["details"]["selectable_tools"]
    assert jobs_store.list_jobs() == []


@pytest.mark.parametrize("tool", ["nmap", "dirsearch", "naabu", "feroxbuster", "katana"])
def test_previously_blocked_tools_are_now_selectable(admin_client, tool):
    """这五个工具**现在都可以提交** —— 这是「工具开放」的实证。

    历史口径把它们列为「禁止提交」（``test_blocked_tool_cannot_be_submitted``）。
    测试阶段取消公网白名单后，它们与其它已接入工具一样可被勾选：目标合法性由
    使用者自己负责。守卫强度靠**另一半**保证：它们仍在
    ``selectable_tools()`` 里，而 ``nuclei`` 不在 —— 谁把白名单加回来，
    这条会红，闸门也仍然只拒未接入的工具。

    显式 ``mode="mock"``：本用例只验「能被接受」，绝不真的跑外部命令。
    """
    from core.tool_registry import selectable_tools

    assert tool in selectable_tools(), f"{tool} 应当可勾选"
    assert "nuclei" not in selectable_tools()

    scope_id = _make_scope(admin_client)
    resp = _public_job(
        admin_client,
        None,
        scope_id,
        strategy="custom",
        tools=[tool],
        mode="mock",
    )
    assert resp.status_code == 202, resp.get_json()
    job = jobs_store.get_job(resp.get_json()["job_id"])
    assert job["tools"] == [tool]


def test_template_cannot_be_smuggled_with_extra_tool(admin_client):
    """「选资产发现却偷偷加 nmap」必须被拒，而不是忽略多余工具。"""
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])

    resp = _public_job(
        admin_client,
        project["id"],
        scope_id,
        strategy="asset_discovery",
        tools=["subfinder", "httpx", "nmap"],
    )
    assert resp.status_code == 400
    assert jobs_store.list_jobs() == []


def test_unknown_strategy_is_400(admin_client):
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])
    resp = _public_job(admin_client, project["id"], scope_id, strategy="turbo")
    assert resp.status_code == 400
    assert jobs_store.list_jobs() == []


def test_custom_strategy_does_not_fall_back_to_the_tool_alias(admin_client, monkeypatch):
    """``custom`` 模板下「明确给了空 tools」不得让别名 ``tool`` 顶上来。

    公网入口这条链比老入口**多一层**：``resolve_strategy_tools()`` 先按模板解析，
    所以两种情形必须分开看（两侧都实测过，不是推理）：

    * ``asset_discovery``（默认模板）+ 空 ``tools``：模板本来就要 ``subfinder + httpx``，
      别名接不接管都不改变结果 —— 这条**不是**漏洞；
    * ``custom`` + 空 ``tools``：``custom`` 的语义是「工具由请求体决定」，
      于是 ``payload.get("tools") or payload.get("tool")`` 里的 ``or`` 会让别名接管 ——
      改前返回 **202 并落库 ``tools=['subfinder']``**（真的去扫），
      而 ``{"tools": []}`` 单独出现时是 400「自定义模式必须显式选择至少一个工具」。
      也就是说：**同一件事，加了一个别名就从「拒绝」变成「接受」**。

    本用例钉的是第二种。判据是「有没有给 ``tools`` 这个键」，不是「这个键的值真不真」。
    """
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])

    empty_only = _public_job(admin_client, project["id"], scope_id, strategy="custom", tools=[])
    assert empty_only.status_code == 400, empty_only.get_json()
    assert empty_only.get_json()["details"]["field"] == "tools"

    with_alias = _public_job(
        admin_client, project["id"], scope_id, strategy="custom", tools=[], tool="subfinder"
    )
    assert with_alias.status_code == 400, with_alias.get_json()
    assert with_alias.get_json()["details"]["field"] == "tools"
    # 400 但留下一条 queued 任务同样是越权执行，所以这条也要断言。
    assert jobs_store.list_jobs() == []


def test_custom_strategy_still_accepts_the_single_tool_alias(admin_client, fake_real_runner):
    """修 `or` 折叠**不得**顺手删掉别名：``custom`` 下只给 ``tool`` 时仍要能建任务。"""
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])

    resp = _public_job(admin_client, project["id"], scope_id, strategy="custom", tool="subfinder")
    assert resp.status_code == 202, resp.get_json()
    job = jobs_store.get_job(resp.get_json()["job_id"])
    assert job["tools"] == ["subfinder"]


def test_real_mode_without_env_switch_is_403_and_does_not_fall_back_to_mock(admin_client, monkeypatch):
    """环境开关没开时**明确报错**，绝不静默退化成 mock。

    「以为打了真实目标、其实拿到假数据」比报错危险得多，所以这条要锁死。
    """
    from core.safety import REAL_SCAN_ENV

    monkeypatch.delenv(REAL_SCAN_ENV, raising=False)
    scope_id = _make_scope(admin_client, active_scan=True)
    project = _make_project(admin_client, scope_ids=[scope_id])

    resp = _public_job(admin_client, project["id"], scope_id)
    assert resp.status_code == 403
    assert resp.get_json()["error_code"] == "scope_violation"
    assert REAL_SCAN_ENV in resp.get_json()["error_message"]
    assert jobs_store.list_jobs() == []


def test_real_mode_with_inactive_scope_is_403(admin_client, monkeypatch):
    """双开关的第二道：Scope.active_scan=false 时仍然拒绝。"""
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")
    scope_id = _make_scope(admin_client, active_scan=False)
    project = _make_project(admin_client, scope_ids=[scope_id])

    resp = _public_job(admin_client, project["id"], scope_id)
    assert resp.status_code == 403
    assert jobs_store.list_jobs() == []


# ── Job + Worker：公网任务进入队列并由 worker 执行（方案第 9、11 节） ──


def test_public_job_enters_queue_with_audit_record(admin_client, fake_real_runner):
    """任务进入队列 + 审计记录；响应里**不再有任何授权结论**。

    响应形状是历史 ``POST /api/jobs`` 之上追加策略与执行上下文：三个
    deprecated 键（``project_id`` / ``project_name`` / ``authorized_public``）
    **恒为 None** 并在 ``deprecated_fields`` 里自报家门。授权业务取消后，
    「这条任务被授权过」这个事实已经不存在，保留键名只为旧客户端不炸。

    其余（``status`` / ``mode`` / ``strategy`` / ``total_steps`` / 落库 / 审计）
    逐条保留 —— 闸门取消了，「任务确实被创建且可追溯」这件事一点没放松。
    """
    scope_id = _make_scope(admin_client)

    resp = _public_job(admin_client, scope_id=scope_id, targets=["www.example.test"])
    assert resp.status_code == 202, resp.get_json()
    body = resp.get_json()

    assert body["status"] == "queued"
    assert body["mode"] == "real"
    assert body["strategy"] == "asset_discovery"
    # Deprecated 授权字段：恒为 None，并如实自报。
    assert body["project_id"] is None
    assert body["project_name"] is None
    assert body["authorized_public"] is None
    assert body["deprecated_fields"] == ["project_id", "project_name", "authorized_public"]
    # 资产发现模板 = subfinder + httpx，一个目标 → 2 步
    assert body["total_steps"] == 2

    job = jobs_store.get_job(body["job_id"])
    assert job is not None
    assert job["status"] == "queued"
    assert job["mode"] == "real"
    assert sorted(job["tools"]) == ["httpx", "subfinder"]
    assert jobs_store.project_id_of_job(body["job_id"]) is None

    # 方案第 11 节：不得创建无审计任务。
    events = [event for event in audit.list_events(limit=50) if event["event_type"] == audit.EVENT_JOB_CREATED]
    assert any(event["target_id"] == body["job_id"] for event in events)


def test_worker_executes_public_job(admin_client, fake_real_runner):
    """方案第 9 节 Worker 项：任务正常执行。"""
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])

    job_id = _public_job(admin_client, project["id"], scope_id).get_json()["job_id"]
    processed = _run_once()
    assert processed >= 1

    detail = admin_client.get(f"/api/jobs/{job_id}").get_json()["job"]
    assert detail["status"] in {"succeeded", "partial"}
    assert detail["done_steps"] == detail["total_steps"]
    for step in detail["steps"]:
        assert step["status"] == "succeeded", step


def test_public_job_can_be_created_in_mock_mode_for_drills(admin_client):
    """演练用：显式 ``mode=mock`` 时不碰任何真实工具，其余闸门照旧。"""
    scope_id = _make_scope(admin_client, active_scan=False)
    project = _make_project(admin_client, scope_ids=[scope_id])

    resp = _public_job(admin_client, project["id"], scope_id, mode="mock")
    assert resp.status_code == 202
    assert resp.get_json()["mode"] == "mock"

    _run_once()
    job_id = resp.get_json()["job_id"]
    detail = admin_client.get(f"/api/jobs/{job_id}").get_json()["job"]
    assert detail["status"] == "succeeded"


def test_custom_strategy_records_chosen_tools(admin_client, fake_real_runner):
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])

    resp = _public_job(
        admin_client,
        project["id"],
        scope_id,
        strategy="custom",
        tools=["httpx"],
    )
    assert resp.status_code == 202
    assert resp.get_json()["strategy"] == "custom"
    assert jobs_store.get_job(resp.get_json()["job_id"])["tools"] == ["httpx"]


def test_web_fingerprint_strategy_never_includes_nuclei(admin_client, fake_real_runner):
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])

    job_id = _public_job(
        admin_client, project["id"], scope_id, strategy="web_fingerprint"
    ).get_json()["job_id"]
    assert jobs_store.get_job(job_id)["tools"] == ["httpx"]


# ── 禁止 Web → Runner（方案第 2、6 节） ────────────────────


def test_public_scan_api_never_calls_runners_directly():
    """公网入口只允许转交 Application Service，不得自己碰 Runner。

    这是方案第 6 节「禁止 Web → Runner」的可执行版本：有人在视图里图省事
    直接 ``build_runner(...).run(...)``，这条断言就会红。
    """
    from api import public_scan

    source = inspect.getsource(public_scan)
    for forbidden in (
        "build_runner",
        "run_tools",
        "run_single_tool",
        "run_scan",
        "RUNNER_REGISTRY",
    ):
        assert forbidden not in source, f"api/public_scan.py 出现了直达 Runner 的调用: {forbidden}"


def _module_code_source(module) -> str:
    """取模块源码，**去掉模块 docstring**。

    ★ 为什么需要这一步：``api/public_scan.py`` 的模块 docstring 里**刻意**
    写着「Policy 层仍然保留 ``validate_job_targets`` / ``validate_step_target``」
    这句口径说明 —— 它是给人读的，不是代码。若直接对 ``inspect.getsource()``
    做字面命中，注释会被当成「又加了一道闸门」，守卫随即变成锚在写法上的假警报。

    做法与 ``test_api_auth_contract.py`` 里那条「先摘 docstring 再断言」一致：
    用 AST 定位模块级 docstring 节点的源码片段并删掉，只对**代码**做断言。
    """
    import ast

    source = inspect.getsource(module)
    tree = ast.parse(source)
    docstring_node = tree.body[0] if tree.body else None
    if (
        isinstance(docstring_node, ast.Expr)
        and isinstance(docstring_node.value, ast.Constant)
        and isinstance(docstring_node.value.value, str)
    ):
        segment = ast.get_source_segment(source, docstring_node)
        if segment:
            source = source.replace(segment, "", 1)
    return source


def test_public_job_orchestration_is_in_application_service():
    """公网编排必须落在 ``core/application``，视图函数不得内联闸门。

    本轮口径：视图调用的是**新名字** ``create_open_public_job``
    （``create_authorized_public_job`` 已降级为同函数对象的 deprecated 别名，
    这条由 ``test_legacy_alias_still_points_to_the_new_entry`` 单独钉住）。
    """
    from api import public_scan

    source = inspect.getsource(public_scan)
    assert "create_open_public_job" in source
    # 视图层不得自己比较工具名单或直接落库。
    # ★ 断言的范围是**去掉 docstring 之后**的代码：模块 docstring 里点名
    #   ``validate_job_targets`` 是在解释「Policy 层还剩什么」，不是调用。
    code = _module_code_source(public_scan)
    for forbidden in (
        "assert_tools_selectable",
        "assert_tools_internet_allowed",
        "validate_job_targets",
        "create_job_with_status",
        "resolve_mode",
    ):
        assert forbidden not in code, f"api/public_scan.py 又出现了内联编排: {forbidden}"


def test_public_scan_api_does_not_reintroduce_authorization_gates():
    """视图层不得把三道授权闸门加回来（本轮取消它们，必须有反向守卫）。

    黑名单刻意是**具体调用**而不是裸词：``project_id`` 作为兼容参数名仍会出现
    （``payload.get("project_id")`` 后面接 ``create_open_public_job``），
    用裸 ``project`` 会把这条合法写法也判红，守卫随即失去意义。

    被禁的是「查项目」「验工具白名单」「内联目标校验 / 落库」这几件具体的事：
    它们必须留在 ``core.application`` / ``core.policy`` 里，只发生一次。

    ★ 同样只检查**去掉 docstring 之后**的代码，理由见 :func:`_module_code_source`。
    """
    from api import public_scan

    code = _module_code_source(public_scan)
    for forbidden in (
        "projects.require",
        "project_store",
        "require_project",
        "assert_tools_internet_allowed",
        "validate_job_targets",
        "create_job_with_status",
        "resolve_mode",
    ):
        assert forbidden not in code, f"api/public_scan.py 又出现了授权闸门: {forbidden}"


def test_service_delegates_to_single_job_entry():
    """公网入口必须**复用** ``create_scan_job``，而不是另写一条 Policy 判定。"""
    from core import application

    source = inspect.getsource(application.create_open_public_job)
    assert "create_scan_job(" in source
    assert "validate_job_targets" not in source
    assert "create_job_with_status" not in source
    # 三道授权闸门（项目 / Scope 关联 / 公网白名单）都不该回到这一层。
    for forbidden in ("projects.require", "require_project", "assert_tools_internet_allowed"):
        assert forbidden not in source, f"create_open_public_job 又把授权闸门加回来了: {forbidden}"


# ── 旧链路不受影响 ─────────────────────────────────────────


def test_legacy_job_api_still_works(admin_client):
    """新增公网入口不得破坏原有 ``POST /api/jobs``（方案第 10 节：不删旧 API）。"""
    scope_id = _make_scope(admin_client, active_scan=False)
    resp = admin_client.post(
        "/api/jobs",
        json={"scope_id": scope_id, "targets": ["a.example.test"], "tools": ["subfinder"]},
    )
    assert resp.status_code == 202
    assert resp.get_json()["mode"] == "mock"
    # 旧响应体里不应出现公网专属字段。
    assert "project_id" not in resp.get_json()


def test_projects_do_not_change_scope_semantics(app_module):
    """项目表的引入不得让 Scope 的构造/校验发生变化。"""
    scope = scope_store.create(name="不受项目影响", allowed_domains=["x.example.test"])
    assert scope.active_scan is False
    assert projects.find_by_scope(scope.id) is None


# ── 扫描中心页面（方案第 7 节「前端修改」） ────────────────


def test_scan_center_page_renders_for_anonymous(client):
    """页面本身不强制登录 —— 否则匿名用户连导航都点不进来。"""
    resp = client.get("/scan-center")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "扫描中心" in body
    # 未登录时给出明确提示，而不是渲染一个看起来能用的表单。
    assert "401 unauthenticated" in body
    # 但**骨架**不下发：匿名看到的是「为什么看不到」，不是一个空表单。
    assert 'id="strategy-list"' not in body
    assert 'id="tool-list"' not in body
    assert 'id="check-form"' not in body
    assert "步骤 1 · 确认目标" not in body


def test_anonymous_scan_center_does_not_leak_backend_jargon(client, admin_client):
    """匿名 ``/scan-center`` 不得下发骨架与后台术语（方案第 4 节原则 2）。

    ``app.py`` 的 ``scan_center()`` 视图**不强制登录**（否则匿名连导航都点不进来），
    但骨架整段是无条件渲染的 —— 实测匿名响应 8833 字符，管理员 8891 字符，
    **只差 58 字符**：三步表单、任务 / 详情两个面板、以及它们自带的后台术语
    全部白送给匿名访客。这与首页的口径不一致（首页 ``build_page_context()``
    对匿名不下发任何数据），也与方案第 4 节「前端不显示数据库字段 / 内部模型」相抵触。

    这里钉的是**术语面**，不是数据面：实体 ID（``proj_`` / ``scope_`` / ``job_``）
    与目标清单本来就没漏（``test_scan_center_page_does_not_leak_targets_or_jobs``
    管那一条）。术语同样是内部模型 —— 匿名访客没有提交动作，不需要知道
    ``mock`` / ``worker`` / ``allowed_cidrs`` 是什么。

    本轮新增 ``gef_open_scan_mode``：页面上的「开放扫描模式未开启」告警里带着
    这个词，它同样是后台术语（一个环境变量名），不该下发给匿名访客。

    刻意**不**列入黑名单的两个词：``扫描中心``（顶部导航与标题，匿名点进来必须看得到）
    与 ``401 unauthenticated``（上面那条提示故意写出来的「为什么看不到」）。

    骨架元素 id 已按**新**三步骨架更新：``scope-form`` / ``job-project`` /
    ``job-scope`` 这三个元素整体删除，因此不再列入黑名单（留着一个不存在的 id
    只会让人以为它还在某个分支里）。
    """
    body = client.get("/scan-center").get_data(as_text=True).lower()

    for term in (
        "mock",
        "queued",
        "worker",
        "python -m",
        "gef_allow_real_scan",
        "gef_open_scan_mode",
        "scope_violation",
        "allowed_domains",
        "allowed_cidrs",
        "active_scan",
        "policy",
        "scope",
        "scope_id",
    ):
        assert term not in body, f"匿名扫描中心仍下发后台术语: {term!r}"

    # 三步骨架的元素一个都不能出现（术语未必带得出，元素 id 一定带得出）。
    for element_id in (
        "check-form",
        "job-target",
        "strategy-list",
        "tool-list",
        "job-operator",
        "limits-fields",
        "job-form",
        "jobs-table",
        "job-detail-panel",
    ):
        assert f'id="{element_id}"' not in body, f"匿名扫描中心仍下发骨架元素: #{element_id}"

    # 反向守卫：登录后骨架与术语照常下发 —— 这条修的是「谁看得见」，不是「还显示不显示」。
    admin_body = admin_client.get("/scan-center").get_data(as_text=True)
    assert "步骤 1 · 确认目标" in admin_body
    # 两条告警之一必然在（本机 .env 与测试环境至少有一个开关是关的）。
    assert "GEF_OPEN_SCAN_MODE" in admin_body or "GEF_ALLOW_REAL_SCAN" in admin_body
    assert "python -m jobs.worker" in admin_body


def test_scan_center_page_has_no_scope_authorization_forms():
    """``#scope-form`` / ``#job-project`` / ``#job-scope`` 不得复活。

    历史上这三个元素是「授权项目与范围」面板的核心：``#scope-form`` 同时装着两个
    ``required`` 下拉（``#job-project`` / ``#job-scope``）和「添加授权范围」的
    ``type=submit`` 按钮。原生约束校验跑在 ``submit`` 事件**之前**：全新用户
    （两下拉都只有占位项）点按钮时 ``valueMissing=true``，浏览器直接拦下，
    ``scan_center.js:bindScopeForm`` 的 submit 回调**一次都不执行**。

    实测（headless Chrome，三状态，从真实渲染结果里原样截取该表单）：

    ==========  ============  ==============  ==================================
    状态        submit 触发   checkValidity   ``:invalid``
    ==========  ============  ==============  ==================================
    1（全空）   0 次          False           ``#job-project``、``#job-scope``
    2（有项目） 0 次          False           ``#job-scope``
    3（有范围） 1 次          True            （无）
    ==========  ============  ==============  ==================================

    状态 1 正是「全新用户第一次进来」——也就是最需要这个按钮的那一次。

    当时用 ``novalidate`` 解决；**本轮的处置更彻底**：授权项目与范围业务整体取消，
    整个面板连同这三个元素一起删掉了，于是这个坑随之消失。守卫因此改成
    **断言表单不复活** —— 谁把面板加回来，就同时把那个坑加了回来，
    而 ``novalidate`` 未必会跟着回来。

    顺带保留一小段旧口径的防线：服务端 ``POST /api/scopes`` 的 ``*`` 全放行拒绝
    与非法 CIDR 拒绝一条都没少（``test_m2_security.py`` 守着），
    删掉的是**界面**，不是范围本身的校验。
    """
    from pathlib import Path

    template = (
        Path(__file__).resolve().parents[2] / "web" / "templates" / "scan_center.html"
    ).read_text(encoding="utf-8")

    for element_id in ("scope-form", "job-project", "job-scope", "project-form", "project-list"):
        assert f'id="{element_id}"' not in template, (
            f"#${element_id} 复活了 —— 授权项目与范围业务已取消，"
            "它同时会把「首个范围建不出来」那个原生校验坑带回来。"
        )
    # 与这两个元素配套的 JS 绑定同样不该存在（绑定一个不存在的元素是死代码，
    # 而它一旦被误当作「还在生效」就会掩盖真正的缺口）。
    source = (Path(__file__).resolve().parents[2] / "web" / "static" / "scan_center.js").read_text(
        encoding="utf-8"
    )
    for removed in ("bindScopeForm", "bindProjectForm", "bindProjectSelectSync", "syncJobScopes"):
        assert removed not in source, f"scan_center.js 里 {removed} 又回来了"


def test_no_real_registrable_placeholder_domain_in_frontend():
    """前端占位域只能落在 RFC 6761 保留域里，不得写真实已注册的域名。

    ``www.example.cn`` 曾出现在 ``scan_center.html`` 的两个 ``placeholder`` 与
    ``scan_center.js`` / ``app.css`` 的注释里。``example.cn`` **不是**保留域 ——
    RFC 2606 只保留 ``example.com`` / ``example.net`` / ``example.org``
    （见 https://www.rfc-editor.org/rfc/rfc2606 ），``.test`` / ``.invalid`` /
    ``.localhost`` 由 RFC 6761 保留；而 ``example.cn`` 是别人**已经注册、
    能解析出 A 记录**的真实域名（本机单次 DNS 查询实测：`example.cn → 8.218.126.38`）。

    它只作为 ``placeholder`` 出现（浏览器不会把 placeholder 当值提交），所以不是
    扫描风险；风险是**给人看**的：下一个照着页面填的用户会把它当成可扫描目标，
    而本仓的硬约束是「不扫描任何未授权的外部目标」。统一换成 ``example.test``。

    守卫按**白名单**判：抽出所有 ``example.<tld>`` 形状的域名，落在保留集里的放行，
    其余一律报错 —— 这样 ``*.example.com``（保留）不会被误伤，
    而下一个 ``example.cn`` 这类会立刻被抓住。
    """
    import re
    from pathlib import Path

    RESERVED = {"com", "net", "org", "test", "invalid", "localhost", "example"}
    root = Path(__file__).resolve().parents[2] / "web"
    offenders = []
    for path in list(root.rglob("*.html")) + list(root.rglob("*.js")) + list(root.rglob("*.css")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"\bexample\.([a-z]{2,})\b", text):
            if match.group(1) not in RESERVED:
                lineno = text[: match.start()].count("\n") + 1
                offenders.append(
                    f"{path.relative_to(root.parent)}:{lineno} 用了非保留域 {match.group(0)!r}"
                )
    assert not offenders, "前端又出现真实可注册域名占位符：\n" + "\n".join(offenders)


def test_scan_center_page_renders_three_steps_for_admin(admin_client):
    """扫描中心现在是**三步**（本轮口径），且旧的四个步骤名不得复活。

    三步的措辞是**用户视角**的（确认目标 → 选择工具 → 创建任务）：授权业务取消
    后，「确认授权状态」这一步没有任何内容可确认，于是它与「输入目标」合并成
    「确认目标」（只做格式预检）。

    工具名（``httpx`` / ``subfinder``）由 ``scan_center.js`` 从
    ``/api/scan-center`` 动态渲染，**不在服务端页面里** —— 所以这里断言的是
    工具清单容器 ``#tool-list`` 存在，而不是某几个工具名出现在 HTML 里。
    （工具清单来自服务端这条不变量由
    ``test_scan_center_js_never_hardcodes_tool_names`` 与
    ``test_scan_center_tool_list_comes_from_server_payload`` 守着。）
    """
    resp = admin_client.get("/scan-center")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    for heading in (
        "步骤 1 · 确认目标",
        "步骤 2 · 选择工具",
        "步骤 3 · 创建任务",
    ):
        assert heading in body, heading
    assert "创建任务" in body
    # 工具清单容器必须在（内容由前端按服务端元数据填充）。
    assert 'id="tool-list"' in body
    assert 'id="strategy-list"' in body

    # 旧的四个步骤名一律不得复活：其中两个是「授权状态」那一步的残骸。
    for removed in (
        "步骤 1 · 输入目标",
        "步骤 2 · 确认授权状态",
        "步骤 3 · 选择工具",
        "步骤 4 · 创建任务",
        "确认授权范围",
        "执行模式与提交",
    ):
        assert removed not in body, f"旧的步骤名又回来了: {removed}"


def test_scan_center_page_has_no_authorization_consent(admin_client):
    """授权确认复选框**已随界面一起去掉**（本轮口径）。

    它从来不是安全边界：一个可被脚本置真的 JSON 布尔值，勾没勾都不改变服务端的
    判定（服务端只「记录」它，见 ``create_open_public_job`` 的
    ``authorization_confirmed`` 参数）。真正的边界一直是目标格式 / 排除列表 /
    Policy / 环境开关。留着它反而制造「勾了就等于放行」的错觉，
    因此本轮连界面一起删除。

    这条是反向守卫：谁把复选框加回页面，它就会红。
    （服务端**接受**这个兼容字段这件事仍由
    ``test_authorization_confirmation_is_recorded_but_is_not_a_gate`` 守着 ——
    删的是界面，不是兼容性。）
    """
    body = admin_client.get("/scan-center").get_data(as_text=True)
    assert 'id="job-consent"' not in body
    assert 'name="authorization_confirmed"' not in body
    assert "我确认该目标属于授权范围" not in body


def test_scan_center_page_has_no_authorization_summary(admin_client):
    """「授权状态摘要」三行（目标 / 授权状态 / 授权资产）**已整体删除**。

    它显示的是服务端下发的 ``blocker`` / ``matches`` —— 而预检接口已经不再返回
    这些字段（授权业务取消），摘要因此没有数据来源，留着只会渲染一行空话。
    """
    body = admin_client.get("/scan-center").get_data(as_text=True)
    for element_id in ("consent-target", "consent-status", "consent-scope"):
        assert f'id="{element_id}"' not in body, element_id
    assert "授权资产" not in body


def test_scan_center_js_has_no_authorization_pipeline():
    """前端不得残留任何授权判定 / 已删字段的用法 —— 并钉住**新**口径的正向事实。

    负向断言（旧管线整体删除）：``renderConsentSummary`` / ``BLOCKER_LABELS`` /
    ``eligible_scope_ids`` / ``applyMatchedScope`` / ``job-scope`` / ``job-project`` /
    ``internet_allowed`` 一个都不该在 ``scan_center.js`` 里。授权业务取消后，
    前端**没有任何**依据可以自己判定授权。

    正向断言（新守卫，不能只删旧断言）：工具可用性只读服务端下发的
    ``tool.selectable``；提交时用 ``sameSet`` 比较「勾选结果 vs 模板工具」，
    并据此提交 ``strategy``；勾选结果由 ``selectedCustomTools`` 读出。
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "web" / "static" / "scan_center.js").read_text(
        encoding="utf-8"
    )
    code_only = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith(("*", "//", "/*"))
    )

    for forbidden in (
        "renderConsentSummary",
        "BLOCKER_LABELS",
        "eligible_scope_ids",
        "applyMatchedScope",
        "job-scope",
        "job-project",
        "internet_allowed",
    ):
        assert forbidden not in code_only, f"scan_center.js 残留旧授权管线: {forbidden}"

    # 前端不得自己拼「已授权」的判定条件（那是服务端的事，而服务端现在也不判了）。
    for forbidden in (
        "check.real_scan_enabled =",
        "item.active_scan &&",
        "scope.active_scan && scope.allowed_domains",
    ):
        assert forbidden not in source, f"前端开始自己判定授权了: {forbidden}"

    # 正向：可用性只读服务端字段；模板只是推荐组合，提交时比集合决定 strategy。
    assert "tool.selectable" in source, "工具是否可勾选必须读服务端下发的 selectable"
    assert "function sameSet(" in source
    assert "function selectedCustomTools(" in source
    assert "function presetOf(" in source
    assert 'strategy = "custom";' in source
    assert "payload.tools = chosen;" in source


def test_scan_center_page_warns_when_real_scan_disabled(admin_client, monkeypatch):
    from core.safety import REAL_SCAN_ENV

    monkeypatch.delenv(REAL_SCAN_ENV, raising=False)
    body = admin_client.get("/scan-center").get_data(as_text=True)
    assert "真实扫描总开关未开启" in body
    assert REAL_SCAN_ENV in body


def test_scan_center_page_has_no_warning_when_enabled(admin_client, monkeypatch):
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")
    body = admin_client.get("/scan-center").get_data(as_text=True)
    assert "真实扫描总开关未开启" not in body


def test_scan_center_page_does_not_leak_targets_or_jobs(client):
    """匿名访问既看不到项目，也看不到任务（任务里含目标域名）。

    注意别把 HTML 里的字段名 ``name="scope_id"`` 当成泄漏 —— 这里要断言的是
    **实体 ID**（``proj_`` / ``scope_`` + 32 位十六进制）一个都不出现。
    """
    import re

    raw = client.get("/scan-center").get_data(as_text=True)
    assert "proj_" not in raw
    assert re.search(r"scope_[0-9a-f]{32}", raw) is None
    assert "job_" not in raw


def test_scan_center_page_requires_worker_hint(admin_client):
    """方案第 7 节的「任务列表」必须提醒 worker 没人跑时会一直排队。"""
    body = admin_client.get("/scan-center").get_data(as_text=True)
    assert "python -m jobs.worker" in body
    assert "queued" in body


def test_index_and_assets_link_to_scan_center(admin_client):
    """导航可达性：三个页面互相能找到扫描中心。"""
    assert "/scan-center" in admin_client.get("/").get_data(as_text=True)
    assert "/scan-center" in admin_client.get("/assets").get_data(as_text=True)


# ── 体验优化 Phase 1：UI 清理（下一阶段方案第 2、6 节） ──────


def test_scan_center_page_has_no_default_target(admin_client):
    """页面**没有任何默认目标**：目标输入框只有 placeholder，没有 value。

    这条守的是「不填就替你扫某个站」这类事故 —— 占位符是示例，浏览器不会提交它。

    本轮口径变更：``#scope-domains`` / ``#scope-cidrs`` 随授权面板一起删除，
    因此唯一还需要检查默认值的输入框是 ``#job-target``。检查方式不变
    （``value=`` 不得出现），只是列表缩短 —— 留着一个不存在的 id 会让用例
    在「元素被删」时报出与默认值无关的红。
    """
    import re

    body = admin_client.get("/scan-center").get_data(as_text=True)
    for field in ("job-target",):
        tag = re.search(r'<input[^>]*id="' + field + r'"[^>]*>', body)
        assert tag is not None, f"页面缺少输入框 {field}"
        assert "value=" not in tag.group(0), f"{field} 带上了默认值: {tag.group(0)}"
    # 被删掉的那两个元素不得复活（它们同样是「默认值」事故的载体）。
    assert 'id="scope-domains"' not in body
    assert 'id="scope-cidrs"' not in body


def test_scan_center_page_hides_internal_ids_in_labels(admin_client):
    """Phase 1「隐藏 Scope ID」的可执行口径：页面上不出现**实体 ID**。

    历史口径是「实体 ID 仍然存在（作为 ``<select>`` 的 value 提交），但不能出现在
    任何人类可读的文案里」—— 当时要守的是 ``scopeLabel()`` / ``projectLabel()``
    这类翻译函数，因为下拉框的 option 文案很容易直接写成 ``scope.id``。

    **本轮的变化**：下拉框与项目列表整体删除，实体 ID 在页面上已经没有任何
    落脚点（既没有 value，也没有文案）。守卫因此升级成更强的形态：
    断言页面里**不出现 ``scope_`` / ``proj_`` 形状的实体 ID**，
    与 ``test_scan_center_page_does_not_leak_targets_or_jobs`` 用同一个正则口径。

    这条同时守住「授权面板没有以别的方式复活」：任何新的下拉框 / 隐藏域只要
    带出实体 ID，都会在这里被抓住。
    """
    import re

    body = admin_client.get("/scan-center").get_data(as_text=True)
    assert re.search(r"scope_[0-9a-f]{32}", body) is None, "页面里出现了 Scope 实体 ID"
    assert re.search(r"proj_[0-9a-f]{32}", body) is None, "页面里出现了项目实体 ID"
    assert re.search(r"job_[0-9a-f]{32}", body) is None, "页面里出现了任务实体 ID"

    # 服务端模板里也不得留下「把实体 ID 当文案」的写法（源码级兜底）。
    from pathlib import Path

    template = (
        Path(__file__).resolve().parents[2] / "web" / "templates" / "scan_center.html"
    ).read_text(encoding="utf-8")
    for forbidden in ("scope.id", "project.id", "scope_id }}", "project_id }}"):
        assert forbidden not in template, f"scan_center.html 又把内部 ID 渲染出来了: {forbidden}"


def test_scan_center_target_label_mentions_url(admin_client):
    """方案第 6 节：用户输入写成「域名、IP、URL」—— 页面标签必须如实照抄。

    ``core/scope.py:normalize_target`` 一直支持 URL（``https://a.example.test/x``
    会被规范化成 ``a.example.test``），但页面上只写了「域名 / IP / 网段」。
    标签少写一种输入，用户就会以为贴 URL 会被拒 —— 而他手上正好只有 URL。
    """
    body = admin_client.get("/scan-center").get_data(as_text=True)
    assert "域名 / IP / 网段 / URL" in body


def test_scan_center_js_submits_the_current_target_input_not_a_stale_snapshot():
    """目标改了但没重新确认时，提交的必须是**输入框此刻的内容**。

    这是 Phase 1 审计发现的缺口：``bindJobForm`` 原来写的是
    ``lastTargets.length ? lastTargets : splitList($("job-target").value)`` ——
    「检查授权 → 改输入框 → 直接创建任务」会提交**改前**的目标，而页面上的
    绿灯说的是改后的那个站。服务端按改前的判定，用户看到的是另一个结论，
    这是最难排查的一类错（两边都没报错，只是说的不是同一件事）。

    测试阶段步骤 1 从「检查授权」改成「确认目标」，缺口**形状变了但风险一样**：
    现在是「确认目标 → 改输入框 → 直接创建任务」。

    三条一起守：
    ① 有 ``currentTargets()`` 这个唯一事实来源，提交路径读的是它而不是快照；
    ② 输入框有 ``input`` 监听，改了就让旧结论失效；
    ③ 旧结论还在但已经对不上输入时，兜底**拒绝提交**，而不是拿它当依据。
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "web" / "static" / "scan_center.js").read_text(
        encoding="utf-8"
    )
    assert "function currentTargets()" in source
    assert "function checkIsFresh()" in source
    assert "function invalidateCheckResult()" in source

    # ① 提交路径取当前输入，不再优先用上一次预检的快照。
    assert "var targets = currentTargets();" in source
    assert 'lastTargets.length ? lastTargets' not in source, "提交又回到快照了"

    # ② 输入框上的监听器是「改了就让旧结论失效」的唯一入口。
    assert 'input.addEventListener("input", function () {' in source
    assert "invalidateCheckResult();" in source
    # ③ 旧结论还在但已经对不上输入时，兜底拒绝提交。
    assert "lastCheckPayload && !checkIsFresh()" in source


def test_scan_center_frontend_shows_and_forwards_the_pace(admin_client):
    """前端必须**显示**节奏，并把选中的节奏**原样转发**。

    两条都不可少：
    * 只显示不转发 —— 请求里没有 ``pace``，服务端按模板档位走，恰好也对（模板是
      ``light``），但页面显示与请求内容从此可以悄悄不一致；
    * 只转发不显示 —— 用户不知道自己要打多快，等于把降速做成暗箱。

    这里只做源码级守卫（项目没有浏览器测试）；渲染路径另用一次性 DOM 桩人工核对过。
    """
    from pathlib import Path

    js_path = Path(__file__).resolve().parents[2] / "web" / "static" / "scan_center.js"
    source = js_path.read_text(encoding="utf-8")

    # 显示：卡片上有节奏行，文案来自服务端元数据（不写死）。
    assert "sc-strategy-pace" in source
    assert "paceLabelOf(" in source and "paceIndex" in source
    assert 'pace: ACTIVE_PACE' in source, "提交时没有把当前节奏带上"

    css_path = Path(__file__).resolve().parents[2] / "web" / "static" / "app.css"
    assert ".sc-strategy-pace" in css_path.read_text(encoding="utf-8")


def test_scan_center_js_does_not_hardcode_pace_wording(admin_client):
    """节奏的中文说明只应来自服务端（``paces[]``），前端不得写死第二份。

    写死就会漂移：后端把「低频」的解释改了，页面还停在上一个版本。
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "web" / "static" / "scan_center.js").read_text(
        encoding="utf-8"
    )
    for hardcoded in ("降低并发与请求速率", "使用工具默认并发与速率"):
        assert hardcoded not in source, f"scan_center.js 写死了节奏说明: {hardcoded}"


def test_scan_center_page_lists_unwired_tools_in_their_own_group(admin_client):
    """未接入的工具（``nuclei``）**列在自己的能力分组里并置灰**，不另开一块说明。

    历史口径下这条叫 ``test_scan_center_page_separates_restricted_tools_note``：
    那时有一块独立的 ``#restricted-note`` 容器，专门写「本阶段未接入/未开放的工具」。
    测试阶段的工具清单改成**按能力分组渲染**，未接入项归到它自己声明的分组
    （``nuclei`` → 漏洞检测）下，容器随之删除 —— 留着它会让同一个结论在页面上
    出现两处，而两处迟早会不一致。

    新守卫分两半，比原来更强：

    * **服务端**：``nuclei`` 必须同时出现在 ``restricted_tools`` 与它所在分组的
      ``tools`` 里，且 ``selectable is False``，``reason`` 非空；
    * **源码**：``scan_center.js`` 必须真的把 ``restricted`` 渲染进分组网格，
      而不是把它们过滤掉 —— 过滤掉就等于「藏起来」，使用者会以为是自己没找到。
    """
    from pathlib import Path

    payload = admin_client.get("/api/scan-center").get_json()
    restricted = {item["tool_name"]: item for item in payload["restricted_tools"]}
    assert "nuclei" in restricted, "nuclei 必须出现在 restricted_tools 里"
    assert restricted["nuclei"]["selectable"] is False
    assert restricted["nuclei"]["reason"], "未接入的工具必须给出原因，否则界面只能显示一个无语的灰块"
    # ``restricted_tools`` 不是一份「扁平黑名单」：每条都自带 ``tool_group``，
    # 前端据此把它摆进对应栏位。少了这个字段，「归到自己声明的分组」就无从谈起。
    assert restricted["nuclei"]["tool_group"] == "vuln", "nuclei 没有声明它属于「漏洞检测」"

    vuln_group = next((g for g in payload["tool_groups"] if g["key"] == "vuln"), None)
    assert vuln_group is not None, "漏洞检测分组不见了"
    assert "nuclei" not in [tool["tool_name"] for tool in vuln_group["tools"]], (
        "nuclei 混进了可勾选清单：它没有 runner，不能出现在这里"
    )
    assert vuln_group["tools"] == [], "本阶段漏洞检测栏确实没有已接入的工具"

    source = (Path(__file__).resolve().parents[2] / "web" / "static" / "scan_center.js").read_text(
        encoding="utf-8"
    )
    assert "unopened.forEach(function (tool) {" in source, (
        "scan_center.js 不再把未接入的工具渲染进分组：它们被藏起来了，"
        "使用者会以为是自己没找到"
    )
    assert "grid.appendChild(renderToolRow(tool, preset));" in source


def test_scan_center_page_does_not_copy_any_strategy_description(admin_client):
    """模板说明在 HTML 里**不得**出现第二份（首屏兜底也不行）。

    模板说明的唯一来源是服务端下发的 ``strategies[].description``。此前
    ``#strategy-note`` 的初始文本逐字抄了「资产发现」那一档的描述 —— 它会在 JS
    拉完元数据后被覆盖，所以肉眼几乎看不见；但后端一改描述，这段 HTML 就静默过期，
    而当时**没有任何守卫**盯着它（节奏说明有守卫，策略说明没有）。

    判据不是「有没有这句话」，而是**服务端当前下发的每一段 description 逐字都
    不在页面里** —— 后端改了描述，这条仍然成立；谁再抄一份，它立刻红。
    """
    from core.tool_registry import list_strategies

    body = admin_client.get("/scan-center").get_data(as_text=True)
    for strategy in list_strategies():
        assert strategy.description not in body, (
            f"scan_center.html 抄了一份模板说明（{strategy.key}）：{strategy.description}"
        )
    # 占位本身仍要留着：JS 拉不到元数据时用户得看到一句人话，而不是空白。
    assert 'id="strategy-note"' in body


# ── Phase 1「增加工具选择」+ 方案第 8、9 节：工具清单 ────────


def test_scan_center_page_always_shows_the_tool_list(admin_client):
    """工具清单**始终可见**，不再只在「自定义模式」下才出现。

    方案第 8 节把「用户无法主动选择工具」列为当前最大缺失。原来清单藏在
    ``<div id="custom-tools" hidden>`` 里 —— 选模板时用户根本看不到这次要跑什么。
    现在模板模式下列表仍然列出（置灰，说明由模板决定），自定义模式才可勾选。
    """
    import re

    body = admin_client.get("/scan-center").get_data(as_text=True)
    tag = re.search(r'<div[^>]*id="custom-tools"[^>]*>', body)
    assert tag is not None, "页面缺少工具清单容器"
    assert "hidden" not in tag.group(0), "工具清单又被藏起来了"
    assert 'id="tool-list"' in body
    assert 'id="tool-list-note"' in body


def test_scan_center_js_never_hardcodes_tool_names():
    """方案第 9 节：**不要把工具写死在前端**。

    这是那一条的可执行版本。工具清单必须整体来自服务端下发的
    ``/api/scan-center``（``tools`` + ``restricted_tools``）；前端一旦出现字面量
    工具名，新增工具就得改两处，而漏改的那一处不会报错 —— 只会静默不显示。

    **黑名单必须从注册表派生**：此前这里手写了 7 个工具名
    （``subfinder`` / ``httpx`` / ``nmap`` / ``naabu`` / ``nuclei`` / ``katana`` /
    ``feroxbuster``），于是把 ``dnsx`` / ``amass`` / ``gospider`` / ``waybackurls`` /
    ``dirsearch`` 等**另外 11 个**写进前端时守卫一律放行 —— 守卫看起来在守，
    实际只守住三分之一。现在逐个走 ``get_supported_runners()`` 与
    ``KNOWN_UNAVAILABLE_TOOLS``，注册表加一个工具，守卫自动覆盖它。
    """
    from pathlib import Path

    from core.tool_registry import KNOWN_UNAVAILABLE_TOOLS
    from modules.registry import get_supported_runners

    source = (Path(__file__).resolve().parents[2] / "web" / "static" / "scan_center.js").read_text(
        encoding="utf-8"
    )
    # 方案第 9 节点名禁止的写法。
    for hardcoded in ("checkbox = httpx", "checkbox = subdomain"):
        assert hardcoded not in source, f"前端写死了工具: {hardcoded}"
    # 工具名一律以字符串字面量形式出现也不行 —— 白名单有 17 个工具，逐个写死
    # 正是方案要避免的形态。工具名只允许出现在注释与 docstring 里。
    code_only = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith(("*", "//", "/*"))
    )
    registry_tools = sorted(set(get_supported_runners()) | set(KNOWN_UNAVAILABLE_TOOLS))
    assert len(registry_tools) >= 18, f"注册表读出点异常，守卫会形同虚设: {registry_tools}"
    for name in registry_tools:
        assert f'"{name}"' not in code_only, f"前端代码里出现了写死的工具名: {name}"


def test_scan_center_tool_list_comes_from_server_payload():
    """工具清单的**数据源**必须是服务端响应，而不是任何本地常量表。"""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "web" / "static" / "scan_center.js").read_text(
        encoding="utf-8"
    )
    # 渲染入口接的是 center.tools / center.restricted_tools（+ 服务端下发的分组）。
    assert "center.tools," in source and "center.restricted_tools," in source
    assert "center.tool_groups" in source
    # 切换模板时复用同一份服务端数据（不发第二次请求、不另建常量表）。
    assert "metadata.tools," in source and "metadata.restricted_tools," in source
    assert "metadata.tool_groups" in source


def test_scan_center_js_renders_groups_from_server_metadata():
    """分组栏位必须整体来自服务端，前端不出现写死的分组中文名。"""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "web" / "static" / "scan_center.js").read_text(
        encoding="utf-8"
    )
    code_only = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith(("*", "//", "/*"))
    )
    # 分组名（含方案第 8 节点名的那几栏）不得以字符串字面量出现在代码里；
    # 它们只允许存在于注释中 —— 一旦写死，后端调整分组前端就不会跟着变。
    for group_name in ("资产发现", "服务识别", "技术识别", "内容发现", "漏洞检测", "辅助能力"):
        assert f'"{group_name}"' not in code_only, f"前端写死了分组名: {group_name}"
    # 空分组要如实说明，而不是渲染一个空框。
    assert "本阶段暂无可用工具" in source
    # 工具用途说明同样来自服务端字段。
    assert "tool.description" in source
    # 未开放的工具也必须按**它自己声明的**分组归位（nuclei → vuln），
    # 前端不得出现「nuclei 属于漏洞检测」这类写死的映射。
    assert "tool.tool_group === group.key" in source
    for mapping in ('"nuclei": "vuln"', "nuclei →", 'if (tool.tool_name === "nuclei")'):
        assert mapping not in source, f"前端写死了受限工具的分组映射: {mapping}"


def test_scan_center_metadata_tools_carry_what_the_frontend_needs(admin_client):
    """前端渲染一个工具需要哪些字段，这里逐项钉死（改字段名就会红）。"""
    body = admin_client.get("/api/scan-center").get_json()
    entry = next(item for item in body["tools"] if item["tool_name"] == "subfinder")
    for field in ("tool_name", "risk_level", "risk_label", "internet_allowed",
                  "default_enabled", "reason",
                  # Tool Registry（方案第 9 节）：没有这三项，前端就只能写死说明与分组。
                  "description", "tool_group", "tool_group_label"):
        assert field in entry, f"工具条目缺少前端需要的字段: {field}"
    assert entry["description"], "工具条目必须带上用途说明"
    assert entry["tool_group"], "工具条目必须带上能力分组"


def test_scan_center_metadata_carries_tool_groups(admin_client):
    """扫描中心必须下发**分组**本身（名称与说明），否则前端还是要写死中文栏位。"""
    body = admin_client.get("/api/scan-center").get_json()
    groups = body["tool_groups"]
    assert [group["key"] for group in groups] == [
        "recon", "service", "tech", "content", "vuln", "assist",
    ]
    for group in groups:
        for field in ("key", "name", "description", "tools"):
            assert field in group, f"分组缺少字段: {field}"
        assert group["description"], f"分组 {group['key']} 没有说明"

    # 分组里的条目与扁平列表是**同一份** to_dict() 结果：同一个工具不可能
    # 在扁平列表里叫一个名字、在分组里叫另一个。
    flat = {item["tool_name"]: item for item in body["tools"]}
    for group in groups:
        for tool in group["tools"]:
            assert tool["tool_group"] == group["key"]
            assert flat[tool["tool_name"]] == tool, (
                f"{tool['tool_name']} 在扁平列表与分组里的元数据不一致"
            )

    # 分组视图与扁平清单同集：未接入的 nuclei **不**混进「能跑的工具」里，
    # 它由 restricted_tools 单独承载（否则前端会把它当成可勾选的工具）。
    grouped = [tool["tool_name"] for group in groups for tool in group["tools"]]
    assert sorted(grouped) == sorted(flat)
    assert "nuclei" not in grouped

    # 「技术识别 / 漏洞检测」本阶段确实没有可跑的工具，必须如实返回空栏位，
    # 而不是把别的工具挪进去凑数 —— 空栏位前端会显示「本阶段暂无可用工具」。
    empty_groups = [group["key"] for group in groups if not group["tools"]]
    assert "tech" in empty_groups


def test_scan_center_restricted_tools_carry_registry_fields(admin_client):
    """受限未开放的条目同样要带说明，否则前端只能写死一句「未接入」。"""
    body = admin_client.get("/api/scan-center").get_json()
    nuclei = next(item for item in body["restricted_tools"] if item["tool_name"] == "nuclei")
    for field in ("description", "tool_group", "tool_group_label", "reason"):
        assert nuclei[field], f"受限条目缺少字段: {field}"
    assert nuclei["tool_group"] == "vuln"
    assert nuclei["internet_allowed"] is False


def test_tools_api_and_scan_center_agree_on_registry_fields(admin_client, client):
    """/api/tools 与 /api/scan-center 必须给出**同一份**注册表字段。

    方案第 9 节让前端读 ``GET /api/tools``，而工具清单的渲染入口目前在
    ``/api/scan-center``；两个接口各写一份取数逻辑正是「改一处漏一处」的来源，
    因此这里逐字段比对（``category`` 除外：它是运行器自报的**观测类别**，
    只有 /api/tools 会去实例化 runner，扫描中心刻意不实例化）。
    """
    tools_body = client.get("/api/tools").get_json()
    assert [group["key"] for group in tools_body["groups"]] == [
        "recon", "service", "tech", "content", "vuln", "assist",
    ]

    from_center = {
        item["tool_name"]: item
        for item in admin_client.get("/api/scan-center").get_json()["tools"]
    }
    for entry in tools_body["tools"]:
        center_entry = from_center[entry["tool_name"]]
        for field in ("risk_level", "risk_label", "internet_allowed",
                      "default_enabled", "reason", "description",
                      "tool_group", "tool_group_label"):
            assert entry[field] == center_entry[field], (
                f"{entry['tool_name']} 的 {field} 在两个接口间不一致"
            )


def test_tools_api_keeps_the_historical_name_key(admin_client, client):
    """历史键名 ``name`` 与 ``category`` 不能被这次改造冲掉（脚本在用）。"""
    body = client.get("/api/tools").get_json()
    for entry in body["tools"]:
        assert entry["name"] == entry["tool_name"]
        assert entry["category"] in {"subdomain", "url", "alive", "web", "port"}
        # 观测类别与工具分组是两件事，不能互相顶替。
        assert entry["category"] != entry["tool_group"]


def test_tools_api_groups_cover_only_registered_runners(client):
    """``/api/tools`` 匿名可读，不暴露「还差哪些工具」：分组里只有已接入 runner。"""
    from modules.registry import get_supported_runners

    body = client.get("/api/tools").get_json()
    names = [tool["tool_name"] for group in body["groups"] for tool in group["tools"]]
    assert sorted(names) == sorted(get_supported_runners())
    assert "nuclei" not in names


def test_both_registry_readouts_agree_on_the_groups_view(admin_client, client):
    """两个读出点的**分组视图**必须逐字段相同（此前只比对过扁平清单）。

    两处调用现在看起来一样（都传 ``list_tool_policies()``），但它是**两个独立的
    调用点**（``api/tools.py`` 与 ``api/public_scan.py``）。只要有人把其中一处改成
    ``list_all_tool_policies()``（默认值就是它），`vuln` 栏就会在一个接口里空、
    在另一个接口里冒出 ``nuclei`` —— 而扁平清单的逐字段比对**不会红**，
    因为 ``nuclei`` 本来就不在扁平清单里。这条用例防的就是这个静默漂移。

    同时锁住「未接入的工具只能从 ``restricted_tools`` 走，不能混进分组」：
    分组的 ``vuln`` 栏在两个接口里都必须是空栏位（如实呈现「本阶段做不到」）。
    """
    from_tools = client.get("/api/tools").get_json()["groups"]
    from_center = admin_client.get("/api/scan-center").get_json()["tool_groups"]

    assert [group["key"] for group in from_tools] == [group["key"] for group in from_center]
    for left, right in zip(from_tools, from_center, strict=True):
        assert left == right, f"分组 {left['key']} 在两个接口间不一致"

    vuln = next(group for group in from_center if group["key"] == "vuln")
    assert vuln["tools"] == [], "未接入的 nuclei 不得混进分组视图"
    # 「存在但本阶段不可用」只能由 restricted_tools 承载，且两个接口都不得改写它。
    assert [item["tool_name"] for item in admin_client.get("/api/scan-center").get_json()["restricted_tools"]] == [
        "nuclei"
    ]
    assert "restricted_tools" not in client.get("/api/tools").get_json()


def test_scan_center_page_renders_recent_jobs(admin_client, fake_real_runner):
    """创建过的公网任务要出现在扫描中心的任务列表里。"""
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])
    job_id = _public_job(admin_client, project["id"], scope_id).get_json()["job_id"]

    body = admin_client.get("/scan-center").get_data(as_text=True)
    assert job_id in body


# ── Phase 3：Scan Profile = 工具组合 + 节奏（下一阶段方案第 5、6 节） ──
#
# 这一节要证的不是「页面显示了低频」，而是「低频真的改变了发出去的请求」：
# 后端把节奏翻译成 Runner 的 ``-t`` / ``-rl``，并在真实步骤之间留出间隔。
# 所有 real 用例都把 ``build_runner`` 换成假 runner，因此没有任何外部流量。


def test_scan_center_metadata_exposes_paces(admin_client, monkeypatch):
    """``/api/scan-center`` 必须下发档位元数据 —— 否则页面只能写死一份文案。"""
    # conftest 把低频间隔钉成 0（测试不该为礼貌间隔付墙钟），这里恢复一个
    # 真实值来验证「元数据如实反映当前设置」，而不是断言一个写死的常数。
    monkeypatch.setenv("GEF_PACE_LIGHT_STEP_DELAY_SEC", "1.5")
    body = admin_client.get("/api/scan-center").get_json()

    paces = {item["pace"]: item for item in body["paces"]}
    assert set(paces) == {"light", "normal"}
    assert paces["light"]["pace_label"] == "低频"
    assert paces["light"]["pace_description"]
    # 低频档必须真的带一个正的间隔秒数，否则「低频」与「常规」没有可观察差别。
    assert paces["light"]["step_delay_seconds"] == 1.5
    assert paces["normal"]["step_delay_seconds"] == 0


def test_every_strategy_card_carries_its_pace(admin_client):
    """每张策略卡片都要带节奏 —— 卡片是用户唯一能看见 Profile 全貌的地方。"""
    body = admin_client.get("/api/scan-center").get_json()

    for strategy in body["strategies"]:
        assert strategy["pace"] in {"light", "normal"}, strategy
        assert strategy["pace_label"], strategy


def test_public_job_defaults_to_the_template_pace(admin_client, fake_real_runner):
    """资产发现模板的缺省档是 ``light``：公网测试的第一步必须最保守。"""
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])

    resp = _public_job(admin_client, project["id"], scope_id)
    assert resp.status_code == 202, resp.get_json()
    body = resp.get_json()
    assert body["pace"] == "light"
    assert body["pace_label"] == "低频"

    # 落库的创建事件与审计都要记下这一档，否则「这个任务当时按什么节奏跑」不可查。
    job_id = body["job_id"]
    created = next(
        event for event in jobs_store.list_events(job_id) if event["event_type"] == jobs_store.EVENT_JOB_CREATED
    )
    assert created["detail"]["pace"] == "light"
    assert jobs_store.pace_of_job(job_id) == "light"

    audited = [
        event for event in audit.list_events(limit=50) if event["event_type"] == audit.EVENT_JOB_CREATED
    ]
    entry = next(event for event in audited if event["target_id"] == job_id)
    assert entry["detail"]["pace"] == "light"


def test_public_job_request_cannot_relax_the_template_pace(admin_client, fake_real_runner):
    """**只能收紧**：请求里写 ``pace=normal`` 也改不回常规档。

    这是本阶段最重要的一条 —— 如果请求能放松模板档位，那么「低频资产发现」
    就只是一个可以被一次 HTTP 请求改掉的界面文案。
    """
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])

    resp = _public_job(admin_client, project["id"], scope_id, pace="normal")
    assert resp.status_code == 202, resp.get_json()
    assert resp.get_json()["pace"] == "light"
    assert jobs_store.pace_of_job(resp.get_json()["job_id"]) == "light"


def test_public_job_accepts_an_explicit_light_pace(admin_client, fake_real_runner):
    """显式写 ``light`` 与缺省结果一致（幂等，不产生第二份判定）。"""
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])

    resp = _public_job(admin_client, project["id"], scope_id, pace="light")
    assert resp.status_code == 202
    assert resp.get_json()["pace"] == "light"


def test_public_job_rejects_an_unknown_pace(admin_client, fake_real_runner):
    """非法档位必须 400 而不是静默回退 —— 写错 ``low`` 却拿到常规档最危险。"""
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])

    resp = _public_job(admin_client, project["id"], scope_id, pace="low")
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error_code"] == "bad_request"
    assert body["details"]["field"] == "pace"
    assert body["details"]["supported"] == ["light", "normal"]
    assert jobs_store.list_jobs() == []


def test_job_detail_reports_the_pace(admin_client, fake_real_runner):
    """``GET /api/jobs/<id>`` 要能回答「这个任务是按什么节奏跑的」。"""
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])
    job_id = _public_job(admin_client, project["id"], scope_id).get_json()["job_id"]

    detail = admin_client.get(f"/api/jobs/{job_id}").get_json()["job"]
    assert detail["pace"] == "light"


def test_legacy_job_entry_still_defaults_to_normal(admin_client):
    """历史入口（``POST /api/jobs``，不带模板）必须保持引入前的行为：常规档。

    这是 Phase 3 的回归底线 —— 加了 Scan Profile 不能让老调用方突然变慢。
    """
    scope_id = _make_scope(admin_client, active_scan=False)
    resp = admin_client.post(
        "/api/jobs",
        json={"scope_id": scope_id, "targets": ["www.example.test"], "tools": ["subfinder"]},
    )
    assert resp.status_code == 202, resp.get_json()
    body = resp.get_json()
    assert body["pace"] == "normal"
    assert jobs_store.pace_of_job(body["job_id"]) == "normal"


def test_legacy_job_entry_accepts_an_explicit_light_pace(admin_client):
    """老入口也能被**收紧**到低频档（运维手工降速的正规入口）。"""
    scope_id = _make_scope(admin_client, active_scan=False)
    resp = admin_client.post(
        "/api/jobs",
        json={
            "scope_id": scope_id,
            "targets": ["www.example.test"],
            "tools": ["subfinder"],
            "pace": "light",
        },
    )
    assert resp.status_code == 202
    assert resp.get_json()["pace"] == "light"


def test_legacy_job_entry_rejects_an_unknown_pace(admin_client):
    scope_id = _make_scope(admin_client, active_scan=False)
    resp = admin_client.post(
        "/api/jobs",
        json={
            "scope_id": scope_id,
            "targets": ["www.example.test"],
            "tools": ["subfinder"],
            "pace": "turbo",
        },
    )
    assert resp.status_code == 400
    assert resp.get_json()["details"]["field"] == "pace"
    assert jobs_store.list_jobs() == []


def test_light_pace_reaches_the_runner_config(admin_client, monkeypatch):
    """低频档必须在**真正发起请求的那一层**生效：Runner 的 config 被换成低预算。

    只断言「接口返回了 pace=light」是不够的 —— 那只能证明它被显示了。
    这里检查 Runner 实际拿到的 ``config``，也就是 ``build_command`` 读的那个对象。
    """
    from core import pace as pace_module
    from jobs.executor import execute_job

    seen = []

    class _RecordingRunner:
        category = "subdomain"
        config = {"threads": 50, "timeout": 10}
        last_execution = {}

        def __init__(self, tool_name):
            # 假 Runner 必须如实报出自己被要求扮演的工具名：节奏预算按
            # ``tool_name`` 查表，报错了就查不到预算（那正是这条用例要抓的）。
            self.tool_name = tool_name

        def run(self, target):
            seen.append(dict(self.config))
            from core.runner_result import RunnerResult

            return RunnerResult.ok([], exit_code=0)

    monkeypatch.setattr("modules.registry.build_runner", lambda name: _RecordingRunner(name))
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")

    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])
    job_id = _public_job(admin_client, project["id"], scope_id, strategy="web_fingerprint").get_json()["job_id"]

    jobs_store.claim_next_job("w-pace", lease_seconds=300)
    execute_job(job_id)

    assert seen, "Runner 一次都没被调用"
    assert seen[0]["threads"] == pace_module.LIGHT_TOOL_BUDGET["httpx"]["threads"]
    assert seen[0]["rate_limit"] == pace_module.LIGHT_TOOL_BUDGET["httpx"]["rate_limit"]


def test_normal_pace_leaves_the_runner_config_untouched(admin_client, monkeypatch):
    """常规档不得改写 Runner 的 config —— 这是「历史行为不变」的可执行口径。"""
    from jobs.executor import execute_job

    seen = []

    class _RecordingRunner:
        tool_name = "subfinder"
        category = "subdomain"
        config = {"threads": 50, "timeout": 10}
        last_execution = {}

        def run(self, target):
            seen.append(dict(self.config))
            from core.runner_result import RunnerResult

            return RunnerResult.ok([], exit_code=0)

    monkeypatch.setattr("modules.registry.build_runner", lambda name: _RecordingRunner())
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")

    scope_id = _make_scope(admin_client, active_scan=True)
    job_id = admin_client.post(
        "/api/jobs",
        json={"scope_id": scope_id, "targets": ["www.example.test"], "tools": ["subfinder"], "mode": "real"},
    ).get_json()["job_id"]

    jobs_store.claim_next_job("w-pace", lease_seconds=300)
    execute_job(job_id)

    assert seen == [{"threads": 50, "timeout": 10}]
    assert "rate_limit" not in seen[0]


def test_light_pace_waits_between_real_steps(admin_client, monkeypatch):
    """低频档在真实步骤之间真的会等 —— 这是「礼貌间隔」的唯一可观察证据。"""
    import time as time_module

    from jobs.executor import execute_job

    class _Runner:
        tool_name = "subfinder"
        category = "subdomain"
        config = {}
        last_execution = {}

        def run(self, target):
            from core.runner_result import RunnerResult

            return RunnerResult.ok([], exit_code=0)

    monkeypatch.setattr("modules.registry.build_runner", lambda name: _Runner())
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")
    monkeypatch.setenv("GEF_PACE_LIGHT_STEP_DELAY_SEC", "0.3")

    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])
    # 资产发现 = subfinder + httpx，一个目标 → 2 步 → 1 个间隔。
    job_id = _public_job(admin_client, project["id"], scope_id).get_json()["job_id"]

    jobs_store.claim_next_job("w-pace", lease_seconds=300)
    started = time_module.perf_counter()
    execute_job(job_id)
    elapsed = time_module.perf_counter() - started

    assert elapsed >= 0.3, f"低频档没有在步骤之间等待（耗时 {elapsed:.3f}s）"


def test_normal_pace_does_not_wait_between_real_steps(admin_client, monkeypatch):
    """常规档一次等待都不该多出来（引入 Scan Profile 前是什么样，现在还是）。"""
    import time as time_module

    from jobs.executor import execute_job

    class _Runner:
        tool_name = "subfinder"
        category = "subdomain"
        config = {}
        last_execution = {}

        def run(self, target):
            from core.runner_result import RunnerResult

            return RunnerResult.ok([], exit_code=0)

    monkeypatch.setattr("modules.registry.build_runner", lambda name: _Runner())
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")
    # 即使低频间隔被设得很大，常规档也**不能**受它影响。
    monkeypatch.setenv("GEF_PACE_LIGHT_STEP_DELAY_SEC", "5")

    scope_id = _make_scope(admin_client, active_scan=True)
    job_id = admin_client.post(
        "/api/jobs",
        json={
            "scope_id": scope_id,
            "targets": ["a.www.example.test", "b.www.example.test"],
            "tools": ["subfinder"],
            "mode": "real",
        },
    ).get_json()["job_id"]

    jobs_store.claim_next_job("w-pace", lease_seconds=300)
    started = time_module.perf_counter()
    execute_job(job_id)
    elapsed = time_module.perf_counter() - started

    assert elapsed < 2.0, f"常规档多出了等待（耗时 {elapsed:.3f}s）"


def test_retry_keeps_the_original_pace(admin_client, fake_real_runner):
    """retry 之后节奏必须还是原来那一档：它属于任务，不属于某一次执行。"""
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])
    job_id = _public_job(admin_client, project["id"], scope_id).get_json()["job_id"]

    jobs_store.claim_next_job("w-pace", lease_seconds=300)
    jobs_store.finish_job(job_id, status=jobs_store.STATUS_FAILED)
    assert jobs_store.retry_job(job_id)["status"] == "queued"

    assert jobs_store.pace_of_job(job_id) == "light"


def test_pace_of_job_falls_back_for_legacy_rows(app_module):
    """没有创建事件的老任务读回缺省档，而不是抛异常把 worker 弄停。"""
    from core import pace as pace_module

    assert jobs_store.pace_of_job("job_does_not_exist") == pace_module.DEFAULT_PACE


# ── Phase 3：公网授权测试完善（规划方案第 14 节 Phase 3，五项） ──
#
# 五项 = 操作者记录 / 授权备注 / 扫描策略 / 限速配置 / 超时配置。
# 前四项的落库位置都是 ``job.created`` 事件 detail（**零 schema 变更**，见
# ``core/jobs.py:_created_detail``）；限速与超时额外要在**真正发请求的那一层**
# 生效，因此这一节最后几条直接检查 Runner 拿到的 ``config``。
#
# 与上一节同一条纪律：所有 real 用例都把 ``build_runner`` 换成假 runner，
# 因此整节没有任何外部流量。


def test_public_job_records_the_operator(admin_client, fake_real_runner):
    """操作者要写进创建事件**与**审计 —— 事后能回答「这条任务是谁提交的」。"""
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])

    resp = _public_job(admin_client, project["id"], scope_id, operator="张三")
    assert resp.status_code == 202, resp.get_json()
    job_id = resp.get_json()["job_id"]
    assert resp.get_json()["operator"] == "张三"

    created = next(
        event for event in jobs_store.list_events(job_id) if event["event_type"] == jobs_store.EVENT_JOB_CREATED
    )
    assert created["detail"]["operator"] == "张三"
    assert jobs_store.operator_of_job(job_id) == "张三"

    audited = [
        event for event in audit.list_events(limit=50) if event["event_type"] == audit.EVENT_JOB_CREATED
    ]
    entry = next(event for event in audited if event["target_id"] == job_id)
    # 方案第 7 节的四要素：operator · target · timestamp · scope_id。
    assert entry["detail"]["operator"] == "张三"
    assert entry["detail"]["targets"] == ["www.example.test"]
    assert entry["detail"]["scope_id"] == scope_id
    assert entry["created_at"], "审计记录必须带时间戳"


def test_public_job_without_operator_falls_back_to_the_default(admin_client, fake_real_runner):
    """不填操作者时留下缺省标识，**不留空值** —— 审计里必须有答案。"""
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])

    job_id = _public_job(admin_client, project["id"], scope_id).get_json()["job_id"]
    assert jobs_store.operator_of_job(job_id) == jobs_store.DEFAULT_OPERATOR
    detail = admin_client.get(f"/api/jobs/{job_id}").get_json()["job"]
    assert detail["operator"] == jobs_store.DEFAULT_OPERATOR


@pytest.mark.parametrize("bad", [True, ["张三"], {"name": "张三"}])
def test_public_job_rejects_a_bad_operator(admin_client, fake_real_runner, bad):
    """操作者形状非法 → 400，且**不落库**（不能留下一条来历不明的任务）。"""
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])

    resp = _public_job(admin_client, project["id"], scope_id, operator=bad)
    assert resp.status_code == 400
    assert resp.get_json()["details"]["field"] == "operator"
    assert jobs_store.list_jobs() == []


def test_public_job_does_not_snapshot_any_project_authorization_note(admin_client, fake_real_runner):
    """**本轮取消的行为**：任务不再从项目上取授权备注。

    历史口径下这条叫 ``test_public_job_snapshots_the_project_authorization_note``：
    创建任务时把项目上的 ``authorization_note`` 抄一份存进任务，作为「依据哪份
    授权」的审计证据。测试阶段项目业务整体取消 —— 请求里即使带着 ``project_id``，
    它也不参与任何判定，因此任务上**不该**再出现项目那侧的备注。

    这是反向守卫：谁把「任务必须挂一份项目授权备注」加回来，它就会红。
    （``authorization_confirmed`` 仍然会被如实记录，那是另一条用例守的。）
    """
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])

    job_id = _public_job(admin_client, project["id"], scope_id).get_json()["job_id"]

    created = jobs_store.created_detail_of_job(job_id)
    assert created["authorization_note"] is None, (
        "任务上又出现了项目授权备注快照：项目业务正在复活"
    )
    detail = admin_client.get(f"/api/jobs/{job_id}").get_json()["job"]
    assert detail["authorization"]["note"] is None
    assert detail["project_id"] is None, "project_id 只能作为 deprecated 兼容字段恒为 null"


def test_public_job_records_the_strategy(admin_client, fake_real_runner):
    """扫描策略要留在任务上：不能只知道跑了哪些工具，还得知道用哪个模板跑的。

    ``project_id`` 只做兼容回显且**恒为 null** —— 它是 deprecated 字段，
    不再表示「这条任务挂在哪个项目下」。
    """
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])

    resp = _public_job(admin_client, project["id"], scope_id, strategy="web_fingerprint")
    assert resp.status_code == 202, resp.get_json()
    job_id = resp.get_json()["job_id"]
    assert resp.get_json()["strategy"] == "web_fingerprint"

    assert jobs_store.strategy_of_job(job_id) == "web_fingerprint"
    detail = admin_client.get(f"/api/jobs/{job_id}").get_json()["job"]
    assert detail["strategy"] == "web_fingerprint"
    # Deprecated 兼容字段：请求里给过 project_id，回显仍然是 null（不参与业务）。
    assert detail["project_id"] is None


def test_authorization_confirmation_is_recorded_but_is_not_a_gate(admin_client, fake_real_runner):
    """授权确认复选框**只被记录**，不是闸门 —— 两种取值都必须建得出任务。

    这条守的是一个刻意的设计决定：本仓库的授权由 Scope / Policy / 环境开关判定，
    一个可被脚本置真的 JSON 布尔值不构成安全边界。把它当闸门只会制造
    「勾了就等于放行」的错觉（页面上也明写了「不是安全边界」）。
    """
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])

    unchecked = _public_job(admin_client, project["id"], scope_id, authorization_confirmed=False)
    assert unchecked.status_code == 202, unchecked.get_json()
    assert unchecked.get_json()["authorization_confirmed"] is False
    assert jobs_store.authorization_of_job(unchecked.get_json()["job_id"])["confirmed"] is False

    checked = _public_job(admin_client, project["id"], scope_id, authorization_confirmed=True)
    assert checked.status_code == 202, checked.get_json()
    assert checked.get_json()["authorization_confirmed"] is True
    assert jobs_store.authorization_of_job(checked.get_json()["job_id"])["confirmed"] is True

    # 完全不给这个字段时如实记 ``None``，而不是替用户假定「已确认」。
    omitted = _public_job(admin_client, project["id"], scope_id)
    assert omitted.status_code == 202
    assert omitted.get_json()["authorization_confirmed"] is None
    assert jobs_store.authorization_of_job(omitted.get_json()["job_id"])["confirmed"] is None


def test_public_job_records_the_limits(admin_client, fake_real_runner):
    """限速 / 超时写进创建事件，并原样回给调用方与详情页。"""
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])

    resp = _public_job(admin_client, project["id"], scope_id, rate_limit=2, timeout_seconds=30)
    assert resp.status_code == 202, resp.get_json()
    body = resp.get_json()
    assert body["limits"] == {"rate_limit": 2, "timeout_seconds": 30}

    job_id = body["job_id"]
    limits = jobs_store.limits_of_job(job_id)
    assert (limits.rate_limit, limits.timeout_seconds) == (2, 30)
    detail = admin_client.get(f"/api/jobs/{job_id}").get_json()["job"]
    assert detail["limits"] == {"rate_limit": 2, "timeout_seconds": 30}

    created = next(
        event for event in jobs_store.list_events(job_id) if event["event_type"] == jobs_store.EVENT_JOB_CREATED
    )
    assert created["detail"]["rate_limit"] == 2
    assert created["detail"]["timeout_seconds"] == 30


def test_public_job_without_limits_leaves_them_unset(admin_client, fake_real_runner):
    """不填限速/超时时，事件 detail 里**不出现**这两个键（没指定就是没指定）。"""
    from core import job_limits

    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])

    job_id = _public_job(admin_client, project["id"], scope_id).get_json()["job_id"]

    assert jobs_store.limits_of_job(job_id).is_empty is True
    created = next(
        event for event in jobs_store.list_events(job_id) if event["event_type"] == jobs_store.EVENT_JOB_CREATED
    )
    assert job_limits.FIELD_RATE_LIMIT not in created["detail"]
    assert job_limits.FIELD_TIMEOUT_SECONDS not in created["detail"]


@pytest.mark.parametrize(
    "overrides,field",
    [
        ({"rate_limit": "abc"}, "rate_limit"),
        ({"rate_limit": 0}, "rate_limit"),
        ({"rate_limit": 100000}, "rate_limit"),
        ({"timeout_seconds": "abc"}, "timeout_seconds"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
        ({"timeout_seconds": 100000}, "timeout_seconds"),
    ],
)
def test_public_job_rejects_out_of_range_limits(admin_client, fake_real_runner, overrides, field):
    """越界一律 400，**不静默夹到边界**：写了 100000 却拿到 100 是最危险的错法。"""
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])

    resp = _public_job(admin_client, project["id"], scope_id, **overrides)
    assert resp.status_code == 400, resp.get_json()
    assert resp.get_json()["details"]["field"] == field
    assert jobs_store.list_jobs() == []


def test_limits_reach_the_runner_config_and_can_only_tighten(admin_client, monkeypatch):
    """限速 / 超时必须在**真正发请求的那一层**生效，且不能放松低频档的预算。

    只断言「接口返回了 rate_limit=2」是不够的 —— 那只能证明它被记录了。
    这里检查 Runner 实际拿到的 ``config``，也就是 ``build_command`` 读的对象：

    * 低频档（httpx 预算 ``rate_limit=10``）遇上请求里的 ``rate_limit=50``
      必须仍是 ``10``：请求放松不了档位已经压下来的速率；
    * 请求里的 ``rate_limit=2`` 才会真的把它收紧到 2；
    * ``timeout_seconds`` 映射成 ``process_timeout``（``modules/base.py`` 读的键）。
    """
    from core import pace as pace_module
    from jobs.executor import execute_job

    seen = []
    original_config = {"threads": 50, "timeout": 10}

    class _RecordingRunner:
        category = "web"
        config = dict(original_config)
        last_execution = {}

        def __init__(self, tool_name):
            self.tool_name = tool_name

        def run(self, target):
            seen.append(dict(self.config))
            from core.runner_result import RunnerResult

            return RunnerResult.ok([], exit_code=0)

    monkeypatch.setattr("modules.registry.build_runner", lambda name: _RecordingRunner(name))
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")

    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])
    budget = pace_module.LIGHT_TOOL_BUDGET["httpx"]

    # 1) 请求想放松（50 > 低频档的 10）：只能收紧，因此仍是 10。
    loose = _public_job(
        admin_client, project["id"], scope_id, strategy="web_fingerprint", rate_limit=50
    ).get_json()["job_id"]
    jobs_store.claim_next_job("w-phase3", lease_seconds=300)
    execute_job(loose)
    assert seen[0]["rate_limit"] == budget["rate_limit"] == 10
    assert seen[0]["threads"] == budget["threads"]

    # 2) 请求真的收紧（2 < 10）：落到 runner.config 上。
    # 3) 超时映射成 process_timeout（工具配置里原本没有这个键）。
    seen.clear()
    tight = _public_job(
        admin_client,
        project["id"],
        scope_id,
        strategy="web_fingerprint",
        rate_limit=2,
        timeout_seconds=1,
    ).get_json()["job_id"]
    jobs_store.claim_next_job("w-phase3", lease_seconds=300)
    execute_job(tight)
    assert seen[0]["rate_limit"] == 2
    assert seen[0]["process_timeout"] == 1

    # 覆盖写的是 Runner 实例上的副本，不是模块级配置对象本身。
    assert _RecordingRunner.config == original_config


def test_legacy_job_entry_accepts_operator_and_limits(admin_client):
    """老入口同样接受这三个字段：同一份规则就该在同一层被接受。

    否则「写了 operator 但那条入口没转发」会变成一个静默不生效的字段 ——
    比报错更难排查。
    """
    scope_id = _make_scope(admin_client, active_scan=False)
    resp = admin_client.post(
        "/api/jobs",
        json={
            "scope_id": scope_id,
            "targets": ["www.example.test"],
            "tools": ["subfinder"],
            "operator": "李四",
            "rate_limit": 3,
            "timeout_seconds": 20,
        },
    )
    assert resp.status_code == 202, resp.get_json()
    job_id = resp.get_json()["job_id"]

    assert jobs_store.operator_of_job(job_id) == "李四"
    limits = jobs_store.limits_of_job(job_id)
    assert (limits.rate_limit, limits.timeout_seconds) == (3, 20)
    # 老入口没有项目，因此不该凭空多出一个 project_id（历史响应形状不变）。
    assert jobs_store.project_id_of_job(job_id) is None
    assert "project_id" not in resp.get_json()


def test_legacy_job_entry_defaults_are_unchanged(admin_client):
    """不带这三个字段时，老入口的行为与引入 Phase 3 之前逐字节一致。"""
    scope_id = _make_scope(admin_client, active_scan=False)
    resp = admin_client.post(
        "/api/jobs",
        json={"scope_id": scope_id, "targets": ["www.example.test"], "tools": ["subfinder"]},
    )
    assert resp.status_code == 202
    job_id = resp.get_json()["job_id"]

    assert jobs_store.operator_of_job(job_id) == jobs_store.DEFAULT_OPERATOR
    assert jobs_store.strategy_of_job(job_id) is None
    assert jobs_store.authorization_of_job(job_id) == {"note": None, "confirmed": None}
    assert jobs_store.limits_of_job(job_id).is_empty is True


def test_scan_center_metadata_exposes_the_limit_ranges(admin_client):
    """上下界与中文说明由服务端下发 —— 否则前端只能抄一份会漂移的副本。"""
    from core import job_limits

    body = admin_client.get("/api/scan-center").get_json()
    limits = body["limits"]
    assert set(limits) == {"rate_limit", "timeout_seconds"}

    assert limits["rate_limit"]["field"] == job_limits.FIELD_RATE_LIMIT
    assert limits["rate_limit"]["min"] == job_limits.RATE_LIMIT_MIN
    assert limits["rate_limit"]["max"] == job_limits.RATE_LIMIT_MAX
    assert limits["rate_limit"]["label"] and limits["rate_limit"]["hint"]
    assert limits["timeout_seconds"]["field"] == job_limits.FIELD_TIMEOUT_SECONDS
    assert limits["timeout_seconds"]["max"] == job_limits.timeout_seconds_max()


def test_scan_center_page_exposes_the_operator_field(admin_client):
    """操作者输入框与「它是记录、不是权限」的说明必须在页面上（方案第 14 节）。"""
    body = admin_client.get("/scan-center").get_data(as_text=True)
    assert 'id="job-operator"' in body
    assert 'name="operator"' in body
    # 限速/超时的输入框由服务端元数据生成，页面上只留一个容器。
    assert 'id="limits-fields"' in body
    assert 'id="limits-config"' in body


def test_scan_center_js_builds_limit_inputs_from_server_metadata():
    """前端不得写死限速/超时的字段名与上下界，一律读服务端下发的 ``limits``。

    与工具清单、分组、节奏同一条理由：写死就会漂移 —— 后端改了上下界，
    页面还停在上一个版本，而且**不会报错**。
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "web" / "static" / "scan_center.js").read_text(
        encoding="utf-8"
    )
    code_only = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith(("*", "//", "/*"))
    )

    assert "renderLimits(center.limits)" in source
    assert "spec.field" in source, "输入框的字段名必须来自服务端元数据"
    assert "spec.label" in source and "spec.min" in source and "spec.max" in source
    # 字段名不得以字符串字面量出现在代码里（注释里出现是允许的 —— 那是设计说明）。
    for hardcoded in ('"rate_limit"', '"timeout_seconds"', "'rate_limit'", "'timeout_seconds'"):
        assert hardcoded not in code_only, f"scan_center.js 写死了限速字段名: {hardcoded}"
    # 空值不拼进请求体：留空 = 不指定，而不是传 0 或 null 绕一圈。
    assert "data-limit-field" in source
    assert "collectLimits()" in source


def test_scan_center_js_forwards_the_operator_and_limits():
    """前端必须真的把操作者与限速转发出去（只显示不转发等于暗箱）。"""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "web" / "static" / "scan_center.js").read_text(
        encoding="utf-8"
    )
    assert 'payload.operator = operatorName' in source
    assert "Object.keys(limits).forEach" in source


def test_job_detail_frontend_shows_the_phase3_context(admin_client):
    """任务详情页要显示操作者 / 策略 / 授权依据 / 本次收紧（源码级守卫）。

    读文件而不是 ``admin_client.get("/static/app.js")``：后者返回的是未关闭的
    文件流，会给整个套件多挂一条 ``ResourceWarning``（实测确认）。
    """
    from pathlib import Path

    script = (Path(__file__).resolve().parents[2] / "web" / "static" / "app.js").read_text(
        encoding="utf-8"
    )
    assert "job.operator" in script
    assert "job.strategy" in script
    assert "job.authorization" in script
    assert "job.limits" in script
    # 授权确认必须写明「使用者确认、不是安全边界」——与页面上的口径一致。
    assert "不是安全边界" in script


# ── 已取消：目标 → 授权资产的自动匹配（`resolve_scope(target)`） ──
#
# 历史背景：`6GetEverything-下一阶段规划方案.md` 第 6 节要求「输入目标后系统后台
# 自动判断该挂哪一份授权资产」，第 16 节① 把链路写成
# 「输入目标 → **自动匹配 scope** → 选择工具 → 创建 job」。
#
# **测试阶段这一整条链路被取消**：不再有授权资产可选，任务默认落在开放测试执行
# 上下文（``scope_id == "open"``）。原先的三条用例（``eligible_scope_ids`` 契约、
# 前端 ``applyMatchedScope``、自动匹配驱动建任务）钉的都是已删除的能力，
# 因此换成三条**反向守卫** —— 谁把那套自动匹配加回来，它们就红。


def test_check_endpoint_has_no_scope_matching_contract(admin_client):
    """预检响应里**不得**再出现「哪份授权资产放行」这类结论。

    历史口径下 ``eligible_scope_ids`` 是前端自动匹配的输入。授权业务取消后它
    没有意义了，留着反而危险：前端一旦读它，就会重新长出一条「先匹配资产再
    提交」的路径，而那条路径的存在本身会让使用者以为「必须先有授权资产」。

    这条逐字点名历史上出现过的字段，**并且**断言 ``checks`` 项的键集合完整相等
    —— 前者防「原样加回来」，后者防「换个名字加回来」。
    """
    allowed = _make_scope(admin_client, domains=["example.test"], active_scan=True)
    _make_project(admin_client, scope_ids=[allowed])

    check = admin_client.post(
        "/api/public-jobs/check", json={"targets": ["www.example.test"]}
    ).get_json()["checks"][0]

    assert set(check) == {"raw", "normalized", "kind", "valid", "verdict", "excluded", "message"}
    for gone in ("eligible_scope_ids", "matches", "ready", "blocker", "candidates", "resolved_check_deferred"):
        assert gone not in check, f"预检又返回了授权匹配结论: {gone}"


def test_scan_center_js_has_no_scope_auto_matching(admin_client):
    """``scan_center.js`` 里不得再有「自动选中授权资产」这条流水线。

    前端源码级守卫（项目没有浏览器测试）。被禁的是那套自动匹配的**载体**：
    读服务端授权结论、改 ``<select>`` 选中项、以及「先匹配后渲染摘要」的固定顺序。
    它们一旦复活，页面就会重新要求使用者先有授权资产 —— 而那正是本轮删掉的东西。

    ★ 断言范围是**代码行**：注释里解释「这套自动匹配已经取消」是允许的，也很必要。
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "web" / "static" / "scan_center.js").read_text(
        encoding="utf-8"
    )
    code_only = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith(("*", "//", "/*"))
    )
    for gone in (
        "applyMatchedScope",
        "eligible_scope_ids",
        "projectSelect",
        "scopeSelect",
        "renderConsentSummary",
        "applyMatchedProject",
    ):
        assert gone not in code_only, f"scan_center.js 又出现了自动匹配授权资产的代码: {gone}"


def test_scan_center_js_never_sends_a_scope_or_project(admin_client):
    """提交请求体里**不得**出现 ``scope_id`` / ``project_id``。

    这是「工具开放、免授权」这条口径在最外层的样子：扫描中心只需要目标 + 策略 +
    工具 + 节奏，服务端按开放模式给出执行上下文。前端一旦重新带上 ``scope_id``，
    就说明有人把「选一份授权资产」这一步又接回去了。

    ★ 顺带钉住 ``/api/public-jobs/check`` 也**只传 targets** —— 预检不再需要
    任何授权上下文，传了就意味着前端还认为有东西要匹配。

    ★ 断言范围是**代码行**：文件头的模块注释里**刻意**写着「删掉了把 scope_id /
    project_id 随请求发出去的那两行」这句口径说明，拿整份源码做字面命中会把
    这句注释算成违规。
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "web" / "static" / "scan_center.js").read_text(
        encoding="utf-8"
    )
    code_only = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith(("*", "//", "/*"))
    )
    for gone in ("scope_id", "project_id"):
        assert gone not in code_only, f"scan_center.js 又在请求里带上了 {gone}"

    # 正向对照：目标和工具确实在传（少了这条，「什么都不传」也会让上面两条变绿）。
    assert "targets: targets" in code_only
    assert "var chosen = selectedCustomTools();" in code_only
    assert "payload.tools = chosen;" in code_only, "自定义策略没有把勾选的工具带上"
    assert 'postJSON("/api/public-jobs/check", { targets: targets })' in code_only


# ── 方案第 13 节：后端安全边界（Job 审计六项 + 拒绝任意字符串工具） ──
#
# 第 13 节把「前端可以开放工具选择」的前提写成了四行必须保留的边界。前三行
# （Scope 校验 / Real Mode 控制 / 工具白名单）在本文件其它小节已有用例，这里
# 补上两处**当时确实没有测试**的缺口：
#
#   ① 「Job 审计记录 job_id、operator、target、tools、time、mode」六项 —— 逐项
#      钉住落库的键。原有用例只覆盖了 operator / targets / scope_id / created_at，
#      一旦有人把 ``tools`` 或 ``mode`` 从 detail 里删掉（它们看着「任务快照里也有」），
#      审计表就再也回答不了「这次到底开了哪些工具、是真扫还是演练」。
#   ② 「禁止任意字符串调用工具」—— 原有用例只覆盖**已登记但被禁**的工具
#      （nmap / dirsearch / …），未登记的工具名当时没有从公网入口验过。
#      ``assert_tools_internet_allowed`` 的单元测试存在（test_tool_registry.py），
#      但公网入口是否真的走到了那个闸门，只有入口级用例才能证明。


def test_job_audit_records_the_six_required_fields(admin_client, fake_real_runner):
    """方案第 13 节「Job 审计」六项必须**逐项**在审计记录里能查到。

    ``job_id`` 是 ``target_id``，``time`` 是 ``created_at``，其余四项在 ``detail``；
    这里刻意不写成「detail 里有哪些键」的白名单断言 —— 那样每加一个 Phase 3 字段
    都要改测试，反而会让人把这条边界删掉。只查第 13 节点名的那六项。
    """
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])

    resp = _public_job(admin_client, project["id"], scope_id, operator="李四")
    assert resp.status_code == 202, resp.get_json()
    job_id = resp.get_json()["job_id"]

    entry = next(
        event
        for event in audit.list_events(limit=50)
        if event["event_type"] == audit.EVENT_JOB_CREATED and event["target_id"] == job_id
    )
    detail = entry["detail"]

    # job_id / time
    assert entry["target_id"] == job_id
    assert entry["created_at"], "审计记录必须带时间戳（方案第 13 节的 time）"
    # operator / target / tools / mode
    assert detail["operator"] == "李四"
    assert detail["targets"] == ["www.example.test"]
    assert sorted(detail["tools"]) == ["httpx", "subfinder"]
    assert detail["mode"] == "real"

    # job.created 事件与审计记录同源：两处对 tools / mode 的说法不能不一致。
    created = next(
        event for event in jobs_store.list_events(job_id) if event["event_type"] == jobs_store.EVENT_JOB_CREATED
    )
    assert sorted(created["detail"]["tools"]) == sorted(detail["tools"])
    assert created["detail"]["mode"] == detail["mode"]


def test_unregistered_tool_name_is_rejected_by_the_registry(admin_client, fake_real_runner):
    """方案第 13 节「禁止任意字符串调用工具」：未登记的名字不能变成一次真实执行。

    与 ``test_blocked_tool_cannot_be_submitted`` 的区别：那条验的是**已登记但被禁**
    的工具（``nmap`` 等），这条验的是**从未登记**的字符串 —— 它必须落在同一个闸门上，
    而不是「没元数据所以没人管」。同时锁定拒绝理由里带出 ``unknown_tools``，
    让调用方能看出「名字打错了」与「这个工具被禁」是两回事。
    """
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])

    resp = _public_job(
        admin_client,
        project["id"],
        scope_id,
        strategy="custom",
        tools=["definitely-not-a-tool"],
    )

    assert resp.status_code == 400, resp.get_json()
    body = resp.get_json()
    assert body["error_code"] == "bad_request"
    assert body["details"]["field"] == "tools"
    assert body["details"]["unknown_tools"] == ["definitely-not-a-tool"]
    # 闸门必须在创建任务**之前**：库里一条都不能多。
    assert jobs_store.list_jobs() == []