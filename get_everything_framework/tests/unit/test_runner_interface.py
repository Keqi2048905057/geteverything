"""M4 单元测试：runner 统一接口（``BaseRunner.run``）。

这是方案 M4 的核心验收面 —— 旧实现里「工具没装 / 超时 / 零结果 / 解析失败」
全都变成 ``return []``，调用方无法区分。本文件用**真实子进程**（把
``sys.executable`` 当成外部工具）逐一验证这些情况现在能分开：

* 可执行文件不存在 → ``tool_not_found``；
* 子进程超时 → ``timeout``；
* 退出码非零 → ``unknown_error``（或 ``permission_denied`` / ``tool_not_found``）；
* 跑通但没有任何输出 → ``no_results``；
* 跑通且有输出 → ``success``、``error_code is None``。

全部离线、秒级、不访问任何外部目标（AGENTS.md 硬约束）。
"""

import os
import sys
import time

import pytest

from core.errors import ErrorCode
from modules.base import BaseRunner


class _PythonToolRunner(BaseRunner):
    """把 ``python -c <code>`` 当作外部工具的最小 runner。

    用解释器而不是 .bat/.sh，保证 Windows 与 Linux 上行为一致，
    且完全不依赖机器上装了哪个安全工具。
    """

    def __init__(self, code, *, tool_name="pytool", config=None, suffix=".txt"):
        base_config = {
            "path": sys.executable,
            "category": "subdomain",
            "process_timeout": 30,
            "extra_args": [],
        }
        base_config.update(config or {})
        super().__init__(base_config, tool_name)
        self.code = code
        self.suffix = suffix

    def _output_file(self, domain):
        return os.path.join(self.output_dir, f"m4_{self.tool_name}_{domain}{self.suffix}")

    def build_command(self, domain, options=None):
        options = options or {}
        output_file = options.get("output_file") or self._output_file(domain)
        # 用各工具通用的 ``-o <file>`` 惯例，这样基类的
        # ``declared_output_file`` / ``_clear_stale_output`` 走的是真实路径解析。
        # extra_args 放前面：命令预览有 300 字符上限，放最后会被截掉。
        cmd = [self.config["path"]]
        cmd.extend(self.config.get("extra_args", []))
        cmd.extend(["-c", self.code, "-o", output_file])
        return cmd

    def run_scan(self, domain):
        output_file = self._output_file(domain)
        cmd = self.build_command(domain, {"output_file": output_file})
        if not self._execute(cmd, domain):
            return []
        return self._read_results(output_file)


@pytest.fixture
def output_dir(tmp_path, monkeypatch):
    """把 runner 的输出目录指到临时目录（绝不写仓库 results/）。"""
    target = str(tmp_path / "results")
    os.makedirs(target, exist_ok=True)
    monkeypatch.setattr("modules.base.OUTPUT_DIR", target, raising=False)
    return target


# 子进程侧代码：统一从 ``-o`` 参数取输出文件位置。
_WRITE_TWO_LINES = (
    "import pathlib,sys;a=sys.argv;o=a[a.index('-o')+1];"
    "pathlib.Path(o).write_text('a.example.test\\nb.example.test\\n', encoding='utf-8')"
)
_WRITE_NOTHING = "import pathlib,sys;a=sys.argv;o=a[a.index('-o')+1];pathlib.Path(o).write_text('', encoding='utf-8')"
_EXIT_NONZERO = "import sys;sys.exit(2)"
_EXIT_127 = "import sys;sys.exit(127)"
_SLEEP = "import time;time.sleep(5)"


# ── 跑通且有结果 ──────────────────────────────────────────


def test_run_success_has_values_and_no_error(output_dir):
    runner = _PythonToolRunner(_WRITE_TWO_LINES)
    result = runner.run("example.test")

    assert result.status == "success"
    assert result.error_code is None
    assert result.values == ["a.example.test", "b.example.test"]
    assert result.found_count == 2
    assert result.exit_code == 0
    assert result.duration_ms is not None and result.duration_ms >= 0
    assert not result.is_failure


def test_run_builds_observations(output_dir):
    runner = _PythonToolRunner(_WRITE_TWO_LINES)
    result = runner.run("example.test")
    assert [item.category for item in result.data] == ["subdomain", "subdomain"]
    assert all(item.source_tool == "pytool" for item in result.data)


def test_run_command_preview_is_redacted(output_dir):
    runner = _PythonToolRunner(_WRITE_TWO_LINES, config={"extra_args": ["-api-key", "SECRETVALUE1234"]})
    result = runner.run("example.test")
    preview = result.command_preview or ""
    assert "SECRETVALUE1234" not in preview
    assert "***" in preview
    # 长目录名不能被误打码，否则预览失去排查价值。
    assert "results" in preview


# ── 跑通但零结果（M4 最关键的一条） ────────────────────────


def test_run_empty_output_is_no_results_not_failure(output_dir):
    """工具跑通、退出码 0、什么都没发现 —— 与「失败」必须分开。"""
    runner = _PythonToolRunner(_WRITE_NOTHING)
    result = runner.run("example.test")

    assert result.status == "success"
    assert result.error_code == ErrorCode.NO_RESULTS
    assert result.found_count == 0
    assert result.exit_code == 0
    assert not result.is_failure
    assert result.error_message


def test_run_missing_output_file_is_no_results(output_dir):
    """工具压根没产出文件（很多工具的正常表现）同样算零结果，不是失败。"""
    runner = _PythonToolRunner("pass")
    result = runner.run("example.test")

    assert result.status == "success"
    assert result.error_code == ErrorCode.NO_RESULTS
    assert result.exit_code == 0


# ── 失败与超时 ────────────────────────────────────────────


def test_run_missing_binary_is_tool_not_found(output_dir):
    runner = _PythonToolRunner(_WRITE_TWO_LINES, config={"path": "definitely-not-installed-xyz"})
    result = runner.run("example.test")

    assert result.status == "failed"
    assert result.error_code == ErrorCode.TOOL_NOT_FOUND
    assert result.exit_code is None
    assert result.is_failure
    # 旧代码在这里会返回 []，M4 必须给出可判定的错误码。
    assert result.values == []


def test_run_nonzero_exit_is_unknown_error(output_dir):
    runner = _PythonToolRunner(_EXIT_NONZERO)
    result = runner.run("example.test")

    assert result.status == "failed"
    assert result.error_code == ErrorCode.UNKNOWN_ERROR
    assert result.exit_code == 2
    assert result.is_failure


def test_run_exit_127_maps_to_tool_not_found(output_dir):
    """POSIX 惯例：127 = command not found。"""
    runner = _PythonToolRunner(_EXIT_127)
    result = runner.run("example.test")
    assert result.error_code == ErrorCode.TOOL_NOT_FOUND
    assert result.exit_code == 127


def test_run_timeout_is_timeout_status(output_dir):
    runner = _PythonToolRunner(_SLEEP, config={"process_timeout": 1})
    result = runner.run("example.test")

    assert result.status == "timeout"
    assert result.error_code == ErrorCode.TIMEOUT
    assert result.is_failure
    assert "1" in (result.error_message or "")


def test_timeout_is_capped_by_scan_limits(output_dir, monkeypatch):
    """单个工具的 process_timeout 不能超过 SCAN_LIMITS 的本机上限。

    历史实现里配置写死 300 秒，而 ``SCAN_LIMITS["process_timeout"]=120``
    从未生效 —— 一条卡死的命令能把 worker 占住五分钟。
    """
    import config
    import modules.base as base_module

    monkeypatch.setitem(config.SCAN_LIMITS, "process_timeout", 5)
    runner = _PythonToolRunner(_WRITE_TWO_LINES, config={"process_timeout": 300})
    assert runner._timeout_seconds() == 5

    # 工具自己的超时更短时，用工具自己的。
    shorter = _PythonToolRunner(_WRITE_TWO_LINES, config={"process_timeout": 2})
    assert shorter._timeout_seconds() == 2

    # 没配时用本机上限。
    unset = _PythonToolRunner(_WRITE_TWO_LINES, config={"process_timeout": None})
    assert unset._timeout_seconds() == 5
    assert base_module.SCAN_LIMITS is config.SCAN_LIMITS


def test_run_never_returns_bare_empty_list_on_failure(output_dir):
    """方案第 8.2 节：禁止失败返回 ``[]``。"""
    for code in (_EXIT_NONZERO, _SLEEP):
        config = {"process_timeout": 1}
        result = _PythonToolRunner(code, config=config).run("example.test")
        assert result.values == []
        assert result.error_code, "失败必须带 error_code"
        assert result.status != "success"


# ── runner 抛异常与前置校验 ────────────────────────────────


def test_run_catches_exception_raised_by_run_scan(output_dir):
    class _Boom(_PythonToolRunner):
        def run_scan(self, domain):
            raise RuntimeError("炸了")

    result = _Boom(_WRITE_TWO_LINES).run("example.test")
    assert result.status == "failed"
    assert result.error_code == ErrorCode.UNKNOWN_ERROR
    assert "炸了" in (result.error_message or "")


def test_run_catches_system_exit(output_dir):
    """``SystemExit`` 是 BaseException，必须被接住，否则会带走 worker 进程。"""

    class _Exit(_PythonToolRunner):
        def run_scan(self, domain):
            raise SystemExit(4)

    result = _Exit(_WRITE_TWO_LINES).run("example.test")
    assert result.exit_code == 4
    assert result.status == "failed"


def test_health_check_reports_missing_binary(output_dir):
    runner = _PythonToolRunner(_WRITE_TWO_LINES, config={"path": "definitely-not-installed-xyz"})
    health = runner.health_check()
    assert health.available is False
    assert health.error_code == ErrorCode.TOOL_NOT_FOUND


def test_health_check_accepts_python(output_dir):
    runner = _PythonToolRunner(_WRITE_TWO_LINES)
    health = runner.health_check()
    assert health.available is True
    assert health.path


def test_health_check_does_not_execute(output_dir):
    """健康检查必须是只读探测，不能真的把工具跑起来。"""
    runner = _PythonToolRunner(_EXIT_127)
    assert runner.health_check().available is True
    assert runner.last_execution == {}


# ── 残留输出文件的根治（docs/CODEBASE_MAP.md §7.2） ────────


def test_run_removes_stale_output_file_before_execution(output_dir):
    """上一次扫描留下的同名文件不能被当成本次结果读回来。

    旧实现里 ``md5(domain)[:12]_<tool>.txt`` 从不清理，于是「工具失败」
    会被伪装成「跑通了、有数据」——这是项目里最贵的坑之一。
    """
    runner = _PythonToolRunner(_WRITE_NOTHING)
    stale = runner._output_file("example.test")
    with open(stale, "w", encoding="utf-8") as handle:
        handle.write("old.example.test\n")

    result = runner.run("example.test")

    # 本次工具什么都没写，所以必须是 no_results，而不是把旧文件读回来。
    assert result.values == []
    assert result.error_code == ErrorCode.NO_RESULTS


def test_run_cleans_stale_file_even_when_tool_fails(output_dir):
    """工具失败时也要清掉旧文件，否则下次「成功」可能是假象。"""
    runner = _PythonToolRunner(_EXIT_NONZERO)
    stale = runner._output_file("example.test")
    with open(stale, "w", encoding="utf-8") as handle:
        handle.write("old.example.test\n")

    result = runner.run("example.test")

    assert result.error_code == ErrorCode.UNKNOWN_ERROR
    assert not os.path.exists(stale)


def test_execute_records_when_stale_file_cannot_be_removed(output_dir, monkeypatch):
    """删不掉旧文件时必须留下明确警告，而不是静默产出可疑结果。"""
    runner = _PythonToolRunner(_WRITE_TWO_LINES)
    stale = runner._output_file("example.test")
    with open(stale, "w", encoding="utf-8") as handle:
        handle.write("old.example.test\n")
    monkeypatch.setattr("modules.base.os.remove", lambda path: (_ for _ in ()).throw(PermissionError("占用中")))

    runner.run("example.test")

    assert "无法删除" in (runner.last_execution.get("stale_output_warning") or "")


# ── 超时必须真的生效（Windows .cmd 包装的孤儿进程） ─────────

_ORPHAN_MARKER = "gef_m4_orphan_marker"


@pytest.mark.skipif(os.name != "nt", reason="只有 Windows 才有 cmd.exe 包装层")
def test_timeout_kills_grandchild_behind_cmd_wrapper(output_dir, tmp_path):
    """Windows 上 ``.cmd`` 工具超时后，真正干活的孙进程也必须一起死。

    ``modules/base.py:_resolve_command`` 会用 ``cmd /c <script>.cmd`` 启动
    ``.cmd`` 工具，于是进程树是 ``python(worker) → cmd.exe → 工具``。
    旧实现用 ``subprocess.run(timeout=...)``：超时只杀掉 ``cmd.exe``，
    工具变成孤儿继续攥着 stdout/stderr 管道，``subprocess.run`` 于是
    **永远等不到管道关闭**——「超时」形同虚设，worker 被永久占住。

    这里用真实的三层结构验证：超时必须快速返回，且孙进程不再存活。
    """
    import subprocess as sp

    script = tmp_path / "orphan_tool.cmd"
    script.write_text(
        f'@echo off\r\n"{sys.executable}" -c "import time;time.sleep(120)" {_ORPHAN_MARKER}\r\n',
        encoding="ascii",
    )
    runner = _PythonToolRunner(_WRITE_TWO_LINES, config={"path": str(script), "process_timeout": 2})

    started = time.perf_counter()
    result = runner.run("example.test")
    elapsed = time.perf_counter() - started

    assert result.status == "timeout"
    assert result.error_code == ErrorCode.TIMEOUT
    # 2 秒超时 + 杀树 + 排空输出，必须远快于孙进程自己的 120 秒。
    assert elapsed < 30, f"超时后没有及时返回（{elapsed:.1f}s），进程树可能没被杀干净"

    assert _count_marker_processes() == 0, "孙进程成为孤儿并仍在运行"


def test_resolve_command_wraps_cmd_files_with_comspec(tmp_path):
    """``.cmd`` / ``.bat`` 必须经 ComSpec 启动（Windows 无法直接执行它们）。"""
    runner = _PythonToolRunner(_WRITE_TWO_LINES)
    script = tmp_path / "tool.cmd"
    script.write_text("@echo off\r\n", encoding="ascii")

    resolved = runner._resolve_command([str(script), "-d", "example.test"])

    assert resolved[0].lower().endswith("cmd.exe")
    assert resolved[1] == "/c"
    assert resolved[2] == str(script)
    assert resolved[3:] == ["-d", "example.test"]


def _count_marker_processes() -> int:
    """数一下命令行里带孤儿标记的 python 进程（用于验证进程树被杀干净）。"""
    import subprocess as sp

    script = (
        "import subprocess,sys;"
        "out=subprocess.run(['wmic','process','where',\"name='python.exe'\",'get','CommandLine'],"
        "capture_output=True,text=True,errors='replace').stdout;"
        f"print(sum(1 for line in out.splitlines() if {_ORPHAN_MARKER!r} in line))"
    )
    completed = sp.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
    try:
        return int((completed.stdout or "0").strip().splitlines()[-1])
    except (ValueError, IndexError):  # pragma: no cover - 环境不支持 wmic
        return 0
