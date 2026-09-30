# 骨架：get_everything_framework 已核实事实

> 用途：生成 `docs/CODEBASE_MAP.md` 时的起点与校对基准。
> 所有条目均来自实际读代码（提交 `d86578a` 之后的 `codex/local-mvp` 工作区）。
> 与代码冲突时以代码为准，并回来修正本文件。

## 1. 定位

资产收集 / 侦察编排框架。Flask 提供 Web API，把 20+ 个外部安全 CLI 工具（subfinder、amass、httpx、nmap…）包装成统一 Runner，结果落 SQLite，另有一个基于规则的 LLM agent 用自然语言驱动同一套 Runner。

- 启动：`app.py:165` → `app.run(host="127.0.0.1", port=5000, debug=True)`
- 项目根：`get_everything_framework/`（Git 仓库根是它的父目录）
- 依赖：`requirement.txt`；dev 依赖 `requirement-dev.txt`、`pyproject.toml`（新增，未提交）

## 2. 分层

```
HTTP
 ├── /                app.py:index()              页面路由（GET/POST 表单：scan / chat）
 └── /api/*           api/__init__.py:api_bp      Blueprint，url_prefix="/api"
        ├── api/tools.py     工具列表 / 数据库信息
        ├── api/scan.py      扫描执行
        ├── api/results.py   结果查询与导出
        ├── api/upload.py    目标文件上传
        └── api/settings.py  配置读写
编排
 └── tool_runner.py:run_tools / run_single_tool / load_targets / load_tools
适配器
 └── modules/registry.py:RUNNER_REGISTRY  →  modules/base.py:BaseRunner  →  子进程 CLI
        └── 每个工具一个 Runner 类，唯一契约是 run_scan(domain) -> list[str]
存储
 └── storage.py:ScanResultStore  →  results/scan_results.db（SQLite）
导出
 └── exporter.py:gather_export_rows / export_results  →  exports/
Agent
 └── agent/service.py:handle_agent_message → agent/action.py:AgentAction（意图→计划→工具）
```

## 3. 关键文件

| 文件 | 职责 |
|---|---|
| `app.py` | Flask 应用 + 主页路由；`template_folder="web/templates"` |
| `config.py` | 全部工具配置 `*_CONFIG`、`TOOL_CATEGORIES`、`TOOL_COMMANDS`、路径常量、`Config`（`.env`） |
| `tool_runner.py` | 目标加载、工具校验、遍历执行、落库 |
| `storage.py` | `TOOL_DATABASES` 映射 + `ScanResultStore`（建表/写入/查询） |
| `exporter.py` | 聚合导出行为 CSV/JSON |
| `target_parser.py` | `.txt/.csv/.xlsx/.json` → 归一化目标列表 |
| `modules/base.py` | `BaseRunner`：命令解析、子进程执行、输出文件读写、临时输入文件 |
| `modules/registry.py` | `RUNNER_REGISTRY` 手写字典（**非自动发现**）+ `build_runner` |
| `modules/url_tools.py` | feroxbuster / dirsearch / waybackurls 等的实际实现（`modules/feroxbuster.py` 只是 re-export） |
| `agent/action.py` | Agent 主循环：意图判定、计划确认、工具执行、回复渲染 |
| `agent/intent.py` `planner.py` `plan_state.py` | 意图识别、计划构建、待确认计划的状态迁移 |
| `agent/providers/*` | LLM provider 适配（deepseek / qwen / ollama / openai_compat） |
| `tests/` | M0 冒烟测试与仓库布局测试 |

## 4. 三条调用链

### 4.1 提交一次扫描

```
api/scan.py:execute_scan                POST /api/run，读 JSON body，_normalize_domain
  → tool_runner.py:load_tools           校验工具名 ∈ get_supported_runners()，否则 ValueError→400
  → tool_runner.py:run_tools            对「工具 × 目标」双重循环
      → load_targets                    domain / file_path / TARGET_CONFIG 兜底
      → modules/registry.py:build_runner  查 RUNNER_REGISTRY，缺失抛 ValueError
      → runner.run_scan(target)        子进程，返回 list[str]
          → modules/base.py:_execute   subprocess.run(check=True, capture_output=True, timeout=process_timeout)
          → modules/base.py:_read_results  读固定路径输出文件，按行 strip 去空
      → tool_runner.py:save_runner_results   category 取 runner.category，默认 "subdomain"
          → storage.py:save_dedicated_results
              → _create_scan_run        插入 scan_runs，拿 run_id
              → INSERT OR IGNORE INTO <tool>_results   UNIQUE(domain, <结果列>)
  → 返回 {targets, tools, total_found, total_inserted, runs[]}
```

数据形态：HTTP JSON → `list[str]` 目标 → 子进程 stdout / 输出文件（文本行）→ `list[str]` → SQLite 行。

`app.py:index()` 的表单路径不走 API，直接 `run_tools(domain, tools=["subfinder"], store)`，并且捕获 `SystemExit` 与 `Exception` 转成页面错误文案。

### 4.2 适配器加载与结果判定

- 注册：`modules/registry.py` 顶部显式 `from .xxx import XxxRunner`，再手写 `RUNNER_REGISTRY` 字典（17 项）。新增工具必须**同时**改：`registry.py`、`config.py`（`*_CONFIG` + `TOOL_CATEGORIES`，可选 `TOOL_COMMANDS`）、`storage.py:TOOL_DATABASES`（否则落库会退到通用 `tool_results` 表）。
- 执行：`BaseRunner._resolve_command` 用 `shutil.which` 解析可执行文件（绝对路径直接用；`.cmd/.bat` 走 `ComSpec /c`）；`_execute` 用 `-o` 输出文件，`_execute_stdout` 捕获 stdout 写文件。
- 成败判定：`_execute` / `_execute_stdout` 只返回 `bool`，失败原因仅 `print` 出来，**不返回 error_code**。区分不了「失败 / 超时 / 跑通但空结果」——这三者最终都表现为 `run_scan` 返回 `[]`。
- `_read_results` 只判断文件是否存在，**不判断新鲜度**：输出文件按 `md5(domain)[:12]_<tool>.txt` 固定命名，上次的残留会被当成本次结果。

### 4.3 Agent 规划链

```
agent/service.py:handle_agent_message
  → agent/action.py:AgentAction.run
      → _resolve_menu_input        数字菜单转文本
      → _handle_pending_plan       确认 / 取消 / 改计划 / 换意图
      → agent/intent.py:analyze_intent      → UserIntent（intent_type 决定分支）
      → _validate_domain          正则 + 禁 IP + blocked_domains/suffixes 白黑名单
      → _enforce_rate_limit       同一 action+domain 默认 8s 间隔（AGENT_TOOL_MIN_INTERVAL_SEC）
      → agent/planner.py:build_plan        → AgentPlan(steps=[PlanStep(tool,args)])
      → _execute_plan → _execute_tool → available_tools[action]["handler"](args)
          ├── _tool_subdomain    → tool_runner.run_tools（真扫）
          ├── _tool_httpx        → modules.httpx.HttpxRunner.run_scan + _save_httpx_metadata
          ├── _tool_view_results / _tool_summary / _tool_alive_results  只读查询
          └── _tool_export_results → exporter
      → _format_execution_summary → _build_response（message / pending_plan / steps / context_state）
```

状态回传：`app.py:index()` 把 `conversation_history`（截尾 40）、`pending_plan`、`agent_context`、`agent_steps`（截尾 50）写进 Flask session。

## 5. 数据模型

`storage.py:TOOL_DATABASES`：`tool_name → {table, column, category}`，17 项，category ∈ {subdomain, alive, web, url, port}。

```sql
scan_runs(id PK, domain, tool_name, result_count, created_at)          -- 每次 run 一行
tool_results(id PK, run_id FK, domain, tool_name, category, value, created_at,
             UNIQUE(domain, tool_name, category, value))               -- 未注册工具的兜底表
<tool>_results(id PK, run_id FK, domain, <结果列>, raw_result, created_at,
               UNIQUE(domain, <结果列>))                                -- 每工具专属表
```

- 专属表结果列按工具不同：`subdomain`（amass/subfinder/...）、`hostname`（dnsx）、`endpoint`（httpx）、`port_result`（naabu/nmap）、`url`（gospider/katana/feroxbuster/dirsearch/waybackurls）。
- 时间戳：`datetime.utcnow().isoformat(timespec="seconds") + "Z"`。
- 跨表查询：`storage.py:_query_subdomain_tables` 对所有 `category == "subdomain"` 的表做 `UNION ALL`，再 `ORDER BY value ASC`。

## 6. 已知坑（写进地图第 7 节）

| 位置 | 问题 |
|---|---|
| `app.py:26` | `template_folder="web/templates"`，但该目录在历史提交 `5853752` 被删除 → `GET /` 必 500（TemplateNotFound） |
| `modules/base.py:_execute/_execute_stdout` | 失败/超时只 `print`，无 error_code、无结构化状态回传 |
| `modules/base.py:_read_results` | 不清理旧输出文件，失败后可能读到上次残留 → 假阳性 |
| `modules/httpx.py:_build_json_output_file` | 输出文件名用原始 domain（其余 Runner 用 md5 前缀），命名不一致 |
| `modules/httpx.py:run_scan` | 只返回 `[url]`，丢弃 status_code/title/webserver/tech/cdn；而 `agent/action.py:_tool_httpx`→`_summarize_httpx_items` 按 dict 取字段 |
| `agent/action.py:_get_storage_tables` | 声称写 `subdomain_results` / `alive_results`，`TOOL_DATABASES` 里并不存在这些表 |
| `config.py` | `TARGET_CONFIG.domains = ["nfl.com"]`、`SCAN_CONFIG.enabled_runners = ["amass"]`：无参调用会打真实外网目标 |
| `config.py:FEROXBUSTER_CONFIG` | wordlist 硬编码 `D:/c4/v2/backend/framework-main/SecLists/...`，与本仓库 `SecLists/` 不符 |
| `config.py:HTTPX_CONFIG` | `path = os.getenv("HTTPX_PATH", "http-x")`，默认值不是 `httpx` |
| `storage.py:_get_connection` | 每个方法新建连接、`with conn` 只提交不关闭 → 连接/句柄泄漏；无 WAL、无 `busy_timeout` → 并发写易 `database is locked` |
| `api/scan.py:execute_scan` | 未捕获 `run_tools` 抛出的 `SystemExit`；且整个扫描同步阻塞在请求线程内 |
| `api/*` | 全部接口无鉴权；无 Scope/授权范围校验（只校验 domain 非空与工具名合法） |
| `target_parser.py:IP_PATTERN` | 纯正则，`999.999.999.999` 也会被当作合法目标 |
| `agent/action.py:RATE_LIMIT_CACHE` | 类级可变字典，跨请求/跨 Agent 实例共享 |
