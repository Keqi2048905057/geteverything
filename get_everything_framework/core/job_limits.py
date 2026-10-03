"""单任务的限速与超时（**只能收紧**，不能放松）。

## 为什么需要这一层

``core.pace`` 只回答「这个任务属于哪一档节奏」，档位是**离散**的两个值。
下一阶段规划方案第 14 节 Phase 3 要求「限速配置 / 超时配置」，即让使用者
能对**单个任务**给出更细的数字（例如「这次只给 1 请求/秒」「单步最多 30 秒」）。

## 本模块唯一的硬规则：只能收紧

一旦允许请求体指定限速与超时，就多了一条**能放松**的输入路径 —— 而这正是
``core.pace`` 刻意避免的形态（``resolve_pace`` 里 ``light`` 永远赢）。
因此本模块不提供任何「调高」的方向：

* **数值越小越保守**：限速是「每秒请求上限」，超时是「单步最多跑多久」，
  两者都是**取较小值**才是收紧；
* :func:`JobLimits.to_runner_overrides` 给出的只是**候选值**，真正落进
  Runner ``config`` 的合并由 :func:`apply_to_runner` 用 ``min`` 完成；
  另一个方向 —— 工具 ``config`` 里没有该键时，候选值本身也不能比工具的
  默认速率还大（见 :data:`RATE_LIMIT_MAX`）；
* 超出绝对上界一律 **400 报错**，不静默夹到边界 —— 写了 ``rate_limit=99999``
  却拿到 1000，与 ``core.pace`` 里「写了拼错的档位却拿到常规档」是同一种
  危险错法：使用者以为自己已经设好了。

## 与既有边界的关系

本模块**不是**安全闸门：Scope 越界、``active_scan``、``GEF_ALLOW_REAL_SCAN``、
公网工具白名单仍然各自独立判定。它也**不能**放松 ``pace`` ——
``light`` 档的预算与请求值取 ``min``，因此「低频资产发现」不会被一次请求
改回高频。它只回答一个问题：「这一步每秒最多发几个请求、最多跑多少秒」。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from config import SCAN_LIMITS

#: 每秒请求上限的绝对上界。
#:
#: 上界必须**低于**工具的默认速率，否则「只能收紧」就是一句空话：
#: 请求里写 ``rate_limit=1000`` 而工具自己的默认值是 150，实际结果是
#: 把限速**放松**了 —— 使用者却以为自己限了速。
#: 低频档实际用的是 3（subfinder）/ 10（httpx），100 足够覆盖所有合理用法，
#: 因此这条上界不会误伤正常用法，只拦住「以为限了速、其实放松了」的错值。
RATE_LIMIT_MAX = 100
RATE_LIMIT_MIN = 1

TIMEOUT_SECONDS_MIN = 1

#: 环境变量覆盖的字段名（与请求体字段同名，便于排障时一一对应）。
FIELD_RATE_LIMIT = "rate_limit"
FIELD_TIMEOUT_SECONDS = "timeout_seconds"


def timeout_seconds_max() -> int:
    """单步超时的绝对上界（= 本机进程级上限）。

    刻意**每次读** :data:`config.SCAN_LIMITS` 而不是在 import 期固化：
    ``GEF_PROCESS_TIMEOUT`` 可以在运行期被改（测试就靠 monkeypatch 把它压到
    几秒来验证「卡死的工具会被判 timeout」），固化会让那条路径失效。
    """
    try:
        limit = int(SCAN_LIMITS["process_timeout"])
    except (KeyError, TypeError, ValueError):  # pragma: no cover - 配置被改坏时的兜底
        return 120
    return max(TIMEOUT_SECONDS_MIN, limit)


def _as_int(value: Any, field: str) -> int | None:
    """把外部值转成整数；``None`` / 空串返回 ``None``，其余非法抛 ``ValueError``。

    刻意**不**接受 ``"30.5"`` / ``True`` 这类「看起来像数字」的值：
    ``bool`` 是 ``int`` 的子类，放进来会让 ``True`` 变成 1 秒超时。

    报错文案**必须带上字段名**：调用方（``core.application``）按文案把错误定位到
    具体字段，再据此回 ``details["field"]`` 给前端。少了字段名时它只能猜，
    于是 ``timeout_seconds="abc"`` 会被报成 ``rate_limit`` 有问题 ——
    使用者盯着一个自己没填过的框找错。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} 必须是整数")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError as exc:
            raise ValueError(f"{field} 必须是整数") from exc
    if isinstance(value, int):
        return value
    raise ValueError(f"{field} 必须是整数")


def normalize_rate_limit(value: Any) -> int | None:
    """规范化「每秒请求上限」；``None`` / 空串表示不指定。

    Raises:
        ValueError: 非整数，或超出 ``[RATE_LIMIT_MIN, RATE_LIMIT_MAX]``。
    """
    number = _as_int(value, FIELD_RATE_LIMIT)
    if number is None:
        return None
    if number < RATE_LIMIT_MIN or number > RATE_LIMIT_MAX:
        raise ValueError(f"rate_limit 取值范围 {RATE_LIMIT_MIN} ~ {RATE_LIMIT_MAX}")
    return number


def normalize_timeout_seconds(value: Any) -> int | None:
    """规范化「单步超时（秒）」；``None`` / 空串表示不指定。

    Raises:
        ValueError: 非整数，或超出 ``[TIMEOUT_SECONDS_MIN, timeout_seconds_max()]``。
    """
    number = _as_int(value, FIELD_TIMEOUT_SECONDS)
    if number is None:
        return None
    maximum = timeout_seconds_max()
    if number < TIMEOUT_SECONDS_MIN or number > maximum:
        raise ValueError(f"timeout_seconds 取值范围 {TIMEOUT_SECONDS_MIN} ~ {maximum}")
    return number


def coerce_rate_limit(value: Any) -> int | None:
    """宽松归一（读库里的历史值用）：非法一律回退 ``None``。"""
    try:
        return normalize_rate_limit(value)
    except ValueError:
        return None


def coerce_timeout_seconds(value: Any) -> int | None:
    """宽松归一（读库里的历史值用）：非法一律回退 ``None``。"""
    try:
        return normalize_timeout_seconds(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class JobLimits:
    """一个任务显式指定的限速与超时（两项都可为空 = 不干预）。

    Attributes:
        rate_limit: 每秒请求上限；``None`` 表示沿用所选节奏档的默认值。
        timeout_seconds: 单步超时（秒）；``None`` 表示沿用「工具配置与本机上限
            的较小值」这一历史口径。
    """

    rate_limit: int | None = None
    timeout_seconds: int | None = None

    @property
    def is_empty(self) -> bool:
        """两项都没指定时为 ``True``（此时执行期不注入任何额外覆盖）。"""
        return self.rate_limit is None and self.timeout_seconds is None

    def to_dict(self) -> dict:
        """接口/事件 detail 用的形状（键名与请求体字段一致）。"""
        return {
            FIELD_RATE_LIMIT: self.rate_limit,
            FIELD_TIMEOUT_SECONDS: self.timeout_seconds,
        }

    def to_detail(self) -> dict:
        """``job.created`` 事件 detail 里的形状：**只写实际指定的项**。

        与 :meth:`to_dict` 刻意分开：``to_dict`` 要固定形状（前端与测试按
        「这两个键总在」取值），而事件 detail 是历史事实 —— 没指定的项写
        ``null`` 只会让「这次到底有没有额外收紧」多一层解读。
        """
        detail: dict[str, int] = {}
        if self.rate_limit is not None:
            detail[FIELD_RATE_LIMIT] = self.rate_limit
        if self.timeout_seconds is not None:
            detail[FIELD_TIMEOUT_SECONDS] = self.timeout_seconds
        return detail

    def to_runner_overrides(self) -> dict[str, int]:
        """转成可并入 Runner ``config`` 的候选覆盖（``None`` 的项不出现）。

        ``rate_limit`` 是 ``subfinder -rl`` / ``httpx -rl`` 读的键；
        ``process_timeout`` 是 ``modules/base.py:_timeout_seconds()`` 读的键。
        这里只做**键名映射**，取 ``min`` 的收紧动作在
        :func:`core.pace.apply_to_runner` 里做（那里才拿得到工具自身配置）。
        """
        overrides: dict[str, int] = {}
        if self.rate_limit is not None:
            overrides["rate_limit"] = self.rate_limit
        if self.timeout_seconds is not None:
            overrides["process_timeout"] = self.timeout_seconds
        return overrides


def resolve_limits(*, rate_limit: Any = None, timeout_seconds: Any = None) -> JobLimits:
    """解析请求体里的限速/超时。

    严格版本：非法值抛 ``ValueError``，由调用方转成 400。
    与 ``core.pace.resolve_pace`` 同一取向 —— 参数写错就报错，不静默回退。

    Raises:
        ValueError: 任一项不是整数或超出允许范围。
    """
    return JobLimits(
        rate_limit=normalize_rate_limit(rate_limit),
        timeout_seconds=normalize_timeout_seconds(timeout_seconds),
    )


def limits_of_detail(detail: Any) -> JobLimits:
    """从 ``job.created`` 事件 detail 里读回限速/超时（宽松，脏数据退化为空）。"""
    if not isinstance(detail, dict):
        return JobLimits()
    return JobLimits(
        rate_limit=coerce_rate_limit(detail.get(FIELD_RATE_LIMIT)),
        timeout_seconds=coerce_timeout_seconds(detail.get(FIELD_TIMEOUT_SECONDS)),
    )


def _positive_int(value: Any) -> int | None:
    """取正整数值；缺失 / 非法 / ``bool`` / 非正数一律返回 ``None``。

    ``bool`` 必须排除：``True`` 是 ``int`` 的子类，放进来会变成「1 请求/秒」
    这种把人吓一跳的收紧。
    """
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def apply_to_runner(runner: Any, limits: "JobLimits | None") -> bool:
    """把单任务的限速 / 超时**收紧**写进**这一个** Runner 实例的 ``config`` 副本。

    与 :func:`core.pace.apply_to_runner` 同一形态（替换 ``config`` 而不是原地改，
    返回「是否真的改写了」），但合并规则是 ``min`` 而不是覆盖：

    * 工具配置里已经有更保守的值（例如低频档刚写进去的 ``rate_limit=10``），
      请求里的 ``rate_limit=50`` **不能**把它顶回去 —— 这正是「只能收紧」的
      可执行口径，也是「低频资产发现」不会被一次请求改回高频的原因；
    * 配置里没有这个键时才写入请求值，且 :data:`RATE_LIMIT_MAX` 已经保证
      它比工具自身的默认速率更保守。

    Args:
        runner: 已构造的 Runner 实例（需要 ``config``）。
        limits: 解析好的限速 / 超时；``None`` 或两项都空时**一个键都不动**。

    Returns:
        bool: 是否真的改写了 ``config``（``False`` 表示这次任务没有额外收紧）。
    """
    if limits is None or limits.is_empty:
        return False
    current = getattr(runner, "config", None)
    if not isinstance(current, dict):
        # 假 Runner（测试替身）没有 config：收紧是策略，不是执行前提。
        return False

    merged = dict(current)
    changed = False
    for key, value in limits.to_runner_overrides().items():
        existing = _positive_int(merged.get(key))
        if existing is None or value < existing:
            merged[key] = value
            changed = True
    if not changed:
        return False
    runner.config = merged
    return True


def describe_limits() -> dict:
    """给前端渲染输入框用的元数据（上下界与一句话说明，中文只维护这一份）。"""
    return {
        "rate_limit": {
            "field": FIELD_RATE_LIMIT,
            "min": RATE_LIMIT_MIN,
            "max": RATE_LIMIT_MAX,
            "label": "每秒请求上限",
            "hint": "数字越小越慢；留空表示沿用所选节奏档的默认值。只能收紧，不能放松。",
        },
        "timeout_seconds": {
            "field": FIELD_TIMEOUT_SECONDS,
            "min": TIMEOUT_SECONDS_MIN,
            "max": timeout_seconds_max(),
            "label": "单步超时（秒）",
            "hint": "数字越小越早终止；留空表示沿用工具配置与本机上限的较小值。只能收紧。",
        },
    }
