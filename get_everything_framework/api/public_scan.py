"""授权公网测试 API（公网授权测试模式体验版方案第 6 节）。

路由：

* ``POST /api/public-jobs``        —— 在授权项目下创建公网测试任务（需登录）
* ``POST /api/public-jobs/check``  —— **只读试算**：目标落在哪些授权范围内、
  现在缺哪一道闸门（需登录）
* ``GET  /api/scan-center``        —— 扫描中心的元数据（项目 / 策略模板 / 工具权限 / 节奏档位）

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
from core.job_limits import describe_limits
from core.pace import list_paces
from core.tool_registry import (
    KNOWN_UNAVAILABLE_TOOLS,
    group_tool_policies,
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
          "pace": "light",                // 可选，只能收紧到 light，不能放松
          "mode": "real",                 // 可选，缺省 real
          "operator": "张三",             // 可选，操作者标识（审计留痕）
          "authorization_confirmed": true, // 可选，页面上的授权确认复选框
          "rate_limit": 2,                // 可选，每秒请求上限（只能收紧）
          "timeout_seconds": 30,          // 可选，单步超时秒数（只能收紧）
          "idempotency_key": "..."        // 可选
        }

    ``operator`` / ``authorization_confirmed`` / ``rate_limit`` /
    ``timeout_seconds`` 是 Phase 3（规划方案第 14 节「公网授权测试完善」）的
    五项之二：它们只被**记录**与**收紧**，不构成任何权限判定。
    ``authorization_confirmed`` 尤其不是闸门 —— 授权由 Scope / Policy /
    环境开关决定，一个可被脚本置真的复选框不该成为安全边界。

    Returns:
        202 + 与 ``POST /api/jobs`` 同形状的响应，外加 ``project_id`` /
        ``project_name`` / ``strategy`` / ``authorized_public`` / ``pace`` /
        ``operator`` / ``authorization_confirmed`` / ``limits``。

    Raises:
        BadRequestError: 参数缺失、策略非法、工具不在公网白名单、``pace`` 非法、
            ``rate_limit`` / ``timeout_seconds`` 越界。
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
        pace=payload.get("pace"),
        mode=payload.get("mode"),
        scenario=payload.get("scenario"),
        idempotency_key=payload.get("idempotency_key"),
        # Phase 3：操作者 / 授权确认 / 限速 / 超时。视图层只做取值，
        # 解析、校验与落库全部在 Application Service 里（有测试守卫这条边界）。
        operator=payload.get("operator"),
        authorization_confirmed=payload.get("authorization_confirmed"),
        rate_limit=payload.get("rate_limit"),
        timeout_seconds=payload.get("timeout_seconds"),
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
                           eligible_scope_ids, candidates, real_scan_enabled,
                           resolved_check_deferred, ready, blocker} ],
              "summary": {"total": N, "ready": N, "blocked": N},
              "suggested_mode": "real" | "mock",
              "project_id": "proj_xxx" | null
            }

    ``eligible_scope_ids`` 是**唯一**的「放行」结论 —— 只含 ``verdict == allowed``
    的 Scope ID。``matches`` 还会带上命中排除列表的范围（``verdict = "excluded"``，
    有诊断价值：让用户看出是自己写的排除列表挡住了），但它**不构成授权**，
    因此不进 ``eligible_scope_ids``。前端的「自动匹配授权资产」直接消费这个字段
    （``web/static/scan_center.js:applyMatchedScope``），**不自己重算 verdict** ——
    重算就是第二条授权判定，改一处漏一处。

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
    * ``strategies``     —— 三档策略模板（含 ``nuclei`` 这类「受限但登记在案」的项，
      以及每档的 ``pace`` / ``pace_label``）；
    * ``paces``          —— 扫描节奏档位（``light`` / ``normal``）与各自的含义、
      步骤间隔秒数，供页面解释「低频到底是什么」；
    * ``tools``          —— 工具权限元数据（风险等级 / 是否允许公网 / 默认勾选 /
      用途说明 / 能力分组）；
    * ``tool_groups``    —— **能力分组本身**（``key`` / 中文名 / 这一栏的说明 /
      本栏工具）。有了它前端才不必写死「资产发现」「服务识别」这些栏位名；
      空分组照样返回，界面据此如实显示「本阶段暂无可用工具」；
    * ``limits``         —— 限速 / 超时的可填范围与中文说明（Phase 3）。同一份
      文案由 :func:`core.job_limits.describe_limits` 维护，因此前端不必抄一遍
      上下界，也不会出现「界面写 100、后端只收 50」这种漂移；
    * ``internet_allowed_tools`` —— 第一阶段公网白名单，便于前端把禁用项置灰。

    字段单一来源（方案第 9 节 Tool Registry）：``tools`` 与 ``tool_groups`` 里的
    每个条目都是 :meth:`core.tool_registry.ToolPolicy.to_dict` 的输出，因此同一
    个工具不可能「在清单里一个说明、在分组里另一个说明」；``/api/tools``
    用的是同一个函数，两个接口同样不会漂移（有测试逐字段比对）。

    含项目名称与授权说明，属敏感信息，因此**必须登录**。
    """
    require_admin()

    items = projects.list_all(limit=200)
    return jsonify(
        {
            "ok": True,
            "projects": [item.to_dict() for item in items],
            "strategies": [item.to_dict() for item in list_strategies()],
            "paces": list_paces(),
            # 扁平清单只含「真能跑」的工具（执行与校验用）；分组视图与它**同源同集**，
            # 未接入的 nuclei 不进这里 —— 它由 ``restricted_tools`` 单独说明
            # 「存在但本阶段不可用」，两处各司其职，不在清单里重复出现。
            "tools": [item.to_dict() for item in list_tool_policies()],
            "tool_groups": group_tool_policies(list_tool_policies()),
            "restricted_tools": [item.to_dict() for item in KNOWN_UNAVAILABLE_TOOLS.values()],
            "limits": describe_limits(),
            "internet_allowed_tools": internet_allowed_tools(),
        }
    )
