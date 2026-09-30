"""
Alterx 子域名变体生成工具运行器。

Alterx 是 ProjectDiscovery 开发的子域名排列组合工具，
基于已知子域名生成可能的子域名变体（如添加前缀、数字排列等），
用于扩大子域名发现范围。
"""

import os

from config import ALTERX_CONFIG
from storage import ScanResultStore

from .base import BaseRunner


class AlterxRunner(BaseRunner):
    """
    Alterx 运行器，基于已有子域名生成变体。

    工作流程：
    1. 从 ScanResultStore 加载目标域名已有的所有子域名候选人
    2. 将候选人列表写入临时输入文件
    3. 调用 Alterx 工具生成变体并输出到文件
    4. 清理临时输入文件

    示例配置 (ALTERX_CONFIG):
        {
            "path": "alterx",
            "extra_args": [],
            "category": "subdomain"
        }
    """

    def __init__(self):
        """初始化 Alterx 运行器，加载 ALTERX_CONFIG 配置并创建存储实例。"""
        super().__init__(ALTERX_CONFIG, "alterx")
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

    def run_scan(self, domain):
        """
        执行 Alterx 子域名变体生成。

        构建命令行：alterx -l <input_file> -o <output_file> [extra_args]

        Args:
            domain: 目标域名字符串

        Returns:
            生成的子域名变体列表，扫描失败时返回空列表
        """
        candidates = self._load_candidates(domain)
        input_file = self._write_input_file(domain, candidates)
        output_file = self._build_output_file(domain)
        cmd = [
            self.config["path"],
            "-l",
            input_file,
            "-o",
            output_file,
        ]
        cmd.extend(self.config.get("extra_args", []))

        try:
            if not self._execute(cmd, domain):
                return []
            return self._read_results(output_file)
        finally:
            if os.path.exists(input_file):
                os.remove(input_file)
