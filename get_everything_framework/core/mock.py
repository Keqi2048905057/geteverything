"""Mock 执行结果（M2 最小版，M4 升级为完整 RunnerResult）。

本机联调版的硬性约束：

* 默认使用 mock 工具模式完成自动化测试（方案第 2.3 节第 9 条）；
* 真实外部工具执行必须显式开启（第 8 条）。

因此 ``mode=mock`` 时**绝不调用真实外部工具**，由本模块产出确定性的假数据；
``mode=real`` 时才走 ``modules/`` 里的真实 runner。

M4 会把这里的 ``MockOutcome`` 升级为方案第 6.2 节要求的完整结构化
``RunnerResult``（含 exit_code / duration_ms / command_preview / raw_artifact_id），
但错误码口径现在就按方案对齐，避免后面改语义。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from core.errors import ErrorCode

# mock 场景名 → 行为。默认 ``success``。
SCENARIO_SUCCESS = "success"
SCENARIO_EMPTY = "empty"
SCENARIO_TOOL_NOT_FOUND = "tool_not_found"
SCENARIO_TIMEOUT = "timeout"
SCENARIO_NON_ZERO_EXIT = "non_zero_exit"
SCENARIO_PARSE_ERROR = "parse_error"
SCENARIO_PARTIAL = "partial"

SCENARIOS = (
    SCENARIO_SUCCESS,
    SCENARIO_EMPTY,
    SCENARIO_TOOL_NOT_FOUND,
    SCENARIO_TIMEOUT,
    SCENARIO_NON_ZERO_EXIT,
    SCENARIO_PARSE_ERROR,
    SCENARIO_PARTIAL,
)

# 场景 → (状态, 错误码)。成功场景的 error_code 为 None。
_SCENARIO_ERRORS = {
    SCENARIO_TOOL_NOT_FOUND: (ErrorCode.TOOL_NOT_FOUND, "mock: 工具未安装"),
    SCENARIO_TIMEOUT: (ErrorCode.TIMEOUT, "mock: 执行超时"),
    SCENARIO_NON_ZERO_EXIT: (ErrorCode.UNKNOWN_ERROR, "mock: 工具返回非零退出码"),
    SCENARIO_PARSE_ERROR: (ErrorCode.PARSE_ERROR, "mock: 输出解析失败"),
    SCENARIO_PARTIAL: (ErrorCode.PARTIAL_SUCCESS, "mock: 部分目标成功"),
}


@dataclass
class MockOutcome:
    """一次 mock 执行的结果。

    Attributes:
        status: ``success`` / ``partial`` / ``failed`` / ``timeout``。
        results: 发现的条目（失败场景为空）。
        error_code: 失败时的统一错误码，成功且有条目时为 None。
        error_message: 面向使用者的说明。
        scenario: 本次使用的场景名，便于测试断言。
    """

    status: str
    results: list[str] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    scenario: str = SCENARIO_SUCCESS

    @property
    def found_count(self) -> int:
        return len(self.results)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "found_count": self.found_count,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "scenario": self.scenario,
            "mock": True,
        }


def normalize_scenario(value: str | None) -> str:
    """把请求里的场景名规范化为受支持的场景；未知值回落到 ``success``。"""
    scenario = (value or "").strip().lower()
    return scenario if scenario in SCENARIOS else SCENARIO_SUCCESS


def _deterministic_hosts(target: str) -> list[str]:
    """为某个目标生成稳定的假子域列表。

    使用目标哈希决定条数（1..3），保证同一目标每次得到同样的结果，
    便于对比两次扫描的 diff（M5）。
    """
    digest = hashlib.sha256(target.encode("utf-8")).hexdigest()
    count = int(digest[:2], 16) % 3 + 1
    labels = ["www", "api", "cdn"]
    return [f"{labels[i]}.{target}" for i in range(count)]


def run_mock(tool_name: str, target: str, scenario: str | None = None) -> MockOutcome:
    """在不触碰外部网络的前提下产出一次模拟执行结果。

    Args:
        tool_name: 工具名（仅用于生成结果标签）。
        target: 目标（域名或 IP）。
        scenario: 场景名，见 :data:`SCENARIOS`；缺省为 ``success``。

    Returns:
        MockOutcome: 结构化结果，失败场景带 ``error_code``。
    """
    chosen = normalize_scenario(scenario)

    if chosen == SCENARIO_SUCCESS:
        return MockOutcome(status="success", results=_deterministic_hosts(target), scenario=chosen)

    if chosen == SCENARIO_EMPTY:
        # 跑通但零结果：必须与「失败」区分开，且按方案 M4 的口径给出
        # ``no_results``（而不是笼统的 None），前端才能显示「未发现结果」。
        return MockOutcome(
            status="success",
            results=[],
            error_code=ErrorCode.NO_RESULTS,
            error_message="mock: 执行成功但未发现任何结果",
            scenario=chosen,
        )

    if chosen == SCENARIO_PARTIAL:
        hosts = _deterministic_hosts(target)
        code, message = _SCENARIO_ERRORS[SCENARIO_PARTIAL]
        return MockOutcome(
            status="partial",
            results=hosts[:1],
            error_code=code,
            error_message=message,
            scenario=chosen,
        )

    code, message = _SCENARIO_ERRORS[chosen]
    status = "timeout" if chosen == SCENARIO_TIMEOUT else "failed"
    return MockOutcome(status=status, results=[], error_code=code, error_message=message, scenario=chosen)
