"""M0 冒烟测试：核心模块可导入、关键常量符合本机联调版的硬性约束。

这些用例只固化「基线事实」，不依赖外部工具、不发网络请求、不写数据库。
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_core_modules_importable():
    """核心模块必须能被导入（收集错误会直接让 CI 失败）。"""
    import config  # noqa: F401
    import exporter  # noqa: F401
    import storage  # noqa: F401
    import target_parser  # noqa: F401
    import tool_runner  # noqa: F401


def test_app_factory_importable_and_registers_api():
    """Flask 应用工厂可导入，且已挂载 API Blueprint。"""
    import app

    assert app.app is not None
    rules = {rule.rule for rule in app.app.url_map.iter_rules()}
    assert "/" in rules
    assert any(rule.startswith("/api/") for rule in rules)


def test_upload_size_limit_is_2mb():
    """上传上限必须是 2 MB（M2 验收项之一）。"""
    from config import MAX_UPLOAD_SIZE

    assert MAX_UPLOAD_SIZE == 2 * 1024 * 1024


def test_output_dir_points_to_results():
    """扫描产物目录仍然落在 results/ 下（该目录不得进入 Git）。"""
    from config import OUTPUT_DIR

    assert Path(OUTPUT_DIR).name == "results"


def test_runner_registry_has_expected_tools():
    """Runner 注册中心包含 README 承诺的 17 个工具。"""
    from modules.registry import get_supported_runners

    runners = set(get_supported_runners())
    assert {"subfinder", "httpx", "nmap", "naabu", "waybackurls"} <= runners
    assert len(runners) == 17
