"""enscan 工具 Runner — 企业信息收集, CLI + JSON 直出"""

import glob
import json
import os
import re
import subprocess
from urllib.parse import urlparse

from config import ENSCAN_CONFIG

from .base import BaseRunner


class ENScanRunner(BaseRunner):
    def __init__(self):
        super().__init__(ENSCAN_CONFIG, "enscan")

    def _parse_json_output(self, raw_text):
        """解析 enscan JSON, 从 icp.domain / icp.website 提取域名"""
        try:
            data = json.loads(raw_text) if isinstance(raw_text, str) else raw_text
        except (json.JSONDecodeError, TypeError):
            return []

        domains = []
        ip_re = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")

        for item in data.get("icp", []):
            if d := (item.get("domain", "") or "").strip().lower().rstrip("."):
                if "." in d and not ip_re.match(d):
                    domains.append(d)
            if w := (item.get("website", "") or "").strip():
                parsed = urlparse(w if "://" in w else f"http://{w}")
                host = (parsed.netloc or parsed.path).lower().lstrip("www.")
                if host and "." in host and not ip_re.match(host):
                    domains.append(host)

        return list(dict.fromkeys(domains))

    def run_scan(self, keyword):
        """
        keyword: 企业名称

        执行 enscan -n <keyword> -json
        """
        run_cmd = self._resolve_command([
            self.config["path"],
            "-n", keyword,
            "-json",
        ] + self.config.get("extra_args", []))

        before = set(glob.glob(os.path.join(self.output_dir, "**", "*.json"), recursive=True))
        try:
            subprocess.run(
                run_cmd,
                check=True,
                cwd=self.output_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.config.get("process_timeout"),
            )
        except subprocess.CalledProcessError as e:
            print(f"[!] enscan 退出码 {e.returncode}")
            print(f"    stdout: {(e.stdout or '')[:500]}")
            print(f"    stderr: {(e.stderr or '')[:500]}")
            return []
        except subprocess.TimeoutExpired:
            print("[!] enscan 扫描超时")
            return []
        except FileNotFoundError:
            print(f"[!] 未找到工具 {self.config['path']}")
            return []

        after = set(glob.glob(os.path.join(self.output_dir, "**", "*.json"), recursive=True))
        new_files = after - before
        if not new_files:
            return []

        latest = max(new_files, key=os.path.getmtime)
        with open(latest, "r", encoding="utf-8") as f:
            raw = f.read()

        safe_file = self._build_output_file(keyword)
        with open(safe_file, "w", encoding="utf-8") as f:
            f.write(raw)

        return self._parse_json_output(raw)
