"""M6 收尾项：``scripts/check_env.py`` 环境自检的行为契约。

这个脚本的价值全在**可信**两个字上，所以测试盯的不是「它能输出文字」，而是三条硬性质：

1. **只读**：跑完一遍，运行期目录一个字节都不许多出来（AGENTS.md 硬约束：
   脚本与测试不得污染 ``results/``）。用「目录快照 + mtime 快照」比对，而不是
   相信注释。
2. **不泄密**：报告里不能出现密钥的值。这里给 ``SECRET_KEY`` /
   ``LOCAL_ADMIN_TOKEN`` 塞一个哨兵串，再断言它没出现在任何输出形态里。
3. **退出码语义**：``ok`` → 0、``warn`` → 1、``fail`` → 2，``--strict`` 把 warn 也当 2。
   一个自检脚本若退出码是错的，CI 里挂上去就等于没有。

另有一组纯函数测试盯版本比较 —— ``"3.10" < "3.9"`` 在字符串比较下是 True，
这个坑一旦踩上，脚本会把合法的 Python 3.10 判成过旧。
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

# ── 加载被测模块（scripts 不是包，与 test_migrate_legacy 同一手法） ──


def _load_check_env():
    """把 ``scripts/check_env.py`` 当模块加载（scripts 不是包）。

    必须先塞进 ``sys.modules``：这个脚本用了 ``from __future__ import annotations``
    + 冻结 dataclass，而 ``dataclasses`` 处理字符串注解时会去
    ``sys.modules[cls.__module__]`` 里查名字字典 —— 没注册就拿到 ``None``，
    直接 ``AttributeError: 'NoneType' object has no attribute '__dict__'``。
    """
    import sys

    path = Path(__file__).resolve().parents[2] / "scripts" / "check_env.py"
    spec = importlib.util.spec_from_file_location("gef_check_env", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["gef_check_env"] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop("gef_check_env", None)
        raise
    return module


@pytest.fixture
def check_env():
    return _load_check_env()


# ── 1. 版本比较（纯函数） ─────────────────────────────────


def test_version_key_orders_3_10_after_3_9(check_env):
    """字符串比较会把 3.10 判成小于 3.9，这里必须是数字比较。"""
    assert check_env._version_key("3.10") > check_env._version_key("3.9")
    assert check_env._version_key("3.9") < check_env._version_key("3.10")
    assert check_env._version_key("3.11.9") == check_env._version_key("3.11.9")


def test_version_key_handles_prerelease_suffix(check_env):
    assert check_env._version_key("1.0.0rc1") < check_env._version_key("1.0.0")
    assert check_env._version_key("2.0") == check_env._version_key("2.0.0")


@pytest.mark.parametrize(
    ("installed", "spec", "expected"),
    [
        ("3.1.3", "==3.1.3", True),
        ("3.1.4", "==3.1.3", False),
        ("9.0", ">=7.0", True),
        ("6.9", ">=7.0", False),
        ("0.5", ">=0.5", True),
        ("1.10", "~=1.10", True),
        ("1.9", "~=1.10", False),
        # 认不出的规格不误报：宁可漏报也不能把好环境判成坏的。
        ("1.0", "any", True),
    ],
)
def test_satisfies(check_env, installed, spec, expected):
    assert check_env._satisfies(installed, spec) is expected


def test_parse_requirements_reads_both_pin_styles(check_env):
    """``requirement.txt`` 用 ``==``、``requirement-dev.txt`` 用 ``>=``，两种都要认。"""
    project_root = Path(__file__).resolve().parents[2]

    runtime = dict(check_env._parse_requirements(project_root / "requirement.txt"))
    assert runtime.get("Flask") == "==3.1.3"
    assert len(runtime) >= 20

    dev = dict(check_env._parse_requirements(project_root / "requirement-dev.txt"))
    assert dev.get("pytest") == ">=7.0"
    assert dev.get("ruff") == ">=0.5"
    assert dev.get("mypy") == ">=1.10"


def test_parse_requirements_skips_comments_and_missing_file(check_env, tmp_path):
    missing = tmp_path / "nope.txt"
    assert check_env._parse_requirements(missing) == []

    sample = tmp_path / "req.txt"
    sample.write_text(
        "# 注释行\n\nflask==3.1.3  # 行尾注释不该把包名弄坏\n-r other.txt\npytest>=7.0\n",
        encoding="utf-8",
    )
    parsed = dict(check_env._parse_requirements(sample))
    assert parsed == {"flask": "==3.1.3", "pytest": ">=7.0"}


# ── 2. 退出码语义 ─────────────────────────────────────────


def _check(check_env, status: str):
    return check_env.Check("样例", status, "细节")


def test_exit_code_maps_status(check_env):
    ok = [_check(check_env, check_env.STATUS_OK)]
    warn = [_check(check_env, check_env.STATUS_WARN)]
    fail = [_check(check_env, check_env.STATUS_FAIL)]

    assert check_env.exit_code(ok) == 0
    assert check_env.exit_code(warn) == 1
    assert check_env.exit_code(fail) == 2
    # 混合时以最严重的为准。
    assert check_env.exit_code([*ok, *warn]) == 1
    assert check_env.exit_code([*warn, *fail]) == 2


def test_strict_promotes_warning_to_blocker(check_env):
    warn = [_check(check_env, check_env.STATUS_WARN)]
    assert check_env.exit_code(warn, strict=True) == 2
    # strict 不会把 ok 变成失败。
    assert check_env.exit_code([_check(check_env, check_env.STATUS_OK)], strict=True) == 0
    # strict 也不会把已有的 fail 降级。
    assert check_env.exit_code([_check(check_env, check_env.STATUS_FAIL)], strict=True) == 2


def test_main_returns_exit_code_and_prints_report(check_env, capsys):
    code = check_env.main([])
    out = capsys.readouterr().out
    assert code in (0, 1, 2)
    assert "环境自检" in out
    assert "汇总:" in out


def test_main_json_output_is_single_parseable_line(check_env, capsys):
    code = check_env.main(["--json"])
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["exit_code"] == code
    assert payload["ok"] is (code == 0)
    assert isinstance(payload["checks"], list) and payload["checks"]
    for item in payload["checks"]:
        assert set(item) >= {"name", "status", "detail", "hint"}
        assert item["status"] in {"ok", "warn", "fail"}


# ── 3. 不泄密 ─────────────────────────────────────────────


def test_report_never_contains_secret_values(check_env, monkeypatch, capsys):
    """哨兵串必须不出现在任何输出形态里（人读报告与 JSON 各查一遍）。"""
    import config

    secret_sentinel = "SENTINEL-SECRET-VALUE-abcdefghijklmnop"
    token_sentinel = "SENTINEL-ADMIN-TOKEN-abcdefghijklmnop"

    monkeypatch.setattr(config.Config, "SECRET_KEY", secret_sentinel, raising=False)
    monkeypatch.setattr(config.Config, "LOCAL_ADMIN_TOKEN", token_sentinel, raising=False)

    checks = check_env.collect_checks()
    human = check_env.render(checks)
    machine = json.dumps(check_env.build_payload(checks), ensure_ascii=False)

    for text in (human, machine):
        assert secret_sentinel not in text
        assert token_sentinel not in text
    # 但要确实报告「已配置」而不是装作看不见。
    secret_row = next(item for item in checks if item.name == "SECRET_KEY")
    assert secret_row.status == check_env.STATUS_OK


def test_report_flags_weak_secret_and_missing_token(check_env, monkeypatch):
    import config

    monkeypatch.setattr(config.Config, "SECRET_KEY", "dev-secret-key", raising=False)
    monkeypatch.setattr(config.Config, "LOCAL_ADMIN_TOKEN", "", raising=False)

    checks = check_env.collect_checks()
    rows = {item.name: item for item in checks}
    assert rows["SECRET_KEY"].status == check_env.STATUS_WARN
    assert rows["LOCAL_ADMIN_TOKEN"].status == check_env.STATUS_WARN
    # 警告必须带可执行的下一步，否则使用者只能猜。
    assert rows["SECRET_KEY"].hint
    assert rows["LOCAL_ADMIN_TOKEN"].hint


def test_report_flags_debug_and_public_bind(check_env, monkeypatch):
    """WEB_DEBUG=true 暴露调试器 → 阻塞项；非回环绑定 → 警告。"""
    import config

    monkeypatch.setattr(config.Config, "WEB_DEBUG", True, raising=False)
    monkeypatch.setattr(config.Config, "WEB_HOST", "0.0.0.0", raising=False)

    rows = {item.name: item for item in check_env.collect_checks()}
    assert rows["WEB_DEBUG"].status == check_env.STATUS_FAIL
    assert rows["WEB_HOST"].status == check_env.STATUS_WARN
    assert check_env.exit_code(list(rows.values())) == 2


# ── 4. 只读（最要紧的一条） ───────────────────────────────


def _snapshot_dir(path: str) -> dict:
    """目录里每个条目的名字 + mtime + 大小。目录不存在记为空。"""
    if not os.path.isdir(path):
        return {}
    snap = {}
    for name in os.listdir(path):
        full = os.path.join(path, name)
        try:
            info = os.stat(full)
        except OSError:  # pragma: no cover - 并发删除才会走到
            continue
        snap[name] = (info.st_mtime_ns, info.st_size)
    return snap


def test_collect_checks_writes_nothing(check_env):
    """自检必须无副作用：跑完后四个运行期目录逐条目比对完全一致。"""
    import config

    dirs = [config.OUTPUT_DIR, config.UPLOAD_DIR, config.EXPORT_DIR, config.BACKUP_DIR]
    before = {path: _snapshot_dir(path) for path in dirs}
    legacy_before = os.path.exists(str(config.SQLITE_CONFIG["path"]))

    check_env.collect_checks()

    after = {path: _snapshot_dir(path) for path in dirs}
    assert after == before, "自检脚本在运行期目录里留下了痕迹"
    # 也不能顺手把旧结果库「探测」出来。
    assert os.path.exists(str(config.SQLITE_CONFIG["path"])) is legacy_before


def test_collect_checks_does_not_create_db(check_env, tmp_path, monkeypatch):
    """应用库不存在时应报 warn 且**不建库**（方案第 5.1 节：/health 类探测必须无副作用）。"""
    import config

    missing = str(tmp_path / "not_created_yet.db")
    monkeypatch.setitem(config.LOCAL_DB_CONFIG, "path", missing)

    checks = check_env.collect_checks()
    row = next(item for item in checks if item.name == "本机应用库")
    assert row.status == check_env.STATUS_WARN
    assert not os.path.exists(missing), "自检把数据库文件创建出来了"


def test_collect_checks_reports_missing_tables(check_env, tmp_path, monkeypatch):
    """库存在但关键表缺失 → 阻塞项，并点名缺了哪些表。"""
    import sqlite3

    import config

    broken = str(tmp_path / "half_built.db")
    conn = sqlite3.connect(broken)
    conn.execute("CREATE TABLE scopes (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

    monkeypatch.setitem(config.LOCAL_DB_CONFIG, "path", broken)
    row = next(item for item in check_env.collect_checks() if item.name == "本机应用库")
    assert row.status == check_env.STATUS_FAIL
    assert "jobs" in row.detail
    assert row.hint


def test_collect_checks_reports_unreadable_db(check_env, tmp_path, monkeypatch):
    """文件在但不是 SQLite → 报阻塞项，而不是让异常冒出来把自检自己搞崩。"""
    import config

    junk = tmp_path / "junk.db"
    junk.write_bytes(b"this is definitely not a sqlite database" * 8)

    monkeypatch.setitem(config.SQLITE_CONFIG, "path", str(junk))
    row = next(item for item in check_env.collect_checks() if item.name == "旧结果库")
    assert row.status == check_env.STATUS_FAIL
    assert "打开失败" in row.detail


# ── 5. 结构性质 ───────────────────────────────────────────


def test_collect_checks_covers_the_documented_dimensions(check_env):
    """检查维度不能悄悄变少 —— 少一项就意味着某个坑没人看一眼。"""
    names = {item.name for item in check_env.collect_checks()}
    for required in (
        "Python 版本",
        "运行依赖",
        "开发依赖",
        ".env 文件",
        "SECRET_KEY",
        "LOCAL_ADMIN_TOKEN",
        "WEB_DEBUG",
        "WEB_HOST",
        "真实扫描总开关",
        "外部工具",
        "旧结果库",
        "本机应用库",
        "worker 心跳",
        "队列",
    ):
        assert required in names, f"缺少检查项: {required}"


def test_every_check_with_a_problem_has_a_hint(check_env):
    """warn / fail 必须带 hint：只说「有问题」而不说「怎么办」的报告没人能用。"""
    for item in check_env.collect_checks():
        if item.status != check_env.STATUS_OK:
            assert item.hint, f"{item.name} 是 {item.status} 却没有给出处理提示"


def test_external_tools_probe_never_executes_anything(check_env, monkeypatch):
    """工具探测只能用 shutil.which —— 任何真正的子进程调用都是越界。"""
    import subprocess

    def _boom(*args, **kwargs):  # pragma: no cover - 触发即失败
        raise AssertionError("环境自检不得启动任何子进程")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(subprocess, "check_output", _boom)
    monkeypatch.setattr(os, "system", _boom)

    check_env.collect_checks()
