from typing import Any, Dict, Iterable, List, Tuple


HIGH_VALUE_KEYWORDS = [
    "admin", "oa", "sso", "idp", "cas", "login", "auth",
    "api", "gateway", "portal", "jw", "jwc", "xg", "ehall",
    "vpn", "mail", "git", "dev", "test", "staging",
    "upload", "file", "pay", "cw", "finance",
]

MEDIUM_VALUE_KEYWORDS = [
    "www", "news", "job", "zs", "lib", "ky", "yjs",
    "office", "service",
]

LOW_VALUE_KEYWORDS = [
    "static", "cdn", "img", "image", "css", "js", "assets",
]


def score_subdomain(hostname: str) -> Tuple[int, List[str]]:
    value = (hostname or "").lower()
    score = 0
    reasons: List[str] = []

    for keyword in HIGH_VALUE_KEYWORDS:
        if keyword in value:
            score += 10
            reasons.append(f"包含高价值关键词: {keyword}")

    for keyword in MEDIUM_VALUE_KEYWORDS:
        if keyword in value:
            score += 4
            reasons.append(f"包含业务系统关键词: {keyword}")

    for keyword in LOW_VALUE_KEYWORDS:
        if keyword in value:
            score -= 5
            reasons.append(f"偏静态资源关键词: {keyword}")

    parts = value.split(".")
    if len(parts) >= 4:
        score += 2
        reasons.append("多级子域，可能是具体业务入口")

    return score, reasons


def rank_subdomains(items: Iterable[Any], top_n: int = 20) -> List[Dict[str, Any]]:
    ranked: List[Dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            hostname = item.get("subdomain") or item.get("hostname") or item.get("value") or ""
        else:
            hostname = str(item)

        hostname = hostname.strip().lower()
        if not hostname:
            continue

        score, reasons = score_subdomain(hostname)
        ranked.append({"hostname": hostname, "score": score, "reasons": reasons})

    ranked.sort(key=lambda row: row["score"], reverse=True)
    return ranked[:top_n]
