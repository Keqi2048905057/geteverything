"""GET /health —— 无需登录的健康检查（方案第 5.1 节）。

单独使用一个无 ``/api`` 前缀的 Blueprint，因此挂在根路径 ``/health`` 上。
响应只包含状态字符串与版本号，不含路径、密钥与完整命令。
"""

from flask import Blueprint, jsonify

from core.health import collect_health

health_bp = Blueprint("health", __name__)


@health_bp.route("/health", methods=["GET"])
def health():
    """返回 Web / 数据库 / worker / 工具的整体健康状态。"""
    return jsonify(collect_health())
