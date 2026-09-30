"""
Httpx HTTP 探测工具运行器。

Httpx 是 ProjectDiscovery 开发的高性能 HTTP 探针工具，
支持同时对大量子域名进行 HTTP/HTTPS 请求探测，
获取响应状态码、页面标题、Web 服务器类型、CDN 信息、
技术栈检测等丰富的 HTTP 响应元数据。

M4 起（方案 M4 交付项）这些字段不再被丢掉：``run()`` 返回的
``Observation.data`` 里保留 ``url`` / ``status_code`` / ``title`` /
``webserver`` / ``tech`` / ``cdn``，供资产页直接展示。旧调用方
``run_scan`` 仍然只返回 URL 字符串列表。
"""

import json
import os
import tempfile
from typing import Any, Dict, List, Optional

from config import HTTPX_CONFIG, OUTPUT_DIR
from core.errors import ErrorCode
from core.runner_result import Observation, RunnerInputError
from storage import ScanResultStore

from .base import BaseRunner


class HttpxRunner(BaseRunner):
    """
    Httpx 运行器，对子域名进行 HTTP/HTTPS 存活探测和指纹识别。

    工作流程：
    1. 从 ScanResultStore 或外部传入加载子域名候选列表
    2. 将候选人列表写入临时输入文件
    3. 调用 Httpx 工具进行 HTTP 探测，输出 JSON 格式结果
    4. 解析 JSON 结果，提取 URL、状态码、标题、Web 服务器、技术栈等字段
    5. 清理临时输入文件

    Httpx 输出的是 JSONL 格式（每行一个 JSON 对象），支持以下探测功能：
    - 标题提取 (-title)
    - 状态码记录 (-status-code)
    - Web 服务器识别 (-web-server)
    - CDN 检测 (-cdn)
    - 技术栈检测 (-tech-detect)

    示例配置 (HTTPX_CONFIG):
        {
            "path": "httpx",
            "threads": 50,
            "timeout": 10,
            "silent": True,
            "tech_detect": False,
            "follow_redirects": False,
            "extra_args": [],
            "category": "http"
        }
    """

    def __init__(self):
        """初始化 Httpx 运行器，加载 HTTPX_CONFIG 配置并创建存储实例。"""
        super().__init__(HTTPX_CONFIG, "httpx")
        self.store = ScanResultStore()
        # 上一次探测的完整字段（含状态码/标题/技术栈），由 run_scan 填入。
        self.last_items: List[Dict[str, Any]] = []

    def _load_candidates(self, domain: str) -> List[str]:
        """
        从 ScanResultStore 加载已有子域名候选列表。

        Args:
            domain: 目标域名字符串

        Returns:
            去重后的子域名候选列表
        """
        rows = self.store.get_results_by_domain(domain)
        return list(dict.fromkeys(subdomain for subdomain, _, _ in rows))

    def _write_input_file(self, domain: str, candidates: List[str]) -> str:
        """
        创建 Httpx 临时输入文件。

        将子域名候选列表写入临时文件，供 Httpx 通过 -l 参数批量读取。

        Args:
            domain: 目标域名（用于文件名标识）
            candidates: 子域名候选字符串列表

        Returns:
            临时文件的完整路径
        """
        temp_file = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=f"_{domain}_httpx_input.txt",
            dir=OUTPUT_DIR,
            delete=False,
        )
        try:
            temp_file.write("\n".join(candidates))
            temp_file.write("\n")
        finally:
            temp_file.close()
        return temp_file.name

    def _build_json_output_file(self, domain: str) -> str:
        """
        构建 JSON 格式输出文件路径。

        Httpx 使用 -json 参数输出 JSONL 格式，与文本格式使用不同的输出文件。

        Args:
            domain: 目标域名字符串

        Returns:
            JSON 输出文件的完整路径
        """
        return os.path.join(self.output_dir, f"{domain}_{self.tool_name}.jsonl")

    def _read_json_results(self, output_file: str) -> List[Dict[str, Any]]:
        """
        从 JSONL 文件中解析 Httpx 扫描结果。

        每行是一个 JSON 对象，包含 HTTP 响应的元数据。
        提取以下字段：
        - url: 完整的请求 URL
        - status_code: HTTP 状态码
        - title: 页面标题
        - webserver: Web 服务器类型
        - tech: 检测到的技术栈列表
        - cdn: CDN 信息
        - input: 原始输入值

        Args:
            output_file: JSONL 格式的输出文件路径

        Returns:
            解析后的 HTTP 探测结果字典列表
        """
        if not os.path.exists(output_file):
            return []

        items: List[Dict[str, Any]] = []
        with open(output_file, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(raw, dict):
                    continue
                items.append(
                    {
                        "url": raw.get("url"),
                        "status_code": raw.get("status_code"),
                        "title": raw.get("title"),
                        "webserver": raw.get("webserver") or raw.get("web_server"),
                        "tech": raw.get("tech") or raw.get("technologies") or [],
                        "cdn": raw.get("cdn") or raw.get("cdn_name"),
                        "input": raw.get("input"),
                        "content_length": raw.get("content_length"),
                        "host": raw.get("host"),
                    }
                )
        return items

    def build_command(self, domain: str, options: Optional[Dict[str, Any]] = None) -> List[str]:
        """
        构建 Httpx 命令行。

        命令行：``httpx -l <input_file> -o <output_file> -json -threads <N>``
        始终启用 ``-title``、``-status-code``、``-web-server``、``-cdn``。

        Args:
            domain: 目标域名字符串。
            options: 支持 ``input_file`` / ``output_file`` / ``tech_detect``。

        Returns:
            命令行的参数列表。
        """
        options = options or {}
        input_file = options["input_file"]
        output_file = options["output_file"]
        tech_detect = bool(options.get("tech_detect", False))

        cmd = [
            self.config["path"],
            "-l",
            input_file,
            "-o",
            output_file,
            "-json",
            "-threads",
            str(self.config["threads"]),
        ]

        timeout = self.config.get("timeout")
        if timeout:
            cmd.extend(["-timeout", str(timeout)])

        if self.config.get("silent", False):
            cmd.append("-silent")

        cmd.extend(["-title", "-status-code", "-web-server", "-cdn"])

        if tech_detect or self.config.get("tech_detect", False):
            cmd.append("-tech-detect")

        if self.config.get("follow_redirects", False):
            cmd.append("-follow-redirects")

        cmd.extend(self.config.get("extra_args", []))

        return cmd

    def parse_output(self, stdout, stderr, artifacts=None):
        """
        解析 Httpx 的 JSONL 输出。

        Args:
            stdout: 子进程标准输出（httpx -silent 时可能为空）。
            stderr: 子进程标准错误（未使用）。
            artifacts: 支持 ``output_file`` 指向 JSONL 文件。

        Returns:
            tuple[list[dict], str | None]: ``(探测结果列表, 解析错误码)``。
        """
        artifacts = artifacts or {}
        output_file = artifacts.get("output_file")
        items = self._read_json_results(output_file) if output_file else []
        if items:
            return items, None
        # 文件不存在或全为空：尝试直接解析 stdout。
        fallback: List[Dict[str, Any]] = []
        for line in (stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict) and raw.get("url"):
                fallback.append(
                    {
                        "url": raw.get("url"),
                        "status_code": raw.get("status_code"),
                        "title": raw.get("title"),
                        "webserver": raw.get("webserver") or raw.get("web_server"),
                        "tech": raw.get("tech") or raw.get("technologies") or [],
                        "cdn": raw.get("cdn"),
                        "input": raw.get("input"),
                    }
                )
        return fallback, None

    def observations(self, values) -> List[Observation]:
        """
        把探测结果转成观测：值取 URL，附加字段进 ``data``。

        M4 交付项要求 httpx 保留 URL / 状态码 / 标题 / Web Server /
        技术栈 / CDN，这些都在 ``data`` 里。

        Args:
            values: ``run_scan`` 返回的 URL 列表，或探测结果字典列表。

        Returns:
            观测列表。
        """
        by_url = {str(item.get("url")): item for item in self.last_items if item.get("url")}
        items: List[Observation] = []
        for value in values or []:
            if isinstance(value, dict):
                url = value.get("url") or value.get("value")
                if not url:
                    continue
                detail = {k: v for k, v in value.items() if k not in ("url", "value", "category")}
            else:
                url = str(value)
                detail = dict(by_url.get(url, {}))
                detail.pop("url", None)
            items.append(
                Observation(
                    category="web",
                    value=str(url),
                    data={k: v for k, v in detail.items() if v is not None},
                    source_tool=self.tool_name,
                )
            )
        return items

    def run_scan(self, domain: str, candidates: Optional[List[str]] = None, tech_detect: bool = False) -> List[str]:
        """
        执行 Httpx HTTP 存活探测。

        Args:
            domain: 目标域名字符串
            candidates: 可选的子域名候选列表，不传则从 ScanResultStore 加载
            tech_detect: 是否启用技术栈检测（-tech-detect）

        Returns:
            HTTP 探测结果的 URL 列表（旧签名；完整字段见 ``self.last_items``）。

        Raises:
            RunnerInputError: 没有任何可供探测的目标（拿不到候选列表）。
            RuntimeError: 工具执行失败（含超时/未安装）。
        """
        targets = list(dict.fromkeys(candidates or self._load_candidates(domain)))
        if not targets:
            # 没有候选目标属于「前置数据缺失」，不是工具故障。
            raise RunnerInputError(
                ErrorCode.NO_RESULTS,
                f"没有可供 httpx 探测的目标: {domain}（请先跑子域发现工具）",
                status="success",
            )

        output_file = self._build_json_output_file(domain)
        input_file = self._write_input_file(domain, targets)
        cmd = self.build_command(
            domain,
            {"input_file": input_file, "output_file": output_file, "tech_detect": tech_detect},
        )

        try:
            if not self._execute(cmd, domain):
                # 保留 _execute 记录的原始失败原因，交给 run() 翻译成 error_code。
                raise RuntimeError("httpx 扫描失败，请检查 httpx 可执行文件和当前 PATH。")
            raw = self._read_json_results(output_file)
            self.last_items = raw
            return [r.get("url") for r in raw if r.get("url")]
        finally:
            if os.path.exists(input_file):
                os.remove(input_file)
