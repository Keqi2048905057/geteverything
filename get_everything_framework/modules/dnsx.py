"""
Dnsx DNS 解析工具运行器。

Dnsx 是 ProjectDiscovery 开发的高性能 DNS 查询工具，
支持 A、AAAA、CNAME、NS、MX、TXT 等多种记录类型的批量查询，
用于验证子域名候选列表的真实存活状态。

M4 起实现方案第 8.1 节的统一接口（``build_command`` / ``run`` /
``parse_output``）；``run_scan`` 的旧签名与返回值保持不变。
"""

import os
import tempfile

from config import DNSX_CONFIG, OUTPUT_DIR
from storage import ScanResultStore

from .base import BaseRunner


class DnsxRunner(BaseRunner):
    """
    Dnsx 运行器，对子域名候选列表进行 DNS 解析验证。

    工作流程：
    1. 从 ScanResultStore 加载目标域名的子域名候选列表
    2. 将候选人列表写入临时输入文件
    3. 调用 Dnsx 工具进行 DNS 批量查询
    4. 清理临时输入文件

    示例配置 (DNSX_CONFIG):
        {
            "path": "dnsx",
            "threads": 50,
            "silent": True,
            "resp_only": False,
            "extra_args": [],
            "category": "dns"
        }
    """

    def __init__(self):
        """初始化 Dnsx 运行器，加载 DNSX_CONFIG 配置并创建存储实例。"""
        super().__init__(DNSX_CONFIG, "dnsx")
        self.store = ScanResultStore()

    def _load_candidates(self, domain):
        """
        从 ScanResultStore 加载已有子域名候选列表。

        如果存储中没有该域名的子域名记录，则退化为仅返回目标域名本身。

        Args:
            domain: 目标域名字符串

        Returns:
            去重后的子域名候选列表，至少包含目标域名
        """
        rows = self.store.get_results_by_domain(domain)
        candidates = list(dict.fromkeys(subdomain for subdomain, _, _ in rows))
        if not candidates:
            return [domain]
        return candidates

    def _write_input_file(self, domain, candidates, suffix=None):
        """
        创建 Dnsx 临时输入文件。

        将子域名候选列表写入临时文件，供 Dnsx 通过 -l 参数批量读取。

        Args:
            domain: 目标域名（用于文件名标识）
            candidates: 子域名候选字符串列表
            suffix: 自定义文件名后缀；默认 ``_<domain>_dnsx_input.txt``。
                保留该参数以保持与基类 :meth:`BaseRunner._write_input_file`
                一致的签名（runner 注册表按基类类型持有子类实例）。

        Returns:
            临时文件的完整路径
        """
        temp_file = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=suffix or f"_{domain}_dnsx_input.txt",
            dir=OUTPUT_DIR,
            delete=False,
        )
        try:
            temp_file.write("\n".join(candidates))
            temp_file.write("\n")
        finally:
            temp_file.close()
        return temp_file.name

    def build_command(self, domain, options=None):
        """
        构建 Dnsx 命令行。

        命令行：``dnsx -l <input_file> -o <output_file> -t <threads> [options] [extra_args]``

        Args:
            domain: 目标域名字符串。
            options: 支持 ``input_file`` / ``output_file``。

        Returns:
            命令行的参数列表。
        """
        options = options or {}
        cmd = [
            self.config["path"],
            "-l",
            options["input_file"],
            "-o",
            options["output_file"],
            "-t",
            str(self.config["threads"]),
        ]

        if self.config.get("silent", False):
            cmd.append("-silent")

        if self.config.get("resp_only", False):
            cmd.append("-resp-only")

        cmd.extend(self.config.get("extra_args", []))

        return cmd

    def parse_output(self, stdout, stderr, artifacts=None):
        """
        从输出文件（或 stdout）读取解析成功的子域名。

        Args:
            stdout: 子进程标准输出（兜底用）。
            stderr: 子进程标准错误（未使用）。
            artifacts: 支持 ``output_file``。

        Returns:
            tuple[list[str], str | None]: ``(存活子域名列表, 解析错误码)``。
        """
        artifacts = artifacts or {}
        output_file = artifacts.get("output_file")
        if output_file:
            return self._read_results(output_file), None
        return [line.strip() for line in (stdout or "").splitlines() if line.strip()], None

    def run_scan(self, domain):
        """
        执行 Dnsx DNS 解析验证。

        Args:
            domain: 目标域名字符串

        Returns:
            通过 DNS 解析验证的存活子域名列表，扫描失败时返回空列表。

        Note:
            需要区分「失败」与「零结果」时请调用 :meth:`BaseRunner.run`。
        """
        output_file = self._build_output_file(domain)
        candidates = self._load_candidates(domain)
        input_file = self._write_input_file(domain, candidates)
        cmd = self.build_command(domain, {"input_file": input_file, "output_file": output_file})

        try:
            if not self._execute(cmd, domain):
                return []
            return self._read_results(output_file)
        finally:
            if os.path.exists(input_file):
                os.remove(input_file)
