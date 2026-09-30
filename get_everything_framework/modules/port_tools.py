"""
端口扫描工具运行器模块。

包含两个端口扫描工具的 Runner 实现：
- NaabuRunner: ProjectDiscovery 开发的高性能端口扫描器
- NmapRunner: 经典的网络发现和安全审计工具

两个 Runner 均继承自 BaseRunner，通过子进程调用对应的外部工具。
"""

from config import NAABU_CONFIG, NMAP_CONFIG

from .base import BaseRunner


class NaabuRunner(BaseRunner):
    """
    Naabu 端口扫描运行器。

    Naabu 是 ProjectDiscovery 开发的快速端口扫描工具，
    支持 SYN 扫描、连接扫描等多种扫描方式，默认使用 SYN 扫描
    （需要 root 权限）。

    使用 -host 指定目标域名/IP，结果通过 -o 输出到文件。

    示例配置 (NAABU_CONFIG):
        {
            "path": "naabu",
            "silent": True,
            "extra_args": [],
            "category": "port"
        }
    """

    def __init__(self):
        """初始化 Naabu 运行器，加载 NAABU_CONFIG 配置。"""
        super().__init__(NAABU_CONFIG, "naabu")

    def run_scan(self, domain):
        """
        执行 Naabu 端口扫描。

        构建命令行：naabu -host <domain> -o <output_file> [-silent] [extra_args]

        Args:
            domain: 目标域名或 IP 地址

        Returns:
            扫描到的开放端口列表，格式为 "ip:port"，扫描失败时返回空列表
        """
        output_file = self._build_output_file(domain)
        cmd = [
            self.config["path"],
            "-host",
            domain,
            "-o",
            output_file,
        ]
        if self.config.get("silent", True):
            cmd.append("-silent")
        cmd.extend(self.config.get("extra_args", []))

        if not self._execute(cmd, domain):
            return []

        return self._read_results(output_file)


class NmapRunner(BaseRunner):
    """
    Nmap 端口扫描运行器。

    Nmap 是经典的网络发现和安全审计工具，支持丰富的扫描选项，
    包括端口发现、服务版本探测、操作系统检测等功能。
    输出使用 -oN 参数生成普通人可读的文本格式文件。

    示例配置 (NMAP_CONFIG):
        {
            "path": "nmap",
            "ports": "1-65535",
            "extra_args": [],
            "category": "port"
        }
    """

    def __init__(self):
        """初始化 Nmap 运行器，加载 NMAP_CONFIG 配置。"""
        super().__init__(NMAP_CONFIG, "nmap")

    def run_scan(self, domain):
        """
        执行 Nmap 端口扫描。

        构建命令行：nmap [-p <ports>] -oN <output_file> <domain> [extra_args]

        Args:
            domain: 目标域名或 IP 地址

        Returns:
            Nmap 扫描结果文本行列表，扫描失败时返回空列表
        """
        output_file = self._build_output_file(domain)
        cmd = [self.config["path"]]
        ports = self.config.get("ports")
        if ports:
            cmd.extend(["-p", str(ports)])
        cmd.extend(["-oN", output_file, domain])
        cmd.extend(self.config.get("extra_args", []))

        if not self._execute(cmd, domain):
            return []

        return self._read_results(output_file)
