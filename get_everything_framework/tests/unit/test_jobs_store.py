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


# ── P0-7b：重试退避 ───────────────────────────────────────


def test_retry_sets_backoff_window(scope_id):
    """重试后任务立刻是 queued，但要等退避窗口过去 worker 才领得到。

    这是「retry 必须退避」的可观测形态：``next_attempt_at`` 有值、且在将来。
    """
    job = _make_job(scope_id)
    jobs_store.request_cancel(job["id"])

    retried = jobs_store.retry_job(job["id"])
    assert retried["status"] == jobs_store.STATUS_QUEUED
    assert retried["attempt"] == 2
    assert retried["next_attempt_at"] is not None
    assert retried["next_attempt_at"] > jobs_store._now()


def test_backoff_delays_claim_until_window_passes(scope_id):
    """退避窗口内领不到；把 ``next_attempt_at`` 拨到过去后立刻能领。"""
    job = _make_job(scope_id)
    jobs_store.request_cancel(job["id"])
    jobs_store.retry_job(job["id"])

    # 窗口内：任务还在队列里（queued 计数含它），但取不到。
    assert jobs_store.claim_next_job("w1") is None
    assert jobs_store.queue_counts()["queued"] == 1

    # 人为把窗口推旧，等价于「等待退避时间过去」。
    from core import db

    with db.transaction() as conn:
        conn.execute(
            "UPDATE jobs SET next_attempt_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", job["id"]),
        )

    claimed = jobs_store.claim_next_job("w2")
    assert claimed["id"] == job["id"]
    assert claimed["status"] == jobs_store.STATUS_RUNNING
    # 领走后退避窗口被清空：窗口只用来「推迟领取」，不是任务的长期属性。
    assert claimed["next_attempt_at"] is None


def test_claim_clears_expired_backoff_window(scope_id):
    """退避窗口已过期后被领取，``next_attempt_at`` 也要被清掉。

    否则一个「早就过期的窗口」会一直挂在这个任务上，事后看任务详情会
    误导成「这个任务还在退避中」。
    """
    job = _make_job(scope_id)
    jobs_store.request_cancel(job["id"])
    jobs_store.retry_job(job["id"])

    from core import db

    with db.transaction() as conn:
        conn.execute(
            "UPDATE jobs SET next_attempt_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", job["id"]),
        )

    claimed = jobs_store.claim_next_job("w1")
    assert claimed["next_attempt_at"] is None


def test_backoff_does_not_block_other_jobs(scope_id):
    """一个任务在退避，不能把后面排队的任务一起堵住。"""
    first = _make_job(scope_id)
    second = _make_job(scope_id)
    jobs_store.request_cancel(first["id"])
    jobs_store.retry_job(first["id"])

    claimed = jobs_store.claim_next_job("w1")
    assert claimed["id"] == second["id"]


def test_retry_backoff_grows_and_is_capped():
    """退避函数本身：第 1 次不退避，之后指数增长并封顶。"""
    assert jobs_store.retry_backoff_seconds(1) == 0
    assert jobs_store.retry_backoff_seconds(2) == jobs_store.RETRY_BACKOFF_BASE_SECONDS

    growth = [jobs_store.retry_backoff_seconds(n) for n in range(2, 12)]
    assert growth == sorted(growth)
    assert growth[-1] == jobs_store.RETRY_BACKOFF_MAX_SECONDS
    assert max(growth) <= jobs_store.RETRY_BACKOFF_MAX_SECONDS


def test_claim_does_not_consume_next_attempt_at_of_fresh_job(scope_id):
    """新任务的 ``next_attempt_at`` 为空 = 立即可领（既有行为不能变）。"""
    job = _make_job(scope_id)
    assert job["next_attempt_at"] is None
    assert jobs_store.claim_next_job("w1")["id"] == job["id"]


# ── P0-7a：幂等键 ─────────────────────────────────────────


def test_create_job_with_same_idempotency_key_reuses_job(scope_id):
    """同一个键在任务未终结期间只产生一个任务，返回同一个 job_id。"""
    first, reused_first = jobs_store.create_job_with_status(
        scope_id=scope_id, targets=["a.example.test"], tools=["subfinder"], idempotency_key="k-1"
    )
    second, reused_second = jobs_store.create_job_with_status(
        scope_id=scope_id, targets=["a.example.test"], tools=["subfinder"], idempotency_key="k-1"
    )

    assert reused_first is False
    assert reused_second is True
    assert first["id"] == second["id"]
    assert len(jobs_store.list_jobs()) == 1
    # 幂等命中的那次不重复展开步骤，否则进度分母会被算错。
    assert len(jobs_store.list_steps(first["id"])) == 1


def test_idempotency_key_is_visible_on_job(scope_id):
    job = _make_job(scope_id, idempotency_key="k-visible")
    assert job["idempotency_key"] == "k-visible"
    assert jobs_store.get_job(job["id"])["idempotency_key"] == "k-visible"


def test_idempotency_key_is_normalized(scope_id):
    """两侧空白会被裁掉：``" k "`` 与 ``"k"`` 是同一把键。"""
    first = _make_job(scope_id, idempotency_key=" k-norm ")
    second, reused = jobs_store.create_job_with_status(
        scope_id=scope_id, targets=["a.example.test"], tools=["subfinder"], idempotency_key="k-norm"
    )
    assert reused is True
    assert second["id"] == first["id"]


def test_create_job_without_idempotency_key_always_creates(scope_id):
    """不传键 = 不幂等，行为与 P0-7 之前完全一致。"""
    _make_job(scope_id)
    _make_job(scope_id)
    assert len(jobs_store.list_jobs()) == 2


def test_idempotency_key_is_released_by_terminal_status(scope_id):
    """键只挡「未终结」任务：任务跑完/取消后同一个键可以再次创建。

    否则「重试一个失败任务」会被幂等键永久挡住，键就从「防重复提交」
    变成了「永久只跑一次」，语义过强。
    """
    first = _make_job(scope_id, idempotency_key="k-release")
    jobs_store.request_cancel(first["id"])

    second, reused = jobs_store.create_job_with_status(
        scope_id=scope_id, targets=["a.example.test"], tools=["subfinder"], idempotency_key="k-release"
    )
    assert reused is False
    assert second["id"] != first["id"]
    assert len(jobs_store.list_jobs()) == 2


def test_idempotency_key_is_not_reused_while_running(scope_id):
    """running 也算「未终结」：任务正在跑时重复提交不能又开一个。"""
    first = _make_job(scope_id, idempotency_key="k-running")
    jobs_store.claim_next_job("w1", lease_seconds=300)

    second, reused = jobs_store.create_job_with_status(
        scope_id=scope_id, targets=["a.example.test"], tools=["subfinder"], idempotency_key="k-running"
    )
    assert reused is True
    assert second["id"] == first["id"]


def test_reused_creation_writes_no_extra_created_event(scope_id):
    """幂等命中不是一次新建，不应再写一条 job_created 事件。"""
    job = _make_job(scope_id, idempotency_key="k-event")
    jobs_store.create_job_with_status(
        scope_id=scope_id, targets=["a.example.test"], tools=["subfinder"], idempotency_key="k-event"
    )

    created = [
        event for event in jobs_store.list_events(job["id"]) if event["event_type"] == jobs_store.EVENT_JOB_CREATED
    ]
    assert len(created) == 1


def test_different_idempotency_keys_create_different_jobs(scope_id):
    first = _make_job(scope_id, idempotency_key="k-a")
    second = _make_job(scope_id, idempotency_key="k-b")
    assert first["id"] != second["id"]


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_blank_idempotency_key_means_no_key(scope_id, bad):
    """空串 / 纯空白 / None 一律视为「没传键」，不是「键等于空串」。"""
    assert jobs_store.normalize_idempotency_key(bad) is None
    first = _make_job(scope_id, idempotency_key=bad)
    second = _make_job(scope_id, idempotency_key=bad)
    assert first["id"] != second["id"]


def test_idempotency_key_rejects_wrong_type_and_overlong(scope_id):
    with pytest.raises(ValueError, match="必须是字符串"):
        jobs_store.normalize_idempotency_key(123)
    with pytest.raises(ValueError, match="最长"):
        jobs_store.normalize_idempotency_key("x" * (jobs_store.MAX_IDEMPOTENCY_KEY_LENGTH + 1))


def test_idempotency_and_backoff_columns_are_additive_on_legacy_db(tmp_path):
    """旧库（没有这两列）升级后仍可读写：这是 P0-7 的迁移验收口径。"""
    import sqlite3

    import core.db as db

    path = str(tmp_path / "legacy_jobs.db")
    # 手工造一个「P0-7 之前」的 jobs 表：只有 attempt，没有新增两列。
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, status TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'mock', targets_json TEXT NOT NULL DEFAULT '[]',
            tools_json TEXT NOT NULL DEFAULT '[]', upload_id TEXT, scenario TEXT,
            created_by TEXT, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
            progress INTEGER NOT NULL DEFAULT 0, total_steps INTEGER NOT NULL DEFAULT 0,
            done_steps INTEGER NOT NULL DEFAULT 0, error_code TEXT, error_message TEXT,
            cancel_requested INTEGER NOT NULL DEFAULT 0, worker_id TEXT, lease_until TEXT,
            attempt INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    conn.execute(
        "INSERT INTO jobs (id, scope_id, status, created_at, total_steps) "
        "VALUES ('job_old', 'scope_old', 'queued', '2026-01-01T00:00:00+00:00', 1)"
    )
    conn.commit()
    conn.close()

    db.init_schema(path)

    columns = {row["name"] for row in db.query("PRAGMA table_info(jobs)", path=path)}
    assert {"idempotency_key", "next_attempt_at", "attempt"} <= columns
    # 既有行没被动过，新列取 NULL。
    old = db.query("SELECT * FROM jobs WHERE id = 'job_old'", path=path)[0]
    assert old["status"] == "queued"
    assert old["attempt"] == 1
    assert old["idempotency_key"] is None
    assert old["next_attempt_at"] is None


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
