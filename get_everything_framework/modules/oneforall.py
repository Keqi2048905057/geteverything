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

    def build_command(self, domain, options=None):
        """
        构建 OneForAll 命令行：``python oneforall.py --target <domain> run``

        结果只走 stdout（OneForAll 自己写 Excel/CSV 到它自己的 results 目录，
        与我们的输出文件无关），因此这里不带输出文件参数。

        Args:
            domain: 目标域名字符串。
            options: 当前未使用的占位参数（与其他 runner 签名保持一致）。

        Returns:
            命令行的参数列表。
        """
        cmd = [
            self.config["path"],
            self.config.get("target_flag", "--target"),
            domain,
        ]
        cmd.extend(self.config.get("run_args", ["run"]))
        cmd.extend(self.config.get("extra_args", []))
        return cmd

    def parse_output(self, stdout, stderr, artifacts=None):
        """
        从输出文件（或 stdout）读取子域名。

        Args:
            stdout: 子进程标准输出（兜底来源）。
            stderr: 子进程标准错误（未使用）。
            artifacts: 支持 ``output_file``。

        Returns:
            tuple[list[str], str | None]: ``(子域名列表, 解析错误码)``。
        """
        artifacts = artifacts or {}
        output_file = artifacts.get("output_file")
        if output_file:
            values = self._read_results(output_file)
            if values:
                return values, None
        return [line.strip() for line in (stdout or "").splitlines() if line.strip()], None

    def run_scan(self, domain):
        """
        执行 OneForAll 子域名收集。

        构建命令行：python oneforall.py --target <domain> run [extra_args]
        输出通过 stdout 捕获并写入文件，之后读取返回。

        Args:
            domain: 目标域名字符串

        Returns:
            收集到的子域名列表，扫描失败时返回空列表

        Note:
            需要区分「失败」与「零结果」时请调用 :meth:`BaseRunner.run`。
        """
        output_file = self._build_output_file(domain)
        cmd = self.build_command(domain)

        if not self._execute_stdout(cmd, domain, output_file):
            return []

        return self._read_results(output_file)
