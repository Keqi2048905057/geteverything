"""结果链诊断用例：证明「正常输出 → 非零结果」并区分六类归因。

本文件**离线、秒级、不访问任何真实公网目标**（AGENTS.md 硬约束）。
做法：把「外部工具」换成一个本地 `.cmd` 垫片，垫片里跑 ``sys.executable``
执行一段确定性的 Python 代码，于是

* 走的是**真实的** ``BaseRunner._execute`` / ``_execute_stdout`` / ``run()``；
* 走的是**真实的** subfinder / assetfinder / dnsx / waybackurls / httpx runner；
* 但子进程只会读写临时目录，绝不碰网络。

它回答四件事：

1. 框架的输出捕获链是否完好 —— 给固定输出，能否解析出非零记录；
2. 「空输出」是否被正确表达为 ``no_results``（而不是失败）；
3. 「进程失败」是否被正确表达为非 ``no_results`` 的错误码；
4. 「上游没有目标」是否与「跑了但零结果」区分得开（httpx 未执行不得报成功）。
"""

import os
import sys
from pathlib import Path

import pytest

from core.errors import ErrorCode
from core.runner_result import STATUS_SUCCESS


# ── 垫片设施 ──────────────────────────────────────────────


def _make_tool(tmp_path: Path, monkeypatch, name: str, body: str) -> str:
    """造一个名为 ``name`` 的假工具，返回可执行文件路径。

    ``body`` 是子进程侧要执行的 Python 源码。垫片（``.cmd``）把它交给
    ``sys.executable``，于是 Windows 上会经过 ``cmd /c`` ——
    正是 ``modules/base.py:_resolve_command`` 要覆盖的那条真实路径。
    """
    tools = tmp_path / "tools"
    tools.mkdir(exist_ok=True)
    script = tools / f"{name}_impl.py"
    script.write_text(body, encoding="utf-8")

    shim = tools / f"{name}.cmd"
    shim.write_text(
        "@echo off\r\n"
        f'"{sys.executable}" "{script}" %*\r\n',
        encoding="ascii",
    )
    return str(shim)


@pytest.fixture
def results_dir(tmp_path, monkeypatch):
    """把 runner 的输出目录指到临时目录（绝不写仓库 results/）。"""
    target = str(tmp_path / "results")
    os.makedirs(target, exist_ok=True)
    monkeypatch.setattr("modules.base.OUTPUT_DIR", target, raising=False)
    monkeypatch.setattr("modules.httpx.OUTPUT_DIR", target, raising=False)
    monkeypatch.setattr("modules.dnsx.OUTPUT_DIR", target, raising=False)
    return target


# 子进程侧代码片段 ------------------------------------------------------

#: 把固定内容写进 ``-o`` 指定的文件（subfinder / dnsx / httpx 形态）。
_WRITE_SUBDOMAINS = """
import pathlib, sys
argv = sys.argv
out = argv[argv.index('-o') + 1]
pathlib.Path(out).write_text(
    'a.example.test\\nb.example.test\\nc.example.test\\n', encoding='utf-8')
"""

#: 只往 stdout 打（assetfinder / waybackurls 形态），不碰文件。
_PRINT_SUBDOMAINS = """
import sys
sys.stdout.write('a.example.test\\nb.example.test\\nc.example.test\\n')
"""

#: 建出空文件（工具确实跑了、但确实没发现东西）。
_WRITE_EMPTY = """
import pathlib, sys
argv = sys.argv
out = argv[argv.index('-o') + 1]
pathlib.Path(out).write_text('', encoding='utf-8')
"""

#: 什么都不输出。
_WRITE_NOTHING = "import sys\n"

#: 非零退出（工具自身报错）。
_EXIT_NONZERO = "import sys\nsys.stderr.write('boom: source unreachable\\n')\nsys.exit(2)\n"

#: httpx 的 JSONL 形态。
_WRITE_HTTPX_JSONL = """
import json, pathlib, sys
argv = sys.argv
out = argv[argv.index('-o') + 1]
lines = [
    json.dumps({"url": "https://a.example.test", "status_code": 200,
                "title": "A", "webserver": "nginx", "tech": ["Nginx"]}),
    json.dumps({"url": "https://b.example.test", "status_code": 301,
                "title": "B", "webserver": "nginx", "tech": []}),
]
pathlib.Path(out).write_text("\\n".join(lines) + "\\n", encoding="utf-8")
"""


def _subfinder_with(path: str):
    from modules.subfinder import SubfinderRunner

    runner = SubfinderRunner()
    runner.config = dict(runner.config, path=path)
    return runner


def _assetfinder_with(path: str):
    from modules.assetfinder import AssetfinderRunner

    runner = AssetfinderRunner()
    runner.config = dict(runner.config, path=path)
    return runner


def _waybackurls_with(path: str):
    from modules.url_tools import WaybackurlsRunner

    runner = WaybackurlsRunner()
    runner.config = dict(runner.config, path=path)
    return runner


def _dnsx_with(path: str):
    from modules.dnsx import DnsxRunner

    runner = DnsxRunner()
    runner.config = dict(runner.config, path=path)
    return runner


def _httpx_with(path: str, candidates=None):
    from modules.httpx import HttpxRunner

    runner = HttpxRunner()
    runner.config = dict(runner.config, path=path)
    if candidates is not None:
        # 契约：执行器在调用 run() 之前，把**同任务上游**已产出的目标交给 runner。
        # 没有这一条注入时，runner 只能去读旧库 —— 而新任务链从不写旧库。
        runner.set_upstream_candidates(candidates)
    return runner


# ── 1. 正常输出必须解析出非零记录（每个工具一条） ──────────


def test_subfinder_normal_output_yields_records(results_dir, tmp_path, monkeypatch):
    """subfinder 用 ``-o`` 写文件：固定 3 行输出必须变成 3 条观测。"""
    tool = _make_tool(tmp_path, monkeypatch, "subfinder", _WRITE_SUBDOMAINS)
    result = _subfinder_with(tool).run("example.test")

    assert result.found_count == 3, "subfinder 的输出捕获/解析链断了"
    assert result.values == ["a.example.test", "b.example.test", "c.example.test"]
    assert result.error_code is None
    assert result.status == STATUS_SUCCESS


def test_assetfinder_normal_output_yields_records(results_dir, tmp_path, monkeypatch):
    """assetfinder 只走 stdout：``_execute_stdout`` 必须把它落盘再解析。

    这是本次故障里最需要排除的一条 —— 若框架「只读输出文件、丢弃 stdout」，
    这条用例必挂。
    """
    tool = _make_tool(tmp_path, monkeypatch, "assetfinder", _PRINT_SUBDOMAINS)
    result = _assetfinder_with(tool).run("example.test")

    assert result.found_count == 3, "assetfinder 的 stdout 没有被捕获/落盘"
    assert result.values == ["a.example.test", "b.example.test", "c.example.test"]
    assert result.error_code is None


def test_waybackurls_normal_output_yields_records(results_dir, tmp_path, monkeypatch):
    """waybackurls 同为 stdout 型工具，必须能拿到 URL。"""
    body = "import sys\nsys.stdout.write('https://example.test/a\\nhttps://example.test/b\\n')\n"
    tool = _make_tool(tmp_path, monkeypatch, "waybackurls", body)
    result = _waybackurls_with(tool).run("example.test")

    assert result.found_count == 2
    assert result.values == ["https://example.test/a", "https://example.test/b"]
    assert result.error_code is None


def test_dnsx_normal_output_yields_records(results_dir, tmp_path, monkeypatch):
    """dnsx 用 ``-l`` 读输入、``-o`` 写结果，且需要上游候选。"""
    body = """
import pathlib, sys
argv = sys.argv
out = argv[argv.index('-o') + 1]
pathlib.Path(out).write_text('1.2.3.4\\n5.6.7.8\\n', encoding='utf-8')
"""
    tool = _make_tool(tmp_path, monkeypatch, "dnsx", body)
    result = _dnsx_with(tool).run("example.test")

    assert result.found_count == 2
    assert result.values == ["1.2.3.4", "5.6.7.8"]
    assert result.error_code is None


def test_httpx_normal_output_yields_records(results_dir, tmp_path, monkeypatch):
    """httpx 的 JSONL 必须解析成带状态码/标题的观测。"""
    tool = _make_tool(tmp_path, monkeypatch, "httpx", _WRITE_HTTPX_JSONL)
    result = _httpx_with(tool, candidates=["a.example.test", "b.example.test"]).run("example.test")

    assert result.found_count == 2
    assert result.values == ["https://a.example.test", "https://b.example.test"]
    assert result.error_code is None
    data = {item.value: item.data for item in result.data}
    assert data["https://a.example.test"]["status_code"] == 200


# ── 2. 空输出 = no_results（不是失败） ─────────────────────


def test_empty_output_is_no_results_not_failure(results_dir, tmp_path, monkeypatch):
    tool = _make_tool(tmp_path, monkeypatch, "subfinder", _WRITE_EMPTY)
    result = _subfinder_with(tool).run("example.test")

    assert result.found_count == 0
    assert result.error_code == ErrorCode.NO_RESULTS
    assert result.status == STATUS_SUCCESS, "零结果不是失败"
    assert not result.is_failure


def test_silent_tool_with_no_output_is_no_results(results_dir, tmp_path, monkeypatch):
    """连文件都不建（stdout 型工具一句话都不打）也必须是 no_results。"""
    tool = _make_tool(tmp_path, monkeypatch, "waybackurls", _WRITE_NOTHING)
    result = _waybackurls_with(tool).run("example.test")

    assert result.found_count == 0
    assert result.error_code == ErrorCode.NO_RESULTS
    assert not result.is_failure


# ── 3. 进程失败 ≠ 零结果 ──────────────────────────────────


def test_nonzero_exit_is_not_no_results(results_dir, tmp_path, monkeypatch):
    """工具自己报错（退出码 2）必须给出非 no_results 的错误码。

    这正是历史上最贵的坑：失败被降级成空结果。
    """
    tool = _make_tool(tmp_path, monkeypatch, "subfinder", _EXIT_NONZERO)
    result = _subfinder_with(tool).run("example.test")

    assert result.error_code is not None
    assert result.error_code != ErrorCode.NO_RESULTS
    assert result.is_failure
    assert result.exit_code == 2
    # stderr 必须被留下来，否则排查时看不到「为什么失败」。
    assert result.stderr_preview and "boom" in result.stderr_preview


def test_missing_binary_is_tool_not_found(results_dir, tmp_path, monkeypatch):
    result = _subfinder_with("definitely-not-installed-xyz").run("example.test")

    assert result.error_code == ErrorCode.TOOL_NOT_FOUND
    assert result.is_failure


# ── 4. 上游没有目标 ≠ 跑了但零结果 ────────────────────────


def test_httpx_without_upstream_candidates_is_not_reported_as_success(
    results_dir, tmp_path, monkeypatch
):
    """httpx 拿不到候选时**根本没有启动进程**，不能报成「执行成功」。

    现状（缺陷 D2）：``modules/httpx.py`` 用 ``status="success"`` 抛
    ``RunnerInputError``，页面上就变成「成功 / 结果数 0 / no_results」，
    与「真的跑了一遍、确实没有存活站点」完全无法区分。
    """
    tool = _make_tool(tmp_path, monkeypatch, "httpx", _WRITE_HTTPX_JSONL)
    runner = _httpx_with(tool, candidates=[])
    runner.set_upstream_candidates([])
    result = runner.run("example.test")

    # 没有候选 ⇒ 没有子进程 ⇒ 没有退出码、没有耗时。
    assert result.exit_code is None
    assert result.found_count == 0
    assert result.status != STATUS_SUCCESS, "未执行的步骤不能显示为「成功」"
    assert result.status == "skipped"


def test_httpx_prefers_upstream_candidates_over_legacy_store(
    results_dir, tmp_path, monkeypatch
):
    """上游候选必须优先于旧库 —— 否则同任务内 subfinder → httpx 永远是断的。

    这是缺陷 D1 的回归用例：旧库（``ScanResultStore``）里没有任何记录，
    新任务链也从不写它，所以只要 runner 仍以旧库为准，httpx 就永远拿不到目标。
    """
    tool = _make_tool(tmp_path, monkeypatch, "httpx", _WRITE_HTTPX_JSONL)
    runner = _httpx_with(tool, candidates=["a.example.test", "b.example.test"])

    injected_input = {}
    original = runner._write_input_file

    def _spy(domain, candidates, suffix=None):
        injected_input["candidates"] = list(candidates)
        return original(domain, candidates, suffix)

    monkeypatch.setattr(runner, "_write_input_file", _spy)
    result = runner.run("example.test")

    assert injected_input.get("candidates") == ["a.example.test", "b.example.test"]
    assert result.found_count == 2


def test_dnsx_prefers_upstream_candidates_over_legacy_store(
    results_dir, tmp_path, monkeypatch
):
    """dnsx 同理：有上游候选时不能再退化成「只解析根域名」。"""
    tool = _make_tool(tmp_path, monkeypatch, "dnsx", _WRITE_SUBDOMAINS)
    runner = _dnsx_with(tool)
    runner.set_upstream_candidates(["a.example.test", "b.example.test"])

    seen = {}
    original = runner._write_input_file

    def _spy(domain, candidates, suffix=None):
        seen["candidates"] = list(candidates)
        return original(domain, candidates, suffix)

    monkeypatch.setattr(runner, "_write_input_file", _spy)
    runner.run("example.test")

    assert seen.get("candidates") == ["a.example.test", "b.example.test"]
