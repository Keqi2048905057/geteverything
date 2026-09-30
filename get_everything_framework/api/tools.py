"""
模块: api/tools.py
功能: 提供工具列表与数据库元信息的查询接口

路由:
  GET /api/tools      — 获取所有可用扫描工具列表及其关联的数据库信息
  GET /api/databases  — 获取所有工具数据库表的元信息（表名、记录数等）
"""

from flask import jsonify

from api import api_bp            # Flask 蓝图实例
from modules import build_runner, get_supported_runners  # 工具运行器工厂函数
from storage import ScanResultStore  # 扫描结果持久化存储


def _build_tool_payload(tool_name: str) -> dict:
    """
    构建单个工具的 API 响应数据结构

    参数:
        tool_name: 工具名称（如 "subfinder", "httpx" 等）

    返回:
        dict: 包含工具名称和分类的字典
              - name: 工具名称
              - category: 工具分类（subdomain/url/alive/web/port 等）

    内部逻辑:
        通过 build_runner() 获取对应工具的运行器实例，
        从运行器中提取分类信息。若运行器未定义 category 属性，
        则默认归类为 "subdomain"。
    """
    # 根据工具名构建运行器实例
    runner = build_runner(tool_name)
    return {
        "name": tool_name,
        # 安全获取分类属性，缺失时默认为 subdomain
        "category": getattr(runner, "category", "subdomain"),
    }


@api_bp.route("/tools", methods=["GET"])
def list_tools():
    """
    列出所有可用扫描工具及其关联的数据库表

    请求方式: GET
    路径: /api/tools
    参数: 无

    返回示例:
        {
            "tools": [
                {
                    "name": "subfinder",
                    "category": "subdomain",
                    "database": {
                        "tool_name": "subfinder",
                        "table_name": "subfinder_results",
                        "record_count": 1234
                    }
                },
                ...
            ]
        }

    内部逻辑:
        1. 创建扫描结果存储实例
        2. 获取所有工具对应的数据库信息，构建 tool_name -> database 映射
        3. 遍历所有支持的扫描工具，为每个工具构建响应数据（工具信息 + 数据库信息）
    """
    # 初始化存储层实例
    store = ScanResultStore()
    # 构建工具名到数据库信息的映射字典，便于快速查找
    database_by_tool = {
        item["tool_name"]: item for item in store.get_tool_databases()
    }
    # 遍历所有支持的工具，拼接工具信息与对应的数据库信息
    return jsonify({
        "tools": [
            {
                **_build_tool_payload(tool_name),
                "database": database_by_tool.get(tool_name),
            }
            for tool_name in get_supported_runners()
        ]
    })


@api_bp.route("/databases", methods=["GET"])
def list_databases():
    """
    列出所有工具对应的数据库表信息

    请求方式: GET
    路径: /api/databases
    参数: 无

    返回示例:
        {
            "databases": [
                {
                    "tool_name": "subfinder",
                    "table_name": "subfinder_results",
                    "record_count": 1234
                },
                ...
            ]
        }

    内部逻辑:
        直接从存储层获取所有工具数据库的元信息列表并返回。
    """
    # 实例化存储层并直接获取数据库元信息
    store = ScanResultStore()
    return jsonify({"databases": store.get_tool_databases()})
