"""异步任务 API（M3）。

路由（方案第 5.3 节）：

* ``POST /api/jobs``                —— 创建任务，**立即**返回 ``job_id`` 与 ``queued``
* ``GET  /api/jobs``                —— 列出任务
* ``GET  /api/jobs/{job_id}``       —— 任务详情（含 steps 与 events）
* ``GET  /api/jobs/{job_id}/results``—— 任务结果（发现资产 / 服务 / 技术栈 / 风险提示）
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
from core import findings
from core import jobs as jobs_store
from core.application import create_scan_job
from core.auth import require_admin
from core.errors import BadRequestError, NotFoundError


def _query_limit(name: str, *, default: int, maximum: int) -> int:
    """解析一个 ``limit`` 风格的 query 参数（非法值回落到默认值）。

    与 ``api/assets.py:_limit`` 同一口径：**不**因为写错就 400 ——
    分页/上限参数写错时给出默认值比让整页报错更有用，而真正的上限
    始终由这里的 ``maximum`` 与数据层再夹一次。
    """
    try:
        return max(1, min(int(request.args.get(name) or default), maximum))
    except (TypeError, ValueError):
        return default


@api_bp.route("/jobs", methods=["POST"])
def create_job():
    """创建异步任务，立即返回 ``job_id``。

    请求体::

        {
          "scope_id": "scope_xxx",
          "targets": ["example.test"],
          "tools": ["subfinder"],
          "mode": "mock",
          "pace": "normal",          // 可选，见下
          "operator": "张三",         // 可选，操作者标识（审计留痕）
          "rate_limit": 2,           // 可选，每秒请求上限（只能收紧）
          "timeout_seconds": 30,     // 可选，单步超时秒数（只能收紧）
          "scenario": "success",     // 可选，仅 mock
          "idempotency_key": "..."   // 可选，见下
        }

    ``operator`` / ``rate_limit`` / ``timeout_seconds`` 与公网入口
    （``POST /api/public-jobs``）同一口径：**只记录、只收紧**，不是权限开关。
    它们之所以也出现在这条老入口上，是因为「某个字段只在某一条入口生效」
    会让使用者以为另一条入口上写了也管用 —— 同一份规则就该在同一层被接受。

    ``pace``（可选，``light`` / ``normal``，见 ``core/pace.py``）：这是
    「这条任务按多快的节奏跑」的**收紧**开关，不是权限开关 —— 它不能放开
    Scope、``active_scan``、环境总开关与公网工具白名单中的任何一条。
    缺省 ``normal``（= 引入 Scan Profile 之前的行为，不降速、不等待）；
    ``light`` 会降低工具并发与每秒请求数，并在真实步骤之间留出间隔。
    这条入口没有策略模板，基线就是 ``normal``，所以它只能把节奏**调低**
    （写 ``normal`` 与不写等效），不存在「调高」这回事。
    非法值 400，不静默回退 —— 拼错的档位拿到常规档是最危险的错法。

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
        pace=payload.get("pace"),
        # Phase 3「操作者记录 / 限速配置 / 超时配置」：这条历史入口同样接受
        # 这三个字段。它们**只记录、只收紧**（见上方 docstring），因此在这一层
        # 转发它们不会放松任何一道闸门；不转发反而会造成「同一个字段在两条
        # 入口上一个生效一个被忽略」这种更难排查的不一致。
        operator=payload.get("operator"),
        rate_limit=payload.get("rate_limit"),
        timeout_seconds=payload.get("timeout_seconds"),
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


@api_bp.route("/jobs/<job_id>/results", methods=["GET"])
def list_job_results(job_id: str):
    """任务结果：**从 Job 导向结果**（下一阶段方案 Phase 4「结果体验」）。

    一次请求给出四段结果，正是方案第 6 节 Phase 4 点名要展示的内容：

    * ``assets``       —— 发现资产（按类型分组，含「谁发现的」）；
    * ``services``     —— 服务（哪台主机上出现了什么协议 / 端口）；
    * ``technologies`` —— 技术栈（Web 服务器 / 组件 / CDN）；
    * ``risk_hints``   —— 风险提示。

    口径（读代码前先读这段，否则很容易把它当漏洞接口）：

    * 数据**只来自本次任务自己的观测**（``observations.job_id``），
      不看资产历史上被谁见过 —— 与 ``/diff`` 同一口径，理由见
      :func:`core.assets.list_job_assets`；
    * 本框架**不做漏洞扫描**：没有 nuclei，也没有 CVE / 严重级别数据。
      因此 ``risk_hints`` 里每一条都是「从已有观测里读出来的事实」
      （明文 HTTP、5xx、默认欢迎页标题……），``level`` 是
      「值不值得人工看一眼」而不是危险度。**没有提示 ≠ 没有漏洞** ——
      这句结论随 ``notes`` 一起下发，前端必须显示；
    * ``mode=mock`` 的任务不会产生任何观测，返回里会带
      ``notes`` 明确说明「这是预期行为，不是采集失败」。

    其它：

    * ``scope_id``（query，可选）只取该范围下的资产；
    * ``observations_limit``（query，默认 500，最大 2000）限制参与派生的
      观测条数上限 —— 结果页是给人看的，不是导出通道；要完整数据请用
      ``/api/observations?job_id=...``；
    * 全部列表都有上限，且 ``counts`` 里给的是**截断前**的真实数量，
      ``truncated`` 明确告诉你有没有被截断；
    * 只读：不写库、不写审计、不触发任何扫描。

    Returns:
        200 + ``{"ok": true, "job_id", "mode", "scope_id", "counts",
        "assets", "services", "technologies", "risk_hints", "notes"}``。
    """
    require_admin()

    job = jobs_store.get_job(job_id)
    if job is None:
        raise NotFoundError(f"任务不存在: {job_id}")

    scope_id = (request.args.get("scope_id") or "").strip() or None
    observations_limit = _query_limit("observations_limit", default=500, maximum=2000)

    assets = assets_store.list_job_assets(job_id, scope_id=scope_id)
    observations = assets_store.list_observations(job_id=job_id, limit=observations_limit)
    # 步骤只用来判断「覆盖是否完整」（有终态失败步骤 → 提示覆盖不完整），
    # 因此只取需要的那几个字段，不需要整份详情。
    steps = [
        {"tool_name": step.get("tool_name"), "target": step.get("target"), "status": step.get("status")}
        for step in jobs_store.list_steps(job_id)
    ]

    result = findings.summarize(assets, observations, mode=job.get("mode"), steps=steps)
    return jsonify(
        {
            "ok": True,
            "job_id": job_id,
            "mode": job.get("mode"),
            "scope_id": job.get("scope_id"),
            **result,
        }
    )


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
