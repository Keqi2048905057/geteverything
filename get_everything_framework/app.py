"""
资产收集工具 — 主应用入口

职责:
    - 页面路由 (/) — 渲染 Web UI + 处理表单提交
    - API Blueprint 注册 — 将 api/ 目录下的所有接口挂载到 /api 前缀
    - 辅助函数 — 规范化、上下文构建

API 接口已拆分至 api/ 目录, 便于独立维护和前端对接:

    api/__init__.py    Blueprint 注册
    api/auth.py        POST /api/auth/login, /api/auth/logout, GET /api/auth/session
    api/health.py      GET  /health（无 /api 前缀，无需登录）
    api/tools.py       GET  /api/tools, /api/databases
    api/scan.py        POST /api/run, /api/tool/<name>/run（需登录）
    api/results.py     GET  /api/results, /api/tool/<name>/results, /api/export
"""

from flask import Flask, redirect, render_template, request, session, url_for

from agent import handle_agent_message
from config import Config, MAX_UPLOAD_SIZE
from core import auth as local_auth
from core.errors import BadRequestError, ScopeViolationError
from core.errors_handlers import register_error_handlers
from core.security import resolve_secret_key, secret_key_is_ephemeral
from storage import ScanResultStore

# ── 应用工厂 ──────────────────────────────────────────────

app = Flask(__name__, template_folder="web/templates", static_folder="web/static")
# 会话密钥：优先用 .env 中的强 SECRET_KEY；弱值/缺失时用进程级一次性密钥，
# 绝不回落到可预测的固定值（方案 M2 交付项）。
app.secret_key = resolve_secret_key()
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE
# 会话 Cookie：HttpOnly，禁止 JS 读取（方案第 3.2 节）。
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

register_error_handlers(app)

# 注册 API Blueprint (所有接口统一挂载在 /api 下)
from api import api_bp  # noqa: E402
app.register_blueprint(api_bp)

# /health 挂在根路径，且不要求登录（方案第 5.1 节）。
from api.health import health_bp  # noqa: E402
app.register_blueprint(health_bp)


# ── 辅助函数 ──────────────────────────────────────────────
# 字符串规范化（去空白 → 转小写 → 空值返回 None）
# 业务侧用法：normalize(value) 用于任意查询参数，normalize_domain 为语义化别名。

def normalize(value):
    """规范化通用字符串值：去空白 + 转小写，空值返回 None。"""
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


# 域名为业务专用语义别名 — 调用方阅读更清晰。
normalize_domain = normalize


def _to_ui_history(history):
    """过滤 agent 历史记录，仅保留 user/assistant 角色"""
    if not history:
        return []
    return [item for item in history if item.get("role") in {"user", "assistant"}]


def build_page_context(
    store,
    domain=None,
    scan_message=None,
    scan_error=None,
    scan_report=None,
    chat_error=None,
):
    """构建页面模板所需的上下文数据"""
    summary = store.get_global_summary()
    domain_results = store.get_results_by_domain(domain) if domain else []
    domain_summary = store.get_domain_summary(domain) if domain else None
    raw_history = session.get("agent_history", [])
    raw_steps = session.get("agent_steps", [])

    return {
        "current_domain": domain or "",
        "scan_message": scan_message,
        "scan_error": scan_error,
        "scan_report": scan_report,
        "chat_error": chat_error,
        "summary": summary,
        "domain_summary": domain_summary,
        "domain_results": domain_results,
        "agent_history": _to_ui_history(raw_history),
        "agent_steps": raw_steps,
        "pending_plan": session.get("pending_plan"),
        "uploaded_targets": session.get("uploaded_targets"),
        "agent_context": session.get("agent_context"),
    }


# ── 页面路由 ──────────────────────────────────────────────

# 会触发真实扫描的首页表单动作（M1 起必须登录，M3 起改为创建异步任务）。
_SCAN_ACTIONS = {"scan"}


def _require_admin_for_page() -> None:
    """页面级认证守卫：未登录时抛 UnauthenticatedError（401）。

    页面路由不使用装饰器，而是显式调用，保证异常统一走
    ``core.errors_handlers``，与 API 的响应格式一致。
    """
    local_auth.require_admin()


@app.route("/", methods=["GET", "POST"])
def index():
    """Web UI 主页: 域名扫描 + 任务列表 + AI Agent 对话

    认证策略：

    * 修改/执行类动作（``action=scan``）必须已通过本地管理员认证；
    * 只读浏览（GET、``action=chat``）不强制登录，避免连首页都打不开。

    Scope 策略（M2 起）：

    * 首页扫描与 ``POST /api/jobs`` 走**同一套** Scope 校验，表单必须选 Scope；
    * 目标越界直接拒绝，不创建任务。

    异步化（M3 起）：

    * 首页提交扫描 = **创建任务**，不再同步等待执行结果；
    * 页面立刻显示 ``queued``，由 worker 进程执行，前端轮询进度；
    * 首页固定 ``mode=mock``（不调用真实外部工具）；真实扫描请用 API。
    """
    store = ScanResultStore()
    domain = normalize_domain(request.values.get("domain"))
    scope_id = normalize(request.values.get("scope_id"))
    job_message = None
    job_error = None
    job_id = normalize(request.values.get("job_id"))
    chat_error = None

    if request.method == "POST":
        action = request.form.get("action", "scan")

        if action in _SCAN_ACTIONS:
            # 未登录时直接 401，不进入创建任务分支。
            _require_admin_for_page()
            if not domain:
                job_error = "请输入要扫描的域名。"
            else:
                try:
                    from api.jobs import _resolve_targets

                    targets, _ = _resolve_targets({"targets": [domain]})
                    from core import jobs as jobs_store
                    from core.policy import validate_job_targets  # 统一 Policy 入口
                    from core.safety import resolve_mode

                    # 首页与 POST /api/jobs 走完全相同的 Scope 判定路径
                    # （scope_id 缺失 → 400；不存在或越界 → 403）。
                    scope, validated = validate_job_targets(scope_id, targets)
                    mode = resolve_mode("mock")

                    job = jobs_store.create_job(
                        scope_id=scope.id,
                        targets=validated,
                        tools=["subfinder"],
                        mode=mode,
                    )
                    job_id = job["id"]
                    job_message = (
                        f"任务已创建：{job_id}（{job['status']}）。"
                        "worker 会异步执行，进度自动刷新。"
                    )
                except ScopeViolationError as exc:
                    job_error = f"Scope 校验失败: {exc.message}"
                except BadRequestError as exc:
                    job_error = exc.message
                except Exception as exc:
                    job_error = f"创建任务失败: {exc}"

        elif action == "chat":
            user_message = request.form.get("agent_message", "").strip()
            if not user_message:
                chat_error = "请输入聊天内容。"
            else:
                history = session.get("agent_history", [])
                pending_plan = session.get("pending_plan")
                uploaded_context = session.get("uploaded_targets")
                context_state = session.get("agent_context")
                try:
                    agent_reply = handle_agent_message(
                        user_message,
                        store=store,
                        history=history,
                        pending_plan=pending_plan,
                        uploaded_context=uploaded_context,
                        context_state=context_state,
                    )
                    if agent_reply.get("focus_domain"):
                        domain = agent_reply["focus_domain"]

                    session["agent_history"] = agent_reply.get("conversation_history", history)[-40:]
                    session["pending_plan"] = agent_reply.get("pending_plan")
                    session["agent_context"] = agent_reply.get("context_state", context_state)

                    steps = agent_reply.get("steps", [])
                    all_steps = session.get("agent_steps", [])
                    all_steps.extend(steps)
                    session["agent_steps"] = all_steps[-50:]
                except Exception as exc:
                    chat_error = f"处理失败: {exc}"
        else:
            job_error = f"未知操作: {action}"

    context = build_page_context(
        store,
        domain=domain,
        scan_message=job_message,
        scan_error=job_error,
        scan_report=None,
        chat_error=chat_error,
    )
    context["is_authenticated"] = local_auth.is_authenticated()
    context["scopes"] = _load_scope_options()
    context["current_scope_id"] = scope_id or ""
    context["focus_job_id"] = job_id or ""
    context["recent_jobs"] = _load_recent_jobs(limit=10 if local_auth.is_authenticated() else 0)
    return render_template("index.html", **context)


def _load_recent_jobs(limit: int = 10) -> list[dict]:
    """读取首页任务列表。

    ``limit=0`` 表示当前请求未登录：任务里含目标域名，属于敏感信息，
    匿名访问时一律返回空列表（只读浏览仍可看首页，但看不到任务内容）。
    """
    if limit <= 0:
        return []
    try:
        from core import jobs as jobs_store

        return jobs_store.list_jobs(limit=limit)
    except Exception:
        # 首页不能因为任务读取失败就 500。
        return []


def _load_scope_options() -> list[dict]:
    """读取首页下拉框需要的 Scope 列表（失败时退化为空列表）。"""
    try:
        from core import scope_store

        return [{"id": scope.id, "name": scope.name, "active_scan": scope.active_scan}
                for scope in scope_store.list_all(limit=50)]
    except Exception:
        # 首页不能因为 Scope 读取失败就 500；下拉框为空并给出提示即可。
        return []


@app.route("/login", methods=["GET", "POST"])
def login_page():
    """本地管理员登录页。

    GET  —— 渲染登录表单；
    POST —— 校验 Token：成功则建立 Session 并跳回首页，失败则原页返回错误。

    登录成功后只写入 Session，不回显 Token；Token 明文只在本机控制台
    （未配置 .env 时的临时 Token）或用户自己的 .env 中可见。
    """
    error = None
    notice = None

    if request.method == "POST":
        token = request.form.get("token", "")
        if local_auth.login_with_token(token):
            return redirect(url_for("index"))
        error = "Token 无效，请检查 .env 中的 LOCAL_ADMIN_TOKEN。"

    if local_auth.is_ephemeral_token():
        notice = "当前使用的是进程内临时 Token：请在启动服务的控制台查看。"

    return render_template("login.html", error=error, notice=notice)


# ── 启动入口 ──────────────────────────────────────────────

def _print_login_hint() -> None:
    """启动时在控制台打印本地管理员登录方式。

    仅输出到本机控制台；API 与页面都不会回显 Token 明文。
    """
    hint = local_auth.login_hint()
    print("\n" + "=" * 62)
    print("  Get Everything Framework — 本机联调版")
    print(f"  访问地址: http://{Config.WEB_HOST}:{Config.WEB_PORT}/")
    print("  登录方式: 页面右上角登录，或请求头携带 X-Local-Token")
    if hint["ephemeral"]:
        print(f"  本次进程临时 Token: {hint['token']}")
        print("  （重启会变化；写入 .env 的 LOCAL_ADMIN_TOKEN 可固定下来）")
    else:
        print("  管理员 Token 已从 .env 的 LOCAL_ADMIN_TOKEN 读取")
    if secret_key_is_ephemeral():
        print("  会话密钥: 进程级一次性（重启后需重新登录）")
    else:
        print("  会话密钥: 已从 .env 的 SECRET_KEY 读取")
    print("=" * 62 + "\n")


def create_app() -> Flask:
    """返回已配置好的 Flask 应用（供 waitress 与测试复用）。"""
    return app


if __name__ == "__main__":
    _print_login_hint()
    from waitress import serve

    # 关闭 debug，默认只绑定 127.0.0.1（方案第 2.3 节第 1 条、M1 验收项）。
    serve(app, host=Config.WEB_HOST, port=Config.WEB_PORT, threads=Config.WEB_THREADS)
