"""P0-6（阶段一）：统一 Application Service 入口的单元测试。

方案第 6 节要求「先增加统一 Service/Policy 入口，再迁移调用方，不要一次性
无边界重写」。本文件锁死**阶段一**的验收口径：

* 创建扫描任务的编排只有一处实现（``core/application.create_scan_job``）；
* 它确实是所有调用方共用的那一个（HTTP API 与首页表单都指向它）；
* Scope 判定仍只走 ``core.policy``，服务层自己不做白名单比较；
* 参数错误/越界/real 未开开关的报错口径与历史行为逐条一致。

本文件不发起任何真实扫描：只创建（claim 之前）任务，不驱动 worker。
"""

import io
import inspect

import pytest

from core import application
from core.application import JobSubmission, create_scan_job, resolve_targets, split_str_list
from core.errors import BadRequestError, ScopeViolationError


@pytest.fixture
def scope_id(app_module):
    """建一个只放行 example.test 的 Scope（``active_scan`` 由用例自行叠加）。

    依赖 ``app_module`` 而不是 ``local_db``：本文件既要直接调服务层，也要走
    ``admin_client`` 做等价性对照，两者必须落在**同一个**临时库上；
    ``app_module`` 同时还把上传目录、导出目录等一并指到临时目录，
    避免任何用例把文件写进仓库的 ``results/`` 或 ``uploads/``。
    """
    from core import scope_store

    scope = scope_store.create(name="服务层测试范围", allowed_domains=["example.test"], active_scan=False)
    return scope.id


def _upload(content: bytes = b"a.example.test\nb.example.test\n") -> str:
    from werkzeug.datastructures import FileStorage

    from core import uploads as core_uploads

    return core_uploads.save_upload(FileStorage(stream=io.BytesIO(content), filename="targets.txt"))["upload_id"]


# ── 单入口：实现只有一处，且被 API 与页面共用 ──────────────


def test_service_module_is_the_only_job_creation_orchestration():
    """``api/jobs.py`` 不得再内联编排：不再直接调 Policy / 落库 / 审计。

    这是阶段一最容易回退的地方 —— 有人为图省事把一段逻辑抄回视图函数，
    于是「Agent 走 Service」这条约定就悄悄失效了。
    """
    from api import jobs as jobs_api

    source = inspect.getsource(jobs_api)
    for forbidden in (
        "validate_job_targets",
        "create_job_with_status",
        "normalize_idempotency_key",
        "resolve_mode",
        "audit.record(audit.EVENT_JOB_CREATED",
    ):
        assert forbidden not in source, f"api/jobs.py 又出现了内联编排: {forbidden}"


def test_app_page_delegates_to_service():
    """首页表单也必须走 Service（此前它反向导入 api 层的私有函数）。"""
    import app as app_module

    source = inspect.getsource(app_module)
    assert "create_scan_job" in source
    assert "from api.jobs import _resolve_targets" not in source


def test_service_does_not_implement_scope_matching():
    """服务层只能转交 Policy，不得自己比较 allowed_domains。"""
    source = inspect.getsource(application)
    assert "validate_job_targets" in source
    for forbidden in ("allowed_domains", "allowed_cidrs", "fnmatch"):
        assert forbidden not in source, f"服务层出现了自己的白名单比较: {forbidden}"


# ── 正常路径 ──────────────────────────────────────────────


def test_create_scan_job_returns_submission(scope_id):
    submission = create_scan_job(scope_id=scope_id, targets=["a.example.test"], tools=["subfinder"])

    assert isinstance(submission, JobSubmission)
    assert submission.job["status"] == "queued"
    assert submission.mode == "mock"
    assert submission.tools == ["subfinder"]
    assert submission.targets == ["a.example.test"]
    assert submission.reused is False
    assert submission.scope.id == scope_id

    body = submission.to_dict()
    assert body["ok"] is True
    assert body["job_id"].startswith("job_")
    assert body["total_steps"] == 1
    assert body["scope_id"] == scope_id
    assert body["reused"] is False


def test_submission_to_dict_matches_api_response_shape(admin_client, scope_id):
    """服务层返回体与 ``POST /api/jobs`` 的响应体字段必须完全一致。"""
    resp = admin_client.post(
        "/api/jobs",
        json={"scope_id": scope_id, "targets": ["a.example.test"], "tools": ["subfinder"]},
    )
    api_body = resp.get_json()

    submission = create_scan_job(scope_id=scope_id, targets=["b.example.test"], tools=["subfinder"])
    assert set(api_body) == set(submission.to_dict())


def test_create_scan_job_is_audited_and_logged(scope_id, caplog):
    """审计与结构化日志由服务层负责（视图函数不再自己写）。"""
    from core import audit, jobs as jobs_store, observability

    with caplog.at_level("INFO", logger="gef"):
        submission = create_scan_job(scope_id=scope_id, targets=["a.example.test"], tools=["subfinder"])

    events = [event for event in audit.list_events(limit=20) if event["event_type"] == audit.EVENT_JOB_CREATED]
    entry = next(event for event in events if event["target_id"] == submission.job["id"])
    assert entry["detail"]["scope_id"] == scope_id

    job_events = [event["event_type"] for event in jobs_store.list_events(submission.job["id"])]
    assert jobs_store.EVENT_JOB_CREATED in job_events

    logged = [record for record in caplog.records if observability.EVENT_JOB_CREATED in record.getMessage()]
    assert logged, "任务创建没有进结构化日志"


def test_create_scan_job_expands_steps_and_dedupes_targets(scope_id):
    submission = create_scan_job(
        scope_id=scope_id,
        targets="a.example.test, a.example.test, b.example.test",
        tools="subfinder,httpx",
    )
    assert submission.targets == ["a.example.test", "b.example.test"]
    assert submission.tools == ["subfinder", "httpx"]
    assert submission.job["total_steps"] == 4


# ── 目标来源：显式列表 / 受控 upload_id ────────────────────


def test_resolve_targets_merges_upload_and_explicit(app_module):
    upload_id = _upload(b"c.example.test\n")
    targets, resolved_upload = resolve_targets(targets=["a.example.test"], upload_id=upload_id)

    assert resolved_upload == upload_id
    assert targets == ["a.example.test", "c.example.test"]


def test_resolve_targets_rejects_arbitrary_path(app_module):
    """``upload_id`` 只能是受控 ID；任意路径一律拒绝（P0-3 在服务层同样成立）。"""
    with pytest.raises(BadRequestError):
        resolve_targets(targets=[], upload_id="../../config.py")


def test_create_scan_job_from_upload_is_scoped(scope_id):
    upload_id = _upload(b"a.example.test\nevil.test\n")
    with pytest.raises(ScopeViolationError):
        create_scan_job(scope_id=scope_id, targets=[], tools=["subfinder"], upload_id=upload_id)


def test_split_str_list_rejects_non_list():
    with pytest.raises(BadRequestError):
        split_str_list(123, "targets")


# ── 参数校验：与历史口径逐条一致 ──────────────────────────


def test_create_scan_job_requires_targets(scope_id):
    with pytest.raises(BadRequestError, match="targets 或 upload_id"):
        create_scan_job(scope_id=scope_id, targets=[], tools=["subfinder"])


def test_create_scan_job_requires_tools(scope_id):
    with pytest.raises(BadRequestError, match="至少一个工具"):
        create_scan_job(scope_id=scope_id, targets=["a.example.test"], tools=[])


def test_create_scan_job_rejects_unknown_tool(scope_id):
    with pytest.raises(BadRequestError):
        create_scan_job(scope_id=scope_id, targets=["a.example.test"], tools=["nuclei"])


def test_create_scan_job_requires_scope(app_module):
    """无 Scope 即拒绝（缺失 → 400，不是 403）。"""
    with pytest.raises(BadRequestError):
        create_scan_job(scope_id=None, targets=["a.example.test"], tools=["subfinder"])


def test_create_scan_job_rejects_unknown_scope(app_module):
    with pytest.raises(ScopeViolationError):
        create_scan_job(scope_id="scope_missing", targets=["a.example.test"], tools=["subfinder"])


def test_create_scan_job_rejects_out_of_scope_target(scope_id):
    with pytest.raises(ScopeViolationError):
        create_scan_job(scope_id=scope_id, targets=["evil.test"], tools=["subfinder"])


def test_create_scan_job_rejects_too_many_targets(scope_id):
    from config import SCAN_LIMITS

    limit = SCAN_LIMITS["max_targets_per_job"]
    with pytest.raises(BadRequestError, match="最多"):
        create_scan_job(scope_id=scope_id, targets=[f"h{i}.example.test" for i in range(limit + 1)], tools=["subfinder"])


@pytest.mark.parametrize("bad_key", [123, "x" * 201])
def test_create_scan_job_rejects_bad_idempotency_key(scope_id, bad_key):
    with pytest.raises(BadRequestError):
        create_scan_job(
            scope_id=scope_id,
            targets=["a.example.test"],
            tools=["subfinder"],
            idempotency_key=bad_key,
        )


def test_create_scan_job_rejects_unknown_scenario(scope_id):
    with pytest.raises(BadRequestError, match="scenario"):
        create_scan_job(scope_id=scope_id, targets=["a.example.test"], tools=["subfinder"], scenario="turbo")


def test_unknown_scenario_is_ignored_in_real_mode(app_module, monkeypatch):
    """``scenario`` 只在 mock 下校验（real 模式带随意场景名不应报错）。"""
    from core import scope_store
    from core.safety import REAL_SCAN_ENV

    monkeypatch.setenv(REAL_SCAN_ENV, "true")
    scope = scope_store.create(name="real 范围", allowed_domains=["a.example.test"], active_scan=True)
    submission = create_scan_job(
        scope_id=scope.id,
        targets=["a.example.test"],
        tools=["subfinder"],
        mode="real",
        scenario="turbo",
    )
    assert submission.mode == "real"
    assert submission.job["scenario"] is None


# ── 双开关：real 模式必须同时满足环境开关与 Scope.active_scan ──


def test_real_mode_blocked_without_env(scope_id, monkeypatch):
    from core.safety import REAL_SCAN_ENV

    monkeypatch.delenv(REAL_SCAN_ENV, raising=False)
    with pytest.raises(ScopeViolationError):
        create_scan_job(scope_id=scope_id, targets=["a.example.test"], tools=["subfinder"], mode="real")


def test_real_mode_blocked_when_scope_inactive(scope_id, monkeypatch):
    from core.safety import REAL_SCAN_ENV

    monkeypatch.setenv(REAL_SCAN_ENV, "true")
    with pytest.raises(ScopeViolationError):
        create_scan_job(scope_id=scope_id, targets=["a.example.test"], tools=["subfinder"], mode="real")


# ── 幂等：服务层把键原样交给 jobs_store，不自行判断 ────────


def test_create_scan_job_reuses_pending_job_by_idempotency_key(scope_id):
    first = create_scan_job(
        scope_id=scope_id,
        targets=["a.example.test"],
        tools=["subfinder"],
        idempotency_key="service-key-1",
    )
    second = create_scan_job(
        scope_id=scope_id,
        targets=["a.example.test"],
        tools=["subfinder"],
        idempotency_key="service-key-1",
    )

    assert first.reused is False
    assert second.reused is True
    assert second.job["id"] == first.job["id"]
    assert second.to_dict()["reused"] is True


def test_reused_submission_is_audited_with_flag(scope_id):
    from core import audit

    create_scan_job(scope_id=scope_id, targets=["a.example.test"], tools=["subfinder"], idempotency_key="service-key-2")
    create_scan_job(scope_id=scope_id, targets=["a.example.test"], tools=["subfinder"], idempotency_key="service-key-2")

    entries = [event for event in audit.list_events(limit=20) if event["event_type"] == audit.EVENT_JOB_CREATED]
    assert any(entry["detail"].get("reused") is True for entry in entries)


# ── 与 API 的等价性：同样输入 → 同样的库内状态 ─────────────


def test_api_and_service_produce_equivalent_jobs(admin_client, scope_id):
    """同一份输入，走 HTTP 与直接调服务层，落库结果必须一致。"""
    from core import jobs as jobs_store

    via_api = admin_client.post(
        "/api/jobs",
        json={"scope_id": scope_id, "targets": ["a.example.test"], "tools": ["subfinder"]},
    ).get_json()
    via_service = create_scan_job(scope_id=scope_id, targets=["a.example.test"], tools=["subfinder"])

    api_job = jobs_store.get_job(via_api["job_id"])
    service_job = jobs_store.get_job(via_service.job["id"])

    for field in ("status", "mode", "total_steps", "scope_id", "done_steps", "progress"):
        assert api_job[field] == service_job[field], f"字段 {field} 不一致"
