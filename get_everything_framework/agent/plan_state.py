from typing import Any, Dict

from .intent import UserIntent, analyze_intent, extract_domain


def is_confirm(text: str) -> bool:
    lowered = (text or "").lower()
    keywords = ["确认", "执行", "开始", "继续", "可以执行", "confirm", "run", "start", "continue", "go ahead"]
    return any(keyword in text or keyword in lowered for keyword in keywords)


def is_cancel(text: str) -> bool:
    lowered = (text or "").lower()
    keywords = ["取消", "停止", "算了", "不要执行", "cancel", "stop", "abort", "never mind"]
    return any(keyword in text or keyword in lowered for keyword in keywords)


def is_plan_modification(text: str) -> bool:
    lowered = (text or "").lower()
    keywords = [
        "不做 httpx",
        "不要 httpx",
        "no httpx",
        "只做 subfinder",
        "改用 amass",
        "修改计划",
        "移除",
        "只探活，不识别技术栈",
    ]
    return any(keyword in text or keyword in lowered for keyword in keywords)


def apply_user_intervention(plan: Dict[str, Any], text: str) -> Dict[str, Any]:
    lowered = (text or "").lower()
    steps = list(plan.get("steps", []))

    if "不做 httpx" in text or "不要 httpx" in text or "no httpx" in lowered:
        steps = [step for step in steps if step.get("tool") != "httpx"]

    if "只探活，不识别技术栈" in text:
        for step in steps:
            if step.get("tool") == "httpx":
                step.setdefault("args", {})["tech_detect"] = False
                step["description"] = "读取数据库中的已有子域名并执行 httpx 存活探测，不识别技术栈"

    if "只做 subfinder" in text:
        for step in steps:
            if step.get("tool") == "subdomain":
                step.setdefault("args", {})["tool"] = "subfinder"
                step["description"] = f"使用 subfinder 收集 {step['args'].get('domain') or '目标'} 的子域名"
        steps = [step for step in steps if step.get("tool") == "subdomain"]

    if "改用 amass" in text:
        for step in steps:
            if step.get("tool") == "subdomain":
                step.setdefault("args", {})["tool"] = "amass"
                step["description"] = f"使用 amass 收集 {step['args'].get('domain') or '目标'} 的子域名"

    plan["steps"] = steps
    return plan


def is_meaningful_new_intent(intent: UserIntent) -> bool:
    return bool(
        intent.target
        or intent.intent_type
        in {
            "view_existing_results",
            "analyze_existing_subdomains",
            "probe_existing_subdomains",
            "uploaded_file_scan",
            "export_results",
            "subdomain_scan",
            "web_probe",
            "set_target",
        }
    )


def is_new_intent(text: str, pending_plan: Dict[str, Any]) -> bool:
    intent = analyze_intent(text)
    if is_confirm(text) or is_cancel(text) or is_plan_modification(text):
        return False
    if is_meaningful_new_intent(intent):
        return True

    domain = extract_domain(text)
    current_target = str((pending_plan or {}).get("target") or "")
    return bool(domain and domain not in current_target)
