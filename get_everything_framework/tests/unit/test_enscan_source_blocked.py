"""enscan 被外部数据源「安全验证」阻断时的状态映射（六种情形）。

**要修的 bug**：ENScan 的 AQC 渠道要求安全验证时，它一边往 stderr 打

    [ERR] 【AQC】需要安全验证，请打开浏览器进行验证后操作，10秒后重试！
    https://aiqicha.baidu.com/

一边每 10 秒重试一次，**永远不自己退出**。框架这一侧的表现是：

* 进程被 120 秒超时掐掉 → 只报「执行超过 120 秒」，把人引向「工具太慢」；
* 若它恰好以退出码 0 收场，更糟 —— 走到 ``_record_execution`` 的默认
  ``status="success"``，被 ``RunnerResult.ok([])`` 翻成 ``no_results``，
  页面上显示「执行成功但零结果」，与「目标真的没有资产」**完全无法区分**。

因此本文件逐条钉死：**被阻断 ≠ 零结果**，且阻断不会被反复重试。

用例里的 stderr 取自 ``results/artifacts/`` 里那次真实运行留下的原始证据
（``art_d01bf98894b84c1681a79e55d4f4aa24.err``），**逐字保留** ANSI 颜色
转义，只把真实域名换成 ``example.test``。不联网、不调用真实工具。
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from core.errors import ErrorCode
from core.runner_result import STATUS_FAILED, STATUS_SUCCESS, STATUS_TIMEOUT

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

#: 真实运行里被安全验证阻断的 stderr（1 行 INF + 12 行 ERR，含 ANSI 转义）。
BLOCKED_STDERR = (FIXTURES / "enscan_aqc_blocked_stderr.txt").read_text(encoding="utf-8")

#: 对照：跑通了、确实零结果 —— 只有 INF，没有任何 ERR。
CLEAN_STDERR = (FIXTURES / "enscan_no_results_stderr.txt").read_text(encoding="utf-8")


@pytest.fixture
def enscan(tmp_path, monkeypatch):
    """把 enscan 的输出目录指到临时目录，并返回 runner 工厂。

    enscan 把 JSON 写在自己的**工作目录**下，所以这里必须同时管住
    ``output_dir`` 与传给 ``_run_subprocess`` 的 ``cwd``。
    """
    target = tmp_path / "results"
    target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("modules.base.OUTPUT_DIR", str(target), raising=False)

    from modules.enscan import ENScanRunner

    def factory():
        runner = ENScanRunner()
        runner.output_dir = str(target)
        # 真实运行走的是 PATH 上的 ENScan.exe；这里只关心状态映射。
        monkeypatch.setattr(runner, "_resolve_command", lambda cmd: cmd)
        return runner

    return factory


def _fake_run(monkeypatch, runner, *, returncode=0, stdout="", stderr="", exc=None, writes=None):
    """替换 ``_run_subprocess``，产出固定的退出码 / 输出 / 异常。

    Args:
        exc: 给定时抛出它（模拟超时等异常路径）。
        writes: ``(cwd) -> None``；在「子进程运行期间」往它的工作目录写文件，
            用来模拟 enscan 自己落盘 JSON。必须发生在调用**中间**，
            因为 ``run_scan`` 是拿执行前后的文件差集来判断新产物的。
    """
    calls = []

    def fake(cmd, timeout, cwd=None):
        calls.append({"cmd": cmd, "timeout": timeout, "cwd": cwd})
        if writes is not None:
            writes(cwd)
        if exc is not None:
            raise exc
        return returncode, stdout, stderr

    monkeypatch.setattr(runner, "_run_subprocess", fake)
    return calls


def _enscan_writer(payload):
    """返回一个「在 cwd 下写 enscan JSON」的回调。"""

    def write(cwd):
        path = os.path.join(str(cwd), "enscan_out.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)

    return write


# ── 情形 1：正常查询、有结果 ───────────────────────────────


def test_normal_query_with_results_stays_success(enscan, monkeypatch):
    runner = enscan()
    _fake_run(
        monkeypatch,
        runner,
        stderr=CLEAN_STDERR,
        writes=_enscan_writer({"icp": [{"domain": "Found.Example.COM"}]}),
    )

    result = runner.run("某某科技有限公司")

    assert result.status == STATUS_SUCCESS
    assert result.error_code is None
    assert result.found_count == 1
    assert "found.example.com" in result.values
    assert not result.is_failure
    assert result.to_step_outcome()["step_status"] == "succeeded"


# ── 情形 2：正常执行、确实没有结果 ─────────────────────────


def test_normal_execution_without_results_is_no_results(enscan, monkeypatch):
    """跑通了、确实零结果 —— 这条路径**必须保持不变**。"""
    runner = enscan()
    _fake_run(monkeypatch, runner, stderr=CLEAN_STDERR)

    result = runner.run("某某科技有限公司")

    assert result.status == STATUS_SUCCESS
    assert result.error_code == ErrorCode.NO_RESULTS
    assert result.found_count == 0
    assert not result.is_failure
    assert result.to_step_outcome()["step_status"] == "succeeded"


# ── 情形 3：AQC 要求安全验证 ───────────────────────────────


def test_aqc_security_verification_is_not_no_results(enscan, monkeypatch):
    """退出码 0 + 验证提示：**绝不能**报成「成功但零结果」。"""
    runner = enscan()
    _fake_run(monkeypatch, runner, returncode=0, stderr=BLOCKED_STDERR)

    result = runner.run("某某科技有限公司")

    assert result.status == STATUS_FAILED
    assert result.error_code == ErrorCode.SOURCE_BLOCKED
    assert result.error_code != ErrorCode.NO_RESULTS
    assert result.is_failure
    assert result.found_count == 0
    assert "安全验证" in result.error_message
    assert "爱企查" in result.error_message
    assert result.to_step_outcome()["step_status"] == "failed"


def test_blocked_source_does_not_return_partial_data(enscan, monkeypatch):
    """被阻断时即使子进程写出了 JSON，也不许当成「查到的资产」上交。"""
    runner = enscan()
    _fake_run(
        monkeypatch,
        runner,
        stderr=BLOCKED_STDERR,
        writes=_enscan_writer({"icp": [{"domain": "leaked.example.com"}]}),
    )

    assert runner.run_scan("某某科技有限公司") == []

    result = runner.run("某某科技有限公司")
    assert result.error_code == ErrorCode.SOURCE_BLOCKED
    assert result.found_count == 0
    assert "leaked.example.com" not in result.values


def test_blocked_message_is_specific_to_the_blocking_source(enscan, monkeypatch):
    """没点名具体渠道时用通用文案，不要硬说成爱企查。"""
    runner = enscan()
    _fake_run(monkeypatch, runner, stderr="[ERR] 目标站点要求安全验证，请完成验证后重试\n")

    result = runner.run("某某科技有限公司")

    assert result.error_code == ErrorCode.SOURCE_BLOCKED
    assert "安全验证" in result.error_message
    assert "爱企查" not in result.error_message


# ── 情形 4：非零退出码 ─────────────────────────────────────


def test_nonzero_exit_without_verification_is_still_a_generic_failure(enscan, monkeypatch):
    runner = enscan()
    _fake_run(monkeypatch, runner, returncode=1, stderr="some other error\n")

    result = runner.run("某某科技有限公司")

    assert result.status == STATUS_FAILED
    assert result.error_code == ErrorCode.UNKNOWN_ERROR
    assert result.exit_code == 1
    assert result.is_failure
    assert result.to_step_outcome()["step_status"] == "failed"


def test_nonzero_exit_with_verification_reports_source_blocked(enscan, monkeypatch):
    """被验证拦下时 ENScan 也可能以非零码收场，别报成「未知错误」。"""
    runner = enscan()
    _fake_run(monkeypatch, runner, returncode=1, stderr=BLOCKED_STDERR)

    result = runner.run("某某科技有限公司")

    assert result.status == STATUS_FAILED
    assert result.error_code == ErrorCode.SOURCE_BLOCKED
    assert result.exit_code == 1
    assert result.is_failure
    assert "安全验证" in result.error_message


# ── 情形 5：超时 / 网络错误 ────────────────────────────────


def test_plain_timeout_stays_timeout(enscan, monkeypatch):
    runner = enscan()
    exc = subprocess.TimeoutExpired(["enscan"], 120, output="", stderr="no output at all\n")
    _fake_run(monkeypatch, runner, exc=exc)

    result = runner.run("某某科技有限公司")

    assert result.status == STATUS_TIMEOUT
    assert result.error_code == ErrorCode.TIMEOUT
    assert result.is_failure
    assert result.to_step_outcome()["step_status"] == "timeout"


def test_timeout_caused_by_verification_is_reported_as_blocked(enscan, monkeypatch):
    """真实运行就是这一条：进程被验证重试拖到超时，原因必须如实点名。"""
    runner = enscan()
    exc = subprocess.TimeoutExpired(["enscan"], 120, output="", stderr=BLOCKED_STDERR)
    _fake_run(monkeypatch, runner, exc=exc)

    result = runner.run("某某科技有限公司")

    assert result.status == STATUS_TIMEOUT
    assert result.error_code == ErrorCode.SOURCE_BLOCKED
    assert result.error_code != ErrorCode.TIMEOUT
    assert result.is_failure
    assert "安全验证" in result.error_message
    # 仍然如实报「超时」，因为进程确实是被我们掐掉的。
    assert result.to_step_outcome()["step_status"] == "timeout"


def test_missing_binary_is_tool_not_found(enscan, monkeypatch):
    """环境类失败的对照组：工具没装仍然是 tool_not_found。"""
    runner = enscan()
    _fake_run(monkeypatch, runner, exc=FileNotFoundError("ENScan.exe"))

    result = runner.run("某某科技有限公司")

    assert result.status == STATUS_FAILED
    assert result.error_code == ErrorCode.TOOL_NOT_FOUND
    assert result.is_failure


# ── 情形 6：不反复重试、不假装成功 ─────────────────────────


def test_blocked_source_is_not_retried(enscan, monkeypatch):
    """一次 ``run()`` 只能拉起**一个**子进程：被阻断后不得自动重试。

    这条直接对应「不要反复自动重试」：框架多试一次，ENScan 就会再向
    爱企查发一轮请求，而验证不会因为多问几次就自己通过。
    """
    runner = enscan()
    calls = _fake_run(monkeypatch, runner, returncode=0, stderr=BLOCKED_STDERR)

    runner.run("某某科技有限公司")

    assert len(calls) == 1


def test_blocked_source_never_maps_to_success(enscan, monkeypatch):
    """把「阻断」与「零结果」并排放在一起，钉死两者必须可区分。"""
    blocked = enscan()
    _fake_run(monkeypatch, blocked, stderr=BLOCKED_STDERR)
    blocked_outcome = blocked.run("某某科技有限公司").to_step_outcome()

    empty = enscan()
    _fake_run(monkeypatch, empty, stderr=CLEAN_STDERR)
    empty_outcome = empty.run("某某科技有限公司").to_step_outcome()

    assert blocked_outcome["error_code"] == ErrorCode.SOURCE_BLOCKED
    assert empty_outcome["error_code"] == ErrorCode.NO_RESULTS
    assert blocked_outcome["error_code"] != empty_outcome["error_code"]
    assert blocked_outcome["step_status"] == "failed"
    assert empty_outcome["step_status"] == "succeeded"
    assert blocked_outcome["found_count"] == empty_outcome["found_count"] == 0


def test_blocked_diagnostics_are_scrubbed_and_bounded(enscan, monkeypatch):
    """诊断信息要能进库：必须脱敏、必须有长度上限、且不能带 ANSI 乱码。"""
    runner = enscan()
    secret = "AQCSECRETTOKEN0123456789abcdef"
    _fake_run(monkeypatch, runner, stderr=BLOCKED_STDERR + f"\nCookie: {secret}\n")

    result = runner.run("某某科技有限公司")

    assert result.error_code == ErrorCode.SOURCE_BLOCKED
    preview = result.to_step_outcome()["stderr_preview"]
    assert preview
    assert secret not in preview
    assert "\x1b" not in preview, "ANSI 转义必须剥掉，否则页面显示成乱码"
    assert len(preview) <= 600


# ── 错误码与前端标签必须同时存在 ───────────────────────────


def test_source_blocked_is_declared_and_labelled():
    """新增错误码必须同时出现在常量表与前端标签表里（同 config_error 的守护）。"""
    assert ErrorCode.SOURCE_BLOCKED == "source_blocked"
    assert "source_blocked" in ErrorCode.ALL

    app_js = Path(__file__).resolve().parents[2] / "web" / "static" / "app.js"
    assert "source_blocked" in app_js.read_text(encoding="utf-8")
