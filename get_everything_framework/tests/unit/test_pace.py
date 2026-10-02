"""扫描节奏（``core.pace``）—— Scan Profile 的第二维（下一阶段方案第 5、6 节 Phase 3）。

本文件锁死四件事，它们共同回答「低频档到底是不是可执行约束，还是只是文案」：

1. **档位模型**：只有 ``light`` / ``normal``，非法值**报错**而不是静默回退；
2. **只能收紧**：``resolve_pace`` 里 ``light`` 永远赢，请求体放松不了模板档位；
3. **真的降速**：``light`` 会把并发与每秒请求数翻译成工具的命令行参数
   （``-t 5 -rl 3`` 这类），且**改的是 config 副本**，不污染模块级配置；
4. **历史行为不变**：``normal`` 不覆盖任何参数、不产生任何等待，
   ``step_delay_for("normal") == 0``。

节奏**不是**安全闸门：本文件不测（也不该测）它放开 Scope / ``active_scan`` /
``GEF_ALLOW_REAL_SCAN`` / 公网白名单 —— 那些边界由
``test_tool_registry.py``、``test_scope.py``、``test_public_scan_mode.py`` 锁死。
"""

import pytest

from core import pace


# ── 档位模型 ──────────────────────────────────────────────


def test_levels_are_exactly_light_and_normal():
    assert pace.PACE_LEVELS == (pace.PACE_LIGHT, pace.PACE_NORMAL)
    assert pace.PACE_LIGHT == "light"
    assert pace.PACE_NORMAL == "normal"


def test_default_pace_is_normal():
    """缺省必须是 ``normal``：引入本模块不得改变任何历史调用方的行为。"""
    assert pace.DEFAULT_PACE == pace.PACE_NORMAL


def test_labels_and_descriptions_cover_every_level():
    """每个档位都要有中文标签与说明 —— 否则页面会显示出裸的英文 key。"""
    for level in pace.PACE_LEVELS:
        assert pace.PACE_LABELS[level]
        assert pace.PACE_DESCRIPTIONS[level]


def test_normalize_accepts_levels_case_insensitively():
    assert pace.normalize_pace("light") == "light"
    assert pace.normalize_pace("  LIGHT ") == "light"
    assert pace.normalize_pace("Normal") == "normal"


@pytest.mark.parametrize("bad", ["low", "fast", "轻", "", "   ", None, 3, ["light"]])
def test_normalize_rejects_anything_else(bad):
    """非法档位**报错**：写错 ``"low"`` 却拿到常规档是最危险的错法。"""
    with pytest.raises(ValueError):
        pace.normalize_pace(bad)


def test_coerce_falls_back_to_default_for_dirty_rows():
    """读**库里**的值才用宽松版：脏数据退化为缺省档，而不是让 worker 停摆。"""
    assert pace.coerce_pace("low") == pace.PACE_NORMAL
    assert pace.coerce_pace(None) == pace.PACE_NORMAL
    assert pace.coerce_pace("light") == "light"
    assert pace.coerce_pace("low", default="light") == "light"


# ── 只能收紧 ──────────────────────────────────────────────


def test_resolve_lets_the_profile_pace_win():
    """模板是低频时，请求写什么都改不回来。"""
    assert pace.resolve_pace(pace.PACE_LIGHT, None) == "light"
    assert pace.resolve_pace(pace.PACE_LIGHT, "normal") == "light"
    assert pace.resolve_pace(pace.PACE_LIGHT, "light") == "light"


def test_resolve_allows_tightening_a_normal_profile():
    assert pace.resolve_pace(pace.PACE_NORMAL, "light") == "light"
    assert pace.resolve_pace(pace.PACE_NORMAL, "normal") == "normal"


def test_resolve_treats_empty_string_as_unspecified():
    """表单里没选中的字段会以空串过来，它等同于「没提」。"""
    assert pace.resolve_pace(pace.PACE_LIGHT, "") == "light"
    assert pace.resolve_pace(pace.PACE_NORMAL, "   ") == "normal"


def test_resolve_rejects_invalid_requested_value():
    """请求体里的非法档位必须报错（而不是被当成缺省档）。"""
    with pytest.raises(ValueError):
        pace.resolve_pace(pace.PACE_NORMAL, "turbo")


def test_resolve_survives_an_invalid_profile_pace():
    """模板档位是仓库内常量，它若被改脏也不该由一次 HTTP 请求暴露成 500。"""
    assert pace.resolve_pace("dirty", None) == pace.PACE_NORMAL
    assert pace.resolve_pace("dirty", "light") == "light"


def test_strictest_picks_the_most_conservative():
    assert pace.strictest("normal", "light", "normal") == "light"
    assert pace.strictest("normal", "normal") == "normal"
    assert pace.strictest() == "normal"


# ── 步骤间隔 ──────────────────────────────────────────────


def test_normal_never_waits():
    """``normal`` 恒为 0 —— 这是「不带 Profile 的任务行为不变」的保证。"""
    assert pace.step_delay_for("normal") == 0.0


def test_light_step_delay_reads_env(monkeypatch):
    monkeypatch.setenv(pace.LIGHT_STEP_DELAY_ENV, "2.5")
    assert pace.light_step_delay_seconds() == 2.5
    assert pace.step_delay_for("light") == 2.5


def test_light_step_delay_falls_back_on_garbage(monkeypatch):
    monkeypatch.setenv(pace.LIGHT_STEP_DELAY_ENV, "soon")
    assert pace.light_step_delay_seconds() == pace.DEFAULT_LIGHT_STEP_DELAY_SEC


def test_light_step_delay_never_goes_negative(monkeypatch):
    monkeypatch.setenv(pace.LIGHT_STEP_DELAY_ENV, "-3")
    assert pace.light_step_delay_seconds() == 0.0


def test_conftest_pins_light_delay_to_zero():
    """测试套件必须为零秒等待：低频是产品行为，不该让每个用例付 1.5 秒墙钟。

    这条断言保护的是 ``tests/conftest.py`` 里那一行 —— 它被删掉时，
    全量测试不会变红，只会变慢（然后有人为了提高速度去改被测代码）。
    """
    assert pace.light_step_delay_seconds() == 0.0


# ── 工具预算与命令行 ──────────────────────────────────────


def test_normal_budget_is_empty():
    assert pace.tool_overrides_for("normal", "subfinder") == {}
    assert pace.tool_overrides_for("normal", "httpx") == {}


def test_light_budget_covers_the_public_whitelist():
    """公网白名单里的每个工具都要有低频预算，否则「低频」对它就是空话。"""
    from core.tool_registry import internet_allowed_tools

    for tool_name in internet_allowed_tools():
        assert pace.tool_overrides_for("light", tool_name), f"{tool_name} 没有低频预算"


def test_light_budget_is_stricter_than_the_tool_defaults():
    """低频必须真的更小：比 ``config`` 里的默认并发和默认速率都低。"""
    from config import HTTPX_CONFIG, SUBFINDER_CONFIG

    subfinder = pace.tool_overrides_for("light", "subfinder")
    assert subfinder["threads"] < SUBFINDER_CONFIG["threads"]
    assert subfinder["rate_limit"] > 0

    httpx = pace.tool_overrides_for("light", "httpx")
    assert httpx["threads"] < HTTPX_CONFIG["threads"]
    assert httpx["rate_limit"] > 0


def test_overrides_are_a_copy_not_the_shared_budget():
    """调用方拿到的必须是副本：改它不能污染模块级预算表。"""
    first = pace.tool_overrides_for("light", "subfinder")
    first["threads"] = 999
    assert pace.LIGHT_TOOL_BUDGET["subfinder"]["threads"] != 999


def test_unknown_tool_has_no_budget():
    assert pace.tool_overrides_for("light", "nmap") == {}


def test_apply_to_runner_copies_config_and_does_not_mutate_the_original():
    """``apply_to_runner`` 必须替换 ``config`` 而不是原地改。

    Runner 的 ``self.config`` 指向**模块级**配置对象；原地改会让同一个进程里
    后续所有任务（以及页面上的工具状态）都跟着变。
    """
    from config import SUBFINDER_CONFIG
    from modules.subfinder import SubfinderRunner

    runner = SubfinderRunner()
    shared = runner.config
    assert shared is SUBFINDER_CONFIG

    changed = pace.apply_to_runner(runner, "light")

    assert changed is True
    assert runner.config["threads"] == pace.LIGHT_TOOL_BUDGET["subfinder"]["threads"]
    assert runner.config["rate_limit"] == pace.LIGHT_TOOL_BUDGET["subfinder"]["rate_limit"]
    # 模块级对象一个字节都没动。
    assert shared == SUBFINDER_CONFIG
    assert SUBFINDER_CONFIG["threads"] != pace.LIGHT_TOOL_BUDGET["subfinder"]["threads"]
    assert "rate_limit" not in SUBFINDER_CONFIG


def test_apply_to_runner_is_a_noop_for_normal():
    from modules.subfinder import SubfinderRunner

    runner = SubfinderRunner()
    shared = runner.config
    assert pace.apply_to_runner(runner, "normal") is False
    assert runner.config is shared


def test_subfinder_command_only_gains_rl_when_budget_is_applied():
    """常规档命令行与历史逐字节一致；低频档才多出 ``-t 5 -rl 3``。"""
    from modules.subfinder import SubfinderRunner

    normal = SubfinderRunner().build_command("example.test")
    assert "-rl" not in normal

    light = SubfinderRunner()
    pace.apply_to_runner(light, "light")
    cmd = light.build_command("example.test")
    assert cmd[cmd.index("-t") + 1] == str(pace.LIGHT_TOOL_BUDGET["subfinder"]["threads"])
    assert cmd[cmd.index("-rl") + 1] == str(pace.LIGHT_TOOL_BUDGET["subfinder"]["rate_limit"])


def test_httpx_command_only_gains_rl_when_budget_is_applied():
    from modules.httpx import HttpxRunner

    options = {"input_file": "in.txt", "output_file": "out.txt"}

    normal = HttpxRunner().build_command("example.test", options)
    assert "-rl" not in normal

    light = HttpxRunner()
    pace.apply_to_runner(light, "light")
    cmd = light.build_command("example.test", options)
    assert cmd[cmd.index("-threads") + 1] == str(pace.LIGHT_TOOL_BUDGET["httpx"]["threads"])
    assert cmd[cmd.index("-rl") + 1] == str(pace.LIGHT_TOOL_BUDGET["httpx"]["rate_limit"])


# ── 接口元数据 ────────────────────────────────────────────


def test_list_paces_shape_is_stable():
    items = pace.list_paces()
    assert [item["pace"] for item in items] == list(pace.PACE_LEVELS)
    for item in items:
        assert set(item) == {"pace", "pace_label", "pace_description", "step_delay_seconds"}


def test_describe_is_lenient_about_dirty_input():
    assert pace.describe("dirty")["pace"] == pace.PACE_NORMAL


# ── 与「唯一的构造接缝」配合 ──────────────────────────────


def test_apply_to_runner_works_on_the_registry_seam():
    """执行期走的是 ``registry.build_runner`` + ``apply_to_runner`` 两步。

    刻意**不**新增 ``build_scoped_runner`` 这类第二构造入口：
    ``build_runner(tool_name)`` 是测试替换真实 Runner 的那一条接缝
    （``monkeypatch.setattr("modules.registry.build_runner", ...)``），
    多一条构造路径就等于多一个「假 Runner 没被替换、真去跑外部命令」的机会。
    这里锁死这个分工：构造归 registry，降速归 pace。
    """
    from modules.registry import build_runner

    runner = build_runner("subfinder")
    assert pace.apply_to_runner(runner, "light") is True
    assert runner.config["rate_limit"] == pace.LIGHT_TOOL_BUDGET["subfinder"]["rate_limit"]


def test_apply_to_runner_tolerates_a_runner_without_config():
    """假 Runner（测试替身）没有 ``config`` 时，降速必须静默跳过而不是报错。

    ``jobs/executor`` 用一个裸 ``try/except`` 包住这次调用，所以这里真正要锁的
    是「不抛异常」—— 降速是策略，不是执行前提，任何情况下都不该把任务弄挂。
    """

    class _FakeRunner:
        tool_name = "subfinder"

    fake = _FakeRunner()
    pace.apply_to_runner(fake, "light")  # 不抛异常即可
    # 没有预算的工具则什么都不做（``False`` 表示「这一档对它没有预算」）。
    assert pace.apply_to_runner(fake, "normal") is False
    assert pace.tool_overrides_for("light", "unknown-tool") == {}