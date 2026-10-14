"""端到端：被安全验证阻断的 enscan 步骤，在**页面拿到的数据**里必须看得见。

上面的 ``test_enscan_source_blocked.py`` 钉的是 Runner 层的状态映射；这一份
往上再走一层，把**真的** ``ENScanRunner`` 塞进**真的**任务执行器里，验证：

* ``job_steps`` 行落库的 ``status`` / ``error_code`` / ``error_message``；
* 原始 stderr 作为 artifact 落盘（证据留存，且脱敏、无 ANSI 乱码）；
* 任务聚合状态与前端 ``ERROR_CODE_LABELS`` 能否把它渲染成一句人话。

为什么值得单独一层：Runner 层「返回了正确的 error_code」与「页面上真的显示
出来了」是两件事 —— 中间的 ``to_step_outcome()`` / ``finish_step()`` /
``aggregate_status()`` 任何一环把状态吃掉，用户看到的仍然是「成功 / 0 条」。
全程不联网、不调用真实 ENScan：子进程调用被替换成固定的退出码与真实 stderr。
"""

from pathlib import Path

import pytest

from core import jobs as jobs_store
from core.errors import ErrorCode
from jobs.executor import aggregate_status, execute_job

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
BLOCKED_STDERR = (FIXTURES / "enscan_aqc_blocked_stderr.txt").read_text(encoding="utf-8")
CLEAN_STDERR = (FIXTURES / "enscan_no_results_stderr.txt").read_text(encoding="utf-8")


@pytest.fixture
def enscan_job(local_db, tmp_path, monkeypatch):
    """建一个「enscan 单工具、real 模式」的任务，并把 enscan 关进临时目录。

    ``build_runner`` **不**替换成假 Runner —— 要验的就是真 Runner 与真执行器
    之间的接线。真正的隔离点只有一个：``_run_subprocess``。
    """
    from core import scope_store
    from modules import base as base_module

    output_dir = tmp_path / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    # ``BaseRunner.__init__`` 在**实例化时**读模块级 ``OUTPUT_DIR``，所以补丁
    # 要打在 ``modules.base`` 上（``modules.enscan`` 自己没有这个全局量）。
    monkeypatch.setattr(base_module, "OUTPUT_DIR", str(output_dir), raising=False)
    # 双开关（环境 + Scope.active_scan）都要开着，否则拦下步骤的是执行期复检，
    # 那样这条用例验的就不是「数据源受阻」了。
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")

    scope_id = scope_store.create(
        name="enscan 阻断端到端范围", allowed_domains=["example.test"], active_scan=True
    ).id
    job = jobs_store.create_job(
        scope_id=scope_id, targets=["example.test"], tools=["enscan"], mode="real"
    )
    jobs_store.claim_next_job("w1", lease_seconds=300)
    return job


def _stub_subprocess(monkeypatch, *, returncode=0, stderr=""):
    """把 enscan 的子进程调用换成固定结果（唯一被替换的边界）。"""
    from modules.enscan import ENScanRunner

    def fake(self, cmd, timeout, cwd=None):
        return returncode, "", stderr

    monkeypatch.setattr(ENScanRunner, "_run_subprocess", fake)


def _stub_subprocess_with_timeout(monkeypatch, *, stderr=""):
    """换成「子进程超时被杀」——与那次真实运行的收场方式一致。"""
    import subprocess

    from modules.enscan import ENScanRunner

    def fake(self, cmd, timeout, cwd=None):
        raise subprocess.TimeoutExpired(cmd, timeout, output="", stderr=stderr)

    monkeypatch.setattr(ENScanRunner, "_run_subprocess", fake)


def test_blocked_enscan_step_is_visible_on_the_page(enscan_job, monkeypatch):
    """核心断言：阻断必须一路走到 ``job_steps``，且不能被渲染成「成功」。"""
    _stub_subprocess(monkeypatch, returncode=0, stderr=BLOCKED_STDERR)

    result = execute_job(enscan_job["id"])

    step = jobs_store.list_steps(enscan_job["id"])[0]
    assert step["status"] == jobs_store.STEP_FAILED
    assert step["status"] != jobs_store.STEP_SUCCEEDED
    assert step["error_code"] == ErrorCode.SOURCE_BLOCKED
    assert step["error_code"] != ErrorCode.NO_RESULTS
    assert "安全验证" in step["error_message"]
    assert "爱企查" in step["error_message"]
    assert step["found_count"] == 0
    # 任务整体也要如实反映：这一步失败，不能是 succeeded。
    assert result["status"] != jobs_store.STATUS_SUCCEEDED
    assert result["status"] == jobs_store.STATUS_FAILED
    # 任务级与步骤行必须说同一件事：顶部不能写「未知错误 / 所有步骤均失败」，
    # 而正下方的步骤行写着「爱企查要求安全验证」—— 两句话会互相打架。
    assert result["error_code"] == ErrorCode.SOURCE_BLOCKED
    assert "安全验证" in result["error_message"]
    assert "未知错误" not in result["error_message"]


def test_blocked_enscan_step_keeps_scrubbed_stderr_evidence(enscan_job, monkeypatch):
    """原始证据必须留得下来，且进库的那份没有 ANSI 乱码。"""
    _stub_subprocess(monkeypatch, returncode=0, stderr=BLOCKED_STDERR)

    execute_job(enscan_job["id"])

    step = jobs_store.list_steps(enscan_job["id"])[0]
    assert step["artifact_id"], "原始 stderr 必须落盘为 artifact"
    assert step["stderr_preview"]
    assert "\x1b" not in step["stderr_preview"]
    assert "安全验证" in step["stderr_preview"]
    assert len(step["stderr_preview"]) <= 600


def test_normal_enscan_step_still_succeeds(enscan_job, monkeypatch):
    """对照组：正常零结果仍然是 succeeded + no_results，修复没有误伤它。"""
    _stub_subprocess(monkeypatch, returncode=0, stderr=CLEAN_STDERR)

    result = execute_job(enscan_job["id"])

    step = jobs_store.list_steps(enscan_job["id"])[0]
    assert step["status"] == jobs_store.STEP_SUCCEEDED
    assert step["error_code"] == ErrorCode.NO_RESULTS
    assert result["status"] == jobs_store.STATUS_SUCCEEDED


def test_blocked_enscan_step_is_executed_once(enscan_job, monkeypatch):
    """被阻断时不能自动重跑：一次执行只拉起一个子进程。"""
    from modules.enscan import ENScanRunner

    calls = []

    def fake(self, cmd, timeout, cwd=None):
        calls.append(cmd)
        return 0, "", BLOCKED_STDERR

    monkeypatch.setattr(ENScanRunner, "_run_subprocess", fake)

    execute_job(enscan_job["id"])

    assert len(calls) == 1, f"被阻断后不该反复请求外部数据源，实际调用了 {len(calls)} 次"


def test_blocked_step_feeds_coverage_gap_accounting(enscan_job, monkeypatch):
    """阻断要计入「覆盖不完整」，否则 findings 会把它当成扫干净了。"""
    from core import assets as assets_store
    from core.findings import summarize

    _stub_subprocess(monkeypatch, returncode=0, stderr=BLOCKED_STDERR)
    execute_job(enscan_job["id"])

    summary = summarize(
        assets=assets_store.list_job_assets(enscan_job["id"]),
        observations=[],
        steps=jobs_store.list_steps(enscan_job["id"]),
    )
    codes = [hint["code"] for hint in summary["risk_hints"]["items"]]
    assert "incomplete_coverage" in codes, f"覆盖缺口未计入：{codes}"


def test_plain_timeout_stays_timeout(enscan_job, monkeypatch):
    """普通的超时（没有验证提示）仍然报 ``timeout`` + ``timeout``。"""
    _stub_subprocess_with_timeout(monkeypatch, stderr="工具就是慢\n")

    result = execute_job(enscan_job["id"])

    step = jobs_store.list_steps(enscan_job["id"])[0]
    assert step["status"] == jobs_store.STEP_TIMEOUT
    assert step["error_code"] == ErrorCode.TIMEOUT
    assert result["status"] == jobs_store.STATUS_TIMEOUT
    assert result["error_code"] == ErrorCode.TIMEOUT


def test_real_artifact_scenario_timeout_names_the_blocking_source(enscan_job, monkeypatch):
    """**复刻那次真实运行**：ENScan 每 10 秒重试 AQC 验证，被 120 秒超时掐掉。

    真实库里落的是 ``status='timeout'`` / ``error_code='timeout'`` /
    ``error_message='执行超过 120 秒，已强制终止'`` —— 原因被误报成「工具太慢」。
    现在状态仍如实报超时，但错误码与说明必须点名「安全验证」。
    """
    _stub_subprocess_with_timeout(monkeypatch, stderr=BLOCKED_STDERR)

    result = execute_job(enscan_job["id"])

    step = jobs_store.list_steps(enscan_job["id"])[0]
    assert step["status"] == jobs_store.STEP_TIMEOUT
    assert step["error_code"] == ErrorCode.SOURCE_BLOCKED
    assert "安全验证" in step["error_message"]
    assert "爱企查" in step["error_message"]
    assert result["error_code"] == ErrorCode.SOURCE_BLOCKED
    assert "安全验证" in result["error_message"]


# ── 任务级聚合：不能与步骤行自相矛盾 ───────────────────────


def test_aggregate_all_blocked_reports_source_blocked():
    """全部步骤都受阻时，任务级也报受阻，而不是笼统的「未知错误」。"""
    status, code = aggregate_status(
        ["failed", "failed"],
        step_error_codes=[ErrorCode.SOURCE_BLOCKED, ErrorCode.SOURCE_BLOCKED],
    )
    assert status == jobs_store.STATUS_FAILED
    assert code == ErrorCode.SOURCE_BLOCKED


def test_aggregate_mixed_failures_stay_unknown_error():
    """混着别的失败原因时，``unknown_error`` 才是更准确的概括。"""
    status, code = aggregate_status(
        ["failed", "failed"],
        step_error_codes=[ErrorCode.SOURCE_BLOCKED, ErrorCode.UNKNOWN_ERROR],
    )
    assert status == jobs_store.STATUS_FAILED
    assert code == ErrorCode.UNKNOWN_ERROR


def test_aggregate_without_error_codes_is_unchanged():
    """不传错误码时行为与修复前完全一致（既有调用方不受影响）。"""
    assert aggregate_status(["failed", "failed"]) == (
        jobs_store.STATUS_FAILED,
        ErrorCode.UNKNOWN_ERROR,
    )
    assert aggregate_status(["succeeded", "failed"]) == (
        jobs_store.STATUS_PARTIAL,
        ErrorCode.PARTIAL_SUCCESS,
    )


def test_aggregate_timeout_keeps_timeout_status_but_names_the_cause():
    """全超时**且**全受阻：状态仍是 ``timeout``（进程确实被掐掉了），
    但错误码要点明受阻 —— 否则使用者会去调大超时，而那根本不管用。"""
    status, code = aggregate_status(
        ["timeout", "timeout"],
        step_error_codes=[ErrorCode.SOURCE_BLOCKED, ErrorCode.SOURCE_BLOCKED],
    )
    assert status == jobs_store.STATUS_TIMEOUT
    assert code == ErrorCode.SOURCE_BLOCKED


def test_aggregate_plain_timeout_is_unchanged():
    """对照组：普通的全超时（没有验证提示）仍然是 ``timeout``。"""
    status, code = aggregate_status(
        ["timeout", "timeout"],
        step_error_codes=[ErrorCode.TIMEOUT, ErrorCode.TIMEOUT],
    )
    assert status == jobs_store.STATUS_TIMEOUT
    assert code == ErrorCode.TIMEOUT


def test_aggregate_cancelled_still_wins():
    status, code = aggregate_status(
        ["failed"], cancelled=True, step_error_codes=[ErrorCode.SOURCE_BLOCKED]
    )
    assert status == jobs_store.STATUS_CANCELLED
    assert code is None
