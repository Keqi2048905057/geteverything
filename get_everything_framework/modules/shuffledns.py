"""ShuffleDNS Runner — 混合模式 (字典爆破 + 已有子域名验证 + 泛解析过滤)

工作流:
    1. 字典爆破: 读字典, 拼出 <word>.<domain> 候选, dnsx 解析
    2. 已有验证: 从数据库读"发现类工具"已收集的子域名, dnsx 解析
    3. 合并去重: 爆破结果 + 已有结果 一起处理
    4. 泛解析过滤: 检测 wildcard IP, 剔除假阳性
    5. 写入输出文件 + 落库

依赖: dnsx (ProjectDiscovery)
不依赖: massdns, shuffledns 二进制
"""

import json
import os
import random
import string
import subprocess
import tempfile

from config import SHUFFLEDNS_CONFIG
from storage import ScanResultStore

from .base import BaseRunner


class ShufflednsRunner(BaseRunner):
    """
    Shuffledns 混合模式 Runner。

    混合模式 = 字典爆破 ∪ 已有子域名验证
    - 字典爆破可独立运行 (即使数据库没数据)
    - 已有子域名验证依赖数据库里有 amass/subfinder 等发现类工具的结果
    - 两者结果合并去重后, 再做泛解析过滤
    """

    # ── 类常量 ─────────────────────────────────────────
    # 只有这些工具的子域名会被当作"已有候选"
    _DISCOVERY_TOOLS = {
        "amass", "amass_intel",
        "subfinder", "assetfinder",
        "oneforall", "enscan",
    }

    # wildcard IP 缓存: {domain: {ip1, ip2, ...}}
    _WILDCARD_CACHE = {}

    def __init__(self):
        super().__init__(SHUFFLEDNS_CONFIG, "shuffledns")
        self.store = ScanResultStore()

    # ── 已有候选加载 ─────────────────────────────────────
    def _load_existing_candidates(self, domain):
        """从数据库加载指定域名下, 所有发现类工具收集到的子域名"""
        rows = self.store.get_results_by_domain(domain)
        return list(dict.fromkeys(
            s for s, tool, _ in rows if tool in self._DISCOVERY_TOOLS
        ))

    # ── 字典爆破 ───────────────────────────────────────
    def _bruteforce_with_dnsx(self, wordlist, domain):
        """读字典 → 拼出 <word>.<domain> 候选 → dnsx 解析"""
        if not os.path.exists(wordlist):
            print(f"[!] 字典文件不存在: {wordlist}")
            return []

        # 读字典, 拼成完整子域
        candidates = []
        with open(wordlist, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                word = line.strip()
                if word and not word.startswith("#"):
                    candidates.append(f"{word}.{domain}")

        if not candidates:
            return []

        # 写临时文件
        words_file = os.path.join(
            self.output_dir,
            f"{self._hash(f'{domain}_brute')}_brute_words.txt",
        )
        with open(words_file, "w", encoding="utf-8") as f:
            f.write("\n".join(candidates))

        try:
            # dnsx 批量解析, -resp-only 只输出有响应的
            r = subprocess.run(
                ["dnsx", "-l", words_file, "-silent", "-resp-only"],
                capture_output=True, text=True, timeout=300,
            )
        finally:
            if os.path.exists(words_file):
                os.unlink(words_file)

        return [line.strip() for line in r.stdout.splitlines() if line.strip()]

    # ── 已有候选验证 ───────────────────────────────────
    def _resolve_dnsx(self, candidates):
        """用 dnsx 批量解析候选列表, 返回 {subdomain: [ips]}"""
        f = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8",
            suffix=".txt", dir=self.output_dir, delete=False,
        )
        try:
            f.write("\n".join(candidates))
            f.close()
            r = subprocess.run(
                ["dnsx", "-l", f.name, "-silent", "-json"],
                capture_output=True, text=True, timeout=120,
            )
        finally:
            os.unlink(f.name)

        resolved = {}
        for line in r.stdout.splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            host = rec.get("host", "")
            ips = rec.get("a", [])
            if host and ips:
                resolved[host] = ips
        return resolved

    # ── 泛解析 IP 检测 ──────────────────────────────────
    def _detect_wildcard_ips(self, domain):
        """用 3 个随机子域探测目标域的泛解析 IP 集合"""
        if domain in self._WILDCARD_CACHE:
            return self._WILDCARD_CACHE[domain]

        # 生成 3 个 12 位随机子域
        probes = [
            f"{''.join(random.choices(string.ascii_lowercase + string.digits, k=12))}.{domain}"
            for _ in range(3)
        ]

        f = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8",
            suffix=".txt", dir=self.output_dir, delete=False,
        )
        try:
            f.write("\n".join(probes))
            f.close()
            r = subprocess.run(
                ["dnsx", "-l", f.name, "-silent", "-json"],
                capture_output=True, text=True, timeout=30,
            )
        finally:
            os.unlink(f.name)

        wips = set()
        for line in r.stdout.splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            for ip in rec.get("a", []):
                wips.add(ip)

        self._WILDCARD_CACHE[domain] = wips
        return wips

    @staticmethod
    def _hash(value):
        """短 hash, 用于临时文件名"""
        import hashlib
        return hashlib.md5(value.encode("utf-8")).hexdigest()[:12]

    # ── 主流程 ───────────────────────────────────────
    def run_scan(self, domain):
        """执行 shuffledns 混合模式扫描"""
        # 1. 字典爆破
        wordlist = self.config.get("wordlist")
        brute_results = []
        if wordlist:
            brute_results = self._bruteforce_with_dnsx(wordlist, domain)
            print(f"[*] 字典爆破命中: {len(brute_results)} 个")

        # 2. 加载已有候选
        existing = self._load_existing_candidates(domain)
        if existing:
            print(f"[*] 数据库已有子域: {len(existing)} 个")

        if not brute_results and not existing:
            return []

        # 3. 解析已有候选 (拿 IP, 用来判断泛解析)
        resolved = self._resolve_dnsx(existing) if existing else {}

        # 4. 把爆破结果合并进来 (无 IP 信息的占位)
        for sub in brute_results:
            resolved.setdefault(sub, ["0.0.0.0"])

        if not resolved:
            return []

        # 5. 泛解析检测
        wips = self._detect_wildcard_ips(domain)
        if wips:
            print(f"[*] wildcard IP 集合: {wips}")

        # 6. 过滤: 爆破结果(占位 IP)直接保留, 已有结果的 IP 落在 wildcard 中则剔除
        valid = []
        for sub, ips in resolved.items():
            if ips == ["0.0.0.0"]:
                # 字典爆破结果, 没有 IP 信息, 视为有效
                valid.append(sub)
            elif not set(ips).issubset(wips):
                valid.append(sub)

        # 7. 写输出文件
        output_file = self._build_output_file(domain)
        with open(output_file, "w", encoding="utf-8") as f:
            f.write("\n".join(sorted(valid)))

        print(f"[*] shuffledns 混合模式: 爆破 {len(brute_results)} + 已有 {len(existing)} → 去重 {len(valid)}")
        return sorted(valid)
