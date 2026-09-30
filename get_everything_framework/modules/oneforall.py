"""
OneForAll 综合子域名收集工具运行器。

OneForAll 是一款功能强大的子域名收集工具，集成了多种数据源和收集方式，
包括搜索引擎、证书透明度日志、DNS 数据集等，支持对目标域名进行全面的子域名收集。
"""

from config import ONEFORALL_CONFIG

from .base import BaseRunner


class OneForAllRunner(BaseRunner):
    """
    OneForAll 运行器，综合收集目标域名的子域名。

    OneForAll 通过 Python 脚本调用，支持灵活的配置参数。
    输出到 stdout 后捕获写入文件，再读取返回。

    示例配置 (ONEFORALL_CONFIG):
        {
            "path": "python",
            "target_flag": "--target",
            "run_args": ["oneforall.py", "run"],
            "extra_args": [],
            "category": "subdomain"
        }
    """

    def __init__(self):
        """初始化 OneForAll 运行器，加载 ONEFORALL_CONFIG 配置。"""
        super().__init__(ONEFORALL_CONFIG, "oneforall")

    def run_scan(self, domain):
        """
        执行 OneForAll 子域名收集。

        构建命令行：python oneforall.py --target <domain> run [extra_args]
        输出通过 stdout 捕获并写入文件，之后读取返回。

        Args:
            domain: 目标域名字符串

        Returns:
            收集到的子域名列表，扫描失败时返回空列表
        """
        output_file = self._build_output_file(domain)
        cmd = [
            self.config["path"],
            self.config.get("target_flag", "--target"),
            domain,
        ]
        cmd.extend(self.config.get("run_args", ["run"]))
        cmd.extend(self.config.get("extra_args", []))

        if not self._execute_stdout(cmd, domain, output_file):
            return []

        return self._read_results(output_file)
