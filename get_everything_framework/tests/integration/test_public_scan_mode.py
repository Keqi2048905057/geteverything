"""公网授权测试模式：端到端集成测试（方案第 9、11 节）。

对应方案第 11 节的验收链路，以及第 9 节要求的五类测试：

```text
Scope    —— 公网域名创建成功
Policy   —— 未授权域名拒绝（403）
Job      —— 公网任务进入 Job 队列
Tool     —— 禁止工具无法提交
Worker   —— 任务正常执行
```

**本文件不发起任何真实外部扫描。** 所有 real 模式用例都把
``modules.registry.build_runner`` 换成假 runner，验证的是「闸门 + 执行链 + 落库」，
而不是「工具真的打了公网」。这与 ``AGENTS.md`` 的硬约束一致：
不扫描任何未授权的外部目标，默认只用 mock runner 与 ``127.0.0.1``。

目标域名统一用 RFC 6761 保留域 ``example.test`` 及其子域 —— 即使某个用例
意外走通了真实执行路径，打出去的也不是任何人的资产。
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


def _public_job(admin_client, project_id, scope_id, **overrides):
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


def test_public_url_target_is_normalized_to_its_host(admin_client, monkeypatch):
    """方案第 6 节：用户会直接粘 URL（带协议或不带协议）—— 都要能走通。

    与 `test_public_ip_target_is_checked_against_allowed_cidrs` 同一条思路，
    补的是**第四种输入**：URL。带协议的 URL 一直支持；**没写协议**的
    （浏览器地址栏里复制出来的那种）此前会掉进 CIDR 分支，报
    「非法的 CIDR: www.example.test/a/b」—— 把一条完全正常的输入说成网段写错。

    这里同时守住「规范化之后落到同一个目标」：带协议、不带协议、带路径三种写法
    必须指向同一个主机，否则授权判定会随写法而变。
    """
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")
    scope_id = _make_scope(admin_client, domains=["example.test"], active_scan=True)
    project = _make_project(admin_client, scope_ids=[scope_id])

    for raw in ("www.example.test", "www.example.test/a/b", "https://www.example.test/a/b?q=1"):
        check = admin_client.post(
            "/api/public-jobs/check",
            json={"targets": [raw], "project_id": project["id"]},
        ).get_json()["checks"][0]
        assert check["valid"] is True, (raw, check)
        assert check["normalized"] == "www.example.test", (raw, check)
        assert check["ready"] is True, (raw, check)

        resp = admin_client.post(
            "/api/public-jobs",
            json={
                "project_id": project["id"],
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


def test_scan_center_metadata_exposes_strategies_and_whitelist(admin_client):
    body = admin_client.get("/api/scan-center").get_json()

    assert [item["key"] for item in body["strategies"]] == [
        "asset_discovery",
        "web_fingerprint",
        "custom",
    ]
    discovery = next(item for item in body["strategies"] if item["key"] == "asset_discovery")
    assert sorted(discovery["tools"]) == ["httpx", "subfinder"]

    assert body["internet_allowed_tools"] == ["httpx", "subfinder"]
    by_name = {item["tool_name"]: item for item in body["tools"]}
    assert by_name["nmap"]["internet_allowed"] is False
    assert by_name["nmap"]["risk_level"] == "high"
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


def test_check_endpoint_reports_ready_for_active_scope(admin_client, monkeypatch):
    """范围已授权 + 环境开关开 → ready，并给出建议模式 real。

    ``conftest.py`` 把 ``GEF_ALLOW_REAL_SCAN`` 钉成 false（测试不能跟随开发机
    ``.env``），所以这里要显式打开才可能 ready —— 这本身就是第 3 道闸门的实证。
    """
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")
    scope_id = _make_scope(admin_client, active_scan=True)
    project = _make_project(admin_client, scope_ids=[scope_id])

    resp = admin_client.post(
        "/api/public-jobs/check",
        json={"targets": ["www.example.test"], "project_id": project["id"]},
    )
    body = resp.get_json()
    check = body["checks"][0]

    assert check["ready"] is True
    assert check["blocker"] == ""
    assert check["normalized"] == "www.example.test"
    assert check["matches"][0]["scope_id"] == scope_id
    assert check["matches"][0]["project_id"] == project["id"]
    assert body["summary"] == {"total": 1, "ready": 1, "blocked": 0}
    assert body["suggested_mode"] == "real"
    assert body["project_id"] == project["id"]


def test_check_endpoint_separates_the_three_classic_blockers(admin_client, monkeypatch):
    """三种「以前都叫 403」的情况，现在必须能分辨出来。

    这是 Phase 2 的核心价值：用户不再需要读错误消息反推缺了哪一道闸门。
    """
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")

    # ① 有范围，但不覆盖目标 → not_authorized
    other = _make_scope(admin_client, domains=["other.example.test"], active_scan=True)
    _make_project(admin_client, scope_ids=[other])
    body = admin_client.post(
        "/api/public-jobs/check", json={"targets": ["www.example.test"]}
    ).get_json()
    assert body["checks"][0]["blocker"] == "not_authorized"
    assert len(body["checks"][0]["matches"]) == 0  # 无关范围不下发

    # ② 覆盖但没开 active_scan → scope_inactive
    inactive = _make_scope(admin_client, domains=["www.example.test"], active_scan=False)
    _make_project(admin_client, scope_ids=[inactive])
    body = admin_client.post(
        "/api/public-jobs/check", json={"targets": ["www.example.test"]}
    ).get_json()
    assert body["checks"][0]["blocker"] == "scope_inactive"
    assert body["checks"][0]["real_scan_enabled"] is True

    # ③ 覆盖且已授权，但环境开关没开 → env_disabled
    #    刻意换一个域名：前两步留下的范围仍然覆盖 www.example.test，
    #    而其中一个没开 active_scan，会先命中 scope_inactive（那是正确结论）。
    #    要单独验证 env_disabled，就必须让目标只被「已开 active_scan」的范围覆盖。
    active = _make_scope(admin_client, domains=["env.example.test"], active_scan=True)
    _make_project(admin_client, scope_ids=[active])
    monkeypatch.delenv("GEF_ALLOW_REAL_SCAN", raising=False)
    body = admin_client.post(
        "/api/public-jobs/check", json={"targets": ["env.example.test"]}
    ).get_json()
    assert body["checks"][0]["blocker"] == "env_disabled"
    assert body["checks"][0]["real_scan_enabled"] is False
    # 范围本身是「已授权」的 —— 缺的只是环境开关，这一点必须能区分出来。
    assert body["checks"][0]["matches"][0]["status"] == "ready"


def test_check_endpoint_reports_no_scope_when_nothing_built(admin_client):
    body = admin_client.post(
        "/api/public-jobs/check", json={"targets": ["www.example.test"]}
    ).get_json()
    assert body["checks"][0]["blocker"] == "no_scope"
    assert body["checks"][0]["candidates"] == 0
    assert body["suggested_mode"] == "mock"


def test_check_endpoint_tolerates_bad_target_among_good_ones(admin_client, monkeypatch):
    """坏目标只影响它自己 —— 批量试算不该被第一个格式错误中断。"""
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")
    scope_id = _make_scope(admin_client, active_scan=True)
    _make_project(admin_client, scope_ids=[scope_id])

    body = admin_client.post(
        "/api/public-jobs/check",
        json={"targets": ["www.example.test", "not a host"]},
    ).get_json()

    assert [item["valid"] for item in body["checks"]] == [True, False]
    assert body["checks"][1]["blocker"] == "invalid_target"
    assert body["summary"] == {"total": 2, "ready": 1, "blocked": 1}


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
    """试算必须**如实声明**它没做 DNS 解析，不能假装已经全查过。"""
    scope_id = _make_scope(admin_client)
    _make_project(admin_client, scope_ids=[scope_id])

    check = admin_client.post(
        "/api/public-jobs/check", json={"targets": ["www.example.test"]}
    ).get_json()["checks"][0]
    assert check["resolved_check_deferred"] is True


# ── Policy / Tool：闸门（方案第 9 节「未授权域名拒绝」「禁止工具无法提交」） ──


def test_unattached_scope_is_rejected_before_job_creation(admin_client):
    """Scope 存在但没挂到这个项目下 → 400，且任务一条都不能落库。"""
    project = _make_project(admin_client)
    scope_id = _make_scope(admin_client)  # 刻意不关联

    resp = _public_job(admin_client, project["id"], scope_id)
    assert resp.status_code == 400
    assert resp.get_json()["details"]["field"] == "scope_id"
    assert jobs_store.list_jobs() == []


def test_missing_project_is_404_and_creates_no_job(admin_client):
    scope_id = _make_scope(admin_client)
    resp = _public_job(admin_client, "proj_missing", scope_id)
    assert resp.status_code == 404
    assert jobs_store.list_jobs() == []


def test_missing_scope_id_is_400(admin_client):
    project = _make_project(admin_client)
    resp = admin_client.post("/api/public-jobs", json={"project_id": project["id"], "targets": ["www.example.test"]})
    assert resp.status_code == 400
    assert resp.get_json()["details"]["field"] == "scope_id"


def test_out_of_scope_target_is_403(admin_client):
    """方案第 11 节：未授权目标返回 403。"""
    scope_id = _make_scope(admin_client, domains=["example.test"])
    project = _make_project(admin_client, scope_ids=[scope_id])

    resp = _public_job(admin_client, project["id"], scope_id, targets=["evil.test"])
    assert resp.status_code == 403
    assert resp.get_json()["error_code"] == "scope_violation"
    assert jobs_store.list_jobs() == []


@pytest.mark.parametrize("blocked", ["nmap", "dirsearch", "naabu", "feroxbuster", "katana"])
def test_blocked_tool_cannot_be_submitted(admin_client, blocked):
    """方案第 9 节：禁止工具无法提交 —— 且必须在**创建任务之前**被拒。"""
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])

    resp = _public_job(admin_client, project["id"], scope_id, strategy="custom", tools=[blocked])
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error_code"] == "bad_request"
    names = [item["tool_name"] for item in body["details"]["blocked_tools"]]
    assert blocked in names
    assert jobs_store.list_jobs() == []


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
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])

    resp = _public_job(admin_client, project["id"], scope_id, targets=["www.example.test"])
    assert resp.status_code == 202, resp.get_json()
    body = resp.get_json()

    assert body["status"] == "queued"
    assert body["mode"] == "real"
    assert body["strategy"] == "asset_discovery"
    assert body["project_id"] == project["id"]
    assert body["authorized_public"] is True
    # 资产发现模板 = subfinder + httpx，一个目标 → 2 步
    assert body["total_steps"] == 2

    job = jobs_store.get_job(body["job_id"])
    assert job is not None
    assert job["status"] == "queued"
    assert job["mode"] == "real"
    assert sorted(job["tools"]) == ["httpx", "subfinder"]

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


def test_public_job_orchestration_is_in_application_service():
    """公网编排必须落在 ``core/application``，视图函数不得内联闸门。"""
    from api import public_scan

    source = inspect.getsource(public_scan)
    assert "create_authorized_public_job" in source
    # 视图层不得自己比较白名单或直接落库。
    for forbidden in (
        "assert_tools_internet_allowed",
        "validate_job_targets",
        "create_job_with_status",
        "resolve_mode",
    ):
        assert forbidden not in source, f"api/public_scan.py 又出现了内联编排: {forbidden}"


def test_service_delegates_to_single_job_entry():
    """公网入口必须**复用** ``create_scan_job``，而不是另写一条 Policy 判定。"""
    from core import application

    source = inspect.getsource(application.create_authorized_public_job)
    assert "create_scan_job(" in source
    assert "validate_job_targets" not in source
    assert "create_job_with_status" not in source


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
    assert "授权公网测试模式" in body
    # 未登录时给出明确提示，而不是渲染一个看起来能用的表单。
    assert "401 unauthenticated" in body


def test_scan_center_page_renders_four_steps_for_admin(admin_client):
    """Phase 1 的四步流程必须字面落在页面上（下一阶段规划方案第 5.2 节）。

    四步的措辞是**用户视角**的（输入目标 → 确认授权状态 → 选择工具 → 创建任务），
    不再出现「选择授权项目 / 选择扫描范围」这类要求用户先理解内部模型的步骤。
    """
    resp = admin_client.get("/scan-center")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    for heading in (
        "步骤 1 · 输入目标",
        "步骤 2 · 确认授权状态",
        "步骤 3 · 选择工具",
        "步骤 4 · 创建任务",
    ):
        assert heading in body, heading
    assert "创建任务" in body
    assert "任务" in body
    # 白名单要如实写进页面，用户才知道能选什么。
    assert "httpx" in body and "subfinder" in body
    # 旧流程的两个步骤名不得复活 —— 它们正是「重复步骤」本身。
    for removed in ("确认授权范围", "执行模式与提交"):
        assert removed not in body, f"旧的步骤名又回来了: {removed}"


def test_scan_center_page_exposes_authorization_consent(admin_client):
    """步骤 2 必须有「我确认该目标属于授权范围」这一句显式确认（方案第 7 节）。

    这条守的是两件事：
    * 用户**看得见**自己确认了什么（而不是点一下按钮就默认被当成已确认）；
    * 它必须写明自己是**使用者确认、不是安全边界** —— 否则下一个人很容易
      以为「勾了就等于放行」，从而把它当成权限开关去改。
    """
    body = admin_client.get("/scan-center").get_data(as_text=True)
    assert 'id="job-consent"' in body
    assert 'name="authorization_confirmed"' in body
    assert "我确认该目标属于授权范围" in body
    assert "不是安全边界" in body


def test_scan_center_page_offers_authorization_summary(admin_client):
    """步骤 2 的摘要三行（目标 / 授权状态 / 授权资产）必须在页面上有落点。"""
    body = admin_client.get("/scan-center").get_data(as_text=True)
    for element_id in ("consent-target", "consent-status", "consent-scope"):
        assert f'id="{element_id}"' in body, element_id
    assert "授权资产" in body


def test_scan_center_js_renders_the_consent_summary_from_server_data():
    """摘要必须**回显服务端结论**，前端不得自己重写一份授权判定。

    源码级守卫：``renderConsentSummary`` 必须读 ``BLOCKER_LABELS``（服务端下发的
    ``blocker`` 的翻译表）与 ``matches[].status``，而不是自己比较
    ``active_scan`` / 环境变量这类东西。
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "web" / "static" / "scan_center.js").read_text(
        encoding="utf-8"
    )
    assert "function renderConsentSummary(" in source
    assert "BLOCKER_LABELS[check.blocker]" in source
    # 前端不得自己拼「已授权」的判定条件（那是服务端的结论）。
    for forbidden in (
        "check.real_scan_enabled =",
        "item.active_scan &&",
        "scope.active_scan && scope.allowed_domains",
    ):
        assert forbidden not in source, f"前端开始自己判定授权了: {forbidden}"


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
    """
    import re

    body = admin_client.get("/scan-center").get_data(as_text=True)
    for field in ("job-target", "scope-domains", "scope-cidrs"):
        tag = re.search(r'<input[^>]*id="' + field + r'"[^>]*>', body)
        assert tag is not None, f"页面缺少输入框 {field}"
        assert "value=" not in tag.group(0), f"{field} 带上了默认值: {tag.group(0)}"


def test_scan_center_page_hides_internal_ids_in_labels(admin_client):
    """Phase 1「隐藏 Scope ID」的可执行口径：页面上不出现实体 ID 文案。

    实体 ID 仍然存在（它要作为表单 value 提交），但**不能**出现在任何人类可读的
    文案里。这里检查三种最容易漏进文案的写法，任何一条复活都会红。
    """
    from pathlib import Path

    js_path = Path(__file__).resolve().parents[2] / "web" / "static" / "scan_center.js"
    source = js_path.read_text(encoding="utf-8")

    # 1) 下拉框文案必须走翻译函数，不允许直接把 ID 当 label。
    assert "scopeLabel(" in source and "projectLabel(" in source
    for forbidden in (
        "option.textContent = scopeId",
        "option.textContent = project.id",
        "scopes.textContent = \"Scope \"",
    ):
        assert forbidden not in source, f"scan_center.js 又把内部 ID 当文案了: {forbidden}"

    # 2) 列表里不再输出裸 ID 列。
    assert 'id.textContent = project.id' not in source


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


def test_scan_center_page_separates_restricted_tools_note(admin_client):
    """受限工具说明写进**独立**元素，不再往策略说明上累加。

    累加会让「切换策略」后说明里混着上一轮的尾巴 —— 这正是 Phase 1 要清掉的
    「异常展示」。这里断言容器存在且初始为空。
    """
    body = admin_client.get("/scan-center").get_data(as_text=True)
    assert 'id="restricted-note"' in body
    assert "本阶段未接入/未开放的工具" not in body


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
    """
    from pathlib import Path

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
    for name in ("subfinder", "httpx", "nmap", "naabu", "nuclei", "katana", "feroxbuster"):
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


def test_public_job_snapshots_the_project_authorization_note(admin_client, fake_real_runner):
    """授权备注取项目上那一份的**当前值**当快照，事后改项目不影响历史任务。"""
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])
    note = project["authorization_note"]

    job_id = _public_job(admin_client, project["id"], scope_id).get_json()["job_id"]

    detail = admin_client.get(f"/api/jobs/{job_id}").get_json()["job"]
    assert detail["authorization"]["note"] == note
    assert jobs_store.created_detail_of_job(job_id)["authorization_note"] == note


def test_public_job_records_the_strategy(admin_client, fake_real_runner):
    """扫描策略要留在任务上：不能只知道跑了哪些工具，还得知道用哪个模板跑的。"""
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])

    resp = _public_job(admin_client, project["id"], scope_id, strategy="web_fingerprint")
    assert resp.status_code == 202, resp.get_json()
    job_id = resp.get_json()["job_id"]
    assert resp.get_json()["strategy"] == "web_fingerprint"

    assert jobs_store.strategy_of_job(job_id) == "web_fingerprint"
    detail = admin_client.get(f"/api/jobs/{job_id}").get_json()["job"]
    assert detail["strategy"] == "web_fingerprint"
    # 项目也要读得回来：审计上下文是「依据哪份授权」，光有 scope 不够。
    assert detail["project_id"] == project["id"]


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


# ── 方案第 6 节：目标 → 授权资产的自动匹配（`resolve_scope(target)`） ──
#
# 注意命名：这里**不是**另一份工作单里的「Phase 4 结果体验」。
# 本项来自 `6GetEverything-下一阶段规划方案.md` 第 6 节（Step 1 目标输入 →
# 「系统后台：调用 ``resolve_scope(target)``，自动判断」）与第 16 节①
# （「输入目标 → **自动匹配 scope** → 选择工具 → 创建 job」）。


def test_check_endpoint_exposes_the_auto_match_contract(admin_client, monkeypatch):
    """试算响应里的 ``eligible_scope_ids`` 必须**只含真正放行**的范围。

    前端 Phase 4 的自动匹配（方案第 6 节 ``resolve_scope(target)``）就吃这个字段。
    因此它必须同时满足两件事，否则「自动选中」会变成一条绕过授权的捷径：

    * 覆盖目标 → 在里面（否则自动匹配永远选不中，功能是死的）；
    * **命中排除列表** → 不在里面（``matches`` 里仍然有它，那是诊断信息，
      但它**不构成授权**）—— 否则自动匹配会把用户送进一个必然 403 的组合。
    """
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")
    allowed = _make_scope(admin_client, domains=["example.test"], active_scan=True)
    # 刻意用**另一个根域**：``example.test`` 会覆盖它的所有子域，
    # 若两个范围同在 ``example.test`` 下，「命中排除」的那一个会同时被
    # 允许范围覆盖，断言就分不清「排除生效」与「别的范围放行」。
    excluded_only = _make_scope(
        admin_client,
        domains=["blocked.test"],
        excluded_domains=["secret.blocked.test"],
    )
    project = _make_project(admin_client, scope_ids=[allowed, excluded_only])

    body = admin_client.post(
        "/api/public-jobs/check",
        json={"targets": ["www.example.test"], "project_id": project["id"]},
    ).get_json()
    assert body["checks"][0]["eligible_scope_ids"] == [allowed]

    denied = admin_client.post(
        "/api/public-jobs/check",
        json={"targets": ["secret.blocked.test"], "project_id": project["id"]},
    ).get_json()["checks"][0]
    # 诊断信息仍在（用户需要知道「是你自己的排除列表挡住了」），但它不构成授权。
    assert [item["scope_id"] for item in denied["matches"]] == [excluded_only]
    assert denied["matches"][0]["verdict"] == "excluded"
    assert denied["eligible_scope_ids"] == []
    assert denied["ready"] is False


def test_scan_center_js_auto_selects_the_scope_from_server_verdict():
    """方案第 6 节：自动匹配只**选中**服务端判定过的资产，不自己做授权判定。

    四条口径一起守（详见 ``scan_center.js:applyMatchedScope``）：
    ① 候选来自服务端结论 ``eligible_scope_ids``，前端不得自己比对
       ``verdict`` / ``allowed_domains`` / ``active_scan`` —— 那会变成第二条授权判定；
    ② 试算完成后才自动选，且**先选后渲染摘要**（否则摘要显示上一个选中项）；
    ③ 有歧义（多目标命中不同资产）时如实说明交给用户，不猜；
    ④ 自动匹配**不扩大任何范围**：它只改 ``<select>`` 的选中项，
       既不新增目标、也不改 Scope —— 「禁止为了体验删除 Scope 校验」一字未动。
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "web" / "static" / "scan_center.js").read_text(
        encoding="utf-8"
    )
    assert "function applyMatchedScope(" in source
    assert "eligible_scope_ids" in source, "自动匹配必须读服务端结论"
    # ① + ② 一次到位：候选读服务端结论，且「先匹配、后渲染摘要」顺序固定。
    assert (
        "var autoOutcome = applyMatchedScope(payload);\n"
        "    renderConsentSummary();" in source
    ), "自动匹配必须发生在渲染摘要之前"
    # ③ 歧义与「已选中」两种如实说明都在。
    assert "多个目标命中的授权资产不一致" in source
    assert "已自动选中覆盖该目标的授权资产" in source

    # ① 前端不得自己重算授权：这些写法只允许出现在服务端的响应里。
    code_only = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith(("*", "//", "/*"))
    )
    for forbidden in (
        "item.verdict ===",
        'item.status === "ready"',
        "scope.allowed_domains.indexOf",
        "scope.active_scan &&",
    ):
        assert forbidden not in code_only, f"前端开始自己判定授权了: {forbidden}"

    # ④ 自动匹配只动下拉框的选中项，不碰目标与 Scope 本身。
    assert "scopeSelect.value = scopeId;" in source
    assert "projectSelect.value = owner.id;" in source


def test_target_to_job_flow_uses_the_auto_matched_scope(admin_client, fake_real_runner):
    """方案第 16 节①：**输入目标 → 自动匹配 scope → 选择工具 → 创建 job** 整条链路。

    这条把 §16 ① 的链路在服务端侧跑完整：前端那条「自动选中」用的是试算响应里的
    ``eligible_scope_ids[0]``，因此这里就用**同一个来源**驱动创建请求 —— 前端
    改了取数口径（例如改成自己比对 ``matches``），这条链路的输入就跟着变，
    而不是靠源码字符串守卫。

    同时覆盖 §16 ② 的「授权目标」一行：任务创建成功、``tools`` 正确保存、
    ``scope`` 正确关联。
    """
    # 两份授权资产，各挂一个项目：自动匹配必须挑中覆盖目标的那一个，而不是「第一个」。
    unrelated = _make_scope(admin_client, domains=["other.example.test"], active_scan=True)
    _make_project(admin_client, name="无关授权项目", scope_ids=[unrelated])
    matched = _make_scope(admin_client, domains=["example.test"], active_scan=True)
    project = _make_project(admin_client, scope_ids=[matched], name="目标所在授权项目")

    check = admin_client.post(
        "/api/public-jobs/check", json={"targets": ["www.example.test"]}
    ).get_json()["checks"][0]
    assert check["ready"] is True
    assert check["eligible_scope_ids"] == [matched]

    # 「选择工具」：资产发现模板（= subfinder + httpx），与页面上选中的模板同义。
    resp = admin_client.post(
        "/api/public-jobs",
        json={
            "project_id": project["id"],
            "scope_id": check["eligible_scope_ids"][0],
            "targets": ["www.example.test"],
            "strategy": "asset_discovery",
        },
    )
    assert resp.status_code == 202, resp.get_json()
    body = resp.get_json()
    assert body["status"] == "queued"
    assert body["mode"] == "real"
    assert body["strategy"] == "asset_discovery"
    assert body["scope_id"] == matched

    job = jobs_store.get_job(body["job_id"])
    assert job["scope_id"] == matched
    assert sorted(job["tools"]) == ["httpx", "subfinder"]
    assert job["targets"] == ["www.example.test"]
    # 自动匹配不改授权范围：任务只落在被选中的那一份资产上。
    assert len(jobs_store.list_jobs()) == 1


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