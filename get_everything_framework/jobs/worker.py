"""独立 worker 进程（M3）。

启动方式::

    python -m jobs.worker

职责（方案第 5.3、6.1、M3 验收项）：

* 从 SQLite 队列里原子领取 ``queued`` 任务；
* 执行 ``mock``（默认）或 ``real`` 步骤，逐步骤写进度；
* 定期写心跳文件 ``results/worker_heartbeat``，让 ``/health`` 的
  ``worker`` 字段从 ``missing`` 变成 ``ok``（「Web 正常但 worker 未启动」
  因此可以被区分出来）；
* 启动时把租约过期的 ``running`` 任务标成 ``interrupted``，
  进程被 kill 后任务不会静默消失；
* 收到 Ctrl+C / SIGTERM 时把当前任务放回 ``interrupted`` 并退出。

Web 进程与 worker 进程通过数据库通信，互不阻塞；本机联调版刻意不做
分布式 worker 与消息队列（方案第 1.5、3.5 节）。
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time

# 允许 `python -m jobs.worker` 从项目根直接运行。
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config import OUTPUT_DIR  # noqa: E402
from core import db, jobs as jobs_store  # noqa: E402
from core import observability  # noqa: E402
from jobs.executor import execute_job  # noqa: E402

HEARTBEAT_FILENAME = "worker_heartbeat"
DEFAULT_POLL_SECONDS = 2.0
DEFAULT_HEARTBEAT_SECONDS = 5.0
DEFAULT_IDLE_EXIT_SECONDS = 0.0  # 0 = 一直等下去（本机联调默认行为）

# 运行中标记：收到信号后置 False，主循环跑到当前步骤边界再退出。
_running = True


def _handle_signal(signum, _frame):  # pragma: no cover - 信号路径不易单测
    global _running
    _running = False
    # 信号路径要**先**保证人能看见（日志 handler 可能还没装好），所以保留一行
    # 人读提示，同时记结构化事件（方案第 19 节）。
    print(f"\n[worker] 收到信号 {signum}，准备在当前步骤结束后退出 ...", flush=True)
    observability.log_event(
        observability.EVENT_WORKER_SIGNAL,
        level="WARNING",
        signal=int(signum),
        worker_id=observability.current_context().get("worker_id"),
    )


def heartbeat_path() -> str:
    """心跳文件路径（与 ``/health`` 读取的路径必须一致）。"""
    return os.path.join(OUTPUT_DIR, HEARTBEAT_FILENAME)


def write_heartbeat(worker_id: str | None = None) -> None:
    """刷新心跳文件。

    ``/health`` 只看 mtime，所以内容只是给人排查用的。
    """
    path = heartbeat_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"worker_id={worker_id or ''}\npid={os.getpid()}\nts={time.time()}\n")


def make_worker_id() -> str:
    """本机联调版：``hostname-pid`` 足够区分进程，不做分布式租约。"""
    import socket

    return f"{socket.gethostname()}-{os.getpid()}"


class Worker:
    """单进程 worker。

    参数全部可从命令行覆盖，便于测试用 ``--once`` 跑一轮就退出。
    """

    def __init__(
        self,
        *,
        worker_id: str | None = None,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
        lease_seconds: int = jobs_store.DEFAULT_LEASE_SECONDS,
        idle_exit_seconds: float = DEFAULT_IDLE_EXIT_SECONDS,
        step_delay: float = 0.0,
        recover_on_start: bool = True,
        verbose: bool = True,
    ):
        self.worker_id = worker_id or make_worker_id()
        self.poll_seconds = max(0.05, float(poll_seconds))
        self.heartbeat_seconds = max(0.5, float(heartbeat_seconds))
        self.lease_seconds = int(lease_seconds)
        self.idle_exit_seconds = float(idle_exit_seconds)
        self.step_delay = max(0.0, float(step_delay))
        self.recover_on_start = recover_on_start
        self.verbose = verbose

        self._last_heartbeat = 0.0
        self._idle_since: float | None = None
        self._current_job_id: str | None = None
        # 方案第 19 节：startup() 时绑定的 worker_id 上下文 token，shutdown() 还原。
        self._context_token: observability.ContextToken | None = None

    # ── 日志 ─────────────────────────────────────────────

    def log(self, message: str) -> None:
        """人读进度提示（控制台）。

        与方案第 19 节的结构化日志并存：控制台保留这一行方便本机盯屏，
        同时以 ``worker_message`` 事件进结构化日志（带 worker_id 上下文）。
        内容**不含目标列表**，只有任务 ID / 状态这类可公开信息。
        """
        if self.verbose:
            print(f"[worker {self.worker_id}] {message}", flush=True)
        observability.log_event(
            observability.EVENT_WORKER_MESSAGE,
            level="DEBUG",
            worker_id=self.worker_id,
            message=message,
        )

    # ── 生命周期 ─────────────────────────────────────────

    def __enter__(self) -> "Worker":
        """作为上下文管理器使用：进入即 ``startup()``。

        ``with Worker(...) as worker:`` 保证退出时一定走 ``shutdown()``，
        从而精确还原 worker_id 上下文（否则同一线程里再跑别的活会继承一个
        已经死掉的 worker 身份）。
        """
        self.startup()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.shutdown()

    def startup(self) -> list[str]:
        """启动准备：建表、写心跳、恢复过期任务。"""
        db.ensure_schema()
        write_heartbeat(self.worker_id)
        # 方案第 19 节：worker_id 绑到当前执行流，之后所有事件自动带上它。
        # 绑定只覆盖本 worker 的生命周期，``shutdown()`` 里精确还原 ——
        # 否则同一线程里跑第二个 worker（或进程内嵌用法）会串号。
        observability.reset_context("worker_id", self._context_token)
        self._context_token = observability.set_context("worker_id", self.worker_id)
        recovered: list[str] = []
        if self.recover_on_start:
            recovered = jobs_store.recover_stale_jobs()
            for job_id in recovered:
                self.log(f"恢复中断任务 {job_id} → interrupted")
        self.log(f"启动完成，数据库={db.db_path()}")
        observability.log_event(
            observability.EVENT_WORKER_STARTED,
            worker_id=self.worker_id,
            recovered=len(recovered),
            lease_seconds=self.lease_seconds,
        )
        return recovered

    def tick(self) -> dict | None:
        """跑一轮：能领到任务就执行完，否则返回 ``None``。"""
        self._maybe_heartbeat()

        job = jobs_store.claim_next_job(self.worker_id, self.lease_seconds)
        if job is None:
            return None

        self._current_job_id = job["id"]
        self.log(f"领取任务 {job['id']}（{job['mode']}，{job['total_steps']} 步）")
        observability.log_event(
            observability.EVENT_WORKER_CLAIMED_JOB,
            worker_id=self.worker_id,
            job_id=job["id"],
            mode=job["mode"],
            total_steps=job["total_steps"],
        )
        try:
            # 任务级的 job_id 上下文：任务里每个步骤事件都会带上它。
            with observability.bind(job_id=job["id"]):
                started = time.perf_counter()
                result = execute_job(job["id"], renew=self._renew_lease, step_delay=self.step_delay)
            self.log(f"任务 {job['id']} 结束：{result['status']}")
            observability.log_event(
                observability.EVENT_WORKER_JOB_FINISHED,
                level="INFO" if result["status"] == jobs_store.STATUS_SUCCEEDED else "WARNING",
                worker_id=self.worker_id,
                job_id=job["id"],
                status=result["status"],
                error_code=result.get("error_code"),
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            return result
        finally:
            self._current_job_id = None

    def run(self) -> int:
        """主循环。返回进程退出码。"""
        self.startup()
        self._idle_since = time.time()

        while _running:
            finished_any = False
            try:
                result = self.tick()
                finished_any = result is not None
            except Exception as exc:  # noqa: BLE001 - worker 不能因为单个任务崩掉
                self.log(f"任务执行异常：{exc!r}")
                observability.log_event(
                    observability.EVENT_WORKER_JOB_EXCEPTION,
                    level="ERROR",
                    worker_id=self.worker_id,
                    job_id=self._current_job_id,
                    error=repr(exc),
                )
                if self._current_job_id:
                    jobs_store.finish_job(
                        self._current_job_id,
                        status=jobs_store.STATUS_FAILED,
                        error_code="unknown_error",
                        error_message=str(exc),
                    )
                    self._current_job_id = None

            if finished_any:
                self._idle_since = None
            else:
                self._idle_since = self._idle_since or time.time()
                if self.idle_exit_seconds and time.time() - self._idle_since >= self.idle_exit_seconds:
                    self.log("空闲超时，退出")
                    observability.log_event(
                        observability.EVENT_WORKER_IDLE_EXIT,
                        worker_id=self.worker_id,
                        idle_seconds=round(time.time() - self._idle_since, 3),
                    )
                    break
                # 没有任务时也要能及时响应 Ctrl+C。
                _sleep(self.poll_seconds)

        self.shutdown()
        return 0

    def shutdown(self) -> None:
        """退出前处理：把正在跑的任务放回 interrupted，写最后一次心跳。"""
        if self._current_job_id:
            self.log(f"退出前把 {self._current_job_id} 标记为 interrupted")
            jobs_store.release_job(self._current_job_id, self.worker_id)
            self._current_job_id = None
        write_heartbeat(self.worker_id)
        self.log("已退出")
        observability.log_event(observability.EVENT_WORKER_SHUTDOWN, worker_id=self.worker_id)
        # 退出时把 worker_id 上下文还原，避免同一线程里的后续代码（或测试）
        # 继承一个已经死掉的 worker 身份。
        observability.reset_context("worker_id", self._context_token)
        self._context_token = None

    # ── 内部 ─────────────────────────────────────────────

    def _maybe_heartbeat(self) -> None:
        now = time.time()
        if now - self._last_heartbeat >= self.heartbeat_seconds:
            write_heartbeat(self.worker_id)
            self._last_heartbeat = now

    def _renew_lease(self) -> None:
        """每个步骤结束后续租，证明 worker 还活着。"""
        if self._current_job_id:
            jobs_store.renew_lease(self._current_job_id, self.worker_id, self.lease_seconds)
        self._maybe_heartbeat()


def _sleep(seconds: float) -> None:
    """可被信号打断的 sleep。"""
    deadline = time.time() + seconds
    while _running and time.time() < deadline:
        time.sleep(min(0.2, max(0.0, deadline - time.time())))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m jobs.worker",
        description="Get Everything Framework 本机联调版异步任务 worker",
    )
    parser.add_argument("--worker-id", default=None, help="自定义 worker 标识（默认 hostname-pid）")
    parser.add_argument("--poll", type=float, default=DEFAULT_POLL_SECONDS, help="空闲轮询间隔（秒）")
    parser.add_argument("--heartbeat", type=float, default=DEFAULT_HEARTBEAT_SECONDS, help="心跳间隔（秒）")
    parser.add_argument("--lease", type=int, default=jobs_store.DEFAULT_LEASE_SECONDS, help="任务租约（秒）")
    parser.add_argument(
        "--idle-exit",
        type=float,
        default=DEFAULT_IDLE_EXIT_SECONDS,
        help="空闲多少秒后自动退出（0 表示不退出，默认 0）",
    )
    parser.add_argument("--once", action="store_true", help="只跑一轮就退出（便于验收与测试）")
    parser.add_argument(
        "--step-delay",
        type=float,
        default=0.0,
        help="每个步骤之间人为等待的秒数（仅用于验收多步任务的进度与取消）",
    )
    parser.add_argument("--quiet", action="store_true", help="不打印进度日志")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)

    worker = Worker(
        worker_id=args.worker_id,
        poll_seconds=args.poll,
        heartbeat_seconds=args.heartbeat,
        lease_seconds=args.lease,
        idle_exit_seconds=args.idle_exit,
        step_delay=args.step_delay,
        verbose=not args.quiet,
    )

    if args.once:
        # ``--once`` 也必须成对 startup/shutdown：方案第 19 节的 worker_id
        # 上下文在 shutdown() 里还原，否则会泄漏给同一线程里的后续代码。
        with worker:
            worker.tick()
        return 0
    return worker.run()


if __name__ == "__main__":  # pragma: no cover - 进程入口
    # 方案第 19 节：worker 进程同样输出结构化单行日志（级别 / 格式取自
    # GEF_LOG_LEVEL / GEF_LOG_FORMAT）。控制台的人读进度行由 --quiet 控制。
    observability.configure_logging()
    raise SystemExit(main())
