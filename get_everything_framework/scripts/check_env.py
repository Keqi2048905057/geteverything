"""一键环境自检（M6 收尾项）。

回答一个问题：**这台机器上，本机联调版能不能跑起来、能不能跑真任务？**

设计约束（每一条都有对应测试锁住）：

* **完全只读**：不写任何文件、不建库、不发任何网络请求、不调用任何扫描工具。
  目录那一项只用 ``os.access`` 判权限，**不**用「写一个探针文件再删」的做法
  —— 那会在仓库里留下痕迹（AGENTS.md 硬约束：测试与脚本不得污染 ``results/``）。
* **不输出任何密钥**：只输出状态字符串（``configured`` / ``ephemeral`` / ``空``），
  绝不输出 ``SECRET_KEY`` 或管理员凭据的值，也不在 ``print`` 语句里出现这些字段名
  （``tests/unit/test_observability.py`` 有源码守卫，会在 print 里搜密钥形状的**字段名**）。
* **不因为「缺东西」就崩**：``config`` / ``core.health`` 导入失败也要给出可读结论，
  否则这个脚本在最需要它的时候恰好用不了。

退出码::

    0 = 全部通过
    1 = 有警告（能跑，但有东西没配好）
    2 = 有阻塞项（跑不起来 / 配置有安全风险）

用法::

    python scripts/check_env.py            # 人读报告
    python scripts/check_env.py --json     # 一行 JSON，便于脚本消费
    python scripts/check_env.py --strict   # 有警告也按退出码 2 处理（CI 用）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

# 允许 `python scripts/check_env.py` 直接运行（脚本不在项目根下）。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"

_EXIT_BY_STATUS = {STATUS_OK: 0, STATUS_WARN: 1, STATUS_FAIL: 2}

# 新库必须有这些表，缺一张就说明 schema 没建全（或建库中途失败）。
REQUIRED_TABLES = (
    "scopes",
    "audit_events",
    "uploads",
    "jobs",
    "job_steps",
    "job_events",
    "artifacts",
    "exports",
    "assets",
    "observations",
)


@dataclass(frozen=True)
class Check:
    """一条检查结果。

    Attributes:
        name: 检查项名称（人读）。
        status: ``ok`` / ``warn`` / ``fail``。
        detail: 结论本身（**不得含任何密钥值**）。
        hint: 不通过时该怎么办。
    """

    name: str
    status: str
    detail: str
    hint: str = ""


# ── 各项检查 ──────────────────────────────────────────────


def check_python() -> Check:
    """Python 版本是否满足 ``pyproject.toml`` 的 ``target-version = py310``。"""
    version = sys.version_info
    text = f"{version.major}.{version.minor}.{version.micro}"
    if (version.major, version.minor) < (3, 10):
        return Check("Python 版本", STATUS_FAIL, f"{text}（要求 >= 3.10）", "升级 Python 到 3.10+")
    if (version.major, version.minor) >= (3, 13):
        # 依赖清单按 3.11 钉版本，新解释器未必有对应的 wheel。
        return Check("Python 版本", STATUS_WARN, f"{text}（依赖清单按 3.11 钉版本）", "如遇依赖装不上，改用 3.11")
    return Check("Python 版本", STATUS_OK, text)


def _version_key(text: str) -> tuple:
    """把版本号转成可比较的元组。

    不引入 ``packaging``（它不在依赖清单里，这个脚本要能在「依赖还没装」时也跑），
    但也不能直接把版本当字符串比 —— ``"3.10" < "3.9"`` 是 True，会把合法的
    3.10 判成过旧。三条规则：

    * 数字段按整数比（``3.10 > 3.9``）；
    * **去掉末尾的零**，让 ``2.0`` 与 ``2.0.0`` 相等；
    * 带预发布后缀的排在同数字正式版**之前**（``1.0.0rc1 < 1.0.0``）。
    """
    token = text.strip().lower()
    if token.startswith("v"):
        token = token[1:]

    match = re.match(r"^(\d+(?:\.\d+)*)(.*)$", token)
    if not match:
        # 完全不是数字版本（例如 "any"）：退化成纯字符串比较，不抛异常。
        return ((), 0, token)

    release = [int(part) for part in match.group(1).split(".")]
    while len(release) > 1 and release[-1] == 0:
        release.pop()

    pre = match.group(2).lstrip("-_.")
    return (tuple(release), 0 if not pre else -1, pre)


def _satisfies(installed: str, spec: str) -> bool:
    """判断已装版本是否满足 ``==`` / ``>=`` / ``>`` / ``<=`` / ``<`` / ``~=``。

    返回 ``True`` 表示满足（或规格无法识别时不误报）。``~=`` 只校验下界
    （``~=1.10`` 的下界是 1.10），上界差异留给人看 detail 里的实际版本号。
    """
    spec = spec.strip()
    for op in ("==", ">=", "<=", "~=", ">", "<"):
        if spec.startswith(op):
            expected = spec[len(op):].strip()
            break
    else:
        return True

    have, want = _version_key(installed), _version_key(expected)
    if op == "==":
        return have == want
    if op in (">=", "~="):
        return have >= want
    if op == ">":
        return have > want
    if op == "<=":
        return have <= want
    return have < want


def _parse_requirements(path: Path) -> list[tuple[str, str]]:
    """解析依赖清单里的 ``name<op>version`` 行（忽略注释、空行与 ``-r`` 之类的指令）。

    ``requirement.txt`` 全部用 ``==`` 钉死，``requirement-dev.txt`` 用 ``>=``
    —— 两种都要能认，否则开发依赖那一项会永远报「读不到」。
    """
    pinned: list[tuple[str, str]] = []
    if not path.exists():
        return pinned
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        # 去掉环境标记（本项目清单未用到，但解析不该因此崩掉）与行尾注释。
        line = line.split(";", 1)[0].split("#", 1)[0].strip()
        if not line:
            continue
        for op in ("==", ">=", "<=", "~=", ">", "<"):
            if op in line:
                name, _, spec = line.partition(op)
                name = name.strip()
                # 只认合法包名，避免把 "https://..." 之类当成包。
                if name and all(char.isalnum() or char in "-_." for char in name):
                    pinned.append((name, op + spec.strip()))
                break
    return pinned


def check_dependencies() -> list[Check]:
    """逐个核对依赖清单里钉的版本是否已装、装的是否是那一版。

    运行依赖缺失是 ``fail``（起不来）；开发依赖缺失只是 ``warn``（只是跑不了测试）。
    """
    import importlib.metadata as metadata

    checks: list[Check] = []

    for label, filename, missing_status in (
        ("运行依赖", "requirement.txt", STATUS_FAIL),
        ("开发依赖", "requirement-dev.txt", STATUS_WARN),
    ):
        pinned = _parse_requirements(PROJECT_ROOT / filename)
        if not pinned:
            checks.append(Check(label, STATUS_WARN, f"读不到 {filename}", "确认文件存在且格式为 name==version"))
            continue

        missing: list[str] = []
        mismatched: list[str] = []
        for name, spec in pinned:
            try:
                installed = metadata.version(name)
            except metadata.PackageNotFoundError:
                missing.append(name)
                continue
            if not _satisfies(installed, spec):
                mismatched.append(f"{name}（清单 {spec} / 实装 {installed}）")

        detail = f"{len(pinned)} 项，缺失 {len(missing)}，不满足 {len(mismatched)}"
        if missing:
            checks.append(
                Check(
                    label,
                    missing_status,
                    f"{detail}：{'、'.join(missing[:5])}{' …' if len(missing) > 5 else ''}",
                    f"python -m pip install -r {filename}",
                )
            )
        elif mismatched:
            checks.append(
                Check(
                    label,
                    STATUS_WARN,
                    f"{detail}：{'、'.join(mismatched[:5])}",
                    f"python -m pip install -r {filename} 可对齐版本",
                )
            )
        else:
            checks.append(Check(label, STATUS_OK, detail))

    return checks


def check_directories() -> list[Check]:
    """四个运行期目录的权限检查（**只判定，不写盘**）。

    目录不存在不算错：``uploads/`` / ``exports/`` / ``backups/`` 都是按需创建，
    这时改为检查其父目录（项目根）是否可写。
    """
    import config

    targets = {
        "results（工具产物 / 心跳）": config.OUTPUT_DIR,
        "uploads（受控上传）": config.UPLOAD_DIR,
        "exports（导出文件）": config.EXPORT_DIR,
        "backups（设置写入前备份）": config.BACKUP_DIR,
    }

    checks: list[Check] = []
    for label, path in targets.items():
        exists = os.path.isdir(path)
        probe = path if exists else str(PROJECT_ROOT)
        writable = os.access(probe, os.W_OK)
        state = "已存在" if exists else "待创建"
        if not writable:
            checks.append(
                Check(label, STATUS_FAIL, f"{state}，且不可写", f"检查 {probe} 的权限（不要在只读盘上跑）")
            )
        else:
            checks.append(Check(label, STATUS_OK, f"{state}，权限可写（未实际写盘）"))
    return checks


def check_env_file() -> list[Check]:
    """``.env`` 与关键开关。**只输出状态，不输出任何值。**"""
    import config
    from core.security import is_weak_secret

    checks: list[Check] = []
    env_path = PROJECT_ROOT / ".env"

    if env_path.exists():
        checks.append(Check(".env 文件", STATUS_OK, "存在"))
    else:
        checks.append(
            Check(
                ".env 文件",
                STATUS_WARN,
                "不存在，将使用全部默认值",
                "Copy-Item .env.example .env 后按需修改",
            )
        )

    secret_field = "SECRET_KEY"
    if is_weak_secret((config.Config.SECRET_KEY or "").strip()):
        checks.append(
            Check(
                secret_field,
                STATUS_WARN,
                "未配置 / 为弱值：本进程会用一次性随机值，重启后所有登录会话失效",
                'python -c "import secrets; print(secrets.token_urlsafe(32))" 写入 .env',
            )
        )
    else:
        checks.append(Check(secret_field, STATUS_OK, "已配置且长度足够"))

    admin_field = "LOCAL_ADMIN_TOKEN"
    if (config.Config.LOCAL_ADMIN_TOKEN or "").strip():
        checks.append(Check(admin_field, STATUS_OK, "已固定"))
    else:
        checks.append(
            Check(
                admin_field,
                STATUS_WARN,
                "留空：启动时控制台打印临时凭据，重启即换",
                "想固定就写入 .env（见 .env.example 的生成命令）",
            )
        )

    if bool(config.Config.WEB_DEBUG):
        checks.append(Check("WEB_DEBUG", STATUS_FAIL, "为 true：会暴露调试器", "改为 false"))
    else:
        checks.append(Check("WEB_DEBUG", STATUS_OK, "false"))

    host = str(config.Config.WEB_HOST or "")
    if host in {"127.0.0.1", "localhost", "::1"}:
        checks.append(Check("WEB_HOST", STATUS_OK, host))
    else:
        checks.append(
            Check(
                "WEB_HOST",
                STATUS_WARN,
                f"{host}（非仅本机回环）",
                "本机联调请保持 127.0.0.1，不要用 0.0.0.0",
            )
        )

    # 真实扫描开关由 core.safety 解析（config.Config 上并没有这一项）。
    from core.safety import REAL_SCAN_ENV, real_scan_enabled

    if real_scan_enabled():
        checks.append(
            Check(
                "真实扫描总开关",
                STATUS_WARN,
                f"{REAL_SCAN_ENV}=true：mode=real 且 Scope.active_scan=true 时会真的调用外部工具",
                "确认只打已授权目标；不用时改回 false",
            )
        )
    else:
        checks.append(Check("真实扫描总开关", STATUS_OK, f"{REAL_SCAN_ENV} 未开启（只允许 mock）"))

    return checks


def check_external_tools() -> Check:
    """17 个外部扫描器是否在 PATH 上（**不执行它们**，只用 ``shutil.which``）。"""
    try:
        from core.health import tools_health
    except Exception as exc:  # pragma: no cover - 只在环境严重损坏时走到
        return Check("外部工具", STATUS_WARN, f"无法探测：{type(exc).__name__}", "先修好 config 导入")

    tools = tools_health()
    available = sorted(name for name, status in tools.items() if status == "available")
    missing = sorted(name for name, status in tools.items() if status != "available")

    if not available:
        return Check(
            "外部工具",
            STATUS_WARN,
            f"0/{len(tools)} 可用：mock 模式不受影响，real 模式全部会以 tool_not_found 失败",
            "按 docs/DEPLOYMENT.md 安装工具，或用 scripts/install_windows.ps1",
        )
    return Check(
        "外部工具",
        STATUS_OK,
        f"{len(available)}/{len(tools)} 可用；缺失：{'、'.join(missing) if missing else '无'}",
    )


def _read_only_conn(path: str) -> sqlite3.Connection:
    uri = "file:" + path.replace("\\", "/").replace("?", "%3f").replace("#", "%23") + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=2)


def check_databases() -> list[Check]:
    """两个 SQLite 库：旧扫描结果库（只读历史）与本机应用库（可写）。

    两个库都**只读打开**；不存在不算错，但要说清「还没有」还是「已就绪」。
    """
    import config

    checks: list[Check] = []

    legacy_path = str(config.SQLITE_CONFIG["path"])
    if not os.path.exists(legacy_path):
        checks.append(
            Check("旧结果库", STATUS_WARN, "不存在（尚无历史扫描数据）", "属正常：M4 起结果走新链路")
        )
    else:
        try:
            conn = _read_only_conn(legacy_path)
            try:
                tables = conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
                ).fetchone()[0]
            finally:
                conn.close()
            checks.append(Check("旧结果库", STATUS_OK, f"可只读打开，{tables} 张表"))
        except sqlite3.Error as exc:
            checks.append(Check("旧结果库", STATUS_FAIL, f"打开失败：{exc}", "文件可能损坏或被占用"))

    try:
        from core import db as core_db
    except Exception as exc:  # pragma: no cover
        checks.append(Check("本机应用库", STATUS_FAIL, f"无法导入 core.db：{type(exc).__name__}", "检查 core/ 目录完整性"))
        return checks

    app_path = core_db.db_path()
    if not os.path.exists(app_path):
        checks.append(
            Check("本机应用库", STATUS_WARN, "不存在（首次写入时自动建库）", "直接启动服务即可，无需手工建库")
        )
        return checks

    try:
        conn = _read_only_conn(app_path)
        try:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        checks.append(Check("本机应用库", STATUS_FAIL, f"打开失败：{exc}", "文件可能损坏或被占用"))
        return checks

    existing = {row[0] for row in rows}
    absent = [name for name in REQUIRED_TABLES if name not in existing]
    if absent:
        checks.append(
            Check("本机应用库", STATUS_FAIL, f"缺少表：{'、'.join(absent)}", "删掉该库文件让程序重建，或检查 core/db.py")
        )
    else:
        checks.append(Check("本机应用库", STATUS_OK, f"可只读打开，{len(existing)} 张表（关键表齐全）"))
    return checks


def check_worker() -> list[Check]:
    """worker 心跳与队列计数 —— 「任务一直 queued」的第一个判别点。"""
    try:
        from core.health import queue_health, worker_health
    except Exception as exc:  # pragma: no cover
        return [Check("worker", STATUS_WARN, f"无法探测：{type(exc).__name__}", "先修好 config 导入")]

    checks: list[Check] = []

    worker = worker_health()
    if worker == "ok":
        checks.append(Check("worker 心跳", STATUS_OK, "ok（30 秒内有心跳）"))
    elif worker == "stale":
        checks.append(
            Check("worker 心跳", STATUS_WARN, "stale（心跳文件超过 30 秒）", "worker 可能已停止，重启 python -m jobs.worker")
        )
    else:
        checks.append(
            Check(
                "worker 心跳",
                STATUS_WARN,
                "missing（从未启动或已删心跳文件）",
                "不启动 worker 时任务会一直停在 queued：python -m jobs.worker",
            )
        )

    queue = queue_health()
    checks.append(
        Check(
            "队列",
            STATUS_OK,
            f"queued={queue['queued']} running={queue['running']}（库状态 {queue['status']}）",
        )
    )
    return checks


def collect_checks() -> list[Check]:
    """跑完全部检查。**只读**：不写文件、不建库、不发网络请求、不执行外部工具。"""
    checks: list[Check] = [check_python()]
    checks.extend(check_dependencies())
    checks.extend(check_directories())
    checks.extend(check_env_file())
    checks.append(check_external_tools())
    checks.extend(check_databases())
    checks.extend(check_worker())
    return checks


# ── 报告与退出码 ──────────────────────────────────────────


def summarize(checks: list[Check]) -> dict[str, int]:
    """按状态计数。"""
    return {
        STATUS_OK: sum(1 for check in checks if check.status == STATUS_OK),
        STATUS_WARN: sum(1 for check in checks if check.status == STATUS_WARN),
        STATUS_FAIL: sum(1 for check in checks if check.status == STATUS_FAIL),
    }


def exit_code(checks: list[Check], *, strict: bool = False) -> int:
    """``fail`` → 2；``warn`` → 1（``strict`` 时也是 2）；全 ``ok`` → 0。"""
    worst = max((_EXIT_BY_STATUS[check.status] for check in checks), default=0)
    if strict and worst == 1:
        return 2
    return worst


def build_payload(checks: list[Check], *, strict: bool = False) -> dict:
    """``--json`` 的载荷（同样不含任何密钥值）。"""
    return {
        "ok": exit_code(checks, strict=strict) == 0,
        "exit_code": exit_code(checks, strict=strict),
        "summary": summarize(checks),
        "checks": [asdict(check) for check in checks],
    }


def render(checks: list[Check]) -> str:
    """人读报告（**不含任何密钥值**）。"""
    lines = ["== 环境自检 ==", f"项目根: {PROJECT_ROOT}", ""]
    for check in checks:
        lines.append(f"[{check.status:>4}] {check.name}: {check.detail}")
        if check.hint and check.status != STATUS_OK:
            lines.append(f"        → {check.hint}")
    counts = summarize(checks)
    lines.append("")
    lines.append(f"汇总: ok {counts[STATUS_OK]} / warn {counts[STATUS_WARN]} / fail {counts[STATUS_FAIL]}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """入口。

    Returns:
        int: 0 = 全部通过；1 = 有警告；2 = 有阻塞项（``--strict`` 时警告也算）。
    """
    parser = argparse.ArgumentParser(description="本机联调版环境自检（只读，不执行任何扫描）")
    parser.add_argument("--json", action="store_true", help="输出一行 JSON 而不是人读报告")
    parser.add_argument("--strict", action="store_true", help="有警告也返回退出码 2（CI 用）")
    args = parser.parse_args(argv)

    checks = collect_checks()

    if args.json:
        print(json.dumps(build_payload(checks, strict=args.strict), ensure_ascii=False))
    else:
        print(render(checks))
        code = exit_code(checks, strict=args.strict)
        if code == 0:
            print("结论: 环境就绪。")
        elif code == 1:
            print("结论: 能跑，但有上面的警告项（mock 模式不受影响）。")
        else:
            print("结论: 有阻塞项，请先按 → 的提示处理。")

    return exit_code(checks, strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
