"""
Subfinder 子域名发现工具运行器。

Subfinder 是一款基于被动源的子域名枚举工具，通过调用多个 API
接口（如 SecurityTrails、Censys、Shodan 等）收集目标域名的子域名信息。

M4 起实现方案第 8.1 节的统一接口：``build_command`` / ``run`` /
``parse_output``。``run_scan`` 保持返回字符串列表以兼容旧调用方，
但**推荐调用 ``run()``** —— 它会把「工具没装 / 超时 / 零结果」区分开。
"""

from config import SUBFINDER_CONFIG

from .base import BaseRunner


class SubfinderRunner(BaseRunner):
    """
    Subfinder 运行器，通过被动源 API 收集子域名。

    使用 Subfinder 工具对目标域名执行子域名枚举，
    支持多线程和超时配置，结果直接输出到文件后读取。

    示例配置 (SUBFINDER_CONFIG):
        {
            "path": "subfinder",
            "threads": 10,
            "timeout": 30,
            "silent": True,
            "category": "subdomain"
        }
    """

    def __init__(self):
        """初始化 Subfinder 运行器，加载 SUBFINDER_CONFIG 配置。"""
        super().__init__(SUBFINDER_CONFIG, "subfinder")

    def build_command(self, domain, options=None):
        """
        构建 Subfinder 命令行：subfinder -d <domain> -t <threads> -o <output_file>
        可选参数包括 ``-timeout``、``-silent`` 与 ``-rl``（每秒请求上限）。

        Args:
            domain: 目标域名字符串。
            options: 可选的覆盖项，支持 ``output_file``。

        Returns:
            命令行的参数列表。
        """
        options = options or {}
        output_file = options.get("output_file") or self._build_output_file(domain)
        cmd = [
            self.config["path"],
            "-d",
            domain,
            "-t",
            str(self.config["threads"]),
            "-o",
            output_file,
        ]

        # 低频档（``core.pace``）会往 config 副本里塞 ``rate_limit``：
        # 只有配置里真的有这个键才拼 ``-rl``，因此常规档的命令行与历史逐字节一致。
        rate_limit = self.config.get("rate_limit")
        if rate_limit:
            cmd.extend(["-rl", str(rate_limit)])

        timeout = self.config.get("timeout")
        if timeout:
            cmd.extend(["-timeout", str(timeout)])

        if self.config.get("silent", False):
            cmd.append("-silent")

        return cmd

    def parse_output(self, stdout, stderr, artifacts=None):
        """
        从输出文件中读取子域名。

        Subfinder 通过 ``-o`` 写文件而不是 stdout，因此 ``stdout`` 参数
        在这里只用于兜底（例如配置改成输出到 stdout 时）。

        Args:
            stdout: 子进程标准输出（可为空）。
            stderr: 子进程标准错误（未使用）。
            artifacts: 可选 dict，支持 ``output_file`` 指定要读的文件。

        Returns:
            tuple[list[str], str | None]: ``(子域名列表, 解析错误码)``。
        """
        artifacts = artifacts or {}
        output_file = artifacts.get("output_file")
        if output_file:
            values = self._read_results(output_file)
            if values:
                return values, None
            return [], None
        # 没有指定文件时退回 stdout 解析。
        lines = [line.strip() for line in (stdout or "").splitlines() if line.strip()]
        return lines, None

    def run_scan(self, domain):
        """
        执行 Subfinder 子域名扫描。

        Args:
            domain: 目标域名字符串

        Returns:
            发现的子域名列表，扫描失败时返回空列表。

        Note:
            失败与零结果在这一层无法区分（历史签名如此）。需要区分时请调用
            :meth:`BaseRunner.run`，它会返回带 ``error_code`` 的 ``RunnerResult``。
        """
        output_file = self._build_output_file(domain)
        cmd = self.build_command(domain, {"output_file": output_file})

        if not self._execute(cmd, domain):
            return []

        return self._read_results(output_file)
