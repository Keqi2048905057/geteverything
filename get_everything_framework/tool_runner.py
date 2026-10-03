"""工具执行编排模块 — 负责加载目标、选择工具、调度扫描并持久化结果。

提供 load_targets、load_tools、run_tools、run_single_tool 四个核心函数。

M4 起：编排层改走 ``BaseRunner.run()``（方案第 8.1 节统一接口），因此

* 失败 / 超时 / 零结果不再被压成同一个空列表，``runs`` 里带上
  ``status`` / ``error_code`` / ``duration_ms`` / ``command_preview``；
* 落库仍然只写值列表（``RunnerResult.values``），旧库结构与导出逻辑不变。
"""

from config import SCAN_CONFIG, TARGET_CONFIG
from modules import build_runner, get_supported_runners
from storage import ScanResultStore


def load_targets(domain=None, file_path=None):
    """加载扫描目标列表。

    优先级：命令行参数 > 配置文件。

    Args:
        domain: 单个目标域名（命令行传入）。
        file_path: 包含目标列表的文件路径（命令行传入）。

    Returns:
        去重后的目标域名列表。
    """
    targets = []

    # 命令行传入的单个域名
    if domain:
        targets.append(domain.strip())

    # 命令行传入的目标文件
    if file_path:
        with open(file_path, "r", encoding="utf-8") as f:
            targets.extend([line.strip() for line in f if line.strip()])

    # 若命令行未提供目标，回退到配置文件
    if not targets:
        targets.extend(TARGET_CONFIG.get("domains", []))

        config_file = TARGET_CONFIG.get("domain_file")
        if config_file:
            with open(config_file, "r", encoding="utf-8") as f:
                targets.extend([line.strip() for line in f if line.strip()])

    # 去重并保持顺序
    unique_targets = []
    seen = set()
    for target in targets:
        if target not in seen:
            unique_targets.append(target)
            seen.add(target)

    return unique_targets


def normalize_tool_names(value):
    """把「工具」参数规范化成**去重保序**的工具名列表。

    ``/api/run``、``/api/tool/<n>/run``、``POST /api/jobs``（经 ``core.application``）
    与 CLI 全都走它，避免出现「同一个请求体从两个入口进来得到两种解释」。

    **不是全仓唯一一份「列表规范化」实现**（此前这句写得过头了，实测打脸）：
    ``core/application.py:95 split_str_list()`` 是另一份，服务对象是
    ``targets`` / ``tools`` 这类请求字段。两者**不是同一个函数**，差别有二：
    它不去重（去重由 :func:`load_tools` 与 ``resolve_targets`` 各自收口），
    且它对非法类型抛 ``BadRequestError`` 而不是 ``ValueError``。
    端到端行为一致的原因不是「只有一份实现」，而是**去重与 registry 校验
    只有一个收口点**（:func:`load_tools`）。改其中一处时，别忘了另一处。

    接受的形态：

    * ``None`` / 空 → ``[]``（由调用方决定「空」是报错还是回落配置）；
    * 逗号分隔字符串 ``"subfinder,httpx"`` → ``["subfinder", "httpx"]``：
      **修复**了此前把它当成一个名叫 ``"subfinder,httpx"`` 的工具、
      于是必然报「存在不支持的工具」的老毛病；
    * 字符串数组 / 元组 → 逐项去空白、丢空项。

    去重是必需的：``total_steps = len(targets) * len(tools)``，
    同一个工具写两遍会让任务凭空多出一倍步骤（且重复执行同一工具）。

    Args:
        value: ``None`` / 字符串 / 字符串数组。

    Returns:
        list[str]: 去重保序的工具名列表。

    Raises:
        ValueError: ``value`` 既不是字符串也不是数组（例如数字、字典）。
    """
    if value is None:
        items: list = []
    elif isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise ValueError("tools 必须是字符串或字符串数组")

    unique_tools = []
    seen = set()
    for item in items:
        name = str(item).strip()
        if not name or name in seen:
            continue
        unique_tools.append(name)
        seen.add(name)
    return unique_tools


def load_tools(cli_tools=None):
    """加载并验证要运行的工具列表。

    未显式提供工具（``cli_tools is None``）时回落到配置里的
    ``SCAN_CONFIG["enabled_runners"]`` —— 这是 CLI/Agent 的历史行为。

    **显式传空**（``[]`` 或 ``""``）不再回落：那正是「用户没选任何工具，
    系统却拿配置默认值去扫」的越权路径（``docs/CODEBASE_MAP.md`` 第 6 节
    BUG 索引第 7 条）。此时返回空列表，由调用方明确拒绝或提示，
    而不是悄悄换一个工具去执行。

    Args:
        cli_tools: 工具名列表、逗号分隔字符串；``None`` 表示未指定。

    Returns:
        验证并去重后的工具名列表。

    Raises:
        ValueError: 参数形态非法，或存在不支持的工具名时抛出。
    """
    if cli_tools is None:
        tools = normalize_tool_names(SCAN_CONFIG.get("enabled_runners", []))
    else:
        tools = normalize_tool_names(cli_tools)

    supported_tools = set(get_supported_runners())
    invalid_tools = [tool for tool in tools if tool not in supported_tools]
    if invalid_tools:
        raise ValueError(f"存在不支持的工具: {', '.join(invalid_tools)}")
    return tools


def save_runner_results(store, domain, runner, results):
    """将 runner 执行结果持久化到数据库。

    Args:
        store: ScanResultStore 实例。
        domain: 目标域名。
        runner: 工具 runner 实例（需有 tool_name 和可选的 category 属性）。
        results: 扫描结果列表。

    Returns:
        save_dedicated_results 的返回值字典。
    """
    # 默认分类为 subdomain，runner 可通过 category 属性覆盖
    category = getattr(runner, "category", "subdomain")
    tool_name = runner.tool_name
    return store.save_dedicated_results(domain, tool_name, category, results)


def run_tools(domain=None, file_path=None, tools=None, store=None):
    """批量运行多个工具对多个目标进行扫描。

    完整的编排流程：加载目标 → 验证工具 → 遍历执行 → 持久化。

    Args:
        domain: 单个目标域名。
        file_path: 目标列表文件路径。
        tools: 工具名列表。
        store: ScanResultStore 实例，默认自动创建。

    Returns:
        字典包含 targets、tools、total_found、total_inserted、runs。
    """
    targets = load_targets(domain, file_path)
    if not targets:
        print("\n[!] 未提供目标，且 config.py 中 TARGET_CONFIG 也为空")
        raise SystemExit(1)

    try:
        selected_tools = load_tools(tools)
    except ValueError as exc:
        print(f"[!] {exc}")
        raise SystemExit(1) from exc

    if not selected_tools:
        print("[!] 未配置任何工具，请检查 config.py 中 SCAN_CONFIG['enabled_runners']")
        raise SystemExit(1)

    store = store or ScanResultStore()
    print(f"--- 任务开始，工具: {', '.join(selected_tools)}，共 {len(targets)} 个目标 ---")
    total_found = 0
    total_inserted = 0
    run_details = []

    # 按工具 → 目标的顺序遍历（每个工具对所有目标执行一遍）
    for tool_name in selected_tools:
        runner = build_runner(tool_name)
        tool_total = 0
        tool_inserted = 0
        print(f"\n=== 开始执行工具: {tool_name} ===")

        for target in targets:
            result = runner.run(target)
            results = result.values
            save_summary = save_runner_results(store, target, runner, results)
            # 失败/超时不再伪装成「零结果」：状态与错误码一并回报。
            detail_line = f"[+] [{tool_name}] {target} 执行完成，发现 {len(results)} 条结果，新增入库 {save_summary['inserted_count']} 条"
            if result.error_code:
                detail_line += f"（status={result.status}, error_code={result.error_code}）"
            print(detail_line)
            tool_total += len(results)
            tool_inserted += save_summary["inserted_count"]
            run_details.append(
                {
                    "domain": target,
                    "tool_name": tool_name,
                    "category": getattr(runner, "category", "subdomain"),
                    "found_count": len(results),
                    "inserted_count": save_summary["inserted_count"],
                    "run_id": save_summary["run_id"],
                    "status": result.status,
                    "error_code": result.error_code,
                    "error_message": result.error_message,
                    "duration_ms": result.duration_ms,
                    "command_preview": result.command_preview,
                }
            )

        total_found += tool_total
        total_inserted += tool_inserted
        print(
            f"=== 工具 {tool_name} 执行完成，累计发现 {tool_total} 条结果，"
            f"新增入库 {tool_inserted} 条 ==="
        )

    print(
        f"--- 所有任务已完成，累计发现 {total_found} 条结果，"
        f"新增入库 {total_inserted} 条 ---"
    )

    return {
        "targets": targets,
        "tools": selected_tools,
        "total_found": total_found,
        "total_inserted": total_inserted,
        "runs": run_details,
    }


def run_single_tool(tool_name, domain, store=None):
    """运行单个工具对单个目标进行扫描。

    适用于外部调用（如 agent）的单次扫描场景。

    Args:
        tool_name: 工具名称。
        domain: 目标域名。
        store: ScanResultStore 实例，默认自动创建。

    Returns:
        字典包含 domain、tool_name、category、found_count、inserted_count、
        run_id、results，以及 M4 新增的 status / error_code / error_message /
        duration_ms / command_preview / observations。
    """
    selected_tools = load_tools([tool_name])
    runner = build_runner(selected_tools[0])
    store = store or ScanResultStore()
    result = runner.run(domain)
    results = result.values
    save_summary = save_runner_results(store, domain, runner, results)

    return {
        "domain": domain,
        "tool_name": tool_name,
        "category": getattr(runner, "category", "subdomain"),
        "found_count": len(results),
        "inserted_count": save_summary["inserted_count"],
        "run_id": save_summary["run_id"],
        "results": results,
        # M4：失败 / 超时 / 零结果分开回报（方案第 6.2 节）。
        "status": result.status,
        "error_code": result.error_code,
        "error_message": result.error_message,
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "command_preview": result.command_preview,
        "parser_version": result.parser_version,
        "observations": [item.to_dict() for item in result.data],
    }
