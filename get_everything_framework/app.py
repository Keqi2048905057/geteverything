"""
资产收集工具 — 主应用入口

职责:
    - 页面路由 — ``GET /`` 直接跳到扫描中心，``/assets`` 资产页，``/login`` 登录页
    - API Blueprint 注册 — 将 api/ 目录下的所有接口挂载到 /api 前缀
    - 辅助函数 — 规范化、上下文构建

**测试阶段口径（重要）**：原首页（``web/templates/index.html``）连同它的
``POST /`` 表单动作（``action=scan`` / ``action=chat``）已整体退役：

* ``GET /`` 改为 302 → ``/scan-center``，登录成功后也直接落在扫描中心；
* 不再保留一个「隐藏的 POST /」——删掉模板却留着路由，只会得到一个
  ``TemplateNotFound`` 死路由；
* ``agent/``（``handle_agent_message``）**保留**在仓库里，只是本轮不再提供
  旧首页的 Agent Chat UI；将来需要独立 Agent 页面时单独设计 ``/agent``。

API 接口已拆分至 api/ 目录, 便于独立维护和前端对接:

    api/__init__.py    Blueprint 注册
    api/auth.py        POST /api/auth/login, /api/auth/logout, GET /api/auth/session
    api/health.py      GET  /health（无 /api 前缀，无需登录）
    api/tools.py       GET  /api/tools, /api/databases
    api/scan.py        POST /api/run, /api/tool/<name>/run（需登录）
    api/results.py     GET  /api/results, /api/tool/<name>/results, /api/export
"""

import time

from flask import Flask, g, redirect, render_template, request, url_for

from config import Config, MAX_UPLOAD_SIZE, SCAN_LIMITS
from core import assets as assets_store
from core import auth as local_auth
from core import observability
from core.canonical import ASSET_TYPES
from core.errors_handlers import register_error_handlers
from core.security import resolve_secret_key, secret_key_is_ephemeral

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


# ── 请求关联 ID 与访问日志（方案第 19 节：Observability） ─────
# 每个请求绑定一个 ``request_id``：
#   * 入站带了合法的 ``X-Request-Id`` 就沿用（便于跨进程追踪）；
#   * 否则生成 ``req_<uuid4hex>``。
# 该 ID 会带进本请求内所有 ``observability.log_event()`` 事件，并回写响应头，
# 使用者可以拿 URL/头里的 ID 去日志里捞这一跳的全部记录。
# 注意：绝不记录完整 URL（query 里可能带目标列表），只记 path。

@app.before_request
def _bind_request_context():
    request_id = observability.accept_request_id(request.headers.get(observability.REQUEST_ID_HEADER))
    if request_id is None:
        request_id = observability.new_request_id()
    g.request_id = request_id
    g._request_started_at = time.perf_counter()
    # 请求处理可能抛异常，``teardown_request`` 一定会跑，所以在这里补记访问日志。
    g._request_logged = False
    g._observability_token = observability.set_request_id(request_id)


@app.after_request
def _attach_request_id_header(response):
    request_id = getattr(g, "request_id", None)
    if request_id:
        response.headers[observability.REQUEST_ID_HEADER] = request_id
    # 状态码只在这里拿得到（响应对象）；teardown 阶段补记日志时用它。
    g._request_status = response.status_code
    return response


@app.teardown_request
def _log_request_finished(exc):
    request_id = getattr(g, "request_id", None)
    if not request_id or getattr(g, "_request_logged", False):
        return  # 没进到 before_request（极早期失败）或已记过，不重复
    g._request_logged = True

    started = getattr(g, "_request_started_at", None)
    duration_ms = None if started is None else int((time.perf_counter() - started) * 1000)
    if exc is not None:
        # after_request 不一定会跑（它自己之前的钩子抛了），异常路径用 exc 兜底。
        status = getattr(exc, "code", None) or 500
    else:
        status = getattr(g, "_request_status", None)

    observability.log_event(
        observability.EVENT_HTTP_REQUEST,
        level="ERROR" if exc is not None else "INFO",
        request_id=request_id,
        method=request.method,
        # 只用 path：query 里可能带目标列表（方案第 19 节：不记录完整目标列表）。
        path=request.path,
        status=status,
        duration_ms=duration_ms,
        error=type(exc).__name__ if exc is not None else None,
    )
    observability.reset_request_id(getattr(g, "_observability_token", None))


# ── 辅助函数 ──────────────────────────────────────────────
# 字符串规范化（去空白 → 转小写 → 空值返回 None）
# 业务侧用法：normalize(value) 用于任意查询参数。

def normalize(value):
    """规范化通用字符串值：去空白 + 转小写，空值返回 None。"""
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


# ── 页面路由 ──────────────────────────────────────────────
#
# 测试阶段：原首页（``web/templates/index.html``）已整体退役 —— 模板被删除，
# 它唯一的渲染入口 ``index()`` 也一并删除。**不留幽灵路由**：
# 删了模板却留着 ``POST /`` 只会变成 ``TemplateNotFound``，
# 而 ``GET /`` 现在直接 302 到扫描中心。


@app.route("/", methods=["GET"])
def index():
    """根路径 → 扫描中心。

    历史行为是渲染首页（域名扫描 + 任务列表 + Agent 对话）。首页退役后，
    这里只做一次跳转：**没有 POST 分支** —— 旧首页表单的
    ``action=scan`` / ``action=chat`` 两个动作随之消失，不再是一个「隐藏的
    页面接口」。点击进入的人应当直接落在登录后的扫描中心。

    跳转而不是渲染：扫描中心本身已经是完整入口，渲染一个空壳首页只会
    让使用者多点一次。登录态由扫描中心自己处理（未登录时它显示登录提示）。
    """
    return redirect(url_for("scan_center"))


def _load_recent_jobs(limit: int = 10) -> list[dict]:
    """读取任务列表（扫描中心页用）。

    ``limit=0`` 表示当前请求未登录：任务里含目标域名，属于敏感信息，
    匿名访问时一律返回空列表。
    """
    if limit <= 0:
        return []
    try:
        from core import jobs as jobs_store

        return jobs_store.list_jobs(limit=limit)
    except Exception:
        # 页面不能因为任务读取失败就 500。
        return []


@app.route("/assets", methods=["GET"])
def assets_page():
    """资产列表页（P1，方案第 8 / 10 节）。

    页面只负责渲染骨架与筛选下拉框，数据由 ``web/static/assets.js``
    调 ``GET /api/assets`` 拉取 —— 与任务列表页同样走「服务端出骨架、
    前端同源取数」的路子，不引入任何前端框架。

    认证策略与任务列表一致：

    * 页面本身**不强制登录**（否则匿名用户连导航都点不进来）；
    * 但资产内容只在已登录时下发（接口返回 401）。

    **不再下发 ``scopes``**：授权范围不再是这个页面的筛选维度
    （筛选与两次任务对比都只按类型 / 状态 / 值 / 任务），
    传一份用不上的授权清单进来只是多一个泄漏面。
    """
    is_authenticated = local_auth.is_authenticated()
    return render_template(
        "assets.html",
        is_authenticated=is_authenticated,
        asset_types=ASSET_TYPES,
        asset_statuses=assets_store.STATUSES,
    )


@app.route("/scan-center", methods=["GET"])
def scan_center():
    """扫描中心页（测试阶段的**唯一**主页面）。

    页面只渲染骨架，数据全部由 ``web/static/scan_center.js`` 通过
    ``GET /api/scan-center`` 同源拉取 —— 与资产页走同一条路子，
    不引入任何前端框架。

    认证策略与资产页一致：页面本身不强制登录（否则匿名连导航都点不进来），
    但策略与工具元数据只在已登录时下发，接口未登录返回 401。

    **这里不创建任何任务**：页面上的一切提交都只是转发到 ``/api/…``，
    闸门全部在服务端（工具是否已接入 → Policy → 模式开关）。
    视图层刻意不做「能不能扫」的判断，避免出现第二条判定实现。

    下发给模板的是 ``open_scan_enabled``（当前进程是否允许免授权扫描）与
    ``selectable_tools``（可勾选的工具名单）；**不再下发公网白名单与项目**。
    """
    from core.safety import open_scan_enabled, real_scan_enabled
    from core.tool_registry import selectable_tools

    is_authenticated = local_auth.is_authenticated()
    return render_template(
        "scan_center.html",
        is_authenticated=is_authenticated,
        real_scan_enabled=real_scan_enabled(),
        open_scan_enabled=open_scan_enabled(),
        selectable_tools=selectable_tools(),
        max_targets_per_job=SCAN_LIMITS["max_targets_per_job"],
        recent_jobs=_load_recent_jobs(limit=10 if is_authenticated else 0),
        focus_job_id=(normalize(request.values.get("job_id")) or ""),
    )


@app.route("/login", methods=["GET", "POST"])
def login_page():
    """本地管理员登录页。

    GET  —— 渲染登录表单；
    POST —— 校验 Token：成功则建立 Session 并**直接进入扫描中心**，失败则原页返回错误。

    登录成功后只写入 Session，不回显 Token；Token 明文只在本机控制台
    （未配置 .env 时的临时 Token）或用户自己的 .env 中可见。
    """
    error = None
    notice = None

    if request.method == "POST":
        token = request.form.get("token", "")
        if local_auth.login_with_token(token):
            return redirect(url_for("scan_center"))
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
    print(f"  访问地址: http://{Config.WEB_HOST}:{Config.WEB_PORT}/scan-center")
    print("            （根路径 / 会 302 到这里；旧首页已退役）")
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
    """返回已配置好的 Flask 应用（供 waitress 与测试复用）。

    结构化日志不在这里配置：``configure_logging()`` 会把 ``gef`` logger 的
    ``propagate`` 关掉并挂上 stderr handler，那会干扰 pytest 的 ``caplog``
    （测试要靠 root logger 抓事件）。因此只在实际启动进程的入口
    （``python app.py`` / ``python -m jobs.worker``）里调用它。
    """
    return app


if __name__ == "__main__":
    # 结构化日志接管（方案第 19 节）：默认 JSON 单行、级别取自 .env 的
    # GEF_LOG_LEVEL / GEF_LOG_FORMAT。控制台登录提示走 print，保持人读友好。
    observability.configure_logging()
    _print_login_hint()
    from waitress import serve

    # 关闭 debug，默认只绑定 127.0.0.1（方案第 2.3 节第 1 条、M1 验收项）。
    serve(app, host=Config.WEB_HOST, port=Config.WEB_PORT, threads=Config.WEB_THREADS)
