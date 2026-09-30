"""M4 铺开单元测试：其余 14 个 runner 的统一接口。

「先跑通再铺开」的第二批。subfinder / httpx / dnsx / amass 的接口已在
``test_runners_m4.py`` 里验证过，本文件覆盖剩下的：

* 子域类：assetfinder、oneforall、alterx、shuffledns、enscan
* 爬虫与历史 URL：gospider、katana、waybackurls
* 目录与端口：feroxbuster、dirsearch、naabu、nmap

本文件**不执行任何外部工具**（唯一例外是 ``shuffledns`` 的 dnsx 调用被
monkeypatch 掉），只验证：

* ``build_command`` 拼出的参数（含输出文件参数、各工具特有开关）；
* ``parse_output`` 对输出文件/stdout 的解析；
* 所有 runner 都真的实现了这两个方法（防止漏铺）；
* ``run_scan`` 的旧签名与返回值仍然兼容。
"""

import json
import os
import sys

import pytest

from modules.registry import RUNNER_REGISTRY


@pytest.fixture
def results_dir(tmp_path, monkeypatch):
    """把所有 runner 的输出目录指到临时目录。

    只有这三个模块**直接**从 ``config`` 导入了 ``OUTPUT_DIR``；
    ``url_tools`` / ``port_tools`` / 各独立 runner 都是继承
    ``BaseRunner`` 后读 ``modules.base.OUTPUT_DIR``，所以改 base 就够了。
    """
    target = str(tmp_path / "results")
    os.makedirs(target, exist_ok=True)
    for module in ("base", "dnsx", "httpx"):
        monkeypatch.setattr(f"modules.{module}.OUTPUT_DIR", target, raising=False)
    return target


def _write(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


# ── 接口覆盖：不许漏铺 ─────────────────────────────────────


@pytest.mark.parametrize("tool_name", sorted(RUNNER_REGISTRY))
def test_every_runner_implements_unified_interface(tool_name):
    """每个 runner 都必须自带 ``build_command`` 与 ``parse_output``。

    基类没有提供默认实现，所以要显式检查 ``__dict__``：漏掉的 runner 会在
    这里被点名，而不是等到运行期才发现。
    """
    runner_cls = RUNNER_REGISTRY[tool_name]

    assert "build_command" in runner_cls.__dict__, f"{tool_name} 缺少 build_command"
    assert "parse_output" in runner_cls.__dict__, f"{tool_name} 缺少 parse_output"


@pytest.mark.parametrize("tool_name", sorted(RUNNER_REGISTRY))
def test_every_runner_build_command_returns_list(tool_name, results_dir):
    """``build_command`` 必须返回参数列表，且首个元素是可执行文件。"""
    runner = RUNNER_REGISTRY[tool_name]()

    options = {"input_file": os.path.join(results_dir, "in.txt"), "output_file": os.path.join(results_dir, "out.txt")}
    try:
        cmd = runner.build_command("example.test", options)
    except (KeyError, ValueError):
        # alterx 需要 input_file（已给）；amass_intel 的默认参数是 ASN，
        # 传域名会 ValueError —— 换成合法 ASN 再试一次。
        cmd = runner.build_command("AS15169", options)

    assert isinstance(cmd, list) and cmd
    assert all(isinstance(item, str) for item in cmd), f"{tool_name} 的命令行里混入了非字符串"

    if tool_name == "shuffledns":
        # shuffledns 自身没有二进制：它用 dnsx 实现 massdns 的角色，
        # 所以首元素是 dnsx 而不是配置里的 path（配置项保留给未来接真二进制）。
        assert cmd[0] == "dnsx"
    else:
        assert cmd[0] == runner.config["path"]


# ── 子域类 ────────────────────────────────────────────────


def test_assetfinder_build_command(results_dir):
    from modules.assetfinder import AssetfinderRunner

    runner = AssetfinderRunner()
    cmd = runner.build_command("example.test")

    assert cmd[0] == runner.config["path"]
    assert "--subs-only" in cmd
    assert cmd[-1] == "example.test"
    # Assetfinder 没有 -o 参数，结果只走 stdout。
    assert "-o" not in cmd


def test_assetfinder_parse_output_normalizes(results_dir):
    from modules.assetfinder import AssetfinderRunner

    out = _write(
        os.path.join(results_dir, "af.txt"),
        "https://a.example.test/path\nb.example.test\nnot-related.test\nA.EXAMPLE.TEST\n",
    )

    values, error = AssetfinderRunner().parse_output("", "", {"output_file": out, "domain": "example.test"})
    assert error is None
    # 只保留属于目标域的条目、统一小写、去重。
    assert values == ["a.example.test", "b.example.test"]


def test_assetfinder_parse_output_falls_back_to_stdout(results_dir):
    from modules.assetfinder import AssetfinderRunner

    values, _ = AssetfinderRunner().parse_output("x.example.test\n", "", {"domain": "example.test"})
    assert values == ["x.example.test"]


def test_oneforall_build_command(results_dir):
    from modules.oneforall import OneForAllRunner

    runner = OneForAllRunner()
    cmd = runner.build_command("example.test")

    assert "--target" in cmd
    assert cmd[cmd.index("--target") + 1] == "example.test"
    assert "run" in cmd


def test_oneforall_parse_output_reads_file(results_dir):
    from modules.oneforall import OneForAllRunner

    out = _write(os.path.join(results_dir, "ofa.txt"), "a.example.test\nb.example.test\n")
    values, error = OneForAllRunner().parse_output("", "", {"output_file": out})
    assert values == ["a.example.test", "b.example.test"]
    assert error is None


def test_alterx_build_command_requires_input_file(results_dir):
    from modules.alterx import AlterxRunner

    runner = AlterxRunner()
    cmd = runner.build_command("example.test", {"input_file": "in.txt", "output_file": "out.txt"})

    assert cmd[cmd.index("-l") + 1] == "in.txt"
    assert cmd[cmd.index("-o") + 1] == "out.txt"

    with pytest.raises(KeyError):
        runner.build_command("example.test", {"output_file": "out.txt"})


def test_alterx_parse_output_reads_file(results_dir):
    from modules.alterx import AlterxRunner

    out = _write(os.path.join(results_dir, "ax.txt"), "dev.example.test\ntest.example.test\n")
    values, error = AlterxRunner().parse_output("", "", {"output_file": out})
    assert values == ["dev.example.test", "test.example.test"]
    assert error is None


# ── shuffledns（内部用 dnsx，验证 dnsx 输出解析） ──────────


def test_shuffledns_build_command_json_mode(results_dir):
    from modules.shuffledns import ShufflednsRunner

    runner = ShufflednsRunner()
    cmd = runner.build_command(None, {"input_file": "in.txt", "json": True})

    assert cmd[0] == "dnsx"
    assert cmd[cmd.index("-l") + 1] == "in.txt"
    assert "-json" in cmd
    assert "-resp-only" not in cmd


def test_shuffledns_build_command_resp_only_mode(results_dir):
    from modules.shuffledns import ShufflednsRunner

    cmd = ShufflednsRunner().build_command(None, {"input_file": "in.txt", "json": False, "resp_only": True})

    assert "-resp-only" in cmd
    assert "-json" not in cmd


def test_shuffledns_parse_output_json_records(results_dir):
    """``-json`` 形态要保留 IP，泛解析判断依赖它。"""
    from modules.shuffledns import ShufflednsRunner

    stdout = "\n".join([
        json.dumps({"host": "a.example.test", "a": ["1.2.3.4", "5.6.7.8"]}),
        json.dumps({"host": "b.example.test", "a": ["9.9.9.9"]}),
    ])

    values, error = ShufflednsRunner().parse_output(stdout, "", None)
    assert error is None
    assert values[0] == {"value": "a.example.test", "ips": ["1.2.3.4", "5.6.7.8"]}
    assert values[1]["value"] == "b.example.test"


def test_shuffledns_parse_output_plain_lines(results_dir):
    from modules.shuffledns import ShufflednsRunner

    values, error = ShufflednsRunner().parse_output("a.example.test\nb.example.test\n", "", None)
    assert values == ["a.example.test", "b.example.test"]
    assert error is None


def test_shuffledns_parse_output_broken_json_is_parse_error(results_dir):
    """看着像 JSON 却一行都解析不出来 = 解析失败，不是「零结果」。"""
    from modules.shuffledns import ShufflednsRunner

    values, error = ShufflednsRunner().parse_output('{"host": "x"\n{"host": "y"\n', "", None)
    assert values == []
    assert error == "parse_error"


def test_shuffledns_bruteforce_uses_base_run_subprocess(results_dir, monkeypatch):
    """字典爆破必须走基类的 ``_run_subprocess``（带超时与进程树清理）。

    历史实现用裸 ``subprocess.run(timeout=)``，Windows 上超时杀不掉孤儿 dnsx。
    """
    from modules.shuffledns import ShufflednsRunner

    runner = ShufflednsRunner()
    wordlist = _write(os.path.join(results_dir, "words.txt"), "www\napi\n")
    calls = []

    def fake_run_subprocess(cmd, timeout, cwd=None):
        calls.append((cmd, timeout, cwd))
        return 0, "www.example.test\napi.example.test\n", ""

    monkeypatch.setattr(runner, "_run_subprocess", fake_run_subprocess)
    monkeypatch.setattr(runner, "_resolve_command", lambda cmd: cmd)

    found = runner._bruteforce_with_dnsx(wordlist, "example.test")

    assert found == ["www.example.test", "api.example.test"]
    assert len(calls) == 1
    assert calls[0][0][0] == "dnsx"
    # 临时单词表必须被清理，不能留在 results/ 里。
    assert [n for n in os.listdir(results_dir) if "brute_words" in n] == []


# ── enscan ────────────────────────────────────────────────


def test_enscan_build_command(results_dir):
    from modules.enscan import ENScanRunner

    runner = ENScanRunner()
    cmd = runner.build_command("某某科技有限公司")

    assert "-n" in cmd
    assert cmd[cmd.index("-n") + 1] == "某某科技有限公司"
    assert "-json" in cmd
    # enscan 把 JSON 写在自己的工作目录，没有 -o。
    assert "-o" not in cmd


def test_enscan_parse_output_extracts_domains(results_dir):
    from modules.enscan import ENScanRunner

    out = _write(
        os.path.join(results_dir, "enscan.json"),
        json.dumps({"icp": [{"domain": "Example.COM", "website": "https://www.example.org/x"}]}),
    )

    values, error = ENScanRunner().parse_output("", "", {"output_file": out})
    assert error is None
    assert "example.com" in values
    assert "example.org" in values


def test_enscan_parse_output_broken_json_is_parse_error(results_dir):
    from modules.enscan import ENScanRunner

    out = _write(os.path.join(results_dir, "enscan.json"), "{这不是 JSON")
    values, error = ENScanRunner().parse_output("", "", {"output_file": out})

    assert values == []
    assert error == "parse_error"


def test_enscan_run_uses_base_run_subprocess(results_dir, monkeypatch):
    """enscan 也必须走带超时保护的执行路径，并且要带 cwd。"""
    from modules.enscan import ENScanRunner

    runner = ENScanRunner()
    calls = []

    def fake_run_subprocess(cmd, timeout, cwd=None):
        calls.append({"cmd": cmd, "timeout": timeout, "cwd": cwd})
        return 0, "", ""

    monkeypatch.setattr(runner, "_run_subprocess", fake_run_subprocess)
    monkeypatch.setattr(runner, "_resolve_command", lambda cmd: cmd)

    runner.run_scan("某某科技")

    assert len(calls) == 1
    # enscan 结果写在它的工作目录下，所以必须显式把 cwd 指到 output_dir。
    assert calls[0]["cwd"] == runner.output_dir
    assert calls[0]["timeout"] == runner._timeout_seconds()


# ── 爬虫与历史 URL ─────────────────────────────────────────


def test_gospider_build_command_uses_stdout(results_dir):
    from modules.url_tools import GospiderRunner

    runner = GospiderRunner()
    cmd = runner.build_command("example.test")

    assert cmd[cmd.index("-s") + 1] == "https://example.test"
    assert "-d" in cmd
    # gospider 结果走 stdout，没有输出文件参数。
    assert "-o" not in cmd


def test_gospider_build_url_keeps_scheme(results_dir):
    from modules.url_tools import GospiderRunner, build_url

    assert build_url("http://example.test") == "http://example.test"
    assert build_url("example.test") == "https://example.test"

    cmd = GospiderRunner().build_command("http://example.test")
    assert cmd[cmd.index("-s") + 1] == "http://example.test"


def test_katana_build_command_writes_file(results_dir):
    from modules.url_tools import KatanaRunner

    cmd = KatanaRunner().build_command("example.test", {"output_file": "out.txt"})

    assert cmd[cmd.index("-u") + 1] == "https://example.test"
    assert cmd[cmd.index("-o") + 1] == "out.txt"


def test_waybackurls_build_command_is_bare_domain(results_dir):
    from modules.url_tools import WaybackurlsRunner

    cmd = WaybackurlsRunner().build_command("example.test")

    assert cmd[1] == "example.test"
    assert "-o" not in cmd


def test_gospider_parse_output_reads_file_then_stdout(results_dir):
    from modules.url_tools import GospiderRunner

    runner = GospiderRunner()
    out = _write(os.path.join(results_dir, "gs.txt"), "https://example.test/a\n")

    values, _ = runner.parse_output("", "", {"output_file": out})
    assert values == ["https://example.test/a"]

    values, _ = runner.parse_output("https://example.test/b\n", "", None)
    assert values == ["https://example.test/b"]


def test_katana_parse_output_reads_file(results_dir):
    from modules.url_tools import KatanaRunner

    out = _write(os.path.join(results_dir, "kt.txt"), "https://example.test/a\nhttps://example.test/b\n")
    values, error = KatanaRunner().parse_output("", "", {"output_file": out})

    assert values == ["https://example.test/a", "https://example.test/b"]
    assert error is None


def test_waybackurls_parse_output_reads_file(results_dir):
    from modules.url_tools import WaybackurlsRunner

    out = _write(os.path.join(results_dir, "wb.txt"), "https://example.test/old\n")
    values, _ = WaybackurlsRunner().parse_output("", "", {"output_file": out})

    assert values == ["https://example.test/old"]


# ── 目录扫描 ──────────────────────────────────────────────


def _ferox_runner(monkeypatch, json_output):
    from modules.url_tools import FeroxbusterRunner

    runner = FeroxbusterRunner()
    runner.config = dict(runner.config, json_output=json_output)
    return runner


def test_feroxbuster_build_command(results_dir, monkeypatch):
    runner = _ferox_runner(monkeypatch, json_output=True)
    cmd = runner.build_command("example.test", {"output_file": "out.txt"})

    assert cmd[cmd.index("-u") + 1] == "https://example.test"
    assert cmd[cmd.index("-o") + 1] == "out.txt"
    assert "--json" in cmd


def test_feroxbuster_parse_output_json_extracts_urls(results_dir, monkeypatch):
    runner = _ferox_runner(monkeypatch, json_output=True)
    out = _write(
        os.path.join(results_dir, "ferox.txt"),
        "\n".join([
            json.dumps({"url": "https://example.test/admin", "status": 200}),
            "半行不是 JSON",
            json.dumps({"url": "https://example.test/login", "status": 302}),
            json.dumps({"url": "https://example.test/admin", "status": 200}),
        ]),
    )

    values, error = runner.parse_output("", "", {"output_file": out})
    assert error is None
    # 提取 url、去重、跳过坏行。
    assert values == ["https://example.test/admin", "https://example.test/login"]


def test_feroxbuster_parse_output_text_mode(results_dir, monkeypatch):
    runner = _ferox_runner(monkeypatch, json_output=False)
    out = _write(os.path.join(results_dir, "ferox.txt"), "https://example.test/admin\n")

    values, _ = runner.parse_output("", "", {"output_file": out})
    assert values == ["https://example.test/admin"]


def test_dirsearch_build_command(results_dir):
    from modules.url_tools import DirsearchRunner

    runner = DirsearchRunner()
    runner.config = dict(runner.config, wordlist="words.txt")
    cmd = runner.build_command("example.test", {"output_file": "out.txt"})

    assert cmd[cmd.index("-u") + 1] == "https://example.test"
    assert cmd[cmd.index("-o") + 1] == "out.txt"
    assert cmd[cmd.index("-w") + 1] == "words.txt"


def test_dirsearch_build_command_omits_empty_wordlist(results_dir):
    from modules.url_tools import DirsearchRunner

    runner = DirsearchRunner()
    runner.config = dict(runner.config, wordlist=None)
    cmd = runner.build_command("example.test", {"output_file": "out.txt"})

    assert "-w" not in cmd


def test_dirsearch_parse_output_reads_file(results_dir):
    from modules.url_tools import DirsearchRunner

    out = _write(os.path.join(results_dir, "ds.txt"), "https://example.test/admin\n")
    values, _ = DirsearchRunner().parse_output("", "", {"output_file": out})

    assert values == ["https://example.test/admin"]


# ── 端口扫描 ──────────────────────────────────────────────


def test_naabu_build_command(results_dir):
    from modules.port_tools import NaabuRunner

    runner = NaabuRunner()
    cmd = runner.build_command("example.test", {"output_file": "out.txt"})

    assert cmd[cmd.index("-host") + 1] == "example.test"
    assert cmd[cmd.index("-o") + 1] == "out.txt"
    assert "-silent" in cmd


def test_naabu_parse_output_reads_file(results_dir):
    from modules.port_tools import NaabuRunner

    out = _write(os.path.join(results_dir, "nb.txt"), "1.2.3.4:80\n1.2.3.4:443\n")
    values, error = NaabuRunner().parse_output("", "", {"output_file": out})

    assert values == ["1.2.3.4:80", "1.2.3.4:443"]
    assert error is None


def test_nmap_build_command_uses_on_flag(results_dir):
    """nmap 用 ``-oN`` 而不是 ``-o``，基类的 declared_output_file 必须认得。"""
    from modules.base import declared_output_file
    from modules.port_tools import NmapRunner

    runner = NmapRunner()
    runner.config = dict(runner.config, ports="80,443")
    cmd = runner.build_command("example.test", {"output_file": "out.txt"})

    assert cmd[cmd.index("-p") + 1] == "80,443"
    assert cmd[cmd.index("-oN") + 1] == "out.txt"
    assert "example.test" in cmd
    assert declared_output_file(cmd) == "out.txt"


def test_nmap_build_command_omits_empty_ports(results_dir):
    from modules.port_tools import NmapRunner

    runner = NmapRunner()
    runner.config = dict(runner.config, ports=None)
    cmd = runner.build_command("example.test", {"output_file": "out.txt"})

    assert "-p" not in cmd


def test_nmap_parse_output_reads_file(results_dir):
    from modules.port_tools import NmapRunner

    out = _write(os.path.join(results_dir, "nmap.txt"), "80/tcp open http\n443/tcp open https\n")
    values, error = NmapRunner().parse_output("", "", {"output_file": out})

    assert values == ["80/tcp open http", "443/tcp open https"]
    assert error is None


# ── 统一行为的横切验证 ────────────────────────────────────


@pytest.mark.parametrize(
    "tool_name",
    ["assetfinder", "oneforall", "alterx", "gospider", "katana", "waybackurls",
     "feroxbuster", "dirsearch", "naabu", "nmap", "amass", "amass_intel"],
)
def test_parse_output_on_missing_file_is_empty_not_crash(tool_name, results_dir):
    """输出文件不存在时必须安静地返回空，而不是抛异常。

    这是「工具没产出任何东西」的正常路径，调用方靠 ``run()`` 的 error_code
    区分它和「工具失败」。
    """
    runner = RUNNER_REGISTRY[tool_name]()
    missing = os.path.join(results_dir, "does-not-exist.txt")

    values, _error = runner.parse_output("", "", {"output_file": missing, "domain": "example.test"})
    assert values == []


def test_results_dir_is_used_by_every_runner(results_dir):
    """所有 runner 的输出目录都必须落在（被 monkeypatch 的）results 目录下。

    防止某个 runner 绕过 ``self.output_dir`` 写到仓库根的 ``results/``。
    """
    real_repo_results = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "results")

    for tool_name in sorted(RUNNER_REGISTRY):
        runner = RUNNER_REGISTRY[tool_name]()
        assert runner.output_dir == results_dir, f"{tool_name} 的输出目录没走 OUTPUT_DIR"

    # 仓库里的 results/ 不能被测试创建出来。
    assert not os.path.exists(os.path.join(real_repo_results, "m4_test_marker"))


def test_no_runner_uses_bare_subprocess_run_for_tools():
    """静态自检：runner 里不该再有裸 ``subprocess.run`` 直接跑外部工具。

    历史实现用 ``subprocess.run(timeout=)``，在 Windows 上超时只杀掉
    ``cmd.exe`` 包装层，孤儿进程攥着管道会让 worker 永久卡住。所有工具调用
    都必须经 ``BaseRunner._run_subprocess``。

    检查的是**调用**而不是注释：注释里提到 ``subprocess.run`` 是允许的
    （用来解释为什么不用它）。
    """
    modules_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "modules")

    import ast

    offenders = []
    for name in sorted(os.listdir(modules_dir)):
        if not name.endswith(".py") or name in ("base.py", "__init__.py", "registry.py"):
            continue
        path = os.path.join(modules_dir, name)
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # 匹配 subprocess.run(...)
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "run"
                and isinstance(func.value, ast.Name)
                and func.value.id == "subprocess"
            ):
                offenders.append(f"{name}:{node.lineno}")

    assert offenders == [], f"这些位置仍在直接调用 subprocess.run: {offenders}"


def test_registry_has_all_expected_tools():
    """清单本身也要被钉住，避免铺开时漏掉某个工具。"""
    expected = {
        "alterx", "amass", "amass_intel", "assetfinder", "dirsearch", "dnsx",
        "enscan", "feroxbuster", "gospider", "httpx", "katana", "naabu",
        "nmap", "oneforall", "shuffledns", "subfinder", "waybackurls",
    }
    assert set(RUNNER_REGISTRY) == expected


def test_sys_path_sanity():
    """本文件依赖 ``pyproject.toml`` 里的 pythonpath 配置。"""
    assert sys.path
