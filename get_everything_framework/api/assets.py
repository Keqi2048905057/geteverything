"""资产 / 观测 API（P1，方案第 8、10 节）。

路由（全部**需本地管理员认证**）：

* ``GET /api/assets``                  —— 资产列表（按范围 / 类型 / 状态 / 关键字筛选）
* ``GET /api/assets/summary``          —— 分类型计数（列表页顶部）
* ``GET /api/assets/{asset_id}``       —— 资产详情 + 观测时间线
* ``GET /api/observations``            —— 观测列表（可按 asset_id / job_id 过滤）
* ``GET /api/jobs/{before}/diff/{after}`` —— 两次任务的 Diff（见 ``api/jobs.py``）

约定：

* 这些接口**只读**，不写库、不触发扫描；
* 出参不含任何服务器路径；
* 与 ``/api/results``（旧库、匿名只读）不同，资产接口读的是**新库**的
  ``assets`` / ``observations``，因而归并在需要登录的一侧 ——
  它是「整理后的情报」，比原始结果行更敏感。
"""

from __future__ import annotations

from flask import jsonify, request

from api import api_bp
from core import assets as assets_store
from core.auth import require_admin
from core.canonical import ASSET_TYPES
from core.errors import BadRequestError, NotFoundError


def _limit(default: int, maximum: int) -> int:
    """解析 ``limit`` 查询参数（非法值回落到默认值）。"""
    try:
        return max(1, min(int(request.args.get("limit") or default), maximum))
    except (TypeError, ValueError):
        return default


@api_bp.route("/assets", methods=["GET"])
def list_assets():
    """列出资产。

    Query 参数:
        scope_id —— 按授权范围过滤（可选）
        type     —— 资产类型（``subdomain`` / ``ip`` / ``url`` / ``port`` …）
        status   —— ``active`` / ``stale`` / ``gone``
        search   —— 值子串匹配（``%`` / ``_`` 已转义）
        limit    —— 默认 100，最大 1000
        offset   —— 分页偏移，默认 0

    Returns:
        ``{"ok": true, "assets": [...], "total": N, "limit": L, "offset": O}``。
    """
    require_admin()

    asset_type = (request.args.get("type") or "").strip().lower() or None
    if asset_type and asset_type not in ASSET_TYPES:
        raise BadRequestError(
            f"不支持的资产类型: {asset_type}",
            details={"supported": list(ASSET_TYPES)},
        )

    status = (request.args.get("status") or "").strip().lower() or None
    if status and status not in assets_store.STATUSES:
        raise BadRequestError(
            f"不支持的状态: {status}",
            details={"supported": list(assets_store.STATUSES)},
        )

    scope_id = (request.args.get("scope_id") or "").strip() or None
    search = (request.args.get("search") or "").strip() or None
    limit = _limit(100, 1000)
    try:
        offset = max(0, int(request.args.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0

    items = assets_store.list_assets(
        scope_id=scope_id,
        asset_type=asset_type,
        status=status,
        search=search,
        limit=limit,
        offset=offset,
    )
    total = assets_store.count_assets(scope_id=scope_id, asset_type=asset_type, status=status)
    return jsonify(
        {
            "ok": True,
            "assets": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "types": list(ASSET_TYPES),
        }
    )


@api_bp.route("/assets/summary", methods=["GET"])
def asset_summary():
    """分类型计数（资产列表页顶部用）。"""
    require_admin()
    scope_id = (request.args.get("scope_id") or "").strip() or None
    return jsonify({"ok": True, **assets_store.asset_summary(scope_id=scope_id)})


@api_bp.route("/assets/<asset_id>", methods=["GET"])
def get_asset(asset_id: str):
    """资产详情 + 观测时间线（最近的在最前）。

    Query 参数:
        observations_limit —— 时间线条数上限（默认 50，最大 500）。
    """
    require_admin()
    asset = assets_store.get_asset(asset_id, include_observations=False)
    if asset is None:
        raise NotFoundError(f"资产不存在: {asset_id}")

    limit = _limit(50, 500)
    asset["observations"] = assets_store.list_observations(asset_id=asset_id, limit=limit)
    return jsonify({"ok": True, "asset": asset})


@api_bp.route("/observations", methods=["GET"])
def list_observations():
    """列出观测记录。

    Query 参数:
        asset_id —— 只看某个资产
        job_id   —— 只看某次任务
        limit    —— 默认 200，最大 2000
    """
    require_admin()
    asset_id = (request.args.get("asset_id") or "").strip() or None
    job_id = (request.args.get("job_id") or "").strip() or None
    if not asset_id and not job_id:
        raise BadRequestError("必须提供 asset_id 或 job_id（避免无条件全表扫描）")

    items = assets_store.list_observations(
        asset_id=asset_id, job_id=job_id, limit=_limit(200, 2000)
    )
    return jsonify({"ok": True, "observations": items, "total": len(items)})
