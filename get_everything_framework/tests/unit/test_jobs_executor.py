"""M3 单元测试：任务执行器与 worker 进程（``jobs.executor`` / ``jobs.worker``）。

重点是 M3 验收里那几条「不做就看不出来」的行为：

* mock 模式绝不触碰真实 runner；
* 进度按步骤写库（刷新页面能看到）；
* cancel 在步骤边界生效并落到 ``cancelled``；
* worker 被 kill 后任务变 ``interrupted``，重启可恢复/重试；
* 任务有 ``started_at`` / ``finished_at`` / ``error_code``。
"""

import os
import subprocess
import sys
import time

import pytest

from core import jobs as jobs_store
from jobs import worker as worker_module
from jobs.executor import aggregate_status, execute_job


@pytest.fixture
def scope_id(local_db):
    from core import scope_store

    return scope_store.create(name="执行器测试范围", allowed_domains=["example.test"]).id


@pytest.fixture
def heartbeat_tmp(tmp_path, monkeypatch):
    """把 worker 心跳文件指到临时目录。"""
    monkeypatch.setattr(worker_module, "OUTPUT_DIR", str(tmp_path / "results"), raising=False)
    return str(tmp_path / "results" / worker_module.HEARTBEAT_FILENAME)


def _make_job(scope_id, targets=("a.example.test",), tools=("subfinder",), **kwargs):
    return jobs_store.create_job(
        scope_id=scope_id,
        targets=list(targets),
        tools=list(tools),
        **kwargs,
    )


# ── mock 执行 ─────────────────────────────────────────────


def test_execute_mock_job_succeeds(scope_id):
    job = _make_job(scope_id, targets=["a.example.test", "b.example.test"], tools=["subfinder"])
    jobs_store.claim_next_job("w1", lease_seconds=300)

    result = execute_job(job["id"])

    assert result["status"] == jobs_store.STATUS_SUCCEEDED
    assert result["progress"] == 100
    assert result["done_steps"] == result["total_steps"] == 2
    assert result["started_at"] and result["finished_at"]
    assert result["error_code"] is None

    steps = jobs_store.list_steps(job["id"])
    assert all(step["status"] == jobs_store.STEP_SUCCEEDED for step in steps)
    assert all(step["found_count"] > 0 for step in steps)
    assert all(step["results"] for step in steps)


def test_execute_mock_never_calls_real_runner(scope_id, monkeypatch):
    """mock 模式必须绕开 modules.registry（方案第 2.3 节第 9 条）。"""
    import modules.registry as registry

    def _explode(*args, **kwargs):  # pragma: no cover
        raise AssertionError("mock 模式不允许构建真实 runner")

    monkeypatch.setattr(registry, "build_runner", _explode)

    job = _make_job(scope_id)
    jobs_store.claim_next_job("w1", lease_seconds=300)
    assert execute_job(job["id"])["status"] == jobs_store.STATUS_SUCCEEDED


def test_execute_partial_scenario(scope_id):
    job = _make_job(scope_id, scenario="partial")
    jobs_store.claim_next_job("w1", lease_seconds=300)
    result = execute_job(job["id"])
    assert result["status"] == jobs_store.STATUS_SUCCEEDED  # 单步 partial 仍算跑通
    assert jobs_store.list_steps(job["id"])[0]["error_code"] == "partial_success"


def test_execute_all_failed_scenario(scope_id):
    job = _make_job(scope_id, targets=["a.example.test", "b.example.test"], scenario="tool_not_found")
    jobs_store.claim_next_job("w1", lease_seconds=300)

    result = execute_job(job["id"])
    assert result["status"] == jobs_store.STATUS_FAILED
    assert result["error_code"] == "unknown_error"
    assert result["error_message"]
    assert all(step["error_code"] == "tool_not_found" for step in jobs_store.list_steps(job["id"]))


def test_execute_timeout_scenario(scope_id):
    job = _make_job(scope_id, scenario="timeout")
    jobs_store.claim_next_job("w1", lease_seconds=300)

    result = execute_job(job["id"])
    assert result["status"] == jobs_store.STATUS_TIMEOUT
    assert result["error_code"] == "timeout"
    assert jobs_store.list_steps(job["id"])[0]["status"] == jobs_store.STEP_TIMEOUT


def test_execute_empty_scenario_is_not_failure(scope_id):
    """零结果与失败必须区分（方案 M4 验收项，M3 已把语义打通）。"""
    job = _make_job(scope_id, scenario="empty")
    jobs_store.claim_next_job("w1", lease_seconds=300)

    result = execute_job(job["id"])
    step = jobs_store.list_steps(job["id"])[0]
    assert result["status"] == jobs_store.STATUS_SUCCEEDED
    assert step["status"] == jobs_store.STEP_SUCCEEDED
    assert step["found_count"] == 0
    # M4：跑通但零结果必须带 no_results，而不是与「成功有结果」同为 None。
    assert step["error_code"] == "no_results"


def test_execute_writes_progress_events(scope_id):
    job = _make_job(scope_id, targets=["a.example.test", "b.example.test"])
    jobs_store.claim_next_job("w1", lease_seconds=300)
    execute_job(job["id"])

    progress_events = [
        event for event in jobs_store.list_events(job["id"]) if event["event_type"] == jobs_store.EVENT_JOB_PROGRESS
    ]
    assert [event["detail"]["progress"] for event in progress_events] == [50, 100]


def test_execute_ingests_assets_from_steps(scope_id):
    """P1（方案第 8 节）：任务跑完，资产与观测必须已落库。

    mock 的 subfinder 结果形如 ``www.a.example.test``，经
    ``ingest_step_observations`` 应落成 ``subdomain`` 资产；
    同一资产在两个目标里重复出现时只留一行资产、两行观测。
    """
    from core import assets as assets_store

    job = _make_job(scope_id, targets=["a.example.test", "b.example.test"], tools=["subfinder"])
    jobs_store.claim_next_job("w1", lease_seconds=300)
    execute_job(job["id"])

    found = assets_store.list_assets(scope_id=scope_id, limit=100)
    assert found, "任务跑完却没有落任何资产"
    assert all(item["type"] == "subdomain" for item in found)
    # 每个资产都指向产生它的任务 —— 「能知道是谁发现的」。
    for asset in found:
        observations = assets_store.list_observations(asset_id=asset["id"])
        assert observations
        assert all(item["job_id"] == job["id"] for item in observations)
        assert all(item["source_tool"] == "subfinder" for item in observations)


def test_execute_records_assets_ingested_event(scope_id):
    """落观测的结果要留事件，否则「资产页少了几条」无法在任务详情里排查。"""
    job = _make_job(scope_id, targets=["c.example.test"], tools=["subfinder"])
    jobs_store.claim_next_job("w1", lease_seconds=300)
    execute_job(job["id"])

    ingest_events = [
        event
        for event in jobs_store.list_events(job["id"])
        if event["event_type"] == jobs_store.EVENT_ASSETS_INGESTED
    ]
    assert ingest_events
    assert ingest_events[0]["detail"]["written"] > 0


def test_ingest_failure_does_not_fail_the_job(scope_id, monkeypatch):
    """落观测失败时任务仍必须是 succeeded —— 观测是派生产物。"""
    from core import assets as assets_store

    def _boom(*args, **kwargs):
        raise RuntimeError("模拟落观测失败")

    monkeypatch.setattr(assets_store, "record_observations", _boom)

    job = _make_job(scope_id, targets=["d.example.test"], tools=["subfinder"])
    jobs_store.claim_next_job("w1", lease_seconds=300)
    result = execute_job(job["id"])

    assert result["status"] == jobs_store.STATUS_SUCCEEDED
    assert jobs_store.list_steps(job["id"])[0]["status"] == jobs_store.STEP_SUCCEEDED


def test_execute_calls_renew_between_steps(scope_id):
    job = _make_job(scope_id, targets=["a.example.test", "b.example.test"])
    jobs_store.claim_next_job("w1", lease_seconds=300)

    calls = []
    execute_job(job["id"], renew=lambda: calls.append(1))
    assert len(calls) == 2


# ── 执行前的 Scope 复检（P0-2） ───────────────────────────


def test_real_step_rechecks_scope_before_calling_runner(scope_id, monkeypatch):
    """Job 创建后 Scope 被删除：real 步骤必须被拦下，且绝不进入 Runner。

    对应方案第 5.3 节「每个 Step 执行前」的复检要求 —— 不能相信创建时的快照。
    """
    import modules.registry as registry
    from core import db

    called = []

    class _Runner:
        tool_name = "subfinder"
        category = "subdomain"

        def run(self, target):  # pragma: no cover - 走到这里就说明复检失效了
            called.append(target)
            raise AssertionError("Scope 已失效，Runner 不允许被调用")

    monkeypatch.setattr(registry, "build_runner", lambda name: _Runner())

    job = _make_job(scope_id, mode="real")
    jobs_store.claim_next_job("w1", lease_seconds=300)

    # 模拟「创建后被删除」：直接删掉 Scope 行。
    with db.transaction() as conn:
        conn.execute("DELETE FROM scopes WHERE id = ?", (scope_id,))

    result = execute_job(job["id"])

    step = jobs_store.list_steps(job["id"])[0]
    assert called == []  # 关键：Runner 一次都没被调用
    assert step["status"] == jobs_store.STEP_FAILED
    assert step["error_code"] == "scope_violation"
    assert "复检" in step["error_message"]
    assert result["status"] == jobs_store.STATUS_FAILED


def test_real_step_rechecks_target_still_in_scope(scope_id, monkeypatch):
    """执行期复检通过时，步骤照常执行（复检不能把正常路径也拦死）。"""
    import modules.registry as registry
    from core.runner_result import RunnerResult

    class _Runner:
        tool_name = "subfinder"
        category = "subdomain"
        last_execution = {}

        def run(self, target):
            return RunnerResult.ok([], exit_code=0)

    monkeypatch.setattr(registry, "build_runner", lambda name: _Runner())

    job = _make_job(scope_id, mode="real")
    jobs_store.claim_next_job("w1", lease_seconds=300)

    result = execute_job(job["id"])
    assert result["status"] == jobs_store.STATUS_SUCCEEDED


# ── cancel ────────────────────────────────────────────────


def test_execute_respects_cancel_request_between_steps(scope_id, monkeypatch):
    """第一步跑完后收到取消请求，第二步必须被跳过。"""
    job = _make_job(scope_id, targets=["a.example.test", "b.example.test"])
    jobs_store.claim_next_job("w1", lease_seconds=300)

    original = jobs_store.update_progress

    def _cancel_after_first(job_id, done_steps, total_steps):
        progress = original(job_id, done_steps, total_steps)
        jobs_store.request_cancel(job_id)
        return progress

    monkeypatch.setattr(jobs_store, "update_progress", _cancel_after_first)

    result = execute_job(job["id"])
    assert result["status"] == jobs_store.STATUS_CANCELLED
    assert result["cancel_requested"] is True

    steps = {step["target"]: step["status"] for step in jobs_store.list_steps(job["id"])}
    assert steps["a.example.test"] == jobs_store.STEP_SUCCEEDED
    assert steps["b.example.test"] == jobs_store.STEP_SKIPPED


# ── 状态聚合 ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "statuses,expected",
    [
        ([], (jobs_store.STATUS_SUCCEEDED, None)),
        (["succeeded", "succeeded"], (jobs_store.STATUS_SUCCEEDED, None)),
        (["succeeded", "failed"], (jobs_store.STATUS_PARTIAL, "partial_success")),
        (["failed", "failed"], (jobs_store.STATUS_FAILED, "unknown_error")),
        (["timeout", "timeout"], (jobs_store.STATUS_TIMEOUT, "timeout")),
    ],
)
def test_aggregate_status(statuses, expected):
    assert aggregate_status(statuses) == expected


def test_aggregate_status_cancelled_wins():
    assert aggregate_status(["succeeded"], cancelled=True) == (jobs_store.STATUS_CANCELLED, None)


# ── worker 生命周期 ───────────────────────────────────────


def test_worker_startup_writes_heartbeat(scope_id, heartbeat_tmp):
    worker = worker_module.Worker(worker_id="w-test", verbose=False)
    worker.startup()

    assert os.path.exists(heartbeat_tmp)
    with open(heartbeat_tmp, encoding="utf-8") as handle:
        text = handle.read()
    assert "w-test" in text


def test_worker_recovers_stale_jobs_on_startup(scope_id, heartbeat_tmp):
    job = _make_job(scope_id)
    jobs_store.claim_next_job("w-dead", lease_seconds=300)

    import core.db as db

    with db.transaction() as conn:
        conn.execute("UPDATE jobs SET lease_until = ? WHERE id = ?", ("2000-01-01T00:00:00+00:00", job["id"]))

    worker = worker_module.Worker(worker_id="w-new", verbose=False)
    recovered = worker.startup()

    assert job["id"] in recovered
    assert jobs_store.get_job(job["id"])["status"] == jobs_store.STATUS_INTERRUPTED


def test_worker_tick_returns_none_when_queue_empty(scope_id, heartbeat_tmp):
    worker = worker_module.Worker(worker_id="w-test", verbose=False)
    worker.startup()
    assert worker.tick() is None


def test_worker_tick_executes_one_job(scope_id, heartbeat_tmp):
    job = _make_job(scope_id)
    worker = worker_module.Worker(worker_id="w-test", verbose=False)
    worker.startup()

    result = worker.tick()
    assert result["id"] == job["id"]
    assert result["status"] == jobs_store.STATUS_SUCCEEDED


def test_worker_shutdown_releases_current_job(scope_id, heartbeat_tmp, monkeypatch):
    """优雅退出时把在跑的任务立刻标 interrupted，不等租约过期。"""
    job = _make_job(scope_id)
    worker = worker_module.Worker(worker_id="w-test", verbose=False)
    worker.startup()
    jobs_store.claim_next_job("w-test", lease_seconds=300)
    worker._current_job_id = job["id"]

    worker.shutdown()
    assert jobs_store.get_job(job["id"])["status"] == jobs_store.STATUS_INTERRUPTED


def test_worker_main_once_exits(scope_id, heartbeat_tmp):
    """``--once`` 用于验收脚本与 CI。"""
    _make_job(scope_id)
    code = worker_module.main(["--once", "--quiet", "--worker-id", "w-once"])
    assert code == 0
    assert jobs_store.list_jobs(status=jobs_store.STATUS_SUCCEEDED)


# ── 真实子进程：kill 后重启不丢任务 ───────────────────────


@pytest.mark.slow
def test_worker_process_killed_then_restarted_does_not_lose_job(scope_id, local_db):
    """M3 验收：kill worker 后重启，任务不会静默消失。

    这里真的起一个子进程 worker：

    1. 它领走任务后立刻被 kill → 任务停在 ``running``（既没成功也没消失）；
    2. 新 worker 启动时回收过期租约 → 任务变 ``interrupted`` 并可以被 retry。
    """
    job = _make_job(scope_id, targets=["a.example.test", "b.example.test"], tools=["subfinder", "httpx"])

    # 项目根 = tests/unit/ 往上三层（tests → 项目根）。
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = os.environ.copy()
    # 子进程必须连到与测试同一个临时库，否则它去建自己的库。
    env["LOCAL_DB_PATH"] = local_db
    # 同理，子进程的**运行期目录**也要改道：worker 启动会写
    # ``<OUTPUT_DIR>/worker_heartbeat``，父进程的 monkeypatch 传不下去，
    # 只有环境变量能拦（``GEF_OUTPUT_DIR`` 是唯一由 config 读取的出口）。
    env["GEF_OUTPUT_DIR"] = os.path.dirname(local_db)
    # 显式拼 PYTHONPATH：不要留下结尾的分隔符，否则会多出一个空路径项，
    # 子进程可能既找不到 ``jobs`` 包、又读不到正确的项目根。
    existing = env.get("PYTHONPATH", "").strip()
    parts = [project_root] + [p for p in existing.split(os.pathsep) if p.strip()]
    env["PYTHONPATH"] = os.pathsep.join(parts)

    # 注意：不要让子进程往一个父进程不读的管道里写日志 —— 对端关闭会让子进程
    # 在 print 时拿到 BrokenPipeError（Python 以 120 退出），于是它还没领到任务
    # 就死了。--quiet + DEVNULL 是最省事也最稳的组合。
    # --step-delay 让任务在 running 上停留足够久，否则轮询来不及看到它。
    log_path = os.path.join(os.path.dirname(local_db), "worker-child.log")
    with open(log_path, "w", encoding="utf-8") as log_handle:
        proc = subprocess.Popen(
            [
                sys.executable, "-m", "jobs.worker",
                "--worker-id", "w-killed",
                "--lease", "600",
                "--step-delay", "5",
                "--quiet",
            ],
            cwd=project_root,
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.time() + 30
            claimed = False
            while time.time() < deadline:
                if jobs_store.get_job(job["id"])["status"] == jobs_store.STATUS_RUNNING:
                    claimed = True
                    break
                if proc.poll() is not None:
                    break
                time.sleep(0.2)
            if not claimed:
                with open(log_path, encoding="utf-8", errors="replace") as handle:
                    child_output = handle.read()
                raise AssertionError(
                    f"worker 未能在 30 秒内领到任务（退出码 {proc.poll()}）\n{child_output}"
                )
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)

    # 被 kill 后任务仍在 running（有租约），不是消失
    assert jobs_store.get_job(job["id"])["status"] == jobs_store.STATUS_RUNNING

    # 重启新 worker：启动时回收过期租约（这里把租约人为推旧，避免等 600 秒）
    from core import db

    with db.transaction() as conn:
        conn.execute("UPDATE jobs SET lease_until = ? WHERE id = ?", ("2000-01-01T00:00:00+00:00", job["id"]))

    code = worker_module.main(["--once", "--quiet", "--worker-id", "w-restart"])
    assert code == 0

    after = jobs_store.get_job(job["id"])
    # 关键断言：任务仍然存在，且有明确状态与错误码，而不是静默消失
    assert after["id"] == job["id"]
    assert after["status"] in {jobs_store.STATUS_INTERRUPTED, jobs_store.STATUS_SUCCEEDED}
    if after["status"] == jobs_store.STATUS_INTERRUPTED:
        assert after["error_code"] == "interrupted"
        assert after["error_message"]
        # interrupted 任务可以重试
        assert jobs_store.retry_job(job["id"])["status"] == jobs_store.STATUS_QUEUED
