"""单任务的限速与超时（``core.job_limits``）—— 「只能收紧」的可执行口径。

本文件锁死四件事，它们共同回答「单任务限速/超时到底是不是可执行约束，还是只是文案」：

1. **严格解析**：``None`` / 空串表示「不指定」，其余非法值一律 ``ValueError``；
   ``bool`` 是 ``int`` 的子类，必须被拒 —— 放进来 ``True`` 就成了 1 请求/秒；
2. **上界是活的**：``timeout_seconds_max()`` **每次调用**都读
   ``config.SCAN_LIMITS["process_timeout"]``，不在 import 期固化，否则
   ``GEF_PROCESS_TIMEOUT``（本机联调把它压小以验证「卡死的工具被判 timeout」）
   这条路径会失效；
3. **只能收紧**：``apply_to_runner`` 用 ``min`` 合并，请求里的放松值被拒；
   且改的是 ``config`` **副本**，不污染 Runner 指向的模块级配置；
4. **脏数据不炸**：读事件 detail / 工具 config 里的历史脏值时走宽松版，
   退化成「没指定」，而不是让 worker 停摆；
5. **报错带字段名**：非整数等格式错误的文案里必须含 ``rate_limit`` /
   ``timeout_seconds`` 之一 —— ``core.application`` 正是靠它把 400 回给
   正确的那一格（``details["field"]``），少了它就只能猜，
   于是 ``timeout_seconds="abc"`` 会被报成「rate_limit 有问题」。

本模块**不是**安全闸门：Scope / ``active_scan`` / ``GEF_ALLOW_REAL_SCAN`` /
公网白名单仍由 ``test_scope.py``、``test_public_scan_mode.py``、
``test_tool_registry.py`` 各自锁死，本文件不重复测。
"""

import pytest

import config
from core import job_limits


# ── 超时上限：每次调用都读配置 ────────────────────────────


def test_timeout_max_reads_config_on_every_call():
    """上界必须是**活值**：``setitem`` 后立刻生效，作用域结束后立刻恢复。

    ``config.SCAN_LIMITS["process_timeout"]`` 由 ``GEF_PROCESS_TIMEOUT`` 决定，
    所以这里既不写死 120、也不写死 monkeypatch 之前的值 —— 只断言
    「改完等于新值」「还原后回到改之前的真实值」。

    刻意用 ``pytest.MonkeyPatch.context()`` 而不是测试函数的 ``monkeypatch``：
    后者与 ``tests/conftest.py`` 的 autouse 夹具（运行期目录隔离）共用同一个
    实例，在这里 ``undo()`` 会连目录隔离一起撤掉；而这条用例要的只是
    「这个小作用域结束就自动还原」。
    """
    original = job_limits.timeout_seconds_max()

    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(config.SCAN_LIMITS, "process_timeout", 5)
        assert job_limits.timeout_seconds_max() == 5
        # 上界与新值同一步生效：6 秒被拒、5 秒通过。
        with pytest.raises(ValueError):
            job_limits.normalize_timeout_seconds(6)
        assert job_limits.normalize_timeout_seconds(5) == 5

    assert job_limits.timeout_seconds_max() == original


def test_timeout_max_matches_the_live_config_value():
    """没有 monkeypatch 时也必须等于配置里的实时值 —— 抓「被固化成常量」的回归。"""
    assert job_limits.timeout_seconds_max() == max(
        job_limits.TIMEOUT_SECONDS_MIN, int(config.SCAN_LIMITS["process_timeout"])
    )


def test_timeout_max_never_goes_below_the_minimum(monkeypatch):
    """配置被压到 0 或负数时，上界退到下界而不是变成「任何超时都非法」。"""
    monkeypatch.setitem(config.SCAN_LIMITS, "process_timeout", 0)
    assert job_limits.timeout_seconds_max() == job_limits.TIMEOUT_SECONDS_MIN
    monkeypatch.setitem(config.SCAN_LIMITS, "process_timeout", -30)
    assert job_limits.timeout_seconds_max() == job_limits.TIMEOUT_SECONDS_MIN


def test_timeout_max_falls_back_when_config_is_broken(monkeypatch):
    """配置被改脏时兜底成 120，而不是让所有请求在解析阶段 500。"""
    monkeypatch.setitem(config.SCAN_LIMITS, "process_timeout", "soon")
    assert job_limits.timeout_seconds_max() == 120


# ── 严格解析：normalize_* ─────────────────────────────────


@pytest.mark.parametrize(
    "func",
    [job_limits.normalize_rate_limit, job_limits.normalize_timeout_seconds],
)
@pytest.mark.parametrize("blank", [None, "", "   "])
def test_normalize_treats_none_and_blank_as_unspecified(func, blank):
    """表单里没填的字段会以空串/None 过来，它等同于「没提」，不是 0。"""
    assert func(blank) is None


def test_normalize_accepts_integers_and_numeric_strings(monkeypatch):
    """数字串（表单过来的形态）必须被接受；带空格也要 strip 掉。"""
    monkeypatch.setitem(config.SCAN_LIMITS, "process_timeout", 30)
    assert job_limits.normalize_rate_limit("30") == 30
    assert job_limits.normalize_rate_limit(" 30 ") == 30
    assert job_limits.normalize_rate_limit(30) == 30
    assert job_limits.normalize_timeout_seconds("30") == 30
    assert job_limits.normalize_timeout_seconds(30) == 30


@pytest.mark.parametrize("flag", [True, False])
def test_normalize_rejects_bool(flag):
    """``bool`` 是 ``int`` 子类：放进来 ``True`` 会变成「1 秒超时 / 1 请求每秒」。"""
    with pytest.raises(ValueError):
        job_limits.normalize_rate_limit(flag)
    with pytest.raises(ValueError):
        job_limits.normalize_timeout_seconds(flag)


@pytest.mark.parametrize("bad", ["abc", "30.5", 30.5, [], {}, [30]])
def test_normalize_rejects_non_integer_shapes(bad):
    """「看起来像数字」不算数字：写错了必须报错，不能静默回退。"""
    with pytest.raises(ValueError):
        job_limits.normalize_rate_limit(bad)
    with pytest.raises(ValueError):
        job_limits.normalize_timeout_seconds(bad)


def test_rate_limit_bounds_are_inclusive_and_off_by_one_safe():
    """下界与上界本身通过，越过任意一侧报错（不静默夹到边界）。"""
    assert job_limits.normalize_rate_limit(job_limits.RATE_LIMIT_MIN) == job_limits.RATE_LIMIT_MIN
    assert job_limits.normalize_rate_limit(job_limits.RATE_LIMIT_MAX) == job_limits.RATE_LIMIT_MAX

    with pytest.raises(ValueError):
        job_limits.normalize_rate_limit(0)
    with pytest.raises(ValueError):
        job_limits.normalize_rate_limit(-1)
    with pytest.raises(ValueError):
        job_limits.normalize_rate_limit(job_limits.RATE_LIMIT_MAX + 1)


def test_timeout_bounds_follow_the_live_maximum(monkeypatch):
    """超时上界不是常量：钉住 30 之后 30 通过、31 报错、0 报错。"""
    monkeypatch.setitem(config.SCAN_LIMITS, "process_timeout", 30)
    assert job_limits.normalize_timeout_seconds(job_limits.TIMEOUT_SECONDS_MIN) == 1
    assert job_limits.normalize_timeout_seconds(30) == 30

    with pytest.raises(ValueError):
        job_limits.normalize_timeout_seconds(31)
    with pytest.raises(ValueError):
        job_limits.normalize_timeout_seconds(0)
    with pytest.raises(ValueError):
        job_limits.normalize_timeout_seconds(-1)


# ── 宽松解析：coerce_* ────────────────────────────────────


def test_coerce_returns_none_instead_of_raising():
    """读库里的历史脏值时不能抛：一个坏行不该让整个 worker 停摆。"""
    for bad in (True, False, "abc", "30.5", 30.5, [], {}, 0, -1, job_limits.RATE_LIMIT_MAX + 1):
        assert job_limits.coerce_rate_limit(bad) is None, bad
    for bad in (True, "abc", 0, -1, [], {}):
        assert job_limits.coerce_timeout_seconds(bad) is None, bad


def test_coerce_keeps_legal_values():
    assert job_limits.coerce_rate_limit("30") == 30
    assert job_limits.coerce_rate_limit(job_limits.RATE_LIMIT_MAX) == job_limits.RATE_LIMIT_MAX
    assert job_limits.coerce_timeout_seconds(job_limits.TIMEOUT_SECONDS_MIN) == 1
    # 空值仍是「不指定」，与非法值回退的 ``None`` 同形 —— 这是刻意的。
    assert job_limits.coerce_rate_limit(None) is None
    assert job_limits.coerce_timeout_seconds("   ") is None


# ── resolve_limits ────────────────────────────────────────


def test_resolve_with_both_empty_is_empty():
    """两项都没给 → ``is_empty``，执行期不注入任何覆盖。"""
    assert job_limits.resolve_limits().is_empty is True
    assert job_limits.resolve_limits(rate_limit=None, timeout_seconds=None).is_empty is True
    assert job_limits.resolve_limits(rate_limit="", timeout_seconds="   ").is_empty is True


def test_resolve_with_one_item_is_not_empty():
    """只给一项就不是空的：空判断必须是「两项都空」，不能是「任意一项空」。"""
    only_rate = job_limits.resolve_limits(rate_limit=5)
    assert only_rate.is_empty is False
    assert only_rate.rate_limit == 5
    assert only_rate.timeout_seconds is None

    only_timeout = job_limits.resolve_limits(timeout_seconds=job_limits.TIMEOUT_SECONDS_MIN)
    assert only_timeout.is_empty is False
    assert only_timeout.timeout_seconds == 1
    assert only_timeout.rate_limit is None


def test_resolve_raises_on_invalid_values():
    """请求体里写错就 400：与 ``core.pace.resolve_pace`` 同一取向。"""
    with pytest.raises(ValueError):
        job_limits.resolve_limits(rate_limit="abc")
    with pytest.raises(ValueError):
        job_limits.resolve_limits(rate_limit=job_limits.RATE_LIMIT_MAX + 1)
    with pytest.raises(ValueError):
        job_limits.resolve_limits(timeout_seconds=0)


# ── JobLimits 的形状 ──────────────────────────────────────


def test_to_dict_always_has_both_keys():
    """接口/事件 detail 的形状必须稳定：``None`` 也要出现（前端靠键存在渲染）。"""
    assert set(job_limits.JobLimits().to_dict()) == {"rate_limit", "timeout_seconds"}
    assert job_limits.JobLimits().to_dict() == {"rate_limit": None, "timeout_seconds": None}
    assert job_limits.JobLimits(rate_limit=20, timeout_seconds=7).to_dict() == {
        "rate_limit": 20,
        "timeout_seconds": 7,
    }


def test_to_runner_overrides_renames_timeout_but_not_rate_limit():
    """键名映射：``rate_limit`` 原样（subfinder/httpx 的 ``-rl``），
    ``timeout_seconds`` 必须变成 ``process_timeout``（modules/base.py 读的键）。"""
    overrides = job_limits.JobLimits(rate_limit=20, timeout_seconds=7).to_runner_overrides()
    assert overrides == {"rate_limit": 20, "process_timeout": 7}
    assert "timeout_seconds" not in overrides


def test_to_runner_overrides_omits_none_items():
    """``None`` 的项不能出现在覆盖里：写进 Runner config 会变成「显式指定」。"""
    assert job_limits.JobLimits(rate_limit=20).to_runner_overrides() == {"rate_limit": 20}
    assert job_limits.JobLimits(timeout_seconds=7).to_runner_overrides() == {"process_timeout": 7}
    assert job_limits.JobLimits().to_runner_overrides() == {}


def test_to_detail_omits_unspecified_items():
    """``to_detail()`` 与 ``to_dict()`` 刻意不同：事件 detail 是历史**事实**。

    没指定的项写 ``null`` 会让「这次到底有没有额外收紧」多一层解读；
    而 ``to_dict()`` 是接口出参，形状必须固定（前端按「两个键总在」取值）。
    """
    assert job_limits.JobLimits().to_detail() == {}
    assert job_limits.JobLimits(rate_limit=20).to_detail() == {"rate_limit": 20}
    assert job_limits.JobLimits(timeout_seconds=7).to_detail() == {"timeout_seconds": 7}
    assert job_limits.JobLimits(rate_limit=20, timeout_seconds=7).to_detail() == {
        "rate_limit": 20,
        "timeout_seconds": 7,
    }
    # 两者读的是同一个对象、同一批值，只是「空」的呈现不同。
    both = job_limits.JobLimits(rate_limit=20, timeout_seconds=7)
    assert set(both.to_dict()) == set(both.to_detail())


@pytest.mark.parametrize(
    "func,bad,field",
    [
        (job_limits.normalize_rate_limit, "abc", "rate_limit"),
        (job_limits.normalize_rate_limit, True, "rate_limit"),
        (job_limits.normalize_rate_limit, [1], "rate_limit"),
        (job_limits.normalize_timeout_seconds, "abc", "timeout_seconds"),
        (job_limits.normalize_timeout_seconds, True, "timeout_seconds"),
        (job_limits.normalize_timeout_seconds, {"v": 1}, "timeout_seconds"),
    ],
)
def test_format_errors_name_the_offending_field(func, bad, field):
    """格式错误的文案必须含字段名 —— ``core.application`` 靠它回 ``details["field"]``。

    少了字段名，调用方只能二选一（猜，或者报一个笼统的 400），实测后果是
    ``timeout_seconds="abc"`` 被报成 ``rate_limit`` 有问题：使用者盯着一个
    自己没填过的框找错。
    """
    with pytest.raises(ValueError) as excinfo:
        func(bad)
    assert field in str(excinfo.value)
    assert "必须是整数" in str(excinfo.value)


# ── limits_of_detail：从事件 detail 读回 ──────────────────


@pytest.mark.parametrize("detail", [None, "x", [], 5, ()])
def test_limits_of_detail_returns_empty_for_non_dict(detail):
    """detail 不是 dict 时给空 ``JobLimits``，不能抛。"""
    assert job_limits.limits_of_detail(detail) == job_limits.JobLimits()


def test_limits_of_detail_drops_dirty_values():
    """脏值逐项退化为「没指定」，而不是让读回失败。"""
    dirty = {"rate_limit": "abc", "timeout_seconds": -1}
    assert job_limits.limits_of_detail(dirty) == job_limits.JobLimits()
    assert job_limits.limits_of_detail({"rate_limit": True}).rate_limit is None


def test_limits_of_detail_reads_back_legal_values(monkeypatch):
    monkeypatch.setitem(config.SCAN_LIMITS, "process_timeout", 30)
    detail = {"rate_limit": "20", "timeout_seconds": 30}
    assert job_limits.limits_of_detail(detail) == job_limits.JobLimits(
        rate_limit=20, timeout_seconds=30
    )
    # 与 to_dict 互逆 —— 事件写出去的值必须能原样读回来。
    assert job_limits.limits_of_detail(job_limits.JobLimits(20, 30).to_dict()) == job_limits.JobLimits(20, 30)


# ── apply_to_runner：只能收紧，且不污染原 config ──────────


class _FakeRunner:
    """最小 Runner 替身：只带一个 ``config``，不构造任何真实工具。"""

    def __init__(self, config_value):
        self.config = config_value


def test_apply_to_runner_is_a_noop_without_limits():
    """``None`` 或空 ``JobLimits`` → ``False``，且 ``config`` 对象都没被替换。"""
    runner = _FakeRunner({"rate_limit": 7})
    shared = runner.config

    assert job_limits.apply_to_runner(runner, None) is False
    assert runner.config is shared

    assert job_limits.apply_to_runner(runner, job_limits.JobLimits()) is False
    assert runner.config is shared
    assert shared == {"rate_limit": 7}


def test_apply_to_runner_tolerates_a_runner_without_config():
    """测试替身没有 ``config``：收紧是策略、不是执行前提，必须静默返回 ``False``。"""

    class _Bare:
        pass

    assert job_limits.apply_to_runner(_Bare(), job_limits.JobLimits(rate_limit=20)) is False


def test_apply_to_runner_copies_config_and_does_not_mutate_the_original():
    """必须替换 ``config`` 而不是原地改。

    Runner 的 ``self.config`` 指向**模块级**配置对象；原地改会让同进程里后续
    所有任务（以及页面上的工具状态）都跟着变。
    """
    shared = {"threads": 50, "timeout": 10}
    runner = _FakeRunner(shared)

    assert job_limits.apply_to_runner(runner, job_limits.JobLimits(rate_limit=20)) is True
    assert runner.config["rate_limit"] == 20
    assert runner.config is not shared
    assert shared == {"threads": 50, "timeout": 10}
    assert runner.config["threads"] == 50


def test_apply_to_runner_refuses_to_loosen_rate_limit():
    """配置里已经更保守（3）时，请求写 50 不能把它顶回去。"""
    shared = {"rate_limit": 3}
    runner = _FakeRunner(shared)

    assert job_limits.apply_to_runner(runner, job_limits.JobLimits(rate_limit=50)) is False
    assert runner.config is shared
    assert runner.config["rate_limit"] == 3


def test_apply_to_runner_tightens_rate_limit():
    """配置里是 3、请求是 1：1 更保守，必须写进去（收紧方向要真的通）。"""
    runner = _FakeRunner({"rate_limit": 3})
    assert job_limits.apply_to_runner(runner, job_limits.JobLimits(rate_limit=1)) is True
    assert runner.config["rate_limit"] == 1


def test_apply_to_runner_tightens_process_timeout():
    """``timeout_seconds`` 落到 ``process_timeout`` 上，且同样只收紧。"""
    runner = _FakeRunner({"process_timeout": 300})
    assert job_limits.apply_to_runner(runner, job_limits.JobLimits(timeout_seconds=30)) is True
    assert runner.config["process_timeout"] == 30


def test_apply_to_runner_refuses_to_loosen_process_timeout():
    shared = {"process_timeout": 10}
    runner = _FakeRunner(shared)

    assert job_limits.apply_to_runner(runner, job_limits.JobLimits(timeout_seconds=60)) is False
    assert runner.config is shared
    assert runner.config["process_timeout"] == 10


@pytest.mark.parametrize("dirty", ["abc", None, 0, -1, True, [], {}])
def test_apply_to_runner_treats_dirty_value_as_missing_key(dirty):
    """config 里的脏值视为「没有该键」，会被请求值写入（否则脏值会永久挡住收紧）。"""
    runner = _FakeRunner({"rate_limit": dirty})

    assert job_limits.apply_to_runner(runner, job_limits.JobLimits(rate_limit=20)) is True
    assert runner.config["rate_limit"] == 20


# ── describe_limits：前端元数据 ───────────────────────────


def test_describe_limits_shape_is_stable():
    described = job_limits.describe_limits()
    assert set(described) == {"rate_limit", "timeout_seconds"}

    for key, item in described.items():
        assert set(item) == {"field", "min", "max", "label", "hint"}, key
        assert item["min"] <= item["max"], key
        assert item["label"], key
        assert item["hint"], key
        # 标签与说明必须是中文：否则页面会显示出裸的英文 key。
        assert any("\u4e00" <= ch <= "\u9fff" for ch in item["label"]), key
        assert any("\u4e00" <= ch <= "\u9fff" for ch in item["hint"]), key

    assert described["rate_limit"]["field"] == job_limits.FIELD_RATE_LIMIT
    assert described["timeout_seconds"]["field"] == job_limits.FIELD_TIMEOUT_SECONDS


def test_describe_limits_max_follows_the_live_config(monkeypatch):
    """元数据里的超时上界也要跟着运行时配置走，不能是 import 期的快照。"""
    monkeypatch.setitem(config.SCAN_LIMITS, "process_timeout", 30)
    described = job_limits.describe_limits()
    assert described["timeout_seconds"]["max"] == 30
    assert described["rate_limit"]["max"] == job_limits.RATE_LIMIT_MAX