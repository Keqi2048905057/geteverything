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
from core import audit
from core import jobs as jobs_store
from core.application import create_scan_job
from core.auth import require_admin
from core.errors import BadRequestError, NotFoundError


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

    实现位置：本视图只做「认证 + 解析 JSON + 拼响应」。目标解析、幂等、
    工具校验、限流、Scope/Policy、模式开关、落库、审计与结构化日志**全部**
    在 :func:`core.application.create_scan_job` 里 —— 那条链是包括 Agent 在内
    所有调用方共用的唯一入口（总方案第 5.2 节 / DSH 方案第 6 节）。
    """
    require_admin()

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise BadRequestError("请求体必须是 JSON 对象")

    submission = create_scan_job(
        scope_id=str(payload.get("scope_id") or "").strip(),
        targets=payload.get("targets"),
        tools=payload.get("tools") or payload.get("tool"),
        upload_id=payload.get("upload_id"),
        mode=payload.get("mode"),
        scenario=payload.get("scenario"),
        idempotency_key=payload.get("idempotency_key"),
    )

    return jsonify(submission.to_dict()), 202


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
