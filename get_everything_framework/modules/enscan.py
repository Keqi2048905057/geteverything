"""enscan 工具 Runner — 企业信息收集, CLI + JSON 直出"""

import glob
import json
import os
import re
import subprocess
import time
from urllib.parse import urlparse

from config import ENSCAN_CONFIG
from core.errors import ErrorCode

from .base import BaseRunner, _decode, _exit_code_to_error

#: ENScan 的查询渠道要求「安全验证」时，打在输出里的特征串。
#:
#: 真实样本（``results/artifacts`` 里那次真实运行留下的 stderr，ANSI 颜色
#: 转义原样保留）::
#:
#:     [ERR] 【AQC】需要安全验证，请打开浏览器进行验证后操作，10秒后重试！
#:     https://aiqicha.baidu.com/
#:
#: 只认「安全验证」一个短语：它同时覆盖 ``需要安全验证`` 与
#: ``请打开浏览器进行验证`` 两种措辞。这个判断只在 enscan 自己的输出上跑，
#: 不会误伤别的工具。
_SECURITY_VERIFICATION_MARKER = "安全验证"

#: 命中特征时给使用者看的话。**它必须与「零结果」区分开**：查询没有做完，
#: 不是「目标没有资产」。前半句是结论，后半句是使用者自己能做的处置。
_AQC_BLOCKED_MESSAGE = (
    "爱企查（AQC）要求安全验证，本次查询未完成："
    "请先在浏览器完成验证，或在设置页配置有效的 AQC Cookie 后重试"
)
_SOURCE_BLOCKED_MESSAGE = "外部数据源要求安全验证，本次查询未完成：请先在浏览器完成验证后重试"

#: 消息里点名具体渠道对使用者更有用；命中这些串说明拦路的是爱企查。
_AQC_MARKERS = ("AQC", "aiqicha")


def _security_verification_notice(*streams):
    """在工具输出里找「外部数据源要求安全验证」的证据。

    为什么单独做一个函数：这段判断在三条路径上都要用（非零退出、超时、
    退出码 0），散着写三份迟早会漏掉一条。

    Args:
        *streams: 子进程的 stderr / stdout；``None`` 与空串都安全。

    Returns:
        str | None: 命中的中文说明；没命中返回 ``None``。
    """
    for text in streams:
        if text and _SECURITY_VERIFICATION_MARKER in text:
            if any(marker in text for marker in _AQC_MARKERS):
                return _AQC_BLOCKED_MESSAGE
            return _SOURCE_BLOCKED_MESSAGE
    return None


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

    def build_command(self, keyword, options=None):
        """
        构建 enscan 命令行：``enscan -n <keyword> -json``

        注意：enscan 直接在当前工作目录下产出 JSON 文件（不是通过 ``-o``
        指定路径），所以这里返回的是**未解析**的命令列表，由
        :meth:`run_scan` 交给 ``_resolve_command`` 处理。

        Args:
            keyword: 企业名称。
            options: 当前未使用的占位参数（与其他 runner 签名保持一致）。

        Returns:
            命令行的参数列表。
        """
        return [
            self.config["path"],
            "-n", keyword,
            "-json",
        ] + self.config.get("extra_args", [])

    def parse_output(self, stdout, stderr, artifacts=None):
        """
        解析 enscan 的 JSON 输出。

        Args:
            stdout: 子进程标准输出。enscan 走文件而不是 stdout，仅在
                没有落盘文件时兜底。
            stderr: 子进程标准错误（未使用）。
            artifacts: 支持 ``output_file`` 指向复制出来的 JSON 文件。

        Returns:
            tuple[list[str], str | None]: ``(域名列表, 解析错误码)``。
        """
        artifacts = artifacts or {}
        output_file = artifacts.get("output_file")
        raw = ""
        if output_file and os.path.exists(output_file):
            with open(output_file, "r", encoding="utf-8", errors="replace") as handle:
                raw = handle.read()
        if not raw:
            raw = stdout or ""
        values = self._parse_json_output(raw)
        if not values and raw.strip():
            # 有内容但一个域名都没解析出来：多半是格式变了，而不是真的零结果。
            return [], "parse_error"
        return values, None

    def run_scan(self, keyword):
        """
        keyword: 企业名称

        执行 ``enscan -n <keyword> -json``。

        enscan 与其它工具不同：结果 JSON 直接写在它的**工作目录**下，
        既没有 ``-o`` 参数也不走 stdout。因此这里：

        1. 先用 :meth:`BaseRunner._run_subprocess` 执行（它带真正的超时
           与进程树清理，避免 enscan 卡死占住 worker）；
        2. 比对执行前后目录里新增的 ``*.json``，取最新的一个；
        3. 复制到受控输出文件后再解析，保证证据采集与旧调用方都能拿到。

        Returns:
            解析出的域名列表；失败或没有新文件时返回空列表。

        Note:
            需要区分「失败」与「零结果」时请调用 :meth:`BaseRunner.run`。
        """
        run_cmd = self._resolve_command(self.build_command(keyword))
        started = time.perf_counter()
        before = set(glob.glob(os.path.join(self.output_dir, "**", "*.json"), recursive=True))
        try:
            returncode, stdout, stderr = self._run_subprocess(
                run_cmd, self._timeout_seconds(), cwd=self.output_dir
            )
        except FileNotFoundError:
            print(f"[!] 未找到工具 {self.config['path']}")
            self._record_execution(
                self.build_command(keyword),
                started=started,
                exit_code=None,
                error_code=ErrorCode.TOOL_NOT_FOUND,
                error_message=f"未找到可执行文件: {self.config.get('path')}",
                status="failed",
            )
            return []
        except subprocess.TimeoutExpired as exc:
            stdout = _decode(exc.stdout)
            stderr = _decode(exc.stderr)
            # 真实运行里最常见的就是这一条：ENScan 每 10 秒重试一次 AQC 的
            # 安全验证，永远等不到它自己退出，于是**由我们**在超时后掐掉。
            # 只报「执行超过 120 秒」会把人引向「工具太慢」，而真正的原因是
            # 数据源要求人工验证 —— 必须如实点名。
            notice = _security_verification_notice(stderr, stdout)
            if notice:
                print(f"[!] enscan 被安全验证阻断（{self._timeout_seconds()} 秒内未完成）")
                self._record_execution(
                    self.build_command(keyword),
                    started=started,
                    exit_code=None,
                    stdout=stdout,
                    stderr=stderr,
                    error_code=ErrorCode.SOURCE_BLOCKED,
                    error_message=f"{notice}（进程在 {self._timeout_seconds()} 秒后被强制终止）",
                    status="timeout",
                )
                return []
            print("[!] enscan 扫描超时")
            self._record_execution(
                self.build_command(keyword),
                started=started,
                exit_code=None,
                stdout=stdout,
                stderr=stderr,
                error_code=ErrorCode.TIMEOUT,
                error_message=f"执行超过 {self._timeout_seconds()} 秒，已强制终止",
                status="timeout",
            )
            return []
        except OSError as exc:
            print(f"[!] enscan 执行失败: {exc}")
            self._record_execution(
                self.build_command(keyword),
                started=started,
                exit_code=None,
                error_code=ErrorCode.UNKNOWN_ERROR,
                error_message=str(exc),
                status="failed",
            )
            return []

        if returncode != 0:
            print(f"[!] enscan 退出码 {returncode}")
            print(f"    stdout: {(stdout or '')[:500]}")
            print(f"    stderr: {(stderr or '')[:500]}")
            # 非零退出**未必**是「未知错误」：被安全验证拦下时 ENScan 也会
            # 以非零码收场。那时如实报「数据源受阻」比报「工具退出码 1」
            # 有用得多 —— 后者会让人去查工具安装，而问题根本不在本机。
            notice = _security_verification_notice(stderr, stdout)
            if notice:
                self._record_execution(
                    self.build_command(keyword),
                    started=started,
                    exit_code=returncode,
                    stdout=stdout,
                    stderr=stderr,
                    error_code=ErrorCode.SOURCE_BLOCKED,
                    error_message=f"{notice}（工具退出码 {returncode}）",
                    status="failed",
                )
                return []
            self._record_execution(
                self.build_command(keyword),
                started=started,
                exit_code=returncode,
                stdout=stdout,
                stderr=stderr,
                error_code=_exit_code_to_error(returncode),
                error_message=f"工具退出码 {returncode}",
                status="failed",
            )
            return []

        # 退出码 0 也不代表查询做完了：ENScan 在 AQC 被拦时可能一边打印
        # 验证提示、一边以 0 退出（真实样本见 ``results/artifacts``）。
        # 这一条必须在**任何「成功」记录之前**判掉，否则会落到下面
        # ``_record_execution(..., status 默认 success)`` 上，被
        # ``RunnerResult.ok([])`` 翻译成 ``no_results`` —— 页面上就成了
        # 「执行成功但零结果」，与「目标真的没有资产」完全无法区分。
        notice = _security_verification_notice(stderr, stdout)
        if notice:
            print("[!] enscan 被安全验证阻断（进程退出码为 0，但查询未完成）")
            self._record_execution(
                self.build_command(keyword),
                started=started,
                exit_code=returncode,
                stdout=stdout,
                stderr=stderr,
                error_code=ErrorCode.SOURCE_BLOCKED,
                error_message=notice,
                status="failed",
            )
            # **不解析、不返回任何域名**：被拦截时那点输出不是完整结果，
            # 把它当成「查到的资产」等于用残缺数据冒充完整答案。
            # 但证据照旧留在 last_execution 里，由 executor 落盘。
            return []

        self._record_execution(
            self.build_command(keyword),
            started=started,
            exit_code=returncode,
            stdout=stdout,
            stderr=stderr,
        )

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

        values, error = self.parse_output("", "", {"output_file": safe_file})
        if error:
            # JSON 在但一条域名都解析不出来：这是解析失败，不是「零结果」。
            # 记进 last_execution，run() 会据此把它标成 failed。
            self.last_execution["error_code"] = ErrorCode.PARSE_ERROR
            self.last_execution["error_message"] = "enscan JSON 输出无法解析出域名"
            self.last_execution["status"] = "failed"
        return values
