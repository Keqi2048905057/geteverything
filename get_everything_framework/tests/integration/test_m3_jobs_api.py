"""M3 集成测试：任务 API（``/api/jobs``）。

对应方案第 5.3 节与 M3 验收项：

* ``POST /api/jobs`` **立即**返回 ``job_id`` 与 ``queued``，不同步等待；
* 创建 10 个 job 后接口仍然立即响应（本文件用总耗时兜底断言）；
* 任务必须关联 Scope，目标必须过 Scope 校验；
* 全部接口需要本地管理员认证；
* 刷新（重新 GET）后状态仍在；
* cancel / retry 生效。
"""

import time

import pytest


def _make_scope(admin_client, **overrides):
    payload = {
        "name": "任务 API 测试范围",
        "allowed_domains": ["example.test"],
        "active_scan": False,
    }
    payload.update(overrides)
    resp = admin_client.post("/api/scopes", json=payload)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["scope"]["id"]


def _create_job(admin_client, scope_id, **overrides):
    payload = {
        "scope_id": scope_id,
        "targets": ["a.example.test"],
        "tools": ["subfinder"],
        "mode": "mock",
    }
    payload.update(overrides)
    return admin_client.post("/api/jobs", json=payload)


# ── 认证 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/jobs"),
        ("post", "/api/jobs"),
        ("get", "/api/jobs/job_x"),
        ("get", "/api/jobs/job_x/results"),
        ("post", "/api/jobs/job_x/cancel"),
        ("post", "/api/jobs/job_x/retry"),
        ("get", "/api/jobs/job_x/steps"),
        ("get", "/api/jobs/job_x/events"),
    ],
)
def test_jobs_api_requires_admin(client, method, path):
    resp = getattr(client, method)(path)
    assert resp.status_code == 401
    assert resp.get_json()["error_code"] == "unauthenticated"


# ── 创建：立即返回 ────────────────────────────────────────


def test_create_job_returns_immediately_with_queued(admin_client):
    scope_id = _make_scope(admin_client)
    resp = _create_job(admin_client, scope_id)

    assert resp.status_code == 202
    body = resp.get_json()
    assert body["ok"] is True
    assert body["status"] == "queued"
    assert body["job_id"].startswith("job_")
    assert body["total_steps"] == 1
    assert body["scope_id"] == scope_id


def test_create_job_does_not_wait_for_execution(admin_client):
    """创建接口只落库：没有 worker 时任务必须停在 queued。"""
    scope_id = _make_scope(admin_client)
    job_id = _create_job(admin_client, scope_id).get_json()["job_id"]

    detail = admin_client.get(f"/api/jobs/{job_id}").get_json()["job"]
    assert detail["status"] == "queued"
    assert detail["progress"] == 0
    assert detail["started_at"] is None
    assert detail["finished_at"] is None
    assert all(step["status"] == "pending" for step in detail["steps"])


def test_create_ten_jobs_stays_responsive(admin_client):
    """M3 验收：创建 10 个 mock job 后 Web 仍立即响应。"""
    scope_id = _make_scope(admin_client)

    started = time.perf_counter()
    job_ids = []
    for _ in range(10):
        resp = _create_job(admin_client, scope_id)
        assert resp.status_code == 202
        job_ids.append(resp.get_json()["job_id"])
    elapsed = time.perf_counter() - started

    assert len(set(job_ids)) == 10
    # 同步执行的话每个 job 都要跑一遍扫描，不可能这么快。
    assert elapsed < 5.0, f"创建 10 个任务耗时 {elapsed:.2f}s，接口疑似在同步等待"

    # 与此同时列表接口也要能秒回
    listing = admin_client.get("/api/jobs").get_json()
    assert listing["counts"]["queued"] == 10


def test_create_job_expands_steps_for_tools_and_targets(admin_client):
    scope_id = _make_scope(admin_client)
    scope_id = scope_id
    resp = _create_job(
        admin_client,
        scope_id,
        targets=["a.example.test", "b.example.test"],
        tools=["subfinder", "httpx"],
    )
    job_id = resp.get_json()["job_id"]
    assert resp.get_json()["total_steps"] == 4

    steps = admin_client.get(f"/api/jobs/{job_id}/steps").get_json()["steps"]
    assert len(steps) == 4
    assert {(s["tool_name"], s["target"]) for s in steps} == {
        ("subfinder", "a.example.test"),
        ("subfinder", "b.example.test"),
        ("httpx", "a.example.test"),
        ("httpx", "b.example.test"),
    }


# ── Scope 强制校验 ────────────────────────────────────────


def test_create_job_without_scope_id_is_rejected(admin_client):
    resp = admin_client.post("/api/jobs", json={"targets": ["a.example.test"], "tools": ["subfinder"]})
    assert resp.status_code == 400
    assert resp.get_json()["error_code"] == "bad_request"


def test_create_job_with_unknown_scope_is_rejected(admin_client):
    resp = _create_job(admin_client, "scope_missing")
    assert resp.status_code == 403
    assert resp.get_json()["error_code"] == "scope_violation"


def test_create_job_with_out_of_scope_target_is_rejected(admin_client):
    """M2/M3 共同要求：目标越界直接拒绝，且不留下 job。"""
    scope_id = _make_scope(admin_client)
    resp = _create_job(admin_client, scope_id, targets=["evil.test"])
    assert resp.status_code == 403
    assert "evil.test" in resp.get_json()["error_message"]

    assert admin_client.get("/api/jobs").get_json()["jobs"] == []


def test_create_job_rejects_one_bad_target_in_many(admin_client):
    """多目标里只要有一个越界就整体拒绝（不做部分执行）。"""
    scope_id = _make_scope(admin_client)
    resp = _create_job(admin_client, scope_id, targets=["a.example.test", "evil.test", "b.example.test"])
    assert resp.status_code == 403
    assert admin_client.get("/api/jobs").get_json()["jobs"] == []


def test_create_job_from_upload_is_scoped(admin_client):
    """上传文件里的目标同样要过 Scope。"""
    import io

    scope_id = _make_scope(admin_client)
    upload_id = admin_client.post(
        "/api/upload",
        data={"file": (io.BytesIO(b"a.example.test\nevil.test\n"), "targets.txt")},
        content_type="multipart/form-data",
    ).get_json()["upload_id"]

    resp = _create_job(admin_client, scope_id, targets=[], upload_id=upload_id)
    assert resp.status_code == 403

    good = admin_client.post(
        "/api/upload",
        data={"file": (io.BytesIO(b"a.example.test\nb.example.test\n"), "targets.txt")},
        content_type="multipart/form-data",
    ).get_json()["upload_id"]
    ok = _create_job(admin_client, scope_id, targets=[], upload_id=good)
    assert ok.status_code == 202
    assert ok.get_json()["total_steps"] == 2


# ── 参数校验 ──────────────────────────────────────────────


def test_create_job_requires_targets(admin_client):
    scope_id = _make_scope(admin_client)
    resp = _create_job(admin_client, scope_id, targets=[])
    assert resp.status_code == 400


def test_create_job_requires_tools(admin_client):
    scope_id = _make_scope(admin_client)
    resp = _create_job(admin_client, scope_id, tools=[])
    assert resp.status_code == 400


def test_create_job_rejects_unknown_tool(admin_client):
    scope_id = _make_scope(admin_client)
    resp = _create_job(admin_client, scope_id, tools=["nuclei"])
    assert resp.status_code == 400
    assert resp.get_json()["error_code"] == "bad_request"


def test_create_job_rejects_unknown_scenario(admin_client):
    scope_id = _make_scope(admin_client)
    resp = _create_job(admin_client, scope_id, scenario="turbo")
    assert resp.status_code == 400


def test_create_job_rejects_too_many_targets(admin_client):
    from config import SCAN_LIMITS

    limit = SCAN_LIMITS["max_targets_per_job"]
    scope_id = _make_scope(admin_client)
    targets = [f"h{i}.example.test" for i in range(limit + 1)]
    resp = _create_job(admin_client, scope_id, targets=targets)
    assert resp.status_code == 400
    assert "最多" in resp.get_json()["error_message"]


def test_create_job_real_mode_blocked_without_env(admin_client, monkeypatch):
    from core.safety import REAL_SCAN_ENV

    monkeypatch.delenv(REAL_SCAN_ENV, raising=False)
    scope_id = _make_scope(admin_client, active_scan=True)
    resp = _create_job(admin_client, scope_id, mode="real")
    assert resp.status_code == 403


def test_create_job_real_mode_blocked_when_scope_inactive(admin_client, monkeypatch):
    from core.safety import REAL_SCAN_ENV

    monkeypatch.setenv(REAL_SCAN_ENV, "true")
    scope_id = _make_scope(admin_client, active_scan=False)
    resp = _create_job(admin_client, scope_id, mode="real")
    assert resp.status_code == 403


def test_create_job_rejects_non_json_body(admin_client):
    resp = admin_client.post("/api/jobs", data="not json", content_type="text/plain")
    assert resp.status_code == 400


# ── 查询 ──────────────────────────────────────────────────


def test_get_job_detail_contains_steps_and_events(admin_client):
    scope_id = _make_scope(admin_client)
    job_id = _create_job(admin_client, scope_id).get_json()["job_id"]

    detail = admin_client.get(f"/api/jobs/{job_id}").get_json()["job"]
    assert detail["id"] == job_id
    assert detail["scope_id"] == scope_id
    assert detail["mode"] == "mock"
    assert len(detail["steps"]) == 1
    assert detail["events"]
    # 审计要求的字段一个都不能少
    for field in ("created_at", "started_at", "finished_at", "progress", "error_code"):
        assert field in detail


def test_get_unknown_job_returns_404(admin_client):
    resp = admin_client.get("/api/jobs/job_missing")
    assert resp.status_code == 404
    assert resp.get_json()["error_code"] == "not_found"


def test_list_jobs_filters_by_status(admin_client):
    scope_id = _make_scope(admin_client)
    first = _create_job(admin_client, scope_id).get_json()["job_id"]
    _create_job(admin_client, scope_id)

    admin_client.post(f"/api/jobs/{first}/cancel")

    queued = admin_client.get("/api/jobs?status=queued").get_json()["jobs"]
    cancelled = admin_client.get("/api/jobs?status=cancelled").get_json()["jobs"]
    assert len(queued) == 1
    assert [job["id"] for job in cancelled] == [first]


def test_list_jobs_rejects_unknown_status(admin_client):
    resp = admin_client.get("/api/jobs?status=turbo")
    assert resp.status_code == 400


def test_job_status_persists_across_requests(admin_client):
    """M3 验收：刷新浏览器后状态仍存在（状态在库里，不在内存里）。"""
    scope_id = _make_scope(admin_client)
    job_id = _create_job(admin_client, scope_id, targets=["a.example.test", "b.example.test"]).get_json()["job_id"]

    # 模拟「刷新」：全新的请求，重新读一遍
    for _ in range(3):
        detail = admin_client.get(f"/api/jobs/{job_id}").get_json()["job"]
        assert detail["id"] == job_id
        assert detail["total_steps"] == 2

    # 用另一个客户端（等价于换一个浏览器会话）读到的也必须是同一份状态
    another = admin_client.application.test_client()
    another.environ_base["HTTP_X_LOCAL_TOKEN"] = admin_client.environ_base["HTTP_X_LOCAL_TOKEN"]
    assert another.get(f"/api/jobs/{job_id}").get_json()["job"]["id"] == job_id


def test_job_steps_endpoint(admin_client):
    scope_id = _make_scope(admin_client)
    job_id = _create_job(admin_client, scope_id).get_json()["job_id"]
    resp = admin_client.get(f"/api/jobs/{job_id}/steps")
    assert resp.status_code == 200
    assert len(resp.get_json()["steps"]) == 1


def test_job_events_endpoint(admin_client):
    scope_id = _make_scope(admin_client)
    job_id = _create_job(admin_client, scope_id).get_json()["job_id"]
    events = admin_client.get(f"/api/jobs/{job_id}/events").get_json()["events"]
    assert [event["event_type"] for event in events] == ["job.created"]


# ── 取消 ──────────────────────────────────────────────────


def test_cancel_queued_job(admin_client):
    """M3 验收：取消后状态变为 cancelled。"""
    scope_id = _make_scope(admin_client)
    job_id = _create_job(admin_client, scope_id).get_json()["job_id"]

    resp = admin_client.post(f"/api/jobs/{job_id}/cancel")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "cancelled"

    detail = admin_client.get(f"/api/jobs/{job_id}").get_json()["job"]
    assert detail["status"] == "cancelled"
    assert detail["finished_at"] is not None


def test_cancel_is_idempotent(admin_client):
    scope_id = _make_scope(admin_client)
    job_id = _create_job(admin_client, scope_id).get_json()["job_id"]

    assert admin_client.post(f"/api/jobs/{job_id}/cancel").get_json()["status"] == "cancelled"
    assert admin_client.post(f"/api/jobs/{job_id}/cancel").get_json()["status"] == "cancelled"


def test_cancel_unknown_job_returns_404(admin_client):
    assert admin_client.post("/api/jobs/job_missing/cancel").status_code == 404


# ── 重试 ──────────────────────────────────────────────────


def test_retry_cancelled_job_requeues(admin_client):
    scope_id = _make_scope(admin_client)
    job_id = _create_job(admin_client, scope_id).get_json()["job_id"]
    admin_client.post(f"/api/jobs/{job_id}/cancel")

    resp = admin_client.post(f"/api/jobs/{job_id}/retry")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "queued"
    assert body["attempt"] == 2


def test_retry_queued_job_is_rejected(admin_client):
    scope_id = _make_scope(admin_client)
    job_id = _create_job(admin_client, scope_id).get_json()["job_id"]

    resp = admin_client.post(f"/api/jobs/{job_id}/retry")
    assert resp.status_code == 400
    assert "retry" in resp.get_json()["error_message"]


def test_retry_unknown_job_returns_404(admin_client):
    assert admin_client.post("/api/jobs/job_missing/retry").status_code == 404


# ── P0-7a：创建接口的幂等键 ───────────────────────────────


def test_create_job_is_idempotent_with_same_key(admin_client):
    """同一个 idempotency_key 重复 POST 只产生一个任务，返回同一个 job_id。"""
    scope_id = _make_scope(admin_client)

    first = _create_job(admin_client, scope_id, idempotency_key="api-key-1")
    second = _create_job(admin_client, scope_id, idempotency_key="api-key-1")

    assert first.status_code == 202 and second.status_code == 202
    assert first.get_json()["reused"] is False
    assert second.get_json()["reused"] is True
    assert first.get_json()["job_id"] == second.get_json()["job_id"]

    jobs = admin_client.get("/api/jobs").get_json()["jobs"]
    assert len(jobs) == 1


def test_create_job_without_key_reports_not_reused(admin_client):
    """不传键时响应里也有 ``reused`` 字段（形状稳定），值为 false。"""
    scope_id = _make_scope(admin_client)
    assert _create_job(admin_client, scope_id).get_json()["reused"] is False


def test_create_job_rejects_non_string_idempotency_key(admin_client):
    scope_id = _make_scope(admin_client)
    resp = _create_job(admin_client, scope_id, idempotency_key=123)
    assert resp.status_code == 400
    assert resp.get_json()["error_code"] == "bad_request"


def test_create_job_rejects_overlong_idempotency_key(admin_client):
    scope_id = _make_scope(admin_client)
    resp = _create_job(admin_client, scope_id, idempotency_key="x" * 201)
    assert resp.status_code == 400


def test_reused_creation_is_audited_with_flag(admin_client):
    """幂等命中在审计里可追溯（否则「少了一个任务」在事后无从解释）。"""
    from core import audit

    scope_id = _make_scope(admin_client)
    _create_job(admin_client, scope_id, idempotency_key="api-key-audit")
    _create_job(admin_client, scope_id, idempotency_key="api-key-audit")

    events = [
        event
        for event in audit.list_events(limit=50)
        if event["event_type"] == audit.EVENT_JOB_CREATED
    ]
    assert any(event["detail"].get("reused") is True for event in events)
    assert any("reused" not in event["detail"] for event in events)


# ── 执行与审计 ────────────────────────────────────────────


def test_job_is_executed_by_executor_and_recorded(admin_client):
    """把 worker 的一步单独跑掉，验证 API 与执行器串得起来。"""
    from core import jobs as jobs_store
    from jobs.executor import execute_job

    scope_id = _make_scope(admin_client)
    job_id = _create_job(admin_client, scope_id, targets=["a.example.test"]).get_json()["job_id"]

    jobs_store.claim_next_job("w-test", lease_seconds=300)
    execute_job(job_id)

    detail = admin_client.get(f"/api/jobs/{job_id}").get_json()["job"]
    assert detail["status"] == "succeeded"
    assert detail["progress"] == 100
    assert detail["started_at"] and detail["finished_at"]
    assert detail["error_code"] is None
    assert detail["steps"][0]["found_count"] > 0
    assert detail["steps"][0]["results"]


def test_job_creation_is_audited(admin_client):
    from core import audit

    scope_id = _make_scope(admin_client)
    job_id = _create_job(admin_client, scope_id).get_json()["job_id"]

    events = audit.list_events(limit=20)
    entry = next(event for event in events if event["event_type"] == audit.EVENT_JOB_CREATED)
    assert entry["target_id"] == job_id
    assert entry["detail"]["scope_id"] == scope_id


def test_health_reports_queue(admin_client):
    scope_id = _make_scope(admin_client)
    _create_job(admin_client, scope_id)

    data = admin_client.get("/health").get_json()
    assert data["queue"]["status"] == "ok"
    assert data["queue"]["queued"] == 1
    # /health 不需要登录，绝不能泄露目标域名
    assert "example.test" not in str(data)
