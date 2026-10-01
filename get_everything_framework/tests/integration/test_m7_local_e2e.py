"""M7 本地全链路 E2E（方案第 18 节）。

方案第 18 节的原话：

    不要为了验证真实链路去扫未授权公网目标。
    ...
    这条链路必须至少有一条全流程测试。

因此这里用 ``tests/fixtures/local_http_server.py``（**只绑定 ``127.0.0.1``**、
端口由系统分配、响应完全确定）当目标，并且跑的是**真实 httpx 子进程**，
不是 `tests/integration/test_m4_runner_result.py` 里那种假 runner。
链路一次性走完：

    target → job → worker → runner(真实 httpx) → raw artifact
           → parser → observation → asset → diff → export

关于候选目标（唯一被替换的一步）
--------------------------------

httpx 的候选来自 ``ScanResultStore``（``HttpxRunner._load_candidates``），
而 ``storage.py`` 按方案要求**只有写入、没有删除接口**（禁止删/清历史数据），
所以同一个域名的候选集在库里只增不减。方案第 10 节的验收形态
「A B C → A C D」需要候选集**收缩**一次，这在库层面无法表达，
于是第二次任务显式替换 ``_load_candidates`` 来模拟「上游子域发现这次给出了
不同的集合」。

被替换的只是**输入发现**；httpx 之后的一切（命令行构造、子进程执行、
JSONL 解析、原始证据落盘、观测归一、资产归并、diff、导出）全是真实代码。

测试隔离
--------

``tests/conftest.py`` 只覆盖了 config 层与 core 层；``modules.base`` /
``modules.httpx`` / ``jobs.worker`` 在**导入时**就把 ``OUTPUT_DIR`` 绑定成了
模块级字符串，因此本文件额外把它们各自指向 ``tmp_path``，避免真实 httpx 的
JSONL 输出与 worker 心跳写进仓库的 ``results/``（AGENTS.md 硬约束）。
"""

import os
import shutil

import pytest

from config import HTTPX_CONFIG
from tests.fixtures.local_http_server import (
    FORBIDDEN_TITLE,
    LocalHttpServer,
    SERVER_HEADER,
)

# 目标的 Scope 必须显式放行本机回环网段（IP 目标只认 allowed_cidrs）。
LOOPBACK_CIDR = "127.0.0.0/8"
TARGET = "127.0.0.1"


def _require_httpx() -> None:
    """没有真实 httpx 可执行文件时跳过（CI 上不装 Go 工具链）。"""
    if shutil.which(HTTPX_CONFIG["path"]) is None:
        pytest.skip(f"未找到 httpx 可执行文件（{HTTPX_CONFIG['path']}），跳过本地全链路 E2E")


@pytest.fixture
def fixture_server():
    """只监听 ``127.0.0.1`` 的确定性 fixture 服务。"""
    with LocalHttpServer() as server:
        yield server


@pytest.fixture
def runner_output_dir(app_module, tmp_path, monkeypatch):
    """把真实 runner / worker 的输出目录也压到临时目录。

    依赖 ``app_module``：它已经把两个数据库与 artifacts / exports 目录指向
    ``tmp_path``，这里只补三处**模块级**绑定。
    """
    target = str(tmp_path / "results")
    os.makedirs(target, exist_ok=True)
    for module in ("base", "httpx"):
        monkeypatch.setattr(f"modules.{module}.OUTPUT_DIR", target, raising=False)
    # worker 心跳文件（/health 读它）走的是 jobs.worker 自己的 OUTPUT_DIR 副本。
    monkeypatch.setattr("jobs.worker.OUTPUT_DIR", target, raising=False)
    return target


def _make_scope(admin_client):
    resp = admin_client.post(
        "/api/scopes",
        json={
            "name": "M7 本地全链路范围",
            "allowed_cidrs": [LOOPBACK_CIDR],
            "active_scan": True,
        },
    )
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["scope"]["id"]


def _create_job(admin_client, scope_id):
    """建一个 real 模式的 httpx 任务（real 需要 Scope.active_scan + 环境开关）。"""
    resp = admin_client.post(
        "/api/jobs",
        json={
            "scope_id": scope_id,
            "targets": [TARGET],
            "tools": ["httpx"],
            "mode": "real",
        },
    )
    assert resp.status_code == 202, resp.get_json()
    return resp.get_json()["job_id"]


def _drain_worker(worker_id: str = "m7-e2e") -> int:
    """跑一轮 worker，把队列里的任务全部处理掉（不进常驻循环）。"""
    from jobs.worker import Worker

    worker = Worker(worker_id=worker_id, verbose=False)
    worker.startup()
    processed = 0
    while worker.tick() is not None:
        processed += 1
        if processed > 20:  # 防御：正常用例不会跑到这里
            break
    return processed


def _candidate(server: LocalHttpServer, path: str) -> str:
    """``host:port/path`` 形态的候选（上游子域发现产出的就是这种不带 scheme 的值）。"""
    return f"{TARGET}:{server.port}{path}"


def _step_observation_map(step: dict) -> dict:
    return {item["value"]: item for item in step["observations"]}


# ── fixture 自身的安全性质 ────────────────────────────────


def test_fixture_server_is_loopback_only(fixture_server):
    """E2E 的目标必须只指向本机 —— 这是「不扫未授权目标」约束的落点。"""
    from core.safety import is_local_only_target

    assert fixture_server.base_url.startswith("http://127.0.0.1:")
    assert is_local_only_target(TARGET)


# ── 全链路 ────────────────────────────────────────────────


@pytest.mark.slow
def test_local_full_chain_target_to_export(
    admin_client,
    fixture_server,
    runner_output_dir,
    monkeypatch,
):
    """一条用例走完 target → job → worker → runner → artifact → parser →
    observation → asset → diff → export（方案第 18 节）。"""
    _require_httpx()
    # real 模式的双开关之一（另一个是 Scope.active_scan）。
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")

    from storage import ScanResultStore

    store = ScanResultStore()

    # 上游子域发现的结果：第一次任务看到 /、/stable、/forbidden。
    store.save_dedicated_results(
        TARGET,
        "subfinder",
        "subdomain",
        [_candidate(fixture_server, path) for path in ("/", "/stable", "/forbidden")],
    )

    scope_id = _make_scope(admin_client)
    before_job = _create_job(admin_client, scope_id)
    assert _drain_worker() >= 1

    # ── target → job → worker → runner ────────────────────
    before_detail = admin_client.get(f"/api/jobs/{before_job}").get_json()["job"]
    assert before_detail["status"] == "succeeded", before_detail
    before_step = before_detail["steps"][0]
    assert before_step["tool_name"] == "httpx"
    assert before_step["status"] == "succeeded"
    assert before_step["error_code"] is None
    # found_count = 解析出的 URL 条数（真实 httpx 探测了 3 个候选）。
    assert before_step["found_count"] == 3
    # 命令预览来自真实执行，且带工具名（不能是空串）。
    assert before_step["command_preview"]
    assert before_step["duration_ms"] is not None

    # ── parser → observation（结构化字段没被丢掉） ────────
    assert before_step["parser_version"] == "1.0"
    observed = _step_observation_map(before_step)
    assert set(observed) == {
        fixture_server.url("/"),
        fixture_server.url("/stable"),
        fixture_server.url("/forbidden"),
    }
    home = observed[fixture_server.url("/")]
    assert home["category"] == "web"
    assert home["data"]["status_code"] == 200
    assert home["data"]["title"] == "Local Fixture Home"
    assert home["data"]["webserver"] == SERVER_HEADER
    assert home["source_tool"] == "httpx"

    # ── raw artifact（原始证据落盘，且不泄露服务器路径） ──
    artifacts = admin_client.get(f"/api/jobs/{before_job}/artifacts").get_json()["artifacts"]
    assert artifacts, "真实 httpx 必须至少落下一份原始证据"
    assert all("path" not in row for row in artifacts)
    # stdout（httpx 会把它自己的 JSONL 也打到标准输出）与 -o 结果文件。
    assert {"stdout", "output"} <= {row["kind"] for row in artifacts}

    # executor 按 stdout → stderr → output 的顺序落盘，**第一个**成功的才回填到
    # step.artifact_id（见 jobs/executor.py 的 artifact_sources）。
    assert before_step["artifact_id"] == next(
        row["id"] for row in artifacts if row["kind"] == "stdout"
    )

    output_artifact = next(row for row in artifacts if row["kind"] == "output")
    payload = admin_client.get(f"/api/artifacts/{output_artifact['id']}").get_json()["artifact"]
    assert payload["missing"] is False
    assert payload["sha256"]
    assert payload["truncated"] is False
    assert "path" not in payload
    # 三条 JSONL 记录（httpx 单行一条）必须全部保留：读取证据时只做脱敏、
    # 不做命令预览那种 300 字符截断。
    assert len(payload["text"].splitlines()) == 3
    assert fixture_server.url("/stable") in payload["text"]
    # 结果文件确实写在被替换过的临时输出目录里，而不是仓库 results/。
    assert os.path.exists(os.path.join(runner_output_dir, f"{TARGET}_httpx.jsonl"))

    # ── observation → asset ──────────────────────────────
    observations = admin_client.get(f"/api/observations?job_id={before_job}").get_json()
    assert observations["total"] == 3
    assert {row["job_id"] for row in observations["observations"]} == {before_job}

    assets = admin_client.get(f"/api/assets?scope_id={scope_id}").get_json()
    assert assets["total"] == 3
    assert {row["type"] for row in assets["assets"]} == {"url"}
    assert {row["value"] for row in assets["assets"]} == {
        fixture_server.url("/"),
        fixture_server.url("/stable"),
        fixture_server.url("/forbidden"),
    }
    summary = admin_client.get(f"/api/assets/summary?scope_id={scope_id}").get_json()
    assert summary["by_type"] == {"url": 3}

    home_asset_id = next(
        row["asset_id"] for row in assets["assets"] if row["value"] == fixture_server.url("/")
    )
    home_asset = admin_client.get(f"/api/assets/{home_asset_id}").get_json()["asset"]
    assert home_asset["first_seen"] and home_asset["last_seen"]
    assert len(home_asset["observations"]) == 1
    assert home_asset["observations"][0]["data"]["status_code"] == 200

    # ── 第二次任务：上游候选集变了，且 / 的属性也变了 ─────
    fixture_server.set_status("/", 403, title=FORBIDDEN_TITLE)
    after_candidates = [
        _candidate(fixture_server, path) for path in ("/", "/stable", "/extra")
    ]
    monkeypatch.setattr(
        "modules.httpx.HttpxRunner._load_candidates",
        lambda self, domain: list(after_candidates),
    )

    after_job = _create_job(admin_client, scope_id)
    assert _drain_worker(worker_id="m7-e2e-after") >= 1

    # ── diff（方案第 10 节的 A B C → A C D：/stable 不变、/ 变了、
    #         /extra 新增、/forbidden 消失） ──────────────
    diff = admin_client.get(f"/api/jobs/{before_job}/diff/{after_job}?scope_id={scope_id}").get_json()
    assert diff["counts"] == {"added": 1, "removed": 1, "changed": 1, "unchanged": 1}

    assert [row["value"] for row in diff["added"]] == [fixture_server.url("/extra")]
    assert [row["value"] for row in diff["removed"]] == [fixture_server.url("/forbidden")]
    assert [row["value"] for row in diff["unchanged"]] == [fixture_server.url("/stable")]

    changed = diff["changed"]
    assert len(changed) == 1
    assert changed[0]["value"] == fixture_server.url("/")
    assert changed[0]["changes"]["status_code"] == {"from": 200, "to": 403}
    assert changed[0]["changes"]["title"] == {"from": "Local Fixture Home", "to": FORBIDDEN_TITLE}
    # diff 条目带溯源信息，前端据此可跳回两次任务。
    assert changed[0]["asset_id"] == home_asset_id
    assert changed[0]["source_tool"] == "httpx"

    # 资产是**归并**的：4 条 URL（/、/stable、/forbidden、/extra）而不是 6 条观测。
    assets_after = admin_client.get(f"/api/assets?scope_id={scope_id}").get_json()
    assert assets_after["total"] == 4
    assert len(admin_client.get(f"/api/assets/{home_asset_id}").get_json()["asset"]["observations"]) == 2
    assert admin_client.get(f"/api/observations?job_id={after_job}").get_json()["total"] == 3

    # ── export（链路终点：可下载的导出文件） ─────────────
    # 注意：``/api/export`` 读的是**旧** ``ScanResultStore``（`gather_export_rows`），
    # 不是新的资产模型，所以这里导出到的是上游候选的原始字面值（``host:port/path``），
    # 而不是归一化后的 URL。把这个现状钉进测试，避免以后误以为它导出的是资产。
    export = admin_client.get(f"/api/export?format=csv&domain={TARGET}").get_json()
    assert export["ok"] is True
    assert export["row_count"] >= 3
    assert not any("path" in str(key).lower() for key in export)
    assert export["download_url"] == f"/api/export/{export['export_id']}/download"

    download = admin_client.get(export["download_url"])
    try:
        assert download.status_code == 200
        csv_text = download.data.decode("utf-8-sig")
    finally:
        # send_file 会把文件句柄挂在响应上，测试里必须显式关闭。
        download.close()
    assert csv_text.splitlines()[0].startswith("category,created_at,domain,tool_name,value")
    for path in ("/", "/stable", "/forbidden"):
        assert _candidate(fixture_server, path) in csv_text

    listed = admin_client.get("/api/exports").get_json()["exports"]
    assert export["export_id"] in {row["export_id"] for row in listed}
    assert all("path" not in row for row in listed)
