"""P0-3 / P0-5 / P0-6 单元测试：Agent 执行边界。

方案第 6 节要求 Agent 不得绕过受控入口：

* **P0-3** Agent 不得接受任意 ``file_path``，文件目标只能来自受控 ``upload_id``；
* **P0-5** Agent 的导出回复不得携带服务器绝对路径；
* **P0-6** Agent 的每一次执行都必须落在明确的工具 handler 上，并留下步骤记录
  （可审计），不得静默改道。

本文件不发起真实扫描：``run_tools`` 被 monkeypatch 成确定性桩函数。
"""

import json

import pytest

from agent.action import AgentAction
from agent.intent import UserIntent
from agent.planner import build_uploaded_file_plan
from core import exports as exports_store


@pytest.fixture
def agent(local_db, store, monkeypatch, tmp_path):
    """把上传目录指向临时路径的 Agent 实例。"""
    from core import uploads as core_uploads

    monkeypatch.setattr(core_uploads, "UPLOAD_DIR", str(tmp_path / "uploads"), raising=False)
    return AgentAction(store=store)


# ── P0-3：任意 file_path 必须被拒绝 ───────────────────────


@pytest.mark.parametrize(
    "evil_path",
    [
        r"C:\Windows\System32\drivers\etc\hosts",
        "/etc/passwd",
        "../../config.py",
        "//attacker/share/targets.txt",
    ],
)
def test_agent_subdomain_rejects_arbitrary_file_path(agent, evil_path, monkeypatch):
    """Agent 层也必须挡住任意 file_path（不能只在 API 层挡）。"""
    called = []
    monkeypatch.setattr(
        "agent.action.run_tools",
        lambda **kwargs: called.append(kwargs) or {"targets": [], "total_found": 0, "total_inserted": 0},
    )

    result = agent._execute_tool("subdomain", {"file_path": evil_path, "tool": "subfinder"})

    assert result["ok"] is False
    assert "file_path" in result["error"]
    assert called == []  # 关键：一次都没真正跑


def test_agent_subdomain_accepts_controlled_upload_id(agent, monkeypatch):
    """受控 upload_id 走通：由 core.uploads 解析路径，Agent 自己拼不出路径。"""
    upload_id = _make_upload()
    captured = {}

    def _fake_run_tools(**kwargs):
        captured.update(kwargs)
        return {"targets": ["a.example.test", "b.example.test"], "total_found": 2, "total_inserted": 2}

    monkeypatch.setattr("agent.action.run_tools", _fake_run_tools)

    result = agent._execute_tool("subdomain", {"upload_id": upload_id, "tool": "subfinder"})

    assert result["ok"] is True
    assert result["upload_id"] == upload_id
    assert result["target_count"] == 2
    # 传给编排层的必须是 core.uploads 解析出的路径，而不是调用方给的值。
    from core import uploads as core_uploads

    assert captured["file_path"] == core_uploads.resolve_targets_file(upload_id)


def test_agent_subdomain_rejects_unknown_upload_id(agent, monkeypatch):
    called = []
    monkeypatch.setattr("agent.action.run_tools", lambda **kwargs: called.append(kwargs))

    result = agent._execute_tool("subdomain", {"upload_id": "upload_deadbeef", "tool": "subfinder"})

    assert result["ok"] is False
    assert "upload_id" in result["error"]
    assert called == []


@pytest.mark.parametrize("bad_id", ["../etc/passwd", "upload_x/../../y", "a\\b"])
def test_agent_subdomain_rejects_traversal_upload_id(agent, bad_id, monkeypatch):
    called = []
    monkeypatch.setattr("agent.action.run_tools", lambda **kwargs: called.append(kwargs))

    result = agent._execute_tool("subdomain", {"upload_id": bad_id, "tool": "subfinder"})

    assert result["ok"] is False
    assert called == []


# ── P0-3：planner 不再产出 file_path ──────────────────────


def _upload_intent() -> UserIntent:
    return UserIntent(raw_text="对刚上传的目标列表做子域名收集", intent_type="uploaded_file_scan", target_type="uploaded_file")


def test_planner_emits_upload_id_not_file_path():
    plan = build_uploaded_file_plan(_upload_intent(), {"upload_id": "upload_abc", "target_count": 3})

    step_args = plan.steps[0].args
    assert step_args["upload_id"] == "upload_abc"
    assert "file_path" not in step_args
    assert "file_path" not in (plan.target or "")


def test_planner_rejects_legacy_file_path_record():
    """老 session 里可能残留含 file_path 的记录：必须提示重传，而不是照旧执行。"""
    plan = build_uploaded_file_plan(_upload_intent(), {"file_path": r"C:\temp\targets.txt", "target_count": 3})

    assert plan.steps == []
    assert "upload_id" in (plan.message or "")


def test_planner_without_upload_returns_guidance():
    plan = build_uploaded_file_plan(_upload_intent(), None)

    assert plan.steps == []
    assert "上传" in (plan.message or "")


# ── P0-5：Agent 导出不泄露路径 ───────────────────────────


def test_agent_export_returns_download_url_not_path(agent):
    result = agent._execute_tool("export_results", {"format": "csv"})

    assert result["ok"] is True
    assert "path" not in result
    assert result["export_id"].startswith("exp_")
    assert result["download_url"] == f"/api/export/{result['export_id']}/download"

    # 导出文件的真实磁盘路径绝不能出现在对外结果里。
    record = exports_store.get_export(result["export_id"])
    assert record["path"] not in json.dumps(result, ensure_ascii=False)


def test_agent_export_intent_response_has_no_absolute_path(agent):
    """整条链路（intent → plan → 执行 → 回复）都不得出现导出文件的绝对路径。"""
    reply = agent.run("导出为 CSV")

    steps = reply.get("steps") or []
    export_steps = [step for step in steps if step.get("action") == "export_results"]
    if export_steps:  # 意图识别到导出时才会有这一步
        body = export_steps[0]["result"]
        assert "path" not in body
        record = exports_store.get_export(body["export_id"])
        assert record["path"] not in json.dumps(reply, ensure_ascii=False)


# ── P0-6：执行留下可审计步骤 ─────────────────────────────


def test_agent_execution_records_steps(agent):
    """Agent 每执行一步都要留下记录，便于审计与回溯（只读路径也应记录）。"""
    reply = agent.run("查看 example.test 的已有结果")

    assert reply["steps"], "Agent 执行后必须留下步骤记录"
    for step in reply["steps"]:
        assert set(step) >= {"ts", "action", "args", "result"}
        assert isinstance(step["result"], dict)


def test_agent_unknown_tool_is_reported_not_executed(agent):
    result = agent._execute_tool("definitely_not_a_tool", {})

    assert result["ok"] is False
    assert "未知工具" in result["error"]


# ── Bug 修复：httpx 步骤的 items 必须是元数据字典 ──────────


def test_agent_httpx_returns_metadata_items(agent, monkeypatch):
    """``_tool_httpx`` 的 ``items`` 必须是结构化元数据，而不是 URL 字符串。

    回归用例：``run_scan`` 返回的是 URL ``str`` 列表，而 ``_summarize_httpx_items``
    逐条调 ``item.get(...)``。以前 ``items`` 直接取了 ``rows``，于是「存活探测 →
    整理回复」这条路径会以 ``AttributeError: 'str' object has no attribute 'get'``
    收场 —— 而它只在真实跑出结果时才触发，零结果时反而看不出来。
    """
    metadata = [{"url": "https://a.example.test", "status_code": 200, "webserver": "nginx", "tech": ["Nginx"]}]

    class _FakeHttpxRunner:
        last_items = metadata

        def run_scan(self, domain, candidates=None, tech_detect=False):
            return [item["url"] for item in metadata]

    monkeypatch.setattr("agent.action.HttpxRunner", _FakeHttpxRunner)
    monkeypatch.setattr(agent, "_enforce_rate_limit", lambda action, domain: None)

    result = agent._execute_tool("httpx", {"domain": "a.example.test"})

    assert result["ok"] is True
    assert result["total"] == 1
    assert result["items"] == metadata
    # 真正会炸的那一步：把结果渲染成对话回复。
    text = agent._format_single_tool_result(result)
    assert "常见 Web Server：nginx" in text


def _make_upload(content: bytes = b"a.example.test\nb.example.test\n") -> str:
    """通过受控入口造一条真实上传记录，返回 upload_id。"""
    from werkzeug.datastructures import FileStorage

    from core import uploads as core_uploads

    storage = FileStorage(stream=__import__("io").BytesIO(content), filename="targets.txt")
    return core_uploads.save_upload(storage)["upload_id"]
