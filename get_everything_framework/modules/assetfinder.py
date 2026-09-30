"""
Assetfinder 子域名发现工具运行器。

Assetfinder 是 Tomnomnom 开发的子域名查找工具，通过查询 Censys、
CertSpotter、HackerTarget 等公开数据源来发现与目标域名关联的子域名。
"""

import re

from config import ASSETFINDER_CONFIG

from .base import BaseRunner


class AssetfinderRunner(BaseRunner):
    """
    Assetfinder 运行器，通过公开数据源查找子域名。

    Assetfinder 将结果输出到 stdout（而非文件），因此使用 _execute_stdout
    方法捕获输出。运行后会通过 _normalize_results 对原始输出进行正则提取
    和去重处理，确保只返回符合域名格式的有效子域名。

    示例配置 (ASSETFINDER_CONFIG):
        {
            "path": "assetfinder",
            "subs_only": True,
            "extra_args": [],
            "category": "subdomain"
        }
    """

    def __init__(self):
        """初始化 Assetfinder 运行器，加载 ASSETFINDER_CONFIG 配置。"""
        super().__init__(ASSETFINDER_CONFIG, "assetfinder")

    def _normalize_results(self, results, domain):
        """
        对 Assetfinder 原始输出进行规范化处理。

        使用正则表达式从每行输出中提取属于目标域名的子域名，
        并进行小写转换和去重。Assetfinder 的原始输出可能包含
        域名、IP 等混合内容，此方法只提取匹配目标域名字段的条目。

        Args:
            results: Assetfinder 原始输出的行列表
            domain: 目标域名（用于正则匹配）

        Returns:
            规范化后的去重子域名字符串列表
        """
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_-])([A-Za-z0-9._-]+\.{re.escape(domain)})\b"
        )
        normalized = []
        seen = set()
        for line in results:
            for match in pattern.findall(line):
                value = match.lower().rstrip(".")
                if value in seen:
                    continue
                normalized.append(value)
                seen.add(value)
        return normalized

    def run_scan(self, domain):
        """
        执行 Assetfinder 子域名扫描。

        构建命令行：assetfinder [--subs-only] <domain> [extra_args]
        输出通过 stdout 捕获并写入文件，之后进行规范化处理。

        Args:
            domain: 目标域名字符串

        Returns:
            规范化后的去重子域名列表，扫描失败时返回空列表
        """
        output_file = self._build_output_file(domain)
        cmd = [self.config["path"]]
        if self.config.get("subs_only", True):
            cmd.append("--subs-only")
        cmd.append(domain)
        cmd.extend(self.config.get("extra_args", []))

        if not self._execute_stdout(cmd, domain, output_file):
            return []

        return self._normalize_results(self._read_results(output_file), domain)
