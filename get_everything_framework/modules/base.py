"""
工具运行器基类模块。

提供 BaseRunner 基类，封装了所有扫描器运行器的通用功能，
包括命令行解析、子进程执行、结果文件读写、临时输入文件创建等。
子类只需实现 run_scan() 方法即可集成新的安全扫描工具。

M4 起额外提供统一接口（方案第 8.1 节）：

* :meth:`BaseRunner.run` —— 把 ``run_scan`` 的返回值（或异常）统一包装成
  :class:`core.runner_result.RunnerResult`，**失败/超时/零结果分开记录**；
* :meth:`BaseRunner.health_check` —— 工具是否可执行；
* ``self.last_execution`` —— 上一次子进程执行的原始细节（exit_code /
  duration_ms / stdout / stderr / 脱敏命令预览），供 ``run`` 组装结果。

这一层是「禁止失败返回 ``[]``」（方案第 8.2 节）的落点：子类仍然可以返回
旧式的字符串列表，但对外暴露的 ``run()`` 永远给出带 ``error_code`` 的结果。
"""

import hashlib
import os
import shutil
import signal
import subprocess
import tempfile
import time

from config import OUTPUT_DIR, SCAN_LIMITS

from core.errors import ErrorCode
from core.runner_result import (
    Observation,
    RunnerResult,
    ToolHealth,
    result_from_exception,
    scrub_command,
)

# stderr 预览的最大字符数：只用于本地排查，不进 API 出参。
STDERR_PREVIEW_LIMIT = 2000

# 超时杀进程后，最多再花这么久把残留输出读干净。
PROCESS_DRAIN_SECONDS = 5


class BaseRunner:
    """
    扫描器运行器基类。

    封装了外部安全工具调用的通用流程：
    - 命令行解析与可执行文件定位（_resolve_command）
    - 子进程执行与错误处理（_execute / _execute_stdout）
    - 结果文件的构建与读取（_build_output_file / _read_results）
    - 临时输入文件的创建（_write_input_file）

    子类需要：
    1. 在 __init__ 中调用 super().__init__(config, tool_name) 传入工具配置
    2. 实现 run_scan(domain) 方法，定义具体的扫描逻辑
    """

    # 该类工具产出的观测类别（子类可覆盖，缺省沿用 config["category"]）。
    category = "subdomain"

    def __init__(self, config, tool_name):
        """
        初始化运行器。

        Args:
            config: 工具配置字典，包含 path、category、timeout 等参数
            tool_name: 工具名称字符串，用于标识和输出文件命名
        """
        self.config = config
        self.output_dir = OUTPUT_DIR
        self.tool_name = tool_name
        self.category = config.get("category", "subdomain")
        # 上一次子进程执行的原始细节；由 _execute / _execute_stdout 写入。
        self.last_execution: dict = {}
        # 执行前清理旧输出文件失败时的警告（见 _clear_stale_output）。
        self._stale_output_warning: str | None = None

    # ── M4 统一接口（方案第 8.1 节） ────────────────────────

    def health_check(self) -> ToolHealth:
        """检查工具可执行文件是否可用。

        只做 ``shutil.which`` / ``os.access`` 判断，**不执行**工具本身，
        因此可以被 ``/health`` 与页面安全调用。
        """
        path = self.config.get("path")
        if not path:
            return ToolHealth(
                name=self.tool_name,
                available=False,
                error_code=ErrorCode.TOOL_NOT_FOUND,
                message="配置中缺少 path",
            )

        resolved = path if os.path.isabs(path) else shutil.which(path)
        if not resolved:
            return ToolHealth(
                name=self.tool_name,
                available=False,
                path=path,
                error_code=ErrorCode.TOOL_NOT_FOUND,
                message=f"未在 PATH 中找到可执行文件: {path}",
            )
        if not os.access(resolved, os.X_OK):
            return ToolHealth(
                name=self.tool_name,
                available=False,
                path=resolved,
                error_code=ErrorCode.PERMISSION_DENIED,
                message=f"没有执行权限: {resolved}",
            )
        return ToolHealth(name=self.tool_name, available=True, path=resolved)

    def build_command(self, target, options=None):
        """构造命令行参数列表。

        基类默认返回 ``None``，表示「命令由 ``run_scan`` 内部构造」。
        需要把命令暴露给调用方（例如做命令白名单校验）的子类可覆盖本方法。
        """
        return None

    def parse_output(self, stdout, stderr, artifacts=None):
        """把工具输出解析成观测列表。

        基类默认返回空列表：大多数工具把结果写进 ``-o`` 指定的文件，
        解析发生在 :meth:`run_scan` 内部，因此这里只作为扩展点。
        """
        return []

    def run(self, target, options=None, context=None) -> RunnerResult:
        """执行一次扫描并返回结构化结果（**推荐入口**）。

        行为：

        * 调用子类的 ``run_scan``；
        * 把返回值（字符串列表 / dict 列表 / ``RunnerResult``）归一化为观测；
        * 把任何异常翻译成带 ``error_code`` 的失败结果，**绝不返回裸空列表**；
        * 把 ``self.last_execution`` 里的 exit_code / 耗时 / 脱敏命令预览带上。

        子类若需要更精细的解析（如 httpx 的 JSONL 字段），可以覆盖本方法，
        或覆盖 :meth:`observations` 只提供「值 → 观测」的映射。
        """
        started = time.perf_counter()
        try:
            raw = self.run_scan(target)
        except BaseException as exc:  # noqa: BLE001 - SystemExit 也必须接住（否则带走 worker）
            elapsed = int((time.perf_counter() - started) * 1000)
            result = result_from_exception(
                exc,
                tool_name=self.tool_name,
                target=target,
                command_preview=self._command_preview(),
            )
            # 子进程已经给出确切原因（超时/未安装）时，以它为准：
            # 子类抛出的通用 RuntimeError 会盖掉更精确的错误码。
            exec_error = self.last_execution.get("error_code")
            if exec_error and result.error_code == ErrorCode.UNKNOWN_ERROR:
                result.error_code = exec_error
                result.error_message = self.last_execution.get("error_message") or result.error_message
                result.status = self.last_execution.get("status", result.status)
            result.duration_ms = self.last_execution.get("duration_ms", elapsed)
            # 异常自身带着退出码（SystemExit）时不要用 None 覆盖掉它。
            if result.exit_code is None:
                result.exit_code = self.last_execution.get("exit_code")
            result.stderr_preview = self._stderr_preview()
            return result

        elapsed = int((time.perf_counter() - started) * 1000)
        if isinstance(raw, RunnerResult):
            result = raw
        else:
            result = self._to_result(raw)

        result.tool_name = result.tool_name or self.tool_name
        result.target = result.target or target
        result.command_preview = result.command_preview or self._command_preview()
        result.duration_ms = (
            result.duration_ms if result.duration_ms is not None else self.last_execution.get("duration_ms", elapsed)
        )
        if result.exit_code is None:
            result.exit_code = self.last_execution.get("exit_code")
        result.stderr_preview = result.stderr_preview or self._stderr_preview()

        # 子进程层面已明确失败（工具没装/超时/非零退出）但结果却像成功：
        # 以执行细节为准，避免「失败被降级成空结果」。
        exec_error = self.last_execution.get("error_code")
        if exec_error and not result.is_failure and not result.data:
            result.status = self.last_execution.get("status", "failed")
            result.error_code = exec_error
            result.error_message = self.last_execution.get("error_message")

        return result

    def observations(self, values) -> list[Observation]:
        """把 ``run_scan`` 的返回值转换成结构化观测。

        基类默认按「字符串值 → 单字段观测」处理；``httpx`` 这类多字段工具
        应覆盖本方法以保留状态码/标题/技术栈等。
        """
        items: list[Observation] = []
        for value in values or []:
            if isinstance(value, Observation):
                items.append(value)
            elif isinstance(value, dict):
                raw_value = value.get("value") or value.get("url") or ""
                if not raw_value:
                    continue
                data = {k: v for k, v in value.items() if k not in ("value", "category")}
                items.append(
                    Observation(
                        category=value.get("category") or self.category,
                        value=str(raw_value),
                        data=data,
                        source_tool=self.tool_name,
                    )
                )
            elif value is not None:
                items.append(
                    Observation(category=self.category, value=str(value), source_tool=self.tool_name)
                )
        return items

    def _to_result(self, raw) -> RunnerResult:
        """把 ``run_scan`` 的返回值归一化为 :class:`RunnerResult`。"""
        data = self.observations(raw)
        return RunnerResult.ok(
            data,
            tool_name=self.tool_name,
            command_preview=self._command_preview(),
        )

    def _command_preview(self) -> str | None:
        """上一次执行的脱敏命令预览。"""
        cmd = self.last_execution.get("cmd")
        if not cmd:
            return None
        return scrub_command(cmd)

    def _stderr_preview(self) -> str | None:
        """上一次执行的 stderr 截断预览（仅本地排查用）。"""
        text = self.last_execution.get("stderr")
        if not text:
            return None
        text = str(text).strip()
        if not text:
            return None
        if len(text) > STDERR_PREVIEW_LIMIT:
            text = text[: STDERR_PREVIEW_LIMIT - 3] + "..."
        return text

    def declared_output_file(self, cmd=None) -> str | None:
        """本工具的结果输出文件路径。

        不传 ``cmd`` 时按以下顺序推断：

        1. ``last_execution["output_file"]`` —— 由 ``_execute_stdout`` 记录
           （结果是我们重定向写盘的，命令行里没有对应参数）；
        2. 命令行里的 ``-o`` / ``--output`` / ``-oN`` 等惯例参数。

        显式传入 ``cmd`` 时**只看这条命令**：``_clear_stale_output`` 等
        执行前逻辑必须基于即将运行的命令判断，不能读到上一次执行的残留记录。

        Args:
            cmd: 命令行参数列表；缺省用上一次执行的命令。

        Returns:
            str | None: 输出文件路径。
        """
        if cmd is not None:
            return declared_output_file(cmd)
        recorded = self.last_execution.get("output_file")
        if recorded:
            return recorded
        return declared_output_file(self.last_execution.get("cmd"))

    def output_file_from_execution(self) -> str | None:
        """从上次执行命中的命令行里取出结果文件。

        大多数工具把结果写进 ``-o`` 指向的文件而 stdout 为空。若只把 stdout
        当证据，「跑通了但没数据」这类问题就会缺少最关键的一份原始记录。
        """
        return self.declared_output_file()

    def _clear_stale_output(self, cmd) -> str | None:
        """执行前删掉结果文件（如果上次留下了），返回该路径。

        **这是 ``_read_results`` 那个经典坑的根治**（``docs/CODEBASE_MAP.md``
        §7.2 / AGENTS.md 第 2 条）：输出文件名只由 ``md5(domain)`` 决定，
        上一次扫描留下的文件会被当成本次结果读回来，于是「工具失败」会被
        伪装成「跑通了、有数据」。

        先删掉再执行，语义就干净了：文件存在 ⇒ 本次工具真的写了它。
        """
        return self._clear_stale_output_for(self.declared_output_file(cmd))

    def _clear_stale_output_for(self, path: str | None) -> str | None:
        """删除指定的结果文件（如果存在），返回该路径。

        与 :meth:`_clear_stale_output` 的区别：这里直接给路径，供
        ``_execute_stdout`` 使用（它的结果文件由我们重定向写盘，
        命令行里没有 ``-o`` 参数可解析）。
        """
        if not path:
            return None
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            # 删不掉也不该让扫描直接失败；但必须留下明确警告，
            # 否则「旧数据被当成本次结果」会再次变成无法解释的幻觉。
            self._stale_output_warning = f"无法删除旧的输出文件（本次结果可能混入旧数据）: {path} ({exc})"
        return path

    def _record_execution(
        self,
        cmd,
        *,
        started: float,
        exit_code: int | None,
        stdout: str | None = None,
        stderr: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        status: str = "success",
        output_file: str | None = None,
    ) -> None:
        """记录一次子进程执行的原始细节（供 ``run`` 组装 RunnerResult）。"""
        self.last_execution = {
            "cmd": list(cmd) if not isinstance(cmd, str) else cmd,
            "started": started,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "error_code": error_code,
            "error_message": error_message,
            "status": status,
        }
        if output_file:
            # ``_execute_stdout`` 的结果文件不出现在命令行里，必须显式记下，
            # 否则证据采集与残留清理都找不到它。
            self.last_execution["output_file"] = output_file
        if self._stale_output_warning:
            self.last_execution["stale_output_warning"] = self._stale_output_warning
            self._stale_output_warning = None

    def _timeout_seconds(self) -> int:
        """本工具的子进程超时（秒）。

        方案第 8.2 节禁止「无超时执行」，第 8.3 节给出本机建议值
        ``process_timeout: 120``。取**二者较小值**：

        * 单个工具可以把自己的超时调得更短（例如 httpx 只给 30 秒）；
        * 但不能超过 :data:`config.SCAN_LIMITS` 里声明的本机上限，
          否则一条卡死的命令能把 worker 占住几分钟。

        历史实现直接用了配置里的 ``300``，而 ``SCAN_LIMITS["process_timeout"]``
        虽然从 M2 起就存在却从未生效 —— 这里把它接上。
        """
        configured = self.config.get("process_timeout")
        limit = int(SCAN_LIMITS["process_timeout"])
        try:
            configured = int(configured) if configured else None
        except (TypeError, ValueError):
            configured = None
        if configured is None or configured <= 0:
            return limit
        return min(configured, limit)

    def _build_output_file(self, domain):
        """
        根据域名构建输出文件路径。

        使用域名的 MD5 哈希前缀 + 工具名作为文件名，
        避免特殊字符导致文件系统问题。

        Args:
            domain: 目标域名字符串

        Returns:
            输出文件的完整路径
        """
        safe = hashlib.md5(domain.encode("utf-8")).hexdigest()[:12]
        return os.path.join(self.output_dir, f"{safe}_{self.tool_name}.txt")

    def _read_results(self, output_file):
        """
        从输出文件中读取扫描结果。

        返回去重后的非空行列表。如果文件不存在则返回空列表。

        Args:
            output_file: 输出文件路径

        Returns:
            字符串列表，每行为一条结果
        """
        if not os.path.exists(output_file):
            return []

        with open(output_file, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]

    def _resolve_command(self, cmd):
        """
        解析并规范化命令行参数。

        处理以下场景：
        - 相对路径：通过 PATH 环境变量查找可执行文件
        - 绝对路径：直接使用
        - Windows .bat/.cmd 文件：通过 ComSpec (cmd.exe) 调用

        Args:
            cmd: 命令行参数列表，cmd[0] 为可执行文件路径

        Returns:
            解析后的完整命令行参数列表

        Raises:
            FileNotFoundError: 如果无法找到可执行文件
        """
        executable = cmd[0]
        resolved = shutil.which(executable) if not os.path.isabs(executable) else executable
        if not resolved:
            raise FileNotFoundError(executable)

        if resolved.lower().endswith((".cmd", ".bat")):
            comspec = os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe")
            return [comspec, "/c", resolved] + cmd[1:]

        return [resolved] + cmd[1:]

    def _run_subprocess(self, cmd, timeout: int):
        """执行子进程并**真正**落实超时（返回 ``(returncode, stdout, stderr)``）。

        为什么不能用 ``subprocess.run(timeout=...)``：Windows 上 ``.cmd`` /
        ``.bat`` 会经 ``cmd.exe`` 再派生一层，超时被杀掉的只是 ``cmd.exe``，
        真正的工具进程变成孤儿继续占用管道 —— ``subprocess.run`` 会一直等
        管道关闭，于是「超时」形同虚设，worker 被永久占住。

        因此这里改成：

        1. ``Popen`` + ``communicate(timeout=...)``；
        2. 超时后**整棵进程树**一起杀（Windows 用 ``taskkill /T``，
           其它平台用进程组）；
        3. 再 ``communicate()`` 一次把残留输出读干净，然后如实抛
           ``TimeoutExpired``，让上层记成 ``timeout`` 而不是「神秘地卡住」。

        POSIX 下必须让子进程**另起进程组**（``start_new_session=True``）：
        默认情况下子进程与 worker 同属一个进程组，此时 ``_kill_process_tree``
        里的 ``os.killpg(os.getpgid(child))`` 会把 worker 自己也一起 ``SIGKILL``
        掉。Windows 不支持 ``start_new_session``（会被忽略），那边靠
        ``taskkill /T`` 按进程树清理，不依赖进程组。
        """
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=os.name != "nt",
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_tree(process)
            # 整棵树被杀后管道就会关闭，通常立刻返回。但要给一个上限：
            # 万一有进程没被杀干净还攥着管道，这里绝不能永久阻塞 worker。
            try:
                stdout, stderr = process.communicate(timeout=PROCESS_DRAIN_SECONDS)
            except subprocess.TimeoutExpired:  # pragma: no cover - 极端兜底
                stdout, stderr = "", ""
            raise subprocess.TimeoutExpired(cmd, timeout, output=stdout, stderr=stderr) from None
        return process.returncode, stdout, stderr

    def _execute(self, cmd, domain):
        """
        执行命令行工具并将输出写入文件（工具自身通过 -o 参数输出到文件）。

        适用于支持 -o 输出文件参数的工具（如 subfinder、naabu 等）。

        返回值仍然是 ``bool``（保持 M0～M3 的调用方兼容），但执行细节
        （exit_code / 耗时 / stdout / stderr / 失败原因）全部记进
        ``self.last_execution``，由 :meth:`run` 翻译成带 ``error_code`` 的
        :class:`RunnerResult`。**调用方不要再把 ``False`` 直接变成 ``[]`` 后
        假装成功** —— 走 ``run()`` 才能区分失败与零结果。

        Args:
            cmd: 完整的命令行参数列表
            domain: 目标域名（用于日志输出）

        Returns:
            True 表示执行成功，False 表示执行失败
        """
        started = time.perf_counter()
        try:
            print(f"[*] 正在使用 {self.tool_name} 扫描域名: {domain} ...")
            # 先清掉上次留下的同名输出文件，避免旧结果被当成本次结果读回来
            # （docs/CODEBASE_MAP.md §7.2 记录的经典坑）。
            self._clear_stale_output(cmd)
            run_cmd = self._resolve_command(cmd)
            returncode, stdout, stderr = self._run_subprocess(run_cmd, self._timeout_seconds())
            if returncode != 0:
                raise subprocess.CalledProcessError(returncode, run_cmd, output=stdout, stderr=stderr)
            self._record_execution(
                cmd,
                started=started,
                exit_code=returncode,
                stdout=stdout,
                stderr=stderr,
            )
            return True
        except FileNotFoundError:
            print(f"[!] 未找到工具 {self.config['path']}，请先安装并加入环境变量")
            self._record_execution(
                cmd,
                started=started,
                exit_code=None,
                error_code=ErrorCode.TOOL_NOT_FOUND,
                error_message=f"未找到可执行文件: {self.config.get('path')}",
                status="failed",
            )
            return False
        except subprocess.TimeoutExpired as exc:
            print(f"[!] {domain} 扫描超时，已停止 {self.tool_name} 任务")
            self._record_execution(
                cmd,
                started=started,
                exit_code=None,
                stdout=_decode(exc.stdout),
                stderr=_decode(exc.stderr),
                error_code=ErrorCode.TIMEOUT,
                error_message=f"执行超过 {self._timeout_seconds()} 秒，已强制终止",
                status="timeout",
            )
            return False
        except subprocess.CalledProcessError as e:
            error_msg = (e.stderr or e.stdout or str(e)).strip()
            print(f"[!] {domain} 扫描失败: {error_msg}")
            self._record_execution(
                cmd,
                started=started,
                exit_code=e.returncode,
                stdout=e.stdout,
                stderr=e.stderr,
                error_code=_exit_code_to_error(e.returncode),
                error_message=f"工具退出码 {e.returncode}",
                status="failed",
            )
            return False
        except OSError as exc:
            print(f"[!] {domain} 执行失败: {exc}")
            self._record_execution(
                cmd,
                started=started,
                exit_code=None,
                error_code=ErrorCode.UNKNOWN_ERROR,
                error_message=str(exc),
                status="failed",
            )
            return False

    def _execute_stdout(self, cmd, domain, output_file):
        """
        执行命令行工具并将标准输出重定向到文件。

        适用于不支持 -o 参数、结果输出到 stdout 的工具（如 assetfinder、oneforall）。

        执行细节同样记录进 ``self.last_execution``，语义与 :meth:`_execute` 一致。

        Args:
            cmd: 完整的命令行参数列表
            domain: 目标域名（用于日志输出）
            output_file: 结果输出文件路径

        Returns:
            True 表示执行成功，False 表示执行失败
        """
        started = time.perf_counter()
        try:
            print(f"[*] 正在使用 {self.tool_name} 扫描目标: {domain} ...")
            # 结果由我们自己重定向写盘，所以「旧结果」问题同样存在：
            # 先删掉旧文件，否则工具失败时会把上次的输出当成本次结果。
            self._clear_stale_output_for(output_file)
            run_cmd = self._resolve_command(cmd)
            returncode, stdout, stderr = self._run_subprocess(run_cmd, self._timeout_seconds())
            if returncode != 0:
                raise subprocess.CalledProcessError(returncode, run_cmd, output=stdout, stderr=stderr)
            self._record_execution(
                cmd,
                started=started,
                exit_code=returncode,
                stdout=stdout,
                stderr=stderr,
                output_file=output_file,
            )
            # 先登记执行细节再落盘，保证即使写文件失败也能解释原因。
            try:
                with open(output_file, "w", encoding="utf-8") as f:
                    f.write(stdout or "")
            except OSError as exc:
                self._record_execution(
                    cmd,
                    started=started,
                    exit_code=returncode,
                    stdout=stdout,
                    stderr=stderr,
                    error_code=ErrorCode.UNKNOWN_ERROR,
                    error_message=f"无法写入结果文件: {exc}",
                    status="failed",
                    output_file=output_file,
                )
                print(f"[!] 无法写入结果文件: {exc}")
                return False
            return True
        except FileNotFoundError:
            print(f"[!] 未找到工具 {self.config['path']}，请先安装并加入环境变量")
            self._record_execution(
                cmd,
                started=started,
                exit_code=None,
                error_code=ErrorCode.TOOL_NOT_FOUND,
                error_message=f"未找到可执行文件: {self.config.get('path')}",
                status="failed",
            )
            return False
        except subprocess.TimeoutExpired as exc:
            print(f"[!] {domain} 扫描超时，已停止 {self.tool_name} 任务")
            self._record_execution(
                cmd,
                started=started,
                exit_code=None,
                stdout=_decode(exc.stdout),
                stderr=_decode(exc.stderr),
                error_code=ErrorCode.TIMEOUT,
                error_message=f"执行超过 {self._timeout_seconds()} 秒，已强制终止",
                status="timeout",
            )
            return False
        except subprocess.CalledProcessError as e:
            error_msg = (e.stderr or e.stdout or str(e)).strip()
            print(f"[!] {domain} 扫描失败: {error_msg}")
            self._record_execution(
                cmd,
                started=started,
                exit_code=e.returncode,
                stdout=e.stdout,
                stderr=e.stderr,
                error_code=_exit_code_to_error(e.returncode),
                error_message=f"工具退出码 {e.returncode}",
                status="failed",
            )
            return False
        except OSError as exc:
            print(f"[!] {domain} 执行失败: {exc}")
            self._record_execution(
                cmd,
                started=started,
                exit_code=None,
                error_code=ErrorCode.UNKNOWN_ERROR,
                error_message=str(exc),
                status="failed",
            )
            return False

    def _write_input_file(self, domain, values, suffix=None):
        """
        创建临时输入文件并写入数据。

        用于将子域名候选列表写入临时文件，供工具通过 -l 参数读取。
        调用方负责在使用后删除临时文件。

        Args:
            domain: 目标域名（用于文件名标识）
            values: 要写入的字符串列表
            suffix: 自定义文件名后缀，默认使用域名和工具名生成

        Returns:
            临时文件的完整路径
        """
        temp_file = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=suffix or f"_{domain}_{self.tool_name}_input.txt",
            dir=OUTPUT_DIR,
            delete=False,
        )
        try:
            temp_file.write("\n".join(values))
            temp_file.write("\n")
        finally:
            temp_file.close()
        return temp_file.name


def _decode(value) -> str | None:
    """把 ``TimeoutExpired`` 里的 bytes/str/stdout 统一成字符串。"""
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _kill_process_tree(process) -> None:
    """终止一个子进程**及其全部后代**。

    Windows 上 ``.cmd`` / ``.bat`` 工具会多一层 ``cmd.exe``：只杀直接子进程
    会留下真正在跑的工具当孤儿（它会一直占着 stdout/stderr 管道，导致
    ``communicate()`` 永不返回）。``taskkill /T`` 按进程树清理，是 Windows
    上唯一可靠的做法；POSIX 下则用进程组。

    POSIX 分支有一处致命陷阱：只有当子进程处在一个**独立于 worker 的**进程组时，
    ``killpg`` 才是安全的。若两者同组（Popen 没传 ``start_new_session=True``，
    或调用方自己造了 Popen），``killpg`` 会连 worker 一起 ``SIGKILL``。
    这里显式比一次进程组，跨平台都无法绕过 —— 宁可只杀直接子进程，
    也不能把调用方自己杀掉。
    """
    if process.poll() is not None:
        return
    if os.name == "nt":  # pragma: no cover - 平台分支
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
            )
        except OSError:
            pass
    else:  # pragma: no cover - 平台分支
        try:
            child_pgid = os.getpgid(process.pid)
            own_pgid = os.getpgid(0)
        except (OSError, AttributeError):
            child_pgid = own_pgid = None
        if child_pgid is not None and child_pgid != own_pgid:
            try:
                os.killpg(child_pgid, signal.SIGKILL)
            except (OSError, AttributeError):
                pass
    try:
        process.kill()
    except OSError:
        pass


# 命令行里指定输出文件的参数名（各工具惯例不一）：
# ``-o`` 是 ProjectDiscovery 系，``-oN`` / ``-oX`` / ``-oG`` / ``-oA`` 是 nmap 系。
OUTPUT_FLAGS = ("-o", "--output", "-output", "-oN", "-oX", "-oG", "-oA")


def declared_output_file(cmd) -> str | None:
    """从命令行里取出 ``-o`` / ``--output`` 指定的输出文件路径。

    Args:
        cmd: 命令行参数列表（字符串形态无法可靠解析，返回 ``None``）。

    Returns:
        str | None: 输出文件路径。
    """
    if not cmd or isinstance(cmd, str):
        return None
    for index, item in enumerate(cmd[:-1]):
        if str(item) in OUTPUT_FLAGS:
            candidate = str(cmd[index + 1])
            return candidate or None
    return None


def _exit_code_to_error(exit_code: int | None) -> str:
    """把子进程退出码翻译成统一错误码。

    约定（POSIX 惯例 + 本机联调需要）：

    * ``126`` —— 找到了但不可执行 → ``permission_denied``；
    * ``127`` —— 命令不存在 → ``tool_not_found``；
    * ``137`` / ``139`` —— 被 kill / 段错误，归为 ``unknown_error``；
    * 其它非零 → ``unknown_error``。
    """
    if exit_code == 126:
        return ErrorCode.PERMISSION_DENIED
    if exit_code == 127:
        return ErrorCode.TOOL_NOT_FOUND
    return ErrorCode.UNKNOWN_ERROR
