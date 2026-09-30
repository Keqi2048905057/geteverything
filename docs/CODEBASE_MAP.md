# get_everything_framework 代码地图

> 本文档基于对项目源码的**逐文件实际阅读**产出（读取范围：项目根 + `api/` + `modules/` + `agent/` + `tests/` 下全部 `.py`，以及 `README.md`、`requirement.txt`、`pyproject.toml`、`.github/workflows/ci.yml`、旧结果库 `results/scan_results.db` 的真实表结构）。
> 文档中出现的行号/函数名均来自实际文件内容；凡代码与 README 不一致处，一律以代码为准并显式标注。
> 项目根：`<仓库根>/get_everything_framework`（Git 仓库根是其上一级目录；文档不再写死本机绝对路径）。
> 凡提到「设计文档/方案」的地方，指的是开发机上的本机联调过程材料 —— 那两份文档
> **不随仓库分发**，此处仅保留历史引用以说明当时的依据来源。
>
> **last-mapped：本机联调版 @ M4 + P0 加固 + P1（资产/观测/Diff/迁移，含前端对比）+ M7（mypy 清零、Diff 可点）+ M5 字典可移植性（2026-10-02）**
> 第 1～8 节记录的是改动前的**原仓库基线**（主线 `main` / `d86578a`），仍然准确描述 `modules/`、`agent/`、`storage.py` 与旧库结构；
> **第 9 节**记录本机联调版新增/改写的部分（M0→M4 及之后的 P0 加固）。两者冲突时，第 9 节更新。

---

## 1. 项目一句话定位与技术栈

**一句话定位**：一个**同步阻塞式**的资产收集编排框架 —— Flask 暴露 REST API，`modules/` 里每个外部安全工具封装成一个 `BaseRunner` 子类，`tool_runner.py` 串行调度它们，结果写进单个 SQLite 文件；另有一套**基于正则规则的对话式 Planner**（被命名为 "LLM Agent"）用于把自然语言转成固定的几个工具步骤。

### 技术栈（全部来自实际文件，非推测）

| 项 | 依据 | 结论 |
|---|---|---|
| 语言/版本 | `pyproject.toml` `target-version = "py310"`；CI 用 `python-version: "3.11"` | Python 3.10+，CI 实测 3.11 |
| Web 框架 | `app.py:17` `from flask import Flask, render_template, request, session`；`requirement.txt:8` `Flask==3.1.3` | Flask 3.x + Werkzeug 3.1.8 + Jinja2 3.1.6 |
| 运行依赖 | `requirement.txt`（26 行，全量钉版本） | `Flask` `python-dotenv` `openai==2.38.0` `openpyxl==3.1.5` `httpx==0.28.1` `tqdm` `pydantic` |
| 开发依赖 | `requirement-dev.txt` | `pytest>=7.0` `ruff>=0.5` `mypy>=1.10` |
| 存储 | `config.py:87` `SQLITE_CONFIG = {"path": .../results/scan_results.db}`；`storage.py:7` `import sqlite3` | SQLite（标准库，无 ORM） |
| 配置 | `config.py:6` `load_dotenv(os.path.join(_BASE_DIR, ".env"))` | `.env` + `python-dotenv`；`.env.example` 提供模板 |
| 外部工具 | `modules/*.py` 里 `subprocess.run` | 17 个 Go/Python/EXE 外部扫描器 |
| 前端 | `app.py:26` `template_folder="web/templates"`，`app.py:160` `render_template("index.html")` | **仓库中不存在 `web/`、`templates/`、`static/` 目录**（实测 `Test-Path` 全部 False）；README:484 也承认是占位文件，因此 `/` 路由目前必然 `TemplateNotFound` |
| 启动方式 | `app.py:165-166` `if __name__ == "__main__": app.run(host="127.0.0.1", port=5000, debug=True)` | `python app.py` → http://127.0.0.1:5000 ；`debug=True` 写死 |
| Agent CLI | `agent_cli.py:17` `agent = AgentAction(debug=True)` | `python agent_cli.py` 进 REPL |
| CI | `.github/workflows/ci.yml` | `ruff check .` + `pytest -q`，工作目录 `get_everything_framework` |

**README 与代码的明确不符**（以代码为准）：
1. README:279 宣称 `nuclei` 可通过 Agent 调用 —— 代码里 `modules/registry.py` 无 nuclei，`agent/action.py` 的 `available_tools` 也没有。
2. README:402 说 registry 有 "17 个工具" —— 与 `RUNNER_REGISTRY` 的 17 个键一致 ✅。
3. README:361 说 `exporter.py` 导出 "CSV / JSON / TXT" —— 实现只支持 `csv` / `json`，其他格式抛 `ValueError`（`exporter.py:116`）。
4. README:329-333 声称存在 `web/templates/index.html`、`static/` —— 实测两个目录都不存在；README:345 还写着 `venv/`、README:336 写着 `exports/`，前者也不存在。
5. README:44 与 README:479 把 Agent 描述为"DeepSeek 等大模型接入" —— 运行时完全不调用模型（见 §4.c）。
6. README:290 说 `target_parser.py` 支持 4 种格式 —— 与实现一致 ✅（`.txt/.csv/.xlsx/.json`）；README 未提"非法目标静默丢弃"。
7. README:408 提到 `results/tmp*_httpx_input.txt` 会被自动删除 —— 实际 `modules/base.py:_write_input_file` 生成的文件名后缀是 `_{domain}_{tool}_input.txt`（`:204`），前缀不是 `tmp`，且基类版本无人调用（子类各自重写）。

### 1.1 全部 HTTP 路由清单（实测 `api_bp` + 页面路由）

| 方法 | 路径 | 处理函数 | 入参形态 | 出参形态 | 鉴权 |
|---|---|---|---|---|---|
| GET/POST | `/` | `app.py:index()` | form（`domain` / `action` / `agent_message`） | HTML 模板（**当前必然 500**） | 无 |
| GET | `/api/tools` | `api/tools.py:list_tools()` | — | `{"tools":[{name,category,database}]}` | 无 |
| GET | `/api/databases` | `api/tools.py:list_databases()` | — | `{"databases":[...]}` | 无 |
| POST | `/api/run` | `api/scan.py:execute_scan()` | JSON `{domain?, tools?, tool?, file_path?}` | 扫描 report dict | 无 |
| POST | `/api/tool/<tool_name>/run` | `api/scan.py:execute_single_tool()` | JSON `{domain}` | 单工具结果 dict | 无 |
| GET | `/api/results` | `api/results.py:query_results()` | query `domain/tool/category/limit` | `{"results":[...]}` | 无 |
| GET | `/api/tool/<tool_name>/results` | `api/results.py:query_tool_results()` | query `domain/limit` | `{"results":[...]}` 或 400 | 无 |
| GET | `/api/export` | `api/results.py:export_data()` | query `domain/tool/category/format/limit` | `{ok,path,count,format}` | 无 |
| POST | `/api/upload` | `api/upload.py:upload_file()` | multipart `file` | `{ok,file_path,target_count,targets_preview}` | 无 |
| GET | `/api/settings` | `api/settings.py:get_settings()` | — | `{ok,settings:{llm,search,enscan}}`（Key 已掩码） | 无 |
| POST | `/api/settings` | `api/settings.py:save_settings()` | JSON 白名单字段 | `{ok,message,updated_fields}` | 无 |
| GET | `/api/settings/enscan` | `api/settings.py:get_enscan_cookies()` | — | `{ok,config_path,cookies}` | 无 |
| POST | `/api/settings/enscan` | `api/settings.py:save_enscan_cookies()` | JSON `enscan_{aqc,tyc,icp}_cookie` | `{ok,config_path,message}` | 无 |

> 共 13 条路由（1 页面 + 12 API），**没有任何认证装饰器、没有 CSRF、没有 Scope 校验**。README:43 也写成"12 个 RESTful 接口"，与实测一致 ✅。

---

## 2. 分层架构图

```
┌─ 入口层 ────────────────────────────────────────────────────────────────┐
│  app.py:index()            GET/POST /          （同步表单扫描 subfinder）│
│  agent_cli.py:main()       CLI REPL            （AgentAction 多轮对话）  │
│  api/__init__.py           api_bp = Blueprint("api", url_prefix="/api")  │
└───────────────┬─────────────────────────────────────────────────────────┘
                │ 请求体: dict (JSON) / form / multipart
┌───────────────▼─ 路由层 api/ ───────────────────────────────────────────┐
│  tools.py   GET  /api/tools        /api/tables出参  → dict              │
│  scan.py    POST /api/run          /api/tool/<name>/run                 │
│  results.py GET  /api/results      /api/tool/<name>/results  /api/export│
│  upload.py  POST /api/upload       → target_parser → uploads/*.txt      │
│  settings.py GET/POST /api/settings        /api/settings/enscan         │
│  …无任何鉴权、无 Scope 校验、无任务队列（全部同步执行）                  │
└───────────────┬─────────────────────────────────────────────────────────┘
                │ 参数 dict[str, Any]
┌───────────────▼─ 编排层（根目录） ──────────────────────────────────────┐
│  tool_runner.load_targets()  → list[str] 目标                          │
│  tool_runner.load_tools()    → list[str] 工具名（校验 registry）        │
│  tool_runner.run_tools()     → 双层 for（工具 × 目标）串行执行          │
│  tool_runner.run_single_tool()                                          │
│  target_parser.parse_targets_file() → list[str]（上传目标归一化）       │
└───────────────┬─────────────────────────────────────────────────────────┘
                │ build_runner(tool_name) → BaseRunner 实例
┌───────────────▼─ 适配器层 modules/ ─────────────────────────────────────┐
│  registry.py  RUNNER_REGISTRY（17 项，import 期全部加载）               │
│  base.py      BaseRunner._resolve_command / _execute / _execute_stdout  │
│               _build_output_file / _read_results / _write_input_file    │
│  17 个 Runner：subfinder assetfinder amass amass_intel oneforall alterx  │
│               shuffledns dnsx httpx naabu nmap gospider katana           │
│               waybackurls feroxbuster dirsearch enscan                  │
└───────────────┬─────────────────────────────────────────────────────────┘
                │ subprocess.run(...)  → 外部进程；产物 = results/<md5>_<tool>.txt
┌───────────────▼─ 工具层（进程外） ──────────────────────────────────────┐
│  subfinder / assetfinder / amass / dnsx / httpx / naabu / nmap /        │
│  katana / gospider / waybackurls / feroxbuster / dirsearch / enscan /   │
│  oneforall(python)   —— 通过 PATH 查找，config.py 只给裸命令名          │
└───────────────┬─────────────────────────────────────────────────────────┘
                │ list[str]（读输出文件后的行列表）
┌───────────────▼─ 存储层 storage.py ─────────────────────────────────────┐
│  ScanResultStore.save_dedicated_results() → scan_runs + <tool>_results  │
│  TOOL_DATABASES：17 个工具的「表名 / 结果列名 / 分类」映射              │
└───────────────┬─────────────────────────────────────────────────────────┘
                │ SQLite 行 (tuple)
┌───────────────▼─ 输出层 ────────────────────────────────────────────────┐
│  exporter.gather_export_rows() / export_results() → exports/*.csv|json  │
│  api/results.py → jsonify({"results": [...]})                          │
│  storage.get_*_summary / get_view_* → 页面 & Agent 文本渲染            │
└─────────────────────────────────────────────────────────────────────────┘

旁路（与上主链解耦，且**不经过任何模型**）：
  app.py:118 action == "chat" → agent/service.handle_agent_message()
    → agent/action.AgentAction.run()
      → agent/intent.analyze_intent()   （正则关键词）
      → agent/planner.build_plan()      （模板拼装）
      → AgentAction._execute_tool()     → 复用 tool_runner.run_tools / storage / exporter
```

**关键结论（与任务描述的差异）**：任务书描述的 "LLM 规划 agent" 在运行时**不会调用任何大模型**。`AgentAction.__init__` 接收 `client` 参数但只赋值给 `self.client` 后从不使用（`agent/action.py:52`、grep 显示 `.chat(` 只出现在 `agent/client.py:34` 与 `agent/providers/openai_compat.py` 内部）。`SYSTEM_PROMPT`（含 skills）只被塞进 `self.conversation_history[0]`（`agent/action.py:776`），从未发送给 provider。规划完全由 `agent/intent.py` 的关键词 + `agent/planner.py` 的固定模板决定。

---

## 3. 模块职责表

### 3.1 根目录

| 文件 | 职责 | 关键类/函数 | 被谁调用 | 依赖谁 |
|---|---|---|---|---|
| `app.py` | Flask 应用实例 + 页面路由 + Blueprint 注册 + 页面上下文构建 | `app`、`index()`、`normalize()`、`normalize_domain`、`build_page_context()`、`_to_ui_history()` | WSGI/`python app.py`；`tests/unit/test_smoke.py:21` | `agent`、`config`、`storage`、`tool_runner`、`api`、flask |
| `config.py` | 全局配置：`Config` 类（从 `.env` 读取）、路径常量、17 个 `*_CONFIG` 工具配置、分类表 | `Config`、`Config.to_dict()`、`Config._mask()`、`build_tool_config()`、`OUTPUT_DIR`、`SQLITE_CONFIG`、`TARGET_CONFIG`、`SCAN_CONFIG`、`TOOL_CATEGORIES`、`TOOL_COMMANDS` | 几乎全部模块 | `dotenv`、`os` |
| `storage.py` | SQLite 数据访问层（建表 + 写入 + 多维查询） | `TOOL_DATABASES`、`ScanResultStore`（`_init_db`、`_get_connection`、`save_dedicated_results`、`save_tool_results`、`save_results`、`get_dedicated_results`、`get_tool_results`、`get_view_results`、`get_alive_results`、`get_tool_database_overview` 等） | `api/*`、`tool_runner`、`exporter`、`agent/action`、`modules/dnsx|httpx|alterx|shuffledns` | `sqlite3`、`config` |
| `tool_runner.py` | 编排核心：加载目标 → 校验工具 → 双层循环执行 → 落库 | `load_targets()`、`load_tools()`、`save_runner_results()`、`run_tools()`、`run_single_tool()` | `app.py:110`、`api/scan.py:105,159`、`agent/action.py:379,396` | `config`、`modules`、`storage` |
| `target_parser.py` | 目标文件解析与归一化（去协议/去尾点/域名与 IP 校验） | `normalize_target()`、`parse_targets_file()`、`_parse_txt/_parse_csv/_parse_json/_parse_xlsx()`、`save_normalized_targets()`、`DOMAIN_PATTERN`、`IP_PATTERN` | `api/upload.py:56,60` | `csv`/`json`/`re`/`urlparse`、`openpyxl`（延迟导入） |
| `exporter.py` | 结果聚合与落盘导出 | `ensure_export_dir()`、`gather_export_rows()`、`export_results()` | `api/results.py:138,241`、`agent/action.py:492,493` | `csv`/`json`、`config`、`storage` |
| `agent_cli.py` | Agent 终端 REPL 入口 | `main()` | `python agent_cli.py` | `agent.AgentAction` |

> 模块级导入副作用提醒：`import storage` 只 import `config`（不建库）；`ScanResultStore()` 才 `_init_db`。但 `import agent`（`agent/__init__.py`）会连带 import `action.py` → `modules.httpx.HttpxRunner` → `storage`，而 `import app`（`app.py:19`）又 import agent，因此**只要启动 Flask 就已经把 LLM 相关模块全部加载**（尽管它们不被使用）。


### 3.2 `api/`（全部无鉴权、无 Scope、无队列）

| 文件 | 职责 | 关键函数 | 被谁调用 | 依赖谁 |
|---|---|---|---|---|
| `api/__init__.py` | 创建并导出 `api_bp`，末尾 import 5 个子模块以完成路由注册 | `api_bp` | `app.py:31` | flask |
| `api/tools.py` | `GET /api/tools`、`GET /api/databases` | `_build_tool_payload()`（内部 `build_runner` 实例化 17 个 Runner）、`list_tools()`、`list_databases()` | 前端/curl | `modules`、`storage` |
| `api/scan.py` | `POST /api/run`（批量）、`POST /api/tool/<name>/run`（单工具） | `_normalize_domain()`、`execute_scan()`、`execute_single_tool()` | 前端/curl | `tool_runner`、`storage` |
| `api/results.py` | 结果查询与导出 | `_normalize_domain()`、`_normalize_value()`、`_parse_limit()`、`query_results()`、`query_tool_results()`、`export_data()` | 前端/curl | `storage`、`exporter` |
| `api/upload.py` | 上传目标文件 → 归一化 txt，结果写 Flask `session` | `upload_file()`、`ALLOWED_EXTENSIONS` | 前端/curl | `target_parser`、`config.UPLOAD_DIR`、`werkzeug.utils.secure_filename` |
| `api/settings.py` | `.env` 与 enscan `config.yaml` 的读写 | `_read_env_file()`、`_write_env_file()`、`_ensure_enscan_config()`、`_read_enscan_yaml()`、`_write_enscan_yaml()`、`get_settings()`、`save_settings()`、`get_enscan_cookies()`、`save_enscan_cookies()`、`ALLOWED_KEYS`、`KEY_MAPPING` | 前端/curl | `config.Config`、`re`、`pathlib` |

### 3.3 `modules/`（适配器层）

**约定（`base.py` + `registry.py`）**
- `modules/base.py:BaseRunner.__init__(config, tool_name)`：从 `config` 取 `category` 写入 `self.category`，`self.output_dir = OUTPUT_DIR`。
- `_resolve_command(cmd)`（`modules/base.py:80`）：相对名走 `shutil.which`，找不到抛 `FileNotFoundError`；`.bat/.cmd` 自动用 `ComSpec /c` 包裹。
- `_execute(cmd, domain)`（`modules/base.py:109`）：`subprocess.run(check=True, capture_output=True, text=True, timeout=config.get("process_timeout"))`，工具自己用 `-o` 写文件；**任何异常都只 `print` + `return False`**。
- `_execute_stdout(cmd, domain, output_file)`（`modules/base.py:146`）：同上，但把 `completed.stdout` 写进 `output_file`。
- `_build_output_file(domain)`（`modules/base.py:46`）：`md5(domain)[:12] + "_" + tool_name + ".txt"` 落在 `results/`。
- `_read_results(output_file)`（`modules/base.py:62`）：文件不存在返回 `[]`，否则返回去空白后的非空行列表。
- `_write_input_file(domain, values)`（`modules/base.py:186`）：在 `results/` 建 `delete=False` 临时文件，**调用方负责删除**。
- `modules/registry.py:RUNNER_REGISTRY`（dict，17 键）→ `get_supported_runners()` 返回排序键列表；`build_runner(tool_name)` 无参实例化，未注册抛 `ValueError`。
- `modules/__init__.py` 只重导出 `build_runner`、`get_supported_runners`。

> **本节描述的是 M0 基线的 `base.py`。** M4 起执行路径改过了，读代码时以这几条为准：
> `_run_subprocess`（`Popen` + `communicate(timeout)` + 进程树清理）取代了
> `subprocess.run(timeout=...)`；`_execute` / `_execute_stdout` 在跑之前先删同名旧输出文件；
> 每个 runner 另有 `build_command` / `parse_output` / `run` 三个方法，
> 失败不再降级成 `return False`，而是带上 `error_code`（详见 §9.10）。
> 表里「实际命令」一列仍然准确——`build_command` 拼出来的就是这些。

| 文件 | 职责 | 关键类 | 对应外部工具（实际命令） | 被谁调用 | 依赖谁 |
|---|---|---|---|---|---|
| `modules/base.py` | 所有 Runner 基类：命令解析/执行/读写/临时文件 | `BaseRunner` | —（无） | 17 个 Runner 继承 | `config.OUTPUT_DIR`、`subprocess`、`shutil`、`hashlib`、`tempfile` |
| `modules/registry.py` | 静态注册表 + 工厂 | `RUNNER_REGISTRY`、`get_supported_runners()`、`build_runner()` | — | `modules/__init__`、`tool_runner`、`api/tools.py` | 17 个 adapter 模块（import 期即加载） |
| `modules/subfinder.py` | 被动子域枚举 | `SubfinderRunner` | `subfinder -d <d> -t 50 -o <f> [-timeout N] -silent` | registry | base、`SUBFINDER_CONFIG` |
| `modules/assetfinder.py` | 公开数据源查子域（stdout 捕获 + 正则规范化） | `AssetfinderRunner` | `assetfinder --subs-only <d>` | registry | base、`ASSETFINDER_CONFIG` |
| `modules/amass.py` | 深度枚举 + ASN 反查（两个 Runner） | `AmassRunner`、`AmassIntelRunner` | `amass enum -d <d> -o <f>`；`amass intel -asn <n> -o <f>` | registry | base、`AMASS_CONFIG`/`AMASS_INTEL_CONFIG` |
| `modules/oneforall.py` | 综合 Python 工具（stdout 捕获） | `OneForAllRunner` | `oneforall --target <d> run` | registry | base、`ONEFORALL_CONFIG` |
| `modules/alterx.py` | 由库内已有子域生成变体 | `AlterxRunner` | `alterx -l <tmp> -o <f>` | registry | base、`storage.ScanResultStore`、`ALTERX_CONFIG` |
| `modules/shuffledns.py` | 「混合模式」爆破 + 已有子域验证 + 泛解析过滤 | `ShufflednsRunner`（`_DISCOVERY_TOOLS`、`_WILDCARD_CACHE`、`_bruteforce_with_dnsx`、`_resolve_dnsx`、`_detect_wildcard_ips`、`_load_existing_candidates`、`run_scan`） | **不是 `shuffledns` 二进制**，内部硬编码调用 `dnsx`（`modules/shuffledns.py:89,109,147`） | registry | base、`storage`、`subprocess`、`SHUFFLEDNS_CONFIG` |
| `modules/dnsx.py` | DNS 批量解析验证（从库里取候选） | `DnsxRunner`（`_load_candidates`、`_write_input_file`、`run_scan`） | `dnsx -l <tmp> -o <f> -t 50 -silent -resp-only` | registry | base、`storage`、`DNSX_CONFIG` |
| `modules/httpx.py` | HTTP 存活探测 + JSONL 解析 | `HttpxRunner`（`_load_candidates`、`_write_input_file`、`_build_json_output_file`、`_read_json_results`、`run_scan`） | `<HTTPX_PATH or "http-x"> -l <tmp> -o <f> -json -threads 50 [-timeout 10] -silent -title -status-code -web-server -cdn [-tech-detect]` | registry、`agent/action.py:457`（直接 import 使用） | base、`storage`、`HTTPX_CONFIG` |
| `modules/url_tools.py` | 5 个 URL/目录类 Runner 的**真实实现** | `GospiderRunner`、`KatanaRunner`、`WaybackurlsRunner`、`FeroxbusterRunner`（`_parse_ferox_json`）、`DirsearchRunner`、`build_url()` | `gospider -s <url> -d 2`；`katana -u <url> -d 2 -o <f>`；`waybackurls <d>`（stdout）；`feroxbuster -u <url> -o <f> --json -w <wordlist>`；`dirsearch -u <url> -o <f>` | registry（经 4 个薄壳模块） | base、5 个 URL/目录 CONFIG |
| `modules/gospider.py` / `katana.py` / `waybackurls.py` / `dirsearch.py` / `feroxbuster.py` | 纯重导出薄壳，无逻辑 | —（仅 `from .url_tools import XRunner`） | 同上 | registry | `url_tools` |
| `modules/port_tools.py` | 端口扫描两兄弟的**真实实现** | `NaabuRunner`、`NmapRunner` | `naabu -host <d> -o <f> -silent`；`nmap [-p N] -oN <f> <d>` | registry（经 naabu.py / nmap.py） | base、`NAABU_CONFIG`/`NMAP_CONFIG` |
| `modules/naabu.py` / `nmap.py` | 纯重导出薄壳 | — | 同上 | registry | `port_tools` |
| `modules/enscan.py` | 企业信息收集，产物走「运行前后 glob 新 JSON 文件」 | `ENScanRunner`（`_parse_json_output`、`build_command`、`parse_output`、`run_scan`） | `enscan -n <keyword> -json`（`cwd=results/`，经 `_run_subprocess`） | registry | base、`glob`、`ENSCAN_CONFIG` |

### 3.4 `agent/`

| 文件 | 职责 | 关键类/函数 | 被谁调用 | 依赖谁 |
|---|---|---|---|---|
| `agent/__init__.py` | 导出 `AgentAction`、`handle_agent_message` | — | `app.py:19`、`agent_cli.py:6` | `.action`、`.service` |
| `agent/service.py` | Web 侧单次对话门面 | `handle_agent_message()` | `app.py:128` | `AgentAction`、`storage` |
| `agent/action.py` | **Agent 全部行为**：对话状态、意图分发、计划执行、工具handler、文本渲染、域名校验、限流 | `AgentAction`（`run()`、`_handle_pending_plan()`、`_execute_plan()`、`_execute_tool()`、`_tool_subdomain/_tool_summary/_tool_view_results/_tool_alive_results/_tool_httpx/_tool_export_results`、`_validate_domain()`、`_enforce_rate_limit()`、`_save_httpx_metadata()`、`available_tools`、`RATE_LIMIT_CACHE`、`DOMAIN_PATTERN`） | `service.handle_agent_message`、`agent_cli.main` | `exporter`、`modules.httpx.HttpxRunner`、`storage`、`tool_runner`、`.intent/.plan_state/.planner/.system_prompt/.target_ranker` |
| `agent/intent.py` | 纯正则关键词意图识别 | `UserIntent`、`analyze_intent()`、`extract_domain()`、`extract_org_name()`、`guess_export_format()`、`_extract_set_target()`、`_has_any()`、各 `*_KEYWORDS` | `action.py:126,191`、`plan_state.py:81` | `re` |
| `agent/planner.py` | 意图 → `AgentPlan`（固定步骤模板） | `PlanStep`、`AgentPlan`、`build_plan()`、`build_passive_plan()`、`build_uploaded_file_plan()`、`strategy_message()`、`_without_excluded_tools()` | `action.py:157,212` | `.intent`、`.strategy_templates` |
| `agent/plan_state.py` | 待确认计划的确认/取消/改写判定 | `is_confirm()`、`is_cancel()`、`is_plan_modification()`、`apply_user_intervention()`、`is_meaningful_new_intent()`、`is_new_intent()`（**定义了但全仓无调用方，死代码**） | `action.py:16` | `.intent` |
| `agent/target_ranker.py` | 子域命名打分排序 | `score_subdomain()`、`rank_subdomains()`、`HIGH/MEDIUM/LOW_VALUE_KEYWORDS` | `action.py:314` | 无 |
| `agent/system_prompt.py` | 基础 system prompt + 拼接已启用 skills | `BASE_SYSTEM_PROMPT`、`SYSTEM_PROMPT` | `action.py:18,776` | `agent.skills.registry` |
| `agent/strategy_templates.py` | 被动/主动侦察路线文本模板 | `render_src_collection_route()` | `planner.py:5` | 无 |
| `agent/client.py` | **模型调用统一入口（当前无人调用）** | `LLMClient`（`chat`、`health_check`）、别名 `OpenAICompatibleClient` | 无调用方（`agent_cli`/`service`/`action` 都不构造它） | `.config`、`.providers` |
| `agent/config.py` | LLM 配置加载与校验（自己再读一遍 `.env` 写 `os.environ`） | `LLMConfig`、`load_llm_config()`、`validate_llm_config()`、`_load_local_env_once()`、`_ENV_LOADED` | `client.py`、`providers/*` | `.errors`、`os`、`pathlib` |
| `agent/errors.py` | 异常层级 | `LLMError` 及其 7 个子类（`LLMConfigError`/`LLMAuthError`/`LLMRateLimitError`/`LLMTimeoutError`/`LLMConnectionError`/`LLMResponseError`/`LLMServerError`） | `providers/*`、`config.py` | 无 |
| `agent/model_result.py` | 结构化模型返回体的 dataclass | `ModelResult` | **无调用方（死代码）** | 无 |

### 3.5 `agent/providers/` 与 `agent/skills/`

| 文件 | 职责 | 关键类/函数 | 被谁调用 | 依赖谁 |
|---|---|---|---|---|
| `agent/providers/__init__.py` | 按 `LLM_PROVIDER` 选 provider | `build_provider()` | `client.py:31` | 4 个 provider |
| `agent/providers/base.py` | 抽象基类 | `BaseLLMProvider.chat/health_check` | 各 provider | `abc` |
| `agent/providers/openai_compat.py` | 唯一有真实网络调用的实现（重试、错误码映射、密钥脱敏） | `OpenAICompatProvider`（`chat`、`_chat_with_retry`、`_chat_once`、`_create_completion`、`_extract_content`、`_convert_error`、`_extract_status_code`、`_sanitize_error_message`、`health_check`） | `build_provider` | `openai` SDK、`.base`、`agent.config`、`agent.errors` |
| `agent/providers/deepseek.py` | DeepSeek 变体（补默认 base_url） | `DeepSeekProvider` | `build_provider` | `openai_compat` |
| `agent/providers/qwen.py` | 通义/DashScope 变体 | `QwenProvider` | `build_provider` | `openai_compat` |
| `agent/providers/ollama.py` | 本地 Ollama 变体（补 base_url 与 api_key="ollama"） | `OllamaProvider` | `build_provider` | `openai_compat` |
| `agent/skills/base.py` | Skill 数据结构与渲染 | `AgentSkill`（`render`） | `skills/registry`、`osint_recon` | `dataclasses` |
| `agent/skills/registry.py` | Skill 注册与 prompt 拼装 | `ALL_SKILLS`、`get_enabled_skills()`、`get_skill_by_id()`、`build_enabled_skills_prompt()` | `system_prompt.py:1` | `.base`、`.osint_recon` |
| `agent/skills/osint_recon.py` | OSINT 信息收集 Skill 的大段提示词与工具契约 | `OSINT_RECON_SKILL`、`OSINT_RECON_PROMPT` | `skills/registry` | `.base` |

### 3.6 `tests/`

| 文件 | 职责 | 关键函数 | 说明 |
|---|---|---|---|
| `tests/conftest.py` | 把项目根塞进 `sys.path` | 模块级代码 | 让 pytest 能 `import app` |
| `tests/unit/test_smoke.py` | 冒烟：核心模块可导入、`/api/` 路由存在、上传上限 2MB、`OUTPUT_DIR` 名为 results、registry 恰好 17 个 | `test_core_modules_importable` 等 5 个 | **不 mock 外部工具、不联网** |
| `tests/unit/test_repo_layout.py` | 仓库布局与 `.gitignore` 规则存在性 | `test_project_files_exist`、`test_repo_files_exist`、`test_ci_workflow_exists`、`test_gitignore_covers_runtime_artifacts` | 断言仓库根有 LICENSE/SECURITY.md/CONTRIBUTING.md/CHANGELOG.md |
| `tests/integration/__init__.py` | 占位文档，**无任何集成用例** | — | 注明 M1 起再加 |
| `tests/fixtures/__init__.py` | 空占位 | — | 无 fixture 文件 |

---

## 4. 三条关键调用链

### 4.a 用户提交一次扫描任务（从 HTTP 到落库）

以 `POST /api/run {"domain":"example.com","tools":["subfinder","dnsx"]}` 为例：

| # | 位置 | 本步数据形态 |
|---|---|---|
| 1 | `api/scan.py:execute_scan` 接收请求 | JSON → `dict`（`request.get_json(silent=True) or {}`，解析失败得 `{}`） |
| 2 | `api/scan.py:_normalize_domain(payload["domain"])` | `"Example.COM "` → `str "example.com"` |
| 3 | `tools = payload["tools"] or payload["tool"]`；`str` 则包一层 list | `list[str]`；**注意 `[]` 或 `""` 会走 `or` 变成 `None`** |
| 4 | 校验 `domain or file_path` 至少一个 → 否则 400 | `dict` 错误响应 |
| 5 | `tool_runner.load_tools(tools)`（`tool_runner.py:54`）| `list[str]` → 与 `get_supported_runners()` 求差集；非法则 `ValueError` → `api/scan.py` 转 400 |
| 6 | `ScanResultStore()` → `storage.py:_init_db` + 17 次 `_create_tool_table`（`CREATE TABLE IF NOT EXISTS`） | SQLite DDL；连接用完由 `with` 提交但**不关闭** |
| 7 | `tool_runner.run_tools(domain, file_path, tools, store)`（`tool_runner.py:94`） | 进入编排 |
| 8 | `load_targets(domain, file_path)`（`tool_runner.py:11`）| `list[str]`；**空则回落到 `TARGET_CONFIG["domains"]`（即 `nfl.com`）** |
| 9 | 空目标 / 空工具 → `raise SystemExit(1)`（`tool_runner.py:111,117,121`） | 异常，**API 层不捕获 `SystemExit`** |
| 10 | 外层 `for tool_name in selected_tools:` → `build_runner(tool_name)`（`modules/registry.py:44`） | Runner 实例（`__init__` 内 `ScanResultStore()` 的还会再开库） |
| 11 | 内层 `for target in targets:` → `runner.run_scan(target)` | 例：`modules/subfinder.py:run_scan` 组装 `cmd: list[str]` |
| 12 | `BaseRunner._build_output_file(target)`（`modules/base.py:46`） | `str` 路径 `results/<md5[:12]>_subfinder.txt` |
| 13 | `BaseRunner._execute(cmd, target)`（`modules/base.py:109`）→ `_resolve_command`（`shutil.which`）→ `subprocess.run(..., timeout=process_timeout)` | 子进程；`capture_output=True` 把 stdout/stderr 收进内存；`check=True` 非 0 退出抛 `CalledProcessError` |
| 14 | 异常分支：`FileNotFoundError` / `TimeoutExpired` / `CalledProcessError` 均 `print` + `return False` | `bool`；**失败信息只进 stdout 日志，不返回、不落库** |
| 15 | `run_scan` 若上一步 `False` 则 `return []`，否则 `_read_results(output_file)` | `list[str]`；文件不存在 → `[]` |
| 16 | `save_runner_results(store, target, runner, results)`（`tool_runner.py:76`）| 取 `runner.category`（缺省 `"subdomain"`）与 `runner.tool_name` |
| 17 | `store.save_dedicated_results(domain, tool_name, category, results)`（`storage.py:252`）→ `_normalize_results`（strip+去重）→ `_create_scan_run` → 逐条 `INSERT OR IGNORE` | 先写 `scan_runs(domain, tool_name, result_count=len(normalized), created_at)` 得 `run_id`，再写 `subfinder_results(run_id, domain, subdomain, raw_result, created_at)`；`UNIQUE(domain, subdomain)` 去重 |
| 18 | 汇总 `run_details`，`print` 统计，返回 report | `dict{targets, tools, total_found, total_inserted, runs}` |
| 19 | `api/scan.py` `jsonify(report)` | HTTP 200 JSON。**注意：整个扫描在 Flask 请求线程内同步跑完，无 job id、无进度、无取消** |

**与描述不符之处**：
- 没有"任务执行/任务状态"实体。`scan_runs` 只有 `id/domain/tool_name/result_count/created_at`（`storage.py:137-147`），**没有 status、started_at、finished_at、error_code、progress**。
- 没有异步：没有 `threading`/`Queue`/`celery`/`apscheduler`（全仓 grep 无匹配），`api/scan.py:105` 同步等待所有子进程结束。
- `error_code` 只在设计文档里存在（本机联调版方案 §6.2），**代码中没有任何一处定义或写入 `error_code`**。
  ▶ **M4 已解决**：`core/errors.py:ErrorCode` 定义了全部错误码，见 §9.10。

### 4.b 适配器的加载与调用：如何发现模块、如何判定成功/失败/超时/空结果

**4.b.0 每个 Runner 的执行方式速查**（决定"输出去哪读"的关键，排错时最容易搞错这一列）

| Runner | 执行方法 | 结果来源 | 失败时 | 独立异常 |
|---|---|---|---|---|
| `SubfinderRunner` | `_execute` | 输出文件 `-o` | `[]` | — |
| `AssetfinderRunner` | `_execute_stdout` | stdout 落文件 | `[]` | — |
| `AmassRunner` | `_execute` | 输出文件 `-o` + 正则过滤 | `[]` | — |
| `AmassIntelRunner` | `_execute` | 输出文件 `-o` | `[]` | `ValueError`（非法 ASN，`amass.py:76`） |
| `OneForAllRunner` | `_execute_stdout` | stdout 落文件 | `[]` | — |
| `AlterxRunner` | `_execute` | 输出文件 `-o`（`finally` 删临时输入） | `[]` | — |
| `ShufflednsRunner` | **自建 `subprocess.run`** | 解析 `dnsx -json` stdout | 抛异常 | `FileNotFoundError`/`TimeoutExpired` 不被捕 |
| `DnsxRunner` | `_execute` | 输出文件 `-o`（`finally` 删临时输入） | `[]` | — |
| `HttpxRunner` | `_execute` | JSONL `-json` → `_read_json_results` → **只取 `url`** | **`RuntimeError`** | `httpx.py:177,211` |
| `NaabuRunner` / `NmapRunner` | `_execute` | 输出文件 `-o` / `-oN` | `[]` | — |
| `GospiderRunner` | `_execute_stdout` | stdout 落文件 | `[]` | — |
| `KatanaRunner` | `_execute` | 输出文件 `-o` | `[]` | — |
| `WaybackurlsRunner` | `_execute_stdout` | stdout 落文件 | `[]` | — |
| `FeroxbusterRunner` | `_execute` | 输出文件 `-o` + `--json` 逐行解析 | `[]` | — |
| `DirsearchRunner` | `_execute` | 输出文件 `-o`（**未开 json_output**） | `[]` | — |
| `ENScanRunner` | **自建 `subprocess.run`** | `results/**/*.json` 前后 glob 差集 | `[]`（三步 except 全覆盖） | — |

结论：**17 个 Runner 里有 4 个（shuffledns / enscan / httpx 的失败分支）不走 `BaseRunner._execute` 的统一异常处理**，这是"同一类 bug 在不同工具上表现不同"的根因。

1. **发现**：`modules/registry.py:1-16` 在 **import 期**硬编码 `from .alterx import AlterxRunner` 等 16 行 import，然后 `RUNNER_REGISTRY` 字典登记 17 个键。没有插件扫描、没有 `pkgutil`/`importlib` 动态发现。
2. **薄壳重导出**：`modules/dirsearch.py`、`feroxbuster.py`、`katana.py`、`gospider.py`、`waybackurls.py` 只做 `from .url_tools import XRunner`；`naabu.py`/`nmap.py` 只做 `from .port_tools import ...`。改实现应改 `url_tools.py` / `port_tools.py`。
3. **构造**：`build_runner(tool_name)`（`modules/registry.py:44`）无参 `runner_cls()`；未注册抛 `ValueError("不支持的收集器: ...")`。
4. **统一执行**：所有 Runner 都实现 `run_scan(target)`，都依赖 `BaseRunner._resolve_command` + `_execute`/`_execute_stdout`，都返回 `list[str]`（`HttpxRunner.run_scan` 也是先解析 JSONL 再只返回 URL 字符串列表）。
5. **成功判定**：`_execute`/`_execute_stdout` 返回 `True` 当且仅当 `subprocess.run` 未抛异常（`check=True` 意味着 exit code 0）。Runner 再读输出文件。
6. **失败 / 超时判定**：**三者被压成同一个 `False`**（`modules/base.py:135-144`、`175-184`）：
   - `FileNotFoundError` → `print("[!] 未找到工具 ...")` → `False`
   - `subprocess.TimeoutExpired` → `print("[!] ... 扫描超时 ...")` → `False`
   - `subprocess.CalledProcessError` → `print("[!] ... 扫描失败: {stderr or stdout}")` → `False`
   - 之后 Runner 一律 `return []`（如 `modules/subfinder.py:65-66`、`modules/port_tools.py:63-64`）。
7. **空结果**：`_read_results` 在文件不存在或全部空行时同样返回 `[]`。
   → **成功但零结果、工具不存在、超时、非 0 退出，在调用方看都是 `[]`，无法区分**。这正是任务书里 "error_code 从哪来" 的答案：**代码里没有 error_code，只有 `print` 的日志和 `[]`**。
8. **例外（会抛异常的 Runner）**：
   - `modules/httpx.py:177` 无候选目标时 `raise RuntimeError`；`:211` 执行失败时也 `raise RuntimeError`。
   - `modules/shuffledns.py:88,108,146` 直接 `subprocess.run(["dnsx", ...])`（**硬编码命令名，不用 `self.config["path"]`**），且**没有 try/except**，`FileNotFoundError`/`TimeoutExpired` 会向上抛出。
   - `modules/amass.py:76` `AmassIntelRunner._parse_asn` 非法 ASN 抛 `ValueError`。
   - 这些异常在 `tool_runner.run_tools` 的第 11 步**没有任何 try/except**，会直接冒泡到 `api/scan.py`（未捕获）→ HTTP 500；在 Agent 路径上则由 `agent/action.py:366 _execute_tool` 的 `except Exception` 兜住并渲染成 `"ok": false`。

### 4.c "LLM agent 规划链"（真实情况：全程无模型调用）

```
用户自然语言
  → app.py:index()  action=="chat"                    （form 字段 agent_message）
  → agent/service.py:handle_agent_message()           （组装 AgentAction，透传 session 里的 history/pending_plan/agent_context/uploaded_targets）
  → agent/action.py:AgentAction.run(text)
      1) self.steps = []
      2) _resolve_menu_input(text)                    菜单 "1"~"4" → 复用上一次选项文本
      3) if self.pending_plan: _handle_pending_plan() 确认/取消/改写/新意图
      4) agent/intent.py:analyze_intent(text, has_uploaded_file, context_state)   ← 纯正则
         · extract_domain / extract_org_name / _extract_set_target
         · 关键词表：SUBdomain/VIEW_RESULT/UPLOAD_FILE/DB_QUERY/RANK/PROBE/EXISTING/...
         · 产出 UserIntent(intent_type, target, requested_tools, needs_confirmation, ...)
      5) intent_type 直接分发：cancel_plan / set_target / view_existing_results /
         analyze_existing_subdomains / storage 问答 / 缺 target 提示
      6) agent/planner.py:build_plan(intent, uploaded_context)   ← 固定模板，非模型生成
         返回 AgentPlan(target, strategy, requires_confirmation, steps=[PlanStep(id,tool,args,description)])
         strategy 文本来自 agent/strategy_templates.py:render_src_collection_route()
      7) requires_confirmation=True → 存进 self.pending_plan 并渲染提示，等用户回"确认执行"
      8) 确认后 _execute_plan() → 逐 step 调 _execute_tool(action, args)
         action ∈ available_tools {subdomain, summary, view_results, alive_results, httpx, export_results}
  → AgentAction._execute_tool()  （agent/action.py:360）
      · handler 抛异常 → {"ok": False, "error": str(exc), "tool": action}
      · _attach_storage_info() 补上 {"type":"sqlite","path":...,"tables":[...]}
  → 各 handler 真正干活：
      _tool_subdomain  → tool_runner.run_tools()  → 复用 §4.a 第 8~18 步
      _tool_httpx      → modules/httpx.py:HttpxRunner.run_scan()（直连，不经 registry）
                         → _save_httpx_metadata() → storage.save_tool_results(domain,"httpx","web",[json 字符串...])
      _tool_view_results/_tool_alive_results/_tool_summary → storage 查询
      _tool_export_results → exporter.gather_export_rows + export_results
  → _format_execution_summary() → 文本
  → app.py 写 session["agent_history"]/["pending_plan"]/["agent_context"]/["agent_steps"]
```

**真实情况说明（必须纠正任务书假设）**：
- **provider 调用这一步不存在**。`agent/providers/*`、`agent/client.py`、`agent/system_prompt.py`、`agent/skills/*`、`agent/model_result.py` 构成一套完整但**未被接线的 LLM 客户端**；`AgentAction` 里的 `self.client` 是死字段。
- 因此 "provider 超时" 类 bug 在当前 Web/CLI 流程中**不会触发**（代码路径不可达）；要触发只能自己写脚本构造 `LLMClient`。
- 当前表现为**确定性规则引擎**：同样的输入永远给同样的计划；"确认执行" 靠 `plan_state.is_confirm()` 的关键词（"确认/执行/开始/继续/run/start..."）。
- `SYSTEM_PROMPT` 只在 `_normalize_history` 里作为 history[0] 存在（`agent/action.py:776`），其唯一效果是被返回给前端/存进 session。

---

## 5. 数据模型

**以下为 `results/scan_results.db` 的实测结构**（用 sqlite3 直接读 `sqlite_master`，而非只看 `storage.py` 的建表语句）。

### 5.1 通用表

```sql
CREATE TABLE scan_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    result_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL            -- datetime.utcnow().isoformat(timespec="seconds") + "Z"
);
CREATE INDEX idx_scan_runs_domain ON scan_runs(domain);
CREATE INDEX idx_scan_runs_tool   ON scan_runs(tool_name);

CREATE TABLE tool_results (              -- 未注册工具的兜底表
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    domain TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    category TEXT NOT NULL,
    value TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(domain, tool_name, category, value),
    FOREIGN KEY(run_id) REFERENCES scan_runs(id)
);
```

### 5.2 工具专属表（17 张，模板由 `storage.py:_create_tool_table` 用 f-string 动态拼表名/列名）

```sql
CREATE TABLE <table> (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    domain TEXT NOT NULL,
    <result_column> TEXT NOT NULL,       -- 见下表
    raw_result TEXT NOT NULL,            -- 当前实现里恒等于 <result_column>
    created_at TEXT NOT NULL,
    UNIQUE(domain, <result_column>),
    FOREIGN KEY(run_id) REFERENCES scan_runs(id)
);
CREATE INDEX idx_<table>_domain ON <table>(domain);
```

| 工具名 | 表名 | 结果列 | category |
|---|---|---|---|
| amass | `amass_results` | `subdomain` | subdomain |
| amass_intel | `amass_intel_results` | `subdomain` | subdomain |
| subfinder | `subfinder_results` | `subdomain` | subdomain |
| assetfinder | `assetfinder_results` | `subdomain` | subdomain |
| shuffledns | `shuffledns_results` | `subdomain` | subdomain |
| alterx | `alterx_results` | `subdomain` | subdomain |
| oneforall | `oneforall_results` | `subdomain` | subdomain |
| enscan | `enscan_results` | `subdomain` | subdomain |
| dnsx | `dnsx_results` | `hostname` | alive |
| httpx | `httpx_results` | `endpoint` | web |
| naabu | `naabu_results` | `port_result` | port |
| nmap | `nmap_results` | `port_result` | port |
| feroxbuster | `feroxbuster_results` | `url` | url |
| dirsearch | `dirsearch_results` | `url` | url |
| waybackurls | `waybackurls_results` | `url` | url |
| katana | `katana_results` | `url` | url |
| gospider | `gospider_results` | `url` | url |

实测行数（当前工作副本）：`waybackurls_results=296`、`enscan_results=37`、`feroxbuster_results=12`、`dirsearch_results=9`、`amass_intel_results=2`、`scan_runs=28`，其余为 0。旧库里**没有** `subdomain_results`、`alive_results`、`jobs`、`assets`、`observations`、`artifacts`、`scopes`、`settings` 等表——`jobs` / `artifacts` / `scopes` / `exports` 属于**新库** `results/local.db`（§9.3）；`assets` / `observations` 同样是新库的表，见 §9.12。

> **注意两套资产模型并存**：`storage.py` 的 17 张工具专属表是「原始结果行」，
> 只增不减、按 `(domain, 结果列)` 去重，回答不了「谁先发现的」；
> `core/assets.py` 的 `assets` / `observations` 是 P1 新增的「唯一资产 + 观测时间线」。
> 两者**没有自动同步**——目前只有 mock/real 任务的步骤会落 `assets`，
> 旧的 `/api/run` 同步扫描链路**不会**产生资产（§9.12.6）。

**表结构的三个硬约束（决定 bug 表现）**：
1. **专属表没有 `category` 列**（分类信息硬编码在 `TOOL_DATABASES` 里），所以 `get_tool_results(category=...)` 在专属表分支无法按分类过滤，只能靠 `TOOL_DATABASES[meta]["category"]` 反查。
2. **`raw_result` 当前恒等于结果列的值**（`storage.py:289` 传的是 `value, value`），没有任何原始行/原始 JSON 被保留 —— 想追溯"工具原话"必须去看 `results/<hash>_<tool>.txt` 文件。
3. **唯一键只有 `(domain, <result_column>)`**，不含 `tool_name`/时间。因此同一域名反复扫描只会让 `run_id` 不断新建、`scan_runs` 不断膨胀，而结果行永远停在第一次插入的那条（`INSERT OR IGNORE`）。


> ⚠️ 但 `agent/action.py` 会把不存在的表名告诉用户：`_get_storage_tables()`（`:508,512,517,519,521`）返回 `"subdomain_results"`、`"alive_results"`；`_answer_storage_question()`（`:853`）说"主要表包括 `subdomain_results`、`alive_results`"。这些表从未创建，相关文本是**误导性输出**（仅字符串，不会引发 SQL 报错）。

### 5.3 任务状态机

**代码中不存在任务状态机。** `scan_runs` 无 `status` 列，写入即终结（`storage.py:_create_scan_run` 一条 INSERT，无 UPDATE）。API 也没有 job 概念。

`queued / running / succeeded / partial / failed / timeout / cancelled / interrupted` 这套状态枚举，以及 `tool_not_found/permission_denied/invalid_target/scope_violation/timeout/parse_error/network_error/rate_limited/partial_success/unknown_error` 这套 error_code，在 M0 基线时**只写在设计文档**（本机联调版方案 §6.1 / §6.2）里，属于当时**尚未实现的 M3/M4 目标**。当时的"状态"只有：HTTP 请求是否返回、子进程是否 exit 0（被吞成 `bool`）、`scan_runs` 是否多了一行。

▶ **M3 已实现状态机**（`core/jobs.py`，见 §9.4），**M4 已实现 error_code**（`core/errors.py`，见 §9.10）。

---

## 6. BUG 定位索引表（共 25 条症状；第 23～25 条为 P1/M7 新增）

| # | 典型症状 | 最可能的 3 个排查位置 | 该处典型失败模式 |
|---|---|---|---|
| 1 | 扫描任务「一直卡在 running」/ 请求长时间不返回 | ① `tool_runner.py:run_tools`（第 130–154 行的双层同步 for）② `modules/base.py:_execute` 的 `timeout=config.get("process_timeout")` ③ `config.py:build_tool_config` 的 `process_timeout: 300` | 根本没有异步任务系统；整个扫描在 Flask 请求线程里同步跑完。单目标最长可挂 300s×目标数，前端只能等到超时。没有 job_id 可查进度，也没有取消接口 |
| 2 | 任务「立刻失败」，没有任何扫描日志 | ① `tool_runner.py:load_targets`（`:29-32` 打开 file_path）② `tool_runner.py:load_tools`（`:68-73`）→ `raise SystemExit(1)` ③ `api/scan.py:execute_scan`（未捕获 `SystemExit`/`FileNotFoundError`） | `file_path` 不存在 → `FileNotFoundError` → 500；`SystemExit` 是 `BaseException`，Flask 不兜，页面/客户端看到 500 或连接被断；`/api/tool/<name>/run` 里 `load_tools([tool_name])` 抛 `ValueError` 被转 400，但 `run_single_tool` 后续的 `runner.run_scan` 无保护 |
| 3 | 工具「明明能跑通」却返回空结果 / 库里 0 行 | ① `modules/base.py:_read_results`（`:62-78` 文件不存在即返回 `[]`）② 各 Runner 的 `-o <output_file>` 写入路径 ③ `modules/base.py:_build_output_file`（`:59` md5 前缀命名） | 工具把结果打到 stdout 而配置用了 `_execute`（或反之），文件根本没生成；`results/` 不可写；域名的 md5 文件名与预期不一致导致读错文件 |
| 4 | 结果页看不到数据（明明 scan_runs 有记录） | ① `api/results.py:query_results` → `exporter.gather_export_rows` ② `storage.py:get_view_results`（**只 UNION subdomain 类表**）③ `storage.py:get_tool_results` | `gather_export_rows` 在 `category is None` 时只调 `get_view_results`（subdomain 8 张表）+ `get_tool_results` 兜底；`url/web/port` 类数据在 `category` 未指定时会被 `limit` 截断或重复。`get_tool_results` 的 `category` 形参**在专属表分支被完全忽略**（`storage.py:671-704`），所以按 category 过滤静默失效 |
| 5 | 上传目标文件解析出错 / 400「未识别到有效目标」 | ① `target_parser.py:normalize_target`（`:31` DOMAIN_PATTERN/IP_PATTERN 双重 `fullmatch`）② `target_parser.py:_parse_xlsx`（`:120` `load_workbook`）③ `api/upload.py:upload_file`（`:46` 扩展名白名单） | 带路径的 URL（`https://a.com/x`）只取 hostname 后仍需匹配域名正则；`*.xlsx` 未装 openpyxl 时 `_parse_xlsx` 抛 `ImportError`，`api/upload.py` **不捕获** → 500；`.xls`（老格式）不在白名单 → 400；中文/全角字符、`_`开头的域会被正则拒掉 |
| 6 | 新增一个扫描工具后「没生效」 | ① `modules/registry.py:RUNNER_REGISTRY`（`:19-37`）② `config.py:build_tool_config` + 对应 `*_CONFIG` ③ `storage.py:TOOL_DATABASES`（`:15-101`） | 三处都要登记：漏 registry → `load_tools` 报"存在不支持的工具"；漏 TOOL_DATABASES → 结果落到通用 `tool_results` 且 `get_dedicated_results` 抛 `ValueError`；漏 `*_CONFIG` → `KeyError: 'path'` |
| 7 | 扫描范围/目标校验被绕过（传入任意 file_path 或空目标却扫了别的域名） | ① `tool_runner.py:load_targets`（`:29-41` 直接 `open(file_path)` + 空目标回落 `TARGET_CONFIG`）② `config.py:TARGET_CONFIG`（`:94-97` 默认 `domains=["nfl.com"]`）③ `api/scan.py:execute_scan`（`:94` 只校验 `domain or file_path` 非空） | **没有 Scope 概念**（grep 全仓无 scope 表/校验器）。`file_path` 可为任意绝对路径（任意文件读取）；目标全被过滤掉时回落到硬编码的 `nfl.com` 并真的发起扫描；`tools: []` 也会因为 `payload.get("tools") or payload.get("tool")` 变成 `None`，进而 `load_tools` 回落到 `SCAN_CONFIG["enabled_runners"]=["amass"]` 去扫 |
| 8 | 设置项保存后「不生效」 | ① `api/settings.py:save_settings` → `_write_env_file`（`:99`）② `config.py:Config` 类属性（`:13-39`，**import 期求值**）③ `api/settings.py:KEY_MAPPING`（`:58-80`） | `.env` 写成功了，但 `Config.LLM_API_KEY` 等是类属性，进程内已固化，必须重启（响应里的 message 也这么说）；`load_dotenv` 默认**不覆盖**已存在的环境变量；`KEY_MAPPING` 里 `enscan_*_cookie` 映射到 `FOFA_EMAIL/FOFA_KEY/HUNTER_API_KEY` 是**永远不会走到的死分支**（enscan 键在 `save_settings` 里走 yaml 分支），极易误导后来者 |
| 9 | 导出文件缺字段 / 行重复 / 混入别的工具数据 | ① `exporter.py:gather_export_rows`（`:46-77` 两段拼接）② `storage.py:_get_tool_results_fallback`（`:706-747` 遍历**全部 17 张表**）③ `exporter.py:export_results`（`:109` 动态 fieldnames） | 同一条子域名会先由 `get_view_results` 加入、又被 `_get_tool_results_fallback` 从同一张专属表再加一次 → 重复行；`category` 过滤在专属表分支失效 → 混入其他分类；`fmt` 不是 csv/json 时抛 `ValueError`，`api/results.py:export_data` 不捕获 → 500（`agent/intent.guess_export_format` 会产出 `"xlsx"`，必然踩中） |
| 10 | 前端页面 500 / `TemplateNotFound: index.html` | ① `app.py:26` `template_folder="web/templates"` ② `app.py:160` `render_template("index.html", **context)` ③ `app.py:92` `index()` 的 `request.values.get("domain")` | 仓库里 **不存在 `web/` 目录**（实测 `Test-Path web` = False），`/` 必然抛 `TemplateNotFound`；同时 `app.py:110` 会在渲染前同步跑 subfinder；`debug=True` 让异常页暴露堆栈 |
| 11 | Agent 规划报错 / 答非所问 | ① `agent/intent.py:analyze_intent`（`:118-273` 的分支顺序）② `agent/planner.py:build_plan`（`:79-199`）③ `agent/action.py:run`（`:109-182`） | 分支顺序敏感：`确认/执行/开始/继续` 的关键词判断（`:121`）优先于一切，含"继续"的正常句子会被吞成 `confirm_plan`；`build_plan` 对 `confirm_plan`/`cancel_plan`/`analyze_existing_subdomains` 都返回 `None`，走到 `:161` 就回"我没有识别到明确任务"；`subdomain_scan` 恒用 `scan_tool="subfinder"`，用户说 amass 也不改（除 `plan_state.apply_user_intervention` 的"改用 amass"字面量） |
| 12 | provider 超时 / 认证失败 | ① `agent/providers/openai_compat.py:_convert_error`（`:133`，按 401/403/429/5xx/404/400 + 文本关键字分类）② `agent/providers/openai_compat.py:_chat_with_retry`（`:52`，退避 `min(2**attempt, 8)`）③ `agent/config.py:validate_llm_config`（`:114`） | **当前 Web/CLI 流程根本不会走到这里**（`LLMClient` 无调用方）。若自行调用：`validate_llm_config` 在 `LLMClient.__init__` 里抛 `LLMConfigError`；`openai` SDK 的 `APITimeoutError` 没有 `status_code`，只能靠 `"timeout" in message.lower()` 命中，`sanitize` 只对 api_key 做替换 |
| 13 | 子进程路径找不到（Windows `.exe` / `scripts/` 下的工具） | ① `config.py:HTTPX_CONFIG`（`:171`）② `modules/base.py:_resolve_command`（`:98-107`）③ `config.py:GO_BIN_WINDOWS/GO_BIN_POSIX`（`:91-92`） | **`HTTPX_CONFIG` 的 path 默认值写成了 `"http-x"`（`:171`）**，PATH 里没有该命令 → `FileNotFoundError` → 静默 `[]`。`scripts/dirsearch.exe`、`scripts/oneforall.exe`、`scripts/OneForAll.exe` 明明存在，但 config 只给裸名 `"dirsearch"`/`"oneforall"`，且 `GO_BIN_*` 常量**定义了从未使用**（不注入 PATH）。只有 `.bat/.cmd` 会被 ComSpec 包裹，`.exe` 依赖 `shutil.which` |
| 14 | 子进程超时 / 命令挂死，进程不退出 | ① `modules/shuffledns.py:_bruteforce_with_dnsx`（`:88` 硬编码 `timeout=300`，无 try/except）② `modules/base.py:_execute`（`:125` `capture_output=True`）③ `modules/enscan.py:run_scan`（`:55` `capture_output=True` + `cwd=results/`） | `capture_output=True` 在输出量大时可能因管道写满而卡住；`shuffledns`/`enscan` 自建 `subprocess.run` **绕过了 `process_timeout` 统一入口**（enscan 用自己的 `process_timeout`，shuffledns 写死 300/120/30）；`shuffledns` 的 `TimeoutExpired` 会向上抛穿到 `api/scan.py` → 500 |
| 15 | 并发任务互相干扰 / 结果串台 | ① `modules/shuffledns.py:_WILDCARD_CACHE`（`:46` **类级可变 dict**）② `agent/action.py:RATE_LIMIT_CACHE`（`:23` 类级 dict，`:814 _enforce_rate_limit`）③ `modules/enscan.py:run_scan`（`:53,77` 运行前后 glob `results/**/*.json` 取差集） | 类属性被所有实例/线程共享：泛解析缓存跨任务污染且无上限；限流是**进程级全局**，一个用户把 `httpx:domain` 锁 8 秒会拒绝另一个会话。enscan 用"新增 json 文件"判定结果，并发或被别的工具写入 json 时会取到**别人的产物** |
| 16 | 数据库被锁 `database is locked` | ① `storage.py:_get_connection`（`:123-125` 无 `PRAGMA busy_timeout`、不启用 WAL）② `storage.py` 全部 `with self._get_connection() as conn:`（sqlite3 的上下文管理器**只提交事务、不关闭连接**）③ 每次 `ScanResultStore()` 都跑一遍 `_init_db` 的 17 次 DDL | 无 WAL、无 busy_timeout、无 `foreign_keys=ON`（外键形同虚设）；每个 API 调用都新建 `ScanResultStore()` 并执行建表语句，写锁竞争窗口被放大；连接对象只提交不关闭，长期运行会累积文件句柄 |
| 17 | 工具产物文件堆积 / 临时文件泄漏 | ① `modules/base.py:_write_input_file`（`:201` `delete=False`，落在 `results/`）② `modules/shuffledns.py` 的 `words_file` 与 `NamedTemporaryFile` ③ `api/upload.py:upload_file`（`:53,58` 先 `file.save(raw_path)` 再解析） | 基类的临时文件"调用方负责删除"，除 `dnsx/httpx/alterx` 外无人删；`upload` 在解析失败（返回 400）时**不清理已保存的 raw 文件**，`uploads/` 会不断积累；`enscan` 把原始 JSON 复制成 `results/<hash>_enscan.txt` 也从不清理 |
| 18 | httpx 探测「跑通了」但拿不到状态码/标题/技术栈 | ① `modules/httpx.py:run_scan`（`:213-214` 只 `return [r.get("url") ...]`）② `modules/httpx.py:_read_json_results`（`:111-155` 解析出的 6 个字段被丢弃）③ `agent/action.py:_save_httpx_metadata`（`:908-911` 存的是 `json.dumps(item)`，落到 `httpx_results.endpoint` 列） | 走 `tool_runner` 的路径**丢失所有元数据**（README/设计文档声称有指纹，实际只剩 URL）；只有经 Agent 的 `_tool_httpx` 才把 JSON 字符串塞进结果列——即同一个工具的两条调用链写出的数据形态不同 |
| 19 | httpx 一执行就把整批任务打挂 | ① `modules/httpx.py:177`（无候选 `raise RuntimeError`）② `modules/httpx.py:211`（`_execute` 返回 False 时 `raise RuntimeError`）③ `tool_runner.py:137`（`runner.run_scan(target)` **无 try/except**） | 与其它 Runner "失败返回 `[]`" 的约定不一致；在 `/api/run` 批量路径上会直接冒泡成 500，**后续目标/工具全部不再执行**；只有在 Agent 路径被 `agent/action.py:366` 的 `except Exception` 兜住 |
| 20 | Agent 对话「失忆」/ 多轮后上下文丢失 | ① `app.py:139,146`（`session["agent_history"]=...[-40:]`、`session["agent_steps"]=...[-50:]`）② `app.py:27` `app.secret_key = Config.SECRET_KEY`（默认 `"dev-secret-key"`）③ `agent/action.py:_trim_history`（`:785` 上限 30 条） | Flask session 是**签名 Cookie**（客户端存储）；`agent_history`/`agent_steps` 里含完整工具结果文本，很容易超过浏览器 4KB Cookie 上限 → Flask 静默丢弃 Cookie → 下一轮 `session.get("agent_history")` 变空。默认密钥还可被伪造 |
| 21 | `/api/tools`、`/api/databases` 返回的记录数不对/很慢 | ① `api/tools.py:list_tools`（`:80-88` 每个工具都 `build_runner` 实例化）② `api/tools.py:_build_tool_payload`（`:35`）③ `storage.py:get_tool_databases`（`:527-541`，**不返回任何 count**） | 接口 docstring（`api/tools.py:52-66`）宣称返回 `record_count`，实现只返回 `tool_name/table/result_column/category`；真正的计数方法 `get_tool_database_overview`（`storage.py:543`）**在 API 层从未被调用**。另外 `build_runner` 会执行 `DnsxRunner/HttpxRunner/AlterxRunner/ShufflednsRunner` 的 `__init__`（各建一个 `ScanResultStore()`，触发建表） |
| 22 | 子域爆破类工具（shuffledns/alterx/dnsx）总是零结果 | ① `config.py:SHUFFLEDNS_CONFIG` / `FEROXBUSTER_CONFIG` 的 `wordlist` ② `modules/shuffledns.py:_bruteforce_with_dnsx`（字典不存在曾只 print 一句就 `return []`）③ `modules/base.py:require_wordlist`（M5 起统一校验） | **M5 已修**：仓库不分发 `SecLists/`，所以两个默认字典**在本机并不存在**——原先 shuffledns 静默返回空、feroxbuster 把不存在的路径当 `-w` 传进子进程。现在：字典路径按**项目根**解析（与 cwd 无关）+ 环境变量可覆盖；**配置了字典却不存在 → `error_code=config_error` 的显式失败，且不启动子进程**。剩下「真零结果」的正常原因：`alterx`/`dnsx`/`httpx` 的候选来自 `store.get_results_by_domain()`，subfinder 没先跑过就永远是空 |
| 23 | 任务跑完了，资产页却「一条都没有」/ 少了几条 | ① `jobs/executor.py:execute_job` 里的 `ingest_step_observations` 调用 ② `core/assets.py:CATEGORY_TO_TYPE`（`web`/`alive`/`dns` 的映射）③ 任务详情里的 `step.assets_ingested` 事件（含 `skipped` 与 `reasons`） | **先看事件，不要先看代码**：`step.assets_ingested` 的 `written`/`skipped`/`reasons` 直接说明这批观测落了几条、为什么跳过。三种常见原因：① 工具的 `Observation.category` 不在 `CATEGORY_TO_TYPE` 里（返回 `None` → 整条跳过，不猜类型）；② 步骤只有字符串结果且工具是 `httpx`/`naabu`/`nmap`（形态不确定 → 故意不落，见 §9.12.3）；③ `canonical.normalize()` 判定值非法（如把本地路径当 URL）。**注意落观测是派生产物**：它失败不会让任务变 failed，所以「任务 succeeded 但没资产」是合法状态，必须靠事件区分 |
| 24 | 对比两次任务时「未变」总是 0，看起来像两次扫描毫无交集 | ① `core/assets.py:diff_jobs` 里 `counts["unchanged"]` 与 `unchanged` 明细的关系 ② 页面「含未变」复选框（`#diff-include-unchanged`）③ `web/static/assets.js:renderDiff` 对空明细的措辞 | `include_unchanged=False`（勾掉「含未变」）**只应影响明细、不应影响计数**。曾经两者一起清零，于是「扫到了但没变化」与「什么都没扫到」变得不可区分。先看 `counts.unchanged`：**它非 0 而明细为空，说明是这次没要明细，不是两次没有交集**（前端会显示「按设置未取明细，共 N 条」）。真正的 0 才是「两次任务的资产集合完全不相交」 |
| 25 | Agent 回复里出现 `AttributeError: 'str' object has no attribute 'get'`（只在**真有存活结果**时） | ① `agent/action.py:_tool_httpx` 的 `items` 字段 ② `agent/action.py:_summarize_httpx_items`（逐条 `item.get(...)`）③ `modules/httpx.py:run_scan` 的返回值语义 | 同一个 `rows` 变量在两条链上有两种形态：`run_scan` 返回 **URL 字符串列表**（旧签名，兼容用），而元数据在 `runner.last_items`（dict 列表）。`items` 错取了 `rows`，于是 `_summarize_httpx_items` 收到一堆 `str` 就炸。**零结果时不炸** —— 所以「本地跑不通、真机上必炸」是它的典型表现。`results` 里的 `total` 也就会与 `items` 长度对不上 |

---

## 7. 已知薄弱点 / 坑（实际阅读所得）

> 共 36 条，每条都给出文件与函数位置，可直接跳转。
>
> **这是 M0 基线时通读代码的记录**，作为「原始代码长什么样」保留。
> 其中已修复的条目在文末加了 `▶` 标注并指向 §9.9 的现状修正表；
> 未加标注的仍然成立。

### 7.1 异常与错误处理

1. **失败被吞成空结果**：`modules/base.py:_execute` 与 `_execute_stdout` 把 `FileNotFoundError`/`TimeoutExpired`/`CalledProcessError` 全部降级为 `return False`，Runner 再 `return []`。调用方无法区分"工具没装""超时""非 0 退出""本来就没结果"。这在设计文档里被列为 P0，代码未改。
   ▶ **M4 已解决**：统一 `RunnerResult` + `error_code`，详见 §9.10。
2. **异常冒泡路径不一致**：`tool_runner.py:137` 对 `runner.run_scan` 无保护，而 `modules/httpx.py:177,211` 会 `raise RuntimeError`、`modules/shuffledns.py` 会抛 `FileNotFoundError/TimeoutExpired`、`modules/amass.py:76` 会抛 `ValueError` → 在 `api/scan.py` 直接 500，在 Agent 路径被 `agent/action.py:366` 兜住，行为取决于从哪个入口进来。
3. **`SystemExit` 与 HTTP 混用**：`tool_runner.py:111/117/121` 用 `raise SystemExit(1)` 表达"无目标/无工具"，这是 CLI 语义；`api/scan.py:execute_scan` 不捕获 `BaseException`，Web 场景下表现为 500 或被 dev server 中断。`app.py:112` 是唯一显式处理 `SystemExit` 的地方。
4. **`_execute_tool` 的宽泛捕获**：`agent/action.py:366` `except Exception as exc: result = {"ok": False, "error": str(exc), "tool": action}` —— 会把 `KeyError`/`AttributeError` 这类编程错误伪装成"工具执行失败"反馈给用户，排查时容易走错方向。
5. **`api/results.py:export_data` 未捕获 `export_results` 的 `ValueError`**（`exporter.py:116`），非法 `format` 直接 500。
6. **`api/upload.py` 未捕获 `target_parser` 的 `ImportError`**（`target_parser.py:117`），上传 `.xlsx` 且未装 openpyxl 时 500，而不是 400 + 明确提示。

### 7.2 路径与外部依赖

7. **硬编码绝对路径**：`config.py` 的 `FEROXBUSTER_CONFIG.wordlist` 曾写死作者本机路径 `D:/c4/v2/backend/framework-main/SecLists/raft-small-directories.txt`，换机器必失败。▶ **M5 已解决**：改为仓库相对路径 `SecLists/raft-small-directories.txt`，并支持 `FEROXBUSTER_WORDLIST` 环境变量覆盖（见 §9.10 末「字典配置」）。
8. **`HTTPX_CONFIG` 命令名疑似笔误**：`config.py` `os.getenv("HTTPX_PATH", "http-x")`，默认值不是 `httpx`。
9. **配置引用的字典文件不存在**：`config.py` 的 `SecLists/subdomains-top1million-5000.txt` 与 `SecLists/raft-small-directories.txt` **两个都指向仓库里并不存在的文件**（仓库不分发 `SecLists/`，见 README）。▶ **M5 已解决（行为部分）**：路径改为**按项目根解析**的相对路径 + 环境变量覆盖；文件确实缺失时不再静默返回空结果，而是 `error_code=config_error` 的显式失败。字典本身仍**不随仓库分发**，需自行下载或用环境变量指向本机字典。
10. **`GO_BIN_WINDOWS`/`GO_BIN_POSIX` 是死常量**（`config.py:91-92`），从未用于注入 PATH；`scripts/*.exe` 也不会被自动发现。
11. **`modules/shuffledns.py` 硬编码 `"dnsx"` 命令名**（`:89,109,147`）而不读 `self.config["path"]`，无法通过配置切换二进制；并且它**根本没有调用 `shuffledns` 二进制**，类名/工具名与实际行为不符。
12. **`modules/enscan.py` 依赖 `cwd=results/` + 前后 glob 差集**（`:53,58,77`）识别产物：并发运行或其它工具往 `results/` 写 `.json` 时会认错文件；文件已存在但被覆盖时 `new_files` 为空 → 静默 `[]`。

### 7.3 SQL 与并发

13. **f-string 拼 SQL 表名/列名**：`storage.py:_create_tool_table`（`:189`）、`get_dedicated_results`（`:498,508`）、`get_tool_results`（`:688`）、`_get_tool_results_fallback`（`:729`）、`_query_subdomain_tables`（`:384,401`）。当前插值来源都是模块级常量 `TOOL_DATABASES`，**不构成注入**，但一旦有人把用户输入接到表名就会立刻变成注入点。值全部走 `?` 占位参数，这点是对的。
14. **无 WAL / 无 `busy_timeout` / 无 `PRAGMA foreign_keys=ON`**（`storage.py:_get_connection`，`:123`）。`FOREIGN KEY(run_id) REFERENCES scan_runs(id)` 因此形同虚设；并发写入直接 `database is locked`。
15. **连接泄漏**：所有写方法用 `with self._get_connection() as conn:`。sqlite3 的 Connection 上下文管理器**只做事务提交/回滚，不关闭连接**，因此每次调用都遗留一个未 `close()` 的连接对象。
16. **`category` 参数被静默忽略**：`storage.py:get_tool_results`（`:671-704`）签名有 `category`，但在专属表分支里完全没用；`_get_tool_results_fallback`（`:706`）也不按 category 过滤。所以 `/api/results?category=web` 对已注册工具不生效。
17. **`gather_export_rows` 双重收集**（`exporter.py:46-77`）：subdomain 行先由 `get_view_results` 取一遍，`get_tool_results` 的 fallback 又把 17 张表 UNION 一遍，导致重复行与 limit 语义混乱。
18. **`_create_scan_run` 无条件插入**（`storage.py:241`）：`result_count=0` 的记录也会写 `scan_runs`，表会随空扫描持续膨胀。
19. **`_init_db` 在每次 `ScanResultStore()` 都执行**：17 次 `CREATE TABLE IF NOT EXISTS` + 18 次 `CREATE INDEX`（`storage.py:177-207`），而 `AgentAction._validate_domain` 之前的每个 handler、每个 API 请求都会新建实例。

### 7.4 全局可变状态

20. **`modules/shuffledns.py:_WILDCARD_CACHE`**（`:46`）：类级 dict，跨实例/跨线程共享，键为域名，**永不清理**，长期运行内存单调增长，且会让后续任务复用被污染的泛解析 IP 集合。
21. **`agent/action.py:RATE_LIMIT_CACHE`**（`:23`）：类级 dict，进程级全局限流（`AGENT_TOOL_MIN_INTERVAL_SEC` 默认 8 秒），多会话互相干扰，且只有插入没有清理。
22. **`agent/config.py:_ENV_LOADED`**（`:22`）+ `_load_local_env_once()` 直接改写 `os.environ`：模块级副作用，且只在 `key` 不存在时写入，与 `config.py:6` 的 `load_dotenv` 存在加载顺序耦合（谁先 import 谁说了算）。

### 7.5 配置与密钥

23. **`SECRET_KEY` 默认固定值**：`config.py:17` `os.getenv("SECRET_KEY", "dev-secret-key")` + `app.py:166` `debug=True`。Session 可伪造，且 Web 调试器暴露。
24. **`/api/settings` 可匿名写 `.env`**：`api/settings.py:save_settings`（`:187`）无任何鉴权。`_write_env_file`（`:99`）把请求体里的值**原样拼进 `KEY=VALUE` 行**，未做引号/换行转义 —— 值里带 `\n` 就能注入任意环境变量（例如覆盖 `LLM_BASE_URL`）。写入也不是原子操作（直接 `open(..., "w")` 覆盖）。
25. **`_write_enscan_yaml` 用 `re.sub` 回填 Cookie**（`api/settings.py:150-166`）：替换串 `rf'\1"{cookie}"'` 未转义，Cookie 中的 `\`、`\g`、`"`、换行都会破坏 YAML 或产生错误替换；读取侧的正则 `rf'{source}:\s*\n\s+cookie:\s*"(.*?)"'`（`:144`）也依赖模板的精确缩进格式。
26. **`KEY_MAPPING` 里 `enscan_*_cookie` → `FOFA_EMAIL/FOFA_KEY/HUNTER_API_KEY`**（`api/settings.py:77-79`）是永远不会执行的映射（enscan 键在保存逻辑里走 yaml 分支），注释也自相矛盾，是典型的踩坑点。

### 7.6 Agent 与模型层

27. **LLM 全链路未接线**：`agent/client.py:LLMClient`、`agent/providers/*`、`agent/model_result.ModelResult` 均无调用方；`AgentAction.__init__(client=...)` 只存不用（`agent/action.py:52`）。`agent/system_prompt.SYSTEM_PROMPT` 的唯一去处是 `conversation_history[0]`（`:776`）。这意味着"LLM Agent"当前是纯规则引擎，文档（README:44、README:479）与实际能力不符。
28. **死代码与重复代码**：
    - 真死代码（全仓无调用方）：`agent/plan_state.py:is_new_intent`（`:80`）、`config.py:TOOL_COMMANDS`（`:244`）、`config.py` 的 `DEFAULT_PASSIVE_TOOLS/DEFAULT_SUBDOMAIN_TOOLS/DEFAULT_WEB_PROBE_TOOLS/DEFAULT_CONTENT_DISCOVERY_TOOLS/DEFAULT_PORT_SCAN_TOOLS`（`:262-266`）、`agent/model_result.py` 整个模块、`storage.py:get_tool_database_overview`（`:543`）、`config.py:GO_BIN_WINDOWS/GO_BIN_POSIX`（`:91-92`）。
    - 重复代码：`modules/base.py:_write_input_file`（`:186`）**只被 `AlterxRunner` 使用**（`modules/alterx.py:71`）；`DnsxRunner`（`dnsx.py:62`）与 `HttpxRunner`（`httpx.py:70`）各自抄了一份逻辑几乎相同的同名方法。
29. **`agent/action.py:_get_storage_tables` 与 `_answer_storage_question` 输出不存在的表名**（`:508,512,517,519,521,853`）：`subdomain_results`、`alive_results` 从未创建，用户按此去查库会扑空；`alive_results` 工具实际查的是 `dnsx_results`（`storage.py:get_alive_results`，`:631`）。
30. **`enscan` Cookie 复用外部 key 的注释误导**：`api/settings.py:77-79` 与模块顶部 docstring 描述不一致。

### 7.7 仓库与工程卫生

31. **敏感产物已进 Git**（`git ls-files` 实测 83 个跟踪文件中包含）：`results/scan_results.db`、`results/*.txt`、`results/outs/*.json`（真实企业名与域名）、`uploads/*.txt`（真实目标清单）、`scripts/dirsearch.exe`、`scripts/OneForAll.exe`、`scripts/oneforall.exe`、`SecLists/raft-small-directories.txt`。仓库根的 `.gitignore` 虽有 `**/results/`、`**/uploads/`、`*.db`，但对**已跟踪文件无效**。
32. **首页无模板**：`app.py:160` 渲染 `index.html`，仓库无 `web/` 目录（当时的实施方案也把它列为 P0）。▶ **M1 已解决**：`web/templates/` 与 `web/static/` 已补齐。
33. **测试覆盖极薄**：`tests/` 下只有 2 个单元测试文件（导入、布局、常量），`tests/integration/` 与 `tests/fixtures/` 均为占位 `__init__.py`；**没有任何针对 Runner、storage、intent/planner 的测试**，也没有 mock runner。
34. **`app.py` 只有单个应用实例**（模块级 `app = Flask(...)`），没有 `create_app()` 工厂；`tests/unit/test_smoke.py:21` 直接 `import app` 并检查 `app.app.url_map`。
35. **`_is_storage_question` 关键词过宽**：`agent/action.py:25-38` 的 `DATABASE_QUERY_KEYWORDS` 含 `"数据库"`、`"保存位置"`、`"db"` 等；`:138` 的条件只在"非 analyze 意图且无扫描词"时短路，边界用例（如"把结果保存位置告诉我然后扫一下"）容易被误判成纯问答而**静默不执行扫描**。
36. **`modules/registry.py` 的 import 期全量加载**：任何单个 adapter 的语法/依赖错误都会让 `import modules` 失败，进而 `/api/tools`、`/api/run`、`/api/tools` 全部 500（例如 `modules/enscan.py` 若缺依赖）。没有按需加载或容错注册。

---

## 8. 最小调试入口速查

> 所有命令的工作目录都是项目根 `get_everything_framework/`。以下仅列**实际读代码得出的**入口，未实测运行（避免发起真实扫描）。

| 目的 | 做法 | 关键观察点 |
|---|---|---|
| 看有哪些工具被注册 | `python -c "from modules import get_supported_runners as g; print(g())"` | 返回 17 个名字；少一个就是 `registry.py` 漏登记 |
| 看某个 Runner 的 category / config | `python -c "from modules import build_runner; r=build_runner('httpx'); print(r.category, r.config)"` | 能直接看出 `path` 是否写错（如 `http-x`） |
| 不跑子进程单独验落库 | `python -c "from storage import ScanResultStore as S; s=S(); print(s.save_dedicated_results('t.com','subfinder','subdomain',['a.t.com']))"` | 返回 `{run_id,scan_count,inserted_count}`；`inserted_count=0` 说明被 `UNIQUE(domain,subdomain)` 去重 |
| 看真实表结构与行数 | sqlite3 打开 `results/scan_results.db`，`select type,name from sqlite_master` | 实测 19 张表（`scan_runs`+`tool_results`+17 专属表+`sqlite_sequence`）；**没有** `subdomain_results`/`alive_results` |
| 验意图识别（离线，不联网） | `python -c "from agent.intent import analyze_intent; print(analyze_intent('扫一下 a.com 的子域名'))"` | 打印 `UserIntent`；用于定位 §6 第 11 行的分支顺序问题 |
| 验计划生成 | `python -c "from agent.intent import analyze_intent as a; from agent.planner import build_plan as b; i=a('扫一下 a.com 的子域名'); print(b(i,{}))"` | `None` 表示 `build_plan` 没有覆盖该 intent_type |
| 跑测试基线 | `python -m pytest -q`（`pyproject.toml` 已配 `pythonpath=["."]`） | 当前只有 2 个测试文件；全绿也只能说明导入/布局没问题 |
| 静态检查 | `ruff check .`（CI 用同一命令，规则集仅 `E4/E7/E9/F`） | 未使用导入、未定义名会被抓 |
| API 自检 | `curl http://127.0.0.1:5000/api/databases` | 返回 17 条，但**没有** `record_count`（与 docstring 不符） |
| 数据库文件位置 | `config.py:88` → `<项目根>/results/scan_results.db` | `GET /api/databases` 只给表元信息，不给路径 |

**改代码前的三个前置提醒**：
1. `results/scan_results.db` 已存在且是**真实数据**（含真实资产与 `results/outs/` 里的企业信息）；表结构变更靠 `CREATE TABLE IF NOT EXISTS` 不会自动迁移，需自行 ALTER 或删库重建。
2. `results/`、`uploads/`、`SecLists/`、`scripts/*.exe` 已被 Git 跟踪（见 §7.7 第 31 条），任何 `git add -A` 都会把扫描产物再次提交。
3. `app.py` 是模块级单例 `app`，改完直接 `python app.py` 会带 `debug=True` 的自动重载；测试里 `import app` 会连带 import 全部 API 与 modules（触发 17 个 Runner 可导入性检查）。

---

## 附：常用定位速查

| 想改什么 | 去哪儿 |
|---|---|
| 新增/修改一个工具的调用命令 | `modules/url_tools.py` / `modules/port_tools.py` / 独立 adapter 文件 + `config.py` 对应 `*_CONFIG` |
| 新增一个工具（三处登记） | `config.py`（CONFIG + TOOL_CATEGORIES）→ `modules/xxx.py`（继承 `BaseRunner`）→ `modules/registry.py:RUNNER_REGISTRY` → `storage.py:TOOL_DATABASES` |
| 改 API 出参/入参 | `api/*.py` 对应路由函数 |
| 改落库结构 | `storage.py:_init_db` / `_create_tool_table` / `TOOL_DATABASES`（注意 DB 已存在时 `CREATE TABLE IF NOT EXISTS` 不会迁移） |
| 改 Agent 识别逻辑 | `agent/intent.py:analyze_intent` 的关键词与分支顺序 |
| 改 Agent 计划内容 | `agent/planner.py:build_plan` + `agent/strategy_templates.py` |
| 真正接入大模型 | `agent/action.py`（构造并调用 `agent/client.py:LLMClient`），`agent/system_prompt.SYSTEM_PROMPT` 已是现成的 messages[0] |
| 加异步任务/状态机/error_code | 已实现于 `core/jobs.py` + `jobs/worker.py` + `jobs/executor.py`，见第 9 节 |

---

## 9. 本机联调版增量（M0 → M4）

> 第 1～8 节是**改动前基线**。本节只记录新增与改写，冲突时以本节为准。
> 里程碑验收报告（M0～M4）是本机过程材料，不随仓库分发；本节即公开可查的增量说明。

### 9.1 新增的目录与模块

```text
get_everything_framework/
├── core/                     ← 纯数据与模型层，不依赖 Flask（worker 也能用）
│   ├── errors.py             统一错误码 ErrorCode + AppError 及子类（含 http_status）
│   ├── errors_handlers.py    Flask 统一错误处理器 + 状态码→错误码映射表
│   ├── ids.py                带前缀的 UUID4（job_/step_/scope_/upload_/evt_…）
│   ├── db.py                 本机应用库连接（WAL + busy_timeout + BEGIN IMMEDIATE 事务）
│   ├── audit.py              audit_events 写入与查询
│   ├── db schema 见 core/db.py:init_schema  scopes / audit_events / uploads / jobs / job_steps / job_events / artifacts / exports / assets / observations
│   ├── scope.py              Scope 模型与目标校验（排除优先、拒全放行）
│   ├── scope_store.py        Scope 持久化 + require()（无 Scope 即拒绝）
│   ├── policy.py             P0-2：统一 Policy/Scope 引擎（Job 创建 / Step 执行前 / 解析后地址 / 重定向）
│   ├── exports.py            P0-5：导出登记 + 公开出参（不含 path）+ 下载文件解析
│   ├── canonical.py          P1：canonical_key 规则（subdomain/host/ip/cidr/url/port/service 的归一化）
│   ├── assets.py             P1：资产/观测两层模型 + Obs 落库 + Diff Engine（added/removed/changed/unchanged）
│   ├── migrate.py            P1 §12：旧库 → 新库的只读迁移（确定性观测 ID、时间归一、单条脏数据不中断）
│   ├── uploads.py            受控上传：uploads/<id>/{raw.*,normalized.txt,meta.json}
│   ├── mock.py               mock 执行结果（7 种场景，错误码对齐 §6.2）
│   ├── safety.py             mock/real 模式解析 + GEF_ALLOW_REAL_SCAN 开关
│   ├── security.py           SECRET_KEY 弱值检测 + 进程级一次性密钥
│   ├── auth.py               单一管理员 Token + Session + X-Local-Token
│   ├── health.py             /health 采集（database / worker / queue / tools / modes / security）
│   ├── runner_result.py      M4：Observation / ToolHealth / RunnerResult + scrub_command
│   ├── artifacts.py          M4：原始证据落盘（stdout/stderr/output）+ 登记 + 截断脱敏读取
│   └── jobs.py               job 数据层：状态机、步骤快照、认领/租约/cancel/retry/恢复
├── jobs/                     ← 进程层（刻意不放进 core/）
│   ├── executor.py           执行逻辑（与进程无关，可直接单测调用）
│   └── worker.py             独立 worker 进程：python -m jobs.worker
├── api/
│   ├── jobs.py               /api/jobs*（7 个接口）+ /api/jobs/<id>/artifacts + /api/artifacts/<id> + /api/jobs/<a>/diff/<b>
│   └── assets.py             P1：/api/assets*（列表/统计/详情）+ /api/observations（全部需管理员）
└── web/
    ├── templates/index.html  首页（Scope 下拉 + 创建任务 + 任务表）
    ├── templates/login.html  登录页
    ├── templates/assets.html P1：资产列表页骨架（筛选下拉 + 表格 + 详情面板）
    ├── static/app.css
    ├── static/app.js         无框架无 CDN：轮询 /health 与 /api/jobs，渲染进度条
    └── static/assets.js      P1：调 /api/assets 渲染资产表与观测时间线（无框架无 CDN）
```

### 9.2 路由增改对照（第 1.1 节表格的现状）

| 方法 | 路径 | 处理函数 | 鉴权 | 说明 |
|---|---|---|---|---|
| GET/POST | `/` | `app.py:index()` | POST 扫描需登录 | 模板已补齐（M1）；M2 起走 Scope 校验；**M3 起提交 = 创建异步任务**（不再同步扫描） |
| GET | `/login` | `app.py:login_page()` | — | M1 新增，Token 换 Session |
| GET | `/health` | `api/health.py`（独立 `health_bp`） | — | M1 新增；M2 加 `modes`/`security`，M3 加 `queue` |
| POST | `/api/auth/login` | `api/auth.py` | — | M1 新增；成功/失败均写审计 |
| POST | `/api/auth/logout` | `api/auth.py` | — | M1 新增 |
| GET | `/api/auth/session` | `api/auth.py` | — | M1 新增 |
| POST | `/api/scopes` | `api/scopes.py` | **需管理员** | M2 新增 |
| GET | `/api/scopes` | `api/scopes.py` | **需管理员** | M2 新增 |
| GET | `/api/scopes/{id}` | `api/scopes.py` | **需管理员** | M2 新增；不存在 → 404 `not_found` |
| POST | `/api/jobs` | `api/jobs.py` | **需管理员** | M3 新增；**202 + `queued`**，立即返回 `job_id` |
| GET | `/api/jobs` | `api/jobs.py` | **需管理员** | M3 新增；支持 `?status=`、返回 `counts` |
| GET | `/api/jobs/{id}` | `api/jobs.py` | **需管理员** | M3 新增；job + steps + events |
| POST | `/api/jobs/{id}/cancel` | `api/jobs.py` | **需管理员** | M3 新增 |
| POST | `/api/jobs/{id}/retry` | `api/jobs.py` | **需管理员** | M3 新增 |
| GET | `/api/jobs/{id}/steps` | `api/jobs.py` | **需管理员** | M3 新增（页面轮询用） |
| GET | `/api/jobs/{id}/events` | `api/jobs.py` | **需管理员** | M3 新增 |
| GET | `/api/jobs/{id}/artifacts` | `api/jobs.py` | **需管理员** | M4 新增；只给元数据（id/kind/size/sha256），**不下发路径** |
| GET | `/api/artifacts/{id}` | `api/jobs.py` | **需管理员** | M4 新增；返回内容（默认 ≤64 KB，截断+脱敏，无 `path`） |
| POST | `/api/run` | `api/scan.py` | **需管理员** | M2 起：必填 `scope_id`，拒绝 `file_path`，收 `upload_id`；`mode=mock`（默认）/`real` |
| POST | `/api/tool/<n>/run` | `api/scan.py` | **需管理员** | 同上，单工具 |
| POST | `/api/upload` | `api/upload.py` | **需管理员** | M2 起只返回 `upload_id`，不再暴露服务器路径 |
| GET/POST | `/api/settings*` | `api/settings.py` | **需管理员** | M1 加认证；M2 加原子写 + 备份 + 审计 |
| GET | `/api/tools`、`/api/databases`、`/api/results` | `api/tools.py`、`api/results.py` | 无 | 保持只读开放（方案只要求修改类 API 认证）；**已用 `test_api_auth_contract.py` 锁定现状** |
| GET | `/api/export` | `api/results.py` | 无 | **P0-5 起**返回 `export_id` / `filename` / `download_url`，**不再返回 `path`** |
| GET | `/api/export/{export_id}/download` | `api/results.py` | 无 | P0-5 新增；`send_file(as_attachment=True)`，未知/已清理的 id → 404 |
| GET | `/api/exports` | `api/results.py` | 无 | P0-5 新增；导出记录列表（同样不含路径） |
| GET | `/assets` | `app.py:assets_page()` | — | P1 新增；资产列表页。匿名可打开但只显示提示，**不下发 Scope 名称**；数据由 `static/assets.js` 调 `/api/assets` |
| GET | `/api/assets` | `api/assets.py` | **需管理员** | P1 新增；支持 `?scope_id=&type=&status=&search=&limit=&offset=`，非法 type/status → 400 |
| GET | `/api/assets/summary` | `api/assets.py` | **需管理员** | P1 新增；`by_type` 计数。与 `<asset_id>` 同前缀：Werkzeug 按「静态段优先」匹配，所以 `summary` 不会被当成资产 ID（`test_assets_summary_route_is_not_shadowed_by_asset_id` 锁住这一点） |
| GET | `/api/assets/{id}` | `api/assets.py` | **需管理员** | P1 新增；资产详情 + 观测时间线（倒序） |
| GET | `/api/observations` | `api/assets.py` | **需管理员** | P1 新增；**必须给 `asset_id` 或 `job_id`**，否则 400（拒绝无条件全表扫描） |
| GET | `/api/jobs/{a}/diff/{b}` | `api/jobs.py` | **需管理员** | P1 新增；Diff Engine，返回 added/removed/changed/unchanged + `counts`；任一 job 不存在 → 404 |

### 9.3 双库架构（**最容易踩的坑**）

| 库 | 路径 | 归属 | 谁在写 |
|---|---|---|---|
| 旧扫描结果库 | `results/scan_results.db` | `storage.py`（按工具建表，19 张） | `tool_runner.run_tools`（仅 `mode=real` 时）；本机联调期间视为**只读历史数据** |
| 新本机应用库 | `results/local.db`（可用 `LOCAL_DB_PATH` 覆盖） | `core/db.py` | `scopes` / `audit_events` / `uploads` / `jobs` / `job_steps` / `job_events` |

**新库的连接约定**（`core/db.py`）：`PRAGMA journal_mode=WAL` + `busy_timeout=5000` + `BEGIN IMMEDIATE`。
这解决了第 7.3 节记录的并发问题——但**只针对新库**。

**旧库（`storage.py`）的 P0 加固**：原先每个方法都写 `with self._get_connection() as conn:`，
而 `sqlite3.Connection` 的 `with` **只提交事务、不关闭连接**，于是每次查询漏一个文件句柄
（pytest 报 `ResourceWarning: unclosed file <_io.FileIO ... mode='rb+'>`，报错位置却指向 `conn.execute(...)`，
看起来像 execute 的锅）。现在：

* `storage.py:_connect()` = 「`try: with conn: yield conn` + `finally: conn.close()`」，事务语义不变；
* `_get_connection()` 加 `PRAGMA busy_timeout=5000`（连接级，不改库文件）；
* **表结构与查询语义一律未动**（`test_storage_connection.py::test_schema_is_unchanged` 锁定）；
* 旧库**仍无 WAL** —— WAL 需要改库文件持久属性，属迁移范畴，未在无人值守期间执行。

`core/health.py:database_health()` 的只读连接（`file:...?mode=ro`）同样改为显式 `close()`：
它在 `/health` 上被反复调用，泄漏会耗尽文件描述符。

测试切库：`tests/conftest.py` 同时 patch `storage.SQLITE_CONFIG["path"]`、`config.LOCAL_DB_CONFIG["path"]`、
`core.uploads.UPLOAD_DIR`、`core.health.OUTPUT_DIR`、`core.artifacts.ARTIFACT_DIR`、`exporter.EXPORT_DIR`，
并调 `core.db.reset_schema_cache()`。
**任何一个漏 patch 都会让测试往仓库 `results/` 里写文件。**

### 9.4 任务状态机（第 5.3 节的「不存在」已不成立）

```
queued ──claim──> running ──┬──> succeeded   （全部步骤成功）
                            ├──> partial     （部分步骤失败，error_code=partial_success）
                            ├──> failed      （全部失败，error_code=unknown_error）
                            ├──> timeout     （全部超时，error_code=timeout）
                            ├──> cancelled   （用户在步骤边界取消）
                            └──> interrupted （租约过期 / worker 优雅退出，可 retry）
```

* 终态：`succeeded / partial / failed / timeout / cancelled / interrupted`；
* **显式跃迁表（P0-7 新增）**：`core/jobs.py:ALLOWED_TRANSITIONS` + `can_transition(from, to)`。
  `finish_job` 会先读当前状态再比对，非法跃迁直接 `ValueError` —— 拦住
  `queued → succeeded`（没被 worker 领过就宣布成功）、`succeeded → failed`（成功被静默覆盖）这类跳步。
  同名状态视为**幂等**（worker 重复写同一终态不会炸）；未知状态一律拒绝。
* 可 retry：`interrupted / failed / timeout / cancelled / partial`（`succeeded` 与 `running` 拒绝）；
* **max attempts（P0-7 新增）**：`MAX_ATTEMPTS = 5`。超限后 `retry_job` 抛 `ValueError`，
  `POST /api/jobs/<id>/retry` 转 400，防止反复重试刷爆队列。
* retry 只重跑**未成功**的步骤，已成功的步骤保留（`job_steps` 是创建时就落好的快照）；
* **单表即队列**：`jobs` 自己就是队列，`claim_next_job` 用 `BEGIN IMMEDIATE` + `UPDATE ... WHERE status='queued'`
  保证同一个 job 只会被一个 worker 领到（`core/jobs.py`）；
* **租约**：领取时写 `worker_id` + `lease_until`；执行中每个步骤结束续租。
  worker 被 kill → 租约过期 → 新 worker 启动时 `recover_stale_jobs()` 标为 `interrupted`（不会静默消失）。
* **仍未实现**：`idempotency_key` 与 `backoff`（都需要给 `jobs` 新增列 = 改表结构，
  已登记 `docs/DECISIONS.md` §3 待授权）。`cancel_requested` 与 `heartbeat` 已有。

### 9.5 三个执行入口的差别（**排查「任务没跑」先看这里**）

| 入口 | 是否异步 | 是否走 Scope | 是否调用真实工具 |
|---|---|---|---|
| `POST /api/jobs` | ✅ 立即返回 `job_id` | ✅ 必填 `scope_id` | `mode=mock`（默认）不调用；`real` 需双开关 |
| `POST /api/run` | ❌ 同步 | ✅ 必填 `scope_id` | 同上 |
| 首页表单 `POST /` | ✅ 创建 job（M3 起） | ✅ 必填 Scope | 固定 `mock` |

真实扫描的**双开关**：环境 `GEF_ALLOW_REAL_SCAN=true` **且** `Scope.active_scan=true`，缺一即 403 `scope_violation`。

### 9.6 worker 进程

```powershell
# 单独跑（项目根）
python -m jobs.worker
python -m jobs.worker --once            # 只跑一轮（验收/CI）
python -m jobs.worker --step-delay 3    # 每步停 3 秒，便于观察进度与取消
powershell -ExecutionPolicy Bypass -File scripts\run_local.ps1   # 同时拉起 Web + worker
```

* 心跳文件 `results/worker_heartbeat`（mtime 30 秒内算 `ok`），`/health` 的 `worker` 字段读它：
  `ok` / `stale` / `missing`——这是「Web 正常但 worker 未启动」的判别依据；
* `--once` 之外都是常驻循环，`Ctrl+C`/SIGTERM 时把在跑的任务立刻标 `interrupted`（`release_job`）；
* **子进程 stdout 不要接一个父进程不读的管道**：对端关闭会让 worker 在 `print` 时 `BrokenPipeError`
  并以退出码 120 死掉（`tests/unit/test_jobs_executor.py` 里有注释记录这个坑）。

### 9.7 本机联调期间仍未修的（按方案分派到后续里程碑）

| 项 | 现状 | 计划 |
|---|---|---|
| `storage.py` 并发 | **P0 已修一半**：连接必定关闭 + 连接级 `busy_timeout=5000`；**仍无 WAL**（WAL 需重建库文件，属迁移范畴） | M5 迁移时补 WAL |
| `/api/export` | **P0-5 已解决**：登记制 + `download_url` + `GET /api/export/<id>/download`，不再返回路径 | — |
| `/api/results`、`/api/tools`、`/api/databases`、`/api/export`、`/api/exports` | 仍匿名可读 | 按 `docs/DECISIONS.md` D **有意保持**，已用 `test_api_auth_contract.py` 锁定现状；收口需授权 |
| 前端轮询 | 任务表 3 秒轮询 `/api/jobs`，未做 SSE/WebSocket | 本机联调够用 |
| 真实 runner 的结构化结果 | **已落地**（M4）：统一 `RunnerResult`，见 §9.10 | — |
| `assets` / `observations` / `artifacts` 表 | `artifacts` 已建（M4）；`assets` / `observations` 未创建 | M5（E 项已预授权） |
| 敏感产物仍在 Git 索引 | **已解决**：自有仓库 `geteverything` 只保留一份干净历史，`results/`、`uploads/`、`SecLists/`、`scripts/*.exe` 均未入库 | — |
| Scope 判定位置 | **P0-2 已统一**到 `core/policy.py`（见 §9.11） | — |
| Agent 的执行边界 | **P0-3 部分**：已禁止任意 `file_path`，但仍直接调 `run_tools` / runner，未改走 Job Service | P0-6，需授权 |
| `jobs` 表幂等与退避 | 无 `idempotency_key` / 无 `backoff`（需 ADD COLUMN = 改表结构） | 已登记 `docs/DECISIONS.md` §3 待授权 |

### 9.8 测试与验收基线

```text
$ python -m ruff check .     # All checks passed!
$ python -m pytest           # 715 passed, 2 skipped, 0 failures
$ python -m mypy app.py core api jobs storage.py modules   # Success: no issues found in 59 source files
$ python -m pytest -m "not slow"   # 跳过起真实子进程的 kill/重启用例
```

> 演进：M1 `70` → M2 `142` → M3 `236` → M4 `405` → P0 加固 `538` → P1 资产/观测/Diff/迁移 `701` → M7 类型收口 + Diff 可点 `707` → **M5 字典可移植性 `715`**。
> **P0 起 `pytest` 已零 warning**（原两条见 `PROJECT_STATE.md`「已修的两条 warning」）。
> P1 新增 `core/assets.py` 时一度引入 10 条 mypy 报错（`result` / `items` 少了类型标注），
> 补标注后回到 34；**M7 把剩下的 34 条全部清掉**（见 §9.13）。
> 注意口径：`mypy` 只检查上面这条命令列出的范围，`agent/providers/` 不在其中
> —— 那一层**没有任何调用方**（§7.6），且它在 Windows 的 openai 存根下会报 7 条
> 与真实缺陷无关的类型错。要连它一起查得显式加 `agent` 参数。

| 测试文件 | 覆盖 |
|---|---|
| `tests/unit/test_smoke.py`、`test_repo_layout.py` | 导入与仓库布局（M0） |
| `tests/unit/test_scope.py` | Scope 匹配语义与全放行拒绝（M1） |
| `tests/unit/test_security_baseline.py` | SECRET_KEY 弱值、受控上传、`.env` 原子写（M2） |
| `tests/unit/test_jobs_store.py` | 状态机、认领、租约、恢复、cancel、retry、**跃迁表 + max attempts**（M3 / P0-7）、**`get_job_or_raise` 与 `get_job` 的可空性差异**（M7） |
| `tests/unit/test_jobs_executor.py` | mock/real 分流、进度、取消边界、**真实子进程 kill/重启**、**执行期 Scope 复检**（M3 / P0-2） |
| `tests/unit/test_runner_result.py` | 命令预览脱敏、`RunnerResult` 组装、artifact 落盘/读取（M4） |
| `tests/unit/test_runner_interface.py` | **真实子进程**：成功/零结果/未安装/非零/127/超时/SystemExit/残留文件清理（M4）；**基类未实现 `run_scan` 必须报失败**、**子类 `_write_input_file` 保留 `suffix`**（M7） |
| `tests/unit/test_runners_m4.py` | subfinder / httpx / dnsx 的 `build_command` + `parse_output`（M4） |
| `tests/unit/test_runners_m4_rollout.py` | **其余 14 个 runner** 的接口覆盖 + 解析 + 横切自检（M4 铺开）；**M5**：字典配置可移植（不是开发机绝对路径、必落在项目根内）、字典缺失时 `config_error` 且**不启动子进程**、`wordlist=None` 不算错、新错误码常量与前端标签同步 |
| `tests/unit/test_policy.py` | **P0-2**：统一 Policy 四个入口（缺失 400 / 越界 403 / 整体拒绝 / 解析后地址校验，注入 resolver 不查真实 DNS） |
| `tests/unit/test_agent_boundary.py` | **P0-3**：Agent 拒绝任意 `file_path`、只收 `upload_id`、planner 不再下发 `file_path`；**M7**：httpx 步骤的 `items` 必须是元数据字典（回归 `'str' object has no attribute 'get'`） |
| `tests/unit/test_storage_connection.py` | **DECISIONS-I**：旧库连接必关（含异常路径）、`busy_timeout`、表结构未变、`-W error::ResourceWarning` 复现 |
| `tests/unit/test_canonical.py` | **P1 §9**：七种类型的归一化规则、方案验收的三个 URL 折叠成一个 key、`guess_type` 不猜错 |
| `tests/unit/test_assets.py` | **P1 §8/§10**：两层模型（一行资产 + N 条观测）、`first_seen` 不被覆盖、scope 参与唯一性、`%`/`_` 转义、状态迁移不删数据、Diff 验收（A B C → A C D）、取消 unchanged 明细后计数仍准、category→type 映射、落库失败不改任务结果 |
| `tests/unit/test_migrate_legacy.py` | **P1 §12**：dry-run 与 `--apply` 前后旧库 sha256 不变、重跑幂等（确定性观测 ID）、`web`→`url` 翻译、`...Z`→`+00:00` 归一、跨表同资产合并成一行、单条失败不中断、CLI 三个退出码 |
| `tests/integration/test_web_baseline.py` | 首页可渲染、登录/登出（M1） |
| `tests/integration/test_m2_security.py` | 认证、受控上传、file_path 拒绝、审计（M2） |
| `tests/integration/test_m2_scope_enforcement.py` | 无 Scope/越界拒绝、mock 不碰真实 runner（M2） |
| `tests/integration/test_m2_page_scan.py` | 首页 = 异步任务、不阻塞（M2/M3） |
| `tests/integration/test_m3_jobs_api.py` | 7 个 jobs 接口、10 个任务响应时间、状态持久化（M3） |
| `tests/integration/test_m4_runner_result.py` | RunnerResult 端到端：零结果 vs 失败、artifact 不下发路径（M4） |
| `tests/integration/test_export_contract.py` | **P0-5**：导出响应无 `path`、可下载、未知/已清理 id → 404、`safe_prefix` 穿越表、前缀逃不出导出目录 |
| `tests/integration/test_api_auth_contract.py` | **P0-1/D**：锁定「哪些只读接口匿名、哪些必须 401」的当前契约 + 响应体不夹带服务器路径 |
| `tests/integration/test_assets_api.py` | **P1**：资产接口全部 401（未登录）、「任务跑完 → 资产可查」端到端链路、`summary` 未被 `<asset_id>` 吃掉、`/api/observations` 拒绝无条件全表扫描、Diff 端点 404 与 `include_unchanged`、**diff 条目必带可用的 `asset_id`**、资产页骨架与匿名时不下发 Scope 名、**静态脚本已把 diff 条目接成点击**、响应无服务器路径 |

### 9.9 第 6 节 BUG 索引表的**现状修正**

| 原条目 | 现状 |
|---|---|
| #1「一直卡在 running / 请求不返回」 | 已解决：`POST /api/jobs` 立即返回；`running` 是真实执行状态，有进度与租约 |
| #2「立刻失败无日志」 | 部分解决：`api/scan.py` 把 `SystemExit`/`ValueError` 统一转成稳定 `error_code`；`jobs/executor.py` 兜住 `SystemExit`（否则会带走 worker） |
| #4「结果页看不到数据」 | 未动（属 M5 资产页） |
| #5「上传解析出错 500」 | 已解决：`api/upload.py` 不再暴露路径；空目标 → 400 `bad_request` |
| #23「SECRET_KEY 默认固定值」 | 已解决：默认值清空，弱值告警 + 进程级一次性密钥，有回归测试 |
| #24「/api/settings 可匿名写 .env」 | 已解决：需管理员；原子写（临时文件+fsync+`os.replace`）+ 写入前备份 + 审计（只记字段名） |
| #32「首页无模板」 | 已解决：`web/templates/` 与 `web/static/` 已补齐 |
| #33「测试覆盖极薄」 | 已解决：**538** 项，含真实子进程 kill/重启 |
| #34「无 create_app()」 | 已加 `create_app()`，但仍保留模块级单例 `app`（测试与 waitress 共用） |
| #2 第 1 条「残留输出文件」 | **已解决**（M4）：`_execute` / `_execute_stdout` 执行前先删同名旧文件；删不掉时写 `stale_output_warning` 到 `last_execution`，不再把上次输出当本次结果 |
| #2 第 5 条「SQLite 并发」 | **旧库已缓解**（P0）：`storage.py` 连接必关 + 连接级 `busy_timeout=5000`；仍无 WAL。新库（`core/db.py`）本来就是 WAL + `busy_timeout` |
| #22「子域爆破类工具总是零结果」 | **已解决（配置与失败形态）**（M5）：字典路径改为按项目根解析的仓库相对路径 + 环境变量覆盖；配置了字典却不存在时抛 `config_error` 且不启动子进程。**字典本身仍不随仓库分发**，需自行下载或改环境变量（§9.14） |

### 9.10 M4：统一结果与错误模型（**「失败被吞成空结果」的终点**）

§9.9 与第 7.7 节都记过这个 P0：`modules/base.py:_execute` 把
`FileNotFoundError` / `TimeoutExpired` / `CalledProcessError` 全部降级成 `return False`，
Runner 再 `return []`，调用方**无法区分**「工具没装」「超时」「非零退出」「本来就没结果」。
M4 起这条链路被拆开：

```text
core/runner_result.py
  ├── Observation     一条结构化观测：category + value + data + source_tool
  ├── ToolHealth      工具健康度：status + message + checked_at
  └── RunnerResult    status / error_code / exit_code / duration_ms /
                      command_preview / data[] / stderr_preview / parser_version
      ├── ok(data, ...)        成功（error_code 可为 no_results）
      ├── failure(code, ...)   失败
      ├── to_dict()            API 出参
      └── to_step_outcome()    写进 job_steps 的形状
```

**统一接口**（方案第 8.1 节）：每个 runner 都要实现

| 方法 | 契约 |
|---|---|
| `build_command(target, options)` | 只拼命令行，**不执行**、不碰磁盘；`options` 至少支持 `output_file` |
| `parse_output(stdout, stderr, artifacts)` | 只解析，返回 `(values, error_code)`；不抛异常、不执行 |
| `run(target)` | 基类提供：执行 + 解析 + 组装 `RunnerResult`，**绝不返回裸空列表** |
| `run_scan(target)` | 历史签名保留：返回字符串列表，供 `tool_runner.py` 等旧调用方使用 |

**17 个 runner 的铺开状态**（以 `modules/registry.py` 为准）：

| 分类 | runner | 输出方式 | 备注 |
|---|---|---|---|
| 子域发现 | `subfinder` | `-o <file>` | 首批 |
| | `amass` / `amass_intel` | `-o <file>` | amass_intel 入参是 ASN，非法即 `ValueError` |
| | `assetfinder` | stdout | 无 `-o`；解析时按目标域正则规范化 |
| | `oneforall` | stdout | 经 `python oneforall.py ... run` 调用 |
| | `enscan` | **自有工作目录下的 `*.json`** | 靠执行前后 diff 找新文件；必须传 `cwd=output_dir`，并走带超时的 `_run_subprocess` |
| | `alterx` | `-l <in> -o <out>` | 候选列表来自 `ScanResultStore`，缺 `input_file` 抛 `KeyError` |
| | `shuffledns` | 无输出文件（内部调 `dnsx`） | `build_command`/`parse_output` 描述的是「用 dnsx 解析一批候选」；混合流程（字典爆破 + 已有候选 + 泛解析过滤）仍在 `run_scan` |
| DNS / HTTP | `dnsx` | `-l <in> -o <out>` | 首批 |
| | `httpx` | `-l <in> -json` | 首批；`Observation.data` 保留 status_code / title / webserver / tech / cdn |
| 爬虫 | `gospider` | stdout | |
| | `katana` | `-o <file>` | |
| | `waybackurls` | stdout | |
| 目录 | `feroxbuster` | `-o <file>` | `--json` 时按行解析 JSON 取 `url`，坏行跳过；字典按项目根解析，缺失即 `config_error` |
| | `dirsearch` | `-o <file>` | `wordlist` 为空则不加 `-w`；配了却不存在同样是 `config_error` |
| 端口 | `naabu` | `-o <file>` | |
| | `nmap` | `-oN <file>` | 正因如此 `OUTPUT_FLAGS` 才需要认 `-oN/-oX/-oG/-oA` |

**三个只有在真机/Linux 上才会暴露的缺陷**（都在 M4 修掉，都有回归测试）：

1. **残留输出文件被当成本次结果**。`_execute` 的结果由工具写盘，若上一轮留下了同名文件，
   本轮工具失败时 `_read_results` 会把**上次的输出**读回来当成功。
   现在 `_execute` / `_execute_stdout` 都在执行前先删（`_clear_stale_output_for`），
   删不掉就记 `stale_output_warning`。
   注意 `_execute_stdout` 的输出文件**不在命令行里**，所以必须显式经
   `_record_execution(..., output_file=...)` 记下来，否则证据采集与残留清理都找不到它。
2. **Windows 下超时杀不掉孙进程**。`.cmd` 工具链是 `python(worker) → cmd.exe → 工具`，
   `subprocess.run(timeout=)` 只杀掉中间层，孤儿进程攥着 stdout/stderr 管道，
   `subprocess.run` 会**永远等不到管道关闭**——「超时」形同虚设，worker 被永久占住
   （实测：任务卡在 `running`，留下孤儿 PID）。改为
   `Popen` + `communicate(timeout)` → `_kill_process_tree`（Windows `taskkill /F /T`）
   → 再 `communicate(timeout=PROCESS_DRAIN_SECONDS)` 排空。
3. **POSIX 下 `killpg` 会连调用方一起杀**。`Popen` 不传 `start_new_session=True` 时，
   子进程与 worker 同属一个进程组，`os.killpg(os.getpgid(child), SIGKILL)` 的杀伤范围
   包含 worker 自己——表现为 Linux/CI 上测试进程凭空消失（本地 Windows 全绿，因为
   `start_new_session` 被忽略、走的是 `taskkill`）。现在 `_run_subprocess` 在 POSIX 下
   另起进程组，且 `_kill_process_tree` 会先比对 child / own 进程组，同组时只 `kill()` 直接子进程。

**产物与脱敏**：`core/artifacts.py` 把每次执行的三种证据落盘（表 `artifacts`，M4 新建）

| kind | 内容 | 落盘后缀 |
|---|---|---|
| `stdout` | 子进程 stdout | `.out` |
| `stderr` | 子进程 stderr | `.err` |
| `output` | 工具自己写的输出文件（`-o` / `-oN` / enscan 的 JSON） | `.result` |

* 超过 `SCAN_LIMITS["max_artifact_bytes"]`（默认 2 MB）会被截断并附截断提示；
  `jobs/executor.py` 另用 `MAX_RESULT_EVIDENCE_BYTES`（1 MB）限制单份结果证据；
* 读取接口 `GET /api/jobs/<id>/artifacts`（列表）与 `GET /api/artifacts/<id>`（内容）
  **都不下发服务器路径**（`list_artifacts` 走 `_row_to_dict(..., include_path=False)`），
  内容读取默认上限 64 KB（`DEFAULT_READ_LIMIT`）并显式返回 `truncated`；
* `command_preview` 经 `core/runner_result.py:scrub_command` 处理，覆盖
  `--api-key=xxx`、`--api-key xxx`、URL 里的 `user:pass@`，以及 20 位以上的长 token。

**为什么 `scrub_command` 要留着长路径**：回归测试 `test_run_command_preview_is_redacted`
最初把 `results/` 里的长路径也打码了，排查时反而看不出工具到底读了哪个文件。
判断标准是「像不像密钥」而不是「长不长」——正则用前后向断言排除路径分隔符后，
`results/...` 这类路径会原样保留（测试里断言 `"results" in preview`）。

### 9.11 P0 产品化加固（M4 之后，按 DSH 执行方案 P0 清单）

> 这一节是 §9.1～§9.10 之后的增量。改动只做「收口既有边界」，**没有引入新框架、
> 没有改技术栈、没有改既有表结构**（`exports` 表是新增的，`jobs` 表一个列都没加）。

#### 9.11.1 `core/policy.py` —— 统一 Policy / Scope 引擎（P0-2）

加固前，Scope 判断散落在三处：`api/scan.py` 自己解析 mode + 查 scope_store，
`api/jobs.py` 又写一遍，`jobs/executor.py` 执行期**完全不查**。
结果是「任务创建时合法，执行时 Scope 已被删/被改」这条缝没人管。

现在只有一个入口模块：

| 函数 | 时机 | 语义 |
|---|---|---|
| `validate_job_targets(scope_id, targets)` | 创建任务 / 同步扫描 | `scope_id` 缺失 → 400 `bad_request`；Scope 不存在 → 403 `scope_violation`；任一目标越界 → 403（**整体拒绝，不部分执行**） |
| `validate_step_target(scope_id, target)` | **每个 real 步骤执行前**（`jobs/executor.py`） | 同上，单目标版本；失败时该步骤记 `scope_violation`，Runner **不会被调用** |
| `validate_resolved_address(scope, host, resolver=...)` | DNS 解析之后 | 解析出的每个 IP 都要落在 Scope 允许范围；loopback / private / link-local 默认拒绝，除非显式写进 `allowed_cidrs` |
| `validate_redirect_target(scope, url)` | HTTP 重定向后 | 只允许 `http` / `https`；重定向到 Scope 外的地址即拒绝 |

配套：`is_dangerous_address()`（SSRF 味道的地址判定）、`resolve_host()`、`scope_address_allowed()`、
`require_scope()`。解析器用参数注入（`Resolver = Callable[[str], list[str]]`），
所以单测**不发真实 DNS**。

测试：`tests/unit/test_policy.py`（约 40 例）、
`tests/unit/test_jobs_executor.py`（「执行期 Scope 复检」：删掉 scope 行后 Runner 一次都没被调用）。

#### 9.11.2 Agent 执行边界（P0-3）

`agent/action.py:_tool_subdomain` 原先接受请求里传来的任意 `file_path` 并直接读文件。
现在：

* **任何** `file_path` 直接拒绝（不是"过滤成安全路径"，是拒绝）；
* 只接受受控 `upload_id`，经 `core/uploads.py:resolve_targets_file()` 换取真实路径；
* `agent/planner.py:build_uploaded_file_plan()` 只下发 `upload_id`；
  老的「只有 `file_path` 的历史记录」不再生成 steps，而是回一句提示要求重新上传；
* 工具 schema 里的参数名也从 `file_path` 改成 `upload_id`——避免模型照着旧名字生成调用。

测试：`tests/unit/test_agent_boundary.py`（16 例，含 `../../` 穿越的 `upload_id` 被拒）。

**仍未做**（P0-6）：Agent 依旧直接调 `tool_runner.run_tools` / `HttpxRunner.run_scan`，
没有改走 Job Service。这是「Agent 提议 = 执行」的残留，改动面涉及 Agent 主流程重排，
属需要授权的项。

#### 9.11.3 导出不再泄露路径（P0-5）

```text
加固前：GET /api/export          → {ok, path: "E:\\...\\exports\\all_results_20261001.csv", count, format}
加固后：GET /api/export          → {ok, export_id, filename, format, row_count, size, sha256,
                                    created_at, download_url}
        GET /api/export/<id>/download  → send_file(as_attachment=True)
        GET /api/exports               → 导出记录列表（同样不含 path）
```

* `core/exports.py` 把导出登记进新表 `exports`（`filename` / `path` / `format` / `row_count` /
  `size` / `sha256` / `created_at` / `created_by`）；
* `to_public_dict()` 是**唯一的出参构造点**，`path` 只在 `get_export()` 的内部形态里出现；
* `get_export(export_id)` 对含 `/` 或 `\` 的 id 直接拒绝，防止把 id 当成路径片段；
* 文件被清理后 `resolve_export_file()` 返回 `None` → 下载路由 404（有测试）；
* `exporter.py:safe_prefix()`：文件名前缀只保留 `[A-Za-z0-9._-]`，折叠连续 `.`，限长 64，
  首尾 `._` 去掉——`../../evil` → `evil`（参数化测试覆盖 8 种输入）。

**保留的已知项**：这些导出接口仍是**匿名可读**（`docs/DECISIONS.md` D：不改鉴权行为）。
`tests/integration/test_export_contract.py::test_export_endpoints_are_currently_anonymous`
把这件事显式写成测试，避免以后有人"顺手"加鉴权却不知道会破坏本机脚本。

#### 9.11.4 任务状态机收口（P0-7）

* `ALLOWED_TRANSITIONS`：`queued → {running, cancelled}`；`running → 六个终态`；
  五个可重试终态 `→ queued`；`succeeded → {}`（绝对终态）；
* `can_transition(from, to)`：同名幂等、未知状态一律 `False`；
* `finish_job` 在事务里先读当前状态再比对，非法跃迁抛 `ValueError`
  （`POST /api/jobs/<id>/retry` 会把 `ValueError` 转成 400）；
* `MAX_ATTEMPTS = 5`：`retry_job` 到上限即拒绝，防止有人写脚本无限重试刷爆队列。

测试：`tests/unit/test_jobs_store.py` 的 `test_can_transition`（16 组参数化）
与 `test_finish_job_rejects_*` / `test_retry_respects_max_attempts`。

**未做的部分**：`idempotency_key`、`backoff` 需要给 `jobs` 加列（改表结构），
已登记 `docs/DECISIONS.md` §3 等授权。`cancel_requested` 与 `heartbeat` 原本就有。

#### 9.11.5 旧结果库连接生命周期（DECISIONS-I）

见 §9.3 的「旧库（`storage.py`）的 P0 加固」。

#### 9.11.6 只读接口的鉴权现状被显式锁定（P0-1 / DECISIONS-D）

`tests/integration/test_api_auth_contract.py` 把两类事实写死成断言：

* **匿名可读**（有意保持）：`/api/tools`、`/api/databases`、`/api/results`、`/api/export`、`/api/exports`；
* **必须 401**：`/api/settings`、`/api/scopes`、`/api/upload`、`/api/run`、
  `/api/tool/<n>/run`、`/api/jobs*`、`/api/artifacts/<id>`、首页表单扫描。

另外断言 `/api/auth/session` 匿名可用（前端靠它判断登录态），
以及 `/api/tools` / `/api/results` 的响应体里**不夹带服务器路径**。
将来任何一侧发生变化，这两个测试会失败——那时要同步改的是
`docs/DECISIONS.md`、`SECURITY.md` 和 README 的鉴权列，而不是删除测试。

### 9.12 P1：统一资产模型与 Diff（方案第 8、9、10 节）

> 这一节是 P1 的第一批落地。**只新增表与模块**：`assets` / `observations`
> 是两张新表，`jobs` 一个列都没加（DECISIONS-E 的「纯增量」约束）。
> 旧的 `storage.py` 工具表、`/api/results` 接口**一行没动** —— 两套模型并存。

#### 9.12.1 为什么需要这一层（`core/assets.py`）

加固前是「**每个工具一张表**」：同一台机器被 subfinder 和 httpx 各发现一次，
就是两条互不相干的记录，既回答不了「这个资产是谁先发现的」，也做不了可靠 diff。
现在改成方案第 8 节的两层模型：

```text
assets（唯一资产，canonical_key 去重）
   ↓ 1 : N
observations（每次观测一行：谁 / 何时 / 当时什么属性）
```

| 表 | 列 | 关键约束 |
|---|---|---|
| `assets` | `id` / `scope_id` / `canonical_key` / `type` / `value` / `first_seen` / `last_seen` / `status` / `confidence` / `metadata_json` | 唯一索引是 **`(canonical_key, IFNULL(scope_id,''))`**，不是 `canonical_key` 单列 |
| `observations` | `id` / `asset_id` / `job_id` / `step_id` / `run_id` / `source_tool` / `observed_at` / `parser_version` / `raw_artifact_id` / `data_json` | `step_id` 是 P1 额外加的（排查「哪个 job 的哪一步」） |

> ⚠️ **`scope_id` 必须参与唯一性**：同一台主机在两个 Scope 下是两条彼此独立的资产。
> 早先版本把 `canonical_key` 建成全局唯一，结果「A 范围的资产在 B 范围里查不到、
> 却又插不进去」，`test_different_scope_means_different_asset` 专门守这一点。
> 索引里用 `IFNULL(scope_id,'')` 而不是裸两列，是因为 SQLite 的 `UNIQUE`
> 允许多个 `NULL`，裸索引会让「不属于任何 Scope」的行绕过去重。

**`first_seen` 永不被覆盖**，`last_seen` 每次观测都推进 —— 那两列就是「首次/最近发现时间」。
`metadata` 用 `setdefault` 合并：人工订正过的 `owner` 不会被后续重跑冲掉。

**状态迁移不删数据**：`mark_stale_assets()` 只把 `status` 改成 `stale`，
行还在、观测时间线也还在（方案明令禁止删历史数据）。

#### 9.12.2 `canonical_key` 规则（`core/canonical.py`，方案第 9 节）

```text
canonical_key = f"{type}|{normalized_value}"      # 例：url|https://example.com/
```

| 类型 | 归一化要点 |
|---|---|
| `subdomain` / `host` | 小写、去尾点、IDNA 编码、RFC1123 标签校验（**允许** `_`，`_dmarc.example.com` 真实存在） |
| `ip` | `ipaddress` 解析（IPv6 压成最简形式；`[::1]` 也接受） |
| `cidr` | `strict=False`：`10.0.0.5/8` 收敛成 `10.0.0.0/8` 而不报错 |
| `url` | 协议小写、host 小写+IDNA、**默认端口丢弃**、空路径补 `/`、query 保留、fragment 丢弃、带凭据直接拒绝、缺协议补 `http://`、非 `http(s)` 协议（含 `javascript:`）拒绝 |
| `port` | 必须 `host:port`，端口 1–65535 |
| `service` | 小写，允许 `-._+/`（如 `ssl/vpn`） |

方案第 9 节的验收就是 `test_plan_acceptance_urls_collapse_to_one`：

```text
HTTPS://Example.COM/  ┐
https://example.com   ├─→  同一个 canonical_key："url|https://example.com/"
https://example.com:443/ ┘
```

`normalize()` 对未知类型抛 `CanonicalError`；`guess_type()` 只做**保守**猜测
（`file:///etc/passwd` 返回 `None`，不硬塞成 URL）。

#### 9.12.3 从任务步骤落观测（`ingest_step_observations`）

`jobs/executor.py` 在 `finish_step` 之后调一次 `ingest_step_observations(step, outcome, scope_id=...)`：

* **数据来源**优先 `outcome["observations"]`（M4 起 runner 给的结构化观测，
  含 httpx 的 `status_code` / `title` / `technology`）；
* 为空才退化到 `outcome["results"]`（旧签名的纯字符串列表），
  并且**只对「明确知道字符串形态」的工具兜底**（`subfinder`/`amass`/`assetfinder`/
  `oneforall`/`alterx`/`shuffledns` → `subdomain`，`dnsx` → `host`）；
  `httpx`/`naabu`/`nmap` 等形态不确定的**一律跳过** —— 把 `1.2.3.4:80` 当 subdomain
  存进去比不存更糟（`test_ingest_step_observations_skips_unknown_string_tools`）。

> ⚠️ **`Observation.category` 不等于资产类型**。`category` 是旧代码就有的字段
> （`storage.py:TOOL_DATABASES` 与各 runner config 在用），取值是 `web` / `alive` /
> `dns` / `url` / `port` / `subdomain`；而资产类型是 `subdomain` / `host` / `ip` /
> `cidr` / `url` / `port` / `service`。两者名字像、语义不同，所以有显式映射表
> `CATEGORY_TO_TYPE`（`web`→`url`、`alive`/`dns`→`host`）。**直接拿 category 当 type
> 会让 `web`/`alive`/`dns` 三种整体变成非法类型而被丢掉。** 未列出的 category 返回
> `None` 并计入 `skipped`，不猜。

**落观测失败绝不影响任务结果**：`ingest_step_observations` 内部吞掉所有异常，
只把原因写进返回值；`executor` 把它记成 `step.assets_ingested` 事件
（`written` / `skipped` / 前 5 条 `reasons`）。这样「资产页为什么少了几条」
能在任务详情里直接看到。观测是**派生产物**，原始结果早已写进 `job_steps` 与 `artifacts`。

#### 9.12.4 Diff Engine（`diff_jobs`，方案第 10 节）

```text
scan N  ─┐
         ├─→ diff_jobs(before_job_id, after_job_id) → added / removed / changed / unchanged
scan N+1 ┘
```

方案第 10 节的验收例子 `test_diff_plan_acceptance_added_removed_unchanged`：

```text
第一次 A B C            第二次 A C D
────────────────        ────────────────
added     = [D]
removed   = [B]
unchanged = [A, C]
changed   = []
counts    = {added:1, removed:1, changed:0, unchanged:2}
```

`changed` 只在**可 diff 属性**变化时产生，取值见 `DIFFABLE_ATTRIBUTES`：

```text
status_code / title / server / technology / url
```

> ⚠️ **`duration_ms` / `checked_at` 这类一次性字段必须排除**，否则每次扫描
> 全表都是 `changed`，diff 直接废掉（`test_diff_ignores_volatile_attributes`）。
> 单个属性最多列 `MAX_DIFF_ATTRIBUTE_ITEMS = 20` 项，防止 metadata 巨大时刷屏。
> `_changed_attributes` 的语义是「后一次没有这个属性 → `to: None`」，
> 所以 `200 → None` 是**真实的属性消失**，不是 bug。

`scope_id` 过滤的口径：过滤后另一侧可能整批消失，**这时应该什么都不报**，
而不是把范围外资产误报成 `removed`（`test_diff_respects_scope_filter` /
`test_diff_without_scope_filter_sees_both_assets` 一对用例把两个方向都钉住）。

`include_unchanged` 的口径 —— **只控制明细下发，不控制计数**：

```text
include_unchanged=True    → unchanged 明细 + counts.unchanged = 真实数量
include_unchanged=False   → unchanged=[] + counts.unchanged = 真实数量  ← 关键
```

> ⚠️ 初版把两者一起清零，等于让「这次真的扫到了、只是没变化（未变 3 条）」
> 与「这次什么都没扫到（未变 0 条）」变成同一个输出 —— 而这正是 diff
> 最需要回答的问题。**计数与明细必须分开处理**，由
> `test_diff_can_omit_unchanged_details` 与 API 侧同名用例双向锁定。

#### 9.12.5 资产 API 与页面（DECISIONS-G）

新增 `api/assets.py`（全部**需管理员**）与 `web/templates/assets.html` +
`web/static/assets.js`（无框架、无 CDN，与 `app.js` 同一套做法）。

* `GET /api/assets` —— 列表；非法 `type` / `status` → 400 并把 `supported` 放进 `details`；
* `GET /api/assets/summary` —— 分类型计数；
* `GET /api/assets/{id}` —— 详情 + 观测时间线；
* `GET /api/observations` —— **必须给 `asset_id` 或 `job_id`**，否则 400（拒绝无条件全表扫描）；
* `GET /api/jobs/{a}/diff/{b}` —— Diff，任一 job 不存在 → 404。

> ⚠️ `/api/assets/summary` 与 `/api/assets/<asset_id>` 同前缀。Werkzeug 按
> 「静态段优先」匹配，所以 `summary` **不会**被 `<asset_id>` 吃掉；
> `test_assets_summary_route_is_not_shadowed_by_asset_id` 把这一点锁成断言 ——
> 这个坑不写测试，将来改路由顺序时无声无息地坏掉。

页面 `/assets` **不强制登录**（否则匿名用户连导航都点不进来），但：
① 未登录时不下发 Scope 名称，只给提示；② 接口返回 401。
资产是「整理后的情报」，比原始结果行更敏感，所以归在需要登录的一侧 ——
与 `/api/results`（旧库、匿名只读）刻意区分开。

页面底部还有 **「两次任务对比」表单**（P1 §10 的前端露出）：选基线任务与对比任务
（下拉默认选中最近两次）、可选限定 Scope 与「含未变」开关，结果按
新增 / 消失 / 变更 / 未变 四段渲染，`changed` 直接显示
`status_code: 200 → 403` 这类属性差异。任务下拉由 `GET /api/jobs?limit=50` 填充 ——
该接口**需管理员**，所以脚本显式判 `resp.status === 401` 并转成提示而不是抛错。

> 「含未变」勾掉时服务端只给计数不给明细，前端据此显示
> 「（按设置未取明细，共 N 条）」而不是「无」—— 否则用户会把
> 「没要明细」读成「两次完全一致」。

**Diff 条目可点进资产详情**：每条明细都带 `asset_id`，脚本据此给条目加上
`diff-item-clickable` 并绑定点击 → 复用列表页的 `openDetail()`（详情面板在页面另一头，
打开后会 `scrollIntoView`，否则点了像没反应）。`asset_id` 为空时不加标记，保持死文本。
这一层由两个断言夹住：服务端侧 `test_diff_items_always_carry_asset_id`
（且详情接口真认这个 id），前端侧 `test_assets_js_wires_diff_items_to_asset_detail`
（静态脚本无构建链，漏接线没有别的方式能发现）。

#### 9.12.6 旧库 → 新库的迁移（`core/migrate.py` + `scripts/migrate_legacy_results.py`）

方案第 12 节「数据库收口」落到可执行层。口径先钉死（方案原文）：

```text
旧库 = legacy read-only      （results/scan_results.db，storage.py）
新库 = canonical             （results/local.db，core/db.py）
```

```text
旧库 17 张工具表 + tool_results
   ↓  每行 → 一条 observation（category 经 CATEGORY_TO_TYPE 翻译成资产类型）
新库 assets（去重）+ observations（时间线）
```

| 关键设计 | 为什么这么做 |
|---|---|
| `open_legacy()` 用 `mode=ro` URI 打开 | 旧库**一个字节都不改**；缺文件时先自己判一次，给出「旧库不存在」而不是 SQLite 的 `unable to open database file` |
| 观测 ID = `obs_mig_` + 旧库行身份的 sha1 | **确定性 ID 是幂等的前提**：重跑时 `observation_exists()` 命中就跳过，中断后重来不会产生重复时间线 |
| `plan` 按 `observed_at` **全局升序**排序 | `first_seen` 只在首次写入时定下、之后永不覆盖。跨表顺序取决于 `table_map` 的遍历顺序，不排序就会把「两张表里先遍历到的那张」的时间当成首次发现 |
| `...Z` → `+00:00` | 旧库写 `datetime.utcnow().isoformat() + "Z"`，新库写 `+00:00`。两种写法混在一列里，**字符串排序会把 `Z` 排到 `+` 之后**，时间线顺序直接错乱 |
| 单条脏数据只记 `reason` 不抛异常 | 旧库里混着脏数据是常态（`bad space.com`、未知 category），一条坏行不该让整次迁移失败 |
| `scope_id` 留 `NULL` | 旧库年代没有 Scope 概念，**不能事后编一个**；编了就等于伪造授权范围 |
| 表不存在就跳过 | 旧库是渐进长出来的（本机副本实际是 17 张表 + `tool_results`），缺表是正常形态 |

CLI 默认 **dry-run**，`--apply` 才真正写；源库与目标库是同一个文件时直接拒绝（退出码 2）。

```text
python scripts/migrate_legacy_results.py              # 只读，看会迁什么
python scripts/migrate_legacy_results.py --apply       # 真正写入
```

测试：`tests/unit/test_migrate_legacy.py`（20 例）。核心是三条**证伪式**断言：
① dry-run 前后旧库 sha256 不变；② `--apply` 前后旧库 sha256 不变；③ 连跑两次，
`count_assets()` 与观测条数都不变。再加「同一资产来自两张表 → 一行资产 + 两条观测，
`first_seen` 取更早那条」。

> ⚠️ **本机旧库是空的**（实测 17 张工具表 + `tool_results` 全部 0 行），
> 所以 dry-run 输出「扫描到 0 行」。这不是脚本坏了 —— DECISIONS-F 明确
> **不执行真实迁移**，脚本与测试用临时库自验；旧库真有数据时，由用户手动加 `--apply`。

#### 9.12.7 P1 之后仍未做的（对照执行方案）

| 方案项 | 现状 |
|---|---|
| §8 两层模型 | ✅ 表 + 数据层 + 执行链接线 + API + 页面 |
| §9 资产规范化 | ✅ 七种类型 + 全部规则写成测试 |
| §10 Diff | ✅ 四类输出 + 属性变化 + 两个端点 + **前端对比表单** |
| §11 统一旧执行链 | ⬜ 未做：`api/scan.py` 同步扫描与 Job 链仍并存（**改的是调用链，属架构级改动，需授权**） |
| §12 旧库数据迁进新库 | 🔄 脚本 + 测试 + dry-run 已完成（DECISIONS-F 允许的部分）；**真实迁移等用户手动 `--apply`** |
| §13 SQLAlchemy + Alembic | ⬜ 未做（新依赖 + ORM 层重写，本机联调版不引入） |
| §15 测试矩阵 | 🔄 API / 安全 / Worker / Runner 四组已有；平台双跑见 §16 |
| §16 Windows + Linux CI | ⬜ 未做（当前 CI 只有 `ubuntu-latest`） |
| §17 mypy | ✅ **0 errors**（M7 已完成，见 §9.13） |
| §18 真实本地 E2E | ⬜ 未做（硬约束：不打真实外部目标） |
| `assets.status` 自动转 `stale` | ⚠️ 函数已就绪（`mark_stale_assets`），**但还没有任何计划任务/接口调它** |
| Diff 条目可点进资产详情 | ✅ 已接（`data-asset-id` + `diff-item-clickable` → `openDetail()`），两侧各有断言 |

### 9.13 M7：mypy 清零（方案第 17 节）

方案第 17 节的要求是「**不能为了绿 CI 而在配置里排除所有问题**」。因此这一轮
**没有动 `pyproject.toml` 的 `[tool.mypy]`**（没加 `ignore_errors`、没缩 `exclude`、
没放宽 `no_implicit_optional`），改的是代码本身：34 → 0。

修复顺序按方案给的优先级：`modules/base.py` → `jobs/executor.py` / `core/jobs.py`
→ `api/*` → 最后 `agent/`。

| 报错点 | 真实问题（不只是类型） | 处理 |
|---|---|---|
| `config.py:TARGET_CONFIG`、`modules/shuffledns.py:_WILDCARD_CACHE` | 空字面量推不出元素类型 | 补 `dict` 标注，不改值 |
| `modules/base.py:BaseRunner.run` 里的 `self.run_scan` | **基类没有 `run_scan`** —— 子类忘记实现时抛的是 `AttributeError`，与「跑通但零结果」不可区分 | 基类显式声明 `run_scan` 并抛 `NotImplementedError`，由 `run()` 统一翻译成带 `error_code` 的失败结果 |
| `modules/base.py:os.getpgid/killpg/signal.SIGKILL` | Windows 存根里没有这三个名字（跨平台代码的常态） | 改 `getattr(os, ...)` 取函数，`SIGKILL` 取不到时退回 `SIGTERM`；顺带去掉了原来那层过宽的 `except AttributeError` |
| `modules/httpx.py`、`modules/dnsx.py` 的 `_write_input_file` | 子类**收窄了基类签名**（丢了 `suffix`）。runner 注册表按基类类型持有子类实例，收窄让「按基类方式调用」不成立 | 两个子类都补回 `suffix` 参数（httpx 的 `candidates` 语义不变），并加测试锁定 |
| `modules/httpx.py:run_scan` 返回值 | `[r.get("url") for r in raw if r.get("url")]` 的类型是 `List[Any \| None]`，与声明的 `List[str]` 不符 —— `None` 真的可能混进结果 | 显式收成 `List[str]`，非字符串/空串一律丢弃 |
| `core/jobs.py:create_job`、`jobs/executor.py` 的两处 `return get_job(...)` | 写路径刚写完就回读，`None` 属于不可能状态，却被类型逼着把 `\| None` 传染出去 | 新增 `jobs.get_job_or_raise()`：读不到直接抛 `ValueError`；写路径改用它。`get_job()` 仍可空（读接口语义不变） |
| `api/scan.py:resolve_scoped_targets` 返回 `tuple[list[str], "object", str]` | 标注写成字符串 `"object"`，于是 `scope.id` 两处报「object 没有 id」 | 标注改为真实的 `core.scope.Scope` |
| `agent/action.py` 的 20 条 | 见下 | 逐条处理 |

`agent/action.py` 的两类：

1. **`self.context` 没有注解**：字面量里六个键的值全是 `None`，类型被推成
   `dict[str, None]`，于是后面每一处写字符串（`mode`/`target`/`org` …）都变成
   「给 None 赋值」，`analyze_intent(context_state=...)` 也收到错类型。
   补 `Dict[str, Any]` 后**同一处根因消掉 9 条**。
2. **`self.pending_plan` 的可空性**：`_handle_pending_plan` 里反复
   `deepcopy(self.pending_plan)`，类型上它是 `dict | None`。把待处理计划先取到
   局部变量 `pending_plan` 并判空（顺带修掉一处真实的 `deepcopy(None)` 隐患），
   消掉 5 条。

另外两处顺带修的真实缺陷（不是纯类型问题）：

* `_tool_httpx` 的 `items` 错取了 `run_scan` 的返回值。`run_scan` 返回的是 URL
  **字符串**列表（旧签名兼容），元数据在 `runner.last_items`。于是
  「存活探测 → 整理回复」这条路径会以 `AttributeError: 'str' object has no
  attribute 'get'` 收场，而且**零结果时不炸**，本地很容易漏掉。已改为取
  `runner.last_items[:20]`，并加回归用例（同时覆盖 `_summarize_httpx_items`）。
  见第 6 节第 25 条。
* `available_tools` 补 `Dict[str, Dict[str, Any]]` 标注，让
  `tool["handler"](args)` 不再是「object 不可调用」。

验证（本轮实测）：

```text
$ python -m mypy app.py core api jobs storage.py modules   # Success: no issues found in 59 source files
$ python -m ruff check .                                   # All checks passed!
$ python -m pytest -q                                      # 705 passed, 2 skipped, 0 failures
```

> **为什么 `agent/` 不在 mypy 命令里却是 0**：`app.py` 会 `from agent import ...`，
> mypy 顺着 import 把 `agent/action.py`、`agent/intent.py` 一起查了。
> 真正落在范围外的是 `agent/providers/*`（无调用方，见 §7.6）：
> 显式加上 `agent` 参数会多出 7 条 openai 存根相关的报错
> （`Cannot assign to a type`、`Module has no attribute "api_base"` 等）。
> 本轮**没有**为了让这 7 条变绿去改 provider 层的组织方式 —— 那属于「改运行语义」，
> 且这一层当前不可达。`agent/providers/tests/test_*.py` 下的本地校验脚本同理。

### 9.14 M5：字典路径可移植 + 缺失即显式失败（第 6 节第 22 条、第 7 节第 7/9 条）

> 这一轮只动了「配置里的字典路径怎么解析、缺失时怎么报」，**没有改技术栈、
> 没有改表结构、没有下载或分发任何字典**。

#### 9.14.1 问题

`config.py` 里两个 `wordlist` 指向的文件在本机都**不存在**：

| 配置 | 原值 | 问题 |
|---|---|---|
| `FEROXBUSTER_CONFIG` | `D:/c4/v2/backend/framework-main/SecLists/raft-small-directories.txt` | 开发机绝对路径，换机器必失败（`PROJECT_STATE.md` Known Failure #5） |
| `SHUFFLEDNS_CONFIG` | `SecLists/subdomains-top1million-5000.txt` | 仓库**不分发** `SecLists/`（README 已声明），文件本身缺失 |

而且两条失败路径的**表现形态**都不对：

* `shuffledns._bruteforce_with_dnsx` 只打一行 `[!] 字典文件不存在` 就 `return []`
  —— 与「跑通但零结果」完全不可区分（`AGENTS.md` 高频坑 #1）；
* `feroxbuster` / `dirsearch` 把不存在的路径原样当 `-w` 塞进子进程；
* 相对路径还依赖**当前工作目录**，worker / CLI / pytest 的 cwd 不同就会指向不同文件。

#### 9.14.2 改法（三层，都只做加法）

1. **配置层**：两个 `wordlist` 改为仓库相对路径，并支持环境变量覆盖
   （`SHUFFLEDNS_WORDLIST` / `FEROXBUSTER_WORDLIST`，写法与 `HTTPX_PATH`、
   `GEF_PROCESS_TIMEOUT` 一致）。`DIRSEARCH_CONFIG.wordlist` 保持 `None`
   —— 那是「不加 `-w`、用工具自带字典」的合法形态。
2. **基类层**：`BaseRunner` 新增两个方法，是**唯一的路径解释口径**：
   * `_resolve_path(path)`：相对路径按 `config._BASE_DIR`（项目根）解析，绝对路径原样返回；
   * `require_path(path, *, label, flag=None)`：文件不存在即抛 `RunnerInputError`；
   * `require_wordlist()`：读 `self.config["wordlist"]`；空 → `None`（不加 `-w`），
     配了却不存在 → 抛错。返回的是**解析后的绝对路径**。
3. **调用层**：`shuffledns.run_scan`、`feroxbuster.run_scan`、`dirsearch.run_scan`
   在**构造命令行之前**调用 `require_wordlist()`；两个目录扫描 runner 的
   `build_command` 用 `self._resolve_path(wordlist)` 输出绝对路径。

#### 9.14.3 新增错误码 `config_error`

「配置指向的外部资源缺失」既不是 `tool_not_found`（工具没装）也不是
`invalid_target`（目标非法），因此新增 `ErrorCode.CONFIG_ERROR = "config_error"`。
它是**加法**：方案第 6.2 节的 10 个工具错误码一个都没改，`ErrorCode.ALL`
同步收录，前端 `web/static/app.js:ERROR_CODE_LABELS` 也补了中文标签
（有测试同时钉住常量表与前端标签表，避免两边漂移）。

`RunnerInputError` → `result_from_exception` 这条既有通道原样复用
（先例是 `modules/httpx.py` 的「没有候选目标」，`no_results` + `status="success"`）；
区别在于字典缺失是**使用者的配置问题**，所以 `status` 保持默认的 `failed`。

#### 9.14.4 回归测试（`tests/unit/test_runners_m4_rollout.py`，新增 6 例 / 9 个用例）

| 用例 | 钉住的行为 |
|---|---|
| `test_wordlist_configs_are_portable` | 两个默认字典都不是开发机绝对路径；相对路径必须落在项目根之内 |
| `test_every_configured_wordlist_path_resolves_under_project_root` | 扫**全部** `*_CONFIG` 常量，任何 `wordlist` 解析后都在项目根内 |
| `test_missing_wordlist_refuses_before_spawning_subprocess`（feroxbuster / dirsearch） | 文件缺失 → `config_error`，且 `_execute` **一次都没被调用**；经 `run()` 是结构化失败 |
| `test_missing_wordlist_error_does_not_leak_into_unknown_error` | 错误码原样透出，不被兜底成 `unknown_error` |
| `test_shuffledns_missing_wordlist_is_a_failure_not_an_empty_result` | shuffledns 不再 print + 空列表；dnsx **不被调用** |
| `test_absent_wordlist_is_not_an_error` | `wordlist=None` 不算配置错误（dirsearch 默认形态） |
| `test_error_code_config_error_is_declared_and_labelled` | `ErrorCode.ALL` 与 `app.js` 标签表都含 `config_error` |

同时改了两处**原本会掩盖缺陷**的断言：`test_dirsearch_build_command` 原先断言
`cmd[...] == "words.txt"`（相对路径原样透传），现在断言解析后的绝对路径。

验证（本轮实测）：

```text
$ python -m pytest -q -p no:warnings   # 715 passed, 2 skipped, 0 failures, 0 errors
$ python -m ruff check .               # All checks passed!
$ python -m mypy app.py core api jobs storage.py modules  # Success: no issues found in 59 source files
$ node --check web/static/app.js       # 通过
$ git diff --check                     # 退出码 0
```


