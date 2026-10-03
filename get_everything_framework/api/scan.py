"""
模块: api/scan.py
功能: 提供扫描执行的 API 接口

路由:
  POST /api/run                   — 对指定域名/文件批量执行扫描工具
  POST /api/tool/<tool_name>/run  — 对指定域名运行单个扫描工具

调用链: API → tool_runner.load_tools() / run_tools() / run_single_tool() → SQLite 存储
"""

from flask import jsonify, request

from api import api_bp            # Flask 蓝图实例
from config import SCAN_LIMITS     # 目标数上限等资源限制
from core import uploads  # 受控上传
from core.auth import require_admin  # 本地管理员认证守卫
from core.errors import BadRequestError, ScopeViolationError
from core.mock import run_mock
from core.policy import validate_job_targets  # 统一 Policy / Scope Engine（P0-2）
from core.safety import MODE_MOCK, MODE_REAL, resolve_mode
from core.scope import Scope  # Scope 模型（返回值类型标注用）
from storage import ScanResultStore  # 扫描结果持久化存储
# 工具加载与执行的核心函数
from tool_runner import load_tools, run_single_tool, run_tools


def resolve_scoped_targets(
    *,
    scope_id: str | None,
    domain: str | None,
    upload_id: str | None,
    mode: str | None,
) -> tuple[list[str], Scope, str]:
    """解析目标并强制通过 Scope 校验（方案第 2.3 节第 2、5 条）。

    具体判定全部委托给 :mod:`core.policy`（统一 Policy / Scope Engine），
    本函数只负责「把请求字段拼成目标列表」与资源上限检查。

    Args:
        scope_id: 请求中的 scope_id，M2 起必填。
        domain: 单个目标。
        upload_id: 受控上传 ID，可提供多个目标。
        mode: ``mock`` / ``real``，缺省 mock。

    Returns:
        ``(targets, scope, mode)``：脱敏后的目标列表、Scope 对象、执行模式。

    Raises:
        BadRequestError: 缺少 scope_id、目标为空或超过单任务上限。
        ScopeViolationError: Scope 不存在，或任一目标越界。
    """
    # 真实扫描需要环境开关；mock 模式不受该开关限制。
    resolved_mode = resolve_mode(mode)

    raw_targets: list[str] = []
    if upload_id:
        raw_targets.extend(uploads.load_targets(upload_id))
    if domain:
        raw_targets.insert(0, domain)

    # 去重但保持顺序
    seen = set()
    unique_targets = []
    for target in raw_targets:
        if target not in seen:
            unique_targets.append(target)
            seen.add(target)

    if not unique_targets:
        raise BadRequestError("没有可扫描的目标")

    max_targets = SCAN_LIMITS["max_targets_per_job"]
    if len(unique_targets) > max_targets:
        raise BadRequestError(
            f"单个任务最多 {max_targets} 个目标，当前 {len(unique_targets)} 个",
            details={"max_targets_per_job": max_targets},
        )

    # 统一入口：scope_id 缺失 → 400；不存在 → 403；任一出界 → 403（整体拒绝）。
    scope, validated = validate_job_targets(scope_id, unique_targets)
    if resolved_mode == MODE_REAL:
        # 双重门槛：环境开关（上面）+ 该 Scope 自身声明允许真实扫描。
        scope.require_active_scan()
    return validated, scope, resolved_mode


def _normalize_domain(value: str | None) -> str | None:
    """
    规范化输入域名

    处理步骤:
        1. 若值为 None，直接返回 None
        2. 去除首尾空白字符
        3. 转为小写（域名大小写不敏感）
        4. 若处理结果为空字符串，返回 None

    参数:
        value: 待规范化的域名字符串，可为 None

    返回:
        str | None: 规范化后的域名字符串，或 None（输入无效时）
    """
    if value is None:
        return None
    # 去空白 + 转小写
    normalized = value.strip().lower()
    # 空字符串视为无效输入
    return normalized or None


@api_bp.route("/run", methods=["POST"])
def execute_scan():
    """
    批量扫描入口 — 对一个或多个目标执行选定的工具

    请求方式: POST
    路径: /api/run
    Content-Type: application/json

    请求体 (JSON):
        {
            "domain": "example.com",        // 目标域名（与 upload_id 二选一必填）
            "tools": ["subfinder", "httpx"], // 工具名列表（支持 "tool" 单值兼容）
            "upload_id": "upload_xxx"        // 可选：受控上传 ID（与 domain 二选一）
        }

    M2 起不再接受任意 ``file_path``：文件目标必须来自 ``POST /api/upload``
    返回的受控 ``upload_id``，防止本地任意文件被当作扫描输入读取。

    响应:
        扫描报告 JSON，包含以下字段:
        - targets: 扫描目标列表
        - tools: 实际使用的工具列表
        - total_found: 发现的结果总数
        - total_inserted: 成功写入数据库的记录数
        - runs: 每个工具的详细执行结果

    错误响应:
        - 401: 未通过本地管理员认证
        - 400: domain 与 upload_id 都未提供
        - 400: 传入了 file_path（已废弃，直接拒绝）
        - 400: upload_id 不存在或已失效
        - 400: tools 参数无效（包含不支持的工具名）

    内部逻辑:
        0. 校验本地管理员认证（M1 起所有扫描接口必须登录）
        1. 解析请求体，提取 domain/tools/upload_id 参数
        2. 规范化域名（去空白、转小写）
        3. 转换单工具字符串为列表格式
        4. 校验必须提供 domain 或 upload_id
        5. 加载并验证工具列表
        6. 执行批量扫描，返回报告
    """
    require_admin()

    # 安全解析 JSON 请求体，解析失败时返回空字典
    payload = request.get_json(silent=True) or {}
    # 提取并规范化域名
    domain = _normalize_domain(payload.get("domain"))
    # 兼容 "tools" 和 "tool" 两种参数名。
    #
    # 这里刻意用 ``in`` 判断「有没有给」，而不是 ``or``：
    # ``tools: []`` / ``tools: ""`` 是**明确的空选择**，此前会被 ``or`` 折叠成
    # ``None``，再让 ``load_tools`` 回落到配置默认值 ``["amass"]`` ——
    # 用户没选任何工具，系统却自己挑一个去跑（``docs/CODEBASE_MAP.md``
    # BUG 索引第 7 条）。现在空选择一律 400，绝不替换成别的工具。
    if "tools" in payload:
        raw_tools = payload.get("tools")
    else:
        raw_tools = payload.get("tool")
    # 受控上传 ID（M2 起唯一的文件目标入口）
    upload_id = (payload.get("upload_id") or "").strip() or None
    # 授权范围：M2 起为必填（方案第 2.3 节第 2 条）
    scope_id = (payload.get("scope_id") or "").strip() or None

    if payload.get("file_path"):
        raise BadRequestError(
            "file_path 已废弃：请先用 POST /api/upload 上传，再传 upload_id",
            details={"field": "file_path"},
        )

    # 校验：domain 和 upload_id 至少提供一个
    if not domain and not upload_id:
        raise BadRequestError("必须提供 domain 或 upload_id", details={"fields": ["domain", "upload_id"]})

    file_path = uploads.resolve_targets_file(upload_id) if upload_id else None

    # 加载并验证工具列表：``load_tools`` 负责逗号分隔字符串、去重与 registry 校验
    # （``/api/jobs`` 那条链走 ``core.application.split_str_list``，两边口径一致）。
    #
    # 关键：**显式**把「没给工具」表达成 ``[]``，而不是 ``None``。``load_tools(None)``
    # 是 CLI 语义（回落到 ``SCAN_CONFIG["enabled_runners"]``，当前是 ``amass``）；
    # 但 HTTP 请求里没写 tools，绝不等于「请用配置里的默认工具」——
    # 那正是「用户没选工具，系统自己挑了一个重的去扫」的路径。
    try:
        selected_tools = load_tools([] if raw_tools is None else raw_tools)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    if not selected_tools:
        raise BadRequestError("必须提供至少一个工具", details={"field": "tools"})

    # ── Scope 强制校验（M2 起） ─────────────────────────────
    # 没有显式 Scope 不允许创建任何扫描任务；目标在执行前必须逐个过校验。
    targets, scope, mode = resolve_scoped_targets(
        scope_id=scope_id,
        domain=domain,
        upload_id=upload_id,
        mode=payload.get("mode"),
    )

    if mode == MODE_MOCK:
        # mock 模式绝不调用真实外部工具，直接返回确定性的模拟结果。
        outcomes = [
            {
                "tool_name": tool_name,
                "target": target,
                **run_mock(tool_name, target, payload.get("scenario")).to_dict(),
            }
            for tool_name in selected_tools
            for target in targets
        ]
        return jsonify(
            {
                "ok": True,
                "mode": MODE_MOCK,
                "scope_id": scope.id,
                "targets": targets,
                "tools": selected_tools,
                "outcomes": outcomes,
            }
        )

    # ── real 模式：Scope 与环境开关均已在校验阶段确认 ──────────
    # 执行批量扫描并写入存储
    store = ScanResultStore()
    report = run_tools(domain=domain, file_path=file_path, tools=selected_tools, store=store)
    report["mode"] = MODE_REAL
    report["scope_id"] = scope.id
    return jsonify(report)


@api_bp.route("/tool/<tool_name>/run", methods=["POST"])
def execute_single_tool(tool_name: str):
    """
    运行单个工具 — 对指定域名执行某一个扫描工具

    请求方式: POST
    路径: /api/tool/<tool_name>/run
    路径参数:
        tool_name: 工具名称（如 subfinder, httpx, dnsx 等）

    请求体 (JSON):
        {
            "domain": "example.com",   // 必填：目标域名
            "scope_id": "scope_xxx",   // M2 起必填：授权范围
            "mode": "mock"             // 可选：mock（默认）/ real
        }

    错误响应:
        - 401: 未通过本地管理员认证
        - 400: domain 缺失，或未提供 scope_id
        - 403: Scope 不存在或目标越界
        - 400: tool_name 无效（不在支持的工具列表中）

    内部逻辑:
        0. 校验本地管理员认证（M1 起所有扫描接口必须登录）
        1. 解析请求体，提取并规范化域名
        2. 校验域名与 scope_id 是否提供
        3. 验证工具名是否合法
        4. 目标过 Scope 校验
        5. mock 模式直接返回模拟结果；real 模式调用真实 runner
    """
    require_admin()

    # 安全解析 JSON 请求体
    payload = request.get_json(silent=True) or {}
    # 提取并规范化目标域名
    domain = _normalize_domain(payload.get("domain"))
    scope_id = (payload.get("scope_id") or "").strip() or None

    # 校验域名必填
    if not domain:
        raise BadRequestError("domain 为必填项", details={"field": "domain"})

    # 验证工具名是否受支持
    try:
        load_tools([tool_name])
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc

    # Scope 强制校验（与 /api/run 同一套规则）
    targets, scope, mode = resolve_scoped_targets(
        scope_id=scope_id,
        domain=domain,
        upload_id=None,
        mode=payload.get("mode"),
    )

    if mode == MODE_MOCK:
        return jsonify(
            {
                "ok": True,
                "mode": MODE_MOCK,
                "scope_id": scope.id,
                "tool_name": tool_name,
                "targets": targets,
                **run_mock(tool_name, targets[0], payload.get("scenario")).to_dict(),
            }
        )

    # 创建存储实例并执行单工具扫描
    result = run_single_tool(tool_name, domain, store=ScanResultStore())
    result["mode"] = MODE_REAL
    result["scope_id"] = scope.id
    return jsonify(result)


# 供测试与内部调用复用（避免 import 私有名）。
__all__ = ["execute_scan", "execute_single_tool", "resolve_scoped_targets", "ScopeViolationError"]
