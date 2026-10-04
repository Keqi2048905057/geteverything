# get_everything_framework 代码地图

> 本文档基于对项目源码的**逐文件实际阅读**产出（读取范围：项目根 + `api/` + `modules/` + `agent/` + `tests/` 下全部 `.py`，以及 `README.md`、`requirement.txt`、`pyproject.toml`、`.github/workflows/ci.yml`、旧结果库 `results/scan_results.db` 的真实表结构）。
> 文档中出现的行号/函数名均来自实际文件内容；凡代码与 README 不一致处，一律以代码为准并显式标注。
> 项目根：`<仓库根>/get_everything_framework`（Git 仓库根是其上一级目录；文档不再写死本机绝对路径）。
> 凡提到「设计文档/方案」的地方，指的是开发机上的本机联调过程材料 —— 那两份文档
> **不随仓库分发**，此处仅保留历史引用以说明当时的依据来源。
>
> **last-mapped：本机联调版 @ M4 + P0 加固 + P1（资产/观测/Diff/迁移，含前端对比）+ M7（mypy 清零、Diff 可点、SQLite 并发、本地 fixture HTTP 全链路 E2E、**测试报告**）+ M5 字典可移植性 + P0-7 幂等键/重试退避 + §16 Windows CI + P1 §19 Observability（结构化日志/关联 ID）+ §14 文档三件套与导出格式 400 收口 + Diff 属性别名归一 + P0-6 阶段一（Application Service 入口收拢）+ M6 环境自检脚本 + M7 测试报告 `docs/TEST_REPORT.md` + 测试运行期目录隔离修复 + P0-6 阶段二前置件 `docs/AGENT_ASYNC_IMPACT.md` + 公网授权测试模式体验版（见 §9.22）+ 下一阶段体验优化 Phase 1～3：UI 清理 / 公网授权入口（只读试算）/ Scan Profile = 工具组合 + 节奏（见 §9.23）+ Phase 4：结果体验 —— `GET /api/jobs/<id>/results` + `core/findings.py`（见 §9.24）+ 下一阶段规划方案 Phase 1（四步流程，`548d196`）+ Phase 2：Tool Registry —— 工具分组/说明、`/api/tools` 加 `groups`、`load_tools` 参数标准化（见 §9.25）+ **Phase 3：公网授权测试完善 —— 操作者 / 授权备注 / 扫描策略 / 限速 / 超时，五项全部走 `job.created` 事件 detail（**零 DDL**，见 §9.26）** + **规划方案第 6 节：目标自动匹配授权资产（`applyMatchedScope`，隐藏 Scope 而不删 Scope，见 §9.27）** + **方案第 13 节后端安全边界缺口回填 + 注册表两个读出点的分组视图收口（实现零改动，见 §9.28）** + **执行期双开关复检（`jobs/executor.py` 现在同时查 Scope 与环境开关，见 §9.29）+ Phase 1 四处审计缺口收口（提交当前输入 / 无协议 URL 归一 / `scope_id` 不再进文案 / 死代码清理）** + **第二轮只读审计的四处守卫/口径缺口收口（`tools`/`tool` 的 `or` 折叠、工具名守卫从注册表派生、策略说明副本、`rate_limit` 生效面如实写明，见 §9.30）** + **第 6 节 BUG 索引表 29 条行号全量复核与刷新（22 条漂移、3 条说法已不成立，见第 6 节开头的「行号批量刷新」说明）**（2026-10-03）**
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
| GET | `/api/tools` | `api/tools.py:list_tools()` | — | `{"tools":[{name,tool_name,category,description,tool_group,tool_group_label,risk_level,risk_label,internet_allowed,default_enabled,reason,database}],"groups":[...]}` | 无 |
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

旁路（与上主链解耦，且**不经过任何模型**；**2026-10-04 起与主链同级需登录**）：
  app.py:257 action == "chat" → agent/service.handle_agent_message()
    → agent/action.AgentAction.run()
      → agent/intent.analyze_intent()   （正则关键词）
      → agent/planner.build_plan()      （模板拼装）
      → AgentAction._execute_tool()     → 复用 tool_runner.run_tools / storage / exporter

▶ P0-6 阶段一：**创建扫描任务**这条编排已收到一处（`core/application.py`），
  上面主链的 `POST /api/jobs` 与首页表单共用它；Agent 的执行路径**仍在旁路上**
  （阶段二才迁移，见 §9.19）。
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
| `tool_runner.py` | 编排核心：加载目标 → 校验工具 → 双层循环执行 → 落库 | `load_targets()`、`normalize_tool_names()`、`load_tools()`、`save_runner_results()`、`run_tools()`、`run_single_tool()` | `app.py:110`、`api/scan.py:105,159`、`agent/action.py:379,396` | `config`、`modules`、`storage` |
| `target_parser.py` | 目标文件解析与归一化（去协议/去尾点/域名与 IP 校验） | `normalize_target()`、`parse_targets_file()`、`_parse_txt/_parse_csv/_parse_json/_parse_xlsx()`、`save_normalized_targets()`、`DOMAIN_PATTERN`、`IP_PATTERN` | `api/upload.py:56,60` | `csv`/`json`/`re`/`urlparse`、`openpyxl`（延迟导入） |
| `exporter.py` | 结果聚合与落盘导出 | `ensure_export_dir()`、`gather_export_rows()`、`export_results()` | `api/results.py:138,241`、`agent/action.py:492,493` | `csv`/`json`、`config`、`storage` |
| `agent_cli.py` | Agent 终端 REPL 入口 | `main()` | `python agent_cli.py` | `agent.AgentAction` |

> 模块级导入副作用提醒：`import storage` 只 import `config`（不建库）；`ScanResultStore()` 才 `_init_db`。但 `import agent`（`agent/__init__.py`）会连带 import `action.py` → `modules.httpx.HttpxRunner` → `storage`，而 `import app`（`app.py:19`）又 import agent，因此**只要启动 Flask 就已经把 LLM 相关模块全部加载**（尽管它们不被使用）。


### 3.2 `api/`（全部无鉴权、无 Scope、无队列）

| 文件 | 职责 | 关键函数 | 被谁调用 | 依赖谁 |
|---|---|---|---|---|
| `api/__init__.py` | 创建并导出 `api_bp`，末尾 import 5 个子模块以完成路由注册 | `api_bp` | `app.py:31` | flask |
| `api/tools.py` | `GET /api/tools`（**Tool Registry 读出点**：注册表字段 + `groups`）、`GET /api/databases` | `_registry_fields()`（读 `core.tool_registry`，未登记则保守降级）、`_build_tool_payload()`（内部 `build_runner` 实例化 17 个 Runner 取观测类别）、`list_tools()`、`list_databases()` | 前端/curl | `modules`、`storage`、`core.tool_registry` |
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
| `tests/__init__.py` | 让 `tests` 成为**常规包** | — | **不可删**：site-packages 里存在一个常规包 `tests`，会把本仓库的命名空间包 `tests` 顶掉，`import tests.fixtures...` 直接 ModuleNotFound |
| `tests/conftest.py` | 把项目根塞进 `sys.path` | 模块级代码 | 让 pytest 能 `import app`；另提供 `admin_client` 等 fixture，并把数据库/上传/产物/导出目录压到 `tmp_path` |
| `tests/unit/test_smoke.py` | 冒烟：核心模块可导入、`/api/` 路由存在、上传上限 2MB、`OUTPUT_DIR` 名为 results、registry 恰好 17 个 | `test_core_modules_importable` 等 5 个 | **不 mock 外部工具、不联网** |
| `tests/unit/test_repo_layout.py` | 仓库布局与 `.gitignore` 规则存在性 | `test_project_files_exist`、`test_repo_files_exist`、`test_ci_workflow_exists`、`test_gitignore_covers_runtime_artifacts` | 断言仓库根有 LICENSE/SECURITY.md/CONTRIBUTING.md/CHANGELOG.md |
| `tests/integration/__init__.py` | 占位文档，**无任何集成用例** | — | 注明 M1 起再加 |
| `tests/fixtures/__init__.py` | 占位文档 | — | 声明 fixtures 包 |
| `tests/fixtures/local_http_server.py` | **M7 本地全链路 E2E 的目标** | `LocalHttpServer`（`port` / `base_url` / `url()` / `set_status()` / `start()` / `stop()`） | 只用标准库 `ThreadingHTTPServer`，**硬绑定 `127.0.0.1`**、端口 `0` 由系统分配；固定路由 `/`(200) `/stable`(200) `/extra`(200) `/forbidden`(403) `/missing`(404) `/redirect`(302→`/`)，未知路径 404；`Server` 头固定 `GefFixture/1.0`。**不引入任何真实外网目标** |

---

## 4. 三条关键调用链

### 4.a 用户提交一次扫描任务（从 HTTP 到落库）

以 `POST /api/run {"domain":"example.com","tools":["subfinder","dnsx"]}` 为例：

| # | 位置 | 本步数据形态 |
|---|---|---|
| 1 | `api/scan.py:execute_scan` 接收请求 | JSON → `dict`（`request.get_json(silent=True) or {}`，解析失败得 `{}`） |
| 2 | `api/scan.py:_normalize_domain(payload["domain"])` | `"Example.COM "` → `str "example.com"` |
| 3 | `tools = payload["tools"]`（**`in` 判断，`[]`/`""` 不再被折叠成 `None`**）；`tool_runner.load_tools` 负责逗号拆分与去重 | `list[str]`；空 → 400（**不回落到 `SCAN_CONFIG`**，见 §9.25.3） |
| 4 | 校验 `domain or file_path` 至少一个 → 否则 400 | `dict` 错误响应 |
| 5 | `tool_runner.load_tools(tools)` | `list[str]` → `normalize_tool_names()` 去重保序 → 与 `get_supported_runners()` 求差集；非法则 `ValueError` → `api/scan.py` 转 400 |
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
  → app.py:257 action=="chat"                         （form 字段 agent_message）
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

## 6. BUG 定位索引表（共 30 条症状；第 23～27 条为 P1/M7 新增，第 28～29 条为 P0-6 轮新增，第 30 条为 2026-10-04 认证缺口轮新增）

> **2026-10-03 行号批量刷新**：本表是 M0 基线时写的，此前的 `文件:行号` 大面积漂移 ——
> 29 条里 22 条含**已漂移**行号，其中 9 处甚至落进了**别的函数体内**（例如第 4 条把
> `get_tool_results` 的 `category` 失效分支指到了 `get_view_overview`），照错行号去读
> 会读到完全不相干的代码。本轮逐条打开文件核对后按**当前 LF 行号**改写，改不动的
> 用 `~~删除线~~ + ▶` 如实标注「此说法已不成立」。3 条「已不存在」的语句/常量
> （第 7 条 `nfl.com`、第 16 条 `with self._get_connection()`、第 21 条 `record_count`）
> 已改写为现状。
>
> **行号口径**：一律 **LF 行号**（`open(..., newline="")` 或 `Get-Content -Encoding UTF8`）。
> 本仓库工作树是 CRLF、提交里是 LF，两种读法行数一致；但用**默认编码**的
> `Get-Content`（无 `-Encoding UTF8`）会因为多字节字符得到**偏小的假行号**
> （实测 `api/scan.py`：默认编码 267 行、`-Encoding UTF8` 320 行，**偏差 16.6%**），
> 复核时请避免。

| # | 典型症状 | 最可能的 3 个排查位置 | 该处典型失败模式 |
|---|---|---|---|
| 1 | 扫描任务「一直卡在 running」/ 请求长时间不返回 | ① `tool_runner.py:run_tools`（**`:164`**，双层同步 for 在 **`:200`（工具）/ `:206-231`（目标）**；旧写的「第 130–154 行」已是 `load_tools` 内部）② `modules/base.py:_execute`（**`:549`**）的超时取自 `self._timeout_seconds()`（**`:412-433`**，`:425` 读 `process_timeout`）③ `config.py:build_tool_config`（`:154`）的 `"process_timeout": 300`（`:159`） | 根本没有异步任务系统；整个扫描在 Flask 请求线程里同步跑完。单目标最长可挂 300s×目标数，前端只能等到超时。没有 job_id 可查进度，也没有取消接口。**注意这条只剩 CLI 与 `tool_runner` 直调成立**：Web 侧 M3 起走 `POST /api/jobs` 的异步任务（有进度、有租约、有取消），见 §9.9 与 §9.4 |
| 2 | 任务「立刻失败」，没有任何扫描日志 | ① `tool_runner.py:load_targets`（**`:17`**，`open(file_path)` 在 **`:37`**；旧写的 `:29-32` 是收集段）② `tool_runner.py:load_tools`（**`:114`**，现在抛 **`ValueError`**；`raise SystemExit(1)` 已全部移进 `run_tools` 的 **`:181` / `:187` / `:191`**）③ `api/scan.py:execute_scan`（`:113`，未捕获 `SystemExit`/`FileNotFoundError`） | `file_path` 不存在 → `FileNotFoundError` → 500；`SystemExit` 是 `BaseException`，Flask 不兜，页面/客户端看到 500 或连接被断；`api/scan.py` 现在**拒收 `file_path`**（M2），这条只剩 CLI 可达；`/api/tool/<name>/run` 里 `load_tools([tool_name])` 抛 `ValueError` 被转 400，但 `run_single_tool` 后续的 `runner.run_scan` 无保护 |
| 3 | 工具「明明能跑通」却返回空结果 / 库里 0 行 | ① `modules/base.py:_read_results`（**`:451`**，文件不存在即返回 `[]` 在 **`:463-464`**；旧写的 `:62-78` 是 `category` 属性与 `__init__`）② 各 Runner 的 `-o <output_file>` 写入路径 ③ `modules/base.py:_build_output_file`（**`:435`**，md5 前缀命名在 `:448`；旧写的 `:59` 是 docstring） | 工具把结果打到 stdout 而配置用了 `_execute`（或反之），文件根本没生成；`results/` 不可写；域名的 md5 文件名与预期不一致导致读错文件 |
| 4 | 结果页看不到数据（明明 scan_runs 有记录） | ① `api/results.py:query_results`（`:103`）→ `exporter.gather_export_rows`（`:57-113`）② `storage.py:get_view_results`（`:605`）③ `storage.py:get_tool_results`（`:699`） | `gather_export_rows` 在 `category is None` 时只调 `get_view_results`（subdomain 8 张表）+ `get_tool_results` 兜底；`url/web/port` 类数据在 `category` 未指定时会被 `limit` 截断或重复。`get_tool_results` 的 `category` 形参**在专属表分支被完全忽略**（**`:716-735`**，不是旧写的 `:671-704` —— 那段已是 `get_view_overview` 的 SELECT），所以按 category 过滤静默失效。▶ **2026-10-03 已把这一条写进 docstring**（`storage.py:702-711` 的 `Args.category` 明说「形参保留但当前不生效」），读代码的人不必再靠这条索引表推断 |
| 5 | 上传目标文件解析出错 / 400「未识别到有效目标」 | ① `target_parser.py:normalize_target`（`:31` DOMAIN_PATTERN/IP_PATTERN 双重 `fullmatch`）② `target_parser.py:_parse_xlsx`（`:120` `load_workbook`）③ `api/upload.py:upload_file`（`:46` 扩展名白名单） | 带路径的 URL（`https://a.com/x`）只取 hostname 后仍需匹配域名正则；`*.xlsx` 未装 openpyxl 时 `_parse_xlsx` 抛 `ImportError`，`api/upload.py` **不捕获** → 500；`.xls`（老格式）不在白名单 → 400；中文/全角字符、`_`开头的域会被正则拒掉 |
| 6 | 新增一个扫描工具后「没生效」 | ① `modules/registry.py:RUNNER_REGISTRY`（`:19-37`，**仍准确**）② `config.py:build_tool_config`（`:154`）+ 对应 `*_CONFIG` ③ `storage.py:TOOL_DATABASES`（**`:21-107`**，不是旧写的 `:15-101` —— `:15` 是 `BUSY_TIMEOUT_MS`） | 三处都要登记：漏 registry → `load_tools` 报"存在不支持的工具"；漏 TOOL_DATABASES → 结果落到通用 `tool_results` 且 `get_dedicated_results` 抛 `ValueError`；漏 `*_CONFIG` → `KeyError: 'path'` |
| 7 | 扫描范围/目标校验被绕过（传入任意 file_path 或空目标却扫了别的域名） | ① `tool_runner.py:load_targets`（**`:17`**；直接 `open(file_path)` 在 `:37`，空目标回落 `TARGET_CONFIG` 在 **`:41-47`** —— 旧写的 `:29-41` 跨了收集段）② `config.py:TARGET_CONFIG`（**`:125-130`**，默认 `domains` 已为**空列表 `[]`**；`nfl.com` 全仓已不存在）③ `api/scan.py:execute_scan`（**`:113`**，校验在 `:146` 起；已**拒收 `file_path`**） | **没有 Scope 概念** ~~（grep 全仓无 scope 表/校验器）~~ ▶ **此前提对 Web 已不成立**：`core/scope.py` 与 `scopes` 表（`core/db.py:98`）都在，`/api/public-jobs` 走 `core/policy.py:110 validate_job_targets()`。本条剩下的成立部分只在 `tool_runner` 这一层：`file_path` 可为任意绝对路径、目标全被过滤掉时回落。~~`tools: []` 也会因为 `payload.get("tools") or payload.get("tool")` 变成 `None`，进而 `load_tools` 回落到 `SCAN_CONFIG["enabled_runners"]=["amass"]` 去扫~~ ▶ **规划方案 Phase 2 已修**：`api/scan.py` 改用 `"tools" in payload` 判断（`or` 恰会把 `[]`/`""` 折叠成 `None`），`load_tools` 把 `None`（未指定 → 回落）与 `[]`（明确不要 → 空列表）严格分开，Web 侧一律传 `[]`，**回落路径在 HTTP 上不可达**；空选择现在是 400。见 §9.25.3 与 `tests/unit/test_tool_parameters.py`。**注意 `file_path`/`load_targets` 那两半不属本轮**：`/api/run` 早已拒收 `file_path`（M2），但 `tool_runner.load_targets` 自身的回落仍在（只剩 CLI 可达） |
| 8 | 设置项保存后「不生效」 | ① `api/settings.py:save_settings`（`:244`）→ `_write_env_file`（**`:137`**，不是旧写的 `:99`）② `config.py:Config` 类属性（`:13-83`，**import 期求值**；旧写的 `:13-39` 只到 `LLM_MODEL_ID`）③ `api/settings.py:KEY_MAPPING`（**`:65-87`**，不是旧写的 `:58-80`；`enscan_*_cookie → FOFA_EMAIL/FOFA_KEY/HUNTER_API_KEY` 死分支在 **`:84-86`**） | `.env` 写成功了，但 `Config.LLM_API_KEY` 等是类属性，进程内已固化，必须重启（响应里的 message 也这么说）；`load_dotenv` 默认**不覆盖**已存在的环境变量；`KEY_MAPPING` 里 `enscan_*_cookie` 映射到 `FOFA_EMAIL/FOFA_KEY/HUNTER_API_KEY` 是**永远不会走到的死分支**（enscan 键在 `save_settings` 里走 yaml 分支），极易误导后来者 |
| 9 | 导出文件缺字段 / 行重复 / 混入别的工具数据 / `?format=xlsx` 报 500 | ① `exporter.py:gather_export_rows`（`:57-113`）② `storage.py:_get_tool_results_fallback`（**`:739-780`** 逐表遍历**全部 17 张表**，不是旧写的 `:706-747` —— 那段是 `get_tool_results` 自身）③ `exporter.py:export_results`（`:116`，动态 fieldnames `:147`）④ `api/results.py:export_data` 的 fmt 白名单校验（`:206`，白名单 `:254`） | 同一条子域名会先由 `get_view_results` 加入、又被 `_get_tool_results_fallback` 从同一张专属表再加一次 → 重复行；`category` 过滤在专属表分支失效 → 混入其他分类；~~`fmt` 不是 csv/json 时抛 `ValueError`，`api/results.py:export_data` 不捕获 → 500~~ **本轮已修**：调用 `exporter` 前用同一份 `SUPPORTED_FORMATS` 拦下，非法值现在是 400 `bad_request`（原 500 `unknown_error`）。注意 `agent/intent.guess_export_format` 仍会产出 `"xlsx"`，那条链现在拿到的是 400 而不是 500 |
| 10 | 前端页面 500 / `TemplateNotFound: index.html` | ① `app.py`（`:36`）`template_folder="web/templates"` ② `app.py:308` `render_template("index.html", **context)` ③ `app.py:index()`（**`:184`**）的 `request.values.get("domain")`（**`:207`**） | ▶ **整条已过期，只在旧 clone 上成立**：仓库里 `web/templates/{index,login,assets,scan_center}.html` 与 `web/static/{app,assets,scan_center}.js` 都在（M1 已补齐），`GET /` 返回 200。旧写的「`app.py:108` 会在渲染前同步跑 subfinder」也已被替换成**异步建任务**（`create_scan_job`，`:234-244`），全文件已无 `SystemExit`；`debug=True` 的自动重载仍只在 `python app.py` 直跑时存在。**排「首页 500」请改看 §9.9 与第 29 条**（两个入口的行为漂移） |
| 11 | Agent 规划报错 / 答非所问 | ① `agent/intent.py:analyze_intent`（函数 **`:106-274`**；旧写的 `:118-273` 起点偏了 12 行）② `agent/planner.py:build_plan`（**`:95-215`**，不是旧写的 `:79-199`）③ `agent/action.py:run`（**`:116-190`**，不是旧写的 `:109-182`） | 分支顺序敏感：`确认/执行/开始/继续` 的关键词判断（`:121`，**仍准确**）优先于一切，含"继续"的正常句子会被吞成 `confirm_plan`；`build_plan` 对 `confirm_plan`/`cancel_plan`/`analyze_existing_subdomains` 都返回 `None`，走到 **`:168-171`**（旧写的 `:161` 已是另一分支）就回"我没有识别到明确任务"；`subdomain_scan` 恒用 `scan_tool="subfinder"`，用户说 amass 也不改（除 `plan_state.apply_user_intervention` 的"改用 amass"字面量） |
| 12 | provider 超时 / 认证失败 | ① `agent/providers/openai_compat.py:_convert_error`（`:133`，按 401/403/429/5xx/404/400 + 文本关键字分类）② `agent/providers/openai_compat.py:_chat_with_retry`（`:52`，退避 `min(2**attempt, 8)`）③ `agent/config.py:validate_llm_config`（`:114`） | **当前 Web/CLI 流程根本不会走到这里**（`LLMClient` 无调用方）。若自行调用：`validate_llm_config` 在 `LLMClient.__init__` 里抛 `LLMConfigError`；`openai` SDK 的 `APITimeoutError` 没有 `status_code`，只能靠 `"timeout" in message.lower()` 命中，`sanitize` 只对 api_key 做替换 |
| 13 | 子进程路径找不到（Windows `.exe` / `scripts/` 下的工具） | ① `config.py:HTTPX_CONFIG`（**`:218-228`**，`"http-x"` 在 **`:219`**；旧写的 `:171` 是空行）② `modules/base.py:_resolve_command`（**`:469-497`**，不是旧写的 `:98-107`）③ `config.py:GO_BIN_WINDOWS/GO_BIN_POSIX`（**`:122` / `:123`**，不是旧写的 `:91-92`） | **`HTTPX_CONFIG` 的默认 path 是 `"http-x"`**（不是 `httpx`）—— 这是**有意**的：本机 PATH 里 `http-x.CMD` 是 `httpx.exe` 的包装脚本（`config.py:219` 的注释与 `docs/DEPLOYMENT.md:413` 都写明，装脚本会建这个别名），所以「疑似笔误」这个判断**不成立**，别去改成 `httpx`。真正会 `FileNotFoundError` → 静默 `[]` 的是**没建别名又没设 `HTTPX_PATH`** 的机器。另注意：`scripts/` 下**现在只有 6 个脚本文件、一个 `.exe` 都没有**（`git ls-files` 可验），旧写的「`scripts/dirsearch.exe`/`oneforall.exe`/`OneForAll.exe` 明明存在」已过期；`GO_BIN_*` 常量**定义了从未使用**（不注入 PATH）依然成立。只有 `.bat/.cmd` 会被 ComSpec 包裹，`.exe` 依赖 `shutil.which` |
| 14 | 子进程超时 / 命令挂死，进程不退出 | ① `modules/shuffledns.py:_bruteforce_with_dnsx`（**`:87`**；**硬编码 `timeout=300` 已不存在**，现走 `_timeout_seconds()`，泛解析探测给 30 秒，`:82`/`:176`）② `modules/base.py:_execute`（**`:549`**；`capture_output` 现在只剩 `_kill_process_tree` 的 `taskkill` 调用 **`:798`**）③ `modules/enscan.py:run_scan`（**`:91`**，子进程调用 **`:115-117`**，已改用 `_run_subprocess(..., cwd=self.output_dir)`，不再自己 `subprocess.run(capture_output=True)`） | `capture_output=True` 在输出量大时可能因管道写满而卡住；**超时已统一走 `process_timeout`**（M4 起 `_timeout_seconds()`），旧写的「shuffledns 写死 300/120/30、绕开统一入口」已过期；`enscan` 仍保留自己的 `process_timeout` 分支。仍需注意 `enscan` 的前后 glob 差集认产物（见第 15 条） |
| 15 | 并发任务互相干扰 / 结果串台 | ① `modules/shuffledns.py:_WILDCARD_CACHE`（**`:52`** 类级可变 dict，不是旧写的 `:46`；使用点 `:159`/`:160`/`:187`）② `agent/action.py:RATE_LIMIT_CACHE`（**`:26`** 类级 dict，`_enforce_rate_limit` 在 **`:872`**；旧写的 `:23`/`:814` 都已漂移）③ `modules/enscan.py:run_scan`（**`:91`**，运行前后 glob `results/**/*.json` 取差集在 **`:113` / `:178`**） | 类属性被所有实例/线程共享：泛解析缓存跨任务污染且无上限；限流是**进程级全局**，一个用户把 `httpx:domain` 锁 8 秒会拒绝另一个会话。enscan 用"新增 json 文件"判定结果，并发或被别的工具写入 json 时会取到**别人的产物** |
| 16 | 数据库被锁 `database is locked` | ① `storage.py:_get_connection`（**`:129-137`**；**已加 `PRAGMA busy_timeout`（`:136`）**，WAL 仍未启；旧写的 `:123-125` 是 docstring 尾部）② `storage.py` 全部写方法用的是 **`with self._connect() as conn:`**（`_connect` 在 **`:140-153`**，11 处调用 `:163/:303/:366/:430/:451/:540/:578/:665/:729/:752`）—— 旧写法 `with self._get_connection() as conn:`（只提交不关闭）**全仓已不存在**，只剩 `_connect` docstring 里的历史注释 ③ 每次 `ScanResultStore()` 都跑一遍 `_init_db`（`:155`）的 17 次 DDL | ~~无 WAL、无 busy_timeout、无 `foreign_keys=ON`~~ ▶ **busy_timeout 已补（P0）**，仍无 WAL、仍无 `foreign_keys=ON`（外键形同虚设）；每个 API 调用都新建 `ScanResultStore()` 并执行建表语句，写锁竞争窗口被放大。**连接泄漏已修**（`_connect` 的 `finally: conn.close()`），旧写的「连接对象只提交不关闭，长期运行会累积文件句柄」已不成立 |
| 17 | 工具产物文件堆积 / 临时文件泄漏 | ① `modules/base.py:_write_input_file`（**`:739`**，`delete=False` 在 **`:759`**；旧写的 `:201` 是 docstring）② `modules/shuffledns.py` 的 `words_file`（`:112-127`）与 `NamedTemporaryFile`（`:132`/`:168`）③ `api/upload.py:upload_file`（**`:24`**）—— `file.save(raw_path)` 已迁到 **`core/uploads.py:100`**（`save_upload` 在 `:73`），扩展名白名单在 **`core/uploads.py:36`**；旧写的 `api/upload.py:53,58` 中 `:58` **已越界**（该文件现在只有 54 行） | 基类的临时文件"调用方负责删除"，除 `dnsx/httpx/alterx` 外无人删；`upload` 在解析失败（返回 400）时**不清理已保存的 raw 文件**，`uploads/` 会不断积累；`enscan` 把原始 JSON 复制成 `results/<hash>_enscan.txt` 也从不清理 |
| 18 | httpx 探测「跑通了」但拿不到状态码/标题/技术栈 | ① `modules/httpx.py:run_scan`（**`:311`**，只 `return [r.get("url") ...]` 在 **`:352-357`**；旧写的 `:213-214` 是 `-rl` 限速参数拼接）② `modules/httpx.py:_read_json_results`（**`:129-177`**，不是旧写的 `:111-155`）③ `agent/action.py:_save_httpx_metadata`（**`:966-969`** 存 `json.dumps(item)`，落到 `httpx_results.endpoint` 列；旧写的 `:908-911` 已是别的回复文案） | 走 `tool_runner` 的路径**丢失所有元数据**（README/设计文档声称有指纹，实际只剩 URL）；只有经 Agent 的 `_tool_httpx` 才把 JSON 字符串塞进结果列——即同一个工具的两条调用链写出的数据形态不同 |
| 19 | httpx 一执行就把整批任务打挂 | ① `modules/httpx.py`（无候选时抛的是 **`RunnerInputError`**，**`:328-334`**；旧写的 `:177` 现在只是 `return items`）② `modules/httpx.py`（`_execute` 判定 `:344` → **`raise RuntimeError` 在 `:346`**；旧写的 `:211` 已是注释）③ `tool_runner.py`（调用点现在是 `runner.run(target)`，**`:207`**；旧写的 `:137` 已是 `normalize_tool_names` 的调用行） | 与其它 Runner "失败返回 `[]`" 的约定不一致；在 `/api/run` 批量路径上会直接冒泡成 500，**后续目标/工具全部不再执行**；只有在 Agent 路径被 `agent/action.py:_execute_tool`（**`:391`**，宽泛捕获在 **`:395-398`**）兜住。**注意**：`RunnerInputError` 继承自 `RunnerError`，与 `RuntimeError` 不是同一个类，按 `except RuntimeError` 捕不到它 |
| 20 | Agent 对话「失忆」/ 多轮后上下文丢失 | ① `app.py:278,285`（`session["agent_history"]=...[-40:]`、`session["agent_steps"]=...[-50:]`；旧写的 `:139,146` 与 `:265,272` 都已是别的行）② `app.py:39` 的密钥来源 —— **现在已改为 `resolve_secret_key()`**（`config.py:20` 的 `SECRET_KEY` 默认是**空串**，弱值会告警并生成进程级一次性密钥），不是旧的 `app.secret_key = Config.SECRET_KEY` 默认 `"dev-secret-key"` ③ `agent/action.py:_trim_history`（**`:843`** 上限 30 条；「30」是类属性 `max_history_messages`，**`:48`**；旧写的 `:785` 已是帮助文案） | Flask session 是**签名 Cookie**（客户端存储）；`agent_history`/`agent_steps` 里含完整工具结果文本，很容易超过浏览器 4KB Cookie 上限 → Flask 静默丢弃 Cookie → 下一轮 `session.get("agent_history")` 变空。~~默认密钥还可被伪造~~ ▶ **M1/P1 已修**：默认密钥清空 + 弱值检测，伪造前提不再成立 |
| 21 | `/api/tools`、`/api/databases` 返回的记录数不对/很慢 | ① `api/tools.py:list_tools`（**`:105-172`**，每个工具都 `build_runner` 实例化在 **`:161`** 附近；旧写的 `:80-88` 已落在 `_build_tool_payload` 的 docstring 里）② `api/tools.py:_build_tool_payload`（**`:76-101`**；旧写的 `:35` 是模块 docstring）③ `storage.py:get_tool_databases`（**`:555-569`**，**不返回任何 count**；旧写的 `:527-541` 已落在 `_query_subdomain_tables` 的 SELECT 段） | **本条正文已按现状改写**（旧写「接口 docstring 宣称返回 `record_count`」：`record_count` 在全仓**已无匹配**，那次 docstring 脱节在 `98ea46f` 已修）。现状：实现只返回 `tool_name/table/result_column/category`；真正的计数方法 `get_tool_database_overview`（**`storage.py:571-604`**）**在 API 层从未被调用**（只有 `tests/unit/test_storage_connection.py` 直接调它）。另外 `build_runner` 会执行 `DnsxRunner/HttpxRunner/AlterxRunner/ShufflednsRunner` 的 `__init__`（各建一个 `ScanResultStore()`，触发建表） |
| 22 | 子域爆破类工具（shuffledns/alterx/dnsx）总是零结果 | ① `config.py:SHUFFLEDNS_CONFIG` / `FEROXBUSTER_CONFIG` 的 `wordlist` ② `modules/shuffledns.py:_bruteforce_with_dnsx`（字典不存在曾只 print 一句就 `return []`）③ `modules/base.py:require_wordlist`（M5 起统一校验） | **M5 已修**：仓库不分发 `SecLists/`，所以两个默认字典**在本机并不存在**——原先 shuffledns 静默返回空、feroxbuster 把不存在的路径当 `-w` 传进子进程。现在：字典路径按**项目根**解析（与 cwd 无关）+ 环境变量可覆盖；**配置了字典却不存在 → `error_code=config_error` 的显式失败，且不启动子进程**。剩下「真零结果」的正常原因：`alterx`/`dnsx`/`httpx` 的候选来自 `store.get_results_by_domain()`，subfinder 没先跑过就永远是空 |
| 23 | 任务跑完了，资产页却「一条都没有」/ 少了几条 | ① `jobs/executor.py:execute_job` 里的 `ingest_step_observations` 调用 ② `core/assets.py:CATEGORY_TO_TYPE`（`web`/`alive`/`dns` 的映射）③ 任务详情里的 `step.assets_ingested` 事件（含 `skipped` 与 `reasons`） | **先看事件，不要先看代码**：`step.assets_ingested` 的 `written`/`skipped`/`reasons` 直接说明这批观测落了几条、为什么跳过。三种常见原因：① 工具的 `Observation.category` 不在 `CATEGORY_TO_TYPE` 里（返回 `None` → 整条跳过，不猜类型）；② 步骤只有字符串结果且工具是 `httpx`/`naabu`/`nmap`（形态不确定 → 故意不落，见 §9.12.3）；③ `canonical.normalize()` 判定值非法（如把本地路径当 URL）。**注意落观测是派生产物**：它失败不会让任务变 failed，所以「任务 succeeded 但没资产」是合法状态，必须靠事件区分 |
| 24 | 对比两次任务时「未变」总是 0，看起来像两次扫描毫无交集 | ① `core/assets.py:diff_jobs` 里 `counts["unchanged"]` 与 `unchanged` 明细的关系 ② 页面「含未变」复选框（`#diff-include-unchanged`）③ `web/static/assets.js:renderDiff` 对空明细的措辞 | `include_unchanged=False`（勾掉「含未变」）**只应影响明细、不应影响计数**。曾经两者一起清零，于是「扫到了但没变化」与「什么都没扫到」变得不可区分。先看 `counts.unchanged`：**它非 0 而明细为空，说明是这次没要明细，不是两次没有交集**（前端会显示「按设置未取明细，共 N 条」）。真正的 0 才是「两次任务的资产集合完全不相交」 |
| 25 | Agent 回复里出现 `AttributeError: 'str' object has no attribute 'get'`（只在**真有存活结果**时） | ① `agent/action.py:_tool_httpx` 的 `items` 字段 ② `agent/action.py:_summarize_httpx_items`（逐条 `item.get(...)`）③ `modules/httpx.py:run_scan` 的返回值语义 | 同一个 `rows` 变量在两条链上有两种形态：`run_scan` 返回 **URL 字符串列表**（旧签名，兼容用），而元数据在 `runner.last_items`（dict 列表）。`items` 错取了 `rows`，于是 `_summarize_httpx_items` 收到一堆 `str` 就炸。**零结果时不炸** —— 所以「本地跑不通、真机上必炸」是它的典型表现。`results` 里的 `total` 也就会与 `items` 长度对不上 |
| 26 | 原始证据打开后「只有一行」/ 明明跑出很多结果却只看到一条；`truncated` 还是 `false` | ① `core/artifacts.py:read_artifact`（脱敏用的是哪支函数）② `core/runner_result.py:scrub_text`（脱敏且默认不截断）vs `scrub_command`（**命令预览**，末尾截到 300 字符）③ API 的 `limit` 与 `SCAN_LIMITS["max_artifact_bytes"]` | **M7 已修**：`read_artifact()` 曾误用 `scrub_command()`，于是 `GET /api/artifacts/<id>` 的 `text` 永远只有头 300 字符，而 `truncated` 仍为 `False`（截断标记只看 `max_artifact_bytes`）。**症状的判别点**：`truncated is False` 但文本长度恰好 ≈300 且以 `...` 结尾。修法是 `scrub_text()`（只脱敏、默认不截断）；命令预览仍走 `scrub_command()`（语义与 300 字符上限未变）|
| 27 | 日志里「找不到一个请求相关的任何记录」/ 结构化字段时有时无 | ① `core/observability.py:log_event`（关联字段来自 contextvar，不是参数）② 绑定处是否成对（`app.py` 的 `before_request`、`jobs/worker.py:startup`、`jobs/executor.py` 的 `with observability.bind(...)`）③ `configure_logging()` 是否在**进程入口**调过（`app.py:__main__` / `jobs/worker.py:__main__`）| 三个高发点：① 直接 `python -c "import app"` 或 `waitress-serve app:app` 起服务**不会**调 `configure_logging()`，事件只进 root logger（没人看）；② `Worker` 只 `startup()` 没 `shutdown()` → `worker_id` 一直挂着，同线程后续代码/测试会继承一个已死 worker 的身份；③ 用行号/字符串去 `grep "print("` 会撞上 `Blueprint("api", …)` 这类同形标识符，实际 print 清单以 `tests/unit/test_observability.py` 的 AST 守卫为准。**另注意**：401 的 `error_message` 里 `X-Local-Token` 后面的词会被脱敏规则打码（见 §9.18.3），不是日志丢了内容 |
| 28 | 对比两次任务时，Web Server / 技术栈明明变了却**报不出 `changed`**（`status_code` / `title` / URL 却能报） | ① `core/assets.py:DIFFABLE_ATTRIBUTES`（白名单用的是哪个键名）② `core/assets.py:ATTRIBUTE_ALIASES` 与 `_canonical_attributes()` ③ `modules/httpx.py:_read_json_results` **实际产出的键名** | **已修**，但复发方式很隐蔽：httpx 产出的是 `webserver` / `tech`，而白名单早期写的是 `server` / `technology` —— 两边对不上，白名单永远匹配不到，`_changed_attributes()` 返回 `{}`。**判别点：只有 `status_code` / `title` / `url` 三项会报变化**。最危险的是单测若用「文档体例」的键名（`server`/`technology`）而不是工具真实键名，测试会全绿而线上失效。以后新增可 diff 属性，先确认工具真实产出的键名 |
| 29 | 「创建任务」的两个入口行为不一致（错误码/文案/限流口径对不上） | ① `core/application.py:create_scan_job()`（唯一编排入口）② `api/jobs.py:create_job` 是否又被写回了内联编排 ③ `app.py:index()` 的扫描分支是否又反向导入 `api.jobs` 的私有函数 | 这类退化**不会让任何功能测试变红**（两条路各自都"能用"），只会让两个入口慢慢漂移。`tests/unit/test_application_service.py` 的三条源码守卫专拦这个：`api/jobs.py` 里不许再出现 `validate_job_targets` / `create_job_with_status` / `normalize_idempotency_key` / `resolve_mode` / `audit.record(job_created)`；`app.py` 里不许再出现 `from api.jobs import _resolve_targets`；`core/application.py` 里不许出现 `allowed_domains` / `allowed_cidrs` / `fnmatch`。**守卫失败时该改的是那段新写的内联代码，不是守卫** |
| 30 | 「某个页面动作不用登录也能跑」/ 「匿名也能看到授权资产清单」 | ① `app.py:index()` 的 `_require_admin_for_page()`（`:174` 定义）是否还在 **`action` 分支之前**（`:227`）—— 若又被塞回 `if action in _SCAN_ACTIONS:` 里面，`action=chat` 就会重新裸奔 ② `app.py:304` 的 `context["scopes"]` 是否又变成无条件 `_load_scope_options()`（正确写法带 `if is_authenticated else []`，与 `:382` 的资产页同口径）③ 新增页面动作时**有没有顺手加守卫** | 症状是「功能全对、就是不用登录」。这类洞**不会让功能测试变红**，因为功能本身是好的。三道守卫：`tests/integration/test_api_auth_contract.py::test_page_chat_action_requires_login`（匿名 chat 必须 401）、`::test_anonymous_homepage_does_not_leak_authorized_assets`（匿名首页不得出现范围名/目标/资产卡片类名/`id="scope_id"`）、`ADMIN_ONLY` 参数化清单（逐个方法绑定实测响应码，**权威口径**）。修法与实测证据见 §9.33。**注意守卫失败时该改的是 `app.py`，不是守卫** |

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
   ▶ **本轮已解决**：改为在调用 `exporter` **之前**用 `exporter.SUPPORTED_FORMATS` 拦下，返回 400 `bad_request`
   + `details.field="format"` / `details.supported=["csv","json"]`。回归用例 6 条（`test_export_contract.py`
   的参数化非法值 + 大小写不敏感 + 缺省仍是 csv + 「校验清单与兜底同一份」）。**注意**这只是把
   「用户传错参数」从 500 改回 400，`agent/intent.guess_export_format` 仍会产出 `"xlsx"`（见 §6 第 9 条）。
6. **`api/upload.py` 未捕获 `target_parser` 的 `ImportError`**（`target_parser.py:117`），上传 `.xlsx` 且未装 openpyxl 时 500，而不是 400 + 明确提示。

### 7.2 路径与外部依赖

7. **硬编码绝对路径**：`config.py` 的 `FEROXBUSTER_CONFIG.wordlist` 曾写死作者本机路径 `D:/c4/v2/backend/framework-main/SecLists/raft-small-directories.txt`，换机器必失败。▶ **M5 已解决**：改为仓库相对路径 `SecLists/raft-small-directories.txt`，并支持 `FEROXBUSTER_WORDLIST` 环境变量覆盖（见 §9.10 末「字典配置」）。
8. **`HTTPX_CONFIG` 的命令名不是 `httpx`**：`config.py`（`:218-228`，默认值在 `:219`）写的是 `os.getenv("HTTPX_PATH", "http-x")`，**这是有意的别名、不是笔误** —— 本机 `/health` 与 `python scripts/check_env.py` 都报 `17/17` 可用，靠的是 PATH 里那个包装脚本（`:219` 的注释、`docs/DEPLOYMENT.md:413` 都写明安装脚本会建 `http-x` 别名）。**别去把它改成 `httpx`**。真正会静默 `[]` 的是「没建别名又没设 `HTTPX_PATH`」的机器 —— 见第 6 节第 13 条。
9. **配置引用的字典文件不存在**：`config.py` 的 `SecLists/subdomains-top1million-5000.txt` 与 `SecLists/raft-small-directories.txt` **两个都指向仓库里并不存在的文件**（仓库不分发 `SecLists/`，见 README）。▶ **M5 已解决（行为部分）**：路径改为**按项目根解析**的相对路径 + 环境变量覆盖；文件确实缺失时不再静默返回空结果，而是 `error_code=config_error` 的显式失败。字典本身仍**不随仓库分发**，需自行下载或用环境变量指向本机字典。
10. **`GO_BIN_WINDOWS`/`GO_BIN_POSIX` 是死常量**（`config.py:122/123`；旧写的 `:91-92` 已漂移），从未用于注入 PATH。**另需注意**：旧写的「`scripts/*.exe` 也不会被自动发现」现在**没有实际所指** —— `scripts/` 下已经一个 `.exe` 都没有（`git ls-files get_everything_framework/scripts` 只列 6 个文本脚本：`check_env.py`/`install_linux.sh`/`install_windows.ps1`/`migrate_legacy_results.py`/`run_local.ps1`/`verify_public_scan.py`）。它作为「不要依赖仓库自带二进制」的提醒仍成立。
11. **`modules/shuffledns.py` 硬编码 `"dnsx"` 命令名**（`:89,109,147`）而不读 `self.config["path"]`，无法通过配置切换二进制；并且它**根本没有调用 `shuffledns` 二进制**，类名/工具名与实际行为不符。
12. **`modules/enscan.py` 依赖 `cwd=results/` + 前后 glob 差集**（`:53,58,77`）识别产物：并发运行或其它工具往 `results/` 写 `.json` 时会认错文件；文件已存在但被覆盖时 `new_files` 为空 → 静默 `[]`。

### 7.3 SQL 与并发

13. **f-string 拼 SQL 表名/列名**：`storage.py:_create_tool_table`（**`:216-235`**）、`_query_subdomain_tables`（**`:412`**）、`get_dedicated_results`（**`:526-528`**）、`get_tool_results`（**`:721-723`**）、`_get_tool_results_fallback`（**`:762-764`**）。（旧写的 `:189`/`:384,401`/`:498,508`/`:688`/`:729` 已漂移）当前插值来源都是模块级常量 `TOOL_DATABASES`，**不构成注入**，但一旦有人把用户输入接到表名就会立刻变成注入点。值全部走 `?` 占位参数，这点是对的（唯一例外是 `_query_subdomain_tables:412` 把**工具名**以 `'{tool}'` 直接插进 SQL，来源是常量字典，同样安全、但更要小心别改成用户输入）。
14. ~~**无 WAL / 无 `busy_timeout` / 无 `PRAGMA foreign_keys=ON`**（`storage.py:_get_connection`，`:123`）。~~ ▶ **2026-10-03 修正**：`busy_timeout` **已补**（`storage.py:136`，`PRAGMA busy_timeout=5000`，见 §9.9 与 P0 加固那一轮），所以「并发写入直接 `database is locked`」对**短暂争用**已不成立。**仍然成立的两半**：无 WAL、无 `PRAGMA foreign_keys=ON` —— `FOREIGN KEY(run_id) REFERENCES scan_runs(id)` 依旧形同虚设。
15. ~~**连接泄漏**：所有写方法用 `with self._get_connection() as conn:`。~~ ▶ **2026-10-03 修正：这条已修，不再成立**。现在全部走 **`with self._connect() as conn:`**（`storage.py:140-153`，`finally: conn.close()`，11 处调用）；`with self._get_connection() as conn:` 这个写法**全仓已不存在**，只剩 `_connect` docstring 里作为历史背景的一句注释。**注意别顺手「修」**：`_get_connection()` 返回裸连接（不带关闭语义）是有意的，`_connect()` 才是唯一入口。
16. **`category` 参数被静默忽略**：`storage.py:get_tool_results`（**`:699`**，签名里的 `category`；专属表分支 **`:716-735`** 完全没用它）与 `_get_tool_results_fallback`（**`:739`**）都不按 category 过滤。所以 `/api/results?category=web` 对已注册工具不生效。（旧写的 `:671-704`/`:706` 已漂移）▶ **2026-10-03 已把这一点写进 `storage.py:702-711` 的 docstring**，属文档级改动、行为未变。
17. **`gather_export_rows` 双重收集**（`exporter.py`，**`:57-113`**；旧写的 `:46-77` 已漂移）：subdomain 行先由 `get_view_results` 取一遍，`get_tool_results` 的 fallback 又把 17 张表逐表查一遍，导致重复行与 limit 语义混乱。
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

31. **敏感产物已进 Git**（M0 基线时 `git ls-files` 实测 83 个跟踪文件中包含）：`results/scan_results.db`、`results/*.txt`、`results/outs/*.json`（真实企业名与域名）、`uploads/*.txt`（真实目标清单）、`scripts/dirsearch.exe`、`scripts/OneForAll.exe`、`scripts/oneforall.exe`、`SecLists/raft-small-directories.txt`。仓库根的 `.gitignore` 虽有 `**/results/`、`**/uploads/`、`*.db`，但对**已跟踪文件无效**。
    ▶ **2026-10-03 复核：本仓库（`geteverything`，独立干净历史）里这一条已不成立** —— `git ls-files` 对 `results/`、`uploads/`、`SecLists/` 都是 **0 个文件**，`scripts/` 下只有 6 个文本脚本、无 `.exe`；182 个跟踪文件里没有任何 db / 密钥 / 样本（见 `docs/DECISIONS.md` §3.12 的七项推送前审计）。**但本条要保留**：它描述的是**上游旧 clone / 旧仓库**的真实状态，而「不要 `git add -A`」这条纪律在两种情况都适用。
32. **首页无模板**：`app.py:160` 渲染 `index.html`，仓库无 `web/` 目录（当时的实施方案也把它列为 P0）。▶ **M1 已解决**：`web/templates/` 与 `web/static/` 已补齐。
33. **测试覆盖极薄**：`tests/` 下只有 2 个单元测试文件（导入、布局、常量），`tests/integration/` 与 `tests/fixtures/` 均为占位 `__init__.py`；**没有任何针对 Runner、storage、intent/planner 的测试**，也没有 mock runner。
   ▶ **早已解决**：现在 `tests/unit/` + `tests/integration/` 共 **1149 项通过 / 2 skipped**（Phase 4 时点；含公网体验版新增 105 项与后续各轮增量），`tests/fixtures/` 有了真实 fixture（`local_http_server.py`，见 §9.17）。
34. **`app.py` 只有单个应用实例**（模块级 `app = Flask(...)`），没有 `create_app()` 工厂；`tests/unit/test_smoke.py:21` 直接 `import app` 并检查 `app.app.url_map`。
35. **`_is_storage_question` 关键词过宽**：`agent/action.py:25-38` 的 `DATABASE_QUERY_KEYWORDS` 含 `"数据库"`、`"保存位置"`、`"db"` 等；`:138` 的条件只在"非 analyze 意图且无扫描词"时短路，边界用例（如"把结果保存位置告诉我然后扫一下"）容易被误判成纯问答而**静默不执行扫描**。
36. **`modules/registry.py` 的 import 期全量加载**：任何单个 adapter 的语法/依赖错误都会让 `import modules` 失败，进而 `/api/tools`、`/api/run`、`/api/tools` 全部 500（例如 `modules/enscan.py` 若缺依赖）。没有按需加载或容错注册。
37. **测试会读开发机的 `.env`，导致「本机绿 / 别处红」**：`config.py` 的 `load_dotenv()` 在 `import config` 时就把 `.env` 灌进 `os.environ`，而测试进程没有任何隔离。**已实测两处**：本机 `.env` 写 `GEF_ALLOW_REAL_SCAN=true` 会顶掉 `test_m2_security.py` 对 `real_scan_enabled is False` 的断言；写 `GEF_LOG_FORMAT=text` 会让 `test_observability.py` 的 JSON 解析失败。
   ▶ **已修**（公网体验版轮）：`tests/conftest.py` 对这两个变量用**赋值**而非 `setdefault`（`load_dotenv()` 默认不覆盖已存在的环境变量），需要 real 模式的用例自行 `monkeypatch.setenv` 并在结束时回滚。
   **判断规则**：凡是「`.env` 能覆盖 + 测试有断言」的开关，都必须在 conftest 里钉死；只 `setdefault` 等于把本机配置变成隐式测试参数。详见 §9.22.5。

---

## 8. 最小调试入口速查

> 所有命令的工作目录都是项目根 `get_everything_framework/`。以下仅列**实际读代码得出的**入口，未实测运行（避免发起真实扫描）。

| 目的 | 做法 | 关键观察点 |
|---|---|---|
| 看有哪些工具被注册 | `python -c "from modules import get_supported_runners as g; print(g())"` | 返回 17 个名字；少一个就是 `registry.py` 漏登记 |
| 看某个 Runner 的 category / config | `python -c "from modules import build_runner; r=build_runner('httpx'); print(r.category, r.config)"` | 能直接看出 `path` 是否写错（如 `http-x`） |
| 不跑子进程单独验落库 | `python -c "from storage import ScanResultStore as S; s=S(); print(s.save_dedicated_results('t.com','subfinder','subdomain',['a.t.com']))"` | 返回 `{run_id,scan_count,inserted_count}`；`inserted_count=0` 说明被 `UNIQUE(domain,subdomain)` 去重 |
| 看真实表结构与行数 | sqlite3 打开 `results/scan_results.db`，`select type,name from sqlite_master` | 实测 **20** 张表（`scan_runs` + `tool_results` + 17 专属表 + `sqlite_sequence`）；**没有** `subdomain_results`/`alive_results`。（旧写 19 张：那张表把 `sqlite_sequence` 之外的计数与含它的计数混了，且当时 17 专属表里有键尚未建表；`python scripts/check_env.py` 现在报「旧结果库 20 张表」） |
| 验意图识别（离线，不联网） | `python -c "from agent.intent import analyze_intent; print(analyze_intent('扫一下 a.com 的子域名'))"` | 打印 `UserIntent`；用于定位 §6 第 11 行的分支顺序问题 |
| 验计划生成 | `python -c "from agent.intent import analyze_intent as a; from agent.planner import build_plan as b; i=a('扫一下 a.com 的子域名'); print(b(i,{}))"` | `None` 表示 `build_plan` 没有覆盖该 intent_type |
| 跑测试基线 | `python -m pytest -q`（`pyproject.toml` 已配 `pythonpath=["."]`） | ▶ **已过期**：现在是 **40 个 `test_*.py`**、全量基线上千条（以 `PROJECT_STATE.md`「最近一次验证」为准）。旧写的「只有 2 个测试文件」是 M0 状态 |
| 静态检查 | `ruff check .`（CI 用同一命令，规则集仅 `E4/E7/E9/F`） | 未使用导入、未定义名会被抓。**规则集与忽略项以 `pyproject.toml` 的 `[tool.ruff.lint]` 为准**（`select = ["E4","E7","E9","F"]`、`ignore = ["E402"]`、`tests/**` 免 `F401`） |
| API 自检 | `curl http://127.0.0.1:5000/api/databases` | 返回 **17** 条，键是 `tool_name`/`table`/`result_column`/`category` —— **没有 `record_count`，这是设计如此**（计数方法 `get_tool_database_overview` 没有 API 出口，见第 6 节第 21 条），不是缺陷。需要认证的接口请用 `X-Local-Token`；`/api/databases` 本身匿名可读 |
| 数据库文件位置 | `config.py`（`:114` `SCAN_CONFIG`/`SQLITE_CONFIG` 的 `path`）→ `<项目根>/results/scan_results.db`；新库在 `:119`（`LOCAL_DB_CONFIG`）→ `results/local.db` | 两个库都可被环境变量改向：`GEF_SCAN_DB_PATH`（旧库，只读用）与 `LOCAL_DB_PATH`（新库）；`GET /api/databases` 只给表元信息，不给路径 |

**改代码前的三个前置提醒**：
1. `results/scan_results.db` 已存在且是**真实数据**（含真实资产与 `results/outs/` 里的企业信息）；表结构变更靠 `CREATE TABLE IF NOT EXISTS` 不会自动迁移，需自行 ALTER 或删库重建。
2. ~~`results/`、`uploads/`、`SecLists/`、`scripts/*.exe` 已被 Git 跟踪（见 §7.7 第 31 条），任何 `git add -A` 都会把扫描产物再次提交。~~
   ▶ **2026-10-03 复核：本仓库里这四者都是 0 个跟踪文件**（`git ls-files` 实测），所以现状下 `git add -A` 不会带进扫描产物。**但纪律照旧**：这几个路径在磁盘上真实存在（`results/`、`uploads/` 都在），且 `.gitignore` 只对**未跟踪**文件有效 —— 一旦哪天有人 `git add -f`，ignore 就失效了。提交前仍应 `git status` 确认新增文件清单。
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
│   ├── runner_result.py      M4：Observation / ToolHealth / RunnerResult + scrub_command（命令预览，300 字符）+ scrub_text（任意文本，默认不截断）
│   ├── artifacts.py          M4：原始证据落盘（stdout/stderr/output）+ 登记 + 截断脱敏读取（**脱敏用 scrub_text，不再被 300 字符预览规则截断**）
│   ├── observability.py      P1 §19：**唯一日志出口**——一行一个 JSON 事件 + 四个关联 ID（request_id/job_id/step_id/worker_id，contextvars 绑定）+ 脱敏与容器上限
│   ├── application.py        P0-6 阶段一：**Application Service 层**——create_scan_job() 是创建扫描任务的唯一编排入口（解析目标 → 查重 → 限流 → Policy → 模式 → 落库 → 审计 → 结构化日志），HTTP 视图 / 首页表单 / 以后的 Agent 共用
│   ├── findings.py           Phase 4：任务结果**派生层**（纯函数）——把 assets + observations 整理成「发现资产 / 服务 / 技术栈 / 风险提示」四段；不碰 sqlite / Flask / 网络（见 §9.24）
│   ├── job_limits.py         规划方案 Phase 3：单任务的**限速 / 超时**（`job_limits`）——**只能收紧**（`min` 合并）、越界 400 不静默夹边界、上下界每次读活配置；不是安全闸门（见 §9.26）
│   └── jobs.py               job 数据层：状态机、步骤快照、认领/租约/cancel/retry/恢复 + `*_of_job()` 从 `job.created` 事件读回审计上下文（pace / operator / strategy / authorization / limits）
├── scripts/                  ← 运维脚本（不在包里，靠 sys.path 前插项目根自举）
│   ├── run_local.ps1         一键拉起 Web + worker（退出时收尾）
│   ├── migrate_legacy_results.py  P1 §12：旧库 → 新库迁移，**默认 dry-run**，`--apply` 才写
│   ├── check_env.py          M6：一键环境自检（**只读**，见 §9.20）
│   └── install_windows.ps1 / install_linux.sh  依赖与 Go 工具安装（可能联网）
├── jobs/                     ← 进程层（刻意不放进 core/）
│   ├── executor.py           执行逻辑（与进程无关，可直接单测调用）
│   └── worker.py             独立 worker 进程：python -m jobs.worker
├── api/
│   ├── jobs.py               /api/jobs*（10 个接口，含 Phase 4 的 /results）+ /api/jobs/<id>/artifacts + /api/artifacts/<id> + /api/jobs/<a>/diff/<b>
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
| GET | `/api/jobs/{id}/results` | `api/jobs.py` | **需管理员** | **Phase 4 新增**；一次给出四段结果：`assets` / `services` / `technologies` / `risk_hints` + `counts` + `notes`。派生层是纯函数 `core/findings.py`，**不是**漏洞扫描（见 §9.23.8） |
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
| GET | `/scan-center` | `app.py:scan_center()` | — | **公网体验版新增**；扫描中心页（项目 / 创建任务 / 任务列表）。匿名可打开但只显示提示；数据由 `static/scan_center.js` 调 `/api/scan-center` |
| POST | `/api/projects` | `api/projects.py` | **需管理员** | **公网体验版新增**；创建授权测试项目（201）。`name` / `authorization_note` 必填，`owner` / `scope_ids` 可选 |
| GET | `/api/projects` | `api/projects.py` | **需管理员** | 同上；项目列表（含 `scope_ids` / `scope_count`） |
| GET | `/api/projects/{id}` | `api/projects.py` | **需管理员** | 同上；不存在 → 404 `not_found` |
| POST | `/api/projects/{id}/scopes` | `api/projects.py` | **需管理员** | 同上；把**已存在**的 Scope 关联进项目（201，幂等）。**不创建 Scope** —— 那仍然只有 `POST /api/scopes` |
| POST | `/api/public-jobs` | `api/public_scan.py` | **需管理员** | 同上；**202 + `queued`**。授权公网测试的唯一任务入口，内部转交 `core.application.create_authorized_public_job`。**规划方案 Phase 3 起**接受 `operator` / `authorization_confirmed` / `rate_limit` / `timeout_seconds`，并在响应里回 `operator` / `authorization_confirmed` / `limits`（见 §9.26） |
| GET | `/api/scan-center` | `api/public_scan.py` | **需管理员** | 同上；页面元数据：`projects` / `strategies` / `paces` / `tools` / `tool_groups` / `restricted_tools` / `limits` / `internet_allowed_tools`。**不下发任何目标清单**；`limits` 是限速/超时输入框的唯一事实源（见 §9.26.4） |

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
* **幂等键（P0-7a 新增）**：`create_job_with_status(..., idempotency_key=...)` 在同键的
  **未终结**任务（`queued` / `running`）存在时直接复用那一个，返回 `(job, reused=True)`：
  不重复插入、不重复展开步骤、不重复写 `job_created` 事件。「查重 + 插入」同在
  `BEGIN IMMEDIATE` 事务内，并发重复提交不会各插一条。键由 `normalize_idempotency_key()`
  规范化（空/空白 → 无键；非字符串或 > 200 字符 → `ValueError` → API 400）。
  **键不是「永久只跑一次」**：任务落终态后键自动释放，否则「重试失败任务」会被永久挡住。
* **重试退避（P0-7b 新增）**：`retry_job()` 写 `next_attempt_at = now + retry_backoff_seconds(attempt)`，
  第 1 次不退避，之后 5 / 10 / 20 / 40… 封顶 300 秒；`claim_next_job()` 的领取条件加
  `next_attempt_at IS NULL OR next_attempt_at <= now`，**领走时清空窗口**（窗口只用来推迟领取，
  不是任务的长期属性）。退避中的任务仍是 `queued`（`/health` 的排队计数含它），
  与「任务丢了」可区分 —— 排查「排队中却不执行」先看这里。
* `cancel_requested` 与 `heartbeat` 已有。

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
| `assets` / `observations` / `artifacts` 表 | **均已建成**：`artifacts` 是 M4，`assets` / `observations` 是 M5/P1 §8（两层模型：一行唯一资产 + N 条观测时间线，`core/db.py` 建表 + `core/assets.py` 读写） | — |
| 敏感产物仍在 Git 索引 | **已解决**：自有仓库 `geteverything` 只保留一份干净历史，`results/`、`uploads/`、`SecLists/`、`scripts/*.exe` 均未入库 | — |
| Scope 判定位置 | **P0-2 已统一**到 `core/policy.py`（见 §9.11） | — |
| Agent 的执行边界 | **P0-3 已完成**（禁止任意 `file_path`，只认受控 `upload_id`）；**P0-6 阶段一已完成**：任务创建编排收拢到 `core/application.py`，HTTP 视图与首页表单共用唯一入口。**阶段二未做**：Agent 仍直接调 `run_tools` / runner | P0-6 阶段二（已授权，见 §9.19 / `docs/DECISIONS.md` §3.2），开工前先出影响说明 |
| `jobs` 表幂等与退避 | **已解决（P0-7）**：`idempotency_key` / `next_attempt_at` 两列纯增量补列（`ALTER TABLE ... ADD COLUMN`，可空，既有行语义不变）+ 两个非唯一索引 | 授权见 `docs/DECISIONS.md` §3.1 |

### 9.8 测试与验收基线

```text
$ python -m ruff check .     # All checks passed!
$ python -m pytest           # 1149 passed, 2 skipped, 0 failures
$ python -m mypy app.py core api jobs storage.py modules scripts   # Success: no issues found in 71 source files
$ python -m pytest -m "not slow"   # 跳过起真实子进程的 kill/重启用例
$ python scripts/check_env.py      # 退出码 0/1/2；只读，不建库、不执行任何扫描（见 §9.20）
$ node --check web/static/{app.js,assets.js,scan_center.js}   # 前端无构建链，只做语法检查
```

> 这几个数字会随每轮推进变化，**以 `PROJECT_STATE.md` 的「最近一次验证」为准**
> （本节是 Phase 4 时的快照）。
>
> 演进：M1 `70` → M2 `142` → M3 `236` → M4 `405` → P0 加固 `538` → P1 资产/观测/Diff/迁移 `701` → M7 类型收口 + Diff 可点 `707` → M5 字典可移植性 `715` → P0-7 幂等键/退避 + §16 Windows CI `739` → M7 SQLite 并发测试 `752` → M7 本地 fixture HTTP 全链路 E2E `759` → P1 §19 Observability（结构化日志与关联 ID）`828` → §14 文档三件套 + 导出格式 400 收口 `838` → Diff 属性别名归一 `847` → P0-6 阶段一（Application Service 入口收拢）`874` → M6 环境自检脚本 `900` → M7 测试报告 + 测试运行期目录隔离修复 `901` → 公网授权测试模式体验版 `1004`（见 §9.22）→ 下一阶段体验优化 Phase 1 UI 清理 `1009` → Phase 2 公网授权测试入口 `1036` → Phase 3 Scan Profile `1091`（见 §9.23）→ **Phase 4 结果体验 `1149`（见 §9.24）**。
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
| `tests/unit/test_jobs_store.py` | 状态机、认领、租约、恢复、cancel、retry、**跃迁表 + max attempts**（M3 / P0-7）、**幂等键（复用/释放/规范化/不重复写事件）与退避窗口（写窗口、挡领取、清窗口、封顶）**（P0-7）、**旧库纯增量补列的迁移用例**、**`get_job_or_raise` 与 `get_job` 的可空性差异**（M7） |
| `tests/unit/test_jobs_executor.py` | mock/real 分流、进度、取消边界、**真实子进程 kill/重启**、**执行期 Scope 复检**（M3 / P0-2） |
| `tests/unit/test_runner_result.py` | 命令预览脱敏（9 例，含 Windows/POSIX 长路径不被误打码）、**`scrub_text` 证据脱敏（长文本原样保留 / 只打码密钥 / 可选 limit / 默认上限不漂移）**、`RunnerResult` 组装、artifact 落盘/读取（M4 / M7） |
| `tests/unit/test_runner_interface.py` | **真实子进程**：成功/零结果/未安装/非零/127/超时/SystemExit/残留文件清理（M4）；**基类未实现 `run_scan` 必须报失败**、**子类 `_write_input_file` 保留 `suffix`**（M7） |
| `tests/unit/test_runners_m4.py` | subfinder / httpx / dnsx 的 `build_command` + `parse_output`（M4） |
| `tests/unit/test_runners_m4_rollout.py` | **其余 14 个 runner** 的接口覆盖 + 解析 + 横切自检（M4 铺开）；**M5**：字典配置可移植（不是开发机绝对路径、必落在项目根内）、字典缺失时 `config_error` 且**不启动子进程**、`wordlist=None` 不算错、新错误码常量与前端标签同步 |
| `tests/unit/test_policy.py` | **P0-2**：统一 Policy 四个入口（缺失 400 / 越界 403 / 整体拒绝 / 解析后地址校验，注入 resolver 不查真实 DNS） |
| `tests/unit/test_agent_boundary.py` | **P0-3**：Agent 拒绝任意 `file_path`、只收 `upload_id`、planner 不再下发 `file_path`；**M7**：httpx 步骤的 `items` 必须是元数据字典（回归 `'str' object has no attribute 'get'`） |
| `tests/unit/test_storage_connection.py` | **DECISIONS-I**：旧库连接必关（含异常路径）、`busy_timeout`、表结构未变、`-W error::ResourceWarning` 复现 |
| `tests/unit/test_db_concurrency.py` | **M7**：SQLite 并发（方案第 15 节 Worker「duplicate execution」）——新库连接确为 WAL + `busy_timeout`、WAL 跨连接保持、**8 线程并发建任务/写审计不撞锁**、读写混合不读半截事务、**8 个 worker 抢 24 个任务不重不漏**（`duplicate execution`）、同一任务只有一条 `job.started`、无任务时并发认领都拿到 `None`、**同一幂等键并发只建 1 个任务**、锁被持有时写者是「等」而不是立刻 `database is locked`（含一条反证用例：无 `busy_timeout` 的裸连接必须失败） |
| `tests/unit/test_canonical.py` | **P1 §9**：七种类型的归一化规则、方案验收的三个 URL 折叠成一个 key、`guess_type` 不猜错 |
| `tests/unit/test_assets.py` | **P1 §8/§10**：两层模型（一行资产 + N 条观测）、`first_seen` 不被覆盖、scope 参与唯一性、`%`/`_` 转义、状态迁移不删数据、Diff 验收（A B C → A C D）、取消 unchanged 明细后计数仍准、category→type 映射、落库失败不改任务结果 |
| `tests/unit/test_migrate_legacy.py` | **P1 §12**：dry-run 与 `--apply` 前后旧库 sha256 不变、重跑幂等（确定性观测 ID）、`web`→`url` 翻译、`...Z`→`+00:00` 归一、跨表同资产合并成一行、单条失败不中断、CLI 三个退出码 |
| `tests/integration/test_web_baseline.py` | 首页可渲染、登录/登出（M1） |
| `tests/integration/test_m2_security.py` | 认证、受控上传、file_path 拒绝、审计（M2） |
| `tests/integration/test_m2_scope_enforcement.py` | 无 Scope/越界拒绝、mock 不碰真实 runner（M2） |
| `tests/integration/test_m2_page_scan.py` | 首页 = 异步任务、不阻塞（M2/M3） |
| `tests/integration/test_m3_jobs_api.py` | 7 个 jobs 接口、10 个任务响应时间、状态持久化（M3） |
| `tests/integration/test_m4_runner_result.py` | RunnerResult 端到端：零结果 vs 失败、artifact 不下发路径（M4） |
| `tests/integration/test_export_contract.py` | **P0-5**：导出响应无 `path`、可下载、未知/已清理 id → 404、`safe_prefix` 穿越表、前缀逃不出导出目录；**本轮新增**：`?format=` 非法值（`xlsx`/`pdf`/带空格/`../csv` 等 6 个参数化取值）必须 **400 `bad_request`** 而不是 500、校验清单与 `exporter.SUPPORTED_FORMATS` 同一份、缺省仍是 csv、大小写不敏感 |
| `tests/integration/test_api_auth_contract.py` | **P0-1/D**：锁定「哪些只读接口匿名、哪些必须 401」的当前契约 + 响应体不夹带服务器路径 |
| `tests/integration/test_assets_api.py` | **P1**：资产接口全部 401（未登录）、「任务跑完 → 资产可查」端到端链路、`summary` 未被 `<asset_id>` 吃掉、`/api/observations` 拒绝无条件全表扫描、Diff 端点 404 与 `include_unchanged`、**diff 条目必带可用的 `asset_id`**、资产页骨架与匿名时不下发 Scope 名、**静态脚本已把 diff 条目接成点击**、响应无服务器路径 |
| `tests/integration/test_m7_local_e2e.py` | **M7（方案第 18 节）**：本地 fixture HTTP **全链路**——真实 httpx 子进程打只绑 `127.0.0.1` 的 fixture，一条用例走完 target → job → worker → runner → raw artifact → parser → observation → asset → diff → export；含「证据必须完整读出（只脱敏、不按 300 字符截断）」的回归。无 httpx 可执行文件时 `pytest.skip` |
| `tests/unit/test_observability.py` | **P1 §19**：`request_id` 生成与入站校验（空格/过短/过长一律拒绝并重生成）、contextvar 绑定/还原/**线程隔离**、事件信封（`ts`/`level`/`event` + 自动并入的四个关联字段）、单行 JSON、级别常量与非法值退化、敏感字段名单只记占位符、自由文本脱敏、**关联 ID 不被裸 token 规则误打码**、长字段截断、**容器最多 20 项（不记完整目标列表）**、`format_event` 两种格式、`configure_logging` 幂等/分级/读配置/配置坏掉也不炸；另有三条**源码守卫**：`print` 里不得出现密钥形状、除 `core.observability` 外不得自建 logger、新增 `print` 必须在登记清单里 |
| `tests/integration/test_observability_chain.py` | **P1 §19**：Web 层每个请求绑定并回写 `X-Request-Id`（合法入站值沿用、非法值拒绝、逐请求唯一、失败响应也带）、访问与失败事件的 `path` **只记路径不带 query**、`job_created` 事件与触发它的请求共用 `request_id` 且不记目标列表、401 事件的错误码且不回显 Token 值、执行层 `job_step_finished` 带 `job_id`/`step_id`/`tool`/`status`/`duration_ms`（失败为 WARNING）、多目标时只记当前步骤目标、worker 三层事件都带 `worker_id` 且上下文管理器退出后还原；**端到端按长方案 P1-5 的验收原话写**：拿一个 `job_id` 去日志里捞，六类事件（创建/开始/每步/结束/领取/worker 结束）一次全部出现且共用同一个 `job_id`，另加反向守卫确认 **12 个目标的整份清单不会出现在任何日志字段里**（连换成别的字段名也拦得住） |

### 9.9 第 6 节 BUG 索引表 + 第 7 节薄弱点的**现状修正**

> 本表的行**跨两处编号空间**，看的时候别看串：`#1`～`#9`、`#22`、`#23`（「资产页一条都没有」）
> 指的是**第 6 节**的 BUG 索引表；而 `#23`（「SECRET_KEY 默认固定值」）、`#24`、`#32`、`#33`、
> `#34` 指的是**第 7.7 节**的薄弱点编号 —— 两边都有 23/24，且是完全不同的两件事。

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

> **2026-10-03 补一行（第 6 节第 4 条）**：#4「结果页看不到数据」的**根因之一已不再是缺陷、
> 而是一个从未生效过的形参** —— `storage.py:get_tool_results(category=...)` 的 `category`
> 在两条分支（专属表 / fallback）里**都不被读取**：指定了 `tool_name` 时分类由
> `TOOL_DATABASES` 反查（专属表没有 `category` 列），未指定时直接走 fallback。
> 要做「按分类过滤」只能用 `get_view_results(category=...)`。
> 本轮把这句话直接写进了 `storage.py:702-713` 的 docstring，避免下一个读代码的人
> 再从这条索引表反推。**属文档级改动，未改任何行为**。

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

> ⚠️ **这一行在 §9.29 之后需要打补丁**：`jobs/executor.py` 现在**查两件事** ——
> Scope 成员资格（`validate_step_target`）与环境开关 / `active_scan`
> （`core/safety.py`）。本节描述的是 P0-2 当时的状态（环境开关仍未复检），
> 最新口径见 **§9.29**。

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

**仍未做**（P0-6 阶段二）：Agent 依旧直接调 `tool_runner.run_tools` / `HttpxRunner.run_scan`，
没有改走 Job Service。这是「Agent 提议 = 执行」的残留。**阶段一已于本轮完成**
（编排收拢到 `core/application.py`，见 §9.19）；阶段二开工前须先出影响说明
（Agent 执行会异步化）。

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

**P0-7 补齐的部分**：`idempotency_key`（同键未终结任务复用，`create_job_with_status`）
与 `next_attempt_at`（重试退避窗口，`retry_job` 写入 / `claim_next_job` 作为领取门槛）。
两列都由 `core/db.py` 的 `_COLUMN_MIGRATIONS` 纯增量补列，授权见 `docs/DECISIONS.md` §3.1。
`MAX_ATTEMPTS = 5` + 退避 = 方案第 7 节要求的「retry 必须有上限和退避」两件都齐了。
`cancel_requested` 与 `heartbeat` 原本就有。

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

`changed` 只在**可 diff 属性**变化时产生。属性白名单是 **canonical key**，不是任意键名 ——
两侧写法先过 `ATTRIBUTE_ALIASES` 归一化再比较：

```text
ATTRIBUTE_ALIASES = {
    "server": "webserver", "webserver": "webserver", "web_server": "webserver",
    "technology": "technologies", "technologies": "technologies", "tech": "technologies",
    "status_code": "status_code", "title": "title", "url": "url",
}
DIFFABLE_ATTRIBUTES = ("status_code", "title", "webserver", "technologies", "url")
```

> ⚠️ **这里踩过一个真实缺陷（已修）**：`DIFFABLE_ATTRIBUTES` 原先写的是
> `server` / `technology`，而 `modules/httpx.py:_read_json_results` 实际产出的键名是
> `webserver` / `tech`。两边对不上，`_changed_attributes()` 在真实 httpx 链路上
> **永远返回 `{}`** —— 只有 `status_code` / `title` / `url` 三项真的能报出变化，
> `webserver` 从 `nginx` 变成 `apache` 也报不出来（方案第 10 节点名要求它能报）。
> 更隐蔽的是：当时的单测用的是**文档体例**的键名而不是 httpx 真实键名，所以测试全绿。
> 修法就是上面这张表 + `_canonical_attributes()`；不变量
> 「`set(ATTRIBUTE_ALIASES.values()) == set(DIFFABLE_ATTRIBUTES)` 且每个 canonical key
> 自映射」写成了用例，防止以后只改一边。
> **排查提示**：以后再加可 diff 属性，先确认「工具真实产出的键名」是什么，
> 不要照着文档写。

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

### 9.15 P0-7：任务幂等键与重试退避（方案第 7 节）+ §16 Windows CI

授权：`docs/DECISIONS.md` §3.1（用户在弹窗中逐项勾选），口径与 DECISIONS-E 同规格 ——
**只 `ADD COLUMN`，不动既有列、不删既有数据**。

#### 9.15.1 表结构：两列纯增量 + 两个非唯一索引

| 列 | 语义 | 可空 |
|---|---|---|
| `jobs.idempotency_key` | 调用方提供的幂等键；同一键只允许存在一个**未终结**任务 | ✅（NULL = 没传键） |
| `jobs.next_attempt_at` | 重试退避的「最早可领取时间」 | ✅（NULL = 立即可领） |

两处都要写：`core/db.py` 的 `CREATE TABLE`（新库）**和** `_COLUMN_MIGRATIONS`（旧库
`ALTER TABLE ... ADD COLUMN`），否则「新库有列、旧库没有」这种状态会一直存在。

两个坑（都在 `core/db.py`）：

1. **索引语句必须排在 `_migrate_columns()` 之后**。旧库升级时列是刚 ALTER 出来的，
   顺序反了就是 `no such column: idempotency_key` —— 升级路径必须只有
   「纯增量、不会失败」的 DDL。
2. **刻意不用 `UNIQUE`**。唯一性由 `core/jobs.create_job_with_status()` 在
   `BEGIN IMMEDIATE` 事务里保证（「查重 + 插入」同事务，写者之间本就串行）；
   部分唯一索引收益很小，却会给旧库引入一个「历史脏数据导致建索引失败」的风险面。

#### 9.15.2 幂等语义（`create_job_with_status`）

* 命中条件 = 同键 **且** `status IN (queued, running)`。任务落终态后键**自动释放** ——
  否则「重试一个失败任务」会被幂等键永久挡住，键就从「防重复提交」变成了
  「永久只跑一次」，语义过强。
* 命中时**不插入、不展开步骤、不写 `job_created` 事件**，直接返回那个 job
  （`reused=True`）。步骤快照重复展开会让 `done_steps / total_steps` 的分母算错。
* `normalize_idempotency_key()`：空串 / 纯空白 / `None` → `None`（视为没传键）；
  非字符串或 > `MAX_IDEMPOTENCY_KEY_LENGTH`（200）→ `ValueError` → API 400。
  **不做静默截断**：超长键多半是调用方把整份请求体塞进来了，属于用法错误。
* `create_job()` 保留原签名（`-> dict`），内部委托给 `create_job_with_status()`，
  既有调用方与测试无需改动。

#### 9.15.3 退避语义（`retry_job` + `claim_next_job`）

* `retry_backoff_seconds(attempt)`：第 1 次（首次执行）0 秒，之后
  `min(5 * 2 ** (attempt - 2), 300)` → 5 / 10 / 20 / 40… 封顶 300 秒。
* `retry_job()`：任务**立刻**变成 `queued`（界面能马上看到），同时写
  `next_attempt_at`；退避秒数一并记进 `job_retry_requested` 事件，事后可解释。
* `claim_next_job()`：领取条件加 `next_attempt_at IS NULL OR next_attempt_at <= now`；
  **领走时把窗口清空** —— 窗口只用来「推迟领取」，不是任务的长期属性，
  留着会让事后的任务详情误导成「它还在退避」。
* 退避中的任务仍是 `queued`：`/health` 的排队计数**含**它，但 worker 领不到。
  排查「状态是排队中却迟迟不执行」时，先看 `next_attempt_at`。
* 一个任务在退避不会阻塞后面的任务（领取条件是逐行的，没有队头阻塞）。

#### 9.15.4 API 与前端

| 位置 | 变化 |
|---|---|
| `POST /api/jobs` | 接受可选 `idempotency_key`（非法值 400）；响应新增 `reused`（不传键时为 `false`，形状稳定） |
| `POST /api/jobs/<id>/retry` | 响应新增 `next_attempt_at` |
| 审计 | 幂等命中仍写一条 `job_created`（`detail.reused = true`）—— 否则「少了一个任务」事后无从解释 |
| `web/static/app.js` | 详情面板新增「最早可重试」一行；`jobSignature()` 纳入 `attempt` / `next_attempt_at`，否则 retry 后轮询拿到的变化不会触发重绘 |

#### 9.15.5 §16 CI 的 Windows runner

`lint-and-test` 改为 `matrix.os: [ubuntu-latest, windows-latest]` +
`fail-fast: false`，两个平台都跑 `ruff check .` 与 `pytest -q`；`mypy` 步骤加
`if: matrix.os == 'ubuntu-latest'`（类型检查结果与平台无关，Windows 再跑一遍
只是把 CI 时间翻倍）。**不涉及任何密钥，不改仓库 Settings。**

`fail-fast: false` 不是可选项：本项目的 `modules/base.py` 就踩过
「POSIX 下杀进程树会连调用方一起 SIGKILL」的**平台专有缺陷**
（见 §9.10），一个平台挂掉连带取消另一个，会让「Windows 特有缺陷」被
「Linux 失败」掩盖成一次笼统的 job 失败。

#### 9.15.6 回归测试（新增 24 例，715 → 739）

| 文件 | 钉住的行为 |
|---|---|
| `tests/unit/test_jobs_store.py` | 幂等：复用同一 `job_id`、步骤不重复展开、`running` 期间键仍生效、终态释放键、不同键互不影响、空白键等于无键、非字符串/超长被拒、幂等命中不重复写事件；退避：窗口在将来、窗口内领不到但仍在排队计数、窗口推旧即可领、退避任务不阻塞后续任务、退避函数指数增长并封顶、领走清窗口、新任务无窗口；迁移：手工造「P0-7 之前」的 `jobs` 表，`init_schema` 后两列补齐且既有行原样保留 |
| `tests/integration/test_m3_jobs_api.py` | 同键只产生一个任务并回 `reused=true`、不传键时 `reused=false`、非字符串/超长键 400、幂等命中可在审计里追溯 |
| `tests/unit/test_assets.py` | `test_new_tables_do_not_touch_legacy_schema` 原先断言 `"idempotency_key" not in job_columns`（写于 P0-7 授权之前），改为断言「两列存在且可空 + 既有列 `attempt` 一个都不能少」 |

验证（本轮实测）：

```text
$ python -m pytest -q -p no:warnings   # 739 passed, 2 skipped, 0 failures, 0 errors
$ python -m ruff check .               # All checks passed!
$ python -m mypy app.py core api jobs storage.py modules  # Success: no issues found in 59 source files
$ node --check web/static/app.js       # 通过
$ git diff --check                     # 退出码 0
```

---

### 9.16 M7：SQLite 并发测试（方案第 15 节「Worker → duplicate execution」）

#### 9.16.1 为什么单开一个测试文件

`AGENTS.md` 的高频坑 #5 与 `PROJECT_STATE.md` 的 Known Failure 都记着同一件事：
「SQLite 并发 —— 旧库无 WAL、无 `busy_timeout`，并发写会 `database is locked`」。
到 M7 为止，这条只被**单线程**间接验证过（`test_storage_connection.py` 只断言
`PRAGMA busy_timeout` 的值），而新库 `core/db.py` 的 WAL + `BEGIN IMMEDIATE`
**从来没有在真实并发下跑过一遍**。

风险不对称：这个设计一旦在并发下失效，症状是**用户看不到任何报错** ——
任务被两个 worker 各跑一遍（重复观测量、重复资产来源），或任务凭空消失。
所以它值得一个专门的、只做这一件事的测试文件。

文件：`tests/unit/test_db_concurrency.py`（13 例）。线程数固定为 `THREADS = 8`，
与 `scripts/run_local.ps1` / waitress 的默认线程数对齐 —— 小于它测不出真实争抢，
大于它只会拖慢用例而不增加判别力。

#### 9.16.2 并发用例最容易犯的错：把失败读成成功

`threading` 默认会把线程内的异常打到 stderr 然后**悄悄结束线程**。若直接
`start()` + `join()`，那么「8 个线程里挂了 3 个」看起来仍然是绿的。
本文件的 `_run_threads()` 因此显式做了三件事：

1. 用锁收集线程内异常（连 `SystemExit` 一起接），`join` 之后重抛第一个；
2. 断言没有线程在 60 秒后仍存活（死锁会表现为「测试永远不返回」而不是失败）；
3. 返回值按 slot 归位，避免「少跑了几个线程」被当成通过。

#### 9.16.3 四组断言

| 组 | 断言的行为 |
|---|---|
| 连接参数 | `core.db.connect()` 的 `journal_mode=wal`、`busy_timeout == BUSY_TIMEOUT_MS`；WAL 跨连接保持；旧库 `storage.py` 的连接也带 `busy_timeout` |
| 并发写 | 8 线程各建 4 个任务全部落库且 id 互不重复；并发 `audit.record` 一条不丢；读写混合下读者拿到的每一行都能被 `get_job` 读到（不读半截事务） |
| 并发认领 | 8 个 worker 抢 24 个任务：**不重不漏**（这是 `duplicate execution` 的正解）；同一任务只有一条 `job.started`；没有任务时并发认领都干净地拿到 `None` |
| 幂等键 | 8 线程用**同一把键**并发创建 → 只建 1 个任务、7 次 `reused=True`、`job.created` 只有 1 条；不同键互不顶掉 |

#### 9.16.4 锁等待的正反两面

`test_writer_waits_for_lock_instead_of_failing` 用「Event 置位 + 0.3 秒后断言
写线程仍存活」构造**确定**的锁竞争（不靠 `sleep` 猜时机），再断言写线程最终成功
且耗时 ≥ 0.25 秒 —— 后者排除「它其实根本没撞上锁」这种假通过。

`test_locked_db_without_busy_timeout_would_fail` 是它的**反证**：同一竞争场景下，
`timeout=0` 的裸连接必须抛 `sqlite3.OperationalError`。没有这条反证，上面那条
用例在「SQLite 某天改成默认无限等锁」时会静默退化成永远通过。

> 顺带确认了一处**代码阅读结论**：`core.db.query()` 走的是 `connect()` 出来的
> 普通连接，`PRAGMA journal_mode=WAL` 是**库文件级**设置，因此「只读接口」也不会
> 把库退回 `delete` 模式（`test_wal_mode_survives_reopening` 钉住这点）。
> 另外，本文件**不覆盖** `mark_stale_assets()` / 资产过期那条线（属 M5 剩余项）。


### 9.17 M7：本地 fixture HTTP 全链路 E2E（方案第 18 节）

第 25 节的 P1 验收清单里一直挂着未勾选的 `[ ] 本地全链路 E2E`，第 26 节执行顺序第 15 条
也是「本地真实 E2E」。在此之前 `mode=real` 只有**单元级**的真实子进程用例
（`test_runner_interface.py` 起真进程验证成功/零结果/未安装/非零/127/超时），
从未有**一条链路**把下面这些层接起来跑通：

```text
target → job → worker → runner(真实 httpx) → raw artifact
       → parser → observation → asset → diff → export
```

#### 9.17.1 目标为什么必须是自己起的 fixture

方案第 18 节的原话是「不要为了验证真实链路去扫未授权公网目标」。所以目标不是任何真实站点，
而是 `tests/fixtures/local_http_server.py`：标准库 `ThreadingHTTPServer` + `daemon_threads`，
**硬绑定 `127.0.0.1`**、端口 `0`（由系统分配，避免端口冲突）。路由与响应完全确定：

| 路径 | 状态 | 标题 | 用途 |
|---|---|---|---|
| `/`、`/stable` | 200 | `Local Fixture Home` | 基线资产；`/` 之后被改成 403 用来造 changed |
| `/extra` | 200 | `Local Fixture Extra` | 第二次扫描才出现 → added |
| `/forbidden` | 403 | `Forbidden` | 第一次有、第二次没有 → removed |
| `/missing` | 404 | `Not Found` | 未在用例中使用，但 404 也必须是**确定**的 |
| `/redirect` | 302 → `/` | — | 留给 `-follow-redirects` 类用例 |
| 其他 | 404 | `Not Found` | 兜底 |

`Server` 头固定为 `GefFixture/1.0`，这样断言 `webserver` 字段时不必依赖 httpx 的版本行为。
`set_status(path, code, title=...)` 用来在运行中途改单个路由 —— Diff 用例靠它把 `/` 从
200 变成 403。

为什么不用「加一个路由 / 删一个路由」来造 diff：httpx 的默认 match 集（`-mc`）跨版本会漂移，
而 403/404 在不同版本里可能被过滤掉。用**显式路由 + 显式状态码**才不依赖工具版本。

#### 9.17.2 唯一被替换的一步：候选集

httpx 的候选来自 `ScanResultStore.get_results_by_domain()`，而 `storage.py` 按方案要求
**只有写入、没有删除接口**（禁止删历史数据），所以同一域名的候选集在库里只增不减 ——
方案第 10 节验收形态「A B C → A C D」需要候选集**收缩**一次，这在库层面无法表达。

于是第二次任务显式替换 `HttpxRunner._load_candidates`（`monkeypatch.setattr`），
模拟「上游子域发现这次给出了不同的集合」。**被替换的只是输入发现**；httpx 之后的一切
——命令行构造、子进程执行、JSONL 解析、证据落盘、观测归一、资产归并、diff、导出 —— 全是真实代码。
这是本条 E2E 与 `test_m4_runner_result.py`（整个 runner 都是假的）最本质的区别。

#### 9.17.3 测试隔离：为什么 conftest 不够

`tests/conftest.py` 只覆盖 config 层与 core 层（两个库、`ARTIFACT_DIR`、`EXPORT_DIR`、
`UPLOAD_DIR`、`core_health.OUTPUT_DIR`）。但 `modules/base.py`、`modules/httpx.py`、
`jobs/worker.py` 在**导入时**就把 `OUTPUT_DIR` 绑定成了各自的模块级字符串，
conftest 管不到它们。若不管，真实 httpx 的 JSONL 与 worker 心跳会写进仓库的 `results/`
（AGENTS.md 硬约束明确禁止）。

因此测试内额外 `monkeypatch.setattr` 了 `modules.base.OUTPUT_DIR`、`modules.httpx.OUTPUT_DIR`、
`jobs.worker.OUTPUT_DIR` 三处，并断言结果文件确实落在临时目录的 `127.0.0.1_httpx.jsonl`。

> 同类陷阱还有一个：`HttpxRunner.__init__` 会立刻 `ScanResultStore()`，
> 所以 `storage.SQLITE_CONFIG["path"]` 必须在**构造 runner 之前**就被改掉 —— conftest 的
> `app_module` fixture 已经做了这件事，测试只需 `app_module` 出现在参数表里。

#### 9.17.4 顺带修掉的既有缺陷：证据被命令预览的规则截断

`core/artifacts.py:read_artifact()` 原本用 `scrub_command()` 处理证据，而该函数的语义是
**命令预览** —— 末尾会截到 `MAX_COMMAND_PREVIEW = 300`。后果：

* `GET /api/artifacts/<id>` 的 `text` **永远只有头 300 字符**；
* 同时 `truncated` 仍然是 `False`（截断标记只看 `max_artifact_bytes`），对外说了假话；
* 被截掉的正好是排查「跑通了但没数据」时要看的部分，与方案第 6.2 节
  「结果详情能看到原始证据」直接冲突。

改法是只加不减：把三轮正则替换抽成 `_redact()`，新增

```python
scrub_text(value: str, limit: int | None = None) -> str   # 只脱敏；默认不截断
scrub_command(cmd, limit: int = MAX_COMMAND_PREVIEW) -> str  # 语义不变（签名多了可选 limit）
preview_text(value, limit=2000)  # 改为委托给 scrub_text
```

`read_artifact()` 改用 `scrub_text()`。既有 9 条脱敏用例原样通过（`scrub_command` 的默认行为
与 300 字符上限未变），另加 4 条把 `scrub_text` 的新语义钉住。

> **一个必须知道的副作用**：`_LONG_TOKEN_RE` 的兜底规则（连续 ≥20 个字母数字 → 打码）
> 现在真正作用于整份证据了。所以证据里**长的连续字母数字串会变成 `***`**；
> `http://127.0.0.1:PORT/path` 这类带路径分隔符的值不受影响（该正则有前后视断言排除路径片段）。
> 这是刻意的取舍：宁可过度脱敏，也不能让 API Key 进前端。

#### 9.17.5 一处被刻意钉住的现状：导出读的是旧库

`GET /api/export` 走 `exporter.gather_export_rows()` → **旧** `ScanResultStore`，
不是新的资产模型。所以它导出的是上游候选的**原始字面值**（`127.0.0.1:PORT/path`），
而不是归一化后的 `http://…` URL。测试里把这个现状写成断言，免得以后误以为它导出的是资产
（那属于方案第 11 节「统一执行链」，见 `docs/DECISIONS.md` §3）。

#### 9.17.6 断言清单（+7 例，752 → 759）

| 组 | 断言 |
|---|---|
| fixture 自身 | 只监听回环地址；`core.safety.is_local_only_target("127.0.0.1")` 为真 |
| target → job → worker → runner | 任务 `succeeded`、步骤 `error_code is None`、`found_count == 3`、`command_preview` 非空、`duration_ms` 非空 |
| parser → observation | `parser_version == "1.0"`；3 条观测的 `category`/`status_code`/`title`/`webserver`/`source_tool` 全对 |
| raw artifact | `stdout` 与 `output` 都在；无 `path`；`missing is False`；`truncated is False`；**内容恰好 3 行**（截断缺陷的回归）；文件确实在临时目录 |
| observation → asset | 3 观测 → 3 个 `type=url` 资产；`summary.by_type == {"url": 3}`；详情带观测时间线 |
| diff | `counts == {added:1, removed:1, changed:1, unchanged:1}`；`/` 的 `status_code: 200 → 403` 且 `title` 同步；`asset_id` 指向同一行资产；第二次扫描后资产总数是 **4 而不是 6**（归并生效） |
| export | 登记成功、`download_url` 可下载、CSV 表头正确、响应与列表都不含 `path` |

#### 9.17.7 一个环境依赖，必须知道

`tests/__init__.py` 是**必需文件，不可删**。site-packages 里存在一个常规包 `tests`
（某些依赖自带），它会把本仓库的**命名空间包** `tests` 顶掉，症状是
`ModuleNotFoundError: No module named 'tests.fixtures'`。加上 `tests/__init__.py` 后
`tests` 成为常规包，解析稳定落在仓库内。

用例开头有 `shutil.which(HTTPX_CONFIG["path"])` 守卫：本机靠
`E:\GoWorkspace\bin\http-x.CMD` 包装脚本指向 `httpx.exe` 才能跑；CI 上没有 Go 工具链时
`pytest.skip`（不是 fail），所以这条 E2E 在 GitHub Actions 上会显示为 skipped 而不是失败。

### 9.18 P1：Observability —— 结构化日志与关联 ID（方案第 19 节）

#### 9.18.1 单一出口：`core/observability.py`

方案第 19 节要求「日志必须结构化」并逐步加入 `request_id` / `job_id` /
`step_id` / `worker_id`。实现只加了一个新模块，没有引入任何第三方日志库
（structlog / loguru 之类都不加），底层就是 stdlib `logging`：

```text
{"ts":"2026-10-01T02:27:38+00:00","level":"INFO","event":"job_step_finished",
 "job_id":"job_xxx","worker_id":"host-1234","step_id":"step_xxx",
 "tool":"httpx","target":"example.test","status":"succeeded","found_count":3,"duration_ms":1200}
```

对外只有两个动词：`log_event(event, level=..., **fields)` 与
`configure_logging(level, fmt, stream)`。事件名集中成模块常量
（`EVENT_HTTP_REQUEST` / `EVENT_JOB_CREATED` / `EVENT_JOB_STARTED` /
`EVENT_JOB_FINISHED` / `EVENT_JOB_STEP_FINISHED` / `EVENT_AGENT_PLAN_STEP` /
`EVENT_REQUEST_FAILED` / `EVENT_UNHANDLED_EXCEPTION` + 一组 `EVENT_WORKER_*`），
避免各处手写字符串漂移。

logger 名固定 `gef`，默认只挂 `NullHandler` 且 `propagate=True`：

- **未调 `configure_logging()`**（库用法、pytest）→ 不往 stderr 乱写，
  但 `caplog` 仍能抓到（测试全靠这个）；
- **调了 `configure_logging()`**（两个进程入口）→ 挂自己的 stderr handler
  并关掉 `propagate`，保证一条事件只输出一次。

#### 9.18.2 四个关联字段怎么绑上去的

用 `contextvars`（不是全局变量、不是 threading.local）——waitress 是多线程模型，
contextvar 在线程内独立，天然不会串号；代价是**必须成对还原**。

| 字段 | 绑定位置 | 还原位置 |
|---|---|---|
| `request_id` | `app.py:_bind_request_context`（`before_request`） | `teardown_request` |
| `job_id` | `jobs/executor.py:execute_job` 的 `observability.bind(job_id=...)` | `with` 退出 |
| `step_id` | `jobs/executor.py` 步骤循环的 `observability.bind(step_id=...)` | `with` 退出 |
| `worker_id` | `jobs/worker.py:Worker.startup` | `Worker.shutdown` |

于是「绑定一次，全链自动继承」：`execute_job` 绑了 `job_id` 后，它内部
`_execute_real_step` → runner → artifact 落盘的任何事件都自动带 `job_id`；
worker 绑了 `worker_id` 后，它跑的所有任务事件都带 `worker_id`。

**`Worker.__enter__` / `__exit__`**：为了让「成对」不可能被忘掉，
`with Worker(...) as worker:` 进入即 `startup()`、退出即 `shutdown()`。
`main(--once)` 与 M7 的 E2E helper 都已改成这种写法。

#### 9.18.3 脱敏：两条规则 + 一个必须知道的副作用

1. **字段名命中敏感词** → 值只记 `***`，原文绝不落盘。敏感词：
   `api_key` / `apikey` / `token` / `secret` / `password` / `passwd` /
   `credential` / `authorization` / `cookie`。这条规则**优先于**下面的 ID 规则
   （所以 `token_id` 也只会记 `***`）。
2. **其余文本**先过 `core.runner_result.scrub_text`（沿用 M4 的唯一脱敏出口），
   于是 `user:pw@host`、`--token VALUE`、裸长 token 都被打码。
3. 单字段超过 500 字符截断；list / tuple / set / dict **最多记 20 项**
   —— 方案第 19 节「不要记录完整目标列表到公共日志」的落点。

**副作用（刻意接受）**：`scrub_text` 里的 `_SECRET_FLAG_RE` 会把
`X-Local-Token 请求头` 这种散文写法里紧跟 `-Token ` 的词也打码。因此 401 的
`error_message` 在日志里会变成 `…请携带 X-Local-Token ***`。
这是**失败即关闭**的取舍：宁可多打码，也不能漏一个真实密钥；
完整未打码的原文仍可在 HTTP 响应体与 `audit_events` 里看到。

**关联 ID 为什么不能被误打码**：`job_` + 32 位十六进制恰好是 36 个字符，
会被 `_LONG_TOKEN_RE`（≥20 个连续 `[A-Za-z0-9_-]`）当成裸 token 全部打成 `***`
——那结构化日志就自废武功了。做法是按字段名区分：`*_id` 结尾的字段按**标识符**
原样记录（只截断长度）；自由文本则先用占位符把 `(job|step|run|asset|obs|art|exp|scope|evt|req|wkr)_[0-9a-f]{6,}`
挖出来，脱敏后再还原。

#### 9.18.4 接线清单（都是加法）

| 位置 | 加了什么 |
|---|---|
| `config.py` | `LOG_LEVEL`（`GEF_LOG_LEVEL`，默认 `INFO`）、`LOG_FORMAT`（`GEF_LOG_FORMAT`，默认 `json`）|
| `.env.example` / `README.md` | 「结构化日志」配置段与排障用法（拿 `X-Request-Id` 去 `grep`）|
| `app.py` | `before_request` 生成/沿用 `request_id`；`after_request` 回写同名响应头并记下状态码；`teardown_request` 记 `http_request_finished`（**只记 `path`，不记 query**）|
| `core/errors_handlers.py` | 三个 handler 各加一条事件：`request_failed`（4xx=WARNING / 5xx=ERROR）、`unhandled_exception`（ERROR，另保留 `app.logger.exception` 打完整栈）|
| `api/jobs.py` | 创建任务后记 `job_created`（带 `request_id`、工具名、**目标个数而不是目标列表**）|
| `jobs/executor.py` | `execute_job` 包一层 `bind(job_id)`；开始记 `job_started`；每步记 `job_step_finished`（示范事件；失败为 WARNING）；收尾记 `job_finished`（取消路径同样记）|
| `jobs/worker.py` | `startup`/`tick`/`run`/`shutdown` 记 `worker_started`/`worker_claimed_job`/`worker_job_finished`/`worker_job_exception`/`worker_idle_exit`/`worker_shutdown`；`Worker.log` 的人读行**同时**转成 `worker_message` 事件；`__main__` 里 `configure_logging()`|
| `agent/action.py` | 原 `print(f"[debug] … result={tool_result}")`（会把整份结果含目标列表倒进控制台）改成 `agent_plan_step` 事件，只记 `tool` / `args` / `ok` / `error`|

#### 9.18.5 三条源码守卫（把「禁止」写成会失败的测试）

`tests/unit/test_observability.py` 末尾用 AST 扫源码（不是字符串匹配）：

1. `test_no_print_of_secret_shaped_values_in_source` —— 方案第 19 节点名禁止的
   `print(f"api_key={key}")` 形状。唯一豁免：`app._print_login_hint`（启动横幅，
   M1 验收项「明确日志提示」，只写本机控制台）。
2. `test_observability_is_the_only_logging_entry` —— 除 `core/observability.py`
   与 `core/errors_handlers.py`（要打完整 traceback）外，不得出现
   `logging.getLogger` / `logging.basicConfig`。
3. `test_new_print_calls_must_be_registered` —— 现存 50 处 `print` 按
   `文件:函数` 粒度登记（`_PRINT_ALLOWLIST`）。新增一处 `print` 就会失败，
   从而被迫回答「这条信息该进结构化日志吗」。**注意粒度是函数而不是文件**：
   `modules/base.py` 登记了 `_execute` / `_execute_stdout`，
   但这不影响该文件里将来新增函数时被拦下。

#### 9.18.6 `duration_ms` 的一处语义差异（别踩）

`job_step_finished` 事件里的 `duration_ms` 是**真实墙钟测量**：`outcome` 里没有
时（mock 步骤）用 `time.perf_counter()` 补。但**库里的 `job_steps.duration_ms`
仍然是 NULL** ——M4 的契约是「mock 不写假数据」，测试
`test_mock_steps_carry_parser_version_absent` 明确断言这一点。两者不矛盾：
一个是「我花了几毫秒跑这一步」的观测，一个是「真实 runner 报告的耗时」。
（第一版实现误把补出来的值也写进了库，被该用例当场拦下。）

#### 9.18.7 新增用例（+69，759 → 828）

| 文件 | 例数 | 覆盖 |
|---|---|---|
| `tests/unit/test_observability.py` | 50 | 事件信封、contextvar 绑定/还原/线程隔离、`request_id` 校验、脱敏（敏感字段 / 自由文本 / 关联 ID 不被误打码）、容器上限、长字段截断、两种格式、`configure_logging` 幂等与分级、三条源码守卫 |
| `tests/integration/test_observability_chain.py` | 19 | Web 层（回写 `X-Request-Id`、逐请求唯一、失败响应也带、`path` 不带 query）、执行层（每步一条 `job_step_finished`、失败 WARNING、只记当前步骤目标）、worker 层（三层事件都带 `worker_id`、`with Worker` 退出后还原）、端到端「一个 `job_id` 串起整条链」+ 反向守卫（整份目标清单不得进日志） |

#### 9.18.8 「一个 `job_id` 串起整条链」的端到端验收

这是**长方案 P1-5 的验收原话**，也是本轮最有说服力的一条证据：
`test_single_job_id_stitches_the_whole_chain` 不按实现写、而是**按排障时的真实动作写** ——
拿一个 `job_id` 去日志里捞，断言六类事件一次全部出现且共用这一个 `job_id`：

```text
job_created（带那一跳 HTTP 的 request_id）
job_started
job_step_finished × N（每个目标一步，step_id 互不相同）
job_finished
worker_claimed_job（带 worker_id）
worker_job_finished（带 worker_id）
```

配套的反向守卫 `test_log_trace_never_contains_the_scope_target_list` 用 **12 个目标**跑一遍整条链，
然后逐条事件检查：既不许出现 `targets` / `target_list` / `all_targets` / `scope_targets` 这类
「整份清单」字段，也不许任何**列表型字段**长度达到目标总数 ——
这一步把方案第 19 节最后那句「不要记录完整目标列表到公共日志」变成了可执行的断言
（只检查字段名是不够的：换个名字塞进去照样漏）。

另外两处**测试隔离**修正：

- `tests/conftest.py` 新增 autouse 的 `_reset_observability_context`：
  每例前后清空四个 contextvar。这层兜底保证「某个用例漏还原」不会污染后续用例。
- `tests/integration/test_m7_local_e2e.py:_drain_worker` 改用 `with Worker(...)`。
  这条是实测踩到的：它原来只 `startup()` 不 `shutdown()`，于是 `worker_id`
  泄漏到同线程里的**下一条用例**（症状：观测测试断言 `current_context() == {}`
  却看到 `{'worker_id': 'm7-e2e-after'}`，而单独跑该文件时全绿）。
  单文件跑没问题、全量跑才炸 —— 典型的测试间污染。

### 9.19 P0-6 阶段一：Application Service 入口收拢

方案第 6 节的原话是「**先增加统一 Service/Policy 入口，再迁移调用方；不要一次性进行
无边界重写**」。本节就是那句话的前半句，**只挪编排位置，不动数据模型、不动执行架构**。

#### 9.19.1 改前的实际拓扑（读码确认，不是推测）

```text
POST /api/jobs → api/jobs.py:create_job
   解析目标 → 校验工具 → 限流 → Policy 判定 → 模式解析 → 落库 → 审计 → 结构化日志
   ↑ 以上全部**内联在视图函数体内**（约 90 行）

首页表单 POST / → app.py:index
   from api.jobs import _resolve_targets      ← 反向导入 api 层的**私有**函数
   + 把 Policy 判定抄了第二遍（validate_job_targets → resolve_mode → create_job）

Agent → agent/action.py:_tool_subdomain / _tool_httpx
   tool_runner.run_tools(...) / HttpxRunner.run_scan(...)   ← 完全绕过 Job 链
```

两个后果，都是可验证的而不是理论担忧：

1. **同一套判定有两份实现**。`create_job` 里的字段顺序、错误文案、限流口径一旦调整，
   `index()` 那份不会跟着变 —— 改一处漏一处。
2. **Agent 那条路上的「同一次扫描」与主链不是同一件事**。在主链上它是一个可审计、
   可取消、可重试、且**执行前会重新复检 Scope** 的 Job；在 Agent 链上只是一次
   同步函数调用：没有 job 记录、没有 step 快照、没有 artifact、没有审计事件，
   Scope 只在入口判了一次。这就是 P0-6 要移动的**权限边界**。

#### 9.19.2 新增 `core/application.py`

放在 `core/` 而不是 `agent/`：它是**与调用方无关**的编排层，Agent 只是将来的调用方之一。

| 导出 | 职责 |
|---|---|
| `create_scan_job(...)` | 创建扫描任务的**唯一**编排入口 |
| `resolve_targets(...)` | 目标来源解析（显式列表 + 受控 `upload_id`），从 api 层私有函数升为公开接口 |
| `split_str_list(...)` | 请求字段 → 字符串列表（原 `api/jobs.py:_split_list`） |
| `JobSubmission` | 返回 `job` / `scope` / 实际入库的 `tools` 与 `targets`；`to_dict()` 给出与 `POST /api/jobs` **完全一致**的响应体 |

校验顺序与历史**逐条一致、刻意不重排**（重排会改掉「哪个错误先报出来」，从而改掉
响应文案与错误码）：

```text
目标非空 → 工具非空 → 幂等键合法 → 工具受支持 → 目标数上限
  → Scope/Policy（validate_job_targets）→ 模式开关（resolve_mode + require_active_scan）
  → mock 场景名 → 落库（create_job_with_status）→ 审计 + 结构化日志
```

#### 9.19.3 边界为什么刻意收窄

这是本轮最关键的设计决定：**阶段一不制造新的「第二处实现」**。

* **不碰 Flask**：认证、请求解析、HTTP 状态码仍由 `api/` 与 `app.py` 负责。
  服务层只接收已经解析好的标量/列表，返回结构化结果或抛 `core.errors` 里的业务异常
  —— 于是它既能在 HTTP 请求里被调用，也能在将来的 Agent/CLI 里被直接调用。
* **不自实现 Scope 判定**：一律转交 `core.policy.validate_job_targets`。
  「Scope 判定只有一处」这条 P0-2 的成果不能被这次重构稀释。
* **不改数据结构**：只调用 `core.jobs` 已有的写入函数，一行 DDL 都没动。

#### 9.19.4 三条源码守卫（比功能测试更重要）

功能测试只能证明「现在是对的」，证明不了「以后不会退回去」。所以
`tests/unit/test_application_service.py` 里用 `inspect.getsource()` 把结构本身钉住：

| 守卫 | 断言 |
|---|---|
| `api/jobs.py` 不得再内联编排 | 源码里不出现 `validate_job_targets` / `create_job_with_status` / `normalize_idempotency_key` / `resolve_mode` / `audit.record(audit.EVENT_JOB_CREATED` |
| `app.py` 不得再反向导入 api 层私有函数 | 源码里不出现 `from api.jobs import _resolve_targets` |
| 服务层不得自己比较白名单 | `core/application.py` 里不出现 `allowed_domains` / `allowed_cidrs` / `fnmatch` |

> 为什么值得这么写：这次重构的核心价值是**「只有一处」**，而最容易发生的退化
> 就是「为了图省事把一段逻辑抄回视图函数」—— 那种改动不会让任何功能测试变红。

另加两条**等价性**断言：同一份输入走 HTTP 与直调服务层，落库结果逐字段一致；
响应体字段集合相等。

#### 9.19.5 阶段二（未做）的影响说明

> **完整版已单独成文**：[`docs/AGENT_ASYNC_IMPACT.md`](AGENT_ASYNC_IMPACT.md)
> ——九条影响（I-1～I-9）、必改代码清单、测试同轮改法、三条缺失能力的补救选项、
> 以及唯一一个需要用户拍板的问题（Agent 只读 handler 是否同轮改读新库）。
> 下面保留压缩版。

`agent/action.py` 仍在直接调 `run_tools` / `HttpxRunner.run_scan`。把它接到
Application Service → Job 链，**不是换个函数调用**，而是：

* **Agent 执行变为异步**：回复里给 `job_id` + `queued`，结果由 worker 产出；
  现在那种「一句话说完就把子域名列表念出来」的体验会消失；
* `agent_cli.py` 与首页 `action=chat` 的交互语义随之改变（需要轮询或二次询问）；
* `tests/unit/test_agent_boundary.py` 现在 monkeypatch `agent.action.run_tools` /
  `agent.action.HttpxRunner` 来断言「被拒绝时两者都没被调用」——
  目标达成后这两个符号不该再存在于 `agent/action.py`，该文件需按新边界重写。

**六个 handler 里只有 2 个是「实际扫描动作」**（`subdomain` / `httpx`），
`summary` / `view_results` / `alive_results` 是只读查询、`export_results` 是导出登记，
都不产生「执行权」，**不在方案第 6 节的验收范围内** —— 这把迁移面从 6 个收窄到 2 个。

**顺带查实的一处现存越权通道**：`tool_runner.py` 与 `modules/httpx.py` 全文
**没有任何 `resolve_mode` / `real_scan_enabled` / Scope 引用**（实测 grep 零命中），
所以 Agent 这条路**不需要 `GEF_ALLOW_REAL_SCAN=true`、也不需要 Scope 就能真实外发扫描**，
而主链 `POST /api/jobs` 与首页要过「环境开关 + `scope.require_active_scan()`」双重门槛。
这才是用户定的验收点「**权限边界移动了**」的实质内容（详见影响说明 I-5）。

按用户约束「**若某一步需要改变核心数据模型或执行架构，先停下来说明具体影响再继续**」，
以上影响须先经确认，阶段二才开工。

#### 9.19.6 回归测试（+27，847 → 874）

`tests/unit/test_application_service.py`：三条源码守卫 + 两条等价性断言 +
逐条覆盖历史口径（参数缺失 / 未知工具 / 越界目标 / 超限 / 非法幂等键 /
非法 `scenario` / real 模式双开关 / 幂等命中 `reused` / 上传目标同样过 Scope /
`upload_id` 任意路径被拒 / 结构化日志确实打出 `job_created`）。

### 9.20 M6：一键环境自检 `scripts/check_env.py`

#### 9.20.1 它回答什么问题

「**这台机器上，本机联调版能不能跑起来、能不能跑真任务？**」

十四项检查分四类。这个分类本身就是排障顺序：先看解释器与依赖（起不来最常见的原因），
再看目录权限与 `.env`，最后才是外部工具 / 数据库 / worker。

| 类 | 检查项 | 判定 |
|---|---|---|
| 解释器与依赖 | Python 版本 | `<3.10` → fail；`>=3.13` → warn（依赖清单按 3.11 钉版本，新解释器未必有轮子） |
| | `requirement.txt` 27 项 | 逐项 `==` 核对；缺失 → **fail**（起不来） |
| | `requirement-dev.txt` 3 项 | 按 `>=` 判定；缺失 → warn（只是跑不了测试） |
| 运行期目录 | `results/` `uploads/` `exports/` `backups/` | 目录不存在不算错（按需创建），改为检查项目根可写 |
| `.env` 与安全 | `.env` 是否存在 | 不存在 → warn（会用全默认值） |
| | `SECRET_KEY` | 弱/缺失 → warn（进程级一次性密钥，重启后会话全失效） |
| | `LOCAL_ADMIN_TOKEN` | 空 → warn（启动打印临时凭据） |
| | `WEB_DEBUG` | `true` → **fail**（会暴露调试器） |
| | `WEB_HOST` | 非 `127.0.0.1`/`localhost`/`::1` → warn |
| | `GEF_ALLOW_REAL_SCAN` | 开启 → warn（正向提示：真的会调外部工具） |
| 运行时 | 17 个外部工具 | `shutil.which` 探测；0 个可用 → warn（mock 不受影响） |
| | 旧结果库 | 不存在 → warn（正常）；存在但打不开 → fail |
| | 本机应用库 | 不存在 → warn 且**不建库**；缺 10 张关键表之一 → fail |
| | worker 心跳 / 队列 | `ok` / `stale` / `missing`；队列只给计数 |

#### 9.20.2 三条硬性质（都有用例锁定，不是注释里的承诺）

1. **只读** —— 不写任何文件、不建库、不发网络请求、不执行任何扫描工具。
   用例用「目录逐条目 `mtime_ns` + `size` 快照比对」验证；应用库不存在时只报 warn，
   并确认文件**真的没被创建出来**。目录权限只用 `os.access` 判定，
   **刻意不写探针文件再删** —— 那会在仓库里留痕（AGENTS.md 硬约束）。
2. **不泄密** —— 报告里不得出现 `SECRET_KEY` / `LOCAL_ADMIN_TOKEN` 的值。
   用例塞哨兵串后在**人读报告与 `--json` 两种输出**里各搜一遍，
   同时要求仍然报出「已配置」，而不是装作看不见这两个键。
3. **退出码语义** —— `ok` → 0、`warn` → 1、`fail` → 2，`--strict` 把 warn 也当 2（CI 用）。
   自检脚本的退出码错了，挂进 CI 等于没挂。

#### 9.20.3 三个值得记住的实现决定

* **不引入 `packaging`**。它不在依赖清单里，而这个脚本恰恰要能在「依赖还没装」时跑得动。
  代价是自己实现 `_version_key` / `_satisfies` —— 但顺手避开了
  `"3.10" < "3.9"` 为真的字符串比较坑（那会把合法的 Python 3.10 判成过旧），
  并处理 `2.0 == 2.0.0` 与 `1.0.0rc1 < 1.0.0`；认不出的规格**不误报**。
* **不用子进程探测工具**。只看 `shutil.which`；用例把
  `subprocess.run` / `Popen` / `check_output` 与 `os.system` 全换成会抛异常的桩，
  证明它不会启动任何进程（详见下面第 5 条高频坑：探测命令本身就是一次执行）。
* **warn/fail 必须带 `hint`**（用例强制遍历）：只说「有问题」不说「怎么办」的报告没人能用。

#### 9.20.4 测试加载它时的坑（`sys.modules` 必须先注册）

`tests/unit/test_check_env.py` 用 `importlib.util.spec_from_file_location` 加载脚本
（`scripts/` 不是包），**必须先 `sys.modules["gef_check_env"] = module` 再 `exec_module`**。

原因：脚本用了 `from __future__ import annotations` + 冻结 `@dataclass`。
`dataclasses` 处理字符串注解时会去 `sys.modules[cls.__module__]` 里取该模块的名字字典，
取到 `None` 就直接 `AttributeError: 'NoneType' object has no attribute '__dict__'`。
这个报错完全不提 dataclass，第一眼会让人以为是脚本本身写错了。

#### 9.20.5 CI 里的定位：warn 是预期结果

`.github/workflows/ci.yml` 增加了一步「环境自检冒烟」，但**只把退出码 2 当失败**：

```yaml
run: python -c "import subprocess,sys; r=subprocess.run([sys.executable,'scripts/check_env.py']); sys.exit(0 if r.returncode <= 1 else 1)"
```

CI runner 上本来就没有 `.env`、也没有那 17 个 Go 工具，warn（退出码 1）才是正常的。
这一步要证明的不是「环境是好的」，而是「**一台干净机器上也能跑完并给出可读结论**」。

#### 9.20.6 回归测试（+26，874 → 900）

`tests/unit/test_check_env.py`：版本比较与规格判定（参数化）、退出码三态与 `--strict`、
`--json` 可解析且字段齐全、哨兵串不进任何输出、`WEB_DEBUG` 与非回环绑定的判定、
四个目录「跑完一模一样」、库不存在时不建库、坏库与缺表分别报 fail、
检查维度不可悄悄变少、warn/fail 必须带 hint、探测过程不得启动子进程。

> **本轮的 mypy 范围变化**：`mypy ... modules scripts`（61 → **63** source files）。
> `scripts/` 里的三个 `.py` 原先完全不在类型检查范围内。

---

### 9.21 M7 测试报告 `docs/TEST_REPORT.md`（本轮）：三条实测事实 + 一个被它抓出来的缺陷

本轮报告本身零业务代码改动，但**为了修它实测出来的测试隔离缺陷**动了四个测试/配置文件
（`tests/conftest.py`、`tests/unit/test_agent_boundary.py`、`tests/unit/test_jobs_executor.py`、
`tests/unit/test_repo_layout.py`）与 `config.py` 的一行出口。报告用三个一次性探针把
「哪些代码真的被测过」从印象变成了数字，这三条都有定位价值，记录在此（复现方式在报告里）。

#### 9.21.1 路由覆盖：41 条方法绑定，40 条被真实走到

> **口径快照说明**：本节是 M7 当时的实测（绑定 41）。后续里程碑新增了路由，
> 最新的覆盖口径见 §9.26.7 与 `docs/TEST_REPORT.md` §10 ——
> 结论（**只有 `GET /api/tool/<tool_name>/results` 一条从未被走到**）在各轮中一直成立。

做法：包装 `flask.Flask.full_dispatch_request` 跑一遍全量测试，收集实际命中的
`method + rule`，再与 `app.url_map` 求差。

```text
声明的方法绑定: 41
测试命中的:     40

== 没有被任何用例走到的方法绑定 ==
    GET /api/tool/<tool_name>/results
```

这是**唯一**一条没有任何用例走到的路由。它是 `api/results.py` 的旧库查询接口
（匿名可读），读 `results/scan_results.db`；测试从设计上不碰仓库旧库，
且本机该库 **20 张表全为 0 行**，所以测了也只是空结果。
另有一条「命中但不是声明路由」的记录 `GET /api/export/exp_x/../../etc/download`
—— 那是穿越防护用例**故意打的 404 路径**，属预期。

> **顺带发现的文档不一致**：`SECURITY.md` 与 `README.md` 都写「7 个匿名只读接口」，
> 但 `tests/integration/test_api_auth_contract.py` 的 `ANONYMOUS_READABLE` **只列了 5 条**
> （缺 `/api/tool/<n>/results` 与 `/api/export/<id>/download`）。也就是说这个「7」
> 目前只有 5 条被参数化用例直接钉住，另外 2 条靠 `test_export_contract.py` 的
> 匿名导出用例与代码审阅间接覆盖。**数字与测试清单不一致，但两边都没错** ——
> 改法见 `docs/TEST_REPORT.md` §3.1。

#### 9.21.2 有 10 个业务模块，测试源码从未提及

做法：遍历 91 个业务 `.py`（排除 `tests/` 与缓存目录），检查其模块名是否出现在
`tests/**/*.py` 的源码里。结果 **10 个从未被提及，全部在 `agent/`**：

```text
agent/model_result.py            agent/providers/{deepseek,ollama,openai_compat,qwen}.py
agent/plan_state.py              agent/skills/osint_recon.py
agent/strategy_templates.py      agent/system_prompt.py
agent/target_ranker.py
```

这条与 §7.6「Agent 运行时不调用大模型」是同一件事的两面：
`agent/client.py` 与 `agent/providers/*` **没有调用方**，规划由
`agent/intent.py` 的正则 + `agent/planner.py` 的模板决定。**没有调用方就没有可测的
运行时行为** —— 给这套未接线的 provider 写单测，测的是「这段死代码本身能不能跑」，
会得到一张好看但误导的覆盖图。真接线时应与接线同一轮补测试。

#### 9.21.3 测试静态检查的三条命令都覆盖了什么（口径提醒）

| 命令 | 覆盖 | **不覆盖** |
|---|---|---|
| `ruff check .` | 全部 `.py`（含 `tests/`、`scripts/`） | 规则集保守（`E4/E7/E9/F`），不管风格与复杂度 |
| `mypy ... modules scripts` | 63 个业务源文件 | `tests/` 与 `agent/providers/*`（后者见 §9.13 末） |
| `node --check` | `web/static/*.js` 的**语法** | 任何运行时行为，没有浏览器执行过 |

`coverage` / `pytest-cov` **不在依赖清单里**，本轮也没有为了出覆盖率图而引入它们
（属依赖变更）。报告里的「覆盖」一律是**粗口径代理**（路由命中、模块提及），
路由命中不代表分支覆盖，模块被提及不代表逻辑被断言。

> 报告里另有一节专门证明 `901 passed` 不等于可信：Diff 属性别名那个缺陷
> （§9.12 末 / 第 6 节第 28 条）在 847 条全绿的用例下藏了很久，
> 因为既有用例写的是**文档体例**键名 `server`/`technology`，而 httpx 实际产出
> `webserver`/`tech`。**用例写的是真实输入还是文档体例，比用例数量更重要。**
> 同一节还记下第三个缺陷：**测试自己往仓库运行期目录里写**（`exports/` 每次 +1 个空 CSV、
> `results/local.db` 每次 +3 行、`worker_heartbeat` 被真实 Worker 刷新），详见 §9.21.4。

#### 9.21.4 「测试从不污染仓库」原先只是一条约定，不是一个断言

写报告时逐文件跑测试、对运行期目录做 SHA-256 快照比对，抓出三处稳定泄漏：

| 泄漏 | 成因 | 单跑一次的后果 |
|---|---|---|
| `exports/` 多一个空 CSV | `test_agent_boundary.py` 的 fixture 只 patch `UPLOAD_DIR`；`_tool_export_results` 走 `exporter` 的**模块级** `EXPORT_DIR` | 文件数 +1 |
| `results/local.db` 多 3 行 | `test_security_baseline.py` 的两个上传用例直接调 `core_uploads.save_upload()`，它内部 `db.ensure_schema()` + `db.transaction()` 用的是仓库库路径 | `audit_events` / `uploads` 各 +3 |
| `results/worker_heartbeat` 被刷新 | 三个文件起真实 `Worker`；`jobs/worker.py` 从 `config` 导入的 `OUTPUT_DIR` 是自己的副本，patch `core.health` 无效 | mtime 被改写 |

**根因是保障挂错了位置**：原先只有 `tests/conftest.py:app_module` 一个夹具在 patch，
而**绕过它的用例（只用 `local_db` 或不用任何夹具）根本不受约束**。

修法（三件一起才成立）：

1. 新增 autouse 夹具 `tests/conftest.py:_isolate_runtime_dirs`：`config.LOCAL_DB_CONFIG["path"]`
   + `core_db.reset_schema_cache()`，外加 `exporter.EXPORT_DIR`、`core_uploads.UPLOAD_DIR`、
   `core_health.OUTPUT_DIR`、`jobs.worker.OUTPUT_DIR`、`core_artifacts.ARTIFACT_DIR` 五处模块属性。
   **autouse 是关键** —— 不再依赖用例「记得」要哪个夹具。
2. 子进程读不到父进程的 monkeypatch，所以给 `config.OUTPUT_DIR` 加了 `GEF_OUTPUT_DIR`
   环境变量出口（与既有的 `GEF_SCAN_DB_PATH` / `LOCAL_DB_PATH` 同规格），
   `test_jobs_executor.py` 的 kill/restart 用例把它传给子进程。
3. 新增回归锁 `test_repo_layout.py::test_autouse_fixture_redirects_every_runtime_path`：
   逐条断言这些路径都不在仓库目录下。**夹具被删或漏项时立刻变红**，
   而不是等下次提交才发现工作区脏了。

**仍然成立的脆弱点**：`modules/base.py` / `modules/httpx.py` / `modules/dnsx.py` 的
`OUTPUT_DIR` 也是导入期绑定，autouse 夹具只覆盖 `jobs.worker` 这一处；
起真实子进程的用例必须**父进程 patch + 子进程传 `GEF_OUTPUT_DIR`** 两件都做。

---

### 9.22 公网授权测试模式体验版（方案第 5～11 节）

> 依据：`docs/milestones/GetEverything_公网授权测试模式体验版方案.md`。
> 目标是一句话：**让「扫自己的授权目标」这件事在框架里有正规入口，
> 而不是靠人去手改 `.env` 和 Scope。**

#### 9.22.1 为什么不是「把闸门放宽」

方案第 2 节写死了两条产品原则，本节改动完全围绕它们：

1. **不得绕过 Scope / Policy** —— 新入口不自己判断目标合不合法，
   而是**转交** `core.application.create_scan_job`（P0-6 阶段一收拢出来的唯一编排入口）。
   `test_public_scan_mode.py::test_service_delegates_to_single_job_entry` 直接从源码上锁这一点。
2. **禁止 Web → Runner** —— `api/public_scan.py` 里不允许出现 `build_runner` / `run_tools`
   / `RUNNER_REGISTRY` 字样；同样有源码守卫。

所以本节的净新增是**三层闸门之上再加两层组织与白名单**，而不是把原来那三道闸拿走。

#### 9.22.2 新增的四个模块

| 文件 | 职责 | 关键不变量 |
|---|---|---|
| `core/tool_registry.py` | 17 个 runner 的**权限元数据**（`risk_level` / `internet_allowed` / `default_enabled` / `reason`）+ 三档策略模板；**规划方案 Phase 2 起**再承载**能力分组与用途说明**（`ToolGroup` / `TOOL_GROUPS` / `description` / `tool_group` / `group_tool_policies()`，见 §9.25） | ① **没登记 = 禁止公网**（`assert_tools_internet_allowed` 对未知工具直接拒，不默认放行）；② 公网白名单恰好 `{httpx, subfinder}`；③ 模板里的工具必须全部在白名单内（有测试）；④ **分组不改变权限**（`test_tool_group_never_changes_permission`），每个工具必须声明一个已登记的分组 |
| `core/projects.py` | 授权测试项目：创建 / 读取 / 关联既有 Scope / 按 Scope 反查项目 | 只**新增** `projects` + `project_scopes` 两张表，**`scopes` 表零改动**（方案第 10 节 / DECISIONS-E）；关联项目**不放宽**任何权限 |
| `api/projects.py` | `/api/projects*` 四条路由 | 只关联**已存在**的 Scope；创建 Scope 仍然只有 `POST /api/scopes` 一处 |
| `api/public_scan.py` | `/api/public-jobs`（202）+ `/api/scan-center`（页面元数据） | 视图层不含任何闸门逻辑，只做参数转发与 201/202 组装 |

`api/__init__.py`、`core/db.py`（建表）、`core/ids.py`（`proj_` 前缀）、
`core/audit.py`（`project.created` / `project.scope_attached`）都是**加法**。

#### 9.22.3 公网任务的完整闸门顺序

`core/application.py:create_authorized_public_job` 是唯一入口，顺序**刻意**如下
（每一步都在前一步不通过时立刻返回，不产生任何落库副作用）：

```text
① 项目存在？           否 → 404 not_found
② scope_id 挂在该项目下？ 否 → 400 bad_request（附 attached_scope_ids，告知当前挂了哪些）
③ 解析策略模板 → 工具清单 → 公网白名单校验
                        否 → 400 bad_request（附 blocked_tools + internet_allowed_tools）
④ 默认 mode=real（**不静默降级为 mock**）→ 转交 create_scan_job
⑤ create_scan_job 内部：目标非空 → 工具非空 → 幂等键 → 工具受支持 → 目标数上限
                        → Scope/Policy → 环境开关 → 落库 → 审计
```

第 ④ 步是本次一个**刻意的取舍**：真实扫描失败时**报错**，而不是退回 mock 给一份假数据。
理由是「以为打了真实目标、其实拿到编的数据」比直接报错危险得多
（`test_real_mode_without_env_switch_is_403_and_does_not_fall_back_to_mock` 锁住）。

`mode=mock` 仍然可以显式指定 —— 那是演练用的，闸门（①②③）一条都不少。

#### 9.22.4 前端：扫描中心（方案第 7 节）

- 新增页面 `GET /scan-center`（`web/templates/scan_center.html`），三块：项目 / 创建任务 / 任务列表；
- 新增 `web/static/scan_center.js`：**全部判断都是转发**，不决定「能不能扫」；
- `web/static/app.js` 末尾把三张文案表（状态 / 步骤状态 / 错误码）挂到 `window.GEF_UI`，
  扫描中心复用它们 —— **同一个 `error_code` 在两页不会显示成不同的话**；
- 被禁工具在界面上**置灰但保留展示**（附 `reason`），这是可用性提示而**不是**安全边界：
  绕过 DOM 直接发请求一样会被后端 400。

#### 9.22.5 一处**必须知道**的测试隔离修复

新增 `.env`（本机开发配置）之后，全量测试冒出两个**与被测代码无关**的失败：

| 失败用例 | 症状 | 根因 |
|---|---|---|
| `test_m2_security.py::test_health_exposes_mode_and_security_status` | `real_scan_enabled` 断言 `False` 却拿到 `True` | 本机 `.env` 里 `GEF_ALLOW_REAL_SCAN=true` 被 `config.load_dotenv()` 读进测试进程 |
| `test_observability.py::test_configure_logging_filters_by_level` | `json.loads` 解析失败，拿到的是 `ts=… level=ERROR …` 文本 | 本机 `.env` 里 `GEF_LOG_FORMAT=text` |

修法在 `tests/conftest.py`：对这两个变量用**赋值**而不是 `setdefault`
（`load_dotenv()` 默认不覆盖已存在的环境变量），并写明为什么必须钉死。
`test_m4_runner_result.py::real_mode` 用 `monkeypatch.setenv` 打开，用例结束自动回滚 —— 不受影响。

> **教训**：「测试跟随开发者本机配置」会让同一份代码在这台机器绿、在那台机器红，
> 且红的原因与被测代码毫无关系。凡是 `.env` 能覆盖、而测试又对其有断言的开关，
> 都必须在 conftest 里钉死。

#### 9.22.6 回归测试（+105，901 → 1004 passed / 2 skipped）

| 文件 | 覆盖 |
|---|---|
| `tests/unit/test_tool_registry.py`（41） | 元数据完整性（注册表 ↔ 元数据表**双向**无缺漏）、白名单恰好两个、判定报错形状、模板解析的四种拒绝；**Phase 2 起**再含分组（每个工具声明已登记分组、分组与权限无关、分组覆盖恰好一次、空栏位顺序、未登记分组抛错、子集输入） |
| `tests/unit/test_projects.py`（26） | 创建/校验/上限/去重、关联幂等、**`scopes` 表结构逐列比对**、关联项目不改写 Scope 本体 |
| `tests/integration/test_public_scan_mode.py`（45） | 方案第 9 节五类（Scope / Policy / Job / Tool / Worker）+ 第 11 节验收 + 扫描中心页面 + 两条源码守卫 + 旧链路不受影响 |
| `tests/conftest.py` | 环境变量钉死（见 §9.22.5） |

> 计数口径：`--collect-only` 汇总（`34 + 26 + 45 = 105`）；
> 排除这三个文件后收集数为 **901**，与上一轮基线**逐条相等** ——
> 即本轮没有任何既有用例被删改。

方案第 9 节的五类对应关系：**Scope** → `test_public_domain_scope_can_be_created`
与 `test_public_ip_target_is_checked_against_allowed_cidrs`；
**Policy** → `test_out_of_scope_target_is_403`；**Job** → `test_public_job_enters_queue_with_audit_record`；
**Tool** → `test_blocked_tool_cannot_be_submitted`（5 个参数化）；**Worker** → `test_worker_executes_public_job`。

> **为什么 Scope 那类有两条**：方案第 4 节把目标类型写成「域名 / IP / CIDR」，
> 而第一版只有域名那条覆盖到了**公网入口链路上**。补的这条走
> `allowed_cidrs`（域名留空）→ 网段内 IP 放行、网段外 IP 403，
> 用的网段是 **RFC 5737 文档保留段**（`192.0.2.0/24` / `198.51.100.0/24`），
> 与用 `example.test` 是同一个思路：即使真发出去也不指向任何人的资产。

#### 9.22.7 本节的已知边界（不是缺陷，是范围）

- `nuclei` 在方案第 4 节被写作 `nuclei(限制)`，但**本项目 runner 里没有它**。
  处理方式是如实登记进 `KNOWN_UNAVAILABLE_TOOLS` 且 `internet_allowed=False`，
  在扫描中心作为「受限未开放」展示 —— **不假装有、也不悄悄漏掉**。
- 项目与 Scope 是**多对多**（`project_scopes`），但当前 UI 只做「项目 → 它的 Scope」正向选择；
  反向（一个 Scope 被几个项目引用）没有界面，只有 `projects.find_by_scope` 这一处后端能力。
- 真实公网扫描**不在本轮执行范围**：本轮实机验收全部打 `127.0.0.1` 与 RFC 6761 保留域
  `example.test`，`GEF_ALLOW_REAL_SCAN` 只在用例内临时打开。
  `scripts/verify_public_scan.py` 是可复跑的验收探针（只打保留域）。
  **凭据一律从环境变量读**（`LOCAL_ADMIN_TOKEN`；地址用 `GEF_VERIFY_BASE`，
  默认 `http://127.0.0.1:5000`），脚本里不写死任何值 —— 这是它能入库的前提。
  缺失凭据时退出码 2，并打印「该设哪个环境变量」（只打变量名，不打值）。
- **新增脚本必须进 `test_observability.py:_PRINT_ALLOWLIST`**：`scripts/verify_public_scan.py`
  用 `print` 把每一步的服务端原始响应（含错误体）打到 stdout，这正是它的用途，
  所以按 `scripts/check_env.py` 的同规格登记了 `main` / `show` / `_admin_token` 三处。
  这个守卫的设计意图是**逼人回答「这条信息该不该进结构化日志」**，不是禁止 `print` ——
  漏登记时全量测试会红，正是它该有的行为（本轮就真实触发过一次）。
  另有 `_ALLOWED_CREDENTIAL_PRINT`，管的是 print 里出现**凭据相关词**
  （`token|secret|api_key|…`）的位置；注意它匹配的是**词**不是**值**——
  正则必然命中「只是提到变量名」的语句，登记时必须自己确认打出来的是名字还是值
  （探针的 `_admin_token` 属于此类：它打印 `LOCAL_ADMIN_TOKEN` 这个名字，
  而且正是因为「没有值」才走到那一句）。

---

### 9.23 下一阶段体验优化：UI 清理 / 公网授权入口 / Scan Profile（方案 Phase 1～3）

> Phase 4（结果体验）见 §9.24。

> 依据：《GetEverything_下一阶段体验优化与公网扫描能力演进方案》（本机过程材料，不入库）。
> 产品原则：**保留安全边界，但降低用户操作复杂度**。方案第 8 节写死
> 「不绕过 Policy / 不绕过 Scope / 不删除审计」——本节所有改动都在这条线上，
> 三个阶段各自独立提交。

#### 9.23.1 一句话：这一节新增的是「表达方式」与「节奏」，不是新的权限

| 阶段 | 提交 | 净变化 | **没有**改变的东西 |
|---|---|---|---|
| Phase 1 UI 清理 | `e94b180` | 只改 `web/`：ID 从可见文案消失、去后台术语 | 任何请求体、任何闸门、任何路由 |
| Phase 2 公网授权入口 | `510fa41` | 新增 `core/authorization.py`（只读试算）+ 四步前端 | Policy / Scope 判定本身 |
| Phase 3 Scan Profile | 本轮 | 新增 `core/pace.py` + 执行期降速 + 前端展示节奏 | 公网白名单、`active_scan`、环境开关、DB 结构 |

#### 9.23.2 Phase 2：`core/authorization.py` 是**只读试算**，不是第二条 Policy

新增的痛点是具体的：旧实现下「目标越界 / 范围没开 `active_scan` / 环境总开关没开」
这三种**完全不同的情况**都返回同一个 `403 scope_violation`，用户只能靠读错误消息反推。

`check_target()` / `check_targets()` 把结论摊开：目标落在哪些已授权范围内、
每个范围什么状态、还缺哪一道闸门（`blocker` 五档：`invalid_target` / `no_scope` /
`not_authorized` / `scope_inactive` / `env_disabled`）。三条设计约束：

1. **匹配复用 `Scope.match_target`**，不另写一份匹配逻辑 —— 因此不可能出现
   「试算说能过、真提交过不了」这种最伤信任的不一致；
2. **只读**：不写库、不写审计、不发任何网络请求；
3. `TargetCheck.eligible` 单独建模 —— 命中**排除列表**的范围不算「可执行」。
   这是实现过程中真实踩到的一个坑：`blocker` 一开始用 `matches` 判断，
   于是「被排除」被误判成「有范围可扫」，`eligible` 拆出来后才对。

#### 9.23.3 Phase 3：`core/pace.py` —— 为什么「节奏」必须单独成层

`core/tool_registry.py` 回答的是「哪些工具允许打公网」，但**没有回答「允许打的工具
应该打多快」**。而后者才是用户最关心的一句话：**拿到授权的公网目标不等于可以对它
施加任意流量。**

于是把「节奏」提升为与工具组合并列的一维（Scan Profile = 工具组合 + 节奏）：

```text
light  低频 —— 覆盖并发与每秒请求数，并在真实步骤之间留间隔
normal 常规 —— 完全沿用工具自身配置，不额外等待（= 引入前的行为）
```

| 机制 | 实现位置 | 关键不变量 |
|---|---|---|
| 档位与文案 | `core/pace.py:PACE_LABELS` / `PACE_DESCRIPTIONS` | 中文文案**只维护一份**，页面 / 错误消息 / 接口同源 |
| 只能收紧 | `core/pace.py:resolve_pace()` | 任一为 `light` 即 `light`；请求体放松不了模板档位 |
| 严格 vs 宽松 | `normalize_pace()` / `coerce_pace()` | 请求体非法值**报错**；读库脏数据才回退缺省 |
| 工具预算 | `core/pace.py:LIGHT_TOOL_BUDGET` | 只覆盖公网白名单内的工具；白名单外的工具进不了入口 |
| 注入 | `core/pace.py:apply_to_runner()` | 写 `config` **副本**，绝不原地改模块级配置对象 |
| 步骤间隔 | `jobs/executor.py` 的 `for step in steps:` 内、`start_step` 之前 | 分片 sleep + 期间取消生效 + 长等待前续租 |
| 持久化 | `core/jobs.py:pace_of_job()` 读 `job.created` 事件 | **不加列**（DECISIONS §1 E 限纯增量） |

**为什么 `pace` 不落成 `jobs` 表的新列**：那是 DB 结构变更。写进 `job.created` 事件的
`detail` + 审计 detail、执行期按 `job_id` 读回，是**必然**而非偏好 —— worker 是**独立进程**，
且任务可能被 retry 或换一个 worker 重启，节奏必须是任务自身的属性，而不是某次调用的参数。

#### 9.23.4 执行期插入点的选择（踩过才知道为什么不能放别处）

「在步骤之间等一下」看似可以在多处实现，实际**只有一个合法位置**：

| 候选位置 | 为什么不行 |
|---|---|
| `modules/base.py` 的 `_execute` | 绕不到 `_execute_stdout`、`shuffledns.py:82`、`enscan.py:115` 这些自定义流程，mock 也根本不过去 |
| 新增 `waiting` 步骤状态 | `pending_steps()` / `reset_running_steps_on_start()` 只认 `pending` / `running`，新状态会在 retry 时把步骤**搁浅**，且 `aggregate_status()` 会报 `succeeded` |
| 与既有 `--step-delay` 合并成 `max()` | 两者语义不同（一个是运维在命令行显式降速，一个是「这个 Profile 是低频档」），叠加才是诚实的结果；且缺省路径下两者都是 0，不会多出任何 sleep |

最终落在 `jobs/executor.py` 的 `for step in steps:` 里、`jobs_store.start_step()` **之前**，
并且**刻意只在真实模式**生效（mock 不产生任何外部流量，对它等待只会让演练变慢）。
`test_execute_calls_renew_between_steps` 断言 renew 恰好被调 2 次 —— 新增的等待
只在 `pace_step_delay` 非 0 且已执行过至少一步时才 `renew()`，因此缺省路径的回调次数不变。

#### 9.23.5 一处**废弃的实现**及其原因（值得记住）

实现过程中曾新增 `modules/registry.py:build_scoped_runner(tool_name, pace)` —— 第二条
能带节奏的构造路径。**已移除**，改为「构造归 `build_runner`、降速归 `apply_to_runner`」两步。

原因：`build_runner(tool_name)` 是测试替换真实 Runner 的**唯一**接缝
（`monkeypatch.setattr("modules.registry.build_runner", ...)`，全仓 20+ 处）。多一条构造
入口，就等于多一个「假 Runner 没被替换、真去执行外部命令」的机会 ——
而「测试期不许打真实外部目标」是本项目的硬约束，最不该在这种地方留缝。
`tests/unit/test_pace.py::test_apply_to_runner_works_on_the_registry_seam` 把这个分工锁住。

> 附带一条：`apply_to_runner()` 在 `jobs/executor.py` 里被 `try/except` 包着 ——
> 降速是**策略**，不是执行前提；任何情况下都不该因为它把任务弄挂。

#### 9.23.6 前端：为什么节奏要写在**卡片上**

`web/static/scan_center.js:renderStrategies()` 给每张策略卡片加一行 `.sc-strategy-pace`。
只把「低频」放进 `#strategy-note` 是不够的：那一行会随用户切换策略而被覆盖，
切走之后就再也看不见「资产发现是低频档」这件事了。

说明文字**不写死在前端**，而是从 `GET /api/scan-center` 的 `paces[]`
（`{pace, pace_label, pace_description, step_delay_seconds}`）取 ——
`web/templates/scan_center.html` 里那句初始文案也会在元数据到位后被 JS 覆盖，
所以它只需与 `asset_discovery` 的描述保持同义，不会成为第二份事实源。

#### 9.23.7 本节的已知边界

- `LIGHT_TOOL_BUDGET` 只覆盖 `subfinder` / `httpx`。这不是遗漏：公网白名单之外的工具
  根本进不了公网入口，为不可达路径写预算等于写死代码。
- 低频档的**实际外发速率**没有被计量（只测到「命令行参数正确」与「步骤之间确有等待」）。
  真实工具对小并发 / 限速参数的解释由工具自身负责。
- `GEF_PACE_LIGHT_STEP_DELAY_SEC` 是**运维级**旋钮，不是策略模板的一部分：
  它允许把间隔调大，但**调不小**（`light` 恒 ≥ 0，`normal` 恒 = 0）。
- 本文件里凡写死「47 / 49」这类路由计数的段落，**都是当时的口径快照**；
  最新的实测值见 §9.26.7。

### 9.24 Phase 4：结果体验 —— 从 Job 导向结果

#### 9.24.1 一句话：这一节新增的是「结果怎么被读」，不是新的扫描能力

方案第 6 节把 Phase 4 写成：「结果体验：从 Job 导向结果；展示：发现资产；服务；
技术栈；风险信息。」它要解决的是**做完一次任务之后看不出到底看到了什么** ——
详情页只有「步骤 × 工具 × 结果数」，成果散落在另一页，没有任何一处把它们整理成人能读的形状。

**本轮新增的能力只有「读」**：一条新路由、一个纯函数派生层、一段前端渲染。
没有新增工具、没有放宽任何闸门、没有改表结构。

#### 9.24.2 `core/findings.py` 为什么是纯函数

它只 import `core.assets.ATTRIBUTE_ALIASES` 与 `urllib.parse`，不碰 sqlite / Flask / 配置 / 网络。
三条理由，都与前几节同源：

1. **可单测**：`tests/unit/test_findings.py` 不需要任何夹具，喂 dict 断言四段（35 条用例）；
2. **不引入写路径**：结果页是只读的，派生层一旦能写库，「看一眼」就变成了副作用；
3. **别名表只有一份**：`server` / `web_server` 归一、`tech` / `technology` 归一，
   与 `/diff` 用**同一张表**。`core/assets.py` 的注释记过一次同样的 bug ——
   两张表一旦漂移，「同一个 nginx 在 diff 里叫 webserver、在结果页不显示」就会复活。

#### 9.24.3 「风险信息」为什么不能做成漏洞报告（本节最该记住的一条）

方案第 5 节提到 `nuclei`，但本项目的事实是：`nuclei` 在
`core/tool_registry.py:KNOWN_UNAVAILABLE_TOOLS` 里、`internet_allowed=False`、
不在 `RUNNER_REGISTRY`，**全仓没有任何 CVE / CVSS / severity 数据**。

于是本阶段做了一个明确的选择：**不假装有漏洞扫描**，把「风险信息」如实降级为
「从已有观测里读出来的、值得人工看一眼的事实」。

| 设计选择 | 为什么 | 反例（如果按另一种做法） |
|---|---|---|
| `level` 只有 `info` / `notice` / `attention` | 语义是「值不值得人工看一眼」 | 用 `low/medium/high` 就等于声称「我们评估过危险程度」 |
| 九类提示全部是**可观察事实** | 每条都能在观测里指出来源 | 「存在 SQL 注入风险」这类结论本项目根本得不出 |
| `notes` 里**恒有**免责句 | 「没有提示 ≠ 没有漏洞」必须被说出来 | 只在零提示时补一句 → 有提示的那次反而看不到说明 |
| 两类**覆盖缺口**提示（未探测主机 / 失败步骤） | 把「没看」与「没问题」分开 | 一份干净的子域列表会被读成「这些主机已经查过了」 |
| 出参里没有 `severity` / `cve` 字段 | 前端才不会再造一个漏洞分级 | 有字段就会有人去填 |

对应用例：`test_findings.py::test_no_vulnerability_severity_concept_exists`、
`...test_summarize_never_emits_a_severity_or_cve_field`、
`...test_no_hints_is_not_a_clean_bill_of_health`、`...test_disclaimer_is_present_even_when_there_are_hints`。

#### 9.24.4 「只看本次任务」的口径与 `list_job_assets()`

`assets` 表**没有 `job_id` 列**（同一台主机被十次任务看到也只有一行），
所以 `core/assets.py:list_job_assets()` 从 `observations` 反查：

```sql
SELECT DISTINCT a.* FROM assets a
JOIN observations o ON o.asset_id = a.id
WHERE o.job_id = ?
```

这与 `/diff` 的口径**完全同源**（`_latest_data_by_asset(job_id)` 也是先按 `job_id` 过滤）。
若改成「该资产历史上被谁见过」，结果页会把**历史观测**混进「这次扫到了什么」，
而那正是 Phase 4 要消灭的歧义（用例：`test_results_only_include_this_jobs_observations`）。

**零 schema 变更**：`jobs` / `assets` / `observations` 三张表一字未改。
这与 Phase 3 的节奏持久化是同一条思路 —— 能从既有行派生，就不要加列
（`docs/DECISIONS.md` §1 E 限纯增量）。

#### 9.24.5 前端：容器同步插入、内容异步填充

`web/static/app.js:renderDetail()` 末尾插入一个**空** `.job-results` 容器，
再异步 `loadResults()` 填充。

为什么不是「取到数据后再 append」：紧跟其后的 `loadArtifacts()` 也是异步的，
两个请求谁先回来谁排前面 —— 页面顺序会随机跳动。先插空容器就把顺序钉死了。

同一处的两个约束：

- 渲染一律走 `textContent`（不拼 innerHTML，不引入 XSS 面）。**代价**是运行时文案里
  不得出现 Markdown 的 `**`，否则页面上会原样显示星号 —— 有源码级断言钉住
  （`test_runtime_copy_contains_no_markdown_markers`）；
- 风险级别的**中文文案**来自服务端 `level_label`，前端不写死
  （`test_app_js_does_not_hardcode_risk_level_wording`）—— 与 Phase 3 对节奏说明的处理同源。

`app.js` 由 `index.html` 与 `scan_center.html` **共用**，所以结果区在两个页面都会出现。

#### 9.24.6 本节的已知边界

- **`job_events` 仍未在 `app.js` 里渲染**：结果区展示的是资产 / 观测派生物，
  事件流仍只能通过 `GET /api/jobs/{id}/events` 看。这是刻意留的（事件是排查用的，
  不是给人读的结论），但确实还没做。
- **`mock` 模式的四段大多是空的**：mock 结果行会经兜底类型落成 `subdomain` 资产，
  但没有结构化观测属性，因此「服务 / 技术栈 / 风险提示」三段为空。
  返回里带 `MOCK_NOTICE` 明说这是预期行为，不是采集失败。
- **观测的 `data_json` 仍按字段拆开渲染**：这里只把 `status_code / title / webserver /
  tech / cdn` 这几项提出来（它们是有语义的），其余原样留在观测时间线里。
- **`GET /api/tool/<tool_name>/results` 依旧没有任何用例走到**（§9.21.1 的结论不变）。

---

### 9.25 下一阶段规划方案 Phase 2：Tool Registry（工具能力平台化）

依据：《6GetEverything-下一阶段规划方案》（仓库根，本机工作单，不入库）第 8、9、13、14 节。
一句话目标：**开放能力给用户，限制风险在后端** —— 前端可以放开工具选择，
后端一条闸门都不放松。

#### 9.25.1 字段名为什么叫 `tool_group` 而不是方案里的 `category`

方案第 9 节的示例条目写的是 `{"name","description","category","risk"}`。
本仓库**没有**照抄 `category`，理由是它已经有三重含义：

| 出现位置 | 含义 |
|---|---|
| `storage.py:TOOL_DATABASES[*]["category"]` | 结果落哪张表 |
| `modules/base.py:BaseRunner.category` | 运行器自报的观测类别 |
| `api/tools.py` 读 runner 的 `category` | 上面那个值的转发 |

再借它当「能力分组」，就会造出一个**同名异义**的字段：看接口的人永远说不清
`category=subdomain` 指的是观测类别还是能力分组。分组与观测类别是两件事，
字段名不共用 —— 分组叫 `tool_group`，观测类别仍是 `category`，
两者在同一份出参里并存（用例：`test_tools_api_keeps_the_historical_name_key`
断言 `entry["category"] != entry["tool_group"]`）。

#### 9.25.2 方案第 8 节那张五栏表是示意，不是要求填满

方案第 8 节画了「资产发现 / 服务识别 / 技术识别 / 漏洞检测 / 辅助能力」五栏。
本仓库的 17 个 runner 里，`技术识别` / `漏洞检测` / `内容发现` **本阶段确实没有
可跑的工具**（`nuclei` 未接入 runner）。

处理方式是**如实返回空栏位**，而不是把别的工具挪进去凑数：

- `TOOL_GROUPS` 有 6 栏（多一栏 `content` 内容发现），空栏位照样下发；
- 前端对空栏位显示「本阶段暂无可用工具」——
  藏掉栏位会让使用者以为是自己没找到；
- 用例 `test_group_tool_policies_keeps_empty_groups_in_plan_order`
  钉死顺序与空栏位，`test_scan_center_metadata_carries_tool_groups` 断言
  `tech` 必须在空栏位里。

#### 9.25.3 三个「真实存在」的参数缺陷（`load_tools` 参数标准化）

这一段的依据不是文档，是代码：

| 缺陷 | 位置（改前） | 后果 |
|---|---|---|
| 空工具**静默回落** | `tool_runner.py:74` `cli_tools or SCAN_CONFIG["enabled_runners"]` | 用户没选任何工具 → 系统拿配置默认值（当时是 `["amass"]`）去扫 |
| 逗号串被当成一个工具 | `api/scan.py:175-176` `isinstance(tools, str) → [tools]` | `"subfinder,httpx"` 变成名叫 `"subfinder,httpx"` 的工具 → 必然「存在不支持的工具」；同一个请求体从 `/api/jobs` 进得来、从 `/api/run` 进不来 |
| 全链不去重 | 两处都没有 | `total_steps = len(targets) * len(tools)`，同一工具写两遍 → 步骤数翻倍且重复执行 |

修法：

- 新增 `tool_runner.normalize_tool_names()` 作为**工具参数**的规范化实现
  （逗号拆分、逐项去空白、丢空项、去重保序、非字符串非数组 → `ValueError`）；
  > **口径校正（§9.30.4）**：此前这里写「全仓唯一一份参数规范化实现」是**过头话**。
  > `core/application.py:95 split_str_list()` 是另一份，服务对象是请求字段；
  > 两者不是同一个函数（它不去重、非法类型抛 `BadRequestError`）。端到端一致
  > 靠的不是「只有一份实现」，而是**去重与 registry 校验只有一个收口点**
  > （`load_tools`）。改一处时别忘了另一处，详见 §9.30.4。
- `load_tools(None)` 与 `load_tools([])` **严格分开**：前者是 CLI 语义（未指定 → 回落
  配置），后者是「明确不要」（→ 空列表，由调用方拒绝）。HTTP 侧一律传 `[]` 而不是
  `None`，因此**回落路径在 Web 上不可达**；
- `api/scan.py` 用 `"tools" in payload` 而不是 `payload.get("tools") or ...`
  —— `or` 恰好会把 `[]` / `""` 折叠成 `None`，正好落进回落分支；
- 工具名仍然逐一过 `get_supported_runners()`，**白名单一条都没放松**。

对应用例：`tests/unit/test_tool_parameters.py`（新，17 条），其中
`test_run_rejects_empty_tools_instead_of_falling_back` 对
`{"tools":[]}` / `{"tools":""}` / `{"tools":"  ,  "}` / 不给 四种形态逐一断言 400 ——
**只要出现 200，就说明「用户没选工具，系统自己挑了一个」这条路径又回来了**。

#### 9.25.4 注册表是唯一读出点，两个接口不可能漂移

`/api/tools` 与 `/api/scan-center` 的注册表字段都来自同一个
`ToolPolicy.to_dict()`：前者按 `get_supported_runners()` 遍历，后者按
`list_tool_policies()` 遍历，**取数函数只有一份**。
`test_tools_api_and_scan_center_agree_on_registry_fields` 逐字段比对两个接口，
`test_scan_center_metadata_carries_tool_groups` 断言分组里的条目与扁平列表**恒等**
（`flat[tool_name] == tool`）—— 同一个工具不可能「在清单里一个说明、在分组里另一个」。

`group_tool_policies()` 遇到未登记的分组**直接抛 `ValueError`**，不静默丢进兜底栏：
那只会在「加了分组字段却忘了登记分组表」时发生，静默兜底会让新工具悄悄消失。

**「唯一来源」与「唯一读出点」是两件事**（口径校正）：唯一来源是
`core/tool_registry.py`，读出点有**两个**且**不等价** ——
`/api/tools` 匿名可读、只给工具清单 + 分组 + 数据库表信息；
`/api/scan-center` 需登录、除工具清单外还带 `restricted_tools` / `projects` /
`strategies` / `paces` / `limits`。方案第 9 节写「前端动态读取 `GET /api/tools`」，
实现走 `/api/scan-center`，理由就写在 `api/tools.py` 的模块 docstring 里。

**方案第 9 节的字段名与本仓的逐字段对照**（照方案字面读会拿到错值而非缺失）：

| 方案第 9 节 | 本仓 | 陷阱 |
|---|---|---|
| `name` | `tool_name`（`/api/tools` 另给 `name` 别名，两者恒等） | `/api/scan-center` 的 `tools[]` **没有** `name`，取它会 `undefined` |
| `category` | **`tool_group`**（能力分组） | 本仓的 `category` 是**观测类别**（`subdomain`/`url`/…）—— 键存在、不报错、**值是错的**；`test_public_scan_mode.py` 的 `entry["category"] != entry["tool_group"]` 只锁住「两者不同」 |
| `risk` | `risk_level` + `risk_label` | 拆成「机器值 + 中文展示值」，语义没丢 |

还有一处**未加守卫的漂移**（已知、未收口）：`/api/tools` 的 `groups` 用
`group_tool_policies(list_tool_policies())`，因此 `vuln` 组恒为空、不含 `nuclei`；
`/api/scan-center` 把 `nuclei` 放进 `restricted_tools` 单独下发。两个接口的
**扁平清单**逐字段比对过，**`groups` 集合没有** —— 见 §9.28.7。

#### 9.25.5 前端：分组栏位名一个都不许写死

`web/static/scan_center.js` 的 `renderToolList(tools, restrictedTools, strategy, groups)`
按服务端 `tool_groups` 渲染：栏位名、每栏说明、每个工具的用途说明全部来自响应。

- 新增 `renderToolRow()`：条目形态只有一份，不会「分组里长一个样、扁平列表里长另一个样」；
- 未接入的 `nuclei` 按**它自己声明的 `tool_group`** 归进「漏洞检测」栏
  （`restricted.filter(tool => tool.tool_group === group.key)`），
  前端因此不需要写死「nuclei 属于漏洞检测」这类映射；
- `groups` 缺失时**退回扁平清单**，行为与引入分组之前一致（旧响应不会白屏）；
- 源码守卫：`test_scan_center_js_never_hardcodes_tool_names`（工具名零字面量）、
  `test_scan_center_js_renders_groups_from_server_metadata`（分组名零字面量）。

#### 9.25.6 本节的已知边界

- **`/api/tools` 仍匿名可读**（`docs/DECISIONS.md` D 有意保持），本轮只是给它加了字段，
  没有动鉴权。新加的 `groups` 刻意**只含已接入 runner 的工具** ——
  匿名接口没必要把「还差哪些工具」一并公开。
- **`/api/tools` 仍会为每个工具 `build_runner()` 实例化**（BUG 索引第 21 条），
  本轮没改：那是「单个 adapter 坏掉就整体 500」的同一根因，属另一件事。
- **扫描模式（方案第 10 节：信息收集 / 基础检测 / 深度测试）未引入**：
  当前只有「模板 + 节奏」两维，加第三维需要先与公网白名单口径对齐（留 Phase 3）。
- **Agent 边界未动**（方案第 12 节）：`agent/action.py` 仍直接调 `tool_runner.run_tools`，
  没有走 Job Service。本轮的参数标准化**没有**放宽它的能力（工具名仍受 registry 校验），
  但「Agent 不拥有最终执行权」这条目前只对 Web 入口成立 —— 见 P0-6 阶段二。

### 9.26 下一阶段规划方案 Phase 3：公网授权测试完善（操作者 / 授权备注 / 策略 / 限速 / 超时）

依据：同一份工作单第 14 节 Phase 3（`6GetEverything-下一阶段规划方案.md:428-436`）。
五项 = **操作者记录 / 授权备注 / 扫描策略 / 限速配置 / 超时配置**。

#### 9.26.1 一句话：五项全部走「事件 detail」，**零 DDL**

这是本节最该先记住的一条。五个字段**都没有**加成 `jobs` 表的列：

| 字段 | 落点 | 读回函数 |
|---|---|---|
| `operator` | `job.created` detail + 审计 detail + 结构化日志 | `core/jobs.py:operator_of_job()` |
| `project_id` | 同上（仅公网入口） | `project_id_of_job()` |
| `strategy` | 同上（仅公网入口） | `strategy_of_job()` |
| `authorization_note` / `authorization_confirmed` | 同上（仅公网入口） | `authorization_of_job()` |
| `rate_limit` / `timeout_seconds` | 同上 | `limits_of_job()` → `core.job_limits` |

理由与 §9.23.3 的 `pace` **完全相同**（读那一节）：加列属于 DB 结构变更
（`DECISIONS §1 E` 限纯增量，§2 把「需要改动数据库结构」挡回预授权流程），
而事件 detail 这条路已经被 `pace` 证明可行。差别只有一处：`pace` 是**一个**
读函数，这里是**一族**，因此抽出了 `created_detail_of_job()` 作为唯一取数点 ——
「某个键读不到时怎么办」只在那一个函数里回答一次。

**反向守卫**：`test_phase3_context_does_not_add_columns_to_jobs` 直接读
`PRAGMA table_info(jobs)`，断言这六个列名**不存在**。哪天有人图省事加成列，
这条会红，而那时必须先去走 §1 的预授权流程。

> 拼 detail 的地方只有一处：`core/jobs.py:_created_detail()`，并且在
> `hit_id is None` 分支内 —— 幂等命中时**不会**写第二条 `job.created`
> （`test_reused_creation_writes_no_extra_created_event` 仍锁着这一点）。
> 调用链上任何一层自行往 detail 里塞键都会造成「同一份事实两种形状」。

#### 9.26.2 `authorization_confirmed` 刻意**不是**闸门（本节的中心判断）

页面上那个「我确认该目标属于授权范围」复选框，服务端**如实记录、不参与判定**。
理由一句话：**一个可被脚本置真的 JSON 布尔值不构成安全边界。**

把它当闸门会制造一种更糟的状态 ——「勾了就等于放行」的错觉。授权本来就由
三道各自独立的闸门判定（Scope 命中 / `Scope.active_scan` / `GEF_ALLOW_REAL_SCAN`），
再叠一个自述布尔值只会让人以为「安全是靠这个勾选框保证的」。

用例把两种取值都跑通：`test_authorization_confirmation_is_recorded_but_is_not_a_gate`
断言 `false` / `true` / 不给（→ `null`）**都能创建任务**。
页面上也明写「**不是安全边界**」，与 `scan_center.html` 的文案同源。

> 这属于**需要用户确认的语义选择**，已按无人值守规则登记在
> `docs/DECISIONS.md` §3.8 第 2 条：要把它改成闸门是一次**新增**判定，需明确授权。

#### 9.26.3 `core/job_limits.py`：只能收紧，且报错必须指到具体字段

| 机制 | 位置 | 关键不变量 |
|---|---|---|
| 严格解析 | `normalize_rate_limit()` / `normalize_timeout_seconds()` | `None` / 空串 = 不指定；其余非法 → `ValueError`；**`bool` 必须拒**（`True` 是 `int` 子类，放进来就是 1 请求/秒） |
| 宽松读回 | `coerce_*()` / `limits_of_detail()` | 读库里的历史脏值退化成「没指定」，而不是让 worker 停摆 |
| 上下界 | `RATE_LIMIT_MAX = 100`、`timeout_seconds_max()` **每次调用**读 `SCAN_LIMITS["process_timeout"]` | 上界必须低于工具自身默认速率（subfinder 默认 150），否则「只能收紧」是空话；超时上界刻意不固化，否则 `GEF_PROCESS_TIMEOUT` 那条路径失效 |
| 合并 | `apply_to_runner(runner, limits)` 用 **`min`** | 低频档已写下的 `httpx -rl 10` 不会被请求里的 `rate_limit=50` 顶回去 |
| 形状 | `to_dict()`（恒两键）vs `to_detail()`（只写实际指定的） | 前者是接口出参（前端按「键总在」取值），后者是历史事实（没指定就不写 `null`） |
| 注入点 | `jobs/executor.py:_execute_real_step()`，在 `pace` **之后** | 顺序不影响结果（两者都是「更保守者胜」），但读起来与页面呈现顺序一致 |

**一个实测出来的缺陷（值得单独记）**：`_as_int()` 原先的报错文案是裸的
「必须是整数」，于是 `core/application.py` 只能用 `in` 猜字段 ——
实测后果是 `timeout_seconds="abc"` 被报成 `details.field = "rate_limit"`，
使用者盯着一个自己没填过的框找错。现在 `_as_int(value, field)` 把字段名写进文案，
`core.application` 按文案定位；这条不变量有参数化用例锁着
（`test_format_errors_name_the_offending_field`）。

#### 9.26.4 前端：限速输入框由服务端元数据**生成**

`GET /api/scan-center` 下发 `limits` = `describe_limits()` 的输出
（每项含 `field` / `min` / `max` / `label` / `hint`）。
`web/static/scan_center.js:renderLimits()` 遍历**服务端给的键**动态生成输入框：

- 字段名取自 `spec.field`（写进 `data-limit-field`），提交时按它拼请求键；
- 源码守卫禁止 `"rate_limit"` / `"timeout_seconds"` 以字符串字面量出现在 JS **代码**里
  （注释可以 —— 那是设计说明）。与工具清单、分组、节奏同一口径；
- **留空 = 不加这个键**，而不是传 `0` 或 `null`：服务端把「没指定」与「指定了非法值」
  分得很开，传 `0` 会被判越界 —— 而用户什么都没填；
- 元数据缺席时**不渲染**输入框：宁可不给这个能力，也不给一个范围写错的框。

`web/static/app.js:renderDetail()` 新增四行元数据（操作者 / 扫描策略 / 授权确认 /
本次收紧）与一行「授权依据」。它们**刻意不进 `jobSignature()`** ——
这些值不随执行变化，进了只会让每 3 秒一轮的轮询无谓重建 DOM、把用户展开的
原始证据冲掉（`jobSignature` 的存在理由见 §9.24.5）。

#### 9.26.5 与既有守卫的相容性（改之前必须知道）

Phase 3 给两个既有响应体加字段，踩点集中在**形状断言**上：

| 断言 | 为什么不能破 | 处理 |
|---|---|---|
| `test_application_service.py:117` `set(api_body) == set(submission.to_dict())` | `JobSubmission.to_dict()` 与 `POST /api/jobs` 必须同形状 | `to_dict()` **一行没改**（仍是 9 键）。新增上下文只进 `AuthorizedJobSubmission.to_dict()`（公网入口那条链） |
| `test_public_scan_mode.py:637` `"project_id" not in resp.get_json()` | 老入口的响应形状是既有契约 | 同上；`POST /api/jobs` 仍能收 `operator` / `rate_limit` / `timeout_seconds`，但**不回显**它们 |
| `test_normal_pace_leaves_the_runner_config_untouched` | 缺省路径不得碰 `runner.config` | `JobLimits.is_empty` → `apply_to_runner` 在碰 config **之前**返回 `False` |
| `test_observability_chain.py` 的日志反向守卫 | 结构化日志不得出现目标清单 / 长文本 | 只把 `operator=`（一个标识字符串）加进日志；授权说明与限速值**不进**日志（前者可能是敏感凭据描述，后者在审计表里可查） |

#### 9.26.6 本节的已知边界

- **`operator` 是「自称」，不是已验证身份**：本仓库认证是一个布尔态的本地管理员
  Token（`session[SESSION_KEY] = True`），`core/audit.py:record()` 的 `actor` 仍硬编码
  `local-admin`。真正的多用户身份属方案第 15 节「多租户 / SSO」暂缓项，
  本轮**没有**偷偷做一半。这一点也登记在 `docs/DECISIONS.md` §3.8 第 3 条。
- **`RATE_LIMIT_MAX = 100` 是一个判断，不是推导**：取它是因为上界要低于工具的默认
  速率（否则放松），而低频档实际只用 3 / 10。登记在 §3.8 第 1 条等用户确认。
- **外发速率仍未被计量**：测到的是「命令行参数正确」（`-rl` / `process_timeout` 落进
  config）与「合并方向正确」（`min`），真实工具对参数的解释由工具自身负责 ——
  与 §9.23.7 同一条边界。
- **`timeout_seconds` 的上界随 `GEF_PROCESS_TIMEOUT` 变**：页面上显示的上界因此是活的。
  这是刻意的（见 §9.26.3），但意味着「界面写 120、后端只收 30」这类漂移**不会**发生，
  而「换台机器上界变了」会。
- **方案第 10 节的扫描模式（信息收集 / 基础检测 / 深度测试）仍未引入**：
  当前 Profile 仍只有「模板 + 节奏」两维，限速/超时是**数值维度**而非第三档模式。
- **`nuclei` 仍未接入**（`internet_allowed=False`），公网白名单**仍是
  `subfinder` + `httpx`**。方案第 8 节的工具表把 `nuclei` 列在「漏洞检测」栏，
  那是示意；本轮没有因 Phase 3 放开任何一条。登记在 §3.8 第 5 条。

#### 9.26.7 本轮实测（口径快照）

```text
用例总数          1290 collected / 1288 passed / 2 skipped / 0 failures
mypy              72 source files（新增 core/job_limits.py，71 → 72）
node --check      web/static/scan_center.js + app.js 均通过
app.url_map       48 规则 / 50 方法绑定 / 42 个 /api/*
被用例命中        49 / 50 —— 唯一没被走到的是 GET /api/tool/<tool_name>/results
```

路由覆盖用与 §3.1 / §6.4 / §9.2 **同一算法**的一次性探针重跑（包装
`flask.Flask.full_dispatch_request` 跑全量后与 `app.url_map` 求差）：

```text
declared: 50
hit:      49
== never hit ==
    GET /api/tool/<tool_name>/results
```

**结论未变**：本轮**没有新增路由**，因此「唯一没被任何用例走到的是
`GET /api/tool/<tool_name>/results`」这句在**第四轮之后依然成立**。

**+101 的构成（逐文件实测，见 `docs/TEST_REPORT.md` §10.1）**：
`tests/unit/test_job_limits.py`（新）59 + `test_jobs_store.py` 72 → 90（+18）
+ `test_public_scan_mode.py` 89 → 113（+24）。

> 差额是用 `git worktree add --detach <tmp> ce0ef22` 检出规划方案 Phase 2 后
> **两个工作树各跑一遍 `--collect-only -q` 求差**得到的，不是推算；
> 且**没有任何一条既有断言被放松**。

### 9.27 下一阶段规划方案第 6 节：目标自动匹配授权资产（隐藏 Scope，不删 Scope）

依据：同一份工作单第 6 节（`6GetEverything-下一阶段规划方案.md:241`）
与第 16 节①（`:466-468`）。提交 `9224bc3`。

#### 9.27.1 一句话：把「隐藏 Scope」做实，一步都没删 Scope

方案第 4 节原则 2 是「**Scope 隐藏实现化**」，第 6 节把它的动作写成
「系统后台：调用 `resolve_scope(target)`，自动判断」，第 16 节① 把链路写成
「输入目标 → **自动匹配 scope** → 选择工具 → 创建 job」。

Phase 1～3 已经做完四步流程、只读试算、工具选择中心，但**这一格是空的**：
试算出结论之后，没有任何代码把那份结论变成下拉框里的选中项，用户仍要自己再挑一次。
本轮补的就是它，落点单一：`web/static/scan_center.js:applyMatchedScope()`。

**为什么这不违反「禁止为了体验删除 Scope 校验」**：

| 维度 | 本轮有没有动 |
|---|---|
| 目标集合 | ❌ 一字未改（自动匹配**不**新增/改写任何目标） |
| Scope 模型（`core/scope.py`） | ❌ 一字未改 |
| Policy（`core/policy.py:validate_job_targets`） | ❌ 一字未改，真正的判定仍只在这里做一次 |
| 被选中的资产来自哪 | ✅ **用户自己已经建好的**、且**服务端**已判定覆盖目标的那一份 |

#### 9.27.2 四条口径（改 `applyMatchedScope` 之前必读）

| 口径 | 实现 | 理由 |
|---|---|---|
| **只认服务端结论** | 候选取试算响应的 `eligible_scope_ids`（`core/authorization.py:TargetCheck.eligible`，即 `verdict == allowed` 的集合） | 前端自己比对 `verdict` / `allowed_domains` / `active_scan` 就是**第二条授权判定** |
| **取交集，唯一才选** | 所有 `valid` 目标的候选求交集，`length === 1` 才自动选中 | 多目标落在多个资产上时随便挑一个，用户会在提交时撞服务端「项目→范围」校验（403），页面上却看不出原因 |
| **有歧义就不猜** | 交集为空/多于一个 → 返回 `ambiguous`，保持用户当前选择并如实说明 | 猜错比不猜更糟：用户会以为系统已经判好了 |
| **不覆盖用户的显式选择** | 当前 `<select>` 就是那个答案时返回 `kept`，不重写、不重建 | 自动匹配是省一步，不是把用户刚改的选择顶回去 |

自动选中时若归属项目不是当前项目，会先把 `#job-project` 切过去再 `syncJobScopes()`
重建范围下拉框 —— 因为服务端要求 `scope ∈ project.scope_ids`（`create_authorized_public_job`），
不切项目就会提交一个必然 400 的组合。

**顺序不变量**：`renderCheckResults()` 里必须**先** `applyMatchedScope(payload)`、
**后** `renderConsentSummary()`。反过来的话摘要里显示的还是上一个选中项
（有一条断言直接比对这两行的相对顺序）。

#### 9.27.3 顺带收敛掉三处「第二条授权判定」

这是本轮**实测发现**的，不是设计出来的：`scan_center.js` 里同一个判断写了三份，
判法还不一样 ——

| 位置 | 改前 | 改后 |
|---|---|---|
| `renderCheckResults()` | `item.verdict === "allowed"` | `isEligibleMatch(check, item)` |
| `renderConsentSummary()` | `item.status === "ready" \|\| item.status === "scope_inactive"` | 同上 |
| `refreshConsentScopeLine()` | 同上 | 同上 |

现在三处都走 `eligibleScopeIds(check)` / `isEligibleMatch(check, match)`，
判据只有服务端那一个 ID 集合。源码守卫把上述写法列入禁止清单
（`item.verdict ===`、`item.status === "ready"`、`scope.allowed_domains.indexOf`、
`scope.active_scan &&`）—— **注释里出现不算**，代码里出现就红。

#### 9.27.4 本节的已知边界（一条**没做到**的，明写在这里）

- ⛔ **方案第 16 节③「Agent 只能 `create_scan_job()`」当前不成立。**
  `agent/action.py` 仍直接调 `tool_runner.run_tools`（`:419` / `:437`）与
  `HttpxRunner().run_scan`（`:503` / `:507` / `:511`），**没有**走 Job Service；
  方案第 12 节那条「Agent → 创建 Job → 返回 job_id」目前只对 Web 入口成立。
  这与用户上一轮对 P0-6 阶段二「先不开工」的答复一致。
  **本轮没有写 `xfail`、也没有写「断言 Agent 确实绕过」的用例**去把缺口粉饰成预期 ——
  那会让下一个人以为「这是设计如此」。缺口登记在 `docs/DECISIONS.md` §3.9 第 1 条。
  要收口就是 P0-6 阶段二开工，影响面见 `docs/AGENT_ASYNC_IMPACT.md`（含 I-5：
  Agent 当前**绕过 `GEF_ALLOW_REAL_SCAN` 与 Scope** 的实测证据）。
- **仍无浏览器测试**：源码守卫 + 一次性 DOM 桩（第五次）。桩加载**真实的**
  `scan_center.js` 并喂服务端真实形状的响应，走真实渲染路径核对四件事
  （唯一命中→选中、歧义→不猜、命中排除→不选、目标一字未改）。
- **「唯一才选」是判断而非推导**：另一种做法（选第一个）会得到一个页面上看不出原因的
  403。登记在 `docs/DECISIONS.md` §3.9 第 2 条等确认。
- **零 DDL / 零新路由 / 零闸门放松**：`app.url_map` 仍 48 规则 / 50 绑定 / 42 个 `/api/*`；
  未动任何表结构；公网白名单仍是 `subfinder` + `httpx`。

#### 9.27.5 本轮实测（口径快照）

```text
用例总数          1293 collected / 1291 passed / 2 skipped / 0 failures
mypy              72 source files（本轮未新增源文件）
node --check      web/static/scan_center.js 通过（本轮改的就是它）
app.url_map       48 规则 / 50 方法绑定 / 42 个 /api/*
被用例命中        49 / 50 —— 唯一没被走到的是 GET /api/tool/<tool_name>/results
本轮 +3           test_public_scan_mode.py 113 → 116（未新增文件）
```

> 路由覆盖探针算法与 §3.1 / §6.4 / §9.2 / §9.26.7 **同一份**（包装
> `flask.Flask.full_dispatch_request` 跑全量后与 `app.url_map` 求差）：
> **「唯一没被任何用例走到的是 `GET /api/tool/<tool_name>/results`」这句
> 在第五轮之后依然成立。**

---

### 9.28 方案第 13 节「后端安全边界」缺口回填 + 注册表读出点漂移收口（实现零改动）

> 依据：`6GetEverything-下一阶段规划方案.md:390-399` 的四行表，与第 16 节②（`:470-475`）。
> **本轮不新增任何源文件、不改任何实现代码**，只补一个测试文件里的用例
> （外加 `api/tools.py` 一处 docstring 与 `docs/API.md` 的口径校正）。

#### 9.28.1 一句话：四行边界从「三行有用例」补成「四行都有入口级用例」

第 13 节把「前端可以开放工具选择」的前提写成四行必须保留的边界。§9.23～§9.27
三轮把 Phase 1～3 与第 6 节都落了地，但**两行实现是真的、却没有入口级用例**。
「没测试」不等于「没实现」—— 本轮先实测确认实现，再补上能证明它真的的用例；
每条用例写下的当次就通过，**实现一行未改**。

同轮另有一个**独立审计**（对工作单 Phase 1～3 逐条对照）发现了一处
「字段同名异义、会静默给错值」与一处「两个读出点的分组视图没有守卫」，
一并收口在 §9.28.4 / §9.28.7。

#### 9.28.2 逐行核对（哪两行当时是空的）

| 第 13 节边界 | 实现位置 | 补测前 | 补测后 |
|---|---|---|---|
| Scope 校验 `target ∈ scope` | `core/policy.py:validate_job_targets()`（经 `core/application.py` 调用） | ✅ `test_out_of_scope_target_is_403`（403 + `scope_violation` + 零 jobs） | 不变 |
| Real Mode 控制 | `core/safety.py:REAL_SCAN_ENV`（`GEF_ALLOW_REAL_SCAN`），缺省 `real` 不静默降级 | ✅ `test_real_mode_without_env_switch_is_403_and_does_not_fall_back_to_mock` | 不变 |
| **Job 审计六项** | `core/application.py:365-389` 的 `detail` + `audit.record` | ⚠️ 只钉住 `operator` / `targets` / `scope_id` / `created_at` | ✅ 新增用例 |
| **工具白名单（任意字符串）** | `core/tool_registry.py:assert_tools_internet_allowed()` 的 `unknown` 分支（`:465-468` / `:478-482`） | ⚠️ 只覆盖**已登记但被禁**的 `nmap` / `dirsearch` / `naabu` / `feroxbuster` / `katana` | ✅ 新增用例 |

#### 9.28.3 六项审计字段逐项可查，且两个来源必须一致

`test_job_audit_records_the_six_required_fields`（`tests/integration/test_public_scan_mode.py`）
按第 13 节的**原话**逐项查，映射关系写死在用例里：

```text
job_id  = audit_events.target_id
time    = audit_events.created_at
operator / target / tools / mode = audit_events.detail 的 operator / targets / tools / mode
  → 再断言 job.created 事件（core/jobs.py:_created_detail）与审计记录对 tools / mode 一致
```

刻意**不**写成「detail 里有哪些键」的白名单断言：那样每加一个 Phase 3 字段都要改测试，
反而会诱导后人把这条边界顺手删掉。只查第 13 节点名的那六项。

**变异验证**（本轮实测，证伪「恰好通过」）：把 `core/application.py` 的 detail 里
`"tools": selected_tools` 一行删掉 → 该用例 **FAILED**；加回 → **PASSED**；
`git status --short get_everything_framework/core/application.py` 无输出（实现零改动）。

**为什么这条有真实价值**：`tools` 与 `mode` 在 `jobs` 表里也各有一份，很容易被后人
当成「审计表里重复了」删掉。一旦删掉，事后就再也分不清「这次开的是哪些工具、
是真扫还是 mock 演练」—— 而那正是审计表存在的理由。

#### 9.28.4 「禁止任意字符串调用工具」的唯一直接证法

`test_unregistered_tool_name_is_rejected_by_the_registry` 用 `strategy="custom"` +
`tools=["definitely-not-a-tool"]` 打公网入口，断言：

```text
400 + error_code=bad_request + details.field=tools + details.unknown_tools=["definitely-not-a-tool"]
  → 且 jobs_store.list_jobs() == []（闸门在创建任务**之前**）
```

它与 `test_blocked_tool_cannot_be_submitted` 是两件事：那条验「**已登记但被禁**」，
这条验「**从未登记**」。后者才是「任意字符串」的字面场景 ——
`tests/unit/test_tool_registry.py:test_assert_rejects_unknown_tool` 证明了闸门函数本身，
但**只有入口级用例**能证明公网入口真的走到了那个闸门（而不是在别处被 `404` /
`KeyError` 之类的偶然路径挡住）。闸门顺序见 `core/application.py:569-573`：
`resolve_strategy_tools()` → `assert_tools_internet_allowed()`，两步都在
`create_scan_job()`（`:586`）**之前**。

#### 9.28.5 本轮**没有**新增的缺口

§9.27.4 的三条（Agent 边界不成立 / 无浏览器测试 / 「唯一才选」是判断而非推导）
**一条都没变**，本轮未触碰与之相关的任何文件。处置口径完全相同：
**不写 `xfail`、不写「断言 Agent 确实绕过」的用例**去把缺口粉饰成预期。

#### 9.28.6 本轮实测（口径快照）

```text
用例总数          1296 collected / 1294 passed / 2 skipped / 0 failures
mypy              72 source files（本轮未新增源文件）
node --check      web/static/scan_center.js 与 app.js 均通过（本轮未改前端）
app.url_map       48 规则 / 50 方法绑定 / 42 个 /api/*
被用例命中        49 / 50 —— 唯一没被走到的是 GET /api/tool/<tool_name>/results
本轮 +3           test_public_scan_mode.py 116 → 119（未新增文件）
零 DDL            未动任何表结构；Scope / Policy / 目标集合一字未改
```

> 路由覆盖探针算法与 §3.1 / §6.4 / §9.2 / §9.26.7 / §9.27.5 **同一份**：
> **「唯一没被任何用例走到的是 `GET /api/tool/<tool_name>/results`」这句
> 在第六轮之后依然成立。**

#### 9.28.7 独立审计发现的两处口径问题（已收口，均未改行为）

对工作单 Phase 1～3 的逐条对照审计（只读，未改文件）报了六条，其中四条是
「看法」、两条是**真实缺陷**，收口方式如下：

**① 方案第 9 节的 `category` 与本仓的 `category` 同名异义 —— 会静默给错值。**
方案的示例是 `{"name":"httpx","category":"service"}`，`category` 指**能力分组**；
本仓的分组字段叫 `tool_group`，而 `category` 是运行器自报的**观测类别**
（`core/tool_registry.py:173-186` 的 `ToolPolicy.to_dict()` **没有** `category` 键，
`api/tools.py` 才补上 `"category": getattr(runner, "category", "subdomain")`）。
按方案字面读 `entry["category"]` 会拿到 `"subdomain"` 而不是 `"service"`：
**键存在、不报错、值是错的**，比字段缺失更危险。
`test_tools_api_keeps_the_historical_name_key` 里的 `entry["category"] != entry["tool_group"]`
只锁住「两者不同」，锁不住「谁对应方案的 `category`」。
**收口方式**：不改行为（改键名会破坏历史契约），在
`api/tools.py` 模块 docstring 与 `docs/API.md` 里写出**逐字段对照表**
（`方案 name → tool_name`、`方案 category → tool_group`、`方案 risk → risk_level` + `risk_label`），
并在 §9.25.4 复述 —— 让按方案实现的人第一步就能看到映射，而不是踩进同名异义。

**② 两个读出点的 `groups` 视图此前没有守卫。**
`/api/tools` 与 `/api/scan-center` 各写一次 `group_tool_policies(list_tool_policies())`
（`api/tools.py:155` / `api/public_scan.py:227`），是**两个独立调用点**；
已有的比对用例只比**扁平清单**的 8 个字段，`groups` 集合没比。一旦有人把其中一处
改成默认值 `list_all_tool_policies()`，`vuln` 栏会一个接口空、另一个接口冒出 `nuclei`，
而扁平清单比对**不会红**（`nuclei` 本来就不在扁平清单里）。
**收口方式**：新增 `test_both_registry_readouts_agree_on_the_groups_view`，
逐分组 `==` 比对，并断言两边的 `vuln` 栏都为空、`nuclei` 只从 `restricted_tools` 走。

**未收口的四条（判定为设计取舍或文档已说明，不改）**：
① 方案第 8 节五个分组 vs 本仓 6 个（多一栏「内容发现」，技术识别/漏洞检测**故意留空**，
理由在 `core/tool_registry.py:112-119`）；② 前端读 `/api/scan-center` 而非方案第 9 节写的
`/api/tools`（两者不等价，见 §9.25.4，已在 docstring / `docs/API.md` 写明）；
③ `risk` 拆成 `risk_level` + `risk_label`（语义没丢）；④ `name` 只在本接口有别名
（已写进 §9.25.4 的对照表）。

### 9.29 执行期双开关复检 + Phase 1 四处审计缺口收口（一轮跨三层）

> 这一节是**第一次真正跨层**（`core/` + `jobs/` + `web/`）的一轮，也是第一次
> **改动执行期闸门顺序**的一轮。改之前先读了三处代码与全套既有守卫，
> 逐条列在这里 —— 动 `jobs/executor.py` 的闸门之前必须读完 §9.29.1～§9.29.3。

#### 9.29.1 一句话：创建期的三道闸门此前只在创建期有效

方案第 13 节要求保留「Real Mode 控制」。创建期确实是三道（§9.22.3 的图）：

```text
core/application.py:327  validate_job_targets()      target ∈ scope
core/application.py:329  resolve_mode()              GEF_ALLOW_REAL_SCAN
core/application.py:332  scope.require_active_scan() Scope.active_scan
```

但 `jobs/executor.py:_execute_real_step` 此前**只复检了第 1 道**（`validate_step_target`），
既不 import `core/safety.py`，也不看 `active_scan`。所以存在这条缝：

```text
任务 A 以 real 模式入队（三道闸门全过）
  → 排队 / 失败重试 / worker 重启补做期间，运维把 GEF_ALLOW_REAL_SCAN 关掉
  → 或者把该 Scope 的 active_scan 收紧为 false
  → worker 取到任务 A，仍然把真实外网请求发出去（因为执行期没看这两件事）
```

§9.11.1 那句「`jobs/executor.py` 执行期**完全不查**」只对「Scope 也不查」的
P0-2 当时成立；到 §9.28 时它已经**查 Scope、不查开关** —— 两处描述都已按最新口径修正。

#### 9.29.2 复检的插入点与顺序（**顺序是有意的，别调**）

`jobs/executor.py:148-172`，位于 `validate_step_target()` 之后、
`if tool_name not in _known_tools()`（`:174`）之前：

```text
① validate_step_target(scope_id, target)        ← 原有，本轮未动
② real_scan_enabled()      False → scope_violation，Runner 不会被调用
③ require_scope(scope_id).require_active_scan() ← 同上
④ 工具是否已登记
```

顺序必须与创建期（`:327` 目标 → `:329` 开关 → `:332` `active_scan`）一致，理由两条：

| 顺序反了会怎样 | 为什么这是真问题 |
|---|---|
| 先查开关，Scope 已被删的任务会拿到「开关没开」 | 让人以为是**环境配置**问题，而真正的变化是**授权范围没了** —— 排查方向整个跑偏 |
| 越界 target 也会先被告知「开关状态」 | 越界目标连「有没有开开关」都不该被回答 |

既有用例 `test_real_step_rechecks_scope_before_calling_runner`（`tests/unit/test_jobs_executor.py:204`）
断言错误消息含「复检」，顺序换了它会先拿到开关的文案而变红 —— 这条**不是巧合**，
它是这个顺序的守卫。

#### 9.29.3 三条设计判断（改这里的代码之前必读）

1. **错误码用 `scope_violation`，不用 `permission_denied`。**
   与 `core/safety.py:59-63` 创建期的口径一致；前端 `app.js:52` 已有
   `scope_violation → 「目标超出授权范围」`，**前端零改动**。
   `permission_denied` 在 `modules/base.py:855-868` 已被退出码 126 占用，
   混用会让「越界」与「工具退出码」看起来是同一类问题。
2. **步骤级 `scope_violation` ≠ 任务级 `scope_violation`。**
   所有步骤都因复检失败时，`aggregate_status()` 给的是 `unknown_error`。
   这是既有聚合语义，本轮**没有**顺手改 —— 改了会影响所有既有任务的终态判定。
3. **不在这里复写匹配逻辑。** `require_scope()` 内部那次读只为拿 `active_scan`
   对象（`validate_step_target` 不返回 Scope 对象）；判定仍只有 `core/policy` 一套。

#### 9.29.4 Phase 1 审计缺口 ①②③⑤⑥⑦（前端与归一化，跨 `web/` + `core/scope.py`）

| # | 缺口 | 收口 |
|---|---|---|
| ① | 提交用上一次试算的快照：`lastTargets.length ? lastTargets : splitList(...)`，于是「检查授权 → 改输入框 → 创建任务」提交**改前**的目标 | `currentTargets()`（`scan_center.js:139`）成为唯一事实来源；提交 `:1302`、摘要 `:1042`、自动重算 `:1257` 三处都改读它；新增 `checkIsFresh()` `:152` / `invalidateCheckResult()` `:158` + 输入监听 `:1131` |
| ② | 没写协议的 URL 掉进 CIDR 分支，报「非法的 CIDR: www.example.test/a/b」 | `core/scope.py:68-87` 新增 `elif "/" in text:` 分支，看 `/` **两边**（`head` 是 IP 字面量？`suffix` 是纯数字？）再决定 |
| ③ | 资产详情「所属范围」直接渲染 `scope_9f3c…`，违反第 4 节原则 2 | `assets.js:72 scopeLabelById()` 读本页已渲染的下拉选项翻成名称；查不到给「（该授权资产已不在列表中）」，**不漏 ID** |
| ⑤ | `index.html` 的 `{% if scan_report %}` 块永远渲染不出来，且是全仓唯一一处把 `scope_id` 写进可见文案的地方 | 删模板分支 + `app.py` 的 `scan_report` 参数与实参 |
| ⑥⑦ | `#scope-list` 与 `.sc-scope-title` 无任何引用 | 删除 |

**缺口 ② 的判定表**（只看一边会静默吞掉用户的笔误）：

| 输入 | `head` 是 IP？ | `suffix` 纯数字？ | 结果 |
|---|---|---|---|
| `192.0.2.5/24` | 是 | 是 | 网段 → `192.0.2.0` |
| `192.0.2.0/99` | 是 | 是 | 报「非法的 CIDR」（掩码非法） |
| `example.test/24` | 否 | 是 | **仍按网段形状保留** → 报「非法的 CIDR」 |
| `www.example.test/a/b` | 否 | 否 | 取 `www.example.test` |

> 只看 `suffix.isdigit()` 会把 `example.test/24` 悄悄变成域名 —— 把用户的网段笔误
> 当成域名放行，比报错更糟（他会以为「网段写错了系统会告诉我」，其实不会）。

#### 9.29.5 本轮实测（口径快照）

```text
用例总数          1307 collected / 1305 passed / 2 skipped / 0 failures
ruff              All checks passed!
mypy              72 source files, no issues（本轮未新增源文件）
node --check      scan_center.js / assets.js / app.js 三个都通过（本轮改了前两个）
app.url_map       48 规则 / 50 方法绑定 / 42 个 /api/*
被用例命中        49 / 50 —— 唯一没被走到的是 GET /api/tool/<tool_name>/results
零 DDL            未动任何表结构；core/policy.py 一行未改
```

> 路由覆盖探针与 §3.1 / §6.4 / §9.2 / §9.26.7 / §9.27.5 / §9.28.6 **同一份**
> （包装 `flask.Flask.full_dispatch_request` 跑全量用例再与 `url_map` 求差）。
> 「唯一没被任何用例走到的是 `GET /api/tool/<tool_name>/results`」这句
> 在第七轮之后依然成立。

**跨进程补验**（本轮补跑，一次性脚本不入库）：§9.29.1 那条缝的现场是
**创建期与执行期分属两个进程**（web 落库 → worker 执行），而
`tests/unit/test_jobs_executor.py` 的两条新用例是**同进程**调 `execute_job()`。
因此另起了一次真实两进程验证：`python app.py` 建 real 任务 → `UPDATE scopes SET active_scan=0`
→ 子进程 `python -m jobs.worker --once`，得到 `scope_violation`
「执行前 Scope 复检失败」；再把子进程的 `GEF_ALLOW_REAL_SCAN=false` 跑一遍，
得到「执行前真实扫描开关复检失败」并点名该变量。目标用 RFC 6761 的
`www.example.test`，真实外部流量 0。详细记录（含「任务级 `unknown_error`
而步骤级 `scope_violation`」这条跨进程才看得见的观感）在 `docs/TEST_REPORT.md` §13.5。

#### 9.29.6 本节**没有**收口的一条（老入口不装公网白名单）

实测：老入口 `POST /api/jobs` 用 `tools=["nmap"]` + `mode="real"`（开关开 +
`active_scan=True`）返回 **202** 并落库，而同样的参数打 `/api/public-jobs` 是
**400 + `blocked_tools`**。原因是 `assert_tools_internet_allowed()` 在全仓
**只有一个生产调用点**：`core/application.py:573`（公网编排 `create_authorized_public_job`）。

**本轮刻意没改**：这是「老入口要不要也变成公网入口」的产品口径问题，
加白名单会改变既有 API 可用行为（`test_legacy_job_api_still_works` 契约要跟着动），
属破坏性变更。三种可选口径已登记在 `docs/DECISIONS.md` §3.11.5 第 1 条等你拍板。

#### 9.29.7 同轮审计判定为「设计取舍」的五条（逐条实测过，不改）

与 §9.28.7 同一口径：把「方案字面写法」与「实现具体做法」之间的差**逐条写下来**，
附上实测依据，避免下一个人把它们当成待办重新推一遍。完整表在
`docs/DECISIONS.md` §3.11.6，这里只记与本层级（`web/`）相关的三条：

| 意见 | 实测 | 判定 |
|---|---|---|
| 第 5.2 节「合并两级选择」，两个下拉还在 | `#job-scope` 的选项由 `#job-project` 联动过滤（`scan_center.js:774`），未选项目前选不到任何范围 —— 已是一级 | 已达成方案意图 |
| 第 5.1 节四个旧步骤名从未字面存在 | `548d196^` 实测标题是「输入目标 / 确认授权范围 / 选择工具 / 执行模式与提交」，第 5.1 节是用户视角描述 | 描述性对照，非缺陷 |
| `#job-consent` 在 `#scope-form` 内 | 实测 `scan_center.html:112` / `:127` / `:179`；JS 一律按 id 读，跨表单无副作用 | 无害，刻意不动 |

另两条（第 8 节「用户无法主动选择工具」这个前提当时已不成立、方案里
`resolve_scope(target)` 这个函数名不存在）见 `docs/DECISIONS.md` §3.11.6。

### 9.30 Phase 1～3 的**第二轮**只读对照审计：四处守卫/口径缺口收口（一轮跨 `api/` + `web/`）

> §9.29 那轮之后又做了一次对 Phase 1 / 2 / 3 的只读对照审计（**三条独立子代理视角**，
> 各自未改文件）。三条里报出来的问题分两类：**守卫强度不足**（看着在守、实际漏守）
> 与**口径不一致**（同一个请求体从两条链进来得到两种解释）。本节记录本轮收口的部分，
> 以及判定为「如实登记、不改行为」的部分。

#### 9.30.1 `/api/jobs` 与 `/api/public-jobs` 的 tools/tool 折叠（**已修**）

`api/jobs.py:106` 与 `api/public_scan.py:96` 此前都写：

```python
tools=payload.get("tools") or payload.get("tool")
```

`or` 把「**明确给了空选择**」与「没给这个键」当成同一件事。后果（一次性探针实测，
读库不读响应体）：

| 请求体 | `POST /api/run` | `POST /api/jobs`（改前） | `POST /api/jobs`（改后） |
|---|---|---|---|
| `{"tools": []}` | 400 | 400（`or` → `None` → 下游空 → 400，**巧合**一致） | 400 |
| `{"tools": [], "tool": "subfinder"}` | **400** | **202，落库 `tools=['subfinder']`、`total_steps=1`** | **400** |
| `{"tools": "", "tool": "subfinder"}` | **400** | **202，落库 `tools=['subfinder']`** | **400** |
| `{"tools": "  ,  ", "tool": "subfinder"}` | 400 | 400（**巧合**一致：该串非空，`or` 不折叠） | 400 |
| `{"tool": "subfinder"}` | 200 | 202（别名正常路径，**不能**一起删） | **202**（未变） |

**「改前」那一列不是推理出来的**：`git worktree add --detach <tmp> c2a83b1` 检出修复前
的提交，在**同一份探针脚本**上跑出 `202 / 202 / 400` 的行（并确认库里真的多出
1 条与 2 条 `queued` 任务），改后同一脚本给 `400 / 400 / 400`。两次都是
`GEF_ALLOW_REAL_SCAN=false` + mock，未发任何外部流量。

这与 §9.25.3 记的老毛病**同因不同向**：那一次是「空选择被折叠成 `None` 后回落配置默认值」，
这一次是「空选择被折叠成 `None` 后让**别名**顶上来」。两者都是「用值的真假代替键的存在」。
修法与 `api/scan.py:168-171` 逐字一致：

```python
tools=payload.get("tools") if "tools" in payload else payload.get("tool")
```

**判据是「有没有给这个键」，不是「这个键的值真不真」。** 别名本身保留
（`test_jobs_still_accepts_the_single_tool_alias` 守着别把它一起删了）。

#### 9.30.2 前端「工具名不写死」的守卫只覆盖 7/18（**已修**，守卫强度问题）

`test_scan_center_js_never_hardcodes_tool_names` 里的字面量黑名单此前是**手写的 7 个**
（`subfinder` / `httpx` / `nmap` / `naabu` / `nuclei` / `katana` / `feroxbuster`）。
探针复刻该守卫逻辑后往 `scan_center.js` 注入 `var HARDCODED = "dnsx";` → **守卫放行**；
`amass` / `gospider` / `waybackurls` / `dirsearch` / `alterx` / `assetfinder` / `enscan` /
`oneforall` / `shuffledns` / `amass_intel` 同样全部漏过 —— 17 个 runner 里 **11 个不在
黑名单上**。这不是「前端写死了」的实现缺陷，而是**守卫形同虚设**：它看起来在守方案第 9 节，
实际只守住三分之一，以后有人写死 `dnsx` 不会有任何红灯。

收口：黑名单改为**从注册表派生**，并加一条「读出点数不得少于 18」的自检（防止派生源
自身坏掉让守卫静默变成空循环）：

```python
registry_tools = sorted(set(get_supported_runners()) | set(KNOWN_UNAVAILABLE_TOOLS))
assert len(registry_tools) >= 18, f"注册表读出点异常，守卫会形同虚设: {registry_tools}"
for name in registry_tools:
    assert f'"{name}"' not in code_only, ...
```

**变异验证**：往 `scan_center.js` 插一行 `var MUTATION_PROBE = "dnsx";` → 该用例
**FAILED**；删掉还原 → **PASSED**；工作树无残留变异。注册表以后加一个工具，
这条守卫自动覆盖它，不再需要有人记得手写进黑名单。

#### 9.30.3 首屏兜底文案是后端描述的**逐字副本**（**已修**）

`web/templates/scan_center.html:164` 的 `#strategy-note` 初始文本此前逐字抄了
`core/tool_registry.py:557` 里 `asset_discovery` 那一档的 `description`。它会在
`scan_center.js:466` 拉到元数据后被覆盖，所以肉眼几乎看不见 —— 但后端一改描述，
这段 HTML 就**静默过期**，而当时**没有任何守卫**盯着它（节奏说明有
`test_scan_center_js_does_not_hardcode_pace_wording`，策略说明没有）。

收口：HTML 里只留中性占位（「正在加载策略说明…」），并新增守卫
`test_scan_center_page_does_not_copy_any_strategy_description` —— 判据不是
「有没有这句话」，而是**服务端当前下发的每一段 `description` 逐字都不在页面里**：

```python
for strategy in list_strategies():
    assert strategy.description not in body
```

后端改描述，这条仍然成立；谁再抄一份，它立刻红。**是「唯一来源」的可执行版本**。

#### 9.30.4 「全仓唯一一份参数规范化实现」是过头话（**已改措辞**，非行为改动）

`tool_runner.normalize_tool_names()` 的 docstring 与 §9.25.3 都写着「全仓唯一一份」。
实测打脸：`core/application.py:95 split_str_list()` 是**另一份**，服务对象是请求字段，
且两者不是同一个函数（`split_str_list is normalize_tool_names == False`）：

| | `normalize_tool_names` | `split_str_list` |
|---|---|---|
| 去重 | 去重保序 | **不去重** |
| 非法类型 | `ValueError` | `BadRequestError` |
| 位置 | `tool_runner.py:60` | `core/application.py:95` |

端到端行为一致的原因**不是**「只有一份实现」，而是**去重与 registry 校验只有一个收口点**
（`load_tools`，`tool_runner.py:107`）。也就是说：这个不变量是真的，但它靠的是收口点唯一，
不是实现唯一。改一处时必须记得另一处 —— 按原文理解会以为改 `normalize_tool_names` 就够了。

#### 9.30.5 `rate_limit` 与 `timeout_seconds` 的**生效面差得很远**（如实记录，未改）

Phase 3 把这两个字段都写进 `Runner.config`（`core/job_limits.py:apply_to_runner`），
但**真的把它变成命令行参数**的 runner 数完全不同（grep + 逐类内省双口径实测）：

| 字段 | 读取点 | 覆盖率 |
|---|---|---|
| `process_timeout` | `modules/base.py:425 _timeout_seconds()`（唯一读取点） | **17 / 17** |
| `rate_limit` | `modules/subfinder.py:64`、`modules/httpx.py:212` | **2 / 17** |

关键事实：**公网白名单恰好就是这两个**（`internet_allowed_tools() == ['httpx','subfinder']`），
所以公网授权测试这条链上 `rate_limit` 是 **2/2 全覆盖**。但白名单外的 15 个 runner
拿到 `rate_limit` 后是**静默 no-op**：`apply_to_runner(rate_limit=5)` 返回 `True` 且
`config` 里确实写进 `5`，而 `build_command()` 里没有 `-rl`。老入口 `POST /api/jobs`
的 real 模式可以走到这些工具（§9.29.6），因此「给 nmap 设了限速」目前只改了记录、
没有改命令。本轮**未改覆盖面**（给 15 个 runner 各加限速参数是独立工作，部分工具
根本没有对应开关），只在 `docs/API.md` §6.3 与本节如实写明。

#### 9.30.6 本节改动**不涉及**的地方

`agent/`（一行未动，Agent 直调 Runner 的缺口仍在，见 §3.9 的「先不开工」）、
全部数据库表结构与数据（**零 DDL**）、`core/policy.py`、`core/scope.py`（本轮未改它）、
认证授权、审计字段集合、公网工具白名单（仍是 `subfinder` + `httpx`，`nuclei` 仍
`internet_allowed=false`）、路由总数（**48 规则 / 50 绑定 / 42 个 `/api/*`，未新增未删除**）。

#### 9.30.7 验证分两层：源码守卫 + **真起实例**（读本节前必须知道）

§9.30.2 与 §9.30.3 都是**前端**改动（前者：工具名守卫；后者：策略说明副本），
其中 §9.30.4 还包含资产详情不再上屏 UUID / 数据库列值那一处。前端改动意味着——
本项目**没有浏览器测试**（无 `package.json`，`tests/` 下无 `.js`）。那些用例是
**源码级守卫**：它们证明的是「`scan_center.js` / `assets.js` 这些文件里
存在/不存在这些字串」，**不能**证明「浏览器里真的这么跑」。
所以本轮除了跑用例，最后还用**独立临时库**真起了一次 waitress，
核对的**不是源码而是服务端发出的字节**：

| 核对项 | 命令/方法 | 结果 |
|---|---|---|
| 服务能起来 | `GET /health` | 200，`tools_summary` 17 / 17 |
| 验收七步 | `python scripts/verify_public_scan.py` | 201 / 201 / 201 / 403 / 400 / 400 / 202，退出码 0 |
| `tools`/`tool` 折叠（§9.30.1） | `POST /api/jobs` 三形态 | `{"tools":[]}` 400、`{"tools":[],"tool":"subfinder"}` **400**、`{"tool":"subfinder"}` 202 |
| 策略说明副本（§9.30.3） | `GET /scan-center` 正文 | 含中性占位，**不含**任何 `list_strategies()` 描述 |
| 资产页 UUID / 列值 | `GET /static/assets.js` 正文 | 不含 `asset.canonical_key`、不含 `textContent = asset.id` |
| 页面可渲染 | `GET /scan-center` / `GET /assets` | 均 200 |

隔离方式：`LOCAL_DB_PATH` / `GEF_OUTPUT_DIR` / `GEF_SCAN_DB_PATH` 全指向 `%TEMP%`，
`LOCAL_ADMIN_TOKEN` 用临时值（与 `.env` 无关）；目标只有 RFC 6761 的 `example.test`，
请求要么 `mock`、要么在闸门处被拒 → **全程无外部流量**。用完已停止、临时目录已删。
**仍然没做**：没点浏览器，DOM 上的表现仍未验证。详见 `docs/TEST_REPORT.md` §14.4、
`docs/DECISIONS.md` §3.12.7。

---

### 9.31 规划方案 §1～§18 逐节对照审计（只读）+ 第 6 节行号批量刷新（2026-10-03，无人值守）

依据：工作单 `6GetEverything-下一阶段规划方案.md`（仓库根，本机材料，不入库）。
本轮**不改执行链**，做的是「照方案逐节核对仓库」与「照仓库校正文档」。

#### 9.31.1 逐节对照表（§1～§18 共 40 条要求）

| 节 | 方案的要求（方案内的行号） | 现状 | 证据（亲读） | 刻意不做 / 漏了 |
|---|---|---|---|---|
| §1 | 当前阶段判断（陈述句，`:34-52`） | 不适用 | `core/jobs.py:488`、`core/policy.py:110`、`core/application.py:202` | — |
| §2.1① | Scope 不该是主要用户概念（`:74-88`） | 已实现 | `scan_center.js:31-42`（`scopeLabel` `:193` / `projectLabel` `:200` / `scopeStateLabel` `:183` / `paceLabelOf` `:253` 四个翻译函数）、`scan_center.html:118-119` 用「授权资产」 | — |
| §2.1② | 项目/范围/`scope_id` 概念重复（`:90-111`） | 已实现（合并为一步） | `scan_center.html:74`、`:112-124`；`docs/DECISIONS.md:895`（两下拉已联动过滤＝已是一级） | — |
| §3.1 | 一次创建流程 **30 秒以内**（`:123`） | **未实现，且此前未被登记** | 全仓「30 秒」只命中方案自身 `:123`；无计时用例、无埋点 | **漏记**，见 §9.31.2 |
| §3.1 | 四件事：输入目标 / 确认授权 / 选择工具 / 创建任务（`:125-130`） | 已实现 | `scan_center.html:54,74,162,182` | — |
| §4 原则 1 | 前端开放能力、后端控制风险（`:136-156`） | 已实现 | `scan_center.js:44-49`；闸门 `core/application.py:569-573` | — |
| §4 原则 2 | 隐藏 Scope（不显示 `scope_id`/UUID/DB 字段，`:158-191`） | 已实现 | `index.html:46-48`、`assets.js:242`、`scan_center.js:38-40`；守卫 `test_m2_page_scan.py:116`、`test_assets_api.py:366`、`test_public_scan_mode.py:876` | — |
| §5.1 | 四个「旧步骤」（`:199-206`） | 不适用（用户视角描述，从未字面存在） | `docs/DECISIONS.md:897`、§9.29 附近 | 刻意（描述性对照） |
| §5.2 | 新四步流程（`:208-224`） | 已实现 | `scan_center.html:54,74,162,182`；`scan_center.js:3-11`；守卫 `test_public_scan_mode.py:745-768`（含反向断言旧步骤名不得复活） | — |
| §6 | 目标输入框：域名 / IP / URL（`:228-239`） | 已实现 | `scan_center.html:56-58`；无协议 URL 归一 `core/scope.py:62-97` | — |
| §6 | 后台 `resolve_scope(target)` 自动判断（`:241-246`） | 部分实现（**同名函数不存在，能力齐备**） | 同名符号只有 `api/scan.py:28`（legacy）；等价能力 `core/authorization.py:246/292`（只读试算）＋ `scan_center.js:858 applyMatchedScope`；`docs/DECISIONS.md:896` | 刻意（方案写的是**意图**，不是函数名） |
| §6 | 情况 A 放行 / 情况 B 拒绝，**禁止删 Scope 校验**（`:243-251`） | 已实现 | `core/authorization.py:127/173`、`core/policy.py:110`；用例 `test_public_scan_mode.py:295,450` | — |
| §7 | 授权确认复选框（`:257-266`） | 已实现 | `scan_center.html:126-133`、`scan_center.js:1314-1325`、`core/application.py:579-583`（**刻意不是闸门**，文案里就写着） | — |
| §7 | 审计记录 `operator`/`target`/`timestamp`/`scope_id`（`:268-270`） | 已实现（**四者都不是表列**，全在事件 detail 或 `created_at`） | `core/application.py:366,369,372,389`；`core/audit.py:29,32,46`；`core/db.py:112`；用例 `test_public_scan_mode.py:1594-1618`、`:2112-2147` | 边界：`audit_events.actor` 仍硬编码 `local-admin`，operator 是「自称」（`docs/DECISIONS.md:564-569`，刻意） |
| §8 | 工具选择中心、用户可主动勾选（`:274-287`） | 部分实现（清单**常显**，但只有 `custom` 模板可勾） | `scan_center.js:511,520-523`、`scan_center.html:171-177` | 刻意（模板语义决定工具；`docs/DECISIONS.md:898`） |
| §8 | 五栏分类表（`:280-286`） | 部分实现（**6 栏**，空栏如实显示） | `core/tool_registry.py:120-127`；空栏文案 `scan_center.js:628-631`；理由 `core/tool_registry.py:114-119` | 刻意（方案那张表示意） |
| §9 | 工具不写死在前端（`:292-297`） | 已实现 | `scan_center.js:559-563`；守卫 `test_public_scan_mode.py:1039`（黑名单**由注册表派生**，见 §9.30.2） | — |
| §9 | Tool Registry 统一管理（`:299-315`） | 已实现 | `core/tool_registry.py:200 TOOL_POLICIES`、`:147 ToolPolicy`、`:553 STRATEGIES`、`:120 TOOL_GROUPS` | — |
| §9 | 前端动态读 `GET /api/tools`（`:318`） | 部分实现（**读的是 `/api/scan-center`**） | `scan_center.js:331`；理由 `api/tools.py:18-27`；`docs/API.md:174` | 刻意（两个读出点**不等价**，见 §9.30.6） |
| §9 | 字段 `name`/`description`/`category`/`risk`（`:302-315`） | 部分实现（字段名**有意不同**） | `core/tool_registry.py:163-186`（`tool_group` 于 `:171`、`to_dict` 于 `:173-186`）、`api/tools.py:99-105`、`docs/API.md:188-194`（`category`→`tool_group`、`risk`→`risk_level`/`risk_label`） | 刻意（本仓 `category` 已是「观测类别」，同名异义会**静默给错值**；改名理由 `core/tool_registry.py:79-84`） |
| §10 | 三档扫描模式、默认信息收集（`:322-332`） | **未实现** | 代码无 `scan_mode`；登记 `docs/DECISIONS.md:92`、`docs/TEST_REPORT.md:1010,1171` | 刻意（语义重叠＋权限扩张，等你拍板） |
| §11⛔1 | 不扩大目标范围（`:346`） | 无此能力 | `api/scopes.py:77`（`scope_store.create` 的**唯一生产调用点**）；`docs/DECISIONS.md:627-630` | 刻意（守住） |
| §11⛔2 | 不自动发现未知资产（`:347`） | 无此能力 | grep `auto_expand`/`expand_scope`/`auto_discover` **零命中**；扫描结果只落 `assets`/`observations` | 刻意（守住） |
| §11⛔3 | 不 Agent 自主扫描（`:348`） | 无调度，但 **Agent 仍可直接执行** | `agent/` 无 `while True`/`schedule`/`thread`；`agent_cli.py:22` 是人手 REPL | 自主编排刻意不做；**执行权问题见 §12 行** |
| §11⛔4 | 不后台持续扫描公网（`:349`） | 无此能力 | 全仓 grep `APScheduler`/`crontab`/`schtasks` **零命中**；`jobs/worker.py:179,216`；`app.js:626,630,631` 仅**客户端状态轮询** | 刻意（守住） |
| §11✅ | 输入目标 → 选择工具 → 创建 Job（`:351-359`） | 已实现 | `scan_center.js:1294-1369`、`api/public_scan.py:46` | — |
| §12 | Agent → 创建 Job → 返回 `job_id`，**禁止直调工具**（`:364-386`） | **未实现** | `agent/action.py:16,419,437,498` 仍直调；`agent/` 内 grep `jobs_store`/`create_scan_job`/`JobSubmission` **零命中** | 刻意（`docs/DECISIONS.md:647-664`、`docs/AGENT_ASYNC_IMPACT.md:152-166`；你对 P0-6 阶段二答复「先不开工」） |
| §13 | Scope 校验 `target ∈ scope`（`:396`） | 已实现 | `core/policy.py:110 validate_job_targets()`；调用 `core/application.py:327`；用例 `test_public_scan_mode.py:450` | — |
| §13 | Real Mode 控制 `GEF_ALLOW_REAL_SCAN`（`:397`） | 已实现（**创建期＋执行期双检**） | `core/safety.py:25,38,43-64`；`jobs/executor.py:141,148-172`；用例 `test_jobs_executor.py:241,298,331` | — |
| §13 | Job 审计六项（`:398`） | 已实现 | `core/application.py:365-389`；用例 `test_public_scan_mode.py:2112-2147` | — |
| §13 | 工具白名单，禁任意字符串（`:399`） | 已实现（**老入口例外已登记**） | `core/tool_registry.py:445 assert_tools_internet_allowed()`，生产唯一调用点 `core/application.py:573`；用例 `test_public_scan_mode.py:2150` | 老入口 `POST /api/jobs` 不装白名单 —— **刻意未改**，三选一待拍板（`docs/DECISIONS.md:856-884`） |
| §14 P1 | 前端体验重构，不改 Policy/Scope/Job 模型（`:405-415`） | 已实现 | `548d196` 的 `--numstat` 9 个文件全在 `web/`、`app.py`、`tests/`，**未含** `core/policy.py`/`core/jobs.py`；用例 `test_m2_page_scan.py:93,116,131` | — |
| §14 P2 | 注册模型 / 列表 API / 前端动态 / Job `tools` 标准化（`:417-426`） | 已实现 | `core/tool_registry.py`；`api/tools.py:108`；`tool_runner.py:60,114`；`tests/unit/test_tool_parameters.py:151,183,216` | — |
| §14 P3 | 操作者 / 授权备注 / 策略 / 限速 / 超时（`:428-436`） | 已实现（**零 DDL**） | `core/jobs.py:337,366,388,435`；`core/job_limits.py:47,99,113`；反向守卫 `tests/unit/test_jobs_store.py:842` | 边界：`rate_limit` 只有 2/17 runner 真生效 —— 刻意未补（`docs/DECISIONS.md:93-115`） |
| §15 | 九项暂缓（React / 框架迁移 / Agent 自主 / 自动扩范围 / Scheduler / 多租户 / SSO / K8s / Redis·Celery，`:440-454`） | 已遵守 | 无 `package.json`；grep `redis`/`celery`/`kubernetes`/`apscheduler` 零命中；无 users/tenant 建表 | 刻意（守住） |
| §16① | 前端流程测试：输入目标→匹配 scope→选工具→建 job（`:462-468`） | 已实现 | `test_public_scan_mode.py:2048 test_target_to_job_flow_uses_the_auto_matched_scope` | — |
| §16② | 权限测试：未授权失败且 Job 不增；授权成功且 tools/scope 正确（`:470-475`） | 已实现 | `:450`＋`:458`（`assert jobs_store.list_jobs() == []`）；`:2048`＋`:2088-2093` | — |
| §16③ | 安全测试：Agent 不能直调 Runner、只能 `create_scan_job()`（`:477-482`） | **未实现（无该用例）** | 全仓无 agent↔job 用例；`tests/unit/test_agent_boundary.py`（17 条）只覆盖 P0-3/5/6 | 刻意（P0-6 先不开工；**刻意不写 `xfail` 粉饰** —— 见 §9.31.3） |
| §17 | 每阶段独立提交＋九字段格式（`:486-503`） | 部分实现（**格式漂移**） | 见 §9.31.3 | 半漏（格式，非能力） |
| §18 | 最终目标与核心原则（`:507-517`） | 不适用（目标陈述） | 原则落地见 §9.25 附近 | — |

**结论**：§1～§18 里**没有「漏做」的能力项**。所有未实现/部分实现项（§10、§12、§16③、
`rate_limit` 覆盖面、§9 的读出点与字段名、§13 老入口白名单）**都已登记为「刻意不做 /
待拍板」**。真正**未被登记**的只有下面这一条。

#### 9.31.2 唯一「既未实现、也未被任何文档登记」的一条：§3.1 的「30 秒以内」

方案 `:123` 写「一次创建流程 30 秒以内完成」。全仓：
**无计时埋点、无验收用例、无任何文档登记它没做**。

**它不是安全或功能缺口**（是产品体验指标），本轮**不改行为**，只如实登记，
并把选项写进 `docs/DECISIONS.md` §3.13.1 等你拍板：
**(a)** 登记为「不验收」（推荐）；**(b)** 补前端计时埋点；
**(c)** 换一个可客观断言的指标（如「点击/输入次数 ≤ N」）。

#### 9.31.3 §17 提交九字段的**格式漂移**（半漏）

**测量口径**（先说清，否则这张表没法复核）：对 `git rev-list origin/main..d603334`
的 **18 个提交**（审计当时的定格）逐个取 `git log -1 --format=%B`，
按**口径 ④** 判定：**解析每一行里「第一个冒号之前」的文本，其中出现了哪个字段名，
就算哪个命中**（字段名后允许跟括号说明）—— 这条口径的要点是**合并标题也算数**
（`未做事项 / 风险：` 一行里两个字段都命中）。

> **为什么先花大力气定口径**：本表初版用的是**行首锚定**口径，结果把 `1746f41`
> 判成 8/10，而它正文里明明写着 `未做事项 / 风险：` —— **口径本身制造了一个假阴性**，
> 我还差点把它当结论写进三份文档。四种口径的对照见下表。

| 结果 | 提交数 | 提交 |
|---|---|---|
| **10/10 齐全** | 8 | `548d196`、`ce0ef22`、`8e94662`、`890e600`、`3146fb4`、`fb2493e`、`d603334`、`1746f41` |
| **部分字段缺**（1～9 个） | 7 | `17dc1bd`、`652b26f`、`c2a83b1`、`0f5422d`、`1a53b4f`、`8e7b8ba`、`5417b4a` |
| **一个字段都没有** | 3 | `9224bc3`、`a646742`、`d057a18` |

> **这张表对「口径」极其敏感，先看这一条再引用数字。** 同一批 18 个提交，
> 换四种同样合理的读法，结果差一倍（全部实测）：
>
> | # | 口径 | 含义 | 齐全数 |
> |---|---|---|---|
> | ④ | 行内**第一个冒号之前**出现的字段名都算 | **合并标题也算**（`未做事项 / 风险：` 两个都算）← **本表采用** | **8/18** |
> | ① | `^\s*字段\s*(?:[（(]…[）)])?\s*[:：]` | 行首 + 允许字段名后带括号说明 | 7/18 |
> | ③ | `字段\s*(?:[（(]…[）)])?\s*[:：]`（不锚定） | **正文里提一句也算** | 7/18，但**具体是哪些提交不一样**（`8e94662` 掉到 9/10、`ce0ef22`/`548d196` 掉到 8/10） |
> | ② | `^\s*字段\s*[:：]` | 行首但**不允许**括号说明 | 4/18 |
>
> **为什么选 ④ 而不是看起来更简单的 ①**：① 会把 `1746f41` 判成 8/10，而那条提交的正文里
> **明明写着** `未做事项 / 风险：`（两个字段名写在同一行、用 `/` 分隔）—— ① 在
> `未做事项` 之后遇到的是 ` /` 而不是 `：`，于是**把两个都算成缺失**。这是**假阴性**，
> 而 ④ 正是为了修掉它。反过来 ③ 是**假阳性**（正文提一句就算有）。
>
> **所以「齐全率」不是一个客观数字，是「数字 + 口径 + 已知偏差方向」。**
> 引用本表时必须带上口径。**可复现**（口径 ④ 的完整逻辑，跑在仓库根）：

```python
import re, subprocess
F = ["里程碑","分支","提交","修改文件","行为变化",
     "新增测试","验证结果","未做事项","风险","下一步"]
def git(*a):
    return subprocess.run(["git",*a],capture_output=True,
                          encoding="utf-8",errors="replace").stdout
for sha in git("rev-list","origin/main..d603334").split():
    hit = set()
    for line in git("log","-1","--format=%B",sha).split("\n"):
        m = re.match(r"^\s*([^：:]*)[：:]", line)      # 行内第一个冒号之前
        if not m:
            continue
        pre = m.group(1)
        for f in F:
            # 字段名后允许括号说明；后一个分支让「未做事项 / 风险：」两个都算
            if re.search(rf"{f}\s*(?:[（(][^）)]*[）)])?\s*$", pre) \
               or re.search(rf"{f}(?![^\s/、,，])", pre):
                hit.add(f)
    print(sha[:7], f"{len(hit)}/10", f"缺 {sorted(set(F)-hit)}")
```

> 其余三个口径只需把判定那一行换成对应正则即可（口径 ①②③ 的正则就写在上面表里）。
> ★ **写这段代码本身就是本节的教训**：我第一版用行首正则跑完就写文档，
> 直到逐条打开 `git log` 才发现 `1746f41` 被误判 —— **「跑出数字」不等于「数字对」**。

**逐条实况**（按上表采用的**口径 ④**；`命中/10`）：

| 提交 | 命中 | 缺失的字段 |
|---|---|---|
| `d603334` | 10/10 | — |
| `fb2493e` | 10/10 | — |
| `3146fb4` | 10/10 | — |
| `890e600` | 10/10 | — |
| `8e94662` | 10/10 | — |
| `ce0ef22` | 10/10 | — |
| `548d196` | 10/10 | — |
| `1746f41` | 10/10 | —（★ **口径 ① 会把它误判成 8/10**：正文里写的是 `未做事项 / 风险：`，两个字段名在同一行用 `/` 分隔） |
| `652b26f` | 8/10 | 新增测试、下一步 |
| `17dc1bd` | 7/10 | 新增测试、风险、下一步 |
| `c2a83b1` | 5/10 | 新增测试、验证结果、未做事项、风险、下一步 |
| `0f5422d` | 5/10 | 同上五项 |
| `1a53b4f` | 5/10 | 同上五项 |
| `8e7b8ba` | 5/10 | 同上五项 |
| `5417b4a` | 3/10 | 里程碑、分支、提交、修改文件、行为变化、新增测试、下一步 |
| `9224bc3` | 0/10 | 全部十项（正文按「做了 / 没做」分段写，没套 §17 标题） |
| `a646742` | 0/10 | 全部十项 |
| `d057a18` | 0/10 | 全部十项 |

> **这张表暴露了「正则审计」的固有局限，必须写出来**：
> ① **合并标题会被行首正则误判**（`1746f41` 的 `未做事项 / 风险：`）—— **假阴性**；
> ② **正文里提一句字段名就会被算成「有」**（口径 ③）—— **假阳性**；
> ③ **字段名后带半句说明**（如 `验证结果（实测）：`）在口径 ② 下会被算成缺失。
> 所以本表**只能当线索，不能当判决**：它足以证明「格式漂移**确实存在且相当普遍**」
> ——这是本节要证明的命题——但**不足以逐个提交宣判「它不合格」**。
> **正确用法**：看整体比例趋势；要点名某个提交时，**打开那条 `git log` 自己读**。

> **两个「本轮收尾提交」不在上表**：`c0f02d4` 之后的 `3191a75`、`cca156d`、
> `b308a0b`、`3c9e5ce` 都是本轮补的、都 10/10。上表是**审计当时**看到的
> 18 个提交的定格；实时数字见下方自指小节的**时点表** ——
> 它每提交一次就动一次，所以这里**刻意不写死**。

**必须澄清我第一版的三个错，以及「澄清本身又错了一处」**（保留在此，因为它就是
「凭印象写文档」的样本 —— 而且**连纠错都是凭印象写的**）：

1. 第一版把 `548d196`/`ce0ef22`/`8e94662` 标成「✅ 齐全」**结果是对的**（实测 10/10），
   但我当时**没有实测**就写了；同表把 `890e600`/`8e7b8ba`/`1746f41` 一并写成
   「✅ 齐全」—— 实测 `8e7b8ba` 只有 **5/10**。**这是错的。**
   ▶ 但 `890e600`（10/10）与 `1746f41`（口径 ④ 下 10/10）**结果是对的** ——
   **写对了不等于知道**，这条错误的真正代价是我一度把 `1746f41` 从「齐全」改判成
   「8/10」，见第 2 条的连锁。
2. 第一版说 `9224bc3` 是「唯一一个没有头部字段的」—— 实测是 **3 个**
   （`9224bc3`、`a646742`、`d057a18`）。
   ▶ **注意这里：「纠正」时我写的是「实测有 4 个」，把 `5417b4a` 也算进去了 ——
   这是错的**：`5417b4a` 实测 **3/10**（它有「验证结果」「未做事项」「风险」等条目），
   不属于「一个字段都没有」。**即「纠错」这一步我也没实测，于是纠错本身也错了一处。**
3. 第一版把 `0f5422d`/`c2a83b1` 描述为「缺验证结果/风险/下一步」—— 实测缺的是
   **五项**（多缺「新增测试」与「未做事项」）。这条**纠正对了**。

★ 三处里错两处、对一处；而**唯一对的那一处（第 3 条）恰好是我真去数了字段的那个**。
这就是为什么本节末尾把「口径 + 命令」写死在文档里，而不是写死数字。

**结论**：§17 的九字段格式在当时的 `origin/main..d603334`（18 个提交）上、
**在口径 ④ 下真正齐全的只有 8 个**（换口径 ① 是 7 个、② 是 4 个）；
`9224bc3` 那批（第 6 节自动匹配前后）是漂移最集中的一段 —— **3 个提交连一个字段名都没有**。
**这不影响任何功能**，但「方案第 17 节要求每阶段按九字段写提交」这句在仓库里
**目前不成立**，如实记下。**最新值现场跑**（时点表见下方自指小节）。

> **本节也要自指，而且比分母更麻烦的是分子也会动**：
> 写下这段的每一次提交本身也是 `origin/main..HEAD` 里的一员，**它们也都是 10/10 齐全的**。
> 于是这个数字在本轮收尾里连着挪了六次（**下表每一格都是跑出来的，没有一格是推算的**）：
>
> | 时点 | 提交数 | 齐全 | 说明 |
> |---|---|---|---|
> | 审计定格 | 18 | 8 | `origin/main..d603334`，上面那张表就是这个时点 |
> | `c0f02d4` | 19 | 9 | 它 10/10 |
> | `3191a75` | 20 | 10 | 它 10/10 |
> | `cca156d` | 21 | 11 | 它 10/10 |
> | `b308a0b` | 22 | 12 | 它 10/10 |
> | `3c9e5ce` | 23 | 13 | 它 10/10（§3.13.7 的推送前审计记录） |
>
> ★ **所以「8/18」这个数只在它被写下的那一刻成立**，任何引用都必须**重新跑一遍**。
> 这不是文档不严谨，是**被测量的对象本身包含测量者**。
>
> ★ **本表在这里主动停住。** 写下这一段的是第 24 个提交，落地后自然会是 24/14 ——
> 但**那不是我跑出来的，是推算的**，所以**不写进表里**。
> **一旦开始「预测下一个数」，这份文档就退化成了它自己批评的那种凭印象写数字。**
> 表头的「跑到写作时的 HEAD 为止」就是这条规矩。
>
> **更值得记的是：这个数字还会被「提交粒度」影响。** 本轮收尾时我先把两个纯文档
> 改动拆成两个提交，其中「只改状态板」那个小到根本写不满九字段 → 齐全率变差；
> 把两个折回一个（`git reset --soft`，内容一字未变、`HEAD^{tree}` 实测相同）后，
> 齐全率立刻好转。**结论：§17 的「每阶段独立提交」与「九字段齐全」在
> 琐碎的文档提交上会互相干扰** —— 为了凑字段把无意义的东西拆开是本末倒置，
> 该合的就该合。**判断口径一律现场跑**（下面这两条命令就是口径本身）：
>
> ```powershell
> git rev-list --count origin/main..HEAD          # 分母：待推送提交数
> git log -1 --format=%B <sha>                    # 逐行解析「第一个冒号前的字段名」
> ```
>
>
> **更值得记的是：这个数字还会被「提交粒度」影响。** 本轮收尾时我先把两个纯文档
> 改动拆成两个提交，其中「只改状态板」那个小到根本写不满九字段 → 齐全率变差；
> 把两个折回一个（`git reset --soft`，内容一字未变、`HEAD^{tree}` 实测相同）后，
> 齐全率立刻好转。**结论：§17 的「每阶段独立提交」与「九字段齐全」在
> 琐碎的文档提交上会互相干扰** —— 为了凑字段把无意义的东西拆开是本末倒置，
> 该合的就该合。**判断口径一律现场跑**（下面这两条命令就是口径本身）：
>
> ```powershell
> git rev-list --count origin/main..HEAD          # 分母：待推送提交数
> git log -1 --format=%B <sha>                    # 逐行解析「第一个冒号前的字段名」
> ```
>
> **刻意不写死 SHA**：amend / 折回一次就失效 —— 那正是本节批评的
> 「凭印象写死数字」。**也刻意不写死「齐全数」**：它同时受口径与提交粒度影响，
> 至少 4 种口径 × 每次提交都会动。

**处置**：**历史提交不重写**（这些提交**还没推送**，重写会改掉已经在会话里出现过的
SHA，且属「大范围 history rewrite」方向）。选项见 `docs/DECISIONS.md` §3.13.1：
**(a)** 只对后续提交严格执行（推荐）；**(b)** 在上表基础上固化一条
`git log --format=%B` 的自检脚本，后续提交前跑一次。

> 这一条与 §16③ 的区别值得写清：**§16③ 是「能力没做、且写不出通过的用例」**，
> 本轮**刻意不补 `xfail`** —— 因为一个永远 xfail 的用例会伪装成「已覆盖」，
> 比没有用例更坏。**§17 是「格式没对齐」**，与能力无关，所以只用记账处理。

#### 9.31.4 第 6 节 29 条行号刷新：改了什么、怎么验的

第 6 节是 `AGENTS.md` 指定的**改 bug 第一入口**，而它已经失信：**22/29 条行号漂移，
9 处落进别的函数体内**。本轮把它整表按**当前 LF 行号**改写。

**核对方法（双向交叉验证，避免「按符号找行号」与「按行号读内容」各自出错）**：

1. 用 `open(path, encoding="utf-8", newline="").read().split("\n")` 取行（不用 `Get-Content` 默认编码 —— 它会给出**偏小的假行号**，实测偏差 16.6%）；
2. **按符号**（正则匹配 `def xxx` / 关键语句）定位行号；
3. **按行号读回**该行内容，确认就是那条引用说的东西；
4. 两处结论不一致时以「读回内容」为准并重新定位。

**三类改动**：

| 类 | 条数 | 处理 |
|---|---|---|
| **已漂移**（行号错、符号还在） | 22 | 改写为当前行号，并在括注里保留旧值以表明「改了什么」（例如 `**`:164`**，旧写的「第 130–154 行」已是 `load_tools` 内部） |
| **已不存在**（语句/常量全仓搜不到） | 3 | 第 7 条 `nfl.com` 默认值（现 `domains` 为**空 `[]`**）、第 16 条 `with self._get_connection()` 写法（现全部 `_connect()`）、第 21 条 `record_count`（全仓无匹配）→ 改写为现状 |
| **前提已过期** | 3 | 第 10 条「仓库里不存在 `web/` 目录」、第 13 条「`scripts/*.exe` 明明存在」、第 19 条「无候选时抛 `RuntimeError`」（现为 `RunnerInputError`，**不是同一个类**）→ `~~删除线~~ + ▶` 如实标注 |

**顺带校正的同类漂移**（同一份文档）：
§7.2 第 8/10 条（`http-x` **不是笔误**、`GO_BIN_*` 行号、`scripts/` 已无 `.exe`）、
§7.3 第 13～17 条（f-string 拼 SQL 的 5 个真实位置、`busy_timeout` **已补**、
连接泄漏**已修**、`category` 被忽略、`gather_export_rows` 双重收集）、
§8「最小调试入口速查」四行（**「只有 2 个测试文件」已过期 —— 现在是 40 个 `test_*.py`**；
「19 张表」实测 **20** 张；`/api/databases` 的「没有 `record_count` 与 docstring 不符」
改为「**这是设计如此**」）、§8 末尾第 2 条与 §7.7 第 31 条
（`git ls-files` 实测：**本仓库** `results/`/`uploads/`/`SecLists/` 都是 0 个跟踪文件 ——
**保留但加 ▶ 注明只对上游旧 clone 成立**，因为「不许 `git add -A`」的纪律仍适用）、
§9.9 的编号空间说明（**第 6 节与第 7.7 节都有 #23/#24，是完全不同的两件事**）。

#### 9.31.5 本轮的两处源码改动（**只有 docstring**）

| 文件:行 | 改了什么 | 为什么 |
|---|---|---|
| `storage.py:702-711` | `Args.category` 从「可选，按分类筛选（暂未在专属表查询中使用）」改为「**形参保留但当前不生效**」，并说明两条分支各自为什么不读它、要按分类过滤得用 `get_view_results(category=...)` | 原措辞会让人以为「传了只是暂时没用」，实际是**这条链上从未生效**；第 6 节第 4/16 条的根因定位点原本指错行号，现在直接写在代码里 |
| `api/tools.py:7-11` | 模块 docstring 的 `/api/databases` 从「（表名、记录数等）」改为「（工具名 / 表名 / 结果列 / 分类）+ **不含任何计数**」，并指明 `get_tool_database_overview()` **没有 API 出口** | 同一处脱节在 `98ea46f` 修过一次 docstring，但**模块头部又漏了**；这是「按 `/api/databases` 找记录数」的人第一个会读的地方 |

**判据**：两处都是注释，`import storage, api.tools` 正常、全量用例 1313 passed / 2 skipped、
`ruff`/`mypy` 全过 —— **零行为变化、零路由变化、零 DDL**。

#### 9.31.6 本轮**没做**的（如实列出）

- **没点浏览器**：第 6 节与 §9.30 的前端结论仍是**源码级**的（本项目没有 `package.json`、
  没有浏览器测试链）；DOM 上的表现仍未验证（这一条从 §9.30.7 延续而来，**不因本轮而改变**）。
- **没起真实例**：本轮只跑本地测试与只读核对；「真起实例」的结论属于**上一轮**（§9.30.7 /
  `docs/TEST_REPORT.md` §14.4），本轮**不重复声明**。
- **没重写历史提交**：见 §9.31.3。
- **没改 `rate_limit` 覆盖面、没做 §10 扫描模式、没改 §12 Agent 边界、没写 §16③ 的用例**：
  全部等你在 `docs/DECISIONS.md` §3 拍板。
- **没推送**：口径同前（先跑七项推送前安全审计，再显式 `git push origin main`，
  **不加 `--tags` / `--follow-tags`**）。

> 上一节 §9.30.7 末尾那句「仍然没做：没点浏览器」**在本轮依然成立**，
> 不重复声明；本节的「没做」清单只列本轮**新**未做的事项。

#### 9.31.7 顺带查证：「不入库」的七个未跟踪项里，只有一类真的被忽略

本轮补交 `Nightly Execution Report` 时（该文件按约定放 `docs/milestones/`），
顺手核了一遍「哪些未跟踪项是真的被 `.gitignore` 忽略、哪些只是靠纪律不提交」。
**权威判据是 `git status --ignored --porcelain`**：`??` = 未跟踪且**未**被忽略，
`!!` = 被忽略。实测：

| 未跟踪项 | `status --ignored` | 真被忽略？ | 说明 |
|---|---|---|---|
| `docs/milestones/` | `!!` | ✅ 是 | `.gitignore:67` 生效，`Nightly Execution Report` 放这里不会被误提交 |
| `1本机联调版实施方案_DSH.md` | `??` | ❌ **否** | `.gitignore:66` 写的是 `本机联调版实施方案_DSH.md`（**不带编号**），与实际文件名不匹配 |
| `2DSH_执行提示词.md` | `??` | ❌ **否** | `.gitignore:65` 写的是 `DSH_执行提示词.md`（**不带编号**），同样不匹配 |
| `3GetEverything_长期产品化总方案_Flask版.md` | `??` | ❌ 否 | 从来不在忽略规则里（`DECISIONS.md:144` 登记为「用户决定不跟踪」） |
| `4GetEverything_DSH执行方案_Flask版.md` | `??` | ❌ 否 | 同上 |
| `6GetEverything-下一阶段规划方案.md` | `??` | ❌ 否 | 本轮的工作单，从未入库 |
| `.archify/` | `??` | ❌ 否 | 本机产物（16 文件 / 2.05 MB） |
| `.dsh/skills/geteverythingskill/` | `??` | ❌ 否 | `DECISIONS.md:144` 登记为「用户决定不跟踪」 |

**两条给后来者的提醒**：

1. **`git check-ignore <路径>/`（带尾斜杠）在 Windows 上不可信** —— 它对任意目录名
   都会返回退出码 0 并**瞎报一条规则**（实测 `foo/` 也「命中」`.gitignore:68`）。
   要判断「某个真实文件会不会被忽略」，正确做法是
   `git check-ignore -v <真实文件路径>`（**不带尾斜杠、指向真实存在的文件**），
   或者直接看 `git status --ignored --porcelain` 的 `??` / `!!`。
   —— 补报告时我先用前者得出了**相反的错误结论**（以为那 7 项都被忽略），
   改用后者才对上。这和 §9.31.4 记的「不实测就写文档」是同一类错法。
2. **`??` 不等于「安全」。** 这 7 项之所以没进仓库，靠的是**纪律**（从不 `git add -A`）
   而不是忽略规则。所以 `AGENTS.md` 与 §7.7 第 31 条那条「不许 `git add -A`」
   在这里**仍然成立、且比想象中更重要** —— 一旦有人图省事 `git add -A`，
   被带上去的会是 2 MB 的 `.archify/` 与本机工作单，而**不是**被忽略。

**没有动 `.gitignore`**：放宽（把编号前缀补上）与收紧（把 `??` 那几项真正忽略）
都是独立决策，会改变提交纪律的边界，等用户拍板。

---

### 9.32 补交 `Nightly Execution Report`（流程缺口，非代码缺口）

`.dsh/skills/geteverythingskill/SKILL.md` §9（`:182-201`）规定无人值守每轮结束
要生成十二节的 `Nightly Execution Report`；`docs/DECISIONS.md:17` 也要求
「命中预授权项必须在 Nightly Execution Report 里逐条列出」，`:1174` 的早晨验收
清单第 1 条就是「打开 Nightly Execution Report」。**但全仓此前并不存在这个文件** ——
`grep` 只命中 `docs/DECISIONS.md` 自己的两处引用。

本轮补上：`docs/milestones/Nightly_Execution_Report_2026-10-04.md`（十二节齐全，
含「我替你拍了哪个板」一节）。放在 `docs/milestones/` 是因为它与
`DSH_本轮交付报告_2026-10-01.md` 同属「本机过程材料」，该目录已被
`.gitignore:67` 忽略（§9.31.7 实测确认）。

**这是流程缺口，不是代码缺口** —— 它不影响任何功能，但它意味着**此前每一轮的
「早晨验收入口」都是断的**：验收清单让你打开一份不存在的报告。如实记下。

---

### 9.33 页面级认证缺口收口：匿名 `action=chat` 可达真实执行 + 匿名首页泄漏授权资产（2026-10-04）

> 本轮**不是**实现规划方案里的新能力，而是把上一轮只读审计挖出的两处**既有**缺陷修掉。
> 它们是同一类问题的两个面：**「有意匿名」的边界被扩大到了它本不该覆盖的东西上。**

#### 9.33.1 缺陷一：匿名 `action=chat` → Agent → 真实子进程

`app.py:index()` 原本只在 `if action in _SCAN_ACTIONS:`（`_SCAN_ACTIONS = {"scan"}`）
**内部**调 `_require_admin_for_page()`，`elif action == "chat":` 分支**没有任何认证调用**。
而 chat 会进 `agent/service.handle_agent_message()` → `AgentAction`，其
`_tool_subdomain` / `_tool_httpx` 直接调 `tool_runner.run_tools()` 与
`HttpxRunner().run_scan()` —— 两者都不查 `GEF_ALLOW_REAL_SCAN`、也不查 Scope
（实测证据见 `docs/AGENT_ASYNC_IMPACT.md` I-5）。

**五个只读探针的实测结论**（全部 `GEF_ALLOW_REAL_SCAN=false`、目标 `example.test`、
`BaseRunner._run_subprocess` 被断言装载过的桩替换）：

| 场景 | 结果 |
|---|---|
| 匿名两步 chat（`test_client`，带 cookie） | `agent.action.run_tools` 调用 **1 次** |
| 匿名两步 chat（真 waitress `127.0.0.1:5089`） | **1 次** |
| 匿名两步 chat（桩 `_run_subprocess`） | 子进程入口触达 **1 次**（`subfinder -d example.test …`） |
| **单请求不带 cookie** | **0 次** —— 必须走完「意图 → 确认执行」两步 |
| 对照：匿名 `POST /api/run`、`/api/tool/subfinder/run`、`/api/jobs` | 全部 **401** |

三点要点：① 它是**两步**（`_handle_pending_plan` 要读 session 里的 `pending_plan`），
跨站表单直发打不通，但本机任何能发 HTTP 的程序都打不通不了它；
② **`GEF_ALLOW_REAL_SCAN=false` 与 Scope 都拦不住**，且**不产生 `job.created` 审计**；
③ **零测试覆盖** —— `tests/` 里 `action=chat` 命中 **0**，
`test_api_auth_contract.py` 的 `ADMIN_ONLY` **从未列出 `("POST", "/")`**。
用 `git log -S` 复核：这条分支自初始提交 `61b0f9b` 起就是这个形状，**不是近期回归**。

**修法（有意做成「默认安全」的形状）**：把守卫提到 `action` 分支之前 ——
```python
if request.method == "POST":
    action = request.form.get("action", "scan")
    _require_admin_for_page()          # ← 所有 POST 一律先认证
    if action in _SCAN_ACTIONS: ...
    elif action == "chat": ...
```
原写法是「按动作名**白名单**护」，新增一个动作就多一个洞；改后新增动作默认是安全的。

#### 9.33.2 缺陷二：匿名 `GET /` 下发整份授权资产清单

`app.py` 原先无条件执行 `context["scopes"] = _load_scope_options()`，
而 Phase 1 又把 `allowed_domains` / `allowed_cidrs` 渲染进了首页资产卡片
（`index.html` 的 `class="scope-asset…"`）—— 等于把「这份授权叫什么、覆盖哪些目标、
是否开启真实扫描」整份摊给匿名访客。

**资产页一直是按登录态过滤的**（`app.py:382` 的
`scopes=_load_scope_options() if is_authenticated else []`，`assets.html:32` 也有
`{% if not is_authenticated %}` 提示），首页漏了同一个判断。**这是不一致，不是设计。**

修法：首页（`app.py:304`）与资产页**同一口径**；未登录时 `scopes=[]`，模板也不再显示
「还没有任何授权范围」（那会让匿名访客误以为系统里没有授权资产）。

#### 9.33.3 这两处为什么算「修」，而不是「改契约」

* `core/auth.py` 的 docstring 自己就写着「不能匿名扫描」；
  `SECURITY.md` / `docs/API.md` 的匿名清单里**从来没有** `action=chat` 能执行这一条 ——
  文档写的是「`action=chat` 可匿名」，**前提是它只读**。事实与前提不符，改的是事实。
* 「放宽 / 绕过认证边界」是红线；**收紧不是**。本轮只动了两处判断的位置与条件，
  没有新增/删除任何路由（仍 **48 规则 / 50 绑定 / 42 个 `/api/*`**），
  没有动 7 个已定的匿名只读 API，也没有动 `GET /` 的匿名可读性本身。
* 需要同步改的**只有文档与测试期望**：`docs/API.md:100-101`、本文件 §6 第 30 条、
  `docs/TEST_REPORT.md` §15。**没有一条既有断言被放松**（新增 2 条，26 条原样通过）。

#### 9.33.4 验证

`pytest -o addopts="" -q` → **1316 passed / 2 skipped**（`--collect-only` 1318）；
`ruff` 全过；`mypy` 72 文件 0 error；三个 JS `node --check` 通过。
此外做了**三次独立于测试**的复核（`test_client` 桩 + **真起 waitress** 桩 +
**登录态正路探针**），每次都**先断言前提**（桩已装载 / `get_admin_token()` 等于探针 Token
且 `is_ephemeral_token()` 为 False）再采信数字 —— 本轮这条纪律一晚上救回两次假阴性
（含一次「管理员也 401」：Token 在 `import app` 之后才设，而 `config` 导入期已跑完
`load_dotenv()`）。新增 3 条用例（前 2 条修复前是红的），细节见 `docs/TEST_REPORT.md` §15。

