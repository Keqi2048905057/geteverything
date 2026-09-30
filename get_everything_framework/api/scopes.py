"""授权范围 API（M2）。

路由：

* ``POST /api/scopes``      —— 创建授权范围（需本地管理员认证）
* ``GET  /api/scopes``      —— 列出授权范围（需本地管理员认证）
* ``GET  /api/scopes/{id}`` —— 查看单个授权范围（需本地管理员认证）

约定（方案第 5.2 节）：

* ``active_scan=false`` 时只允许被动的本机 fixture 测试；
* 真实工具扫描必须显式设置 ``active_scan=true`` **且**环境开关
  ``GEF_ALLOW_REAL_SCAN=true``；
* 每个 job 必须关联 ``scope_id``（M3 落地）。
"""

from __future__ import annotations

from flask import abort, jsonify, request

from api import api_bp
from core import audit, db, scope_store
from core.auth import require_admin
from core.errors import BadRequestError


def _ensure_schema() -> None:
    """确保应用库与表结构存在（幂等，首次调用建库）。"""
    db.ensure_schema()


def _payload_to_scope_kwargs(payload: dict) -> dict:
    """把请求体转换为 ``scope_store.create`` 的关键字参数。"""
    if not isinstance(payload, dict):
        raise BadRequestError("请求体必须是 JSON 对象")

    name = str(payload.get("name") or "").strip()
    if not name:
        raise BadRequestError("name 为必填项")

    def _as_list(key: str) -> list[str]:
        value = payload.get(key)
        if value is None:
            return []
        if isinstance(value, str):
            # 容忍逗号分隔的单字符串写法，避免调用方为一行输入写数组。
            return [item.strip() for item in value.split(",") if item.strip()]
        if not isinstance(value, list):
            raise BadRequestError(f"{key} 必须是字符串数组")
        return [str(item).strip() for item in value if str(item).strip()]

    allowed_domains = _as_list("allowed_domains")
    allowed_cidrs = _as_list("allowed_cidrs")

    if not allowed_domains and not allowed_cidrs:
        raise BadRequestError("至少需要提供 allowed_domains 或 allowed_cidrs 中的一项")

    return {
        "name": name,
        "allowed_domains": allowed_domains,
        "allowed_cidrs": allowed_cidrs,
        "excluded_domains": _as_list("excluded_domains"),
        "active_scan": bool(payload.get("active_scan", False)),
    }


@api_bp.route("/scopes", methods=["POST"])
def create_scope():
    """创建授权范围。"""
    require_admin()
    _ensure_schema()

    payload = request.get_json(silent=True)
    if payload is None:
        raise BadRequestError("请求体必须是 JSON")

    scope = scope_store.create(**_payload_to_scope_kwargs(payload))
    audit.record(
        audit.EVENT_SCOPE_CREATED,
        target_id=scope.id,
        detail={
            "name": scope.name,
            "allowed_domains": scope.allowed_domains,
            "allowed_cidrs": scope.allowed_cidrs,
            "active_scan": scope.active_scan,
        },
    )
    return jsonify({"ok": True, "scope": scope.to_dict()}), 201


@api_bp.route("/scopes", methods=["GET"])
def list_scopes():
    """列出授权范围。"""
    require_admin()
    _ensure_schema()

    limit = request.args.get("limit", default=200, type=int)
    scopes = scope_store.list_all(limit=limit or 200)
    return jsonify({"ok": True, "scopes": [scope.to_dict() for scope in scopes]})


@api_bp.route("/scopes/<scope_id>", methods=["GET"])
def get_scope(scope_id: str):
    """查看单个授权范围。"""
    require_admin()
    _ensure_schema()

    scope = scope_store.get(scope_id)
    if scope is None:
        # 404 由 HTTPException 处理器统一转成 JSON（error_code=bad_request）。
        abort(404, description=f"Scope 不存在: {scope_id}")
    return jsonify({"ok": True, "scope": scope.to_dict()})
