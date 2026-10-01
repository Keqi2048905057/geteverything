"""本地 fixture HTTP 服务：在 ``127.0.0.1`` 上提供确定性响应（方案第 18 节）。

方案第 18 节要求「用本地受控环境验证 target → job → worker → runner →
raw artifact → parser → observation → asset → diff → export」，并明确警告
「不要为了验证真实链路去扫未授权公网目标」。本模块就是那个受控环境：

* **只绑定 ``127.0.0.1``**，端口由操作系统分配（``port=0``），不监听外部网卡；
* 响应完全确定：状态码、标题、``Server`` 头都由本文件写死；
* 支持 ``set_status()`` 就地改路由，用来制造「同一资产两次观测属性不同」，
  这是 diff 的 ``changed`` 分支唯一可复现的来源。

路由::

    /           200  Local Fixture Home      （基线状态；可被 set_status 改成 403）
    /stable     200  Local Fixture Home      （两次任务里都不变 → diff 的 unchanged）
    /extra      200  Local Fixture Extra     （只在第二次任务里出现 → diff 的 added）
    /forbidden  403  Forbidden               （只在第一次任务里出现 → diff 的 removed）
    /missing    404  Not Found
    /redirect   302  → /                     （Location 相对路径）
    其它路径     404  Not Found

> 全部候选路径都返回 200/403，**刻意不依赖 404**：httpx 默认的 ``-mc``
> 匹配集在不同版本间会变，用 404 当「新增资产」会让用例随工具版本漂移。

用法::

    with LocalHttpServer() as server:
        server.base_url             # http://127.0.0.1:54321
        server.url("/forbidden")    # http://127.0.0.1:54321/forbidden
        server.set_status("/", 403, title="Forbidden")   # 制造属性变化
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

DEFAULT_TITLE = "Local Fixture Home"
EXTRA_TITLE = "Local Fixture Extra"
FORBIDDEN_TITLE = "Forbidden"
NOT_FOUND_TITLE = "Not Found"
REDIRECT_TITLE = "Redirecting"

#: ``Server`` 响应头。``BaseHTTPRequestHandler`` 默认把它拼成
#: ``<server_version> <sys_version>``，这里由 :meth:`_FixtureHandler.version_string`
#: 直接接管，保证头值完全确定、可断言。
SERVER_HEADER = "GefFixture/1.0"

#: 路径 → ``(状态码, 标题, Location)``。``Location`` 为 ``None`` 表示不带该头。
_ROUTES: dict[str, tuple[int, str, str | None]] = {
    "/": (200, DEFAULT_TITLE, None),
    "/stable": (200, DEFAULT_TITLE, None),
    "/extra": (200, EXTRA_TITLE, None),
    "/forbidden": (403, FORBIDDEN_TITLE, None),
    "/missing": (404, NOT_FOUND_TITLE, None),
    "/redirect": (302, REDIRECT_TITLE, "/"),
}


class _FixtureHandler(BaseHTTPRequestHandler):
    """只实现 GET：探测工具（httpx / 爬虫 / 解析器）够用，且行为可预期。"""

    server_version = SERVER_HEADER
    sys_version = ""

    def version_string(self) -> str:  # noqa: D102 - 见 SERVER_HEADER 的说明
        return SERVER_HEADER

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler 约定的方法名
        routes = self.server.routes  # type: ignore[attr-defined]
        route = routes.get(self.path)
        if route is None:
            status, title, location = 404, NOT_FOUND_TITLE, None
        else:
            status, title, location = route

        body = (
            "<!doctype html><html><head><title>{title}</title></head>"
            "<body><h1>{title}</h1></body></html>"
        ).format(title=title).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:  # pragma: no cover - 测试不需要访问日志
        """压掉 ``BaseHTTPRequestHandler`` 的 stderr 访问日志。"""


class LocalHttpServer:
    """``127.0.0.1`` 上的确定性 HTTP 服务（上下文管理器可用）。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self._server = ThreadingHTTPServer((host, port), _FixtureHandler)
        self._server.routes = dict(_ROUTES)  # type: ignore[attr-defined]
        # 每个请求一个线程；daemon 保证测试崩溃时也不会挂住进程退出。
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    # ── 地址 ───────────────────────────────────────────────

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def base_url(self) -> str:
        """形如 ``http://127.0.0.1:54321``（**不带**尾斜杠）。"""
        return f"http://127.0.0.1:{self.port}"

    def url(self, path: str = "/") -> str:
        """拼出完整 URL；``path`` 需以 ``/`` 开头。"""
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + path

    # ── 路由控制 ───────────────────────────────────────────

    def set_status(self, path: str, status_code: int, *, title: str | None = None) -> None:
        """就地修改某个路径的状态码（可选同时改标题）。

        这是「同一次扫描里属性发生变化」的唯一来源：先在 diff 基线任务里
        看到 200，改成本方法后再跑一次看到 403，``changed`` 才有内容。
        """
        current = self._server.routes.get(path)  # type: ignore[attr-defined]
        current_title = current[1] if current else NOT_FOUND_TITLE
        current_location = current[2] if current else None
        self._server.routes[path] = (  # type: ignore[attr-defined]
            int(status_code),
            title if title is not None else current_title,
            current_location,
        )

    def routes(self) -> dict[str, tuple[int, str, str | None]]:
        """当前路由表（只读快照，便于断言）。"""
        return dict(self._server.routes)  # type: ignore[attr-defined]

    # ── 生命周期 ───────────────────────────────────────────

    def start(self) -> "LocalHttpServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> "LocalHttpServer":
        return self.start()

    def __exit__(self, *exc_info: object) -> bool:
        self.stop()
        return False
