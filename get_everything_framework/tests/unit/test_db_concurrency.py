"""M7 测试矩阵：SQLite 并发（方案第 15 节「Worker：duplicate execution」+ 第 7 节）。

背景（``AGENTS.md`` 高频坑 #5、``PROJECT_STATE.md`` 的 Known Failure）：

* 旧结果库 ``storage.py`` 早期**没有 WAL、没有 busy_timeout**，并且
  ``with conn`` 只提交不关闭连接 —— 并发写会直接 ``database is locked``；
* 新应用库 ``core/db.py`` 从 M2 起就声明了「WAL + busy_timeout + 事务」，
  但此前**从未被并发验证过**：单线程跑绿并不代表 waitress 的 8 个线程下
  也不会撞锁。

本文件补上这块空白。它只做三件事，全部在**临时库**上完成，不碰仓库
``results/``：

1. 连接参数确实生效（``journal_mode=wal``、``busy_timeout`` 断言）；
2. 多线程并发**写**（创建任务 / 写审计）不抛 ``database is locked``；
3. 多线程并发**认领**同一个队列 —— 任务不重不漏（无重复执行）；
4. 并发使用同一把幂等键 —— 只会有一个任务被真正创建（P0-7a 的并发保证）。

为什么值得单独一个文件：这三条正是「单表即队列」这个设计的全部风险面，
而这个设计一旦在真实并发下失效，症状是**用户看不到任何报错**（任务被领两次
跑两遍，或任务凭空消失），只会在数据里留下重复的观测量。
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

import core.db as core_db
from core import jobs as jobs_store

#: 并发线程数。取 8 与 ``scripts/run_local.ps1`` / waitress 的默认线程数对齐 ——
#: 小于它测不出真实的争抢，大于它只会拖慢用例而不会提高判别力。
THREADS = 8


@pytest.fixture
def scope_id(local_db):
    """建一个临时 Scope 供 job 关联。"""
    from core import scope_store

    return scope_store.create(name="并发测试范围", allowed_domains=["example.test"]).id


def _run_threads(worker, count=THREADS) -> list:
    """并发跑 ``worker(index)``，返回各线程的返回值；线程内异常原样抛出。

    ``threading`` 默认会把线程里的异常打到 stderr 然后**悄悄结束**，
    于是「8 个线程里挂了 3 个」在测试里看起来可能仍然通过。这里显式收集
    异常，在 join 之后重抛第一个 —— 并发用例最怕的就是把失败读成成功。
    """
    results: list = [None] * count
    errors: list[BaseException] = []
    lock = threading.Lock()

    def _target(index: int) -> None:
        try:
            value = worker(index)
        except BaseException as exc:  # noqa: BLE001 - 必须连 SystemExit 一起抓住
            with lock:
                errors.append(exc)
            return
        results[index] = value

    threads = [threading.Thread(target=_target, args=(i,), name=f"gef-conc-{i}") for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not any(thread.is_alive() for thread in threads), "有线程在 60 秒内没有结束（死锁？）"
    if errors:
        raise AssertionError(f"并发线程里出现 {len(errors)} 个异常，第一个是: {errors[0]!r}") from errors[0]
    return results


# ── 1. 连接参数真的生效 ───────────────────────────────────


def test_new_db_connection_uses_wal_and_busy_timeout(local_db):
    """``core.db.connect`` 必须同时给出 WAL 与 busy_timeout（方案第 7 节）。"""
    conn = core_db.connect()
    try:
        (journal_mode,) = conn.execute("PRAGMA journal_mode").fetchone()
        (busy_timeout,) = conn.execute("PRAGMA busy_timeout").fetchone()
    finally:
        conn.close()

    assert str(journal_mode).lower() == "wal"
    assert busy_timeout == core_db.BUSY_TIMEOUT_MS


def test_wal_mode_survives_reopening(local_db):
    """WAL 是**库文件级**设置：后面的连接不该把它退回 ``delete``。"""
    first = core_db.connect()
    first.close()
    second = core_db.connect()
    try:
        (journal_mode,) = second.execute("PRAGMA journal_mode").fetchone()
    finally:
        second.close()
    assert str(journal_mode).lower() == "wal"


def test_legacy_store_sets_busy_timeout(tmp_path):
    """旧结果库每个连接也带 busy_timeout（HIGH #5 的最小修复不能回退）。"""
    from storage import BUSY_TIMEOUT_MS, ScanResultStore

    store = ScanResultStore(db_path=str(tmp_path / "conc_legacy.db"))
    conn = store._get_connection()
    try:
        (busy_timeout,) = conn.execute("PRAGMA busy_timeout").fetchone()
    finally:
        conn.close()
    assert busy_timeout == BUSY_TIMEOUT_MS


# ── 2. 并发写不撞锁 ───────────────────────────────────────


def test_concurrent_job_creation_does_not_hit_locked_db(scope_id):
    """8 个线程同时创建任务：全部落库，且没有一次 ``database is locked``。"""
    per_thread = 4

    def _worker(index: int) -> list[str]:
        ids = []
        for n in range(per_thread):
            job = jobs_store.create_job(
                scope_id=scope_id,
                targets=[f"t{index}-{n}.example.test"],
                tools=["subfinder"],
                created_by=f"thread-{index}",
            )
            ids.append(job["id"])
        return ids

    created = _run_threads(_worker)

    flat = [job_id for ids in created for job_id in ids]
    assert len(flat) == THREADS * per_thread
    # 主键层面不重复（``new_job_id`` 在并发下也必须唯一）。
    assert len(set(flat)) == len(flat)

    counts = jobs_store.queue_counts()
    assert counts[jobs_store.STATUS_QUEUED] == THREADS * per_thread
    assert counts["total"] == THREADS * per_thread


def test_concurrent_audit_writes_do_not_hit_locked_db(local_db):
    """审计表同样只能有一个写者：并发 ``audit.record`` 不许丢事件。"""
    from core import audit

    per_thread = 5

    def _worker(index: int) -> None:
        for n in range(per_thread):
            audit.record("test.concurrent", actor=f"thread-{index}", detail={"n": n})

    _run_threads(_worker)

    events = audit.list_events(limit=THREADS * per_thread + 10)
    mine = [e for e in events if e["event_type"] == "test.concurrent"]
    assert len(mine) == THREADS * per_thread


def test_concurrent_reads_during_writes_see_consistent_rows(scope_id):
    """读写混合：读者绝不能拿到半截事务（方案第 12 节数据一致性）。"""

    def _worker(index: int) -> None:
        if index % 2 == 0:
            for n in range(4):
                jobs_store.create_job(
                    scope_id=scope_id,
                    targets=[f"mix{index}-{n}.example.test"],
                    tools=["subfinder"],
                )
        else:
            for _ in range(6):
                # 读路径：列表 + 计数 + 单条详情；任一环节看到不一致都会抛错。
                jobs_store.list_jobs(limit=100)
                jobs_store.queue_counts()
                listed = jobs_store.list_jobs(limit=5)
                for row in listed:
                    assert jobs_store.get_job(row["id"]) is not None

    _run_threads(_worker)

    counts = jobs_store.queue_counts()
    assert counts["total"] == (THREADS // 2) * 4
    for status in jobs_store.ALL_STATUSES:
        assert counts[status] >= 0


# ── 3. 并发认领：不重不漏（duplicate execution） ──────────


def test_concurrent_claim_takes_each_job_exactly_once(scope_id):
    """8 个 worker 抢 24 个任务：每个任务只被领到一次，且一个都不剩。

    这是「单表即队列」最核心的断言。方案第 7 节要求「同一个任务只被一个
    worker 领到」，若 ``BEGIN IMMEDIATE`` + ``WHERE status='queued'`` 失效，
    这里会看到重复 job id（任务被跑两遍）。
    """
    job_count = 24
    for n in range(job_count):
        jobs_store.create_job(
            scope_id=scope_id,
            targets=[f"claim{n}.example.test"],
            tools=["subfinder"],
        )

    def _worker(index: int) -> list[str]:
        worker_id = f"worker-{index}"
        claimed: list[str] = []
        while True:
            job = jobs_store.claim_next_job(worker_id, lease_seconds=60)
            if job is None:
                return claimed
            # 领到的一定是 running，且归属自己。
            assert job["status"] == jobs_store.STATUS_RUNNING
            assert job["worker_id"] == worker_id
            assert job["lease_until"] is not None
            claimed.append(job["id"])

    batches = _run_threads(_worker)
    all_claimed = [job_id for batch in batches for job_id in batch]

    assert len(all_claimed) == job_count, "有任务没被领到（凭空消失）"
    assert len(set(all_claimed)) == job_count, "有任务被多个 worker 领到（重复执行）"

    counts = jobs_store.queue_counts()
    assert counts[jobs_store.STATUS_RUNNING] == job_count
    assert counts[jobs_store.STATUS_QUEUED] == 0


def test_concurrent_claim_records_exactly_one_started_event(scope_id):
    """同一个任务只能有一条 ``job.started`` 事件（事件也不许重复）。"""
    job = jobs_store.create_job(
        scope_id=scope_id,
        targets=["once.example.test"],
        tools=["subfinder"],
    )

    def _worker(index: int) -> str | None:
        claimed = jobs_store.claim_next_job(f"worker-{index}", lease_seconds=60)
        return claimed["id"] if claimed else None

    claimed_ids = [item for item in _run_threads(_worker) if item]
    assert claimed_ids == [job["id"]]

    started = [e for e in jobs_store.list_events(job["id"]) if e["event_type"] == jobs_store.EVENT_JOB_STARTED]
    assert len(started) == 1


def test_claim_does_not_double_claim_after_huge_contention(local_db):
    """没有任务时，8 个 worker 一起抢必须都干净地拿到 ``None``。"""

    def _worker(index: int) -> bool:
        for _ in range(3):
            assert jobs_store.claim_next_job(f"worker-{index}") is None
        return True

    assert all(_run_threads(_worker))


# ── 4. 并发下的幂等键（P0-7a） ────────────────────────────


def test_concurrent_same_idempotency_key_creates_one_job(scope_id):
    """同一把幂等键被 8 个线程同时使用 → 只创建 1 个任务，7 次命中。

    单线程用例只能证明「查重逻辑存在」；这里才证明「查与插在同一事务内」
    这个前提真的成立（否则会插出 8 条同键任务）。
    """
    key = "concurrent-key-0001"

    def _worker(index: int) -> tuple[str, bool]:
        job, reused = jobs_store.create_job_with_status(
            scope_id=scope_id,
            targets=["idem.example.test"],
            tools=["subfinder"],
            idempotency_key=key,
        )
        return job["id"], reused

    outcomes = _run_threads(_worker)

    job_ids = {job_id for job_id, _ in outcomes}
    assert len(job_ids) == 1, f"同一幂等键创建了多个任务: {job_ids}"
    assert sum(1 for _, reused in outcomes if reused) == THREADS - 1

    counts = jobs_store.queue_counts()
    assert counts[jobs_store.STATUS_QUEUED] == 1

    only_id = job_ids.pop()
    created_events = [
        e for e in jobs_store.list_events(only_id) if e["event_type"] == jobs_store.EVENT_JOB_CREATED
    ]
    assert len(created_events) == 1, "命中幂等键不应再写 job.created"


def test_concurrent_distinct_keys_create_distinct_jobs(scope_id):
    """不同幂等键互不干扰（并发下也不能互相顶掉）。"""

    def _worker(index: int) -> str:
        job, reused = jobs_store.create_job_with_status(
            scope_id=scope_id,
            targets=[f"distinct{index}.example.test"],
            tools=["subfinder"],
            idempotency_key=f"key-{index}",
        )
        assert reused is False
        return job["id"]

    job_ids = _run_threads(_worker)
    assert len(set(job_ids)) == THREADS


# ── 5. 锁竞争本身：等待而不是立刻失败 ─────────────────────


def test_writer_waits_for_lock_instead_of_failing(local_db):
    """持锁线程未释放时，第二个写者应「等」而不是立刻 ``database is locked``。

    构造方式刻意做成确定的（不靠 sleep 猜时机）：

    1. 主线程用 ``BEGIN IMMEDIATE`` 拿住写锁；
    2. 写线程**先置位 Event**、再发起写入 —— Event 置位就说明它马上就撞上锁；
    3. 主线程等到 Event、再确认写线程还没结束（说明它确实被挡住了），
       然后才 ``COMMIT``；
    4. 写线程随后成功写入，且它的耗时里包含了一段真实的等待。

    第 3 步与第 4 步的耗时断言一起，才排除了「写线程根本没撞上锁」这种
    假通过 —— 那种情况下它会立刻结束，``elapsed`` 接近于 0。
    """
    holder = core_db.connect()
    holder.execute("BEGIN IMMEDIATE")
    holder.execute(
        "INSERT INTO audit_events (event_type, actor, target_id, detail_json, created_at) "
        "VALUES ('test.hold', 'holder', NULL, '{}', '2026-01-01T00:00:00Z')"
    )

    about_to_write = threading.Event()
    outcomes: list[str] = []
    errors: list[BaseException] = []
    elapsed: list[float] = []

    def _writer() -> None:
        about_to_write.set()
        started = time.perf_counter()
        try:
            with core_db.transaction() as conn:
                conn.execute(
                    "INSERT INTO audit_events (event_type, actor, target_id, detail_json, created_at) "
                    "VALUES ('test.waited', 'waiter', NULL, '{}', '2026-01-01T00:00:01Z')"
                )
            outcomes.append("ok")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            elapsed.append(time.perf_counter() - started)

    thread = threading.Thread(target=_writer, name="gef-lock-waiter")
    thread.start()
    assert about_to_write.wait(timeout=10), "写线程没能进入写入路径"
    # 等到这里，写线程已经在 BEGIN IMMEDIATE 上等锁：它必须还活着。
    time.sleep(0.3)
    assert thread.is_alive(), "写者没有在锁上等待（说明根本没撞上锁，本用例失去判别力）"

    holder.execute("COMMIT")
    holder.close()
    thread.join(timeout=30)

    assert not thread.is_alive(), "写者在锁释放后仍未结束"
    assert not errors, f"等锁路径抛异常: {errors[0]!r}"
    assert outcomes == ["ok"]
    assert elapsed and elapsed[0] >= 0.25, f"写者没有真的等待过锁: elapsed={elapsed}"


def test_locked_db_without_busy_timeout_would_fail(tmp_path):
    """反证：同一个竞争场景下，**不带** busy_timeout 的裸连接确实会失败。

    这条用例存在的意义是防止上面的用例变成「永远通过」的假断言 ——
    如果某天 SQLite 或封装变了，让裸连接也能无限等锁，这里会立刻红。
    """
    path = str(tmp_path / "bare.db")
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("CREATE TABLE t (id INTEGER)")
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO t (id) VALUES (1)")

    bare = sqlite3.connect(path, timeout=0, isolation_level=None)
    try:
        with pytest.raises(sqlite3.OperationalError):
            bare.execute("INSERT INTO t (id) VALUES (2)")
    finally:
        bare.close()
        holder.execute("ROLLBACK")
        holder.close()
