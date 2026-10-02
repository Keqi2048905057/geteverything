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
    """Phase 2 的五步简化流程在页面上落成四段（第 5 步「创建任务」是提交按钮本身）。"""
    resp = admin_client.get("/scan-center")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    for heading in (
        "步骤 1 · 输入目标",
        "步骤 2 · 确认授权范围",
        "步骤 3 · 选择工具",
        "步骤 4 · 执行模式与提交",
    ):
        assert heading in body, heading
    assert "创建任务" in body
    assert "任务" in body
    # 白名单要如实写进页面，用户才知道能选什么。
    assert "httpx" in body and "subfinder" in body


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


def test_scan_center_page_separates_restricted_tools_note(admin_client):
    """受限工具说明写进**独立**元素，不再往策略说明上累加。

    累加会让「切换策略」后说明里混着上一轮的尾巴 —— 这正是 Phase 1 要清掉的
    「异常展示」。这里断言容器存在且初始为空。
    """
    body = admin_client.get("/scan-center").get_data(as_text=True)
    assert 'id="restricted-note"' in body
    assert "本阶段未接入/未开放的工具" not in body


def test_scan_center_page_renders_recent_jobs(admin_client, fake_real_runner):
    """创建过的公网任务要出现在扫描中心的任务列表里。"""
    scope_id = _make_scope(admin_client)
    project = _make_project(admin_client, scope_ids=[scope_id])
    job_id = _public_job(admin_client, project["id"], scope_id).get_json()["job_id"]

    body = admin_client.get("/scan-center").get_data(as_text=True)
    assert job_id in body