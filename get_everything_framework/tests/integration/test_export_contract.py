"""P0-5 集成测试：导出接口不得返回服务器路径，且文件可直接下载。

方案第 3.4 节点名的反例是 ``{"path": "C:\\..."}``；本文件锁死修复后的契约：

* ``GET /api/export`` 响应里**没有** ``path`` 字段，只有 ``export_id`` 与 ``download_url``；
* ``GET /api/export/<id>/download`` 能把文件字节流取回来，且文件名与导出时一致；
* 导出记录可追溯（``GET /api/exports`` 列出，且同样不含路径）；
* ``?domain=`` 这类未受信任输入不能穿越导出目录（文件名前缀被清洗）。

鉴权现状（`docs/DECISIONS.md` D 项）：``/api/tools``、``/api/results``、``/api/export``
**当前仍然匿名可读**，属已知项；本轮刻意不改鉴权行为（避免破坏本机脚本兼容性），
因此这里的用例是「锁定当前契约」，而不是「断言它们需要登录」。
"""

import json
import os

import pytest

from core import exports as exports_store


# ── 认证现状（已知项，锁定契约） ──────────────────────────


def test_export_endpoints_are_currently_anonymous(client):
    """锁定 DECISIONS-D 的已知现状：导出接口匿名可读，尚未收口鉴权。

    这条用例是**有意的**：当后续轮次真的给这三个接口加上鉴权时，
    它会失败并提醒执行者同步更新 DECISIONS.md 与 SECURITY.md。
    """
    resp = client.get("/api/export?format=csv")
    assert resp.status_code == 200


# ── 不返回路径 ────────────────────────────────────────────


def test_export_response_has_no_server_path(admin_client):
    resp = admin_client.get("/api/export?format=csv")
    assert resp.status_code == 200
    body = resp.get_json()

    assert body["ok"] is True
    # 关键断言：响应里不能有任何形式的 path 字段。
    assert not any("path" in str(key).lower() for key in body)
    serialized = json.dumps(body, ensure_ascii=False)
    assert "C:\\" not in serialized
    assert ":/" not in serialized.replace("download_url", "")
    assert body["export_id"].startswith("exp_")
    assert body["download_url"] == f"/api/export/{body['export_id']}/download"


def test_export_is_downloadable(admin_client):
    body = admin_client.get("/api/export?format=csv").get_json()
    download = admin_client.get(body["download_url"])

    try:
        assert download.status_code == 200
        assert download.data  # 真的有字节
        assert body["filename"] in download.headers.get("Content-Disposition", "")
        # 下载响应头也不得带本地路径。
        assert "C:\\" not in str(download.headers)
    finally:
        # send_file 会把文件句柄挂在响应上，测试里必须显式关闭，
        # 否则 pytest 会报 "unclosed file"（真实服务由 WSGI 服务器负责关闭）。
        download.close()


def test_export_json_format_download_is_valid_json(admin_client):
    body = admin_client.get("/api/export?format=json").get_json()
    assert body["format"] == "json"
    assert body["filename"].endswith(".json")

    download = admin_client.get(body["download_url"])
    try:
        assert download.status_code == 200
        assert isinstance(json.loads(download.data.decode("utf-8")), list)
    finally:
        download.close()


def test_export_unknown_id_is_404(admin_client):
    resp = admin_client.get("/api/export/exp_does_not_exist/download")
    assert resp.status_code == 404
    assert resp.get_json()["error_code"] == "not_found"


# ── 非法 format 是「用户传错参数」，不是「服务器内部错误」 ──


@pytest.mark.parametrize("fmt", ["xlsx", "pdf", "CSV2", "json ", "c s v", "1", "../csv"])
def test_export_rejects_unsupported_format_with_400(admin_client, fmt):
    """回归：曾经返回 500 ``unknown_error``。

    真实缺陷（本轮实测复现）：``?format=xlsx`` 会让 ``exporter.export_results``
    抛 ``ValueError``，而该异常没有被捕获，一路冒到全局兜底处理器，
    对外就成了一条 500 —— 把「调用方参数写错了」报成了「服务端崩了」。
    修法是**在调用 exporter 之前**用同一份 ``SUPPORTED_FORMATS`` 拦掉。
    """
    resp = admin_client.get(f"/api/export?format={fmt}")
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert body["error_code"] == "bad_request"
    assert body["details"]["field"] == "format"
    assert body["details"]["supported"] == ["csv", "json"]


def test_export_format_check_shares_one_source_of_truth():
    """400 的校验清单与 exporter 内部兜底必须是同一份，否则两边会漂移。"""
    from exporter import SUPPORTED_FORMATS

    assert SUPPORTED_FORMATS == ("csv", "json")


def test_export_defaults_to_csv_when_format_is_absent(client):
    """不传 ``format`` 时保持既有默认行为（csv），不能因为加了校验就改变默认值。"""
    resp = client.get("/api/export")
    assert resp.status_code == 200
    assert resp.get_json()["format"] == "csv"


def test_export_format_is_case_insensitive(admin_client):
    """``?format=CSV`` 大小写不敏感，不能因为加了白名单校验就把它拒了。"""
    resp = admin_client.get("/api/export?format=CSV")
    assert resp.status_code == 200
    assert resp.get_json()["format"] == "csv"


def test_export_id_with_path_traversal_is_rejected(admin_client):
    """``export_id`` 里塞路径分隔符不能变成任意文件读取。"""
    for bad in ["exp_x%2F..%2F..%2Fetc", "exp_x%5C..%5Cwindows"]:
        resp = admin_client.get(f"/api/export/{bad}/download")
        assert resp.status_code in {400, 404}


# ── 记录可追溯 ────────────────────────────────────────────


def test_exports_are_listed_and_traceable(admin_client):
    created = admin_client.get("/api/export?format=csv").get_json()
    listing = admin_client.get("/api/exports").get_json()["exports"]

    entry = next(row for row in listing if row["export_id"] == created["export_id"])
    assert entry["format"] == "csv"
    assert entry["row_count"] == created["row_count"]
    assert entry["sha256"] == created["sha256"]
    # 列表同样不得泄露路径。
    assert all("path" not in row for row in listing)


def test_export_records_are_persisted_with_metadata(admin_client):
    body = admin_client.get("/api/export?format=csv").get_json()
    record = exports_store.get_export(body["export_id"])

    assert record is not None
    assert record["size"] > 0
    assert record["sha256"]
    # 内部形状带 path（供下载路由解析），公开形状不带 —— 两者必须分开。
    assert os.path.isabs(record["path"])
    assert "path" not in exports_store.to_public_dict(record)


def test_unregistered_export_file_is_404(admin_client, tmp_path):
    """登记的文件被删除后，下载必须 404，而不是抛 500 或回落到别的路径。"""
    body = admin_client.get("/api/export?format=csv").get_json()
    record = exports_store.get_export(body["export_id"])
    os.remove(record["path"])

    resp = admin_client.get(body["download_url"])
    assert resp.status_code == 404


# ── 文件名前缀清洗（路径穿越防护） ────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("example.test", "example.test"),
        ("../../evil", "evil"),
        ("..\\..\\evil", "evil"),
        ("a/b/c", "a_b_c"),
        ("a..b", "a.b"),
        ("  ", "results"),
        ("", "results"),
        ("...", "results"),
        ("_._", "results"),
    ],
)
def test_safe_prefix_blocks_traversal(raw, expected):
    from exporter import safe_prefix

    assert safe_prefix(raw) == expected


def test_safe_prefix_is_bounded():
    """超长前缀必须被截断，避免超出文件系统文件名上限。"""
    from exporter import MAX_PREFIX_LENGTH, safe_prefix

    assert len(safe_prefix("a" * 500)) == MAX_PREFIX_LENGTH


def test_export_prefix_cannot_escape_export_dir(admin_client, tmp_path):
    """``?domain=../../pwned`` 不能把导出文件写到导出目录之外。"""
    body = admin_client.get("/api/export?format=csv&domain=../../pwned").get_json()
    record = exports_store.get_export(body["export_id"])

    export_dir = os.path.abspath(os.path.dirname(record["path"]))
    expected_dir = os.path.abspath(str(tmp_path / "exports"))
    assert export_dir == expected_dir
    assert ".." not in record["filename"]
