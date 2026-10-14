"""扫描中心 API（原「授权公网测试」入口；测试阶段口径见下）。

路由：

* ``POST /api/public-jobs``        —— 创建扫描任务（需登录）。**兼容入口**：
  响应语义与 ``POST /api/jobs`` 一致，只是额外带上策略与节奏；
* ``POST /api/public-jobs/check``  —— **只读目标预检**：目标格式是否合法、
  规范化结果、是否命中排除列表（需登录）。**不做任何授权判断**；
* ``GET  /api/scan-center``        —— 扫描中心的元数据（策略模板 / 工具可用性 / 节奏档位）

硬性要求：

* **禁止** ``Web → Runner``。本模块只解析请求，然后调用
  :func:`core.application.create_open_public_job`；
* 该函数只做「策略 → 工具」解析与「工具是否已接入」校验，随后委派给
  :func:`core.application.create_scan_job` 走 Policy / mode 判定；
* 因此目标格式非法仍然是 400、未接入的工具在**创建任务之前**就被拒，
  且任务必然带审计记录。

## 口径变更（测试阶段）

历史版本本模块的职责是「授权公网测试」：先查项目、再查项目下的 Scope、
再验公网工具白名单。测试阶段这三件事**全部取消**：

* 前端不再传 ``project_id`` / ``scope_id``；传了也不参与业务逻辑；
* 未开启 ``GEF_OPEN_SCAN_MODE`` 且未给 ``scope_id`` 时，创建任务**明确 400
  并点名这个环境变量** —— 不静默降级成 mock，也不假装成功；
* ``/api/public-jobs/check`` 从「缺哪一道闸门」改为纯粹的**目标预检**。

**没有取消的**：目标格式与排除列表校验（``core.policy`` 的
``validate_job_targets`` / ``validate_step_target``）、SSRF 防护（解析到内网地址
一律拒绝）、限速 / 超时 / 并发限制、审计与结构化日志。
"""

from __future__ import annotations

from flask import jsonify, request

from api import api_bp
from core.application import create_open_public_job, split_str_list
from core.auth import require_admin
from core.errors import BadRequestError, InvalidTargetError, ScopeViolationError
from core.job_limits import describe_limits
from core.pace import list_paces
from core.scope import MATCH_ALLOWED, MATCH_EXCLUDED, Scope, normalize_target, open_scope
from core.tool_registry import (
    KNOWN_UNAVAILABLE_TOOLS,
    group_tool_policies,
    list_strategies,
    list_tool_policies,
    selectable_tools,
)


@api_bp.route("/public-jobs", methods=["POST"])
def create_public_job():
    """创建扫描任务，立即返回 ``job_id``。

    请求体::

        {
          "targets": ["www.example.test"],
          "strategy": "asset_discovery",  // 可选，缺省资产发现
          "tools": ["httpx"],             // 仅 strategy=custom 时使用
          "pace": "light",                // 可选，只能收紧到 light，不能放松
          "mode": "real",                 // 可选，缺省 real
          "operator": "张三",             // 可选，操作者标识（审计留痕）
          "rate_limit": 2,                // 可选，每秒请求上限（只能收紧）
          "timeout_seconds": 30,          // 可选，单步超时秒数（只能收紧）
          "idempotency_key": "..."        // 可选
        }

    **Deprecated 兼容字段**：``project_id`` / ``scope_id`` /
    ``authorization_confirmed`` 仍被接受（旧客户端会传），但只有 ``scope_id``
    还会被 :func:`core.policy.require_scope` 读到 —— 留空即使用开放测试执行
    上下文；``project_id`` 与 ``authorization_confirmed`` 不参与任何判定。

    ``operator`` / ``rate_limit`` / ``timeout_seconds`` 是 Phase 3 的执行上下文：
    它们只被**记录**与**收紧**，不构成任何权限判定。

    Returns:
        202 + 与 ``POST /api/jobs`` 同形状的响应，外加 ``strategy`` /
        ``pace`` / ``operator`` / ``limits``；``project_id`` / ``project_name`` /
        ``authorized_public`` 作为 deprecated 字段恒为 ``None``。

    Raises:
        BadRequestError: 参数缺失、策略非法、工具未接入、``pace`` 非法、
            ``rate_limit`` / ``timeout_seconds`` 越界；未开启开放模式且未给
            ``scope_id``。
        ScopeViolationError: 环境开关未开，或目标越界 / 命中排除列表。
    """
    require_admin()

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise BadRequestError("请求体必须是 JSON 对象")

    submission = create_open_public_job(
        targets=payload.get("targets"),
        # Deprecated：旧客户端仍会传 scope_id；空值走开放模式，非空则照常解析。
        scope_id=payload.get("scope_id"),
        upload_id=payload.get("upload_id"),
        strategy=payload.get("strategy"),
        # 与 ``api/scan.py`` / ``api/jobs.py`` 同一条口径：用 ``in`` 判断
        # 「有没有给」，而不是 ``or`` —— 后者会把 ``tools: []`` 折叠成 ``None``
        # 再让别名 ``tool`` 顶上来，于是「明确不要任何工具」变成「用别名那个工具」。
        tools=payload.get("tools") if "tools" in payload else payload.get("tool"),
        pace=payload.get("pace"),
        mode=payload.get("mode"),
        scenario=payload.get("scenario"),
        idempotency_key=payload.get("idempotency_key"),
        # Phase 3：操作者 / 限速 / 超时。视图层只做取值，解析、校验与落库
        # 全部在 Application Service 里（有测试守卫这条边界）。
        operator=payload.get("operator"),
        # Deprecated：不再参与业务逻辑，仅为兼容旧客户端而接受。
        project_id=payload.get("project_id"),
        authorization_confirmed=payload.get("authorization_confirmed"),
        rate_limit=payload.get("rate_limit"),
        timeout_seconds=payload.get("timeout_seconds"),
    )
    return jsonify(submission.to_dict()), 202


@api_bp.route("/public-jobs/check", methods=["POST"])
def check_public_job_targets():
    """**只读目标预检**：这些目标格式是否合法、会怎么被规范化。

    请求体::

        {
          "targets": ["www.example.test"],   // 或 "a.example.test, b.example.test"
          "scope_id": "scope_xxx"            // Deprecated / 可选，不参与判定
        }

    Returns:
        200 + ::

            {
              "ok": true,
              "checks": [ {raw, normalized, kind, valid, verdict, message,
                           excluded} ],
              "summary": {"total": N, "valid": N, "invalid": N,
                          "excluded": N, "ready": N},
              "suggested_mode": "real" | "mock",
              "scope_id": null
            }

    ``ready`` 的含义已经**不再是「有没有授权」**，而只是「这些目标都合法、
    且没有命中排除列表」—— 即「可以提交」。它不构成任何授权。

    ``checks`` 里每一项：

    ==============  ====================================================
    ``valid``       目标格式是否合法（域名 / IP / CIDR）
    ``normalized``  规范化结果（小写、去 scheme / 端口 / 路径 / 尾点）
    ``kind``        ``domain`` / ``ip`` / ``cidr``
    ``verdict``     ``allowed`` / ``excluded`` / ``invalid``
    ``excluded``    是否命中 ``excluded_domains``（排除优先，仍然生效）
    ``message``     面向使用者的一句话说明
    ==============  ====================================================

    **本接口不创建任务、不写库、不发网络请求，也不写审计** —— 它是查询。
    它**不再返回** ``eligible_scope_ids`` / ``blocker`` / ``matches`` 这类
    授权结论：授权业务已取消，留着它们只会让前端以为还有一道闸门。
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

    # 排除列表来自使用者自己创建的范围；开放模式下没有显式范围，
    # 因此用一个「只查排除列表」的临时 Scope。这里刻意**不**用 Scope 的白名单
    # 判定，因为那正是被取消的那件事。
    exclusion_scope = _exclusion_scope(payload.get("scope_id"))

    checks = [_precheck_target(raw, exclusion_scope) for raw in targets]
    valid = sum(1 for item in checks if item["valid"])
    excluded = sum(1 for item in checks if item["excluded"])
    ready = sum(1 for item in checks if item["valid"] and not item["excluded"])
    return jsonify(
        {
            "ok": True,
            "checks": checks,
            "summary": {
                "total": len(checks),
                "valid": valid,
                "invalid": len(checks) - valid,
                "excluded": excluded,
                "ready": ready,
            },
            # 预检只回答「能不能提交」：全部合法且未被排除即 real，否则 mock。
            "suggested_mode": "real" if ready == len(checks) else "mock",
            # Deprecated：不再回显项目；scope_id 也恒为 None（授权业务已取消）。
            "scope_id": None,
            "project_id": None,
        }
    )


def _exclusion_scope(scope_id: object) -> Scope:
    """取出「排除列表」用于预检：显式范围用它自己的，否则用开放范围的（空）。

    只读、不写库。Scope 不存在时退回开放范围 —— 预检不该因为一个过期的
    ``scope_id`` 就整体失败，那会把「目标格式对不对」这个主问题淹没。
    """
    ref = str(scope_id or "").strip()
    if ref:
        try:
            from core import scope_store

            return scope_store.require(ref)
        except (ScopeViolationError, InvalidTargetError):
            pass
    return open_scope()


def _precheck_target(raw: str, exclusion_scope: Scope) -> dict:
    """对单个目标做格式预检（不碰网络、不写库）。"""
    try:
        target = normalize_target(raw)
    except InvalidTargetError as exc:
        return {
            "raw": raw,
            "normalized": None,
            "kind": None,
            "valid": False,
            "verdict": "invalid",
            "excluded": False,
            "message": str(exc),
        }

    _, verdict = exclusion_scope.match_target(target.value)
    excluded = verdict == MATCH_EXCLUDED
    if excluded:
        message = f"{target.value} 命中排除列表，不会被执行"
    elif verdict == MATCH_ALLOWED:
        message = f"{target.value} 格式合法，可以提交"
    else:
        # 非开放范围且目标不在白名单内。授权业务已取消，因此这**不**阻断提交；
        # 只要格式合法就放行，只是如实提示「它不在某个已建范围内」。
        message = f"{target.value} 格式合法（不在已建授权范围内，不影响提交）"

    return {
        "raw": raw,
        "normalized": target.value,
        "kind": target.kind,
        "valid": True,
        "verdict": "excluded" if excluded else "allowed",
        "excluded": excluded,
        "message": message,
    }


@api_bp.route("/scan-center", methods=["GET"])
def scan_center_metadata():
    """扫描中心渲染所需的只读元数据。

    一次给全，前端不必串行请求三个接口：

    * ``strategies``     —— 三档策略模板（含 ``nuclei`` 这类「未接入但登记在案」
      的项，以及每档的 ``pace`` / ``pace_label``）；
    * ``paces``          —— 扫描节奏档位（``light`` / ``normal``）与各自的含义、
      步骤间隔秒数，供页面解释「低频到底是什么」；
    * ``tools``          —— 工具可用性元数据（风险等级 / **是否可勾选** /
      默认勾选 / 用途说明 / 能力分组）；
    * ``tool_groups``    —— **能力分组本身**（``key`` / 中文名 / 这一栏的说明 /
      本栏工具）。有了它前端才不必写死「资产发现」「服务识别」这些栏位名；
      空分组照样返回，界面据此如实显示「本阶段暂无可用工具」；
    * ``restricted_tools`` —— 登记在案但未接入的工具（如 ``nuclei``），界面显示
      「未接入」并置灰；
    * ``limits``         —— 限速 / 超时的可填范围与中文说明（Phase 3）。同一份
      文案由 :func:`core.job_limits.describe_limits` 维护，因此前端不必抄一遍
      上下界，也不会出现「界面写 100、后端只收 50」这种漂移；
    * ``selectable_tools`` —— 当前可勾选的工具名单（= 全部已接入工具）。
      ``internet_allowed_tools`` 作为 deprecated 兼容键同值返回。

    字段单一来源（方案第 9 节 Tool Registry）：``tools`` 与 ``tool_groups`` 里的
    每个条目都是 :meth:`core.tool_registry.ToolPolicy.to_dict` 的输出，因此同一
    个工具不可能「在清单里一个说明、在分组里另一个说明」；``/api/tools``
    用的是同一个函数，两个接口同样不会漂移（有测试逐字段比对）。

    ``projects`` 已**移除**：授权项目业务取消，页面不再有这一栏。

    含工具与策略元数据，属内部信息，因此**必须登录**。
    """
    require_admin()

    return jsonify(
        {
            "ok": True,
            "strategies": [item.to_dict() for item in list_strategies()],
            "paces": list_paces(),
            # 扁平清单只含「真能跑」的工具（执行与校验用）；分组视图与它**同源同集**，
            # 未接入的 nuclei 不进这里 —— 它由 ``restricted_tools`` 单独说明
            # 「存在但本阶段不可用」，两处各司其职，不在清单里重复出现。
            "tools": [item.to_dict() for item in list_tool_policies()],
            "tool_groups": group_tool_policies(list_tool_policies()),
            "restricted_tools": [item.to_dict() for item in KNOWN_UNAVAILABLE_TOOLS.values()],
            "limits": describe_limits(),
            "selectable_tools": selectable_tools(),
            "internet_allowed_tools": selectable_tools(),  # Deprecated 兼容键
        }
    )
