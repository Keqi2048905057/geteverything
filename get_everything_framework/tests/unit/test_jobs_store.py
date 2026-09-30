"""M3 单元测试：任务数据层（``core.jobs``）。

覆盖方案第 6.1 节状态机与第 7 节 jobs/job_steps/job_events 字段要求，
以及 M3 验收里最容易被忽略的两条：

* worker 被 kill 后任务不会静默消失（租约过期 → ``interrupted``）；
* 任务一定有 ``started_at`` / ``finished_at`` / ``error_code``。
"""

import pytest

from core import jobs as jobs_store


@pytest.fixture
def scope_id(local_db):
    """建一个临时 Scope 供 job 关联。"""
    from core import scope_store

    return scope_store.create(name="任务测试范围", allowed_domains=["example.test"]).id


def _make_job(scope_id, targets=("a.example.test",), tools=("subfinder",), **kwargs):
    return jobs_store.create_job(
        scope_id=scope_id,
        targets=list(targets),
        tools=list(tools),
        **kwargs,
    )


# ── 创建与步骤快照 ────────────────────────────────────────


def test_create_job_is_queued_with_step_snapshot(scope_id):
    job = _make_job(scope_id, targets=["a.example.test", "b.example.test"], tools=["subfinder", "httpx"])

    assert job["status"] == jobs_store.STATUS_QUEUED
    assert job["progress"] == 0
    assert job["total_steps"] == 4
    assert job["done_steps"] == 0
    assert job["started_at"] is None and job["finished_at"] is None
    assert job["error_code"] is None
    assert job["attempt"] == 1

    steps = jobs_store.list_steps(job["id"])
    assert len(steps) == 4
    assert [(s["tool_name"], s["target"]) for s in steps] == [
        ("subfinder", "a.example.test"),
        ("subfinder", "b.example.test"),
        ("httpx", "a.example.test"),
        ("httpx", "b.example.test"),
    ]
    assert {s["status"] for s in steps} == {jobs_store.STEP_PENDING}


def test_create_job_writes_created_event(scope_id):
    job = _make_job(scope_id)
    events = jobs_store.list_events(job["id"])
    assert events[0]["event_type"] == jobs_store.EVENT_JOB_CREATED
    assert events[0]["detail"]["total_steps"] == 1


def test_create_job_rejects_empty_targets(scope_id):
    with pytest.raises(ValueError):
        jobs_store.create_job(scope_id=scope_id, targets=[], tools=["subfinder"])


def test_create_job_rejects_empty_tools(scope_id):
    with pytest.raises(ValueError):
        jobs_store.create_job(scope_id=scope_id, targets=["a.example.test"], tools=[])


# ── 队列与认领 ────────────────────────────────────────────


def test_claim_next_job_returns_none_when_empty(scope_id):
    assert jobs_store.claim_next_job("w1") is None


def test_claim_next_job_is_fifo(scope_id):
    first = _make_job(scope_id)
    second = _make_job(scope_id)

    claimed = jobs_store.claim_next_job("w1")
    assert claimed["id"] == first["id"]
    assert claimed["status"] == jobs_store.STATUS_RUNNING
    assert claimed["worker_id"] == "w1"
    assert claimed["lease_until"] is not None
    # started_at 在认领时写入（验收项：任务必须有 started_at）
    assert claimed["started_at"] is not None

    assert jobs_store.claim_next_job("w2")["id"] == second["id"]
    assert jobs_store.claim_next_job("w3") is None


def test_claim_next_job_does_not_steal_running_job(scope_id):
    """同一个任务不会被第二个 worker 领走。"""
    job = _make_job(scope_id)
    assert jobs_store.claim_next_job("w1")["id"] == job["id"]
    assert jobs_store.claim_next_job("w2") is None


def test_claim_records_started_event(scope_id):
    job = _make_job(scope_id)
    jobs_store.claim_next_job("w1")
    types = [event["event_type"] for event in jobs_store.list_events(job["id"])]
    assert jobs_store.EVENT_JOB_STARTED in types


# ── 租约与恢复 ────────────────────────────────────────────


def test_recover_stale_jobs_marks_interrupted(scope_id):
    """M3 验收：kill worker 后任务不会静默消失。"""
    job = _make_job(scope_id)
    jobs_store.claim_next_job("w-dead")

    # 把租约推到过去，模拟 worker 被 kill 后无人续租。
    import core.db as db

    with db.transaction() as conn:
        conn.execute("UPDATE jobs SET lease_until = ? WHERE id = ?", ("2000-01-01T00:00:00+00:00", job["id"]))

    recovered = jobs_store.recover_stale_jobs()
    assert recovered == [job["id"]]

    after = jobs_store.get_job(job["id"])
    assert after["status"] == jobs_store.STATUS_INTERRUPTED
    assert after["error_code"] == "interrupted"
    assert after["finished_at"] is not None
    assert after["worker_id"] is None and after["lease_until"] is None
    assert after["error_message"]


def test_recover_stale_jobs_keeps_live_lease(scope_id):
    """租约还有效（worker 活着）时不能被误判为中断。"""
    job = _make_job(scope_id)
    jobs_store.claim_next_job("w-alive", lease_seconds=300)

    assert jobs_store.recover_stale_jobs() == []
    assert jobs_store.get_job(job["id"])["status"] == jobs_store.STATUS_RUNNING


def test_recover_resets_running_steps_to_pending(scope_id):
    """中断后步骤回到 pending，retry 才能续跑。"""
    job = _make_job(scope_id)
    jobs_store.claim_next_job("w-dead")
    step = jobs_store.list_steps(job["id"])[0]
    jobs_store.start_step(step["id"])

    import core.db as db

    with db.transaction() as conn:
        conn.execute("UPDATE jobs SET lease_until = ? WHERE id = ?", ("2000-01-01T00:00:00+00:00", job["id"]))

    jobs_store.recover_stale_jobs()
    assert jobs_store.list_steps(job["id"])[0]["status"] == jobs_store.STEP_PENDING


def test_renew_lease(scope_id):
    job = _make_job(scope_id)
    jobs_store.claim_next_job("w1", lease_seconds=1)

    assert jobs_store.renew_lease(job["id"], "w1", lease_seconds=300) is True
    # 非归属 worker 不能续租
    assert jobs_store.renew_lease(job["id"], "w-other", lease_seconds=300) is False


def test_release_job_marks_interrupted_immediately(scope_id):
    """优雅退出：不等租约过期就立刻标 interrupted。"""
    job = _make_job(scope_id)
    jobs_store.claim_next_job("w1", lease_seconds=300)

    assert jobs_store.release_job(job["id"], "w1") is True
    assert jobs_store.get_job(job["id"])["status"] == jobs_store.STATUS_INTERRUPTED


def test_release_job_respects_ownership(scope_id):
    job = _make_job(scope_id)
    jobs_store.claim_next_job("w1", lease_seconds=300)

    assert jobs_store.release_job(job["id"], "w-other") is False
    assert jobs_store.get_job(job["id"])["status"] == jobs_store.STATUS_RUNNING


# ── 取消 ──────────────────────────────────────────────────


def test_cancel_queued_job_is_immediate(scope_id):
    """M3 验收：取消后状态变为 cancelled。"""
    job = _make_job(scope_id)
    cancelled = jobs_store.request_cancel(job["id"])

    assert cancelled["status"] == jobs_store.STATUS_CANCELLED
    assert cancelled["finished_at"] is not None
    assert cancelled["progress"] == 100
    # 被取消的 queued 任务不会再有 worker 领到
    assert jobs_store.claim_next_job("w1") is None


def test_cancel_queued_job_skips_pending_steps(scope_id):
    job = _make_job(scope_id, targets=["a.example.test", "b.example.test"])
    jobs_store.request_cancel(job["id"])
    assert {step["status"] for step in jobs_store.list_steps(job["id"])} == {jobs_store.STEP_SKIPPED}


def test_cancel_running_job_sets_flag(scope_id):
    """running 任务不能立刻改状态，只能打标记让 worker 收尾。"""
    job = _make_job(scope_id)
    jobs_store.claim_next_job("w1", lease_seconds=300)

    result = jobs_store.request_cancel(job["id"])
    assert result["status"] == jobs_store.STATUS_RUNNING
    assert result["cancel_requested"] is True
    assert jobs_store.is_cancel_requested(job["id"]) is True


def test_cancel_is_idempotent_on_terminal_job(scope_id):
    job = _make_job(scope_id)
    jobs_store.request_cancel(job["id"])
    again = jobs_store.request_cancel(job["id"])
    assert again["status"] == jobs_store.STATUS_CANCELLED


def test_cancel_unknown_job_returns_none(local_db):
    assert jobs_store.request_cancel("job_missing") is None


# ── 重试 ──────────────────────────────────────────────────


def test_retry_resets_job_and_attempt(scope_id):
    job = _make_job(scope_id)
    jobs_store.request_cancel(job["id"])

    retried = jobs_store.retry_job(job["id"])
    assert retried["status"] == jobs_store.STATUS_QUEUED
    assert retried["attempt"] == 2
    assert retried["progress"] == 0
    assert retried["done_steps"] == 0
    assert retried["started_at"] is None and retried["finished_at"] is None
    assert retried["cancel_requested"] is False


def test_retry_keeps_succeeded_steps(scope_id):
    """已成功的步骤不重跑，只重跑失败部分。"""
    job = _make_job(scope_id, targets=["a.example.test", "b.example.test"])
    jobs_store.claim_next_job("w1", lease_seconds=300)

    steps = jobs_store.list_steps(job["id"])
    jobs_store.finish_step(steps[0]["id"], status=jobs_store.STEP_SUCCEEDED, found_count=1, results=["x"])
    jobs_store.finish_step(steps[1]["id"], status=jobs_store.STEP_FAILED, error_code="unknown_error")
    jobs_store.finish_job(job["id"], status=jobs_store.STATUS_PARTIAL)

    jobs_store.retry_job(job["id"])
    after = {step["id"]: step for step in jobs_store.list_steps(job["id"])}
    assert after[steps[0]["id"]]["status"] == jobs_store.STEP_SUCCEEDED
    assert after[steps[1]["id"]]["status"] == jobs_store.STEP_PENDING


def test_retry_rejects_running_job(scope_id):
    job = _make_job(scope_id)
    jobs_store.claim_next_job("w1", lease_seconds=300)
    with pytest.raises(ValueError):
        jobs_store.retry_job(job["id"])


def test_retry_rejects_succeeded_job(scope_id):
    job = _make_job(scope_id)
    jobs_store.claim_next_job("w1", lease_seconds=300)
    jobs_store.finish_job(job["id"], status=jobs_store.STATUS_SUCCEEDED)
    with pytest.raises(ValueError):
        jobs_store.retry_job(job["id"])


def test_retry_unknown_job_returns_none(local_db):
    assert jobs_store.retry_job("job_missing") is None


# ── 进度与终态 ────────────────────────────────────────────


def test_update_progress_rounds_to_percent(scope_id):
    job = _make_job(scope_id, targets=["a.example.test", "b.example.test", "c.example.test"])
    assert jobs_store.update_progress(job["id"], 1, 3) == 33
    assert jobs_store.update_progress(job["id"], 2, 3) == 67
    assert jobs_store.update_progress(job["id"], 3, 3) == 100
    assert jobs_store.get_job(job["id"])["progress"] == 100


def test_finish_job_requires_terminal_status(scope_id):
    job = _make_job(scope_id)
    with pytest.raises(ValueError):
        jobs_store.finish_job(job["id"], status=jobs_store.STATUS_RUNNING)


def test_finish_job_writes_finished_event(scope_id):
    job = _make_job(scope_id)
    # 方案第 7 节：queued 不能直接落到执行结果终态，必须先被 worker 领成 running。
    jobs_store.claim_next_job("w1", lease_seconds=300)
    jobs_store.finish_job(job["id"], status=jobs_store.STATUS_FAILED, error_code="unknown_error")
    types = [event["event_type"] for event in jobs_store.list_events(job["id"])]
    assert jobs_store.EVENT_JOB_FINISHED in types


# ── 状态机合法跃迁（方案第 7 节） ─────────────────────────


@pytest.mark.parametrize(
    "from_status,to_status,expected",
    [
        # queued 只能被领取或直接取消，不能一步跳到结果态
        (jobs_store.STATUS_QUEUED, jobs_store.STATUS_RUNNING, True),
        (jobs_store.STATUS_QUEUED, jobs_store.STATUS_CANCELLED, True),
        (jobs_store.STATUS_QUEUED, jobs_store.STATUS_SUCCEEDED, False),
        (jobs_store.STATUS_QUEUED, jobs_store.STATUS_TIMEOUT, False),
        (jobs_store.STATUS_QUEUED, jobs_store.STATUS_FAILED, False),
        # running 能落到各终态
        (jobs_store.STATUS_RUNNING, jobs_store.STATUS_SUCCEEDED, True),
        (jobs_store.STATUS_RUNNING, jobs_store.STATUS_PARTIAL, True),
        (jobs_store.STATUS_RUNNING, jobs_store.STATUS_INTERRUPTED, True),
        (jobs_store.STATUS_RUNNING, jobs_store.STATUS_QUEUED, False),
        # 终态之间不能互相跳（只能经 retry 回 queued）
        (jobs_store.STATUS_FAILED, jobs_store.STATUS_SUCCEEDED, False),
        (jobs_store.STATUS_INTERRUPTED, jobs_store.STATUS_QUEUED, True),
        (jobs_store.STATUS_PARTIAL, jobs_store.STATUS_QUEUED, True),
        # succeeded 是绝对终态
        (jobs_store.STATUS_SUCCEEDED, jobs_store.STATUS_QUEUED, False),
        (jobs_store.STATUS_SUCCEEDED, jobs_store.STATUS_FAILED, False),
        # 同名状态幂等
        (jobs_store.STATUS_FAILED, jobs_store.STATUS_FAILED, True),
        # 未知状态一律拒绝
        ("not_a_status", jobs_store.STATUS_QUEUED, False),
    ],
)
def test_can_transition(from_status, to_status, expected):
    assert jobs_store.can_transition(from_status, to_status) is expected


def test_finish_job_rejects_queued_to_terminal(scope_id):
    """回归：``queued → succeeded`` / ``queued → timeout`` 这类跳步必须被拒绝。"""
    job = _make_job(scope_id)
    for illegal in (jobs_store.STATUS_SUCCEEDED, jobs_store.STATUS_TIMEOUT, jobs_store.STATUS_FAILED):
        with pytest.raises(ValueError, match="非法的状态跃迁"):
            jobs_store.finish_job(job["id"], status=illegal)

    assert jobs_store.get_job(job["id"])["status"] == jobs_store.STATUS_QUEUED


def test_finish_job_rejects_unknown_job(local_db):
    with pytest.raises(ValueError, match="任务不存在"):
        jobs_store.finish_job("job_missing", status=jobs_store.STATUS_SUCCEEDED)


def test_finish_job_is_idempotent_on_same_terminal(scope_id):
    """worker 重复写同一个终态不能炸（幂等）。"""
    job = _make_job(scope_id)
    jobs_store.claim_next_job("w1", lease_seconds=300)
    jobs_store.finish_job(job["id"], status=jobs_store.STATUS_SUCCEEDED)
    jobs_store.finish_job(job["id"], status=jobs_store.STATUS_SUCCEEDED)
    assert jobs_store.get_job(job["id"])["status"] == jobs_store.STATUS_SUCCEEDED


def test_finish_job_rejects_transition_from_succeeded(scope_id):
    """succeeded 之后不能被改写成 failed —— 否则「成功」会被静默覆盖。"""
    job = _make_job(scope_id)
    jobs_store.claim_next_job("w1", lease_seconds=300)
    jobs_store.finish_job(job["id"], status=jobs_store.STATUS_SUCCEEDED)

    with pytest.raises(ValueError, match="非法的状态跃迁"):
        jobs_store.finish_job(job["id"], status=jobs_store.STATUS_FAILED)


def test_retry_respects_max_attempts(scope_id):
    """方案第 7 节：必须有 max attempts，防止无限重试刷爆队列。"""
    job = _make_job(scope_id)

    for expected_attempt in range(2, jobs_store.MAX_ATTEMPTS + 1):
        jobs_store.request_cancel(job["id"])
        retried = jobs_store.retry_job(job["id"])
        assert retried["attempt"] == expected_attempt

    # 已到上限：再 retry 必须被拒绝，且任务状态不变。
    jobs_store.request_cancel(job["id"])
    with pytest.raises(ValueError, match="最大重试次数"):
        jobs_store.retry_job(job["id"])
    assert jobs_store.get_job(job["id"])["status"] == jobs_store.STATUS_CANCELLED


# ── 查询 ──────────────────────────────────────────────────


def test_list_jobs_filters_by_status(scope_id):
    done = _make_job(scope_id)
    _make_job(scope_id)
    jobs_store.request_cancel(done["id"])

    assert len(jobs_store.list_jobs()) == 2
    cancelled = jobs_store.list_jobs(status=jobs_store.STATUS_CANCELLED)
    assert [job["id"] for job in cancelled] == [done["id"]]


def test_queue_counts(scope_id):
    _make_job(scope_id)
    running = _make_job(scope_id)
    jobs_store.claim_next_job("w1", lease_seconds=300)

    counts = jobs_store.queue_counts()
    assert counts["queued"] == 1
    assert counts["running"] == 1
    assert counts["total"] == 2
    assert running["id"]  # 仅用于说明这两个 job 确实存在


def test_get_job_detail_contains_steps_and_events(scope_id):
    job = _make_job(scope_id)
    detail = jobs_store.get_job_detail(job["id"])
    assert detail["id"] == job["id"]
    assert len(detail["steps"]) == 1
    assert detail["events"]


def test_get_job_detail_unknown_returns_none(local_db):
    assert jobs_store.get_job_detail("job_missing") is None


def test_get_job_or_raise_distinguishes_missing_job(scope_id):
    """``get_job`` 可空、``get_job_or_raise`` 不可空 —— 两种语义都要有。

    写路径（``create_job`` / ``cancel_job`` / ``finish_job``）刚写完就回读，
    ``None`` 属于不可能状态；让它们返回 ``dict | None`` 会把 None 判断一路
    传染给调用方，也把「任务真的不见了」降级成一次普通解引用。
    """
    job = _make_job(scope_id)

    assert jobs_store.get_job_or_raise(job["id"])["id"] == job["id"]
    with pytest.raises(ValueError, match="任务不存在"):
        jobs_store.get_job_or_raise("job_missing")
