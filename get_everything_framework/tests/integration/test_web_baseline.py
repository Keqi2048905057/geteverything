"""M1 集成测试：首页、登录页、/health、未登录拒绝扫描。

全部走 Flask 测试客户端，不启动真实服务器、不写仓库数据库。
"""

import json


def test_index_renders_without_template_error(client):
    """M1 验收：首页不再 TemplateNotFound。"""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "本机联调版" in body
    # M2 起首页要求先有 Scope；M3 起首页提交创建的是异步任务。
    assert "任务" in body


def test_login_page_renders(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "管理员 Token" in resp.get_data(as_text=True)


def test_health_needs_no_login_and_hides_paths(client):
    """M1 验收：/health 返回数据库与 worker 状态，且不泄露路径。"""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["version"].startswith("local-")
    assert data["database"] in {"ok", "missing", "error"}
    assert data["worker"] in {"ok", "missing", "stale"}
    assert "tools" in data and "tools_summary" in data

    raw = json.dumps(data, ensure_ascii=False)
    assert "E:\\" not in raw and "E:/" not in raw
    assert "nvarchar" not in raw
    assert "dev-secret-key" not in raw


def test_health_reports_worker_missing_by_default(client):
    """M1 阶段没有独立 worker 进程，必须能被区分出来。"""
    data = client.get("/health").get_json()
    assert data["worker"] in {"missing", "stale"}


def test_anonymous_scan_is_rejected(client):
    """M1 验收：未登录不能执行扫描。"""
    resp = client.post("/api/run", json={"domain": "example.test", "tools": ["subfinder"]})
    assert resp.status_code == 401
    body = resp.get_json()
    assert body["error_code"] == "unauthenticated"


def test_anonymous_single_tool_scan_is_rejected(client):
    resp = client.post("/api/tool/subfinder/run", json={"domain": "example.test"})
    assert resp.status_code == 401


def test_anonymous_settings_read_and_write_are_rejected(client):
    """M1 验收：未登录不能读取或写入设置（含密钥）。"""
    assert client.get("/api/settings").status_code == 401
    assert client.post("/api/settings", json={"llm_api_key": "x"}).status_code == 401
    assert client.get("/api/settings/enscan").status_code == 401
    assert client.post("/api/settings/enscan", json={"enscan_aqc_cookie": "x"}).status_code == 401


def test_anonymous_index_post_scan_is_rejected(client):
    """首页表单扫描同样需要登录（返回 401 JSON）。"""
    resp = client.post("/", data={"action": "scan", "domain": "example.test"})
    assert resp.status_code == 401
    assert resp.get_json()["error_code"] == "unauthenticated"


def test_wrong_token_is_rejected(client):
    resp = client.post(
        "/api/run",
        json={"domain": "example.test", "tools": ["subfinder"]},
        headers={"X-Local-Token": "definitely-wrong"},
    )
    assert resp.status_code == 401


def test_login_flow_grants_session(admin_client, admin_token):
    """登录页 POST 成功后会建立会话，随后首页显示已登录。"""
    client = admin_client
    resp = client.post("/login", data={"token": admin_token}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")

    home = client.get("/")
    assert "已登录" in home.get_data(as_text=True)


def test_login_flow_rejects_bad_token(client):
    resp = client.post("/login", data={"token": "nope"})
    assert resp.status_code == 200
    assert "Token 无效" in resp.get_data(as_text=True)


def test_auth_session_and_logout(client, admin_token):
    status = client.get("/api/auth/session").get_json()
    assert status["authenticated"] is False
    assert status["auth_header"] == "X-Local-Token"

    login = client.post("/api/auth/login", json={"token": admin_token})
    assert login.status_code == 200
    assert client.get("/api/auth/session").get_json()["authenticated"] is True

    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/session").get_json()["authenticated"] is False


def test_admin_token_can_read_settings(admin_client):
    """带上有效 Token 后，设置接口返回脱敏配置。"""
    resp = admin_client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert "settings" in body
