"""M0 仓库布局测试：本机联调版要求的目录与文件必须存在。

用途：防止后续里程碑误删基线文件，也作为「新开发者照文档能起来」的前置检查。
"""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PROJECT_ROOT.parent

REQUIRED_PROJECT_FILES = [
    "app.py",
    "config.py",
    "tool_runner.py",
    "storage.py",
    "exporter.py",
    "target_parser.py",
    "requirement.txt",
    "requirement-dev.txt",
    "pyproject.toml",
    "README.md",
]

REQUIRED_REPO_FILES = [
    ".gitignore",
    "LICENSE",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
]


@pytest.mark.parametrize("rel_path", REQUIRED_PROJECT_FILES)
def test_project_files_exist(rel_path):
    assert (PROJECT_ROOT / rel_path).is_file(), f"缺少项目文件: {rel_path}"


@pytest.mark.parametrize("rel_path", REQUIRED_REPO_FILES)
def test_repo_files_exist(rel_path):
    assert (REPO_ROOT / rel_path).is_file(), f"缺少仓库文件: {rel_path}"


def test_ci_workflow_exists():
    """CI workflow 必须位于 Git 仓库根的 .github/workflows/ 下。"""
    workflows = REPO_ROOT / ".github" / "workflows"
    assert workflows.is_dir()
    assert list(workflows.glob("*.yml")) or list(workflows.glob("*.yaml"))


def test_gitignore_covers_runtime_artifacts():
    """运行期产物（数据库 / 结果 / 上传 / 虚拟环境 / 密钥）必须在 .gitignore 中。"""
    ignore_text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in ["__pycache__/", ".env", "venv/", ".venv/"]:
        assert pattern in ignore_text, f".gitignore 缺少规则: {pattern}"


# 运行期目录/库文件一旦被测试写进去，就会以「未提交改动」的形式留在工作区，
# 且下一轮 `git status` 看不出是测试干的（AGENTS.md 硬约束：测试不得污染仓库）。
def test_autouse_fixture_redirects_every_runtime_path():
    """``tests/conftest.py:_isolate_runtime_dirs`` 必须钉住全部运行期路径。

    这是回归锁：本文件曾实测出三类泄漏（``exports/`` 每跑一次多一个空 CSV、
    ``results/local.db`` 每跑一次多 3 行 audit/uploads、``results/worker_heartbeat``
    被真实 Worker 刷新）。只要有人把 autouse 夹具删掉或漏掉某一项，
    这里立刻变红 —— 而不是等下一次提交时才发现工作区脏了。
    """
    import core.artifacts as core_artifacts
    import core.db as core_db
    import core.health as core_health
    import core.uploads as core_uploads
    import exporter
    import jobs.worker as worker

    repo_results = (PROJECT_ROOT / "results").resolve()
    repo_exports = (PROJECT_ROOT / "exports").resolve()
    repo_uploads = (PROJECT_ROOT / "uploads").resolve()

    local_db = Path(core_db.db_path()).resolve()
    assert local_db != (repo_results / "local.db").resolve(), "本机应用库仍指向仓库 results/"
    assert repo_results not in local_db.parents, f"本机应用库落在仓库 results/ 里: {local_db}"

    for label, path, repo_dir in (
        ("core.health.OUTPUT_DIR", core_health.OUTPUT_DIR, repo_results),
        ("core.artifacts.ARTIFACT_DIR", core_artifacts.ARTIFACT_DIR, repo_results),
        ("jobs.worker.OUTPUT_DIR", worker.OUTPUT_DIR, repo_results),
        ("core.uploads.UPLOAD_DIR", core_uploads.UPLOAD_DIR, repo_uploads),
        ("exporter.EXPORT_DIR", exporter.EXPORT_DIR, repo_exports),
    ):
        resolved = Path(path).resolve()
        assert repo_dir not in resolved.parents, f"{label} 仍指向仓库目录: {resolved}"
