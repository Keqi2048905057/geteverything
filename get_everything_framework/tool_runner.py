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


def load_tools(cli_tools=None):
    """加载并验证要运行的工具列表。

    优先使用命令行参数，否则使用配置文件中的 enabled_runners。

    Args:
        cli_tools: 命令行传入的工具名列表。

    Returns:
        验证后的工具名列表。

    Raises:
        ValueError: 存在不支持的工具时抛出。
    """
    tools = cli_tools or SCAN_CONFIG.get("enabled_runners", [])
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
