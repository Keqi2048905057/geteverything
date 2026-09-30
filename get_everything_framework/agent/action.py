import ipaddress
import json
import os
import re
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from exporter import export_results, gather_export_rows
from modules.httpx import HttpxRunner
from storage import ScanResultStore, TOOL_DATABASES
from tool_runner import run_tools

from .intent import UserIntent, analyze_intent, extract_domain
from .plan_state import apply_user_intervention, is_cancel, is_confirm, is_meaningful_new_intent, is_plan_modification
from .planner import AgentPlan, build_passive_plan, build_plan
from .system_prompt import SYSTEM_PROMPT
from .target_ranker import rank_subdomains


class AgentAction:
    RATE_LIMIT_CACHE: Dict[str, float] = {}
    DOMAIN_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
    DATABASE_QUERY_KEYWORDS = (
        "数据库",
        "database",
        "database file",
        "db",
        "sqlite",
        "保存路径",
        "保存位置",
        "存在哪",
        "storage path",
        "save path",
        "数据库文件",
        "db_path",
    )

    def __init__(
        self,
        store: Optional[ScanResultStore] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        client: Optional[Any] = None,
        max_history_messages: int = 30,
        debug: bool = False,
        pending_plan: Optional[Dict[str, Any]] = None,
        uploaded_context: Optional[Dict[str, Any]] = None,
        context_state: Optional[Dict[str, Any]] = None,
    ):
        self.store = store or ScanResultStore()
        self.client = client
        self.max_history_messages = max(8, max_history_messages)
        self.debug = debug
        self.pending_plan = pending_plan
        self.uploaded_context = uploaded_context or {}
        self.context = {
            "mode": None,
            "org": None,
            "target": None,
            "last_target": None,
            "last_menu": None,
            "strategy_context": None,
        }
        if context_state:
            for key in self.context:
                self.context[key] = context_state.get(key, self.context[key])
        self.steps: List[Dict[str, Any]] = []

        self.min_tool_interval_sec = int(os.getenv("AGENT_TOOL_MIN_INTERVAL_SEC", "8"))
        self.blocked_domains = self._load_set_env("AGENT_BLOCKED_DOMAINS", defaults={"localhost", "localdomain"})
        self.blocked_suffixes = self._load_set_env("AGENT_BLOCKED_SUFFIXES", defaults={".local", ".lan", ".internal"})
        self.allowed_suffixes = self._load_set_env("AGENT_ALLOWED_SUFFIXES", defaults=set())
        self.conversation_history = self._normalize_history(conversation_history)

        self.available_tools = {
            "subdomain": {
                "description": "收集子域名",
                "params": {"domain": "string", "tool": "amass|subfinder|dnsx", "file_path": "string(optional)"},
                "handler": self._tool_subdomain,
            },
            "summary": {
                "description": "查看汇总信息",
                "params": {"domain": "string(optional)"},
                "handler": self._tool_summary,
            },
            "view_results": {
                "description": "查看已有子域名结果",
                "params": {"domain": "string", "tool": "string(optional)", "limit": "int(optional)"},
                "handler": self._tool_view_results,
            },
            "alive_results": {
                "description": "查看存活资产结果",
                "params": {"domain": "string", "limit": "int(optional)"},
                "handler": self._tool_alive_results,
            },
            "httpx": {
                "description": "执行 Web 存活探测",
                "params": {"domain": "string", "source": "string(optional)", "tech_detect": "bool(optional)"},
                "handler": self._tool_httpx,
            },
            "export_results": {
                "description": "导出结果文件",
                "params": {"domain": "string(optional)", "format": "csv|json", "category": "string(optional)", "tool_name": "string(optional)"},
                "handler": self._tool_export_results,
            },
        }

    def run(self, user_message: str) -> Dict[str, Any]:
        self.steps = []
        text = (user_message or "").strip()
        if not text:
            return self._build_response("请输入有效问题。")

        self._append_message("user", text)

        menu_text = self._resolve_menu_input(text)
        if menu_text:
            text = menu_text

        if self.pending_plan:
            handled = self._handle_pending_plan(text)
            if handled:
                return handled

        intent = analyze_intent(
            text,
            has_uploaded_file=bool(self.uploaded_context.get("file_path")),
            context_state=self.context,
        )

        if intent.intent_type == "cancel_plan":
            return self._handle_cancel_without_pending()

        self._update_context_from_intent(intent)
        focus_domain = intent.target or extract_domain(text) or self.context.get("last_target") or self.context.get("target")

        if self._is_storage_question(text) and intent.intent_type not in {"analyze_existing_subdomains"} and not self._has_scan_intent(text):
            message = self._answer_storage_question(focus_domain)
            self._append_message("assistant", message)
            return self._build_response(message, focus_domain=focus_domain)

        if intent.intent_type == "set_target":
            return self._handle_set_target(intent)

        if intent.intent_type == "view_existing_results":
            return self._handle_view_existing_results(intent)

        if intent.intent_type == "analyze_existing_subdomains":
            return self._handle_analyze_existing_subdomains(intent)

        if intent.intent_type in {"probe_existing_subdomains", "web_probe", "subdomain_scan"} and not intent.target:
            message = "我识别到你要执行主动任务，但当前还缺少目标域名。请补充域名，例如 `peizheng.edu.cn`。"
            self._append_message("assistant", message)
            return self._build_response(message, plan_status="need_target")

        plan = build_plan(intent, self.uploaded_context)
        if self.context.get("mode") == "passive_only" and intent.intent_type == "strategy_only" and intent.target:
            plan = build_passive_plan(intent.target)

        if not plan:
            message = "我没有识别到明确任务。请补充目标域名，或直接说查看已有结果、存活探测、导出为 CSV。"
            self._append_message("assistant", message)
            return self._build_response(message, focus_domain=focus_domain)

        if not plan.target or not plan.steps:
            message = self._format_strategy_only_message(plan, intent)
            self._append_message("assistant", message)
            return self._build_response(message, focus_domain=focus_domain, pending_plan=None, plan_status="strategy_only")

        if plan.requires_confirmation:
            self.pending_plan = plan.to_dict()
            message = self._format_pending_plan_message(self.pending_plan, intent)
            self._append_message("assistant", message)
            return self._build_response(
                message,
                focus_domain=focus_domain or plan.target,
                pending_plan=self.pending_plan,
                plan_status="awaiting_confirmation",
            )

        return self._execute_plan(plan.to_dict(), original_message=text)

    def _handle_pending_plan(self, text: str) -> Optional[Dict[str, Any]]:
        if is_confirm(text):
            return self._execute_plan(deepcopy(self.pending_plan), original_message=text)

        if is_cancel(text):
            return self._cancel_pending_plan()

        new_intent = analyze_intent(
            text,
            has_uploaded_file=bool(self.uploaded_context.get("file_path")),
            context_state=self.context,
        )
        if is_meaningful_new_intent(new_intent):
            self._update_context_from_intent(new_intent)
            old_plan = deepcopy(self.pending_plan)

            if new_intent.intent_type == "view_existing_results":
                self.pending_plan = None
                return self._handle_view_existing_results(new_intent)

            if new_intent.intent_type == "analyze_existing_subdomains":
                self.pending_plan = None
                return self._handle_analyze_existing_subdomains(new_intent)

            if new_intent.intent_type == "set_target":
                self.pending_plan = None
                return self._handle_set_target(new_intent)

            new_plan = build_plan(new_intent, self.uploaded_context)
            if not new_plan:
                self.pending_plan = None
                return None

            self.pending_plan = new_plan.to_dict()
            message = self._render_replace_plan_message(old_plan=old_plan, new_plan=self.pending_plan)
            self._append_message("assistant", message)
            return self._build_response(
                message,
                focus_domain=new_intent.target or self.pending_plan.get("target"),
                pending_plan=self.pending_plan,
                plan_status="awaiting_confirmation",
            )

        if is_plan_modification(text):
            updated_plan = apply_user_intervention(deepcopy(self.pending_plan), text)
            self.pending_plan = updated_plan
            message = self._format_pending_plan_message(updated_plan)
            self._append_message("assistant", message)
            return self._build_response(
                message,
                focus_domain=updated_plan.get("target"),
                pending_plan=updated_plan,
                plan_status="awaiting_confirmation",
            )

        message = self._render_pending_plan_help(self.pending_plan)
        self._append_message("assistant", message)
        return self._build_response(
            message,
            focus_domain=self.pending_plan.get("target"),
            pending_plan=self.pending_plan,
            plan_status="awaiting_confirmation",
        )

    def _handle_set_target(self, intent: UserIntent) -> Dict[str, Any]:
        if not intent.target:
            message = "没有识别到要设置的目标域名。"
            self._append_message("assistant", message)
            return self._build_response(message, plan_status="need_target")

        self.context["target"] = intent.target
        self.context["last_target"] = intent.target
        message = (
            f"已将目标设置为 `{intent.target}`。\n"
            "你可以继续回复：\n"
            "- 存活探测\n"
            "- 查看已有结果\n"
            "- 导出为 CSV\n"
            "- 只做被动信息收集"
        )
        self._set_last_menu(
            {
                "1": "查看已有结果",
                "2": "导出为 CSV",
                "3": "只做被动信息收集",
                "4": "存活探测",
            }
        )
        self._append_message("assistant", message)
        return self._build_response(message, focus_domain=intent.target, plan_status="target_set")

    def _handle_view_existing_results(self, intent: UserIntent) -> Dict[str, Any]:
        if not intent.target:
            message = "我需要一个目标域名才能查看已有结果，例如：`查看 peizheng.edu.cn 的已有结果`。"
            self._append_message("assistant", message)
            return self._build_response(message, plan_status="need_target")

        summary_args = {"domain": intent.target}
        view_args = {"domain": intent.target, "limit": 20}
        summary_result = self._tool_summary(summary_args)
        view_result = self._tool_view_results(view_args)
        self._record_step("summary", summary_args, summary_result)
        self._record_step("view_results", view_args, view_result)

        message = self._render_existing_results_message(intent.target, summary_result, view_result)
        self._set_last_menu(
            {
                "1": f"查看 {intent.target} 的已有结果",
                "2": "导出为 CSV",
                "3": "基于这些结果做优先级分析",
                "4": "存活探测",
            }
        )
        self._append_message("assistant", message)
        return self._build_response(
            message,
            focus_domain=intent.target,
            pending_plan=None,
            plan_status="completed_view_results",
        )

    def _handle_analyze_existing_subdomains(self, intent: UserIntent) -> Dict[str, Any]:
        if not intent.target:
            message = "我理解你想基于数据库里的已有子域名结果做只读分析，但当前没有识别到目标域名。请补充目标域名，例如 `peizheng.edu.cn`。"
            self._append_message("assistant", message)
            return self._build_response(message, plan_status="need_target")

        args = {"domain": intent.target, "tool": "subfinder", "limit": 200}
        result = self._tool_view_results(args)
        self._record_step("view_results", args, result)
        ranked = rank_subdomains(result.get("items", []), top_n=20)

        message = self._render_src_target_advice(intent.target, result, ranked)
        self._set_last_menu(
            {
                "1": "导出为 CSV",
                "2": "只看已有结果",
                "3": "只做被动信息收集",
                "4": "存活探测",
            }
        )
        self._append_message("assistant", message)
        return self._build_response(
            message,
            focus_domain=intent.target,
            pending_plan=None,
            plan_status="completed_readonly_analysis",
        )

    def _execute_plan(self, plan: Dict[str, Any], original_message: str) -> Dict[str, Any]:
        tool_results: List[Dict[str, Any]] = []
        focus_domain = plan.get("target")

        for step in plan.get("steps", []):
            action = step.get("tool")
            args = step.get("args", {})
            tool_result = self._execute_tool(action, args)
            self._record_step(action, args, tool_result)
            tool_results.append(tool_result)
            if self.debug:
                print(f"[debug] plan_step={action} args={args} result={tool_result}")
            if not tool_result.get("ok"):
                break

        message = self._format_execution_summary(plan, tool_results)
        self.pending_plan = None
        self._append_message("assistant", message)
        export_path = next((item.get("path") for item in tool_results if item.get("tool") == "export_results" and item.get("ok")), None)
        return self._build_response(
            message,
            focus_domain=focus_domain if self._is_domain(focus_domain) else None,
            pending_plan=None,
            plan_status="completed",
            export_path=export_path,
        )

    def _execute_tool(self, action: str, args: Dict[str, Any]) -> Dict[str, Any]:
        tool = self.available_tools.get(action)
        if not tool:
            return self._attach_storage_info({"ok": False, "error": f"未知工具: {action}", "tool": action}, action, args)
        try:
            result = tool["handler"](args)
        except Exception as exc:
            result = {"ok": False, "error": str(exc), "tool": action}
        return self._attach_storage_info(result, action, args)

    def _tool_subdomain(self, args: Dict[str, Any]) -> Dict[str, Any]:
        scan_tool = str(args.get("tool", "subfinder")).strip().lower()
        file_path = str(args.get("file_path", "")).strip()
        domain = str(args.get("domain", "")).strip().lower() or None

        if scan_tool not in {"amass", "subfinder", "dnsx"}:
            raise ValueError("tool 仅支持 amass/subfinder/dnsx")

        if file_path:
            report = run_tools(file_path=file_path, tools=[scan_tool], store=self.store)
            return {
                "ok": True,
                "tool": "subdomain",
                "domain": None,
                "scan_tool": scan_tool,
                "file_path": file_path,
                "target_count": len(report.get("targets", [])),
                "total_found": report["total_found"],
                "total_inserted": report["total_inserted"],
            }

        if not domain:
            raise ValueError("缺少 domain 或 file_path 参数")

        self._validate_domain(domain)
        self._enforce_rate_limit("subdomain", domain)
        report = run_tools(domain=domain, tools=[scan_tool], store=self.store)
        return {
            "ok": True,
            "tool": "subdomain",
            "domain": domain,
            "scan_tool": scan_tool,
            "total_found": report["total_found"],
            "total_inserted": report["total_inserted"],
        }

    def _tool_summary(self, args: Dict[str, Any]) -> Dict[str, Any]:
        domain_raw = str(args.get("domain", "")).strip().lower()
        if domain_raw:
            self._validate_domain(domain_raw)
            return {"ok": True, "tool": "summary", "domain": domain_raw, "data": self.store.get_domain_summary(domain_raw)}
        return {"ok": True, "tool": "summary", "domain": None, "data": self.store.get_global_summary()}

    def _tool_view_results(self, args: Dict[str, Any]) -> Dict[str, Any]:
        domain = self._normalize_domain_arg(args.get("domain"))
        tool_name = str(args.get("tool", "")).strip().lower() or None
        limit = self._safe_limit(args.get("limit"), default=50)
        self._validate_domain(domain)
        rows = self.store.get_view_results(domain=domain, tool_name=tool_name)
        items = [
            {"domain": row_domain, "subdomain": subdomain, "tool_name": row_tool, "created_at": created_at}
            for row_domain, subdomain, row_tool, created_at in rows[:limit]
        ]
        return {
            "ok": True,
            "tool": "view_results",
            "domain": domain,
            "filter_tool": tool_name,
            "category": "subdomain",
            "total": len(rows),
            "items": items,
        }

    def _tool_alive_results(self, args: Dict[str, Any]) -> Dict[str, Any]:
        domain = self._normalize_domain_arg(args.get("domain"))
        limit = self._safe_limit(args.get("limit"), default=50)
        self._validate_domain(domain)
        rows = self.store.get_alive_results(domain=domain)
        return {
            "ok": True,
            "tool": "alive_results",
            "domain": domain,
            "total": len(rows),
            "items": [
                {"domain": row_domain, "hostname": hostname, "tool_name": tool_name, "created_at": created_at}
                for row_domain, hostname, tool_name, created_at in rows[:limit]
            ],
        }

    def _tool_httpx(self, args: Dict[str, Any]) -> Dict[str, Any]:
        domain = self._normalize_domain_arg(args.get("domain"))
        self._validate_domain(domain)
        self._enforce_rate_limit("httpx", domain)

        source = str(args.get("source") or "").strip().lower() or None
        tech_detect = bool(args.get("tech_detect"))
        existing_subdomains = self.store.get_results_by_domain(domain)
        runner = HttpxRunner()

        if source == "existing_subdomains":
            if not existing_subdomains:
                raise RuntimeError(f"数据库中没有 {domain} 的已有子域名结果，无法对已有子域名做探测")
            rows = runner.run_scan(domain=domain, candidates=[sub for sub, _, _ in existing_subdomains], tech_detect=tech_detect)
            probe_mode = "existing_subdomains"
            target_count = len(existing_subdomains)
        elif existing_subdomains:
            rows = runner.run_scan(domain=domain, tech_detect=tech_detect)
            probe_mode = "stored_subdomains"
            target_count = len(existing_subdomains)
        else:
            rows = runner.run_scan(domain=domain, candidates=[domain], tech_detect=tech_detect)
            probe_mode = "direct_domain"
            target_count = 1

        self._save_httpx_metadata(domain, rows)
        return {
            "ok": True,
            "tool": "httpx",
            "domain": domain,
            "probe_mode": probe_mode,
            "target_count": target_count,
            "total": len(rows),
            "tech_detect": tech_detect,
            "items": rows[:20],
        }

    def _tool_export_results(self, args: Dict[str, Any]) -> Dict[str, Any]:
        domain = str(args.get("domain", "")).strip().lower() or None
        tool_name = str(args.get("tool_name") or "").strip().lower() or None
        category = str(args.get("category", "")).strip().lower() or None
        fmt = str(args.get("format", "csv")).strip().lower() or "csv"
        limit = self._safe_limit(args.get("limit"), default=1000)
        rows = gather_export_rows(self.store, domain=domain, tool_name=tool_name, category=category, limit=limit)
        path = export_results(rows, fmt=fmt, prefix=domain or "all_results")
        return {"ok": True, "tool": "export_results", "domain": domain, "format": fmt, "count": len(rows), "path": path}

    def _attach_storage_info(self, result: Dict[str, Any], action: str, args: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = str(result.get("tool") or action).strip().lower()
        result["storage"] = {
            "type": "sqlite",
            "path": self.store.db_path or "results/scan_results.db",
            "tables": self._get_storage_tables(tool_name, result, args),
        }
        return result

    def _get_storage_tables(self, tool_name: str, result: Dict[str, Any], args: Dict[str, Any]) -> List[str]:
        if tool_name == "subdomain":
            scan_tool = str(result.get("scan_tool") or args.get("tool") or "subfinder").strip().lower()
            tables = ["subdomain_results"]
            if scan_tool in TOOL_DATABASES:
                tables.append(TOOL_DATABASES[scan_tool]["table"])
            if scan_tool == "dnsx":
                tables.append("alive_results")
            return self._dedupe_list(tables)
        if tool_name == "httpx":
            return ["httpx_results", "tool_results"]
        if tool_name == "alive_results":
            return ["alive_results", "dnsx_results"]
        if tool_name == "view_results":
            return ["subdomain_results"]
        if tool_name == "summary":
            return ["scan_runs", "subdomain_results", "alive_results", "tool_results"]
        return ["tool_results"]

    def _format_pending_plan_message(self, plan: Dict[str, Any], intent: Optional[UserIntent] = None) -> str:
        if plan.get("steps") and any(step.get("tool") == "httpx" for step in plan["steps"]) and any(
            str(step.get("args", {}).get("source")) == "existing_subdomains" for step in plan["steps"]
        ):
            tech_detect = any(bool(step.get("args", {}).get("tech_detect")) for step in plan["steps"] if step.get("tool") == "httpx")
            target = plan.get("target") or "未指定目标"
            lines = [
                f"我识别到你要基于 {target} 已收集的子域名做进一步处理，不会重新执行子域名收集。",
                "",
                f"目标：{target}",
                "数据来源：数据库中已有子域名结果",
                "任务类型：主动 Web 探测",
                f"技术栈识别：{'开启' if tech_detect else '关闭'}",
                "",
                "建议执行步骤：",
                "1. httpx：读取已有子域名并进行存活探测",
                f"2. httpx：{'识别状态码、标题、Web 服务和技术栈信息' if tech_detect else '只做存活探测，不识别技术栈'}",
                "3. summary：汇总结果与保存位置",
                "",
                "这会对已收集子域名发起 HTTP/HTTPS 请求。请确认是否执行。",
                "",
                "你可以回复：",
                "- 确认执行",
                "- 只探活，不识别技术栈",
                "- 只看已有存活结果",
                "- 导出已有结果为 CSV",
                "- 取消",
            ]
            return "\n".join(lines)

        lines = [
            "我建议执行以下计划：",
            "",
            f"目标：{plan.get('target') or '未指定目标'}",
            f"策略：{plan.get('strategy', '未定义')}",
            "步骤：",
        ]
        for index, step in enumerate(plan.get("steps", []), start=1):
            lines.append(f"{index}. {step.get('description', step.get('tool', '未命名步骤'))}")
        lines.extend(self._plan_options("awaiting_confirmation"))
        return "\n".join(lines)

    def _format_strategy_only_message(self, plan: AgentPlan, intent: UserIntent) -> str:
        if plan.message:
            return plan.message
        if intent.passive_only:
            self.context["strategy_context"] = {"target": intent.target, "org": intent.org_name}
            self._set_last_menu({"1": "查看已有结果", "2": "导出为 CSV", "3": "切换为主动收集", "4": "存活探测"})
            return plan.strategy
        return f"当前识别到的是策略咨询，不会直接保存为待执行计划。\n{plan.strategy}"

    def _render_replace_plan_message(self, old_plan: Dict[str, Any], new_plan: Dict[str, Any]) -> str:
        lines = [
            "我识别到你已经切换到了一个新的明确意图。",
            f"旧计划：{old_plan.get('target') or '未指定目标'}",
            f"新计划：{new_plan.get('target') or '未指定目标'}",
            "",
            "新的待确认计划如下：",
            f"策略：{new_plan.get('strategy', '未定义')}",
        ]
        for index, step in enumerate(new_plan.get("steps", []), start=1):
            lines.append(f"{index}. {step.get('description', step.get('tool', '未命名步骤'))}")
        lines.extend(self._plan_options("awaiting_confirmation"))
        return "\n".join(lines)

    def _render_pending_plan_help(self, pending_plan: Dict[str, Any]) -> str:
        return "\n".join(
            [
                f"当前还有一个待确认计划：{pending_plan.get('target') or '未指定目标'}",
                "你可以回复：",
                "- 确认执行",
                "- 取消",
                "- 只做 subfinder，不做 httpx",
                "- 只探活，不识别技术栈",
                "- 查看 peizheng.edu.cn 的已有结果",
            ]
        )

    def _render_existing_results_message(self, domain: str, summary_result: Dict[str, Any], view_result: Dict[str, Any]) -> str:
        summary = summary_result.get("data", {}) or {}
        items = view_result.get("items", [])
        lines = [
            f"已读取 `{domain}` 的已有结果。",
            f"子域名总数：{summary.get('total_subdomains', view_result.get('total', 0))}",
            f"最近扫描时间：{summary.get('last_scan_at') or '未知'}",
            f"结果库：`{self.store.db_path or 'results/scan_results.db'}`",
            "",
            "前 10 条结果：",
        ]
        if not items:
            lines.append("当前没有找到已有子域名结果。")
        else:
            for index, item in enumerate(items[:10], start=1):
                lines.append(f"{index}. {item['subdomain']}  来源={item['tool_name']}")
        lines.extend(
            [
                "",
                "你可以继续回复：",
                "- 导出为 CSV",
                "- 只看 subfinder 结果",
                "- 基于这些结果做优先级分析",
                "- 存活探测",
            ]
        )
        return "\n".join(lines)

    def _format_execution_summary(self, plan: Dict[str, Any], tool_results: List[Dict[str, Any]]) -> str:
        if not tool_results:
            return "当前计划没有执行任何步骤。"

        lines = [
            f"已按计划处理你的需求：{plan.get('strategy', '未定义策略')}。",
            f"目标：{plan.get('target') or '未指定目标'}",
            "",
            "执行结果：",
        ]
        for index, result in enumerate(tool_results, start=1):
            lines.append(f"{index}. {self._format_single_tool_result(result)}")
        export_path = next((item.get("path") for item in tool_results if item.get("tool") == "export_results" and item.get("ok")), None)
        if export_path:
            lines.extend(["", f"导出文件：`{export_path}`"])
        return "\n".join(lines)

    def _format_single_tool_result(self, tool_result: Dict[str, Any]) -> str:
        tool_name = tool_result.get("tool", "unknown")
        storage = tool_result.get("storage", {})
        db_type = storage.get("type", "sqlite")
        db_path = storage.get("path", self.store.db_path or "results/scan_results.db")
        tables = " / ".join(storage.get("tables", [])) or "未标注表名"

        if not tool_result.get("ok"):
            return f"{tool_name} 执行失败：{tool_result.get('error', '未知错误')}。结果库：{db_type} `{db_path}`，相关表：{tables}。"

        if tool_name == "subdomain":
            if tool_result.get("file_path"):
                return (
                    f"使用 {tool_result.get('scan_tool', 'subfinder')} 对上传目标列表完成子域名收集，"
                    f"目标数 {tool_result.get('target_count', 0)}，发现 {tool_result.get('total_found', 0)} 条，"
                    f"新增入库 {tool_result.get('total_inserted', 0)} 条。结果库：{db_type} `{db_path}`，相关表：{tables}。"
                )
            return (
                f"使用 {tool_result.get('scan_tool', 'subfinder')} 对 `{tool_result.get('domain', '')}` 完成子域名收集，"
                f"发现 {tool_result.get('total_found', 0)} 条，新增入库 {tool_result.get('total_inserted', 0)} 条。"
                f"结果库：{db_type} `{db_path}`，相关表：{tables}。"
            )

        if tool_name == "httpx":
            mode_text = {
                "direct_domain": "直接探测目标域名本身",
                "stored_subdomains": "基于已有子域名批量探测",
                "existing_subdomains": "严格读取数据库中的已有子域名批量探测",
            }.get(tool_result.get("probe_mode"), "批量探测")
            summary = self._summarize_httpx_items(tool_result.get("items", []))
            return (
                f"对 `{tool_result.get('domain', '')}` 完成 httpx 探测，模式为{mode_text}，"
                f"目标数 {tool_result.get('target_count', 0)}，存活结果 {tool_result.get('total', 0)} 条。"
                f"{summary} 结果库：{db_type} `{db_path}`，相关表：{tables}。"
            )

        if tool_name == "summary":
            data = tool_result.get("data", {}) or {}
            total_subdomains = data.get("total_subdomains")
            if total_subdomains is not None:
                return f"`{tool_result.get('domain')}` 当前累计子域名 {total_subdomains} 条。结果库：{db_type} `{db_path}`，相关表：{tables}。"
            return f"已返回汇总信息。结果库：{db_type} `{db_path}`，相关表：{tables}。"

        if tool_name == "view_results":
            filter_tool = tool_result.get("filter_tool")
            filter_text = f"（过滤工具：{filter_tool}）" if filter_tool else ""
            return f"`{tool_result.get('domain', '')}` 当前共有 {tool_result.get('total', 0)} 条子域名记录{filter_text}。结果库：{db_type} `{db_path}`，相关表：{tables}。"

        if tool_name == "alive_results":
            items = tool_result.get("items", [])
            tool_names = sorted({str(item.get('tool_name', '')).strip() for item in items if item.get("tool_name")})
            source_text = " / ".join(tool_names) if tool_names else "alive_results"
            return f"`{tool_result.get('domain', '')}` 当前共有 {tool_result.get('total', 0)} 条存活记录，来源工具为 {source_text}。结果库：{db_type} `{db_path}`，相关表：{tables}。"

        if tool_name == "export_results":
            return f"已导出 {tool_result.get('count', 0)} 条结果为 {str(tool_result.get('format', 'csv')).upper()} 文件：`{tool_result.get('path', '')}`。"

        return f"已完成工具 `{tool_name}` 执行。结果库：{db_type} `{db_path}`，相关表：{tables}。"

    def _render_src_target_advice(self, domain: str, result: Dict[str, Any], ranked: List[Dict[str, Any]]) -> str:
        total = result.get("total", 0)
        lines = [
            "我识别到你不是要重新扫描，而是要基于数据库里的已有子域名结果做只读分析和优先级判断。",
            f"目标：{domain}",
            "数据来源：本地结果库中的 subfinder 子域名结果",
            "动作类型：只读查询，不会发起新的扫描请求",
            "",
            f"我先从数据库读取 `{domain}` 的 subfinder 结果，共获取到 {total} 条子域名。",
        ]
        if not ranked:
            lines.append("当前没有可用于排序的子域名记录。你可以先确认是否已有历史结果，或在授权范围内再决定是否执行新的收集计划。")
        else:
            lines.extend(["", "基于命名特征，建议优先关注这些目标："])
            for index, item in enumerate(ranked[:10], start=1):
                reason_text = "；".join(item["reasons"]) if item["reasons"] else "命名上无明显高价值特征"
                lines.append(f"{index}. {item['hostname']}  分数={item['score']}  理由：{reason_text}")
        lines.extend(
            [
                "",
                "建议策略：",
                "1. 第一阶段只做访问面确认，不进行漏洞验证。",
                "2. 优先筛选存在登录、统一认证、后台、上传、财务、教务等入口的系统。",
                "3. 在 SRC 授权范围内，再对高优先级目标做合规测试。",
                "",
                f"结果保存位置：SQLite 数据库 `{self.store.db_path or 'results/scan_results.db'}`",
                "相关表：`subdomain_results` / `tool_results`",
            ]
        )
        lines.extend(self._plan_options("completed_readonly_analysis"))
        return "\n".join(lines)

    def _plan_options(self, plan_status: str) -> List[str]:
        if plan_status == "strategy_only":
            return ["", "你可以回复：", "- 使用 peizheng.edu.cn 作为目标", "- 只做被动信息收集", "- 给我一个信息收集路线", "- 存活探测", "- 取消"]
        if plan_status in {"completed_readonly_analysis", "completed_view_results"}:
            return ["", "你可以回复：", "- 导出这些子域名为 CSV", "- 继续对高优先级目标做存活检查", "- 只看登录/认证/后台相关目标", "- 取消"]
        return ["", "你可以回复：", "- 确认执行", "- 只做 subfinder，不做 httpx", "- 只看已有结果", "- 导出为 CSV", "- 取消"]

    def _build_response(
        self,
        message: str,
        focus_domain: Optional[str] = None,
        pending_plan: Optional[Dict[str, Any]] = None,
        plan_status: Optional[str] = None,
        export_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "message": message,
            "focus_domain": focus_domain,
            "conversation_history": self.conversation_history,
            "steps": self.steps,
            "pending_plan": pending_plan,
            "plan_status": plan_status,
            "export_path": export_path,
            "context_state": self.context,
        }

    def _record_step(self, action: str, args: Dict[str, Any], result: Dict[str, Any]) -> None:
        self.steps.append({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "action": action, "args": args, "result": result})

    def _normalize_history(self, history: Optional[List[Dict[str, str]]]) -> List[Dict[str, str]]:
        normalized: List[Dict[str, str]] = []
        if history:
            for item in history:
                role = (item or {}).get("role")
                content = (item or {}).get("content", "")
                if role in {"system", "user", "assistant"} and isinstance(content, str):
                    normalized.append({"role": role, "content": content})
        if not normalized or normalized[0].get("role") != "system":
            normalized = [{"role": "system", "content": SYSTEM_PROMPT}] + normalized
        else:
            normalized[0] = {"role": "system", "content": SYSTEM_PROMPT}
        return self._trim_history(normalized)

    def _append_message(self, role: str, content: str) -> None:
        self.conversation_history.append({"role": role, "content": content})
        self.conversation_history = self._trim_history(self.conversation_history)

    def _trim_history(self, history: List[Dict[str, str]]) -> List[Dict[str, str]]:
        if len(history) <= self.max_history_messages:
            return history
        system_msg = history[0]
        tail = history[-(self.max_history_messages - 1):]
        return [system_msg] + tail

    def _normalize_domain_arg(self, value: Any) -> str:
        domain = str(value or "").strip().lower()
        if not domain:
            raise ValueError("缺少 domain 参数")
        return domain

    def _validate_domain(self, domain: str) -> None:
        if not self.DOMAIN_PATTERN.fullmatch(domain):
            raise ValueError(f"非法域名: {domain}")
        try:
            ipaddress.ip_address(domain)
        except ValueError:
            pass
        else:
            raise ValueError("不允许直接使用 IP 地址")
        if domain in self.blocked_domains:
            raise ValueError(f"域名被策略拦截: {domain}")
        if any(domain.endswith(suffix) for suffix in self.blocked_suffixes):
            raise ValueError(f"域名后缀被策略拦截: {domain}")
        if self.allowed_suffixes and not any(domain.endswith(suffix) for suffix in self.allowed_suffixes):
            raise ValueError(f"域名不在允许后缀范围内: {domain}")

    def _enforce_rate_limit(self, action: str, domain: str) -> None:
        key = f"{action}:{domain}"
        now = time.time()
        last = self.RATE_LIMIT_CACHE.get(key, 0.0)
        if now - last < self.min_tool_interval_sec:
            wait_sec = int(self.min_tool_interval_sec - (now - last)) + 1
            raise ValueError(f"触发频率限制，请 {wait_sec} 秒后重试: {action} {domain}")
        self.RATE_LIMIT_CACHE[key] = now

    def _safe_limit(self, value: Any, default: int = 20) -> int:
        try:
            parsed = int(value)
            return default if parsed < 1 else min(parsed, 5000)
        except Exception:
            return default

    def _load_set_env(self, env_key: str, defaults: Optional[set] = None) -> set:
        raw = os.getenv(env_key, "").strip()
        if not raw:
            return set(defaults or set())
        parsed = {item.strip().lower() for item in raw.split(",") if item.strip()}
        if defaults:
            parsed.update({item.lower() for item in defaults})
        return parsed

    def _is_storage_question(self, text: str) -> bool:
        lowered = text.lower()
        return any(keyword in text or keyword in lowered for keyword in self.DATABASE_QUERY_KEYWORDS)

    def _has_scan_intent(self, text: str) -> bool:
        lowered = text.lower()
        scan_keywords = ("子域", "subdomain", "探活", "存活", "httpx", "扫描", "收集", "recon")
        return any(keyword in text or keyword in lowered for keyword in scan_keywords)

    def _answer_storage_question(self, focus_domain: Optional[str]) -> str:
        db_path = self.store.db_path or "results/scan_results.db"
        domain_text = f"当前与 `{focus_domain}` 相关的扫描结果" if focus_domain else "当前扫描结果"
        return (
            f"{domain_text} 默认保存在 SQLite 数据库 `{db_path}`。\n"
            "主要表包括 `subdomain_results`、`alive_results`、`tool_results`，各工具的专用表例如 `dnsx_results`、`httpx_results` 也会按工具分别保存。"
        )

    def _dedupe_list(self, values: List[str]) -> List[str]:
        seen = set()
        ordered: List[str] = []
        for value in values:
            if value and value not in seen:
                seen.add(value)
                ordered.append(value)
        return ordered

    def _is_domain(self, value: Optional[str]) -> bool:
        return bool(value and self.DOMAIN_PATTERN.fullmatch(value))

    def _cancel_pending_plan(self) -> Dict[str, Any]:
        self.pending_plan = None
        message = "已取消当前待执行计划。"
        self._append_message("assistant", message)
        return self._build_response(message, plan_status="cancelled")

    def _handle_cancel_without_pending(self) -> Dict[str, Any]:
        if self.context.get("strategy_context") or self.context.get("last_menu") or self.context.get("last_target"):
            self.context["strategy_context"] = None
            self.context["last_menu"] = None
            self.context["last_target"] = None
            self.context["target"] = None
            self.context["mode"] = None
            message = "已清理当前策略上下文。"
        else:
            message = "当前没有待取消的计划或策略上下文"
        self._append_message("assistant", message)
        return self._build_response(message, plan_status="cancelled")

    def _update_context_from_intent(self, intent: UserIntent) -> None:
        if intent.passive_only:
            self.context["mode"] = "passive_only"
        elif intent.scan_allowed:
            self.context["mode"] = "active"
        if intent.org_name:
            self.context["org"] = intent.org_name
        if intent.target:
            self.context["target"] = intent.target
            self.context["last_target"] = intent.target

    def _resolve_menu_input(self, text: str) -> Optional[str]:
        normalized = (text or "").strip()
        if normalized in {"1", "2", "3", "4"}:
            menu = self.context.get("last_menu") or {}
            return menu.get(normalized)
        return None

    def _set_last_menu(self, mapping: Dict[str, str]) -> None:
        self.context["last_menu"] = mapping

    def _save_httpx_metadata(self, domain: str, rows: List[Dict[str, Any]]) -> None:
        serialized = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in rows]
        if serialized:
            self.store.save_tool_results(domain, "httpx", "web", serialized)

    def _summarize_httpx_items(self, items: List[Dict[str, Any]]) -> str:
        if not items:
            return ""
        webservers: Dict[str, int] = {}
        techs: Dict[str, int] = {}
        cdn_count = 0
        for item in items:
            webserver = str(item.get("webserver") or "").strip()
            if webserver:
                webservers[webserver] = webservers.get(webserver, 0) + 1
            for tech in item.get("tech", []) or []:
                techs[str(tech)] = techs.get(str(tech), 0) + 1
            if item.get("cdn"):
                cdn_count += 1
        top_servers = ", ".join(name for name, _ in sorted(webservers.items(), key=lambda x: (-x[1], x[0]))[:3])
        top_techs = ", ".join(name for name, _ in sorted(techs.items(), key=lambda x: (-x[1], x[0]))[:5])
        parts = []
        if top_servers:
            parts.append(f"常见 Web Server：{top_servers}。")
        if top_techs:
            parts.append(f"常见技术栈：{top_techs}。")
        if cdn_count:
            parts.append(f"检测到 CDN 的目标数：{cdn_count}。")
        return " ".join(parts)
