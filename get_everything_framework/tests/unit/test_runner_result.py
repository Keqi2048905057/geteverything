"""M4 单元测试：统一结果模型与原始证据。

锁死方案 M4 的三条验收口径：

* 工具不存在 → ``tool_not_found``；超时 → ``timeout``；
  工具返回空列表 → ``no_results``（**三者必须能区分**）；
* 命令预览不得泄露 API Key（方案第 8.2 节）；
* stdout/stderr 作为 artifact 落盘，对外只给截断 + 脱敏后的内容，不给本地路径。
"""

import os

import pytest

from core.artifacts import (
    KIND_STDERR,
    KIND_STDOUT,
    get_artifact,
    list_artifacts,
    read_artifact,
    save_artifact,
)
from core.errors import ErrorCode
from core.runner_result import (
    Observation,
    RunnerInputError,
    RunnerResult,
    ToolHealth,
    result_from_exception,
    scrub_command,
)


class _FakeRunner:
    """最小 runner：只提供 run_scan 与配置，用于验证基类的包装逻辑。"""

    def __init__(self, name="fake", config=None):
        self.tool_name = name
        self.config = config or {"path": "definitely-not-a-real-binary-xyz", "category": "subdomain"}
        self.category = self.config.get("category", "subdomain")
        self.last_execution = {}


# ── 命令预览脱敏 ──────────────────────────────────────────


def test_scrub_command_redacts_flag_value():
    preview = scrub_command(["subfinder", "-d", "example.test", "-api-key", "SUPERSECRET123456"])
    assert "SUPERSECRET123456" not in preview
    assert "***" in preview
    assert "example.test" in preview


def test_scrub_command_redacts_authorization_header():
    preview = scrub_command('httpx -H "Authorization: Bearer abcdefghijklmnopqrstuvwxyz"')
    assert "abcdefghijklmnopqrstuvwxyz" not in preview


def test_scrub_command_redacts_url_credentials():
    preview = scrub_command(["curl", "https://admin:hunter2@example.test/health"])
    assert "hunter2" not in preview
    assert "admin" in preview


def test_scrub_command_redacts_long_bare_token():
    token = "ghp_" + "a1b2c3d4" * 5
    preview = scrub_command(["tool", token])
    assert token not in preview


def test_scrub_command_keeps_normal_args_untouched():
    preview = scrub_command(["subfinder", "-d", "example.test", "-silent"])
    assert preview == "subfinder -d example.test -silent"


def test_scrub_command_keeps_windows_paths_intact():
    """长目录名不能被当成密钥打码，否则命令预览会失去排查价值。"""
    path = r"E:\Programmingtools\get_everything_framework\get_everything_framework\results\abc123def456.txt"
    preview = scrub_command(["subfinder", "-d", "example.test", "-o", path])
    assert path in preview
    assert "***" not in preview


def test_scrub_command_keeps_posix_paths_intact():
    path = "/home/somebody/really_long_workspace_name/results/output_file_name.txt"
    preview = scrub_command(["tool", "-o", path])
    assert path in preview


def test_scrub_command_still_redacts_bare_token_without_context():
    token = "sk-" + "c" * 32
    assert token not in scrub_command(["tool", token])


def test_scrub_command_truncates_long_preview():
    preview = scrub_command(["tool"] + [f"arg{i}" for i in range(200)])
    assert len(preview) <= 300
    assert preview.endswith("...")


# ── RunnerResult 语义 ─────────────────────────────────────


def test_ok_with_data_has_no_error_code():
    result = RunnerResult.ok([Observation(category="subdomain", value="a.example.test")])
    assert result.status == "success"
    assert result.error_code is None
    assert result.found_count == 1
    assert not result.is_failure


def test_ok_without_data_is_no_results_not_failure():
    """M4 核心口径：跑通但零结果 ≠ 失败。"""
    result = RunnerResult.ok([])
    assert result.status == "success"
    assert result.error_code == ErrorCode.NO_RESULTS
    assert result.found_count == 0
    assert not result.is_failure


def test_failure_is_failure():
    result = RunnerResult.failure(ErrorCode.TOOL_NOT_FOUND, "没装")
    assert result.status == "failed"
    assert result.is_failure


def test_timeout_status_is_failure():
    result = RunnerResult.failure(ErrorCode.TIMEOUT, "超时", status="timeout")
    assert result.status == "timeout"
    assert result.is_failure


def test_unknown_status_rejected():
    with pytest.raises(ValueError):
        RunnerResult(status="weird")


def test_to_dict_shape_matches_plan():
    """方案第 6.2 节要求的字段必须都在。"""
    payload = RunnerResult.ok(
        [Observation(category="web", value="https://a.example.test", data={"status_code": 200})],
        exit_code=0,
        duration_ms=1234,
        command_preview="httpx -l in.txt",
        raw_artifact_id="art_1",
    ).to_dict()
    for key in (
        "status",
        "data",
        "error_code",
        "error_message",
        "exit_code",
        "duration_ms",
        "command_preview",
        "raw_artifact_id",
        "parser_version",
    ):
        assert key in payload
    assert payload["data"][0]["data"]["status_code"] == 200


def test_result_from_exception_maps_known_errors():
    assert result_from_exception(FileNotFoundError("x")).error_code == ErrorCode.TOOL_NOT_FOUND
    assert result_from_exception(PermissionError("x")).error_code == ErrorCode.PERMISSION_DENIED
    assert result_from_exception(TimeoutError("x")).status == "timeout"
    assert result_from_exception(SystemExit(3)).exit_code == 3
    assert result_from_exception(RuntimeError("x")).error_code == ErrorCode.UNKNOWN_ERROR


def test_result_from_exception_honours_runner_input_error():
    """前置数据缺失必须能表达成 no_results，而不是被兜底成 unknown_error。"""
    result = result_from_exception(RunnerInputError(ErrorCode.NO_RESULTS, "没有候选目标", status="success"))
    assert result.status == "success"
    assert result.error_code == ErrorCode.NO_RESULTS


def test_tool_health_dict():
    health = ToolHealth(name="subfinder", available=False, error_code=ErrorCode.TOOL_NOT_FOUND, message="没装")
    assert health.to_dict()["available"] is False


# ── 原始证据 ──────────────────────────────────────────────


def test_save_artifact_writes_file_and_registers(local_db, tmp_path, monkeypatch):
    import core.artifacts as artifacts

    monkeypatch.setattr(artifacts, "ARTIFACT_DIR", str(tmp_path / "artifacts"), raising=False)
    saved = save_artifact("line1\nline2\n", kind=KIND_STDOUT, tool_name="subfinder", target="example.test")

    assert saved is not None
    assert saved["size"] == len("line1\nline2\n")
    assert saved["truncated"] is False
    assert os.path.exists(saved["path"])
    assert get_artifact(saved["id"])["sha256"] == saved["sha256"]


def test_save_artifact_skips_empty_content(local_db, tmp_path, monkeypatch):
    import core.artifacts as artifacts

    monkeypatch.setattr(artifacts, "ARTIFACT_DIR", str(tmp_path / "artifacts"), raising=False)
    assert save_artifact("", kind=KIND_STDOUT) is None
    assert save_artifact(None, kind=KIND_STDERR) is None


def test_save_artifact_truncates_over_limit(local_db, tmp_path, monkeypatch):
    import core.artifacts as artifacts

    monkeypatch.setattr(artifacts, "ARTIFACT_DIR", str(tmp_path / "artifacts"), raising=False)
    saved = save_artifact("x" * 5000, kind=KIND_STDOUT, max_bytes=100)

    assert saved["truncated"] is True
    assert saved["original_size"] == 5000
    assert saved["size"] > 100  # 含截断提示
    assert os.path.getsize(saved["path"]) < 1000


def test_read_artifact_scrubs_and_hides_path(local_db, tmp_path, monkeypatch):
    import core.artifacts as artifacts

    monkeypatch.setattr(artifacts, "ARTIFACT_DIR", str(tmp_path / "artifacts"), raising=False)
    saved = save_artifact("token=ghp_" + "b" * 40, kind=KIND_STDERR)

    payload = read_artifact(saved["id"])
    assert payload is not None
    assert "path" not in payload
    assert "ghp_" + "b" * 40 not in payload["text"]
    assert payload["missing"] is False


def test_read_artifact_reports_truncation_for_large_read(local_db, tmp_path, monkeypatch):
    import core.artifacts as artifacts

    monkeypatch.setattr(artifacts, "ARTIFACT_DIR", str(tmp_path / "artifacts"), raising=False)
    saved = save_artifact("y" * 500, kind=KIND_STDOUT)

    payload = read_artifact(saved["id"], limit=50)
    assert payload["truncated"] is True


def test_read_artifact_missing_file_is_flagged(local_db, tmp_path, monkeypatch):
    import core.artifacts as artifacts

    monkeypatch.setattr(artifacts, "ARTIFACT_DIR", str(tmp_path / "artifacts"), raising=False)
    saved = save_artifact("data", kind=KIND_STDOUT)
    os.remove(saved["path"])

    payload = read_artifact(saved["id"])
    assert payload["missing"] is True
    assert payload["text"] == ""


def test_read_artifact_unknown_id_returns_none(local_db):
    assert read_artifact("art_does_not_exist") is None


def test_list_artifacts_has_no_path(local_db, tmp_path, monkeypatch):
    import core.artifacts as artifacts

    monkeypatch.setattr(artifacts, "ARTIFACT_DIR", str(tmp_path / "artifacts"), raising=False)
    save_artifact("out", kind=KIND_STDOUT, job_id="job_x")
    save_artifact("err", kind=KIND_STDERR, job_id="job_x")

    rows = list_artifacts(job_id="job_x")
    assert len(rows) == 2
    assert all("path" not in row for row in rows)
    assert {row["kind"] for row in rows} == {KIND_STDOUT, KIND_STDERR}
