"""方案第 19 节 P1：Observability 的集成测试。

覆盖三层接线：

1. Web 层 —— 每个请求绑定 ``request_id``、回写 ``X-Request-Id`` 响应头、
   并在访问事件里只用 ``path``（不把带目标列表的 query 写进日志）；
2. 执行层 —— 每个步骤结束产出 ``job_step_finished`` 事件，带
   ``job_id`` / ``step_id`` / ``tool`` / ``status`` / ``duration_ms``；
3. worker 层 —— 领取任务 / 任务结束的事件带 ``worker_id``。
"""

import json
import logging

import pytest

from core import jobs as jobs_store
from core import observability

pytestmark = pytest.mark.usefixtures("local_db")


def _events(caplog, name=None):
    """把 caplog 里的 gef 记录解析成事件字典列表。"""
    parsed = []
    for record in caplog.records:
        if record.name != observability.LOGGER_NAME:
            continue
        payload = json.loads(record.getMessage())
        if name is None or payload.get("event") == name:
            parsed.append(payload)
    return parsed


# ── Web 层：request_id ───────────────────────────────────


def test_health_response_carries_generated_request_id(client):
    response = client.get("/health")
    assert response.status_code == 200
    request_id = response.headers.get(observability.REQUEST_ID_HEADER)
    assert request_id is not None
    assert observability.REQUEST_ID_PATTERN.match(request_id)

    body = response.get_json()
    assert isinstance(body, dict)


def test_inbound_request_id_is_reused(client):
    response = client.get("/health", headers={observability.REQUEST_ID_HEADER: "caller-supplied-id-0001"})
    assert response.headers[observability.REQUEST_ID_HEADER] == "caller-supplied-id-0001"


def test_inbound_request_id_must_be_safe(client):
    """非法（带空格 / 过短 / 过长）的入站值一律忽略，换成新生成的 ID。"""
    for bogus in ("has space", "x", "y" * 200, "bad!" + "a" * 20):
        response = client.get("/health", headers={observability.REQUEST_ID_HEADER: bogus})
        request_id = response.headers[observability.REQUEST_ID_HEADER]
        assert request_id != bogus
        assert observability.REQUEST_ID_PATTERN.match(request_id)


def test_each_request_gets_its_own_request_id(client):
    first = client.get("/health").headers[observability.REQUEST_ID_HEADER]
    second = client.get("/health").headers[observability.REQUEST_ID_HEADER]
    assert first != second


def test_error_response_still_carries_request_id(admin_client):
    """失败请求也要有 ID —— 排障时正是要靠它把 500/401 与日志对上。"""
    response = admin_client.get("/api/jobs/does-not-exist")
    assert response.status_code == 404
    request_id = response.headers.get(observability.REQUEST_ID_HEADER)
    assert request_id is not None
    assert observability.REQUEST_ID_PATTERN.match(request_id)


def test_error_event_shares_request_id_with_response(admin_client, caplog):
    caplog.set_level(logging.INFO, logger=observability.LOGGER_NAME)
    response = admin_client.get("/api/jobs/does-not-exist")
    request_id = response.headers[observability.REQUEST_ID_HEADER]

    failed = [event for event in _events(caplog, observability.EVENT_REQUEST_FAILED) if event.get("request_id") == request_id]
    assert failed, "失败的请求必须有一条 request_failed 事件"
    event = failed[0]
    assert event["status"] == 404
    assert event["error_code"] == "not_found"
    assert event["level"] == "WARNING"
    assert event["path"] == "/api/jobs/does-not-exist"


def test_unauthenticated_request_is_logged_without_token_value(client, caplog):
    """401 事件必须带错误码，且绝不能把 Token 值写进日志。"""
    caplog.set_level(logging.INFO, logger=observability.LOGGER_NAME)
    response = client.get("/api/jobs", headers={"X-Local-Token": "leaked-token-value-xyz"})

    assert response.status_code == 401
    request_id = response.headers[observability.REQUEST_ID_HEADER]
    failed = [event for event in _events(caplog, observability.EVENT_REQUEST_FAILED) if event.get("request_id") == request_id]
    assert failed and failed[0]["error_code"] == "unauthenticated"
    assert "leaked-token-value-xyz" not in caplog.text


def test_access_event_is_logged_with_path_only(client, caplog):
    caplog.set_level(logging.INFO, logger=observability.LOGGER_NAME)
    response = client.get("/health")
    request_id = response.headers[observability.REQUEST_ID_HEADER]

    events = [event for event in _events(caplog, observability.EVENT_HTTP_REQUEST) if event.get("request_id") == request_id]
    assert len(events) == 1
    event = events[0]
    assert event["method"] == "GET"
    assert event["path"] == "/health"
    assert event["status"] == 200
    assert isinstance(event["duration_ms"], int)
    assert "query" not in event


def test_access_event_never_records_query_string(client, caplog):
    """方案第 19 节：不记录完整目标列表到公共日志。"""
    caplog.set_level(logging.INFO, logger=observability.LOGGER_NAME)
    response = client.get("/health?targets=host1.example.test,host2.example.test")
    request_id = response.headers[observability.REQUEST_ID_HEADER]

    events = [event for event in _events(caplog, observability.EVENT_HTTP_REQUEST) if event.get("request_id") == request_id]
    assert events and events[0]["path"] == "/health"
    assert "host1.example.test" not in caplog.records[-1].getMessage()


def test_request_context_does_not_leak_between_requests(client):
    """请求之间必须干净：上一个请求的 request_id 不能串到下一个。"""
    client.get("/health")
    assert observability.current_context() == {}


def test_job_created_event_carries_request_id_without_target_list(admin_client, caplog):
    """创建任务的事件要和触发它的那次 HTTP 请求用同一个 request_id 串起来。"""
    caplog.set_level(logging.INFO, logger=observability.LOGGER_NAME)
    scope = admin_client.post("/api/scopes", json={"name": "观测范围", "allowed_domains": ["example.test"]})
    assert scope.status_code in (200, 201), scope.get_data(as_text=True)
    scope_id = scope.get_json()["scope"]["id"]

    response = admin_client.post(
        "/api/jobs",
        json={
            "scope_id": scope_id,
            "targets": ["a.example.test", "b.example.test"],
            "tools": ["subfinder"],
            "mode": "mock",
        },
    )
    assert response.status_code == 202, response.get_data(as_text=True)
    request_id = response.headers[observability.REQUEST_ID_HEADER]

    events = _events(caplog, observability.EVENT_JOB_CREATED)
    assert events, "创建任务必须产出 job_created 事件"
    event = events[-1]
    assert event["request_id"] == request_id
    assert event["job_id"] == response.get_json()["job_id"]
    assert event["target_count"] == 2
    # 方案第 19 节：不把完整目标列表写进公共日志。
    assert "targets" not in event
    assert "a.example.test" not in caplog.records[-1].getMessage()


# ── 执行层：job_step_finished ────────────────────────────


def _run_mock_job(targets=None):
    from core import scope_store
    from jobs.executor import execute_job

    scope_id = scope_store.create(name="观测范围", allowed_domains=["example.test"]).id
    job = jobs_store.create_job(
        scope_id=scope_id,
        targets=targets or ["example.test"],
        tools=["subfinder"],
        mode="mock",
    )
    jobs_store.claim_next_job("obs-worker", lease_seconds=300)
    return job, execute_job(job["id"])


def test_step_finished_event_carries_step_and_job_id(caplog):
    caplog.set_level(logging.INFO, logger=observability.LOGGER_NAME)
    job, _ = _run_mock_job()

    events = _events(caplog, observability.EVENT_JOB_STEP_FINISHED)
    assert len(events) == 1
    event = events[0]
    step = jobs_store.list_steps(job["id"])[0]

    assert event["job_id"] == job["id"]
    assert event["step_id"] == step["id"]
    assert event["tool"] == "subfinder"
    assert event["status"] == jobs_store.STEP_SUCCEEDED
    # mock 步骤不写库里的 duration_ms（M4 契约），但事件里是真实墙钟测量。
    assert isinstance(event["duration_ms"], int)
    assert event["duration_ms"] >= 0
    assert step["duration_ms"] is None


def test_step_finished_event_does_not_dump_target_list(caplog):
    """多个目标时，事件只带当前这一步的目标，不倒整份列表。"""
    caplog.set_level(logging.INFO, logger=observability.LOGGER_NAME)
    targets = ["a.example.test", "b.example.test", "c.example.test"]
    job, _ = _run_mock_job(targets=targets)

    events = _events(caplog, observability.EVENT_JOB_STEP_FINISHED)
    assert len(events) == len(targets)
    assert [event["target"] for event in events] == targets
    for event in events:
        assert "targets" not in event  # 没有整份列表字段


def test_failed_step_is_logged_at_warning(caplog):
    """失败步骤用 WARNING 级别，便于告警规则按级别过滤。"""
    caplog.set_level(logging.INFO, logger=observability.LOGGER_NAME)
    from core import scope_store
    from jobs.executor import execute_job

    scope_id = scope_store.create(name="失败范围", allowed_domains=["example.test"]).id
    job = jobs_store.create_job(
        scope_id=scope_id,
        targets=["example.test"],
        tools=["subfinder"],
        mode="mock",
        scenario="timeout",  # core.mock 的确定性失败场景
    )
    jobs_store.claim_next_job("obs-worker", lease_seconds=300)
    result = execute_job(job["id"])

    step = jobs_store.list_steps(job["id"])[0]
    assert step["status"] == jobs_store.STEP_TIMEOUT

    events = _events(caplog, observability.EVENT_JOB_STEP_FINISHED)
    assert events and events[0]["level"] == "WARNING"
    assert events[0]["status"] == jobs_store.STEP_TIMEOUT
    assert events[0]["error_code"] == "timeout"
    assert result["status"] in (jobs_store.STATUS_FAILED, jobs_store.STATUS_PARTIAL, jobs_store.STATUS_TIMEOUT)


# ── worker 层：worker_id 关联 ────────────────────────────


def test_worker_emits_correlated_events(caplog):
    from jobs.worker import Worker

    caplog.set_level(logging.DEBUG, logger=observability.LOGGER_NAME)
    from core import scope_store

    scope_id = scope_store.create(name="worker 范围", allowed_domains=["example.test"]).id
    jobs_store.create_job(scope_id=scope_id, targets=["example.test"], tools=["subfinder"], mode="mock")

    worker = Worker(worker_id="obs-worker-1", verbose=False, recover_on_start=False)
    worker.startup()
    try:
        result = worker.tick()
        assert result is not None
        # worker 上下文在 startup() 之后、shutdown() 之前是持久的
        # （任务事件自动继承 worker_id）。
        assert observability.current_context().get("worker_id") == "obs-worker-1"
    finally:
        worker.shutdown()
    # 退出后必须还原，避免同一线程里的后续代码继承已死的 worker 身份。
    assert observability.current_context() == {}

    claimed = _events(caplog, observability.EVENT_WORKER_CLAIMED_JOB)
    assert claimed and claimed[0]["worker_id"] == "obs-worker-1"

    finished = _events(caplog, observability.EVENT_WORKER_JOB_FINISHED)
    assert finished and finished[0]["worker_id"] == "obs-worker-1"
    assert finished[0]["job_id"] == result["id"]
    assert finished[0]["status"] == result["status"]


def test_worker_context_manager_restores_context(caplog):
    """``with Worker(...)`` 必须成对 startup/shutdown，退出时清干净上下文。"""
    from core import scope_store
    from jobs.worker import Worker

    caplog.set_level(logging.DEBUG, logger=observability.LOGGER_NAME)
    scope_id = scope_store.create(name="ctx 范围", allowed_domains=["example.test"]).id
    jobs_store.create_job(scope_id=scope_id, targets=["example.test"], tools=["subfinder"], mode="mock")

    with Worker(worker_id="ctx-worker", verbose=False, recover_on_start=False) as worker:
        assert observability.current_context().get("worker_id") == "ctx-worker"
        assert worker.tick() is not None

    assert observability.current_context() == {}


def test_worker_log_does_not_print_when_quiet(capsys):
    from jobs.worker import Worker

    worker = Worker(worker_id="quiet-worker", verbose=False, recover_on_start=False)
    worker.log("不应出现在 stdout")
    captured = capsys.readouterr()
    assert "不应出现在 stdout" not in captured.out
    assert "不应出现在 stdout" not in captured.err


# ── 长方案 P1-5 的验收口径：一个 job_id 串起整条执行链 ────────


def test_single_job_id_stitches_the_whole_chain(admin_client, caplog):
    """长方案 P1-5 验收原话：「输入一个 job_id 可以串起整条执行链」。

    这条用例按**排障时的真实动作**来写：拿一个 ``job_id`` 去日志里捞，
    应当一次性看到 Web 层（创建）→ 执行层（开始 / 每一步 / 结束）→
    worker 层（领取 / 任务结束）的全部事件，且都带这一个 job_id。
    这是整轮 §19 改造的**端到端验收**，比逐个字段的单测更能说明问题。
    """
    from jobs.worker import Worker

    caplog.set_level(logging.DEBUG, logger=observability.LOGGER_NAME)

    scope = admin_client.post("/api/scopes", json={"name": "链路范围", "allowed_domains": ["example.test"]})
    assert scope.status_code in (200, 201), scope.get_data(as_text=True)
    scope_id = scope.get_json()["scope"]["id"]

    response = admin_client.post(
        "/api/jobs",
        json={
            "scope_id": scope_id,
            "targets": ["a.example.test", "b.example.test"],
            "tools": ["subfinder"],
            "mode": "mock",
        },
    )
    assert response.status_code == 202, response.get_data(as_text=True)
    job_id = response.get_json()["job_id"]
    request_id = response.headers[observability.REQUEST_ID_HEADER]

    with Worker(worker_id="trace-worker", verbose=False, recover_on_start=False) as worker:
        processed = 0
        while worker.tick() is not None:
            processed += 1
            if processed > 20:  # 防御，正常不会走到
                break
    assert processed == 1

    # 模拟排障：只拿 job_id 去捞日志。
    all_events = [json.loads(record.getMessage()) for record in caplog.records if record.name == observability.LOGGER_NAME]
    trace = [event for event in all_events if event.get("job_id") == job_id]
    events = {event["event"] for event in trace}

    # 链路两端 + 每个步骤都在，且全部共用同一个 job_id。
    assert {
        observability.EVENT_JOB_CREATED,
        observability.EVENT_JOB_STARTED,
        observability.EVENT_JOB_STEP_FINISHED,
        observability.EVENT_JOB_FINISHED,
        observability.EVENT_WORKER_CLAIMED_JOB,
        observability.EVENT_WORKER_JOB_FINISHED,
    } <= events

    # 创建事件额外带 request_id，把「这一跳 HTTP 请求」也接上。
    created = [event for event in trace if event["event"] == observability.EVENT_JOB_CREATED]
    assert created and created[0]["request_id"] == request_id

    # 每一步都能拿到 step_id，且 step_id 互不相同（同一步的 started/finished 才该相同）。
    step_ids = {event["step_id"] for event in trace if event.get("step_id")}
    assert len(step_ids) == 2  # 2 个目标 = 2 个步骤

    # worker 侧事件都带 worker_id，便于区分是哪个 worker 跑的。
    worker_events = [event for event in trace if event["event"].startswith("worker_")]
    assert worker_events
    assert all(event.get("worker_id") == "trace-worker" for event in worker_events)

    # 方案第 19 节：这份「公共日志」里不能出现完整目标列表。
    assert "targets" not in created[0]
    assert created[0]["target_count"] == 2


def test_log_trace_never_contains_the_scope_target_list(admin_client, caplog):
    """反向守卫：整条链的日志里不得出现「一次性列出全部目标」的字段。

    逐步的 ``target`` 是允许的（那是当前这一步的目标，排查必需）；
    被禁止的是把整份目标列表塞进一个字段 —— 这正是方案第 19 节最后一句。
    """
    from jobs.worker import Worker

    caplog.set_level(logging.DEBUG, logger=observability.LOGGER_NAME)
    scope = admin_client.post("/api/scopes", json={"name": "守卫范围", "allowed_domains": ["example.test"]})
    scope_id = scope.get_json()["scope"]["id"]
    targets = [f"host{i}.example.test" for i in range(12)]

    response = admin_client.post(
        "/api/jobs",
        json={"scope_id": scope_id, "targets": targets, "tools": ["subfinder"], "mode": "mock"},
    )
    assert response.status_code == 202

    with Worker(worker_id="guard-worker", verbose=False, recover_on_start=False) as worker:
        while worker.tick() is not None:
            pass

    for record in caplog.records:
        if record.name != observability.LOGGER_NAME:
            continue
        payload = json.loads(record.getMessage())
        # 不许有「整份目标列表」字段（各种可能的名字都堵上）。
        for key in ("targets", "target_list", "all_targets", "scope_targets"):
            assert key not in payload, f"日志里出现了整份目标列表字段 {key}: {payload}"
        # 任何列表字段都不该装下整份目标清单（逐步的 ``target`` 是字符串，不受影响）。
        for key, value in payload.items():
            if isinstance(value, list):
                assert len(value) < len(targets), f"{key} 疑似装下了完整目标列表: {payload}"
