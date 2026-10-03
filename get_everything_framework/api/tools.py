"""
模块: api/tools.py
功能: 提供工具列表与数据库元信息的查询接口

路由:
  GET /api/tools      — 工具注册表（Tool Registry）+ 关联数据库表信息
  GET /api/databases  — 获取所有工具数据库表的元信息（表名、记录数等）

**Tool Registry（下一阶段方案第 9 节）**
    工具能力元数据的**唯一来源**是 :mod:`core.tool_registry`；本接口与
    ``/api/scan-center`` 只是它的**两个读出点**：工具的中文说明
    （``description``）、能力分组（``tool_group`` / ``tool_group_label``）、
    风险等级（``risk_level`` / ``risk_label``）、是否允许公网
    （``internet_allowed``）全部来自 :meth:`core.tool_registry.ToolPolicy.to_dict`，
    与 ``/api/scan-center`` 是同一次调用的结果 ——
    两个接口不可能对同一个工具给出不同的说明（有测试逐字段比对）。

    **两个读出点不等价，不要互换**（方案第 9 节写「前端动态读取 ``GET /api/tools``」，
    实现走的是 ``/api/scan-center``，理由如下）：

    * 本接口**匿名可读**，只给「工具清单 + 分组」，不含
      ``restricted_tools`` / ``projects`` / ``strategies`` / ``paces`` / ``limits``；
    * ``/api/scan-center`` **需登录**，除工具清单外还带四步流程所需的全部元数据，
      并把未接入的 ``nuclei`` 放进 ``restricted_tools`` 单独说明。

    因此实现没有让前端读本接口：切过来会同时丢掉受限工具说明与整个流程的元数据。
    判断依据与逐字段对照见 ``docs/API.md`` 的「Tool Registry」段。

    条目里同时保留 ``name`` 与 ``tool_name``：``name`` 是这套接口的历史键名
    （脚本在用），``tool_name`` 是注册表与本仓库其它地方的统一键名。
    两者恒等，同时给出是为了不改历史契约、也不引入第二套命名。

    ``category`` 仍然是**观测类别**（``subdomain`` / ``url`` / ``web`` …），
    由运行器自己声明，决定结果落哪张表；它与工具分组 ``tool_group``
    是两件不同的事，字段名刻意不共用（理由见 ``core/tool_registry.py``）。
    **方案第 9 节示例里的 ``category`` 指的是能力分组，对应本仓的 ``tool_group``** ——
    按方案字面读 ``category`` 会拿到观测类别，是错值而不是缺失。
"""

from flask import jsonify

from api import api_bp            # Flask 蓝图实例
from core.tool_registry import (  # Tool Registry：工具能力与权限元数据
    get_tool_policy,
    group_tool_policies,
    list_tool_policies,
)
from modules import build_runner, get_supported_runners  # 工具运行器工厂函数
from storage import ScanResultStore  # 扫描结果持久化存储


def _registry_fields(tool_name: str) -> dict:
    """取一个工具在注册表里的元数据（未登记时给出**明确**的降级值）。

    未登记不应该发生（``tests/unit/test_tool_registry.py`` 要求每个 runner 都有
    条目），但这里也不抛异常：``/api/tools`` 是匿名可读的列表接口，
    为了一个字段缺失而整页 500 比少一个字段更糟。降级值一律取「最保守」：
    ``internet_allowed=False``、``risk_level="high"``。
    """
    policy = get_tool_policy(tool_name)
    if policy is not None:
        return policy.to_dict()
    return {
        "tool_name": tool_name,
        "risk_level": "high",
        "risk_label": "未登记",
        "internet_allowed": False,
        "default_enabled": False,
        "reason": "该工具没有在 core.tool_registry 里登记权限元数据，按禁止处理",
        "description": "",
        "tool_group": "",
        "tool_group_label": "",
    }


def _build_tool_payload(tool_name: str) -> dict:
    """构建单个工具的 API 响应数据结构。

    Args:
        tool_name: 工具名称（如 ``subfinder`` / ``httpx``）。

    Returns:
        dict: 历史字段 + 注册表字段的合并体。

        * ``name`` / ``tool_name`` —— 工具名（两者恒等）；
        * ``category`` —— **观测类别**，从运行器实例读取
          （``subdomain`` / ``url`` / ``alive`` / ``web`` / ``port``）；
        * ``description`` / ``tool_group`` / ``tool_group_label`` —— 注册表里的
          用途说明与能力分组；
        * ``risk_level`` / ``risk_label`` / ``internet_allowed`` /
          ``default_enabled`` / ``reason`` —— 权限元数据。
    """
    # 根据工具名构建运行器实例
    runner = build_runner(tool_name)
    return {
        "name": tool_name,
        "tool_name": tool_name,
        # 安全获取分类属性，缺失时默认为 subdomain
        "category": getattr(runner, "category", "subdomain"),
        **_registry_fields(tool_name),
    }


@api_bp.route("/tools", methods=["GET"])
def list_tools():
    """
    列出所有可用扫描工具（Tool Registry）及其关联的数据库表

    请求方式: GET
    路径: /api/tools
    参数: 无

    返回示例:
        {
            "tools": [
                {
                    "name": "subfinder",
                    "tool_name": "subfinder",
                    "category": "subdomain",
                    "description": "子域名发现：查询证书透明度与被动 DNS 库，不向目标发包。",
                    "tool_group": "recon",
                    "tool_group_label": "资产发现",
                    "risk_level": "low",
                    "risk_label": "低（被动/轻量探测）",
                    "internet_allowed": true,
                    "default_enabled": true,
                    "reason": "",
                    "database": {
                        "tool_name": "subfinder",
                        "table": "subfinder_results",
                        "result_column": "subdomain",
                        "category": "subdomain"
                    }
                },
                ...
            ],
            "groups": [
                {
                    "key": "recon",
                    "name": "资产发现",
                    "description": "子域 / 资产枚举，回答「目标有哪些入口」。",
                    "tools": [ ...同上，仅属于本分组的条目... ]
                },
                ...
            ]
        }

    ``groups`` 供界面按能力分组渲染（方案第 8 节「工具选择中心」）；
    它只包含**已接入 runner** 的工具 —— 未接入的（如 ``nuclei``）不在本接口，
    因为本接口匿名可读，没必要把「还差哪些工具」也一并公开。
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
        ],
        # 分组视图：与 /api/scan-center 的 tool_groups 同源（同一个
        # group_tool_policies()），只是这里的输入限定为「已接入 runner」。
        "groups": group_tool_policies(list_tool_policies()),
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
                    "table": "subfinder_results",
                    "result_column": "subdomain",
                    "category": "subdomain"
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
