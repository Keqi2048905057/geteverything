"""异步任务 API（M3）。

路由（方案第 5.3 节）：

* ``POST /api/jobs``                —— 创建任务，**立即**返回 ``job_id`` 与 ``queued``
* ``GET  /api/jobs``                —— 列出任务
* ``GET  /api/jobs/{job_id}``       —— 任务详情（含 steps 与 events）
* ``POST /api/jobs/{job_id}/cancel``—— 请求取消
* ``POST /api/jobs/{job_id}/retry`` —— 重新排队
* ``GET  /api/jobs/{a}/diff/{b}``   —— 两次任务的资产 Diff（方案第 10 节）

硬性要求：

* 全部接口需要本地管理员认证（方案第 2.3 节第 6 条）；
* 创建任务**不允许同步等待**扫描完成，只落库并返回；
* 任何任务必须关联 Scope，目标必须逐个过 Scope 校验（第 2、5 条）；
* mock 是默认模式，real 需要 Scope.active_scan + 环境开关双重确认。
"""

from __future__ import annotations

from flask import jsonify, request

from api import api_bp
from core import artifacts as artifacts_store
from core import assets as assets_store
from core import audit, jobs as jobs_store, uploads
from core import observability
from core.auth import require_admin
from core.errors import BadRequestError, NotFoundError
from core.mock import SCENARIOS, normalize_scenario
from core.policy import validate_job_targets
from core.safety import MODE_MOCK, MODE_REAL, resolve_mode
from config import SCAN_LIMITS


def _split_list(value, field: str) -> list[str]:
    """把请求字段规范化为字符串列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if not isinstance(value, list):
        raise BadRequestError(f"{field} 必须是字符串数组")
    return [str(item).strip() for item in value if str(item).strip()]


def _resolve_targets(payload: dict) -> tuple[list[str], str | None]:
    """从请求体解析目标：``targets`` 显式列表，或 ``upload_id`` 受控上传。

    Returns:
        tuple[list[str], str | None]: ``(targets, upload_id)``。
    """
    upload_id = (payload.get("upload_id") or "").strip() or None
    targets = _split_list(payload.get("targets"), "targets")

    if upload_id:
        targets.extend(uploads.load_targets(upload_id))

    # 去重保序
    seen = set()
    unique: list[str] = []
    for target in targets:
        if target not in seen:
            unique.append(target)
            seen.add(target)
    return unique, upload_id


@api_bp.route("/jobs", methods=["POST"])
def create_job():
    """创建异步任务，立即返回 ``job_id``。

    请求体::

        {
          "scope_id": "scope_xxx",
          "targets": ["example.test"],
          "tools": ["subfinder"],
          "mode": "mock",
          "scenario": "success",     // 可选，仅 mock
          "idempotency_key": "..."   // 可选，见下
        }

    幂等（P0-7a）：带 ``idempotency_key`` 时，同一个键在「上一个同键任务还没终结」
    期间只会产生一个任务；重复请求返回**同一个** ``job_id``，响应里
    ``reused=true``。键的语义是「防重复提交」，不是「永久只跑一次」——任务落到
    终态（succeeded / failed / cancelled）后，同一个键可以再次创建。

    Returns:
        202 + ``{"ok": true, "job_id": ..., "status": "queued", "reused": false}``。
        这里用 202 Accepted 而不是 200：请求已被接受，但**尚未**完成。
        命中幂等键时状态码仍是 202（响应体形状不变），调用方看 ``reused``。
    """
    require_admin()

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise BadRequestError("请求体必须是 JSON 对象")

    scope_id = str(payload.get("scope_id") or "").strip()

    targets, upload_id = _resolve_targets(payload)
    if not targets:
        raise BadRequestError("必须提供 targets 或 upload_id")

    tools = _split_list(payload.get("tools") or payload.get("tool"), "tools")
    if not tools:
        raise BadRequestError("必须提供至少一个工具", details={"field": "tools"})

    # 幂等键由调用方提供，非法（非字符串 / 超长）一律 400，不做静默截断。
    try:
        idempotency_key = jobs_store.normalize_idempotency_key(payload.get("idempotency_key"))
    except ValueError as exc:
        raise BadRequestError(str(exc), details={"field": "idempotency_key"}) from exc

    # 工具名与 /api/run 走同一套校验。
    from tool_runner import load_tools

    try:
        tools = load_tools(tools)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc

    max_targets = SCAN_LIMITS["max_targets_per_job"]
    if len(targets) > max_targets:
        raise BadRequestError(
            f"单个任务最多 {max_targets} 个目标，当前 {len(targets)} 个",
            details={"max_targets_per_job": max_targets},
        )

    # scope_id 缺失 / Scope 不存在 / 目标越界，全部由统一 Policy 入口判定：
    # 缺失 → 400，不存在或越界 → 403（整体拒绝，不部分执行）。
    scope, validated_targets = validate_job_targets(scope_id, targets)

    mode = resolve_mode(payload.get("mode"))
    if mode == MODE_REAL:
        scope.require_active_scan()

    scenario = None
    if mode == MODE_MOCK:
        raw_scenario = payload.get("scenario")
        if raw_scenario is not None and str(raw_scenario).strip().lower() not in SCENARIOS:
            raise BadRequestError(
                f"scenario 仅支持 {', '.join(SCENARIOS)}",
                details={"field": "scenario"},
            )
        scenario = normalize_scenario(raw_scenario) if raw_scenario is not None else None

    job, reused = jobs_store.create_job_with_status(
        scope_id=scope.id,
        targets=validated_targets,
        tools=tools,
        mode=mode,
        upload_id=upload_id,
        scenario=scenario,
        idempotency_key=idempotency_key,
    )
    detail = {
        "scope_id": scope.id,
        "mode": mode,
        "tools": tools,
        "targets": validated_targets,
        "total_steps": job["total_steps"],
    }
    if reused:
        # 命中已有任务：这不是一次「新建」，因此只记一条「重复请求被折叠」
        # 的可追溯记录（审计事件类型仍是 job_created，target 指向那个已存在的任务）。
        detail["idempotency_key"] = idempotency_key
        detail["reused"] = True
    audit.record(audit.EVENT_JOB_CREATED, target_id=job["id"], detail=detail)

    # 方案第 19 节：任务创建也进结构化日志。request_id 由 app.py 的
    # before_request 绑定，这里不用透传；**只记工具名与数量，不记目标列表**
    # （完整目标在 audit_events 与 job 快照里，那是有意留存的审计数据）。
    observability.log_event(
        observability.EVENT_JOB_CREATED,
        job_id=job["id"],
        scope_id=scope.id,
        mode=mode,
        tools=tools,
        target_count=len(validated_targets),
        total_steps=job["total_steps"],
        reused=reused,
    )

    return (
        jsonify(
            {
                "ok": True,
                "job_id": job["id"],
                "status": job["status"],
                "mode": job["mode"],
                "total_steps": job["total_steps"],
                "scope_id": job["scope_id"],
                "reused": reused,
            }
        ),
        202,
    )


@api_bp.route("/jobs", methods=["GET"])
def list_jobs():
    """列出任务（按创建时间倒序）。"""
    require_admin()

    limit = request.args.get("limit", default=50, type=int)
    status = (request.args.get("status") or "").strip() or None
    if status and status not in jobs_store.ALL_STATUSES:
        raise BadRequestError(f"未知状态: {status}", details={"allowed": list(jobs_store.ALL_STATUSES)})

    jobs = jobs_store.list_jobs(limit=limit or 50, status=status)
    return jsonify({"ok": True, "jobs": jobs, "counts": jobs_store.queue_counts()})


@api_bp.route("/jobs/<job_id>", methods=["GET"])
def get_job(job_id: str):
    """任务详情：job + steps + events。"""
    require_admin()

    job = jobs_store.get_job_detail(job_id)
    if job is None:
        raise NotFoundError(f"任务不存在: {job_id}")
    return jsonify({"ok": True, "job": job})


@api_bp.route("/jobs/<job_id>/cancel", methods=["POST"])
def cancel_job(job_id: str):
    """请求取消任务。

    * ``queued``  —— 立即 cancelled；
    * ``running`` —— 置 ``cancel_requested``，worker 在下一个步骤边界收尾；
    * 终态      —— 幂等，返回当前状态。
    """
    require_admin()

    job = jobs_store.request_cancel(job_id)
    if job is None:
        raise NotFoundError(f"任务不存在: {job_id}")

    audit.record(audit.EVENT_JOB_CANCELLED, target_id=job_id, detail={"status": job["status"]})
    return jsonify({"ok": True, "job_id": job_id, "status": job["status"], "cancel_requested": job["cancel_requested"]})


@api_bp.route("/jobs/<job_id>/retry", methods=["POST"])
def retry_job(job_id: str):
    """把可重试的终态任务重新排队。

    已经成功的步骤不会重跑，只重跑未成功部分（``job_steps`` 里保留快照）。
    """
    require_admin()

    try:
        job = jobs_store.retry_job(job_id)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    if job is None:
        raise NotFoundError(f"任务不存在: {job_id}")

    audit.record(audit.EVENT_JOB_RETRY_REQUESTED, target_id=job_id, detail={"attempt": job["attempt"]})
    return jsonify(
        {
            "ok": True,
            "job_id": job_id,
            "status": job["status"],
            "attempt": job["attempt"],
            # 退避（P0-7b）：任务已回到 queued，但要到这个时间之后 worker 才会领。
            # 下发它，前端才能把「排队中」和「在等退避」区分开。
            "next_attempt_at": job["next_attempt_at"],
        }
    )


@api_bp.route("/jobs/<job_id>/steps", methods=["GET"])
def list_job_steps(job_id: str):
    """只看步骤（页面轮询用，比详情轻）。"""
    require_admin()

    if jobs_store.get_job(job_id) is None:
        raise NotFoundError(f"任务不存在: {job_id}")
    steps = jobs_store.list_steps(job_id)
    return jsonify({"ok": True, "job_id": job_id, "steps": steps})


@api_bp.route("/jobs/<job_id>/events", methods=["GET"])
def list_job_events(job_id: str):
    """只看事件时间线。"""
    require_admin()

    if jobs_store.get_job(job_id) is None:
        raise NotFoundError(f"任务不存在: {job_id}")
    events = jobs_store.list_events(job_id, limit=request.args.get("limit", default=200, type=int) or 200)
    return jsonify({"ok": True, "job_id": job_id, "events": events})


@api_bp.route("/jobs/<job_id>/artifacts", methods=["GET"])
def list_job_artifacts(job_id: str):
    """列出该任务的原始证据（stdout/stderr）。

    M4 交付项「结果详情能看到原始证据」。这里只给元数据（ID/大小/sha256），
    要读内容再调 ``GET /api/artifacts/<id>``；**不下发服务器路径**。
    """
    require_admin()

    if jobs_store.get_job(job_id) is None:
        raise NotFoundError(f"任务不存在: {job_id}")
    limit = request.args.get("limit", default=100, type=int) or 100
    return jsonify({"ok": True, "job_id": job_id, "artifacts": artifacts_store.list_artifacts(job_id=job_id, limit=limit)})


@api_bp.route("/artifacts/<artifact_id>", methods=["GET"])
def read_artifact(artifact_id: str):
    """读取一份原始证据的内容（截断 + 脱敏 + 不返回本地路径）。

    方案第 8.2 节禁止「把工具 stderr 原样暴露给前端」，因此这里：

    * 默认最多返回 64 KB，并显式给出 ``truncated``；
    * 对内容做凭据脱敏；
    * 响应里没有 ``path`` 字段。
    """
    require_admin()

    limit = request.args.get("limit", default=artifacts_store.DEFAULT_READ_LIMIT, type=int)
    payload = artifacts_store.read_artifact(artifact_id, limit=max(1, min(int(limit or 0), 1024 * 1024)))
    if payload is None:
        raise NotFoundError(f"原始证据不存在: {artifact_id}")
    return jsonify({"ok": True, "artifact": payload})


@api_bp.route("/jobs/<before_job_id>/diff/<after_job_id>", methods=["GET"])
def diff_jobs(before_job_id: str, after_job_id: str):
    """比较两次任务看到的资产（方案第 10 节 Diff Engine）。

    返回 ``added`` / ``removed`` / ``changed`` / ``unchanged`` 四类，
    每类是资产信息 + 本次观测到的属性。``changed`` 额外带 ``changes``，
    形如 ``{"status_code": {"from": 200, "to": 403}}``。

    Query 参数:
        scope_id           —— 只比较该范围下的资产（可选）
        include_unchanged  —— ``0`` / ``false`` 时不下发 unchanged 明细（默认下发）
    """
    require_admin()

    for job_id in (before_job_id, after_job_id):
        if jobs_store.get_job(job_id) is None:
            raise NotFoundError(f"任务不存在: {job_id}")

    scope_id = (request.args.get("scope_id") or "").strip() or None
    raw_flag = (request.args.get("include_unchanged") or "").strip().lower()
    include_unchanged = raw_flag not in {"0", "false", "no"}

    result = assets_store.diff_jobs(
        before_job_id,
        after_job_id,
        scope_id=scope_id,
        include_unchanged=include_unchanged,
    )
    return jsonify({"ok": True, **result})
