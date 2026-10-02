"""授权测试项目 API（公网授权测试模式体验版方案第 4 节步骤 1、2）。

路由：

* ``POST /api/projects``                    —— 创建授权测试项目（需登录）
* ``GET  /api/projects``                    —— 列出项目（需登录）
* ``GET  /api/projects/{id}``               —— 查看单个项目（需登录）
* ``POST /api/projects/{id}/scopes``        —— 把已存在的 Scope 关联到项目（需登录）

约定（方案第 2 节「禁止绕过 Scope / Policy」）：

* 本模块**只做授权证据的组织**，不判定「能不能扫」。项目下挂的 Scope 仍然要
  自己带 ``active_scan=true`` 才允许真实扫描；
* 关联 Scope 时只校验「Scope 存在」，不复制、不改写 Scope 的任何字段 ——
  Scope 的创建与校验路径保持唯一（``api/scopes.py`` + ``core/scope.py``）。
"""

from __future__ import annotations

from flask import abort, jsonify, request

from api import api_bp
from core import audit, db, projects
from core.auth import require_admin
from core.errors import BadRequestError


def _ensure_schema() -> None:
    """确保应用库与表结构存在（幂等，首次调用建库）。"""
    db.ensure_schema()


@api_bp.route("/projects", methods=["POST"])
def create_project():
    """创建授权测试项目。

    请求体::

        {
          "name": "培正学院授权测试",
          "authorization_note": "2026-10-02 校方信息中心书面授权，仅被动信息收集",
          "owner": "张三",
          "scope_ids": ["scope_xxx"]     // 可选，也可稍后关联
        }

    ``authorization_note`` 是公网扫描的**授权证据**，必填且有最短长度 ——
    留空等于没有授权记录，直接 400。
    """
    require_admin()
    _ensure_schema()

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise BadRequestError("请求体必须是 JSON 对象")

    project = projects.create(
        name=payload.get("name"),
        authorization_note=payload.get("authorization_note"),
        owner=payload.get("owner"),
        scope_ids=payload.get("scope_ids"),
    )
    audit.record(
        audit.EVENT_PROJECT_CREATED,
        target_id=project.id,
        detail={
            "name": project.name,
            "owner": project.owner,
            # 授权说明本身是审计证据，如实记录；它不是密钥。
            "authorization_note": project.authorization_note,
            "scope_ids": list(project.scope_ids),
        },
    )
    return jsonify({"ok": True, "project": project.to_dict()}), 201


@api_bp.route("/projects", methods=["GET"])
def list_projects():
    """列出授权测试项目（按创建时间倒序）。"""
    require_admin()
    _ensure_schema()

    limit = request.args.get("limit", default=200, type=int)
    items = projects.list_all(limit=limit or 200)
    return jsonify({"ok": True, "projects": [item.to_dict() for item in items]})


@api_bp.route("/projects/<project_id>", methods=["GET"])
def get_project(project_id: str):
    """查看单个项目。"""
    require_admin()
    _ensure_schema()

    project = projects.get(project_id)
    if project is None:
        # 404 由统一错误处理器转成 JSON（error_code=not_found）。
        abort(404, description=f"项目不存在: {project_id}")
    return jsonify({"ok": True, "project": project.to_dict()})


@api_bp.route("/projects/<project_id>/scopes", methods=["POST"])
def attach_project_scope(project_id: str):
    """把**已存在**的 Scope 关联到项目下（重复关联幂等）。

    刻意不在这里创建 Scope：Scope 的创建有它自己的校验（全放行通配符拒绝、
    CIDR 解析等），必须走 ``POST /api/scopes`` 那一条路径，不能有第二条。
    """
    require_admin()
    _ensure_schema()

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise BadRequestError("请求体必须是 JSON 对象")

    scope_id = str(payload.get("scope_id") or "").strip()
    project = projects.attach_scope(project_id, scope_id)
    audit.record(
        audit.EVENT_PROJECT_SCOPE_ATTACHED,
        target_id=project.id,
        detail={"scope_id": scope_id, "scope_ids": list(project.scope_ids)},
    )
    return jsonify({"ok": True, "project": project.to_dict()}), 201
