import re

from config import AMASS_CONFIG, AMASS_INTEL_CONFIG

from .base import BaseRunner


class AmassRunner(BaseRunner):
    def __init__(self):
        super().__init__(AMASS_CONFIG, "amass")

    def _normalize_results(self, results, domain):
        normalized_results = []
        seen = set()
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_-])([A-Za-z0-9._-]+\.{re.escape(domain)})\b"
        )

        for line in results:
            for match in pattern.findall(line):
                value = match.lower().rstrip(".")
                if value in seen:
                    continue
                normalized_results.append(value)
                seen.add(value)

        return normalized_results

    def build_command(self, domain, options=None):
        """构建 amass enum 命令行（``-o`` 写文件，供证据采集复用）。"""
        options = options or {}
        output_file = options.get("output_file") or self._build_output_file(domain)
        cmd = [
            self.config["path"],
            "enum",
            "-d",
            domain,
            "-o",
            output_file,
        ]

        if self.config.get("passive", False):
            cmd.append("-passive")

        if self.config.get("brute", False):
            cmd.append("-brute")

        timeout = self.config.get("timeout")
        if timeout:
            cmd.extend(["-timeout", str(timeout)])

        if self.config.get("silent", False):
            cmd.append("-silent")

        cmd.extend(self.config.get("extra_args", []))

        return cmd

    def parse_output(self, stdout, stderr, artifacts=None):
        """从输出文件（或 stdout）提取属于目标域名的子域名。

        amass 的 stdout 混杂大量进度信息，因此优先读 ``-o`` 文件；
        只有在没有文件时才退回 stdout 并交给 ``_normalize_results`` 过滤。
        """
        artifacts = artifacts or {}
        output_file = artifacts.get("output_file")
        domain = artifacts.get("domain")
        if output_file:
            raw = self._read_results(output_file)
        else:
            raw = [line.strip() for line in (stdout or "").splitlines() if line.strip()]
        if domain:
            return self._normalize_results(raw, domain), None
        return raw, None

    def run_scan(self, domain):
        output_file = self._build_output_file(domain)
        cmd = self.build_command(domain, {"output_file": output_file})

        if not self._execute(cmd, domain):
            return []

        return self._normalize_results(self._read_results(output_file), domain)


class AmassIntelRunner(BaseRunner):
    """
    amass intel -asn 模式

    通过 ASN 号反向发现关联域名，输入不限格式：
        AS15169  /  15169  /  ASN15169  均可
    """

    def __init__(self):
        super().__init__(AMASS_INTEL_CONFIG, "amass_intel")

    def _parse_asn(self, identify):
        raw = str(identify).strip().upper()
        raw = raw.removeprefix("ASN").removeprefix("AS").strip()
        if not raw.isdigit():
            raise ValueError(f"无效的 ASN 输入: {identify}")
        return raw

    def _normalize_domain_list(self, results):
        normalized = []
        seen = set()
        for line in results:
            value = line.strip().lower().rstrip(".")
            if not value or value in seen:
                continue
            # amass intel 输出已经是规范域名列表
            if "." in value:
                normalized.append(value)
                seen.add(value)
        return normalized

    def build_command(self, identify, options=None):
        """构建 amass intel -asn 命令行。

        Args:
            identify: ASN 号（``AS15169`` / ``15169``）。
            options: 支持 ``output_file``。

        Raises:
            ValueError: ASN 输入非法。
        """
        options = options or {}
        asn = self._parse_asn(identify)
        output_file = options.get("output_file") or self._build_output_file(f"AS{asn}")
        cmd = [
            self.config["path"],
            "intel",
            "-asn",
            asn,
            "-o",
            output_file,
        ]

        timeout = self.config.get("timeout")
        if timeout:
            cmd.extend(["-timeout", str(timeout)])

        if self.config.get("silent", False):
            cmd.append("-silent")

        cmd.extend(self.config.get("extra_args", []))

        return cmd

    def parse_output(self, stdout, stderr, artifacts=None):
        """从输出文件（或 stdout）提取关联域名列表。"""
        artifacts = artifacts or {}
        output_file = artifacts.get("output_file")
        if output_file:
            raw = self._read_results(output_file)
        else:
            raw = [line for line in (stdout or "").splitlines() if line.strip()]
        return self._normalize_domain_list(raw), None

    def run_scan(self, identify):
        """
        identify: ASN 号，如 "AS15169" / "15169"

        amass intel -asn <asn> 返回关联的域名列表
        """
        asn = self._parse_asn(identify)
        output_file = self._build_output_file(f"AS{asn}")
        cmd = self.build_command(identify, {"output_file": output_file})

        if not self._execute(cmd, identify):
            return []

        return self._normalize_domain_list(self._read_results(output_file))
