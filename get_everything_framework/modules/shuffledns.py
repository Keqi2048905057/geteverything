"""ShuffleDNS Runner — 混合模式 (字典爆破 + 已有子域名验证 + 泛解析过滤)

工作流:
    1. 字典爆破: 读字典, 拼出 <word>.<domain> 候选, dnsx 解析
    2. 已有验证: 从数据库读"发现类工具"已收集的子域名, dnsx 解析
    3. 合并去重: 爆破结果 + 已有结果 一起处理
    4. 泛解析过滤: 检测 wildcard IP, 剔除假阳性
    5. 写入输出文件 + 落库

依赖: dnsx (ProjectDiscovery)
不依赖: massdns, shuffledns 二进制

M4 起本 runner 也接入统一接口：真正执行的是 dnsx，因此
``build_command`` / ``parse_output`` 描述的是「用 dnsx 解析一批候选」
这一原子动作，``run_scan`` 仍是编排它的混合流程。与其它 runner 的区别是
这里**没有** ``-o`` 输出文件（结果只走 stdout），落盘由本 runner 自己完成。
"""

import json
import os
import random
import string
import subprocess
import tempfile

from config import SHUFFLEDNS_CONFIG
from core.errors import ErrorCode
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
    _WILDCARD_CACHE: dict = {}

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
    def _run_dnsx(self, input_file, *, json_mode=True, resp_only=False, timeout=None):
        """执行一次 dnsx 并返回 ``(returncode, stdout, stderr)``。

        统一走 :meth:`BaseRunner._run_subprocess`：它带真正的超时与进程树
        清理。历史实现用裸 ``subprocess.run(timeout=...)``，Windows 上超时
        只杀掉包装层，孤儿 dnsx 攥着管道会让整条调用永久卡住。

        失败一律降级成 ``(None, "", "")``，由调用方当成「本轮 dnsx 没结果」，
        不让一次 dnsx 故障把整个混合流程带崩。
        """
        cmd = self.build_command(
            None,
            {"input_file": input_file, "json": json_mode, "resp_only": resp_only},
        )
        try:
            return self._run_subprocess(self._resolve_command(cmd), timeout or self._timeout_seconds())
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            print(f"[!] dnsx 未完成: {exc}")
            return None, "", ""

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
            returncode, stdout, stderr = self._run_dnsx(words_file, json_mode=False, resp_only=True)
            if returncode != 0:
                return []
            values, _error = self.parse_output(stdout, stderr, None)
            return [v if isinstance(v, str) else v.get("value", "") for v in values if v]
        finally:
            if os.path.exists(words_file):
                os.unlink(words_file)

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
            returncode, stdout, stderr = self._run_dnsx(f.name, json_mode=True)
        finally:
            os.unlink(f.name)

        if returncode != 0:
            return {}

        values, _error = self.parse_output(stdout, stderr, None)
        resolved = {}
        for item in values:
            if isinstance(item, dict):
                host = item.get("value", "")
                ips = item.get("ips") or []
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
            # 泛解析探测是辅助步骤，给一个更短的上限，别为它多等两分钟。
            returncode, stdout, stderr = self._run_dnsx(f.name, json_mode=True, timeout=30)
        finally:
            os.unlink(f.name)

        wips = set()
        if returncode == 0:
            values, _error = self.parse_output(stdout, stderr, None)
            for item in values:
                if isinstance(item, dict):
                    wips.update(item.get("ips") or [])

        self._WILDCARD_CACHE[domain] = wips
        return wips

    @staticmethod
    def _hash(value):
        """短 hash, 用于临时文件名"""
        import hashlib
        return hashlib.md5(value.encode("utf-8")).hexdigest()[:12]

    # ── 主流程 ───────────────────────────────────────
    def build_command(self, domain, options=None):
        """构建「用 dnsx 解析候选列表」的命令行。

        ShuffleDNS 自身没有二进制（本实现用 dnsx 代替 massdns），因此这里
        构建的是 dnsx 命令行：``dnsx -l <input_file> -silent -json``。

        Args:
            domain: 目标域名（仅用于日志与默认输入文件命名）。
            options: 必须提供 ``input_file``（候选列表）；``json`` 控制是否
                要 ``-json`` 逐行输出（泛解析检测与 IP 解析都需要）。

        Returns:
            命令行的参数列表。

        Raises:
            KeyError: 未提供 ``input_file``。
        """
        options = options or {}
        input_file = options["input_file"]
        cmd = ["dnsx", "-l", input_file]
        if self.config.get("silent", True):
            cmd.append("-silent")
        if options.get("json", True):
            cmd.append("-json")
        if options.get("resp_only"):
            cmd.append("-resp-only")
        cmd.extend(self.config.get("extra_args", []))
        return cmd

    def parse_output(self, stdout, stderr, artifacts=None):
        """解析 dnsx 的输出。

        两种形态都支持：

        * ``-json``：每行一个 JSON 对象，取出 ``host`` 与 ``a``（IP 列表），
          返回 ``list[dict]``，每个元素形如 ``{"value": host, "ips": [...]}``；
        * ``-resp-only`` 或纯文本：按行切成字符串列表。

        Args:
            stdout: 子进程标准输出。
            stderr: 子进程标准错误（未使用）。
            artifacts: 可选 ``output_file``（本 runner 一般不落盘，
                提供它是为了与其它 runner 的签名保持一致）。

        Returns:
            tuple[list, str | None]: ``(解析结果, 解析错误码)``。
        """
        artifacts = artifacts or {}
        output_file = artifacts.get("output_file")
        text = ""
        if output_file and os.path.exists(output_file):
            with open(output_file, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        if not text:
            text = stdout or ""

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return [], None

        # 判断是否是 -json 形态：只要首行能解析成带 host 的 JSON 就走 JSON 分支。
        records = []
        malformed = 0
        json_like = 0
        for line in lines:
            if not line.startswith("{"):
                continue
            json_like += 1
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(raw, dict):
                malformed += 1
                continue
            host = raw.get("host") or raw.get("input") or ""
            ips = raw.get("a") or raw.get("aaaa") or []
            if isinstance(ips, str):
                ips = [ips]
            if host:
                records.append({"value": host, "ips": list(ips)})

        if records:
            return records, None
        if json_like and malformed == json_like:
            # 看着像 JSON 却一行都没解析成功：这是解析失败，不是零结果。
            return [], ErrorCode.PARSE_ERROR
        return lines, None

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
