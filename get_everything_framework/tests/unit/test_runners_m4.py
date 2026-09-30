"""M4 单元测试：先跑通的三个 runner（subfinder / httpx / dnsx）。

「先跑通再铺开」策略的第一批：这三个是最常用的工具，把它们改造成
方案第 8.1 节的统一接口，验证解析与结构化字段正确后，再铺到其余 14 个。

本文件**不执行任何外部工具**：只验证

* ``build_command`` 拼出的命令行参数（含路径/参数顺序）；
* ``parse_output`` 对输出文件的解析；
* ``run_scan`` 的旧签名兼容（仍返回 URL/子域名字符串列表）；
* ``httpx`` 保留状态码/标题/Web Server/技术栈/CDN（方案 M4 交付项）。
"""

import json
import os

import pytest

from core.errors import ErrorCode
from core.runner_result import RunnerInputError


@pytest.fixture
def results_dir(tmp_path, monkeypatch):
    """把 runner 的输出目录指到临时目录。"""
    target = str(tmp_path / "results")
    os.makedirs(target, exist_ok=True)
    monkeypatch.setattr("modules.base.OUTPUT_DIR", target, raising=False)
    monkeypatch.setattr("modules.httpx.OUTPUT_DIR", target, raising=False)
    monkeypatch.setattr("modules.dnsx.OUTPUT_DIR", target, raising=False)
    return target


# ── subfinder ─────────────────────────────────────────────


def test_subfinder_build_command(results_dir):
    from modules.subfinder import SubfinderRunner

    runner = SubfinderRunner()
    cmd = runner.build_command("example.test", {"output_file": os.path.join(results_dir, "out.txt")})

    assert cmd[0] == runner.config["path"]
    assert "-d" in cmd and cmd[cmd.index("-d") + 1] == "example.test"
    assert "-o" in cmd
    assert cmd[cmd.index("-o") + 1].endswith("out.txt")
    # 配置里 silent=True，必须体现出来（否则输出里混日志会污染解析）。
    assert "-silent" in cmd


def test_subfinder_parse_output_reads_file(results_dir):
    from modules.subfinder import SubfinderRunner

    out = os.path.join(results_dir, "sub.txt")
    with open(out, "w", encoding="utf-8") as handle:
        handle.write("a.example.test\n\nb.example.test\n")

    runner = SubfinderRunner()
    values, error = runner.parse_output("", "", {"output_file": out})
    assert values == ["a.example.test", "b.example.test"]
    assert error is None


def test_subfinder_parse_output_falls_back_to_stdout(results_dir):
    from modules.subfinder import SubfinderRunner

    values, _ = SubfinderRunner().parse_output("x.example.test\n", "", None)
    assert values == ["x.example.test"]


def test_subfinder_observations_are_subdomains(results_dir):
    from modules.subfinder import SubfinderRunner

    runner = SubfinderRunner()
    items = runner.observations(["a.example.test"])
    assert items[0].category == "subdomain"
    assert items[0].value == "a.example.test"
    assert items[0].source_tool == "subfinder"


def test_subfinder_run_missing_binary_is_tool_not_found(results_dir):
    """runner 层也要能直接给出 error_code（供 /api/tool/<n>/run 用）。"""
    from modules.subfinder import SubfinderRunner

    runner = SubfinderRunner()
    runner.config = dict(runner.config, path="definitely-not-installed-xyz")
    result = runner.run("example.test")

    assert result.error_code == ErrorCode.TOOL_NOT_FOUND
    assert result.status == "failed"


# ── httpx ─────────────────────────────────────────────────


def _write_jsonl(path, items):
    with open(path, "w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item) + "\n")


def test_httpx_build_command_requests_metadata(results_dir):
    from modules.httpx import HttpxRunner

    runner = HttpxRunner()
    cmd = runner.build_command(
        "example.test",
        {"input_file": "in.txt", "output_file": "out.jsonl"},
    )

    assert "-json" in cmd
    for flag in ("-title", "-status-code", "-web-server", "-cdn"):
        assert flag in cmd, f"缺少 M4 要求保留的字段开关: {flag}"


def test_httpx_parse_output_keeps_all_fields(results_dir):
    """M4 交付项：URL / 状态码 / 标题 / Web Server / 技术栈 / CDN。"""
    from modules.httpx import HttpxRunner

    out = os.path.join(results_dir, "probe.jsonl")
    _write_jsonl(
        out,
        [
            {
                "url": "https://a.example.test",
                "status_code": 200,
                "title": "首页",
                "webserver": "nginx",
                "tech": ["Nginx", "Vue.js"],
                "cdn": "cloudflare",
                "input": "a.example.test",
            }
        ],
    )

    items, error = HttpxRunner().parse_output("", "", {"output_file": out})
    assert error is None
    assert items[0]["status_code"] == 200
    assert items[0]["title"] == "首页"
    assert items[0]["webserver"] == "nginx"
    assert items[0]["tech"] == ["Nginx", "Vue.js"]
    assert items[0]["cdn"] == "cloudflare"


def test_httpx_observations_carry_metadata(results_dir):
    from modules.httpx import HttpxRunner

    out = os.path.join(results_dir, "probe.jsonl")
    _write_jsonl(out, [{"url": "https://a.example.test", "status_code": 403, "title": "Forbidden", "tech": ["Nginx"]}])

    runner = HttpxRunner()
    items, _ = runner.parse_output("", "", {"output_file": out})
    runner.last_items = items
    observations = runner.observations([item["url"] for item in items])

    assert observations[0].category == "web"
    assert observations[0].value == "https://a.example.test"
    assert observations[0].data["status_code"] == 403
    assert observations[0].data["title"] == "Forbidden"
    assert observations[0].data["tech"] == ["Nginx"]


def test_httpx_tolerates_broken_json_lines(results_dir):
    """工具输出里混入半行/非 JSON 时不能整体炸掉。"""
    from modules.httpx import HttpxRunner

    out = os.path.join(results_dir, "probe.jsonl")
    with open(out, "w", encoding="utf-8") as handle:
        handle.write('{"url": "https://a.example.test", "status_code": 200}\n')
        handle.write("这不是 JSON\n")
        handle.write("\n")
        handle.write('{"url": "https://b.example.test", "status_code": 301}\n')

    items, _ = HttpxRunner().parse_output("", "", {"output_file": out})
    assert [item["url"] for item in items] == ["https://a.example.test", "https://b.example.test"]


def test_httpx_without_candidates_reports_no_results(results_dir, monkeypatch):
    """没有候选目标 = 前置数据缺失，不能算工具故障。"""
    from modules.httpx import HttpxRunner

    runner = HttpxRunner()
    monkeypatch.setattr(runner, "_load_candidates", lambda domain: [])

    with pytest.raises(RunnerInputError) as excinfo:
        runner.run_scan("example.test")
    assert excinfo.value.error_code == ErrorCode.NO_RESULTS
    assert excinfo.value.status == "success"


def test_httpx_run_wraps_missing_candidates_as_success_no_results(results_dir, monkeypatch):
    """经 ``run()`` 包装后：status=success + error_code=no_results。"""
    from modules.httpx import HttpxRunner

    runner = HttpxRunner()
    monkeypatch.setattr(runner, "_load_candidates", lambda domain: [])
    result = runner.run("example.test")

    assert result.status == "success"
    assert result.error_code == ErrorCode.NO_RESULTS
    assert not result.is_failure


def test_httpx_cleans_up_input_file_on_failure(results_dir, monkeypatch):
    """临时输入文件必须在失败路径上也被删除（否则 results/ 会越堆越多）。"""
    from modules.httpx import HttpxRunner

    runner = HttpxRunner()
    monkeypatch.setattr(runner, "_load_candidates", lambda domain: ["a.example.test"])
    # 让子进程执行失败，但不真的起进程。
    monkeypatch.setattr(runner, "_execute", lambda cmd, domain: False)

    with pytest.raises(RuntimeError):
        runner.run_scan("example.test")
    leftovers = [name for name in os.listdir(results_dir) if "httpx_input" in name]
    assert leftovers == []


# ── dnsx ──────────────────────────────────────────────────


def test_dnsx_build_command(results_dir):
    from modules.dnsx import DnsxRunner

    runner = DnsxRunner()
    cmd = runner.build_command("example.test", {"input_file": "in.txt", "output_file": "out.txt"})

    assert cmd[cmd.index("-l") + 1] == "in.txt"
    assert cmd[cmd.index("-o") + 1] == "out.txt"
    assert "-resp-only" in cmd  # DNSX_CONFIG 里 resp_only=True


def test_dnsx_load_candidates_falls_back_to_domain(results_dir, monkeypatch):
    from modules.dnsx import DnsxRunner

    runner = DnsxRunner()
    monkeypatch.setattr(runner.store, "get_results_by_domain", lambda domain: [])
    assert runner._load_candidates("example.test") == ["example.test"]


def test_dnsx_observations_category_is_alive(results_dir):
    from modules.dnsx import DnsxRunner

    runner = DnsxRunner()
    items = runner.observations(["a.example.test"])
    # DNSX_CONFIG 的 category 是 alive，不是 subdomain。
    assert items[0].category == "alive"


def test_dnsx_parse_output_reads_file(results_dir):
    from modules.dnsx import DnsxRunner

    out = os.path.join(results_dir, "dnsx.txt")
    with open(out, "w", encoding="utf-8") as handle:
        handle.write("a.example.test\nb.example.test\n")

    values, error = DnsxRunner().parse_output("", "", {"output_file": out})
    assert values == ["a.example.test", "b.example.test"]
    assert error is None
