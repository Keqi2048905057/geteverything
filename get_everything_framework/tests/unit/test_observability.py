"""方案第 19 节 P1：Observability —— 结构化日志与关联 ID 的单元测试。"""

import json
import logging
import threading

import pytest

from core import observability


@pytest.fixture(autouse=True)
def _restore_logger_state():
    """每个用例后复原 logger / contextvar / 格式，避免测试之间互相污染。"""
    yield
    for handler in list(observability.logger.handlers):
        if getattr(handler, "_gef_stream", False):
            observability.logger.removeHandler(handler)
            handler.close()
    observability.logger.propagate = True
    observability.logger.setLevel(logging.DEBUG)
    observability._FORMAT = observability.DEFAULT_FORMAT
    for name in observability._CONTEXT_ORDER:
        observability._CONTEXT_VARS[name].set(None)


# ── request_id ───────────────────────────────────────────


def test_new_request_id_has_prefix_and_is_unique():
    first = observability.new_request_id()
    second = observability.new_request_id()
    assert first.startswith("req_")
    assert first != second
    assert observability.REQUEST_ID_PATTERN.match(first)


@pytest.mark.parametrize("value", ["abcdefgh", "req_" + "a" * 30, "trace-1.2:3_4"])
def test_accept_request_id_keeps_safe_values(value):
    assert observability.accept_request_id(value) == value


@pytest.mark.parametrize("value", [None, "", "short", "has space here", "bad!" + "a" * 20, "x" * 100, 12345])
def test_accept_request_id_rejects_unsafe_values(value):
    assert observability.accept_request_id(value) is None


def test_accept_request_id_strips_surrounding_whitespace():
    assert observability.accept_request_id("  abcdefgh1234  ") == "abcdefgh1234"


# ── contextvars ──────────────────────────────────────────


def test_current_context_is_empty_by_default():
    assert observability.current_context() == {}


def test_bind_sets_and_restores_context():
    with observability.bind(worker_id="w-1", job_id="job_x"):
        assert observability.current_context() == {"worker_id": "w-1", "job_id": "job_x"}
    assert observability.current_context() == {}


def test_bind_restores_previous_value_on_exit():
    observability.set_context("job_id", "job_outer")
    try:
        with observability.bind(job_id="job_inner"):
            assert observability.current_context()["job_id"] == "job_inner"
        assert observability.current_context()["job_id"] == "job_outer"
    finally:
        observability._CONTEXT_VARS["job_id"].set(None)


def test_set_context_ignores_unknown_name_and_none_value():
    assert observability.set_context("nope", "x") is None
    assert observability.set_context("job_id", None) is None
    assert observability.current_context() == {}


def test_context_is_isolated_between_threads():
    """waitress 是多线程模型：线程之间绝不能串号。"""
    seen: list[dict] = []

    def worker():
        seen.append(observability.current_context())
        with observability.bind(request_id="req_thread"):
            seen.append(observability.current_context())

    with observability.bind(request_id="req_main"):
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        # 主线程的绑定不受子线程影响（子线程也看不到主线程的值，见 seen[0]）。
        assert observability.current_context() == {"request_id": "req_main"}

    assert seen[0] == {}
    assert seen[1] == {"request_id": "req_thread"}
    assert observability.current_context() == {}


# ── 事件形状 ─────────────────────────────────────────────


def test_log_event_returns_envelope_with_context(caplog):
    caplog.set_level(logging.INFO, logger=observability.LOGGER_NAME)
    with observability.bind(request_id="req_abc", job_id="job_1", step_id="step_1", worker_id="w-1"):
        payload = observability.log_event(
            observability.EVENT_JOB_STEP_FINISHED,
            job_id="job_1",
            tool="httpx",
            status="succeeded",
            duration_ms=1200,
        )

    assert payload["level"] == "INFO"
    assert payload["event"] == "job_step_finished"
    assert payload["request_id"] == "req_abc"
    assert payload["job_id"] == "job_1"
    assert payload["step_id"] == "step_1"
    assert payload["worker_id"] == "w-1"
    assert payload["tool"] == "httpx"
    assert payload["duration_ms"] == 1200
    assert payload["ts"].startswith("20")


def test_log_event_is_one_json_line(caplog):
    caplog.set_level(logging.INFO, logger=observability.LOGGER_NAME)
    observability.log_event("probe", note="hello")
    line = caplog.records[-1].getMessage()
    assert "\n" not in line
    assert json.loads(line)["event"] == "probe"


def test_log_event_accepts_logging_level_constants(caplog):
    caplog.set_level(logging.DEBUG, logger=observability.LOGGER_NAME)
    assert observability.log_event("probe", level=logging.WARNING)["level"] == "WARNING"
    assert observability.log_event("probe", level="debug")["level"] == "DEBUG"
    assert observability.log_event("probe", level="nonsense")["level"] == "INFO"


def test_log_event_falls_back_to_unspecified_event_name(caplog):
    caplog.set_level(logging.INFO, logger=observability.LOGGER_NAME)
    assert observability.log_event("")["event"] == "unspecified"
    assert observability.log_event(None)["event"] == "unspecified"


def test_log_event_does_not_raise_on_unserializable_values(caplog):
    caplog.set_level(logging.INFO, logger=observability.LOGGER_NAME)
    payload = observability.log_event("probe", blob=object())
    json.dumps(payload)  # 必须始终可序列化
    assert isinstance(payload["blob"], str)


# ── 脱敏 ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "key",
    ["api_key", "APIKEY", "local_admin_token", "secret", "password", "authorization", "cookie", "credential"],
)
def test_sensitive_fields_are_replaced_by_placeholder(caplog, key):
    caplog.set_level(logging.INFO, logger=observability.LOGGER_NAME)
    payload = observability.log_event("probe", **{key: "super-secret-value"})
    assert payload[key] == observability.REDACTED
    assert "super-secret-value" not in caplog.records[-1].getMessage()


def test_sensitive_detection_beats_identifier_rule(caplog):
    """``token_id`` 命中敏感词 → 即使以 ``_id`` 结尾也只记占位符。"""
    caplog.set_level(logging.INFO, logger=observability.LOGGER_NAME)
    payload = observability.log_event("probe", token_id="tok_abcdef123456")
    assert payload["token_id"] == observability.REDACTED


def test_secret_inside_free_text_is_scrubbed(caplog):
    caplog.set_level(logging.INFO, logger=observability.LOGGER_NAME)
    payload = observability.log_event("probe", note="fetch http://user:pw@example.test/x")
    assert "user:pw@" not in payload["note"]
    assert "example.test" in payload["note"]


def test_long_bare_token_is_scrubbed(caplog):
    caplog.set_level(logging.INFO, logger=observability.LOGGER_NAME)
    payload = observability.log_event("probe", note="ghp_" + "e" * 40)
    assert payload["note"] == observability.REDACTED


def test_correlation_ids_survive_scrubbing(caplog):
    """关联 ID 长 36 字符，必须原样保留 —— 否则结构化日志就失去关联能力。"""
    caplog.set_level(logging.INFO, logger=observability.LOGGER_NAME)
    job_id = "job_deadbeefdeadbeefdeadbeefdeadbeef"
    step_id = "step_deadbeefdeadbeefdeadbeefdeadbeef"
    payload = observability.log_event("probe", job_id=job_id, step_id=step_id)
    assert payload["job_id"] == job_id
    assert payload["step_id"] == step_id


def test_correlation_id_embedded_in_free_text_survives(caplog):
    caplog.set_level(logging.INFO, logger=observability.LOGGER_NAME)
    payload = observability.log_event("probe", message="任务 job_deadbeefdeadbeefdeadbeefdeadbeef 结束")
    assert "job_deadbeefdeadbeefdeadbeefdeadbeef" in payload["message"]


def test_long_values_are_truncated(caplog):
    """长文本按 MAX_FIELD_CHARS 截断（用带分隔符的内容，避免命中裸 token 兜底规则）。"""
    caplog.set_level(logging.INFO, logger=observability.LOGGER_NAME)
    payload = observability.log_event("probe", note="some log line\n" * 400)
    assert len(payload["note"]) == observability.MAX_FIELD_CHARS
    assert payload["note"].endswith("...")


# ── 容器：不记录完整目标列表 ──────────────────────────────


def test_container_fields_are_capped(caplog):
    """方案第 19 节：不要记录完整目标列表到公共日志。"""
    caplog.set_level(logging.INFO, logger=observability.LOGGER_NAME)
    targets = [f"host{i}.example.test" for i in range(observability.MAX_CONTAINER_ITEMS + 15)]
    payload = observability.log_event("probe", targets=targets)
    assert payload["targets"] == targets[: observability.MAX_CONTAINER_ITEMS]


def test_dict_fields_are_capped_and_nested_values_scrubbed(caplog):
    caplog.set_level(logging.INFO, logger=observability.LOGGER_NAME)
    payload = observability.log_event(
        "probe",
        extra={
            "api_key": "leak-me",
            "targets": [f"h{i}.example.test" for i in range(observability.MAX_CONTAINER_ITEMS + 5)],
        },
    )
    assert payload["extra"]["api_key"] == observability.REDACTED
    assert len(payload["extra"]["targets"]) == observability.MAX_CONTAINER_ITEMS


def test_tuple_and_set_fields_become_lists(caplog):
    caplog.set_level(logging.INFO, logger=observability.LOGGER_NAME)
    payload = observability.log_event("probe", pair=("a", "b"), single={"c"})
    assert payload["pair"] == ["a", "b"]
    assert payload["single"] == ["c"]


# ── format_event / configure_logging ─────────────────────


def test_format_event_text_mode_is_key_value():
    text = observability.format_event({"level": "INFO", "event": "probe", "n": 1}, fmt="text")
    assert text == "level=INFO event=probe n=1"


def test_format_event_unknown_format_falls_back_to_json():
    assert json.loads(observability.format_event({"event": "probe"}, fmt="yaml"))["event"] == "probe"


def test_configure_logging_writes_to_given_stream():
    import io

    stream = io.StringIO()
    observability.configure_logging(level="INFO", fmt="json", stream=stream)
    observability.log_event("probe", note="hello")
    lines = stream.getvalue().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "probe"


def test_configure_logging_is_idempotent():
    import io

    stream = io.StringIO()
    observability.configure_logging(level="INFO", stream=stream)
    observability.configure_logging(level="INFO", stream=stream)
    installed = [h for h in observability.logger.handlers if getattr(h, "_gef_stream", False)]
    assert len(installed) == 1
    observability.log_event("probe")
    assert len(stream.getvalue().strip().splitlines()) == 1


def test_configure_logging_filters_by_level():
    import io

    stream = io.StringIO()
    observability.configure_logging(level="ERROR", stream=stream)
    observability.log_event("probe", level="INFO")
    assert stream.getvalue() == ""
    observability.log_event("probe", level="ERROR")
    assert json.loads(stream.getvalue().strip())["level"] == "ERROR"


def test_configure_logging_defaults_come_from_config(monkeypatch):
    import io

    from config import Config

    monkeypatch.setattr(Config, "LOG_LEVEL", "WARNING")
    monkeypatch.setattr(Config, "LOG_FORMAT", "text")

    stream = io.StringIO()
    observability.configure_logging(stream=stream)
    observability.log_event("probe", note="x")
    assert stream.getvalue() == ""  # WARNING 级别过滤掉 INFO
    observability.log_event("probe", level="WARNING", note="x")
    assert stream.getvalue().strip().startswith("ts=")


def test_configure_logging_survives_broken_config(monkeypatch):
    import io

    import config

    monkeypatch.delattr(config, "Config", raising=False)
    stream = io.StringIO()
    observability.configure_logging(stream=stream)
    observability.log_event("probe")
    assert json.loads(stream.getvalue().strip())["event"] == "probe"


def test_logger_is_silent_but_capturable_by_default(caplog):
    """未调 configure_logging() 时不往 stderr 乱写，但 caplog 仍抓得到。"""
    assert any(isinstance(h, logging.NullHandler) for h in observability.logger.handlers)
    assert observability.logger.propagate is True
    caplog.set_level(logging.INFO, logger=observability.LOGGER_NAME)
    observability.log_event("probe")
    assert caplog.records[-1].name == observability.LOGGER_NAME


# ── 源码守卫（方案第 19 节的禁止项） ──────────────────────

# 唯一被允许打印凭据的位置：启动横幅（M1 验收项「明确日志提示」）。
# 它只写本机控制台、给坐在机器前的人看登录凭据，且刻意**不走**结构化日志。
_ALLOWED_CREDENTIAL_PRINT = {("app.py", "_print_login_hint")}

# 允许保留 print 的文件（均为「进程级人读输出」）。任何新增 print 都必须
# 显式改这个集合 —— 从而被迫回答「这条信息应该进结构化日志吗？」
# 用 ``文件:函数`` 粒度，不用文件粒度：函数里再冒出 print 依然会被拦下。
_PRINT_ALLOWLIST = {
    ("agent_cli.py", "main"),  # 交互式 CLI 的问答界面
    ("app.py", "_print_login_hint"),  # 启动横幅（本机控制台）
    ("jobs/worker.py", "log"),  # worker 控制台进度行（--quiet 可关）
    ("jobs/worker.py", "_handle_signal"),  # 信号提示要先于日志 handler 可见
    ("modules/base.py", "_execute"),  # 各工具适配器的运行期提示
    ("modules/base.py", "_execute_stdout"),
    ("modules/enscan.py", "run_scan"),
    ("modules/shuffledns.py", "_run_dnsx"),
    ("modules/shuffledns.py", "run_scan"),
    ("scripts/migrate_legacy_results.py", "main"),  # stdout 被测试契约观察
    ("scripts/check_env.py", "main"),  # 环境自检：报告本身就是要给人看的 stdout
    ("tool_runner.py", "run_tools"),  # 旧同步执行链的进度输出
}


def _iter_py_files():
    """遍历业务代码（yield ``(相对路径, 源码)``），跳过测试与运行期目录。"""
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[2]
    skip_dirs = {"tests", ".venv", "venv", "results", "uploads", "exports", "__pycache__"}
    for path in sorted(project_root.rglob("*.py")):
        rel = path.relative_to(project_root).as_posix()
        if skip_dirs & set(rel.split("/")):
            continue
        # ``utf-8-sig``：部分历史文件带 BOM，按 utf-8 读会在首行留下 \ufeff。
        yield rel, path.read_text(encoding="utf-8-sig")


def _print_calls():
    """用 AST 找出真正的 ``print(...)`` 调用。

    不能用「行里含 ``print(``」——那会把 ``Blueprint("api", ...)`` 这类
    同形标识符和文档字符串里的示例代码一起误判。
    """
    import ast

    for rel, source in _iter_py_files():
        tree = ast.parse(source)
        owner_of: dict[int, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for child in ast.walk(node):
                    owner_of.setdefault(id(child), node.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                yield rel, node.lineno, owner_of.get(id(node)), ast.get_source_segment(source, node) or ""


def test_no_print_of_secret_shaped_values_in_source():
    """方案第 19 节明令禁止 ``print(f"api_key={key}")`` 这类写法。

    唯一例外是启动横幅 :func:`app._print_login_hint`（见常量注释）。
    """
    import re

    sensitive = re.compile(r"(?i)(api[_-]?key|token|secret|password|passwd|credential)")
    offenders = [
        f"{rel}:{line}: {segment.strip()}"
        for rel, line, owner, segment in _print_calls()
        if sensitive.search(segment) and (rel, owner) not in _ALLOWED_CREDENTIAL_PRINT
    ]
    assert offenders == [], "print 语句里出现疑似密钥输出：\n" + "\n".join(offenders)


def test_observability_is_the_only_logging_entry():
    """结构化日志必须从 ``core.observability`` 走，禁止各模块自建 logger。

    例外 ``core/errors_handlers.py``：它用 ``app.logger.exception`` 打印完整
    traceback（失败时最需要的原始信息），同时另记一条结构化事件。
    """
    import re

    allowed = {"core/observability.py", "core/errors_handlers.py"}
    pattern = re.compile(r"logging\.getLogger|logging\.basicConfig")
    offenders: list[str] = []
    for rel, source in _iter_py_files():
        if rel in allowed:
            continue
        for number, line in enumerate(source.splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{rel}:{number}: {line.strip()}")
    assert offenders == [], "以下位置绕过了 core.observability：\n" + "\n".join(offenders)


def test_new_print_calls_must_be_registered():
    """``print`` 是人读控制台输出，只允许出现在登记过的 ``文件:函数`` 里。

    这不是「禁止 print」——方案第 19 节禁的是**结构化日志用 print**。
    把清单钉死，是为了让任何新增的 ``print`` 都必须显式改这个测试，
    从而逼出一个问题：「这条信息应该进结构化日志吗？」
    """
    offenders = [
        f"{rel}:{line} (def {owner}): {segment.strip()[:80]}"
        for rel, line, owner, segment in _print_calls()
        if (rel, owner) not in _PRINT_ALLOWLIST
    ]
    assert offenders == [], "新增了未登记的 print：\n" + "\n".join(offenders)
