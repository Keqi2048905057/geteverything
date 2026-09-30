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
