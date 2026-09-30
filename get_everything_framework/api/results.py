"""
模块: api/results.py
功能: 提供扫描结果的查询与导出接口

路由:
  GET /api/results                   — 通用结果查询（支持 domain/tool/category 多维度过滤）
  GET /api/tool/<tool_name>/results  — 按工具名查询其专属数据库中的结果
  GET /api/export                    — 将扫描结果导出为 CSV 或 JSON 文件

依赖:
  - storage.ScanResultStore: 扫描结果持久化存储层
  - exporter: 结果导出与数据聚合模块
"""

from flask import jsonify, request

from api import api_bp            # Flask 蓝图实例
from exporter import export_results, gather_export_rows  # 结果导出相关函数
from storage import ScanResultStore  # 扫描结果存储层


def _normalize_domain(value: str | None) -> str | None:
    """
    规范化输入域名

    处理步骤:
        1. 若值为 None，返回 None（表示未指定过滤条件）
        2. 去除首尾空白字符
        3. 转为小写（域名大小写不敏感）
        4. 若结果为空字符串，返回 None

    参数:
        value: 待规范化的域名字符串

    返回:
        str | None: 规范化后的域名字符串，或 None（无效/未指定时）
    """
    if value is None:
        return None
    # 去空白并转小写
    normalized = value.strip().lower()
    # 空字符串视为无效，等同于未指定
    return normalized or None


def _normalize_value(value: str | None) -> str | None:
    """
    规范化通用查询值（工具名、分类等）

    处理逻辑与 _normalize_domain 相同：
    去空白 → 转小写 → 空值返回 None

    参数:
        value: 待规范化的通用字符串

    返回:
        str | None: 规范化后的字符串，或 None
    """
    if value is None:
        return None
    # 去空白并转小写
    normalized = value.strip().lower()
    return normalized or None


def _parse_limit(raw: str | None, default: int = 200, maximum: int = 1000) -> int:
    """
    解析并限制 limit 查询参数

    用于控制单次查询返回的最大记录数，防止查询过大数据集导致性能问题。

    参数:
        raw: 从请求参数中获取的原始字符串值
        default: 默认返回条数（当 raw 为 None 或无效时使用）
        maximum: 允许的最大返回条数上限

    返回:
        int: 合法的 limit 值，范围 [1, maximum]

    处理规则:
        - 无效值或缺失 → 使用 default
        - 值小于 1 → 修正为 1
        - 值大于 maximum → 截断为 maximum
    """
    try:
        # 将原始值转为整数，若为 None 则使用默认值
        # min 确保不超过最大值，max 确保不小于 1
        return max(1, min(int(raw or default), maximum))
    except (ValueError, TypeError):
        # 输入无法解析为整数时，返回默认值
        return default


@api_bp.route("/results", methods=["GET"])
def query_results():
    """
    通用结果查询 — 支持多维度过滤的扫描结果检索

    请求方式: GET
    路径: /api/results

    Query 参数:
        domain   — 按目标域名过滤（可选，大小写不敏感）
        tool     — 按工具名称过滤（可选，"subfinder"/"httpx" 等）
        category — 按结果分类过滤（可选，"subdomain"/"url"/"alive"/"web"/"port"）
        limit    — 返回条数上限（可选，默认 200，最大 1000）

    返回示例:
        {
            "results": [
                {
                    "id": 1,
                    "domain": "example.com",
                    "tool_name": "subfinder",
                    "category": "subdomain",
                    "result": "sub.example.com",
                    "created_at": "2025-01-01T00:00:00"
                },
                ...
            ]
        }

    内部逻辑:
        1. 创建存储层实例
        2. 从查询参数中提取并规范化各过滤条件
        3. 调用数据聚合函数 gather_export_rows 获取结果
        4. 包装为 JSON 响应返回
    """
    # 初始化存储实例
    store = ScanResultStore()
    # 提取并规范化查询参数
    domain = _normalize_domain(request.args.get("domain"))
    tool_name = _normalize_value(request.args.get("tool"))
    category = _normalize_value(request.args.get("category"))
    limit = _parse_limit(request.args.get("limit"))

    # 调用数据聚合函数，获取符合过滤条件的扫描结果行
    rows = gather_export_rows(
        store,
        domain=domain,
        tool_name=tool_name,
        category=category,
        limit=limit,
    )
    return jsonify({"results": rows})


@api_bp.route("/tool/<tool_name>/results", methods=["GET"])
def query_tool_results(tool_name: str):
    """
    按工具名查询专属数据库中的扫描结果

    每个扫描工具在 SQLite 中有独立的存储表，此接口直接从对应表中查询数据。

    请求方式: GET
    路径: /api/tool/<tool_name>/results
    路径参数:
        tool_name: 工具名称（如 subfinder, httpx, nuclei 等）

    Query 参数:
        domain — 按目标域名过滤（可选）
        limit  — 返回条数上限（可选，默认 200，最大 1000）

    返回示例:
        {
            "results": [
                {"result": "sub.example.com", "created_at": "..."},
                ...
            ]
        }

    错误响应:
        - 400: tool_name 对应的数据库表不存在

    内部逻辑:
        1. 创建存储层实例
        2. 提取并规范化查询参数
        3. 通过存储层的专属查询方法获取结果
        4. 若工具表不存在，返回 400 错误
    """
    # 初始化存储实例
    store = ScanResultStore()
    # 提取并规范化查询参数
    domain = _normalize_domain(request.args.get("domain"))
    limit = _parse_limit(request.args.get("limit"))

    # 调用存储层专属查询，按工具名查对应表
    try:
        results = store.get_dedicated_results(tool_name, domain=domain, limit=limit)
    except ValueError as exc:
        # 无此工具对应的数据库表时返回 400
        return jsonify({"error": str(exc)}), 400

    return jsonify({"results": results})


@api_bp.route("/export", methods=["GET"])
def export_data():
    """
    导出扫描结果为文件

    将符合过滤条件的扫描结果导出为 CSV 或 JSON 格式文件。

    请求方式: GET
    路径: /api/export

    Query 参数:
        domain   — 按目标域名过滤（可选）
        tool     — 按工具名称过滤（可选）
        category — 按结果分类过滤（可选）
        format   — 导出格式: "csv" 或 "json"（可选，默认 "csv"）
        limit    — 导出条数上限（可选，默认 1000，最大 5000）

    返回示例:
        {
            "ok": true,
            "path": "/path/to/example.com_20250101_120000.csv",
            "count": 500,
            "format": "csv"
        }

    内部逻辑:
        1. 创建存储层实例
        2. 提取并规范化过滤条件与导出参数
        3. 聚合符合条件的结果记录
        4. 调用导出函数生成文件到磁盘
        5. 返回文件路径、记录数、格式信息
    """
    # 初始化存储实例
    store = ScanResultStore()
    # 提取并规范化查询参数
    domain = _normalize_domain(request.args.get("domain"))
    tool_name = _normalize_value(request.args.get("tool"))
    category = _normalize_value(request.args.get("category"))
    # 导出格式，默认 csv，转小写统一处理
    fmt = (request.args.get("format") or "csv").lower()
    # 导出使用更大的默认值和上限
    limit = _parse_limit(request.args.get("limit"), default=1000, maximum=5000)

    # 聚合符合条件的扫描结果
    rows = gather_export_rows(
        store,
        domain=domain,
        tool_name=tool_name,
        category=category,
        limit=limit,
    )
    # 生成导出文件，文件名使用域名前缀（未指定域名时用 "all_results"）
    path = export_results(rows, fmt=fmt, prefix=domain or "all_results")

    return jsonify({
        "ok": True,
        "path": path,
        "count": len(rows),
        "format": fmt,
    })
