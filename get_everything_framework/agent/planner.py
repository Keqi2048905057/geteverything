from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .intent import UserIntent
from .strategy_templates import render_src_collection_route


@dataclass
class PlanStep:
    id: str
    tool: str
    args: Dict[str, Any]
    description: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "tool": self.tool,
            "args": self.args,
            "description": self.description,
        }


@dataclass
class AgentPlan:
    target: Optional[str]
    strategy: str
    requires_confirmation: bool = True
    steps: List[PlanStep] = field(default_factory=list)
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "strategy": self.strategy,
            "requires_confirmation": self.requires_confirmation,
            "steps": [step.to_dict() for step in self.steps],
            "message": self.message,
        }


def strategy_message(message: str) -> AgentPlan:
    return AgentPlan(target=None, strategy=message, requires_confirmation=False, steps=[], message=message)


def build_passive_plan(target: Optional[str] = None) -> AgentPlan:
    return AgentPlan(
        target=target,
        strategy=render_src_collection_route(target=target, passive_only=True),
        requires_confirmation=False,
        steps=[],
    )


def build_uploaded_file_plan(intent: UserIntent, uploaded_file: Optional[Dict[str, Any]]) -> AgentPlan:
    if not uploaded_file:
        return strategy_message("我没有找到最近上传的目标文件，请先上传 .txt 或 .csv 目标列表。")

    return AgentPlan(
        target=f"上传文件：{uploaded_file['file_path']}",
        strategy="批量目标子域名收集",
        requires_confirmation=True,
        steps=[
            PlanStep(
                id="subdomain",
                tool="subdomain",
                args={"file_path": uploaded_file["file_path"], "tool": "subfinder"},
                description=f"对上传文件中的 {uploaded_file['target_count']} 个目标执行子域名收集",
            )
        ],
    )


def _without_excluded_tools(requested_tools: List[str], excluded_tools: List[str]) -> List[str]:
    excluded = set(excluded_tools or [])
    return [tool for tool in requested_tools or [] if tool not in excluded]


def build_plan(intent: UserIntent, uploaded_context: Optional[Dict[str, Any]] = None) -> Optional[AgentPlan]:
    uploaded_context = uploaded_context or {}

    if intent.intent_type == "strategy_only":
        if intent.passive_only:
            return build_passive_plan(intent.target)
        return AgentPlan(
            target=intent.target,
            strategy=render_src_collection_route(target=intent.target, passive_only=False),
            requires_confirmation=False,
            steps=[],
        )

    if intent.intent_type == "set_target":
        return AgentPlan(
            target=intent.target,
            strategy=f"已将目标设置为 {intent.target}",
            requires_confirmation=False,
            steps=[],
            message=f"已将目标设置为 `{intent.target}`。你可以继续回复：\n- 存活探测\n- 查看已有结果\n- 导出为 CSV\n- 只做被动信息收集",
        )

    if intent.intent_type == "uploaded_file_scan":
        return build_uploaded_file_plan(intent, uploaded_context)

    if intent.intent_type == "export_results":
        fmt = intent.export_format or "csv"
        return AgentPlan(
            target=intent.target,
            strategy="导出已有结果",
            requires_confirmation=False,
            steps=[
                PlanStep(
                    id="export_results",
                    tool="export_results",
                    args={"domain": intent.target, "format": fmt},
                    description=f"导出结果为 {fmt.upper()} 文件",
                )
            ],
        )

    if intent.intent_type == "view_existing_results":
        return AgentPlan(
            target=intent.target,
            strategy="查看已有结果",
            requires_confirmation=False,
            steps=[
                PlanStep(
                    id="view_results",
                    tool="view_results",
                    args={"domain": intent.target, "limit": 20},
                    description="查看本地数据库中已有结果",
                )
            ],
        )

    if intent.intent_type == "probe_existing_subdomains":
        tech_label = "开启" if intent.need_tech_stack else "关闭"
        return AgentPlan(
            target=intent.target,
            strategy=(
                f"基于已有子域名执行 Web 探测，不会重新收集子域名。"
                f"技术栈识别：{tech_label}。执行前需要确认。"
            ),
            requires_confirmation=True,
            steps=[
                PlanStep(
                    id="httpx",
                    tool="httpx",
                    args={
                        "domain": intent.target,
                        "source": "existing_subdomains",
                        "tech_detect": bool(intent.need_tech_stack),
                    },
                    description="读取数据库中的已有子域名并执行 httpx 存活探测",
                ),
                PlanStep(
                    id="summary",
                    tool="summary",
                    args={"domain": intent.target},
                    description="汇总结果与保存位置",
                ),
            ],
        )

    if intent.intent_type == "subdomain_scan":
        tools = _without_excluded_tools(intent.requested_tools or ["subdomain"], intent.excluded_tools)
        if "subdomain" not in tools:
            tools = ["subdomain"]
        scan_tool = "subfinder"
        return AgentPlan(
            target=intent.target,
            strategy="域名子域名收集",
            requires_confirmation=intent.needs_confirmation,
            steps=[
                PlanStep(
                    id="subdomain",
                    tool="subdomain",
                    args={"domain": intent.target, "tool": scan_tool},
                    description=f"使用 {scan_tool} 收集 {intent.target} 的子域名",
                )
            ],
        )

    if intent.intent_type == "web_probe":
        tech_label = "并识别技术栈" if intent.need_tech_stack else ""
        return AgentPlan(
            target=intent.target,
            strategy=f"Web 存活探测{tech_label}",
            requires_confirmation=intent.needs_confirmation,
            steps=[
                PlanStep(
                    id="httpx",
                    tool="httpx",
                    args={"domain": intent.target, "tech_detect": bool(intent.need_tech_stack)},
                    description=f"对 {intent.target} 执行 httpx 存活探测",
                )
            ],
        )

    return None
