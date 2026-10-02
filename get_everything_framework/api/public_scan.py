"""授权公网测试 API（公网授权测试模式体验版方案第 6 节）。

路由：

* ``POST /api/public-jobs`` —— 在授权项目下创建公网测试任务（需登录）
* ``GET  /api/scan-center`` —— 扫描中心的元数据（项目 / 策略模板 / 工具权限）

硬性要求（方案第 2、6 节）：

* **禁止** ``Web → Runner``。本模块只解析请求，然后调用
  :func:`core.application.create_authorized_public_job`；
* 该函数会依次过「项目 → 项目内 Scope → 公网工具白名单」，
  再委派给 :func:`core.application.create_scan_job` 走 Policy / Scope / mode 判定；
* 因此未授权目标仍然是 403、被禁工具在**创建任务之前**就被拒，
  且任务必然带审计记录。
"""

from __future__ import annotations

from flask import jsonify, request

from api import api_bp
from core import projects
from core.application import create_authorized_public_job
from core.auth import require_admin
from core.errors import BadRequestError
from core.tool_registry import (
    KNOWN_UNAVAILABLE_TOOLS,
    internet_allowed_tools,
    list_strategies,
    list_tool_policies,
)


@api_bp.route("/public-jobs", methods=["POST"])
def create_public_job():
    """在授权项目下创建公网测试任务，立即返回 ``job_id``。

    请求体::

        {
          "project_id": "proj_xxx",
          "scope_id": "scope_xxx",        // 必须是该项目下已关联的 Scope
          "targets": ["www.example.test"],
          "strategy": "asset_discovery",  // 可选，缺省资产发现
          "tools": ["httpx"],             // 仅 strategy=custom 时使用
          "mode": "real",                 // 可选，缺省 real
          "idempotency_key": "..."        // 可选
        }

    Returns:
        202 + 与 ``POST /api/jobs`` 同形状的响应，外加 ``project_id`` /
        ``strategy`` / ``authorized_public``。

    Raises:
        BadRequestError: 参数缺失、策略非法、工具不在公网白名单。
        NotFoundError: 项目不存在。
        ScopeViolationError: Scope 未开 ``active_scan``，或环境开关未开，或目标越界。
    """
    require_admin()

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise BadRequestError("请求体必须是 JSON 对象")

    submission = create_authorized_public_job(
        project_id=payload.get("project_id"),
        scope_id=payload.get("scope_id"),
        targets=payload.get("targets"),
        upload_id=payload.get("upload_id"),
        strategy=payload.get("strategy"),
        tools=payload.get("tools") or payload.get("tool"),
        mode=payload.get("mode"),
        scenario=payload.get("scenario"),
        idempotency_key=payload.get("idempotency_key"),
    )
    return jsonify(submission.to_dict()), 202


@api_bp.route("/scan-center", methods=["GET"])
def scan_center_metadata():
    """扫描中心渲染所需的只读元数据。

    一次给全，前端不必串行请求三个接口：

    * ``projects``       —— 可选项目（含已关联 Scope 的 ID，但不含目标清单）；
    * ``strategies``     —— 三档策略模板（含 ``nuclei`` 这类「受限但登记在案」的项）；
    * ``tools``          —— 工具权限元数据（风险等级 / 是否允许公网 / 默认勾选）；
    * ``internet_allowed_tools`` —— 第一阶段公网白名单，便于前端把禁用项置灰。

    含项目名称与授权说明，属敏感信息，因此**必须登录**。
    """
    require_admin()

    items = projects.list_all(limit=200)
    return jsonify(
        {
            "ok": True,
            "projects": [item.to_dict() for item in items],
            "strategies": [item.to_dict() for item in list_strategies()],
            "tools": [item.to_dict() for item in list_tool_policies()],
            "restricted_tools": [item.to_dict() for item in KNOWN_UNAVAILABLE_TOOLS.values()],
            "internet_allowed_tools": internet_allowed_tools(),
        }
    )
