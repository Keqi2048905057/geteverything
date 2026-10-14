"""通过**真实 HTTP 接口**确认：被安全验证阻断的 enscan 任务，页面拿到的是什么。

前面两份用例分别钉住 Runner 层与执行器层。这一份再往上一层，走
``GET /api/jobs/<id>`` —— 前端 ``renderDetail(job)`` 消费的就是这个响应体。
只有这样才回答得了「页面是否错误地显示成成功」。

不联网、不调用真实 ENScan：子进程边界被替换成固定的退出码与真实 stderr。
"""

from pathlib import Path

import pytest

from core import jobs as jobs_store

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
BLOCKED_STDERR = (FIXTURES / "enscan_aqc_blocked_stderr.txt").read_text(encoding="utf-8")
CLEAN_STDERR = (FIXTURES / "enscan_no_results_stderr.txt").read_text(encoding="utf-8")


@pytest.fixture
def admin_client(app_module, admin_token):
    """已认证的 Flask 测试客户端。

    ``GET /api/jobs/<id>`` 需要管理员权限；这里带上 ``X-Local-Token``，
    走的就是浏览器登录后那条真实路径。
    """
    client = app_module.app.test_client()
    client.environ_base["HTTP_X_LOCAL_TOKEN"] = admin_token
    return client


@pytest.fixture
def blocked_job(admin_client, app_module, monkeypatch, tmp_path):
    """跑完一个真实的 enscan 任务，返回 (job_id, API 响应里的 job)。"""
    from core import scope_store
    from modules import base as base_module
    from modules.enscan import ENScanRunner

    output_dir = tmp_path / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(base_module, "OUTPUT_DIR", str(output_dir), raising=False)
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")
    monkeypatch.setattr(
        ENScanRunner, "_run_subprocess", lambda self, cmd, timeout, cwd=None: (0, "", BLOCKED_STDERR)
    )

    scope_id = scope_store.create(
        name="enscan 阻断接口范围", allowed_domains=["example.test"], active_scan=True
    ).id
    job = jobs_store.create_job(
        scope_id=scope_id, targets=["example.test"], tools=["enscan"], mode="real"
    )
    jobs_store.claim_next_job("w1", lease_seconds=300)

    from jobs.executor import execute_job

    execute_job(job["id"])
    return job["id"]


def _get_job(client, job_id):
    response = client.get(f"/api/jobs/{job_id}")
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["job"]


def test_api_job_detail_shows_blocked_not_success(blocked_job, admin_client):
    """页面拿到的 ``job`` 里，这一步必须是 failed/source_blocked，而不是成功。"""
    job = _get_job(admin_client, blocked_job)

    step = job["steps"][0]
    assert step["status"] == "failed"
    assert step["status"] != "succeeded"
    assert step["error_code"] == "source_blocked"
    assert step["error_code"] != "no_results"
    assert step["found_count"] == 0
    # 页面上那行中文说明必须能让使用者知道「去完成验证」。
    assert "安全验证" in step["error_message"]
    assert "爱企查" in step["error_message"]


def test_api_step_preview_is_readable_and_scrubbed(blocked_job, admin_client):
    """原始证据那一栏不能是 ANSI 乱码，也不能泄露 Cookie。"""
    job = _get_job(admin_client, blocked_job)

    preview = job["steps"][0]["stderr_preview"]
    assert preview
    assert "\x1b" not in preview, "ANSI 转义会让页面显示成 [[31mERR[0m]"
    assert "安全验证" in preview
    assert len(preview) <= 600


def test_api_job_level_status_is_not_success(blocked_job, admin_client):
    """任务级不能报成功 —— 这是页面最顶上那几行。"""
    job = _get_job(admin_client, blocked_job)

    assert job["status"] != "succeeded"
    assert job["status"] == "failed"


def test_api_job_level_agrees_with_the_step_row(blocked_job, admin_client):
    """任务顶部的错误码不能与正下方的步骤行互相打架。"""
    job = _get_job(admin_client, blocked_job)

    assert job["error_code"] == "source_blocked"
    assert job["error_code"] == job["steps"][0]["error_code"]
    assert "安全验证" in job["error_message"]
    assert "未知错误" not in job["error_message"]


def test_clean_run_still_reports_success_over_api(admin_client, app_module, monkeypatch, tmp_path):
    """对照组：正常零结果在接口上仍然是 succeeded + no_results。"""
    from core import scope_store
    from modules import base as base_module
    from modules.enscan import ENScanRunner

    output_dir = tmp_path / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(base_module, "OUTPUT_DIR", str(output_dir), raising=False)
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")
    monkeypatch.setattr(
        ENScanRunner, "_run_subprocess", lambda self, cmd, timeout, cwd=None: (0, "", CLEAN_STDERR)
    )

    scope_id = scope_store.create(
        name="enscan 正常范围", allowed_domains=["example.test"], active_scan=True
    ).id
    job = jobs_store.create_job(
        scope_id=scope_id, targets=["example.test"], tools=["enscan"], mode="real"
    )
    jobs_store.claim_next_job("w1", lease_seconds=300)

    from jobs.executor import execute_job

    execute_job(job["id"])

    detail = _get_job(admin_client, job["id"])
    assert detail["status"] == "succeeded"
    assert detail["steps"][0]["status"] == "succeeded"
    assert detail["steps"][0]["error_code"] == "no_results"
