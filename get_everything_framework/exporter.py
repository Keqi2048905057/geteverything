"""结果导出模块 — 将扫描结果导出为 CSV 或 JSON 文件。

支持按域名/工具/分类筛选后导出。
"""

import csv
import json
import os
import time
from typing import Any, Dict, List, Optional

from config import EXPORT_DIR
from storage import ScanResultStore


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
    """
    rows: List[Dict[str, Any]] = []

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

    # 其他分类结果（category 为 subdomain 时传 None 避免重复查询子域名）
    generic_rows = store.get_tool_results(
        domain=domain,
        tool_name=tool_name,
        category=category if category != "subdomain" else None,
        limit=limit,
    )
    for row_domain, row_tool, row_category, value, created_at in generic_rows:
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
    ensure_export_dir()

    # 使用时间戳生成唯一文件名
    ts = time.strftime("%Y%m%d_%H%M%S")
    fmt = (fmt or "csv").lower().strip()
    filename = f"{prefix}_{ts}.{fmt}"
    path = os.path.join(EXPORT_DIR, filename)

    if fmt == "json":
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(rows, handle, ensure_ascii=False, indent=2)
        return path

    if fmt == "csv":
        # 从数据中动态提取所有字段名
        fieldnames = sorted({key for row in rows for key in row.keys()}) if rows else ["domain", "tool_name", "category", "value", "created_at"]
        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return path

    raise ValueError("暂不支持该导出格式")
