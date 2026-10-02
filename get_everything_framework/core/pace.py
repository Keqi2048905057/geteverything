"""扫描节奏（pace）—— 「低频」是可执行约束，不是界面上的标签。

## 为什么需要这一层

``core.tool_registry`` 已经回答了「哪些工具允许打公网」，但没有回答
「允许打的工具应该打多快」。而后者才是用户最关心的一句话：
**拿到授权的公网目标不等于可以对它施加任意流量。**

因此在策略模板（``ScanStrategy``）之上再叠一档 ``pace``：

* ``normal`` —— 常规。沿用工具自身配置（``HTTPX_CONFIG["threads"]`` 等），
  不额外降速。这一档与引入本模块之前的行为**逐字节一致**，因此
  ``POST /api/jobs`` 这类不带 Profile 的历史调用方行为不变。
* ``light``  —— 低频。降低并发与请求速率，并在真实步骤之间留出间隔。

## 单一事实源与「只能收紧」

* 档位只有 ``PACE_LEVELS`` 两个值，标签集中在 ``PACE_LABELS``；
* :func:`resolve_pace` 只允许**变保守**（``light`` 可以覆盖 ``normal``，
  反之不行）—— 请求体因此无法把「低频资产发现」悄悄改成常规档；
* 具体预算集中在 ``LIGHT_TOOL_BUDGET``，执行期由 ``jobs.executor`` 注入
  Runner 的 ``config`` 副本，**不修改模块级配置对象**。

## 与既有边界的关系

节奏不是安全闸门：Scope 越界、``active_scan``、``GEF_ALLOW_REAL_SCAN``、
公网工具白名单仍然各自独立判定，本模块**不判定**这些，也**不放松**它们。
它只回答一个问题：「这一步应该等多长时间、用多低的并发」。
"""

from __future__ import annotations

import os
from typing import Any

# ── 档位 ──────────────────────────────────────────────────

PACE_LIGHT = "light"
PACE_NORMAL = "normal"

PACE_LEVELS = (PACE_LIGHT, PACE_NORMAL)

# 中文说明只维护一份：页面、错误消息、接口文档都从这里取。
# 短标签给卡片式展示（一行放得下），长描述给「点开才看」的解释区。
PACE_LABELS = {
    PACE_LIGHT: "低频",
    PACE_NORMAL: "常规",
}

PACE_DESCRIPTIONS = {
    PACE_LIGHT: "降低并发与请求速率，并在真实步骤之间留出间隔。",
    PACE_NORMAL: "使用工具默认并发与速率，不额外等待。",
}

#: 缺省档位 —— 必须与「引入本模块之前」的行为一致：不等待、不覆盖工具参数。
DEFAULT_PACE = PACE_NORMAL

#: ``light`` 档在**真实**步骤之间留出的间隔秒数。
#: 走环境变量是为了让自动化测试把它压到 0（``tests/conftest.py``），
#: 而不是让每个用例都为「礼貌间隔」付真实墙钟时间。
DEFAULT_LIGHT_STEP_DELAY_SEC = 1.5
LIGHT_STEP_DELAY_ENV = "GEF_PACE_LIGHT_STEP_DELAY_SEC"

#: 每档的工具预算覆盖。键是工具名，值是 ``build_command`` 认得的配置键：
#:
#: * ``threads``      —— subfinder ``-t`` / httpx ``-threads``（并发）；
#: * ``rate_limit``   —— subfinder ``-rl`` / httpx ``-rl``（每秒请求上限）。
#:
#: 只列**允许公网**的轻量工具；高风险工具压根进不了公网白名单
#: （见 ``core.tool_registry.assert_tools_internet_allowed``），
#: 因此这里不需要、也不应该为它们定义预算。
LIGHT_TOOL_BUDGET: dict[str, dict[str, int]] = {
    # subfinder 默认并发 50：低频档压到 5，并把每秒请求限到 3。
    "subfinder": {"threads": 5, "rate_limit": 3},
    # httpx 默认并发 50、每秒 150 请求：低频档压到 5 / 10。
    "httpx": {"threads": 5, "rate_limit": 10},
}

#: ``normal`` 档不覆盖任何参数。
NORMAL_TOOL_BUDGET: dict[str, dict[str, int]] = {}


def normalize_pace(value: object) -> str:
    """把外部输入归一为合法档位；非法值抛 ``ValueError``。

    「非法值报错」而不是「静默回退」是刻意的：如果使用者写了 ``"low"``
    这类拼错的档位却拿到常规档，他会以为自己已经用了最保守的档。
    读回历史数据请用宽松的 :func:`coerce_pace`。
    """
    if isinstance(value, str):
        key = value.strip().lower()
        if not key:
            raise ValueError("pace 不能为空字符串")
        if key in PACE_LEVELS:
            return key
    raise ValueError(f"pace 仅支持 {' / '.join(PACE_LEVELS)}")


def coerce_pace(value: object, default: str = DEFAULT_PACE) -> str:
    """宽松归一：非法或缺失一律回退 ``default``。

    给「从库里读回来的值」用 —— 数据库可能被手工改脏，那时退化为缺省档
    比让整个 worker 抛异常停摆更合理。
    """
    try:
        return normalize_pace(value)
    except ValueError:
        return default


def resolve_pace(profile_pace: object = None, requested: object = None) -> str:
    """结合模板档位与请求档位，得出最终档位（**只能收紧**）。

    Args:
        profile_pace: 策略模板自带的档位；``None`` 时按缺省档。
        requested: 请求体里显式给出的档位；``None`` 表示不干预。

    Returns:
        str: 最终档位。``light`` 优先级高于 ``normal``。

    Raises:
        ValueError: ``requested`` 给了非法值（模板档位非法时按缺省档处理，
            因为那是仓库内的常量，不该由一次 HTTP 请求把它暴露成 500）。
    """
    base = coerce_pace(profile_pace)
    if requested is None:
        return base
    if isinstance(requested, str) and not requested.strip():
        # 空串等同于「没提」——表单里未选中的字段会以空串过来。
        return base
    want = normalize_pace(requested)
    return PACE_LIGHT if PACE_LIGHT in (base, want) else PACE_NORMAL


def strictest(*paces: object) -> str:
    """取最保守的一档（任一为 ``light`` 即 ``light``）。"""
    return PACE_LIGHT if PACE_LIGHT in {coerce_pace(item) for item in paces} else PACE_NORMAL


def light_step_delay_seconds() -> float:
    """``light`` 档的步骤间隔秒数（读环境变量，便于测试归零）。"""
    raw = os.getenv(LIGHT_STEP_DELAY_ENV)
    if raw is None or not str(raw).strip():
        return DEFAULT_LIGHT_STEP_DELAY_SEC
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_LIGHT_STEP_DELAY_SEC
    return value if value > 0 else 0.0


def step_delay_for(pace: object) -> float:
    """某档位在**真实**步骤之间应等待的秒数。

    ``normal`` 恒为 0：这是「历史行为不变」的保证 —— 不带 Profile 的任务
    （如 ``POST /api/jobs``）不会因为本模块多出任何等待。
    """
    return light_step_delay_seconds() if coerce_pace(pace) == PACE_LIGHT else 0.0


def tool_overrides_for(pace: object, tool_name: str) -> dict[str, int]:
    """某档位下该工具应使用的配置覆盖（可直接并入 Runner 的 ``config``）。

    返回空字典表示「沿用工具自身配置」。调用方必须**复制** ``config``
    再合并，绝不能原地改 Runner 的 ``config``（它是模块级配置对象的引用，
    原地改会污染同一进程内后续所有任务与页面上的工具状态）。
    """
    budget = LIGHT_TOOL_BUDGET if coerce_pace(pace) == PACE_LIGHT else NORMAL_TOOL_BUDGET
    return dict(budget.get(tool_name, {}))


def apply_to_runner(runner: Any, pace: object) -> bool:
    """把某档位的工具预算写进**这一个** Runner 实例的 ``config`` 副本。

    在**构造之后**覆盖而不是在构造之前传参，是为了让
    ``jobs.executor`` 继续调用同一个 ``modules.registry.build_runner``
    —— 那条调用是测试替换真实 Runner 的接缝（``monkeypatch.setattr``），
    换一条构造路径会让假 Runner 失效、真的去跑外部命令。

    Args:
        runner: 已构造的 Runner 实例（需要 ``config`` / ``tool_name``）。
        pace: 节奏档位。

    Returns:
        bool: 是否真的改写了 ``config``（``False`` 表示该档位对该工具无预算）。
    """
    overrides = tool_overrides_for(pace, getattr(runner, "tool_name", ""))
    if not overrides:
        return False
    runner.config = dict(getattr(runner, "config", {}), **overrides)
    return True


def describe(pace: object) -> dict:
    """给前端/接口用的档位元数据。"""
    key = coerce_pace(pace)
    return {
        "pace": key,
        "pace_label": PACE_LABELS[key],
        "pace_description": PACE_DESCRIPTIONS[key],
        "step_delay_seconds": step_delay_for(key),
    }


def list_paces() -> list[dict]:
    """列出全部档位（供 ``/api/scan-center`` 解释「低频」到底是什么）。"""
    return [describe(key) for key in PACE_LEVELS]
