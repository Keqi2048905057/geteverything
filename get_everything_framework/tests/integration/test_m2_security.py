"""M2 集成测试：Scope 强制校验、受控上传、任意 file_path 拒绝、审计。

对应方案 M2 验收项：

* 匿名访问扫描/设置接口返回 401/403；
* 默认配置不会扫描任何外部域名；
* 无 Scope 创建 job 返回 400（job 在 M3 引入，此处先验证 Scope API 与模型）；
* 上传 2 MB 以上文件被拒绝；
* 上传文件不会覆盖旧文件；
* 禁止任意字符串 file_path。
"""

import io
import json

import pytest


# ── Scope API ─────────────────────────────────────────────


def test_scopes_require_admin(client):
    assert client.get("/api/scopes").status_code == 401
    assert client.post("/api/scopes", json={"name": "x"}).status_code == 401


def test_create_and_list_scope(admin_client):
    resp = admin_client.post(
        "/api/scopes",
        json={
            "name": "本地测试范围",
            "allowed_domains": ["example.test"],
            "allowed_cidrs": ["127.0.0.1/32"],
            "active_scan": False,
        },
    )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["ok"] is True
    scope = body["scope"]
    assert scope["id"].startswith("scope_")
    assert scope["allowed_domains"] == ["example.test"]
    assert scope["active_scan"] is False

    listing = admin_client.get("/api/scopes").get_json()
    assert [s["id"] for s in listing["scopes"]] == [scope["id"]]

    one = admin_client.get(f"/api/scopes/{scope['id']}").get_json()
    assert one["scope"]["id"] == scope["id"]


def test_get_unknown_scope_returns_404(admin_client):
    resp = admin_client.get("/api/scopes/scope_missing")
    assert resp.status_code == 404
    assert resp.get_json()["error_code"] == "not_found"


def test_scope_requires_allow_rule(admin_client):
    resp = admin_client.post("/api/scopes", json={"name": "空范围"})
    assert resp.status_code == 400
    assert resp.get_json()["error_code"] == "bad_request"


def test_scope_rejects_allow_all_wildcard(admin_client):
    resp = admin_client.post("/api/scopes", json={"name": "全放行", "allowed_domains": ["*"]})
    assert resp.status_code == 400


def test_scope_rejects_unbounded_cidr(admin_client):
    resp = admin_client.post("/api/scopes", json={"name": "全放行", "allowed_cidrs": ["0.0.0.0/0"]})
    assert resp.status_code == 400


def test_scope_accepts_comma_separated_string(admin_client):
    resp = admin_client.post(
        "/api/scopes",
        json={"name": "逗号写法", "allowed_domains": "a.test, b.test"},
    )
    assert resp.status_code == 201
    assert resp.get_json()["scope"]["allowed_domains"] == ["a.test", "b.test"]


# ── 受控上传 ──────────────────────────────────────────────


def _upload(admin_client, name="targets.txt", content=b"example.test\n", **extra):
    data = {"file": (io.BytesIO(content), name)}
    data.update(extra)
    return admin_client.post("/api/upload", data=data, content_type="multipart/form-data")


def test_upload_requires_admin(client):
    assert _upload(client).status_code == 401


def test_upload_returns_controlled_id_not_path(admin_client):
    resp = _upload(admin_client)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["upload_id"].startswith("upload_")
    assert body["target_count"] == 1
    # 不允许再对外暴露服务器路径
    assert "file_path" not in body
    assert "E:" not in json.dumps(body)


def test_upload_over_limit_is_rejected(admin_client):
    """M2 验收：上传 2 MB 以上文件被拒绝（Flask MAX_CONTENT_LENGTH → 413）。"""
    oversized = b"example.test\n" * 200_000  # ≈ 2.8 MB
    resp = _upload(admin_client, content=oversized)
    assert resp.status_code == 413
    assert resp.get_json()["ok"] is False


def test_upload_rejects_bad_extension(admin_client):
    resp = _upload(admin_client, name="payload.exe", content=b"MZ")
    assert resp.status_code == 400
    assert resp.get_json()["error_code"] == "bad_request"


def test_upload_rejects_empty_target_file(admin_client):
    resp = _upload(admin_client, content=b"### nothing here ###\n")
    assert resp.status_code == 400


# ── 任意 file_path 被拒绝 ─────────────────────────────────


@pytest.mark.parametrize(
    "file_path",
    [
        "C:\\Windows\\win.ini",
        "/etc/passwd",
        "results/scan_results.db",
        "../../secret.txt",
    ],
)
def test_run_rejects_raw_file_path(admin_client, file_path, tmp_path):
    """M2 验收：直接传绝对 file_path 被拒绝。"""
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("should never be read\n", encoding="utf-8")

    resp = admin_client.post(
        "/api/run",
        json={"file_path": file_path, "tools": ["subfinder"]},
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error_code"] == "bad_request"
    assert "upload_id" in body["error_message"]


def test_run_rejects_unknown_upload_id(admin_client):
    resp = admin_client.post(
        "/api/run",
        json={"upload_id": "upload_deadbeef", "tools": ["subfinder"]},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error_code"] == "bad_request"


def test_run_requires_domain_or_upload_id(admin_client):
    resp = admin_client.post("/api/run", json={"tools": ["subfinder"]})
    assert resp.status_code == 400


def test_run_rejects_unknown_tool(admin_client):
    resp = admin_client.post("/api/run", json={"domain": "example.test", "tools": ["nuclei"]})
    assert resp.status_code == 400
    assert resp.get_json()["error_code"] == "bad_request"


# ── 默认目标与审计 ────────────────────────────────────────


def test_no_default_external_target_configured():
    """M2 验收：默认配置不会扫描任何外部域名。"""
    from config import TARGET_CONFIG

    assert TARGET_CONFIG["domains"] == []
    assert TARGET_CONFIG["domain_file"] is None


def test_settings_write_is_audited(admin_client, tmp_path, monkeypatch):
    """M2 验收：设置写入必须写审计日志（且不记录值）。"""
    from api import settings

    env_path = tmp_path / ".env"
    env_path.write_text("LLM_MODEL_ID=old-model\n", encoding="utf-8")
    monkeypatch.setattr(settings, "ENV_PATH", str(env_path))
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path / "backups"))

    resp = admin_client.post("/api/settings", json={"llm_model_id": "new-model"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["updated_fields"] == ["LLM_MODEL_ID"]
    assert body["backup"]  # 写入前做了备份

    from core import audit

    events = audit.list_events(limit=10)
    types = [event["event_type"] for event in events]
    assert audit.EVENT_SETTINGS_UPDATED in types

    entry = next(event for event in events if event["event_type"] == audit.EVENT_SETTINGS_UPDATED)
    assert entry["detail"]["fields"] == ["LLM_MODEL_ID"]
    # 审计详情里不能出现字段值
    assert "new-model" not in json.dumps(entry, ensure_ascii=False)


def test_failed_login_is_audited(client):
    client.post("/api/auth/login", json={"token": "wrong-token"})
    from core import audit

    events = audit.list_events(limit=10)
    assert any(event["event_type"] == audit.EVENT_LOGIN_FAILED for event in events)


def test_health_exposes_mode_and_security_status(client):
    data = client.get("/health").get_json()
    assert data["modes"]["default_mode"] == "mock"
    assert data["modes"]["real_scan_enabled"] is False
    assert data["security"]["secret_key"] in {"configured", "ephemeral"}
    assert data["security"]["debug_enabled"] is False
    assert data["security"]["bind_host_default"] == "127.0.0.1"
