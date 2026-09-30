"""M4 集成测试：统一结果模型在 API 与任务上的表现。

覆盖方案 M4 的验收项：

* 工具不存在 → job/step 显示 ``tool_not_found``；
* 工具返回空列表 → 显示 ``no_results``（且**不是**失败）；
* 工具超时 → 显示 ``timeout``；
* 结果详情能看到原始证据；
* 任务详情里能看到脱敏命令预览与耗时。

real 模式不真的调用外部工具：用 monkeypatch 把 ``build_runner`` 换成一个
可控的假 runner，这样验证的是**执行链与落库**，符合「不扫未授权目标」约束。
"""

import json

import pytest

from core import jobs as jobs_store


def _make_scope(admin_client, **overrides):
    payload = {"name": "M4 证据范围", "allowed_domains": ["example.test"], "active_scan": True}
    payload.update(overrides)
    resp = admin_client.post("/api/scopes", json=payload)
    assert resp.status_code == 201
    return resp.get_json()["scope"]["id"]


def _create_job(admin_client, scope_id, *, tools=("subfinder",), targets=("example.test",), mode="mock", **extra):
    body = {"scope_id": scope_id, "targets": list(targets), "tools": list(tools), "mode": mode}
    body.update(extra)
    resp = admin_client.post("/api/jobs", json=body)
    assert resp.status_code == 202, resp.get_json()
    return resp.get_json()["job_id"]


def _run_once():
    """跑一轮 worker：把当前队列里的任务全部处理掉（不进入常驻循环）。"""
    from jobs.worker import Worker

    worker = Worker(worker_id="m4-test", verbose=False)
    worker.startup()
    processed = 0
    while worker.tick() is not None:
        processed += 1
        if processed > 50:  # 防御：正常用例不会跑到这里
            break
    return processed


# ── mock 场景经任务链路仍然可区分 ──────────────────────────


@pytest.mark.parametrize(
    "scenario,expected_status,expected_error",
    [
        ("success", "succeeded", None),
        ("empty", "succeeded", "no_results"),
        ("tool_not_found", "failed", "tool_not_found"),
        ("timeout", "timeout", "timeout"),
        ("parse_error", "failed", "parse_error"),
        ("partial", "succeeded", "partial_success"),
    ],
)
def test_job_step_error_codes_by_scenario(admin_client, scenario, expected_status, expected_error):
    scope_id = _make_scope(admin_client)
    job_id = _create_job(admin_client, scope_id, scenario=scenario)
    _run_once()

    detail = admin_client.get(f"/api/jobs/{job_id}").get_json()["job"]
    step = detail["steps"][0]
    assert step["status"] == expected_status, detail
    assert step["error_code"] == expected_error


def test_job_zero_results_is_not_a_failure(admin_client):
    """M4 验收项：工具返回空列表时显示 no_results，而不是 failed。"""
    scope_id = _make_scope(admin_client)
    job_id = _create_job(admin_client, scope_id, scenario="empty")
    _run_once()

    detail = admin_client.get(f"/api/jobs/{job_id}").get_json()["job"]
    assert detail["status"] == "succeeded"
    step = detail["steps"][0]
    assert step["status"] == "succeeded"
    assert step["error_code"] == "no_results"
    assert step["found_count"] == 0


def test_job_timeout_is_distinguishable_from_failure(admin_client):
    scope_id = _make_scope(admin_client)
    timeout_job = _create_job(admin_client, scope_id, scenario="timeout")
    failed_job = _create_job(admin_client, scope_id, scenario="tool_not_found")
    _run_once()

    timeout_step = admin_client.get(f"/api/jobs/{timeout_job}").get_json()["job"]["steps"][0]
    failed_step = admin_client.get(f"/api/jobs/{failed_job}").get_json()["job"]["steps"][0]
    assert timeout_step["error_code"] == "timeout"
    assert failed_step["error_code"] == "tool_not_found"


# ── real 模式的统一结果（用假 runner，不碰外部工具） ────────


class _FakeResultRunner:
    """按预设返回 RunnerResult 的假 runner，模拟真实工具的各种结局。"""

    def __init__(self, result, *, stdout=None, stderr=None):
        self._result = result
        self.tool_name = "subfinder"
        self.category = "subdomain"
        self.last_execution = {"stdout": stdout, "stderr": stderr}

    def run(self, target):
        return self._result


def _patch_runner(monkeypatch, runner):
    monkeypatch.setattr("modules.registry.build_runner", lambda name: runner)


@pytest.fixture
def real_mode(monkeypatch):
    """打开 real 模式的两个开关（环境 + Scope.active_scan）。"""
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")
    return True


def test_real_step_records_structured_result(admin_client, monkeypatch, real_mode):
    """real 步骤要把 status/error_code/耗时/脱敏命令预览写进 job_steps。"""
    from core.runner_result import Observation, RunnerResult

    result = RunnerResult.ok(
        [Observation(category="web", value="https://a.example.test", data={"status_code": 200, "title": "首页"})],
        exit_code=0,
        duration_ms=42,
        command_preview="httpx -l in.txt",
    )
    _patch_runner(monkeypatch, _FakeResultRunner(result, stdout="https://a.example.test\n"))

    scope_id = _make_scope(admin_client)
    job_id = _create_job(admin_client, scope_id, mode="real")
    _run_once()

    step = admin_client.get(f"/api/jobs/{job_id}").get_json()["job"]["steps"][0]
    assert step["status"] == "succeeded"
    assert step["error_code"] is None
    assert step["exit_code"] == 0
    assert step["duration_ms"] == 42
    assert step["command_preview"] == "httpx -l in.txt"
    assert step["found_count"] == 1
    assert step["observations"][0]["data"]["status_code"] == 200
    assert step["observations"][0]["data"]["title"] == "首页"
    assert step["artifact_id"]


def test_real_step_no_results_reports_no_results(admin_client, monkeypatch, real_mode):
    """工具跑通但零结果：状态 succeeded、错误码 no_results（M4 验收项）。"""
    from core.runner_result import RunnerResult

    _patch_runner(monkeypatch, _FakeResultRunner(RunnerResult.ok([], exit_code=0), stdout=""))

    scope_id = _make_scope(admin_client)
    job_id = _create_job(admin_client, scope_id, mode="real")
    _run_once()

    detail = admin_client.get(f"/api/jobs/{job_id}").get_json()["job"]
    step = detail["steps"][0]
    assert detail["status"] == "succeeded"
    assert step["status"] == "succeeded"
    assert step["error_code"] == "no_results"


def test_real_step_tool_not_found(admin_client, monkeypatch, real_mode):
    from core.errors import ErrorCode
    from core.runner_result import RunnerResult

    _patch_runner(
        monkeypatch,
        _FakeResultRunner(RunnerResult.failure(ErrorCode.TOOL_NOT_FOUND, "未找到可执行文件: subfinder")),
    )

    scope_id = _make_scope(admin_client)
    job_id = _create_job(admin_client, scope_id, mode="real")
    _run_once()

    detail = admin_client.get(f"/api/jobs/{job_id}").get_json()["job"]
    assert detail["status"] == "failed"
    assert detail["error_code"] == "unknown_error"  # 任务层聚合（全部步骤失败）
    step = detail["steps"][0]
    assert step["error_code"] == "tool_not_found"


def test_real_step_timeout_maps_to_job_timeout(admin_client, monkeypatch, real_mode):
    from core.errors import ErrorCode
    from core.runner_result import RunnerResult, STATUS_TIMEOUT

    _patch_runner(
        monkeypatch,
        _FakeResultRunner(RunnerResult.failure(ErrorCode.TIMEOUT, "执行超时", status=STATUS_TIMEOUT)),
    )

    scope_id = _make_scope(admin_client)
    job_id = _create_job(admin_client, scope_id, mode="real")
    _run_once()

    detail = admin_client.get(f"/api/jobs/{job_id}").get_json()["job"]
    assert detail["status"] == "timeout"
    assert detail["error_code"] == "timeout"
    assert detail["steps"][0]["error_code"] == "timeout"


# ── 原始证据（M4 交付项「结果详情能看到原始证据」） ────────


def test_artifacts_listed_and_readable(admin_client, monkeypatch, real_mode):
    from core.runner_result import Observation, RunnerResult

    result = RunnerResult.ok(
        [Observation(category="subdomain", value="a.example.test")],
        exit_code=0,
        duration_ms=10,
        command_preview="subfinder -d example.test",
    )
    _patch_runner(
        monkeypatch,
        _FakeResultRunner(result, stdout="a.example.test\n", stderr="WARN: api key invalid\n"),
    )

    scope_id = _make_scope(admin_client)
    job_id = _create_job(admin_client, scope_id, mode="real")
    _run_once()

    listing = admin_client.get(f"/api/jobs/{job_id}/artifacts").get_json()["artifacts"]
    assert {row["kind"] for row in listing} == {"stdout", "stderr"}
    # 列表不得泄露服务器路径。
    assert all("path" not in row for row in listing)

    stdout_row = next(row for row in listing if row["kind"] == "stdout")
    payload = admin_client.get(f"/api/artifacts/{stdout_row['id']}").get_json()["artifact"]
    assert "a.example.test" in payload["text"]
    assert "path" not in payload
    assert payload["sha256"] == stdout_row["sha256"]


def test_artifact_read_redacts_secrets(admin_client, monkeypatch, real_mode):
    """方案第 8.2 节：不把工具 stderr 原样暴露给前端。"""
    from core.runner_result import RunnerResult

    secret = "sk-" + "z" * 30
    _patch_runner(
        monkeypatch,
        _FakeResultRunner(RunnerResult.ok([], exit_code=0), stdout="", stderr=f"auth failed: token={secret}"),
    )

    scope_id = _make_scope(admin_client)
    job_id = _create_job(admin_client, scope_id, mode="real")
    _run_once()

    listing = admin_client.get(f"/api/jobs/{job_id}/artifacts").get_json()["artifacts"]
    stderr_row = next(row for row in listing if row["kind"] == "stderr")
    payload = admin_client.get(f"/api/artifacts/{stderr_row['id']}").get_json()["artifact"]
    assert secret not in payload["text"]


def test_artifacts_require_admin_and_404_unknown(client, admin_client):
    assert client.get("/api/jobs/job_x/artifacts").status_code == 401
    assert client.get("/api/artifacts/art_x").status_code == 401

    scope_id = _make_scope(admin_client)
    job_id = _create_job(admin_client, scope_id)
    assert admin_client.get(f"/api/jobs/{job_id}/artifacts").status_code == 200
    assert admin_client.get("/api/jobs/job_missing/artifacts").status_code == 404
    assert admin_client.get("/api/artifacts/art_missing").status_code == 404


def test_empty_stdout_produces_no_artifact_file(admin_client, monkeypatch, real_mode):
    """空 stdout 是常态，不该为它产生空文件与登记行。"""
    from core.runner_result import RunnerResult

    _patch_runner(
        monkeypatch,
        _FakeResultRunner(RunnerResult.ok([], exit_code=0), stdout="", stderr=None),
    )

    scope_id = _make_scope(admin_client)
    job_id = _create_job(admin_client, scope_id, mode="real")
    _run_once()

    listing = admin_client.get(f"/api/jobs/{job_id}/artifacts").get_json()["artifacts"]
    assert listing == []


def test_failure_keeps_error_code_for_manual_triage(admin_client, monkeypatch, real_mode):
    """失败时 stderr 至少要能取到，否则「结果详情能看到原始证据」不成立。"""
    from core.runner_result import RunnerResult

    _patch_runner(
        monkeypatch,
        _FakeResultRunner(RunnerResult.failure("parse_error", "输出解析失败"), stdout="乱码", stderr="parse error at line 3"),
    )

    scope_id = _make_scope(admin_client)
    job_id = _create_job(admin_client, scope_id, mode="real")
    _run_once()

    detail = admin_client.get(f"/api/jobs/{job_id}").get_json()["job"]
    assert detail["steps"][0]["error_code"] == "parse_error"

    listing = admin_client.get(f"/api/jobs/{job_id}/artifacts").get_json()["artifacts"]
    assert {row["kind"] for row in listing} == {"stdout", "stderr"}


# ── mock 任务不应产生真实证据 ──────────────────────────────


def test_mock_job_has_no_artifacts(admin_client):
    scope_id = _make_scope(admin_client)
    job_id = _create_job(admin_client, scope_id)
    _run_once()

    assert admin_client.get(f"/api/jobs/{job_id}/artifacts").get_json()["artifacts"] == []


# ── 迁移兼容：旧库缺列时仍可读写 ──────────────────────────


def test_step_migration_adds_m4_columns(local_db):
    """``CREATE TABLE IF NOT EXISTS`` 不会补列；M3 建的库必须能升上来。"""
    import sqlite3

    with sqlite3.connect(local_db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(job_steps)")}
    for column in ("observations_json", "duration_ms", "command_preview", "parser_version"):
        assert column in columns


def test_artifacts_table_exists(local_db):
    import sqlite3

    with sqlite3.connect(local_db) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "artifacts" in tables


def test_mock_steps_carry_parser_version_absent(local_db):
    """mock 步骤不产出真实观测，M4 新增字段保持空值，不能写成假数据。"""
    from core import scope_store

    scope_id = scope_store.create(name="空字段范围", allowed_domains=["example.test"]).id
    job = jobs_store.create_job(scope_id=scope_id, targets=["example.test"], tools=["subfinder"], mode="mock")
    jobs_store.claim_next_job("w1", lease_seconds=300)

    from jobs.executor import execute_job

    execute_job(job["id"])
    step = jobs_store.list_steps(job["id"])[0]
    assert step["observations"] == []
    assert step["duration_ms"] is None
    assert step["command_preview"] is None
    assert json.loads(json.dumps(step["results"])) == step["results"]
