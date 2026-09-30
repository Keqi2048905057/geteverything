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
    def __init__(self):
        super().__init__(GOSPIDER_CONFIG, "gospider")

    def run_scan(self, domain):
        output_file = self._build_output_file(domain)
        cmd = [
            self.config["path"],
            "-s",
            build_url(domain),
            "-d",
            str(self.config.get("depth", 2)),
        ]
        cmd.extend(self.config.get("extra_args", []))

        if not self._execute_stdout(cmd, domain, output_file):
            return []

        return self._read_results(output_file)


class KatanaRunner(BaseRunner):
    def __init__(self):
        super().__init__(KATANA_CONFIG, "katana")

    def run_scan(self, domain):
        output_file = self._build_output_file(domain)
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

        if not self._execute(cmd, domain):
            return []

        return self._read_results(output_file)


class WaybackurlsRunner(BaseRunner):
    def __init__(self):
        super().__init__(WAYBACKURLS_CONFIG, "waybackurls")

    def run_scan(self, domain):
        output_file = self._build_output_file(domain)
        cmd = [self.config["path"], domain]
        cmd.extend(self.config.get("extra_args", []))

        if not self._execute_stdout(cmd, domain, output_file):
            return []

        return self._read_results(output_file)


class FeroxbusterRunner(BaseRunner):
    def __init__(self):
        super().__init__(FEROXBUSTER_CONFIG, "feroxbuster")

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

        if not self._execute(cmd, domain):
            return []

        raw = self._read_results(output_file)
        if self.config.get("json_output"):
            return self._parse_ferox_json(raw)
        return raw


class DirsearchRunner(BaseRunner):
    def __init__(self):
        super().__init__(DIRSEARCH_CONFIG, "dirsearch")

    def run_scan(self, domain):
        output_file = self._build_output_file(domain)
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

        if not self._execute(cmd, domain):
            return []

        return self._read_results(output_file)
