from config import (
    DIRSEARCH_CONFIG,
    FEROXBUSTER_CONFIG,
    GOSPIDER_CONFIG,
    KATANA_CONFIG,
    WAYBACKURLS_CONFIG,
)

from .base import BaseRunner


def build_url(domain):
    if domain.startswith(("http://", "https://")):
        return domain
    return f"https://{domain}"


class GospiderRunner(BaseRunner):
    """Gospider 爬虫。

    结果走 stdout（无 ``-o``），因此 ``build_command`` 不带输出文件参数，
    由 :meth:`BaseRunner._execute_stdout` 重定向落盘。
    """

    def __init__(self):
        super().__init__(GOSPIDER_CONFIG, "gospider")

    def build_command(self, domain, options=None):
        options = options or {}
        cmd = [
            self.config["path"],
            "-s",
            build_url(domain),
            "-d",
            str(self.config.get("depth", 2)),
        ]
        cmd.extend(self.config.get("extra_args", []))
        return cmd

    def parse_output(self, stdout, stderr, artifacts=None):
        """gospider 结果在 stdout；有落盘文件时优先读文件。"""
        artifacts = artifacts or {}
        output_file = artifacts.get("output_file")
        if output_file:
            values = self._read_results(output_file)
            if values:
                return values, None
        return [line.strip() for line in (stdout or "").splitlines() if line.strip()], None

    def run_scan(self, domain):
        output_file = self._build_output_file(domain)
        cmd = self.build_command(domain)

        if not self._execute_stdout(cmd, domain, output_file):
            return []

        return self._read_results(output_file)


class KatanaRunner(BaseRunner):
    """Katana 爬虫。通过 ``-o`` 写结果文件。"""

    def __init__(self):
        super().__init__(KATANA_CONFIG, "katana")

    def build_command(self, domain, options=None):
        options = options or {}
        output_file = options.get("output_file") or self._build_output_file(domain)
        cmd = [
            self.config["path"],
            "-u",
            build_url(domain),
            "-d",
            str(self.config.get("depth", 2)),
            "-o",
            output_file,
        ]
        cmd.extend(self.config.get("extra_args", []))
        return cmd

    def parse_output(self, stdout, stderr, artifacts=None):
        artifacts = artifacts or {}
        output_file = artifacts.get("output_file")
        if output_file:
            values = self._read_results(output_file)
            if values:
                return values, None
        return [line.strip() for line in (stdout or "").splitlines() if line.strip()], None

    def run_scan(self, domain):
        output_file = self._build_output_file(domain)
        cmd = self.build_command(domain, {"output_file": output_file})

        if not self._execute(cmd, domain):
            return []

        return self._read_results(output_file)


class WaybackurlsRunner(BaseRunner):
    """waybackurls 历史 URL 收集。结果走 stdout。"""

    def __init__(self):
        super().__init__(WAYBACKURLS_CONFIG, "waybackurls")

    def build_command(self, domain, options=None):
        cmd = [self.config["path"], domain]
        cmd.extend(self.config.get("extra_args", []))
        return cmd

    def parse_output(self, stdout, stderr, artifacts=None):
        artifacts = artifacts or {}
        output_file = artifacts.get("output_file")
        if output_file:
            values = self._read_results(output_file)
            if values:
                return values, None
        return [line.strip() for line in (stdout or "").splitlines() if line.strip()], None

    def run_scan(self, domain):
        output_file = self._build_output_file(domain)
        cmd = self.build_command(domain)

        if not self._execute_stdout(cmd, domain, output_file):
            return []

        return self._read_results(output_file)


class FeroxbusterRunner(BaseRunner):
    """feroxbuster 目录爆破。``--json`` 时输出 JSONL，只取 url 字段。"""

    def __init__(self):
        super().__init__(FEROXBUSTER_CONFIG, "feroxbuster")

    def build_command(self, domain, options=None):
        options = options or {}
        output_file = options.get("output_file") or self._build_output_file(domain)
        cmd = [
            self.config["path"],
            "-u",
            build_url(domain),
            "-o",
            output_file,
        ]
        if self.config.get("json_output"):
            cmd.append("--json")
        wordlist = self.config.get("wordlist")
        if wordlist:
            cmd.extend(["-w", wordlist])
        cmd.extend(self.config.get("extra_args", []))
        return cmd

    def parse_output(self, stdout, stderr, artifacts=None):
        artifacts = artifacts or {}
        output_file = artifacts.get("output_file")
        raw = self._read_results(output_file) if output_file else []
        if not raw:
            raw = [line.strip() for line in (stdout or "").splitlines() if line.strip()]
        if self.config.get("json_output"):
            return self._parse_ferox_json(raw), None
        return raw, None

    def _parse_ferox_json(self, lines):
        """feroxbuster --json 每行一个 JSON 对象, 提取 url 字段"""
        import json

        urls = []
        for line in lines:
            try:
                obj = json.loads(line)
                if u := obj.get("url"):
                    urls.append(u.strip())
            except (json.JSONDecodeError, ValueError):
                continue
        return list(dict.fromkeys(urls))

    def run_scan(self, domain):
        output_file = self._build_output_file(domain)
        cmd = self.build_command(domain, {"output_file": output_file})

        if not self._execute(cmd, domain):
            return []

        values, _ = self.parse_output("", "", {"output_file": output_file})
        return values


class DirsearchRunner(BaseRunner):
    """dirsearch 目录扫描。通过 ``-o`` 写结果文件。"""

    def __init__(self):
        super().__init__(DIRSEARCH_CONFIG, "dirsearch")

    def build_command(self, domain, options=None):
        options = options or {}
        output_file = options.get("output_file") or self._build_output_file(domain)
        cmd = [
            self.config["path"],
            "-u",
            build_url(domain),
            "-o",
            output_file,
        ]
        wordlist = self.config.get("wordlist")
        if wordlist:
            cmd.extend(["-w", wordlist])
        cmd.extend(self.config.get("extra_args", []))
        return cmd

    def parse_output(self, stdout, stderr, artifacts=None):
        artifacts = artifacts or {}
        output_file = artifacts.get("output_file")
        if output_file:
            values = self._read_results(output_file)
            if values:
                return values, None
        return [line.strip() for line in (stdout or "").splitlines() if line.strip()], None

    def run_scan(self, domain):
        output_file = self._build_output_file(domain)
        cmd = self.build_command(domain, {"output_file": output_file})

        if not self._execute(cmd, domain):
            return []

        return self._read_results(output_file)
