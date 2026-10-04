"""结果导出模块 — 将扫描结果导出为 CSV 或 JSON 文件。

支持按域名/工具/分类筛选后导出。
"""

import csv
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

from config import EXPORT_DIR
from storage import ScanResultStore

# 文件名前缀白名单：只保留字母/数字/点/下划线/连字符。
# ``prefix`` 直接来自查询参数（如 ``?domain=...``），若原样拼进文件名，
# ``?domain=../../evil`` 就能把文件写到 EXPORT_DIR 之外（路径穿越）。
_UNSAFE_PREFIX_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
# 连续的点也要折叠：``..`` 在 Windows/Unix 的文件名里都仍有穿越语义。
_DOT_RUN = re.compile(r"\.{2,}")
MAX_PREFIX_LENGTH = 64

# 支持的导出格式。API 层用它做参数校验（非法值 → 400），
# 这里再用它兜底（非法值 → ValueError），两处共用同一份清单，不会漂移。
SUPPORTED_FORMATS = ("csv", "json")


def safe_prefix(value: str | None, default: str = "results") -> str:
    """把任意字符串清洗为可安全用作文件名前缀的值。

    规则（顺序执行，缺一不可）：

    1. 只保留 ``[A-Za-z0-9._-]``，其余字符替换为 ``_``；
    2. 把连续的点 ``..`` 折叠成单个 ``.``；
    3. 去掉首尾的 ``.`` 与 ``_``（避免隐藏文件与 ``..`` 残留）；
    4. 为空时回落到 ``default``；过长则截断到 :data:`MAX_PREFIX_LENGTH`。

    Args:
        value: 原始前缀（通常来自未受信任的查询参数）。
        default: 清洗后为空时使用的兜底值。

    Returns:
        str: 可安全拼接进文件名的前缀。
    """
    text = _UNSAFE_PREFIX_CHARS.sub("_", str(value or "").strip())
    text = _DOT_RUN.sub(".", text)
    text = text.strip("._")[:MAX_PREFIX_LENGTH]
    return text or default


def ensure_export_dir() -> None:
    """确保导出目录存在，不存在则自动创建。"""
    os.makedirs(EXPORT_DIR, exist_ok=True)


def gather_export_rows(
    store: ScanResultStore,
    domain: Optional[str] = None,
    tool_name: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 1000,
) -> List[Dict[str, Any]]:
    """从数据库中收集要导出的结果行。

    子域名类结果通过 get_view_results 获取，
    其他分类通过 get_tool_results 获取。

    Args:
        store: ScanResultStore 实例。
        domain: 可选，按域名筛选。
        tool_name: 可选，按工具名筛选。
        category: 可选，按分类筛选。
        limit: 最大返回数量，默认 1000。

    Returns:
        字典列表，每项含 domain、tool_name、category、value、created_at。

    去重（本轮修复）：
        两条取数路径会**重叠** —— ``get_view_results()`` 只扫子域名专属表，
        而 ``get_tool_results()`` 在未指定已注册 ``tool_name`` 时会落到
        ``_get_tool_results_fallback()``，那个回退**遍历 ``TOOL_DATABASES`` 全部表**，
        其中就包含同一批子域名表。于是同一条子域名先由前者加入、再由后者
        从同一张表原样加第二次：5 条唯一子域名导出成 10 行，CSV 里能看到
        逐字节相同的重复行（实测：3 条 → 6 行）。

        这里按 ``(domain, category, tool_name, value, created_at)`` 整键去重。
        **键里带 ``created_at`` 是刻意的**：两条路径对同一条记录读到的
        ``created_at`` 完全相同，因此重复行会被摘掉；而「同一子域名在两次
        不同时间的扫描里各出现一次」是两条真实的观测，不该被合并成一条。

        不用「只要 category 是 subdomain 就跳过」的写法：``tool_name`` 传一个
        **未注册**的名字时，``get_view_results()`` 会因逐表 ``continue`` 而返回空，
        此时子域名行**只能**由回退路径提供，按分类一概跳过会把它们全丢光。
        按「已经收过的整键」来判断，则只在真的重复时跳过。
    """
    rows: List[Dict[str, Any]] = []
    seen: set = set()

    # 子域名类结果（category 未指定或为 subdomain 时收集）
    if category in {None, "", "subdomain"}:
        subdomain_rows = store.get_view_results(domain=domain, tool_name=tool_name)
        for row_domain, subdomain, row_tool, created_at in subdomain_rows[:limit]:
            rows.append(
                {
                    "domain": row_domain,
                    "tool_name": row_tool,
                    "category": "subdomain",
                    "value": subdomain,
                    "created_at": created_at,
                }
            )
            seen.add((row_domain, "subdomain", row_tool, subdomain, created_at))

    # 其他分类结果（category 为 subdomain 时传 None 避免重复查询子域名）
    generic_rows = store.get_tool_results(
        domain=domain,
        tool_name=tool_name,
        category=category if category != "subdomain" else None,
        limit=limit,
    )
    for row_domain, row_tool, row_category, value, created_at in generic_rows:
        key = (row_domain, row_category, row_tool, value, created_at)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "domain": row_domain,
                "tool_name": row_tool,
                "category": row_category,
                "value": value,
                "created_at": created_at,
            }
        )

    return rows[:limit]


def export_results(rows: List[Dict[str, Any]], fmt: str = "csv", prefix: str = "results") -> str:
    """将结果行导出为文件。

    Args:
        rows: 要导出的行数据列表。
        fmt: 导出格式，"csv" 或 "json"。
        prefix: 文件名前缀。

    Returns:
        导出文件的完整路径。

    Raises:
        ValueError: 不支持的导出格式。
    """
    if fmt not in SUPPORTED_FORMATS:
        # 先校验格式再落盘：非法 fmt 会直接拼出一个奇怪扩展名的文件。
        raise ValueError("暂不支持该导出格式")

    ensure_export_dir()

    # 使用时间戳生成唯一文件名；prefix 来自调用方，必须先清洗（防路径穿越）。
    ts = time.strftime("%Y%m%d_%H%M%S")
    filename = f"{safe_prefix(prefix)}_{ts}.{fmt}"
    path = os.path.join(EXPORT_DIR, filename)

    if fmt == "json":
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(rows, handle, ensure_ascii=False, indent=2)
        return path

    # 从数据中动态提取所有字段名
    fieldnames = sorted({key for row in rows for key in row.keys()}) if rows else ["domain", "tool_name", "category", "value", "created_at"]
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path
