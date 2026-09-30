"""P0-1 契约锁定测试：只读 API 的鉴权现状（已知项，不改行为）。

`docs/DECISIONS.md` D 项预授权 = ✅（限文档与测试）：
**不改鉴权行为**，只把当前契约用测试锁定，并在 README / SECURITY 写明。

本文件是「防回归的说明书」，不是「期望的理想状态」：

* 如果将来有人给这些接口加上鉴权，这些用例会失败 —— 那是**好事**，
  说明必须同步更新 `docs/DECISIONS.md`、`SECURITY.md` 与 README 的鉴权列；
* 反之，如果这些接口意外变成「必须登录但没人知道」，这里也会失败。

同时锁定**写/执行类**接口必须登录（这条是硬边界，不能松）。
"""

import pytest

#: 当前仍然匿名可读的只读接口（已知项，见 DECISIONS-D）。
ANONYMOUS_READABLE = [
    "/api/tools",
    "/api/databases",
    "/api/results",
    "/api/export",
    "/api/exports",
]

#: 必须登录的接口（硬边界）。
ADMIN_ONLY = [
    ("GET", "/api/settings"),
    ("POST", "/api/settings"),
    ("GET", "/api/scopes"),
    ("POST", "/api/scopes"),
    ("POST", "/api/upload"),
    ("POST", "/api/run"),
    ("POST", "/api/tool/subfinder/run"),
    ("POST", "/api/jobs"),
    ("GET", "/api/jobs"),
    ("POST", "/api/jobs/job_x/cancel"),
    ("POST", "/api/jobs/job_x/retry"),
    ("GET", "/api/jobs/job_x/steps"),
    ("GET", "/api/jobs/job_x/events"),
    ("GET", "/api/jobs/job_x/artifacts"),
    ("GET", "/api/artifacts/art_x"),
]


def test_auth_session_itself_is_anonymous_by_design(client):
    """``/api/auth/session`` 必须匿名可读：前端靠它判断「我现在登录了没」。"""
    body = client.get("/api/auth/session").get_json()
    assert body["authenticated"] is False
    assert body["auth_header"] == "X-Local-Token"


@pytest.mark.parametrize("path", ANONYMOUS_READABLE)
def test_readonly_endpoints_are_currently_anonymous(client, path):
    """锁定 DECISIONS-D 的现状：只读接口匿名可读（有意保持，不是遗漏）。"""
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} 不再匿名可读：请同步更新 DECISIONS.md / SECURITY.md / README"
    assert resp.get_json() is not None


@pytest.mark.parametrize("method,path", ADMIN_ONLY)
def test_admin_only_endpoints_reject_anonymous(client, method, path):
    """写 / 执行类接口必须 401 —— 这是不可放松的边界。"""
    resp = client.open(path, method=method, json={})
    assert resp.status_code == 401, f"{method} {path} 竟然允许匿名访问"
    assert resp.get_json()["error_code"] == "unauthenticated"


def test_tools_payload_has_no_server_path(client):
    """/api/tools 匿名可读，但**不能**顺带泄露服务器路径。"""
    import json

    raw = json.dumps(client.get("/api/tools").get_json(), ensure_ascii=False)
    assert "C:\\" not in raw
    assert "local.db" not in raw


def test_results_payload_has_no_server_path(client):
    import json

    raw = json.dumps(client.get("/api/results").get_json(), ensure_ascii=False)
    assert "C:\\" not in raw
    assert "scan_results.db" not in raw


def test_page_scan_still_requires_login(client):
    """首页表单扫描也必须登录（P0 验收：无匿名扫描）。"""
    resp = client.post("/", data={"action": "scan", "domain": "example.test"})
    assert resp.status_code == 401
