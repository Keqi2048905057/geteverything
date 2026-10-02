"""授权公网测试 API（公网授权测试模式体验版方案第 6 节）。

路由：

* ``POST /api/public-jobs``        —— 在授权项目下创建公网测试任务（需登录）
* ``POST /api/public-jobs/check``  —— **只读试算**：目标落在哪些授权范围内、
  现在缺哪一道闸门（需登录）
* ``GET  /api/scan-center``        —— 扫描中心的元数据（项目 / 策略模板 / 工具权限）

硬性要求（方案第 2、6 节）：

* **禁止** ``Web → Runner``。本模块只解析请求，然后调用
  :func:`core.application.create_authorized_public_job`；
* 该函数会依次过「项目 → 项目内 Scope → 公网工具白名单」，
  再委派给 :func:`core.application.create_scan_job` 走 Policy / Scope / mode 判定；
* 因此未授权目标仍然是 403、被禁工具在**创建任务之前**就被拒，
  且任务必然带审计记录。

``/api/public-jobs/check`` 的存在理由（下一阶段方案第 4、6 节 Phase 2）：
把「目标越界 / 范围没开 active_scan / 环境开关没开」这三件事在**提交之前**
摊开给用户看。它**只读**、不写库、不发网络请求，匹配一律复用
:meth:`core.scope.Scope.match_target`（即 Policy 内部用的同一个方法），
因此不存在第二条判定实现。
"""

from __future__ import annotations

from flask import jsonify, request

from api import api_bp
from core import authorization, projects
from core.application import create_authorized_public_job, split_str_list
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


@api_bp.route("/public-jobs/check", methods=["POST"])
def check_public_job_targets():
    """**只读试算**：这些目标现在能不能扫、缺哪一道闸门。

    请求体::

        {
          "targets": ["www.example.test"],     // 或 "a.example.test, b.example.test"
          "project_id": "proj_xxx"             // 可选，仅用于回显建议范围
        }

    Returns:
        200 + ::

            {
              "ok": true,
              "checks": [ {raw, normalized, kind, valid, matches: [...],
                           candidates, real_scan_enabled, ready, blocker} ],
              "summary": {"total": N, "ready": N, "blocked": N},
              "suggested_mode": "real" | "mock"
            }

    ``blocker`` 的取值与含义：

    ==================  ============================================
    ``no_scope``        一个授权范围都还没建
    ``not_authorized``  有范围，但没有一个覆盖这个目标
    ``scope_inactive``  目标被授权了，但那个范围没开真实扫描
    ``env_disabled``    范围允许，但环境总开关 ``GEF_ALLOW_REAL_SCAN`` 没开
    ``invalid_target``  目标本身格式非法
    （空字符串）        可以扫
    ==================  ============================================

    **本接口不创建任务、不写库、不发网络请求，也不写审计** —— 它是查询。
    真正能不能扫仍由 ``POST /api/public-jobs`` 那条链判定；试算结果只用于
    让用户提前看到卡点，不构成任何授权。
    """
    require_admin()

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise BadRequestError("请求体必须是 JSON 对象")

    raw_targets = payload.get("targets")
    if raw_targets is None:
        raw_targets = payload.get("target")
    # 与 create_scan_job 同口径：接受逗号分隔字符串或数组（复用同一个解析函数，
    # 避免两处对「空项 / 非字符串」的处理不一致）。
    targets = split_str_list(raw_targets, "targets")
    if not targets:
        raise BadRequestError("必须提供至少一个 targets", details={"field": "targets"})

    checks = authorization.check_targets(targets)
    ready = sum(1 for item in checks if item.ready)
    return jsonify(
        {
            "ok": True,
            "checks": [item.to_dict() for item in checks],
            "summary": {"total": len(checks), "ready": ready, "blocked": len(checks) - ready},
            "suggested_mode": authorization.default_mode_for(checks),
            # 回显请求里的项目，便于前端把「建议范围」定位到该项目下的那一个。
            "project_id": (str(payload.get("project_id") or "").strip() or None),
        }
    )


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
