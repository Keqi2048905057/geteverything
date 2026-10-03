# 变更日志

本文件记录本仓库的阶段性变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased] — 本机联调版

代码基线：`Linki4964/get_everything_framework` 的 `main`。
本仓库（`geteverything`）刻意**不继承**上游历史，只有一条干净历史，从「M0 保护工作区与建立基线」重新开始。

> 执行依据（执行提示词、实施方案、逐里程碑验收报告）是**本机过程材料，不入库**，
> 仅保存在开发机上。仓库内的项目说明见 `README.md` 与 `docs/CODEBASE_MAP.md`。

### M0 — 保护工作区与建立基线（已完成）

新增：

- `get_everything_framework/pyproject.toml`：pytest / ruff / mypy 工具链配置（不声明可安装包元数据）。
- `get_everything_framework/requirement-dev.txt`：开发依赖（pytest / ruff / mypy）。
- `get_everything_framework/tests/`：`conftest.py`、`unit/`（冒烟与仓库布局测试）、`integration/`、`fixtures/` 占位。
- `.github/workflows/ci.yml`：push / PR 上运行 `ruff check .` 与 `pytest -q`。
- `LICENSE`（MIT + 授权使用声明）、`SECURITY.md`、`CONTRIBUTING.md`、本文件。
- `.gitignore`：补齐运行期产物、缓存与本地密钥的忽略规则。

排除在版本控制之外（文件仍在磁盘上）：`results/`（含 `scan_results.db`）、`uploads/`、
`SecLists/`、`scripts/*.exe`。（注：`SecLists/` 在**当前工作树里并不存在** —— 它只被
`.gitignore` 忽略；默认字典因此缺失，`M5` 起以 `config_error` 明确报出而不是静默空结果。）

### M1 — 首页可用与静态基线（已完成）

新增：

- `get_everything_framework/web/templates/`（`index.html`、`login.html`）与 `web/static/`（`app.css`、`app.js`）。
  前端无框架、无 CDN，只用 `fetch` 轮询；产物全在本机。
- `core/auth.py`：单一管理员 Token（`LOCAL_ADMIN_TOKEN`）+ HttpOnly Session + `X-Local-Token` 三种入口；无 RBAC。
- `core/security.py`：`SECRET_KEY` 弱值检测；未配置时生成**进程级一次性**密钥并告警。
- `core/errors.py` / `core/errors_handlers.py`：统一 `ErrorCode` 与 Flask 错误处理器，替换散落的 `abort(500)`。
- `core/ids.py`：带前缀的 UUID4（`job_` / `step_` / `scope_` / `upload_` / `evt_` / `artifact_`）。

变更：

- `app.py` 增加 `create_app()` 工厂（保留模块级单例供 waitress 与测试共用）。
- `app.py:index()` 的模板路径修正为 `web/templates/`，首页不再 `TemplateNotFound`。

修复（对应 `docs/CODEBASE_MAP.md` 第 7 节）：

- #3「首页必然 500」、#32「首页无模板」、#23「`SECRET_KEY` 默认固定值」、#34「无 `create_app()`」。

### M2 — 安全边界与 Scope 强制（已完成）

新增：

- `core/scope.py` / `core/scope_store.py`：Scope 模型与匹配语义（**排除优先**、拒绝全放行）；
  `require()` 保证「无 Scope 即拒绝」，不再有隐式全开放。
- `core/uploads.py`：受控上传 `uploads/<id>/{raw.*,normalized.txt,meta.json}`，
  API 只返回 `upload_id`，**不再暴露服务器路径**。
- `core/audit.py`：`audit_events` 记录登录、Scope 变更、设置写入、扫描提交。
- `core/safety.py`：`mock`（默认）/ `real` 模式解析 + `GEF_ALLOW_REAL_SCAN` 开关。
- `api/scopes.py`：Scope 的增查接口。

变更：

- `api/scan.py`：`/api/run` 与 `/api/tool/<n>/run` 必填 `scope_id`，拒绝 `file_path`，改收 `upload_id`。
- `api/settings.py`：写 `.env` 改为原子写（临时文件 + `fsync` + `os.replace`）+ 写入前备份 + 审计（只记字段名）。
- 真实扫描的**双开关**：环境 `GEF_ALLOW_REAL_SCAN=true` **且** `Scope.active_scan=true`，缺一即 403 `scope_violation`。

修复：

- #24「`/api/settings` 可匿名写 `.env`」、#5「上传解析出错 500」。

### M3 — 异步任务与状态机（已完成）

新增：

- `core/db.py`：新库 `results/local.db`（WAL + `busy_timeout=5000` + `BEGIN IMMEDIATE`），
  与旧库 `results/scan_results.db` 并存（旧库在联调期间视为只读历史数据）。
- `core/jobs.py`：任务数据层——状态机、步骤快照、`BEGIN IMMEDIATE` 认领、租约（`worker_id` + `lease_until`）、
  `cancel` / `retry` / `recover_stale_jobs()`。
- `jobs/executor.py`：与进程无关的执行逻辑（可直接单测调用）。
- `jobs/worker.py`：独立 worker 进程 `python -m jobs.worker`，心跳文件 `results/worker_heartbeat`。
- `api/jobs.py`：`/api/jobs*` 共 7 个接口（M3 当时的数量；后续里程碑陆续增加，
  现为 **10 个** —— 见「下一阶段体验优化 Phase 4」与 `docs/API.md` §6.4）。

变更：

- `POST /api/jobs` 立即返回 **202 + `queued` + `job_id`**，扫描不再阻塞请求线程。
- 首页表单提交改为创建异步任务。
- `/health` 增加 `queue` 与 `worker` 字段（`ok` / `stale` / `missing`）。

修复：

- #1「一直卡在 running / 请求不返回」、#2 前半「立刻失败无日志」（统一转稳定 `error_code`）。

### M4 — 统一结果模型与错误码（已完成）

新增：

- `core/runner_result.py`：`Observation`（结构化观测）/ `ToolHealth`（工具健康度）/ `RunnerResult`
  （`status` / `error_code` / `exit_code` / `duration_ms` / `command_preview` / `data[]` / `stderr_preview` / `parser_version`），
  以及出参脱敏 `scrub_command()`。
- `core/artifacts.py` + `artifacts` 表：每次执行的 stdout / stderr / 工具输出文件落盘为**原始证据**，
  截断后登记 sha256/大小；读取接口只返回截断并脱敏的内容，**不下发服务器路径**。
- `api/jobs.py`：`GET /api/jobs/<id>/artifacts`（元数据）与 `GET /api/artifacts/<id>`（内容）。

变更：

- 每个 runner 实现统一接口：`build_command(target, options)`（只拼命令）、
  `parse_output(stdout, stderr, artifacts)`（只解析，返回 `(values, error_code)`）、
  基类 `run(target)`（执行 + 解析 + 组装 `RunnerResult`）。
  `run_scan(target)` 保留旧的「返回字符串列表」签名，旧调用方不受影响。
- 17 个 runner 全部铺开：`subfinder` / `amass` / `amass_intel` / `assetfinder` / `oneforall` /
  `alterx` / `shuffledns` / `enscan` / `dnsx` / `httpx` / `gospider` / `katana` / `waybackurls` /
  `feroxbuster` / `dirsearch` / `naabu` / `nmap`。
- `httpx` 的 `Observation.data` 保留 `status_code` / `title` / `webserver` / `tech` / `cdn`。

修复（都是只有真机才会暴露的缺陷）：

- **失败被吞成空结果**：`_execute` / `_execute_stdout` 不再把异常降级成 `return False`，
  而是带 `ErrorCode` 返回；调用方能区分「工具没装」「超时」「非零退出」「本来就没结果」。
- **残留输出文件被当成本次结果**：执行前先删同名旧输出文件；删不掉时记 `stale_output_warning`，
  不再把上一次的输出读回来当成功。
- **Windows 下超时杀不掉孙进程**：`.cmd` 工具链是 `python(worker) → cmd.exe → 工具`，
  `subprocess.run(timeout=)` 只杀中间层，孤儿进程攥着管道会让「超时」形同虚设、worker 被永久占住。
  改为 `Popen` + `communicate(timeout)` + 进程树清理（Windows `taskkill /F /T`）。
- **POSIX 下 `killpg` 会连调用方一起杀**：`Popen` 未另起进程组时，子进程与 worker 同组，
  `os.killpg()` 的杀伤范围包含 worker 自己（表现为 Linux/CI 上测试进程凭空消失，本地 Windows 全绿）。
  现在 POSIX 下子进程另起进程组，且清理前比对 child / own 进程组。

### P0 — 产品化加固（本轮，非里程碑，按 DSH 执行方案 P0 清单）

新增：

- `core/policy.py`：**统一 Policy / Scope 引擎**。四个入口
  `validate_job_targets` / `validate_step_target` / `validate_resolved_address` / `validate_redirect_target`，
  语义统一为「缺失 → 400、不存在或越界 → 403、整体拒绝不部分执行」。
- `core/exports.py` + `exports` 表：导出登记（`filename` / `format` / `row_count` / `size` / `sha256`），
  对外只给 `export_id` 与 `download_url`，`path` 仅内部使用。
- `api/results.py`：`GET /api/export/<export_id>/download`（`send_file` 附件下载）与 `GET /api/exports`。
- `core/jobs.py`：显式状态跃迁表 `ALLOWED_TRANSITIONS` + `can_transition()`；`MAX_ATTEMPTS = 5`。
- `exporter.py:safe_prefix()`：导出文件名前缀白名单过滤（折叠 `..`、限长 64）。

变更：

- `api/scan.py` / `api/jobs.py` / `app.py` 全部改走 `core/policy.py`，删除各自重复的 Scope 判断。
- `jobs/executor.py`：real 步骤在调用 Runner **之前**重新校验 Scope，任务创建后 Scope 被删/被改即中止。
- `agent/action.py` / `agent/planner.py`：Agent 层只接受受控 `upload_id`，任意 `file_path` 一律拒绝。
- `storage.py`：新增 `_connect()` 上下文管理器（事务语义不变、退出必 `close()`）+ 连接级 `busy_timeout=5000`。
  10 个调用点全部切换；**未改表结构、未动既有数据**。
- `core/health.py`：健康检查的只读连接改显式 `close()`。
- `core/db.py`：`exports` 表 + `idx_exports_created` 索引。

修复：

- **DECISIONS-I**：`ResourceWarning: unclosed file` 的真实来源不是报告里的 `core/db.py:244`，
  而是 `storage.py` 的 `with conn`（只提交不关闭）。已修并补回归测试。
- **DECISIONS-I**：`test_security_baseline.py` 的 `SECRET_KEY` 告警改为 `pytest.warns` 显式断言。
  现在 `python -m pytest` **零 warning**。
- 导出下载与超大上传用例的 `ResourceWarning`（前者漏关响应，后者来自 Werkzeug 测试客户端的临时文件）。

### P1 — 统一资产模型与 Diff（本轮，方案第 8、9、10 节）

新增：

- `core/canonical.py`：**`canonical_key` 规则**。七种类型（`subdomain` / `host` / `ip` /
  `cidr` / `url` / `port` / `service`）的归一化：host 小写 + 去尾点 + IDNA + RFC1123 校验；
  IP 压成最简形式；CIDR `strict=False`；URL 协议小写、默认端口丢弃、空路径补 `/`、
  query 保留、fragment 丢弃、带凭据拒绝、非 `http(s)` 协议拒绝；`canonical_key = "type|value"`。
- `core/assets.py`：**资产 / 观测两层模型**。
  - `record_observation()` / `record_observations()`：同一资产只落一行 `assets`，
    每次观测在 `observations` 留痕；`first_seen` 永不被覆盖，`last_seen` 每次推进。
  - `ingest_step_observations()`：把已完成步骤的结构化观测落库（executor 调用）。
  - `mark_stale_assets()` / `list_assets()` / `count_assets()` / `list_observations()` /
    `asset_summary()` / `get_asset()` / `get_asset_by_key()`。
  - `diff_jobs()`：**Diff Engine**，输出 `added` / `removed` / `changed` / `unchanged` + `counts`。
- `core/db.py`：新增 `assets` / `observations` 两张表与索引；新增 `query()` 只读辅助函数
  （**只新增表，未改任何既有表结构、未动既有数据**）。
- `api/assets.py`：`GET /api/assets`、`/api/assets/summary`、`/api/assets/<id>`、
  `/api/observations`（全部需管理员）。
- `api/jobs.py`：`GET /api/jobs/<before>/diff/<after>`（Diff 端点）。
- `web/templates/assets.html` + `web/static/assets.js`：**资产列表页**（无框架、无 CDN），
  支持按范围/类型/状态/关键字筛选、分页、点开观测时间线；页面下方为**两次任务对比**表单
  （选基线与对比任务 + 可选限定范围 + 「含未变」开关），结果按
  新增 / 消失 / 变更 / 未变 四段渲染，`changed` 直接显示
  `status_code: 200 → 403` 这类属性差异。任务下拉默认选中最近两次。
  **对比清单里的条目可以点进资产详情**（脚本给带 `asset_id` 的条目加 `diff-item-clickable`
  并复用 `openDetail()`，详情面板会滚入视口）。
- `app.py`：`GET /assets` 页面路由；首页顶栏加入口。
- `core/migrate.py` + `scripts/migrate_legacy_results.py`：**旧库 → 新库的只读迁移**。
  旧库以 `mode=ro` 打开，一个字节都不改；观测 ID 由旧库行身份哈希而来（**确定性**，
  所以可安全重跑，已迁行跳过）；`...Z` 时间戳统一成 `+00:00`；
  计划按 `observed_at` 全局升序排序（否则 `first_seen` 会取成「两张表里先遍历到的那张」的时间）；
  单条脏数据只记 `reason` 不中断；`scope_id` 留 `NULL`（旧库年代没有 Scope 概念，不能事后编）。
  CLI **默认 dry-run**，`--apply` 才写；源库=目标库时拒绝执行。

变更：

- `core/jobs.py`：新增事件类型 `EVENT_ASSETS_INGESTED = "step.assets_ingested"`。
- `jobs/executor.py`：每个步骤 `finish_step` 后调用 `ingest_step_observations()`，
  并把 `written` / `skipped` / 前 5 条原因记成事件。
  **落观测是派生产物：它失败不会让任务变成 failed。**
- `api/__init__.py`：注册 `api.assets`。
- `web/static/app.css`：资产页布局与状态配色。

修复：

- `core/assets.py` 初版把 `canonical_key` 建成**全局唯一**，导致同一资产在两个 Scope 下
  「查不到又插不进」。改为唯一索引 `(canonical_key, IFNULL(scope_id,''))`
  （`IFNULL` 是因为 SQLite 的 `UNIQUE` 允许多个 `NULL`，裸两列索引会漏掉无 Scope 的行）。
- `core/assets.py` 引入的 10 条 mypy 报错（缺类型标注）已补回，当时仍为 34（M7 存量，无新债）。
  **M7 已把这 34 条全部清掉，见下。**
- `core/assets.py:diff_jobs()`：`include_unchanged=False` 原先会把 `counts["unchanged"]`
  一起抹成 0，导致「这次扫到了但没变化（未变 3）」与「这次什么都没扫到（未变 0）」
  完全不可区分 —— 而这恰是 diff 最有用的一条信息。改为**只影响明细下发、不影响计数**，
  并由 `test_diff_can_omit_unchanged_details` 与 API 侧用例双向锁定。

### M7 — mypy 清零（本轮，方案第 17 节）

方案第 17 节明确「**不能为了绿 CI 而在配置里排除所有问题**」。因此本轮
**没有改 `pyproject.toml` 的 `[tool.mypy]`**（没加 `ignore_errors`、没缩 `exclude`、
没放宽 `no_implicit_optional`），改的是代码本身：**34 → 0**。

变更：

- `config.py`、`modules/shuffledns.py`：空字面量补 `dict` 标注。
- `modules/base.py`：基类**显式声明** `run_scan` 并抛 `NotImplementedError`
  （原先基类根本没有这个方法，子类忘记实现时抛 `AttributeError`，
  与「跑通但零结果」在调用方看来不可区分），由 `run()` 统一翻译成带 `error_code` 的失败结果；
  `_kill_process_tree` 的 POSIX 分支改用 `getattr(os, "getpgid"/"killpg")` 取函数、
  `getattr(signal, "SIGKILL", SIGTERM)` 取信号 —— Windows 存根里这三个名字并不存在，
  顺带去掉了原来那层过宽的 `except AttributeError`。
- `modules/httpx.py`、`modules/dnsx.py`：`_write_input_file` **补回基类的 `suffix` 参数**。
  子类收窄签名会让「按基类类型调用子类实例」（runner 注册表就是这么用的）不成立。
- `modules/httpx.py`：`run_scan` 显式收成 `List[str]`（原先 `List[Any | None]`，`None` 真可能混入）。
- `core/jobs.py`：新增 `get_job_or_raise()`（读不到抛 `ValueError`）。写路径
  （`create_job` / `cancel_job` / `finish_job`）刚写完就回读，`None` 属不可能状态，
  不该把 `| None` 传染给调用方；`get_job()` 保持可空，读接口语义不变。
- `api/scan.py`：`resolve_scoped_targets` 的返回标注由字符串 `"object"` 改为真实的 `core.scope.Scope`。
- `agent/action.py`：
  - `self.context` 补 `Dict[str, Any]` —— 字面量六个键全是 `None`，类型被推成
    `dict[str, None]`，导致后面每处写字符串都报「给 None 赋值」（**一处根因消掉 9 条**）；
  - `_handle_pending_plan` 把待处理计划取到局部变量并判空（消掉 5 条，
    同时修掉一处真实的 `deepcopy(None)` 隐患）；
  - `available_tools` 补 `Dict[str, Dict[str, Any]]`，`tool["handler"](args)` 不再是「object 不可调用」。

修复：

- **`agent/action.py:_tool_httpx` 的 `items` 取错了对象。** `run_scan` 返回的是 URL
  **字符串**列表（旧签名兼容），元数据在 `runner.last_items`。原先 `items = rows`，
  于是「存活探测 → 整理回复」会以 `AttributeError: 'str' object has no attribute 'get'`
  收场 —— 而且**零结果时不炸**，只在真有存活结果时出现。已改为 `runner.last_items[:20]`，
  并加回归用例（同时覆盖 `_summarize_httpx_items`）。见 `docs/CODEBASE_MAP.md` 第 6 节第 25 条。

新增测试（+4，701 → 705）：

- `test_base_runner_without_run_scan_fails_loudly`：基类未实现 `run_scan` 必须报失败。
- `test_write_input_file_accepts_suffix_across_runners`：httpx / dnsx 的签名与基类一致。
- `test_get_job_or_raise_distinguishes_missing_job`：两种语义都要有。
- `test_agent_httpx_returns_metadata_items`：httpx 步骤的 `items` 必须是元数据字典。

范围说明：`mypy` 命令里的 `app.py` 会顺着 import 把 `agent/action.py` 一起查。
真正落在范围外的是 `agent/providers/*`（**当前无任何调用方**）：显式加 `agent` 参数
会多出 7 条 openai 存根相关的报错。本轮**没有**为这 7 条去改 provider 层的组织方式
（属改运行语义，且该层不可达）。

### P1 补充 — Diff 条目可点进资产详情（本轮）

`docs/CODEBASE_MAP.md` 第 9.12.7 节最后一条遗留：清单已渲染 `data-asset-id`，
但没人接点击，于是「能点进详情」只是注释里的一句承诺。

变更：

- `web/static/assets.js`：`renderDiff` 给**带 `asset_id`** 的条目加 `diff-item-clickable`
  与 `title`（为空的保持死文本）；`bind()` 在 `#diff-body` 上做事件委托，
  命中后复用列表页的 `openDetail()`；`openDetail` 成功渲染后
  `scrollIntoView` 到详情面板 —— 面板在页面另一头，不滚过去点了像没反应。
- `web/static/app.css`：`.diff-list li.diff-item-clickable` 的虚线下划线与 hover 配色。

新增测试（+2，705 → 707）：

- `test_diff_items_always_carry_asset_id`：每条 diff 明细都带 `asset_id`，
  且 `/api/assets/<id>` 真的认它（否则前端点进去是 404）。
- `test_assets_js_wires_diff_items_to_asset_detail`：静态脚本确实接线了 ——
  无前端构建链时，漏接线没有任何别的方式能发现。

### M5 补充 — 字典路径可移植 + 缺失即显式失败（本轮）

来源：`PROJECT_STATE.md` Known Failure #5 / `SECURITY.md` / `docs/CODEBASE_MAP.md` 第 6 节第 22 条
（三处记的其实是同一个缺陷）。

问题：`config.py` 的两个 `wordlist` 在本机**都不存在** ——
`FEROXBUSTER_CONFIG` 是开发机绝对路径 `D:/c4/v2/backend/framework-main/SecLists/...`，
`SHUFFLEDNS_CONFIG` 指向仓库并不分发的 `SecLists/`。而失败形态是错的：
shuffledns 只打一行提示就 `return []`（与「跑通但零结果」不可区分），
feroxbuster/dirsearch 把不存在的路径原样当 `-w` 塞进子进程，
相对路径还依赖当前工作目录。

变更：

- `config.py`：两个 `wordlist` 改为仓库相对路径，并支持 `SHUFFLEDNS_WORDLIST` /
  `FEROXBUSTER_WORDLIST` 覆盖（写法同 `HTTPX_PATH`）。`DIRSEARCH_CONFIG.wordlist`
  保持 `None`（不加 `-w`、用工具自带字典，是合法形态）。
- `core/errors.py`：新增 `ErrorCode.CONFIG_ERROR = "config_error"`，收进 `ErrorCode.ALL`。
  **加法**：方案第 6.2 节的 10 个工具错误码一个都没改。
- `modules/base.py`：新增 `_resolve_path()`（相对路径按项目根解析，**与 cwd 无关**）、
  `require_path()`、`require_wordlist()`；缺失即抛 `RunnerInputError`，绝不进入命令行。
- `modules/shuffledns.py` / `modules/url_tools.py`：三个 runner 在**构造命令行之前**
  走 `require_wordlist()`；`build_command` 输出解析后的绝对路径。
- `web/static/app.js`：`ERROR_CODE_LABELS` 补 `config_error` 中文标签。
- 文档：`README.md`（字典两种写法对照表 + 缺失即失败）、`.env.example`（两个覆盖变量）、
  `docs/CODEBASE_MAP.md`（§9.14 新增、第 6 节 #22、第 7 节 #7/#9、§9.9、§9.8 基线）。

新增测试（+8，707 → 715）：

- `test_wordlist_configs_are_portable`：两个默认字典都不是开发机绝对路径。
- `test_every_configured_wordlist_path_resolves_under_project_root`：扫全部 `*_CONFIG`。
- `test_missing_wordlist_refuses_before_spawning_subprocess`（feroxbuster / dirsearch 各 1 例）：
  `config_error` 且 `_execute` 一次都没被调用。
- `test_missing_wordlist_error_does_not_leak_into_unknown_error`。
- `test_shuffledns_missing_wordlist_is_a_failure_not_an_empty_result`：dnsx 不被调用。
- `test_absent_wordlist_is_not_an_error`：`wordlist=None` 不算配置错误。
- `test_error_code_config_error_is_declared_and_labelled`：常量表与前端标签表同步。

顺带修正被掩盖的断言：`test_dirsearch_build_command` 原先断言字典路径**原样透传**
（`"words.txt"`），正是缺陷本身；现在断言解析后的绝对路径。

范围说明：**没有下载或分发任何字典**，`SecLists/` 仍然缺失 —— 修的是「路径怎么解析、
缺失怎么报」，不是「字典从哪来」。也不涉及表结构、鉴权、Scope/Policy。

### P0-7 — 任务幂等键与重试退避（本轮，方案第 7 节）

来源：`GetEverything_DSH执行方案_Flask版.md` 第 7 节「必须增加/确认：idempotency key /
retry count / max attempts / backoff」——其中 `max attempts` 已在 M3 落地，
本轮补齐**幂等键**与**退避**两项，以及 §16 的 Windows CI。

授权：`docs/DECISIONS.md` §3.1 —— 用户在弹窗中逐项勾选授权，口径与 E 项同规格
（**只 `ADD COLUMN`，不动既有列、不删既有数据**）。

变更：

- `core/db.py`：`jobs` 表新增 `idempotency_key TEXT` 与 `next_attempt_at TEXT`
  （同时写进 `CREATE TABLE` 与 `_COLUMN_MIGRATIONS`，旧库靠
  `ALTER TABLE ... ADD COLUMN` 升上来）。新增两个**非唯一**索引
  `idx_jobs_idempotency` / `idx_jobs_next_attempt`；它们必须排在
  `_migrate_columns()` **之后**，否则旧库升级时会 `no such column`。
  刻意不用 `UNIQUE`：唯一性由 `BEGIN IMMEDIATE` 事务内的「查重 + 插入」保证，
  避免给旧库升级引入「历史脏数据导致建索引失败」的风险面。
- `core/jobs.py`：
  - `create_job_with_status()`：同键的**未终结**（`queued` / `running`）任务直接复用，
    返回 `(job, reused=True)`，不重复插入、不重复展开步骤、不重复写 `job_created` 事件；
    「查重 + 插入」同一事务，并发重复提交不会各插一条。`create_job()` 仍是原签名。
  - `normalize_idempotency_key()`：空串 / 纯空白 / `None` → `None`（视为没传键），
    非字符串或超过 200 字符 → `ValueError`。
  - `retry_job()`：写 `next_attempt_at = now + retry_backoff_seconds(attempt)`；
    第 1 次不退避，之后 5 / 10 / 20 / 40… 秒，封顶 300 秒。
  - `claim_next_job()`：领取条件加 `next_attempt_at IS NULL OR <= now`，
    领走时清空窗口（窗口只用来「推迟领取」，不是任务的长期属性）。
  - `_job_to_dict()`：两列按列名存在性读取，旧库缺列时退化为 `None`。
- `api/jobs.py`：`POST /api/jobs` 接受可选 `idempotency_key`（非法值 400），
  响应新增 `reused`；`POST /api/jobs/<id>/retry` 响应新增 `next_attempt_at`。
  幂等命中仍写一条审计（`detail.reused = true`），否则「少了一个任务」事后无从解释。
- `web/static/app.js`：任务详情新增「最早可重试」一行；`jobSignature()` 纳入
  `attempt` 与 `next_attempt_at`（否则 retry 后详情页看不到变化）。
- `.github/workflows/ci.yml`（§16）：`lint-and-test` 改用
  `matrix.os: [ubuntu-latest, windows-latest]` 且 `fail-fast: false`；
  两个平台都跑 `ruff check .` + `pytest -q`，`mypy` 步骤只在 ubuntu 上跑。
  **未涉及任何密钥，未改仓库 Settings。**

新增测试（+24，715 → 739）：

- 幂等：同键复用同一个 `job_id`、步骤不重复展开、键在 `running` 期间仍生效、
  任务落终态后键释放、不同键互不影响、空/空白键等于没传键、非字符串与超长被拒、
  幂等命中不重复写 `job_created` 事件。
- 退避：retry 后 `next_attempt_at` 在将来、窗口内领不到（但仍在 `queued` 计数里）、
  窗口推旧后立刻能领、退避中的任务不阻塞后面的任务、退避函数指数增长并封顶、
  领走时清空窗口、新任务 `next_attempt_at` 为空即可领。
- 迁移：`test_idempotency_and_backoff_columns_are_additive_on_legacy_db`
  手工造一个「P0-7 之前」的 `jobs` 表，`init_schema` 后两列补齐且既有行原样保留。
- API：`reused` 字段、同键只产生一个任务、非字符串/超长键 400、幂等命中可审计。

顺带修正被掩盖的断言：`tests/unit/test_assets.py` 的
`test_new_tables_do_not_touch_legacy_schema` 原先断言
`"idempotency_key" not in job_columns` —— 该断言写于 P0-7 授权之前，
本轮改为断言「两列存在且可空」，并保留「既有列 `attempt` 一个都不能少」。

### M7 补充 — SQLite 并发测试（本轮，方案第 15 节）

来源：`GetEverything_DSH执行方案_Flask版.md` 第 15 节「Worker：restart / stale lease /
retry / cancel / **duplicate execution**」与第 7 节的队列要求。此前 `core/db.py` 的
「WAL + `busy_timeout` + `BEGIN IMMEDIATE`」只有**单线程**的间接验证
（`test_storage_connection.py` 只断言 `PRAGMA busy_timeout` 的值），并发风险
（任务被领两次 / 被读成半截事务）一直只是代码阅读结论。

授权：落在 `docs/DECISIONS.md` 第 2 节白名单「补充与更新测试」内 —— **只新增测试文件，
不改任何产品代码**。

新增 `get_everything_framework/tests/unit/test_db_concurrency.py`（+13，739 → 752）：

- 连接参数：`core.db.connect()` 的 `journal_mode=wal` 与 `busy_timeout == BUSY_TIMEOUT_MS`；
  WAL 跨连接保持（读路径不会把库退回 `delete`）；旧库 `storage.py` 的连接也带 `busy_timeout`。
- 并发写：8 线程各建 4 个任务全部落库且 id 互不重复；并发 `audit.record` 一条不丢；
  读写混合下读者拿到的每一行都能被 `get_job` 读到（不读半截事务）。
- 并发认领：8 个 worker 抢 24 个任务**不重不漏**（`duplicate execution` 的正解）；
  同一任务只有一条 `job.started`；没有任务时并发认领都干净地拿到 `None`。
- 幂等键：8 线程用同一把键并发创建 → 只建 1 个任务、7 次 `reused=True`、
  `job.created` 只有 1 条；不同键互不顶掉。
- 锁等待的正反两面：持锁时写者应「等」而不是立刻 `database is locked`（确定性构造，
  带耗时断言防假通过）；**反证**用例确认 `timeout=0` 的裸连接在同一场景下必须抛
  `sqlite3.OperationalError`，避免前一条退化成永远通过。

实现上的一个关键点：`threading` 默认把线程内异常打到 stderr 后**悄悄结束线程**，
直接 `join()` 会把「8 个线程挂了 3 个」读成绿色。本文件用 `_run_threads()` 收集并
重抛线程内异常，同时断言无线程在 60 秒后仍存活（死锁要表现为失败而不是挂住）。

### M7 补充 — 本地 fixture HTTP 全链路 E2E（本轮，方案第 18 节）

来源：`GetEverything_DSH执行方案_Flask版.md` 第 18 节。原话是

> 不要为了验证真实链路去扫未授权公网目标。
> …… 这条链路必须至少有一条全流程测试。

第 25 节的 P1 验收清单里也一直挂着未勾选的 `[ ] 本地全链路 E2E`，第 26 节的执行顺序第 15 条
同样是「本地真实 E2E」。此前 `mode=real` 只有**单元级**的真实子进程用例
（`test_runner_interface.py` 起真进程验证成功/超时/未安装），从未有**一条链路**把
target → job → worker → runner → raw artifact → parser → observation → asset → diff → export
接起来跑通过。这条链路跨越 6 层，任何一层的接口漂移都只有在这里才会暴露。

授权：落在 `docs/DECISIONS.md` 第 2 节白名单「补充与更新测试」+「Bug 修复」内。

新增三个文件：

| 文件 | 作用 |
|---|---|
| `tests/fixtures/local_http_server.py` | 只绑 `127.0.0.1`、端口 `0` 由系统分配的标准库 `ThreadingHTTPServer`。固定路由 `/`(200) `/stable`(200) `/extra`(200) `/forbidden`(403) `/missing`(404) `/redirect`(302→`/`)，`Server` 头固定 `GefFixture/1.0`，带 `set_status()` 供 Diff 用例改单个路由的状态码与标题 |
| `tests/integration/test_m7_local_e2e.py` | 跑**真实 httpx 子进程**打本地 fixture，一次走完全链路（+2 例 / 7 个断言组） |
| `tests/__init__.py` | **必需**：site-packages 里存在一个常规包 `tests`，会把本仓库的命名空间包 `tests` 顶掉，`import tests.fixtures...` 直接 `ModuleNotFoundError`。加上这个文件后 `tests` 成为常规包，解析稳定落在仓库内 |

被替换的**只有一步**：httpx 的候选来自 `ScanResultStore`，而 `storage.py` 按方案要求只有写入、
没有删除接口（禁止删历史数据），所以同一域名的候选集在库里只增不减 —— 方案第 10 节验收形态
「A B C → A C D」需要候选集**收缩**一次，这在库层面无法表达。因此第二次任务显式替换
`HttpxRunner._load_candidates` 模拟「上游子域发现这次给了不同集合」。被替换的只是**输入发现**；
httpx 之后的一切（命令行构造、子进程执行、JSONL 解析、证据落盘、观测归一、资产归并、diff、导出）全是真实代码。

顺带修掉一个**既有缺陷**（本轮发现，非本次引入）：

- `core/artifacts.py:read_artifact()` 用的是 `scrub_command()`，而该函数的语义是
  **命令预览**：末尾会截到 `MAX_COMMAND_PREVIEW = 300` 字符。后果是
  `GET /api/artifacts/<id>` 的 `text` 永远只有头 300 字符，同时 `truncated` 仍是 `False`
  —— 既违反方案第 6.2 节「结果详情能看到原始证据」，又对外说了假话。
  证据动辄几十 KB，被截的正好是排查时需要看的部分。
- 改法（只加不减）：把脱敏规则抽成 `_redact()`，新增 `scrub_text(value, limit=None)`
  —— **只脱敏、默认不截断**、可选硬上限；`read_artifact()` 改用它。
  `scrub_command()` 的对外行为、默认 300 字符上限、`preview_text()` 的语义**均未变**
  （既有 9 条脱敏用例原样通过，另加 4 条 `scrub_text` 用例把新语义钉住）。
- 副作用说明：`_LONG_TOKEN_RE` 的兜底规则现在真正作用于整份证据，所以证据里
  **连续 20 字符以上的字母数字串会被打码成 `***`**（URL 里的 `127.0.0.1:PORT/path` 不受影响，
  该正则有路径片段的前后视断言）。这是刻意的取舍：宁可过度脱敏，也不能让 API Key 进前端。

回归测试（+7，752 → 759）：

- fixture 自身只监听回环地址，且 `core.safety.is_local_only_target("127.0.0.1")` 为真
  —— 把「不扫未授权目标」这条硬约束钉进测试；
- 第一次任务：任务 `succeeded`、步骤 `error_code is None`、`found_count == 3`、
  `parser_version == "1.0"`、三条观测的 `status_code` / `title` / `webserver` / `source_tool` 全部正确；
- 原始证据：`stdout` 与 `output` 两份都在、响应里没有 `path`、`missing is False`、
  `truncated is False`、内容恰好 3 行（**这条就是上面那个截断缺陷的回归**）、
  且结果文件确实落在临时目录 `127.0.0.1_httpx.jsonl` 而不是仓库 `results/`；
- 观测→资产：3 条观测 → 3 个 `type=url` 资产，`summary.by_type == {"url": 3}`，详情带观测时间线；
- Diff：`counts == {added: 1, removed: 1, changed: 1, unchanged: 1}`，
  `/` 的 `status_code: 200 → 403` 且 `title` 同步变化，`asset_id` 指向同一行资产（资产是**归并**的：
  第二次扫描后总数是 4 而不是 6）；
- 导出：`/api/export` 登记成功、`download_url` 可下载、CSV 表头与上游候选值正确、
  响应与列表都不含 `path`。
  这里刻意把现状写进断言：`/api/export` 读的是**旧** `ScanResultStore`，
  导出的是候选的原始字面值（`host:port/path`），**不是**归一化后的资产 —— 免得以后误以为它导出的是资产模型。

### P1 — Observability：结构化日志与关联 ID（本轮，方案第 19 节）

来源：`GetEverything_DSH执行方案_Flask版.md` 第 19 节，原文要求「逐步加入
`request_id` / `job_id` / `step_id` / `worker_id`」「日志必须结构化」、明令禁止
`print(f"api_key={key}")`、并给出目标事件形状（`job_step_finished` 带 `job_id` /
`step_id` / `tool` / `status` / `duration_ms`），最后一条是**「不要记录完整目标列表到公共日志」**。
第 25 节 P1 验收清单里的 `[ ] Observability 完成基础版本`、第 26 节执行顺序第 16 条「Observability」
即本条。此前全仓**零** `logging` 调用、零 `request_id`、零请求钩子。

授权：落在 `docs/DECISIONS.md` 第 2 节白名单「日志改进」+「补充与更新测试」内。

新增 `core/observability.py` 作为**唯一日志出口**（不引入 structlog / loguru 等新依赖，
底层就是 stdlib `logging`）：

```json
{"ts":"2026-10-01T02:27:38+00:00","level":"INFO","event":"job_step_finished",
 "job_id":"job_xxx","worker_id":"host-1234","step_id":"step_xxx",
 "tool":"httpx","target":"example.test","status":"succeeded","found_count":3,"duration_ms":1200}
```

四个关联字段用 `contextvars` 绑定（waitress 多线程下线程内独立、不串号），
「绑定一次、全链继承」，不需要逐层透传参数：

| 字段 | 绑定位置 | 还原位置 |
|---|---|---|
| `request_id` | `app.py:_bind_request_context`（`before_request`；入站 `X-Request-Id` 合法则沿用）| `teardown_request` |
| `job_id` | `jobs/executor.py:execute_job` 的 `observability.bind(job_id=…)` | `with` 退出 |
| `step_id` | 同上，步骤循环里的 `bind(step_id=…)` | `with` 退出 |
| `worker_id` | `jobs/worker.py:Worker.startup` | `Worker.shutdown` |

为让「成对」不可能被忘掉，`Worker` 支持 `with Worker(...) as worker:`（进入即 `startup()`、
退出即 `shutdown()`）；`main(--once)` 与 M7 的 E2E helper 都已改成这种写法。

接线（全部是加法）：

- `app.py`：`before_request` 生成/沿用 `request_id`；`after_request` 回写同名响应头；
  `teardown_request` 记 `http_request_finished`（**只记 `path`，不记 query**，query 可能带目标列表）；
- `core/errors_handlers.py`：三个 handler 各加一条事件 —— `request_failed`
  （4xx=WARNING / 5xx=ERROR）、`unhandled_exception`（ERROR，完整 traceback 仍走 `app.logger.exception`）；
- `api/jobs.py`：创建任务后记 `job_created`，带 `request_id`、工具名、**目标个数而不是目标列表**；
- `jobs/executor.py`：任务开始/结束各一条，每个步骤一条 `job_step_finished`（失败为 WARNING，
  取消路径也记 `job_finished`）；
- `jobs/worker.py`：`worker_started` / `worker_claimed_job` / `worker_job_finished` /
  `worker_job_exception` / `worker_idle_exit` / `worker_shutdown`；人读的 `Worker.log` 行保留，
  **同时**转成 `worker_message` 事件；
- `agent/action.py`：原 `print(f"[debug] … result={tool_result}")`（会把整份结果含目标列表
  倒进控制台）改成 `agent_plan_step` 事件，只记 `tool` / `args` / `ok` / `error`；
- `config.py` + `.env.example` + `README.md`：新增 `GEF_LOG_LEVEL`（默认 `INFO`）与
  `GEF_LOG_FORMAT`（默认 `json`，可选 `text`），文档里给出「拿 `X-Request-Id` 去 grep」的排障用法。

**脱敏与容量**（方案第 19 节「不记录完整目标列表」的落点）：

- 字段名命中 `api_key` / `token` / `secret` / `password` / `authorization` / `cookie` /
  `credential` → 值只记 `***`（这条优先于下面的 ID 规则，所以 `token_id` 也只记 `***`）；
- 其余文本先过 `core.runner_result.scrub_text`（沿用 M4 的唯一脱敏出口）；
- 单字段超 500 字符截断；list / dict 最多记 20 项。

两条必须知道的实现细节：

1. **关联 ID 不能被裸 token 兜底规则误打码**。`job_` + 32 位十六进制恰好 36 字符，
   正好命中 `_LONG_TOKEN_RE`（≥20 个连续 `[A-Za-z0-9_-]`）会被整串打成 `***`，
   结构化日志就自废武功。做法是按字段名区分：`*_id` 结尾按**标识符**原样记录；
   自由文本先把 `(job|step|…|req|wkr)_[0-9a-f]{6,}` 挖出来占位，脱敏后再还原。
2. **`duration_ms` 的语义差异**：事件里是真实墙钟测量（mock 步骤也用
   `time.perf_counter()` 补），但**库里的 `job_steps.duration_ms` 仍是 NULL**
   —— M4 契约「mock 不写假数据」的测试当场拦下了第一版把补出来的值写进库的实现。

刻意接受的副作用：`scrub_text` 的 `-Token VALUE` 规则会作用于自由文本，所以 401 的
`error_message` 在日志里是 `…请携带 X-Local-Token ***`。这是**失败即关闭**的取舍
（宁可多打码也不漏密钥）；完整原文仍可在 HTTP 响应体与 `audit_events` 里看到。

三条**源码守卫**（把方案第 19 节的「禁止」写成会失败的测试，用 AST 而不是字符串匹配）：
`print` 里不得出现密钥形状（唯一豁免启动横幅 `app._print_login_hint`）；
除 `core/observability.py` 与 `core/errors_handlers.py` 外不得自建 logger；
现存 50 处 `print` 按 `文件:函数` 粒度登记，新增一处即失败。

回归测试（+69，759 → 828）：

| 文件 | 例数 | 覆盖 |
|---|---|---|
| `tests/unit/test_observability.py` | 50 | 事件信封、contextvar 绑定/还原/**线程隔离**、`request_id` 校验（空格/过短/过长一律拒绝并重生成）、敏感字段只记占位符、自由文本脱敏、**关联 ID 不被误打码**、长字段截断、**容器最多 20 项**、两种格式、`configure_logging` 幂等/分级/读配置/配置坏掉也不炸、三条源码守卫 |
| `tests/integration/test_observability_chain.py` | 19 | Web 层（回写 `X-Request-Id`、逐请求唯一、**失败响应也带**、`path` 不带 query、401 不回显 Token 值）、执行层（每步一条、失败 WARNING、只记当前步骤目标）、worker 层（三层事件都带 `worker_id`、`with Worker` 退出后还原）、**端到端「一个 `job_id` 串起整条链」**（长方案 P1-5 的验收原话）+ 反向守卫（12 个目标的整份清单不得出现在任何日志字段里） |

> 最后那两条是**对着长方案的验收口径**写的，不是对着实现写的：
> 长方案 P1-5 的原话是「输入一个 `job_id` 可以串起整条执行链」，所以用例就照排障时的
> 真实动作来 —— 拿一个 `job_id` 去日志里捞，断言 Web 创建、执行开始/每步/结束、
> worker 领取/结束**六类事件一次全部出现且共用这一个 `job_id`**，创建事件还额外
> 带上了那一跳 HTTP 的 `request_id`。这是整轮 §19 改造最有说服力的一条证据。

另有两处**测试隔离**修正：

- `tests/conftest.py` 新增 autouse 的 `_reset_observability_context`，每例前后清空四个
  contextvar —— 兜底保证「某个用例漏还原」不会污染后续用例；
- `tests/integration/test_m7_local_e2e.py:_drain_worker` 改用 `with Worker(...)`。
  这条是**实测踩到**的：它原来只 `startup()` 不 `shutdown()`，`worker_id` 于是泄漏到
  同线程里的下一条用例（症状：观测测试断言 `current_context() == {}` 却看到
  `{'worker_id': 'm7-e2e-after'}`，而**单独跑该文件时全绿、全量跑才炸**）。

### P1 — §14 文档同步 + 导出格式 400 收口（本轮，方案第 14 节）

方案第 14 节要求 README 写清「环境要求 / 安装 / 启动 Web / 启动 Worker / 认证 / Scope /
Mock 模式 / Real 模式 / API / 测试 / 故障排查」，并**建议**增加 `docs/ARCHITECTURE.md`、
`docs/API.md`、`docs/DEPLOYMENT.md`。本轮把这三份补齐（安全文档本来就在仓库根
`SECURITY.md`，未新建 `docs/SECURITY.md`，三份新文档一律链接到 `../SECURITY.md`）。

**新增文档**（内容来自实际读码 + `app.url_map` 枚举 + `app.test_client()` 匿名探测，
不是照抄 README）：

| 文档 | 内容要点 |
|---|---|
| `docs/ARCHITECTURE.md` | 分层链路 Flask Web/API → Auth → Policy/Scope → Job → Worker → Runner → Artifact → Asset/Observation；「Agent 只能提出计划，不能直接执行」；Worker 与 Web 解耦；**明确记录当前技术选型被刻意冻结**（不做 Flask→FastAPI / SQLite→PostgreSQL / Worker→Redis+Celery / Jinja→React）；含 §19 Observability 一节 |
| `docs/API.md` | **逐条核对真实路由**：`app.url_map` 共 39 条规则 / 41 个方法绑定；`/api/*` **34 条**、其中**需管理员认证 24 条**、**匿名可读 7 条**、登录相关公开 3 条；非 `/api` 5 条。每条给方法 / 路径 / 鉴权 / 请求体或 query / 成功响应形状 / 主要错误码；含统一错误信封与状态码→`error_code` 映射表 |
| `docs/DEPLOYMENT.md` | 环境要求、依赖安装、`.env` 逐键说明（**不写任何真实密钥值**）、启动 Web、**单独启动 worker**（不启动则任务永远停在 `queued`）、一键脚本、健康检查、Mock/Real 双开关、三条基线命令、故障排查 |

**修一个真实缺陷：`GET /api/export?format=xlsx` 返回 500 而不是 400**

实测复现：`api/results.py:export_data` 把 `format` 直接转小写后交给
`exporter.export_results`，后者对非 `csv`/`json` 抛 `ValueError`，而调用点**没有捕获**
—— 异常一路冒到 `core/errors_handlers.py` 的全局兜底，对外变成
**500 `unknown_error`「服务内部错误」**。也就是说：**调用方参数写错了，却被报成服务端崩了**。
更糟的是 `agent/intent.py:guess_export_format` 会产出 `"xlsx"`，这条链是必然踩中的。

修法（最小必要）：

- `exporter.py` 新增模块级常量 `SUPPORTED_FORMATS = ("csv", "json")`，
  原先写在 `export_results` 里的字面量元组改为引用它；
- `api/results.py:export_data` 在**调用 exporter 之前**校验，非法值抛
  `BadRequestError` → **400 `bad_request`**，带
  `details.field="format"` 与 `details.supported=["csv","json"]`。

> 两处共用同一份 `SUPPORTED_FORMATS`，并有一条用例专门断言「校验清单与内部兜底是同一份」，
> 避免以后只改一边造成漂移。行为兼容性：缺省仍是 `csv`（有用例），`?format=CSV`
> 仍大小写不敏感（有用例）。

**顺带修正一批「文档与代码不一致」**（都是核对代码后确认的）：

| 位置 | 原来的说法 | 实际 |
|---|---|---|
| `README.md:43` | 「12 个 RESTful 接口：`/api/tools`、`/api/scan`、…」 | `/api/scan` **这个路由不存在**；`/api/*` 实际 **34 条** |
| `README.md:124` | 建议用 `curl …/api/tools` 做健康检查 | 健康检查是 `GET /health`（挂根路径、免登录、无副作用） |
| `README.md:153` | 匿名只读接口「以下三个」 | 实际 **7 个**（多出 `/api/databases`、`/api/tool/<n>/results`、`/api/exports`、`/api/export/<id>/download`） |
| `README.md:111` | 「需在 `web/templates/index.html` 部署前端模板」 | 模板与静态资源都已在仓库里，`GET /` 返回 200 |
| `README.md` Q6 | 「`/` 报 TemplateNotFound，index.html 是占位文件」 | M1 已修；该说法只在**旧 clone** 上成立（历史提交 `5853752` 删过 `web/templates/`） |
| `README.md:130` | 测试基线 `759 passed` | 本轮为 `838 passed, 2 skipped` |
| `README.md` 全文 | **0 次**提及 worker / `LOCAL_ADMIN_TOKEN` / `GEF_ALLOW_REAL_SCAN` / `local.db` | 照着 README 装完，`POST /api/jobs` 建的任务会**永远停在 `queued`**。已补「启动 Worker」「Mock / Real 模式」「环境要求」三节 |
| `api/tools.py` docstring | `table_name` / `record_count` | 实际键是 `table` / `result_column` / `category`（以 `storage.get_tool_databases()` 为准） |
| `api/scopes.py:110` 注释 | 404 会被转成 `error_code=bad_request` | 实际是 `not_found`（与 `core/errors_handlers.py` 的映射表一致，注释过时） |
| `api/scan.py:238`、`api/results.py:166` | 举例工具名含 `nuclei` | `RUNNER_REGISTRY` 里**没有 nuclei**，实际 17 个工具 |
| `SECURITY.md:51` | `FEROXBUSTER_CONFIG.wordlist` 是开发机绝对路径 | M5 已修为仓库相对路径 + `FEROXBUSTER_WORDLIST` 覆盖；同时把匿名只读清单补成 7 条、补记导出格式缺陷 |
| `AGENTS.md:3` | `CODEBASE_MAP.md`「560 行，含 22 条索引表」 | 实际 1800+ 行 / **27 条**；同时给第 26 节三个「高频坑」加了现状标注（第 1、2、5 条已部分/全部修复，第 3 条只在旧 clone 上成立） |
| `docs/CODEBASE_MAP.md` §9.7 | `assets` / `observations` 「未创建 … M5」 | 早已建成（P1 §8 两层模型），该行 stale，已更新 |

回归测试（+10，828 → 838）：`tests/integration/test_export_contract.py` 新增
6 条参数化非法 `format`（`xlsx` / `pdf` / `CSV2` / `json ` / `c s v` / `../csv`）断言 400 +
`details`，
另加「校验清单与 `SUPPORTED_FORMATS` 同源」「缺省仍是 csv」「大小写不敏感」三组。

### 修复 — Diff 属性别名归一（真实缺陷）

**症状**：方案第 10 节点名要求 Diff 的 `changed` 至少能指出 `status_code` / `title` /
`server` / `technology` / URL，但在**真实 httpx 链路上只有三项能报出变化**。

**根因**（已实测复现，不是读码推测）：`core/assets.py:DIFFABLE_ATTRIBUTES` 写的是
`server` / `technology`，而 `modules/httpx.py:_read_json_results` 实际产出的键名是
`webserver` / `tech`。用真实键名喂进 `_changed_attributes()` 返回 `{}` —— 属性白名单
永远匹配不上，`webserver` 从 `nginx` 变成 `apache` 也不会被报成 changed。

> 这个缺陷此前被测试掩盖：`tests/unit/test_assets.py` 的用例用的是**文档体例**的键名
> （`server` / `technology`），而不是 httpx 的真实键名，于是测试全绿而线上失效。

**修法**（用户选定：集中成表，禁止散落 `if`）：

- `core/assets.py` 新增模块级 `ATTRIBUTE_ALIASES`，把两侧写法映射到 canonical key：
  `server` / `webserver` / `web_server` → `webserver`；
  `technology` / `technologies` / `tech` → `technologies`；
  身份映射 `status_code` / `title` / `url`。
- `DIFFABLE_ATTRIBUTES` 改为 canonical key：`("status_code", "title", "webserver", "technologies", "url")`。
- 新增 `_canonical_attributes(data)`（键名小写 → 过别名表 → 每个 canonical 名只留第一个非空值），
  `_changed_attributes(before, after)` 先对**两侧**都做归一化再比较。
- **不变量**：`set(ATTRIBUTE_ALIASES.values()) == set(DIFFABLE_ATTRIBUTES)`，且每个
  canonical key 映射到自身 —— 这条写成用例，防止以后只改一边。

- `web/static/assets.js` 新增 `ATTRIBUTE_LABELS`，把 canonical key 渲染成中文标签
  （`webserver` → `Web Server`、`technologies` → `技术栈`），避免页面直接露英文键名。

回归测试（+9，838 → 847）：别名表与 `DIFFABLE_ATTRIBUTES` 同源且自映射、
按 httpx 真实键名做的参数化归一（3 例）、别名值相同时不报变化（3 例）、
白名单外属性不受别名表影响。全部既有断言改用 canonical key。

### P0-6（阶段一）— Application Service 入口收拢

用户本轮已授权 P0-6（见 `docs/DECISIONS.md` §3.2），并要求「先梳理现有调用拓扑，
再分阶段改造，不要一次性大范围重构」。本轮只做**阶段一：把编排收到一处**，
**不动数据模型、不动执行架构**；Agent 那条路留到阶段二。

**改前的实际拓扑**（读码确认，不是推测）：

```text
POST /api/jobs → api/jobs.py:create_job
   解析目标 → 校验工具 → 限流 → Policy 判定 → 模式解析 → 落库 → 审计 → 结构化日志
   （以上全部**内联在视图函数里**）

首页表单 POST / → app.py:index
   from api.jobs import _resolve_targets   ← 反向导入 api 层的私有函数
   + 把 Policy 判定抄了第二遍

Agent → agent/action.py
   tool_runner.run_tools(...) / HttpxRunner.run_scan(...)   ← 完全绕过 Job 链
```

同一套判定两份实现，改一处漏一处；而 Agent 那条路上，同一次「子域名收集」在主链上
是可审计、可取消、可重试、可复检 Scope 的 Job，在 Agent 链上却只是一次同步函数调用。

**新增 `core/application.py`（Application Service 层）**：

- `create_scan_job(...)` —— 创建扫描任务的**唯一**编排入口。校验顺序与历史逐条一致
  （刻意不重排，避免响应文案与错误码漂移）：目标非空 → 工具非空 → 幂等键合法 →
  工具受支持 → 目标数上限 → Scope/Policy → 模式开关 → mock 场景名 → 落库 →
  审计 + 结构化日志。
- `resolve_targets(...)` / `split_str_list(...)` —— 目标来源解析（显式列表 + 受控
  `upload_id`），从 api 层私有函数升为公开接口。
- `JobSubmission` —— 返回 job / scope / 实际入库的 tools 与 targets；`to_dict()`
  给出与 `POST /api/jobs` **完全一致**的响应体。

**边界刻意收窄**（这是本轮的关键设计决定）：

- **不碰 Flask**：认证、请求解析、HTTP 状态码仍由 `api/` 与 `app.py` 负责；
  服务层只接收已解析好的标量/列表，返回结构化结果或抛 `core.errors` 的业务异常。
- **不自实现 Scope 判定**：一律转交 `core.policy.validate_job_targets`。
- **不改数据结构**：只调用 `core.jobs` 已有的写入函数。

**调用方迁移**：`api/jobs.py:create_job` 缩成「认证 + 解析 JSON + 拼响应」；
`app.py:index()` 的扫描分支改调同一个入口，反向导入 api 层私有函数的写法消失。

回归测试（+27，847 → 874）：`tests/unit/test_application_service.py` —— 除了逐条覆盖
历史口径（参数缺失 / 未知工具 / 越界 / 超限 / 非法幂等键 / 非法 scenario / real 双开关 /
幂等 `reused` / 上传目标同样过 Scope），更关键的是三条**源码守卫**：

- `api/jobs.py` 里不得再出现 `validate_job_targets` / `create_job_with_status` /
  `normalize_idempotency_key` / `resolve_mode` / `audit.record(job_created)`
  —— 防止有人把编排抄回视图函数，让「统一入口」悄悄失效；
- `app.py` 里不得再出现 `from api.jobs import _resolve_targets`；
- `core/application.py` 里不得出现 `allowed_domains` / `allowed_cidrs` / `fnmatch`
  —— 服务层不得自己比较白名单。

外加两条等价性断言：HTTP 与直调服务层的落库结果逐字段一致、响应体字段集合相等。

> **阶段二未做（需先说明影响）**：`agent/action.py` 仍在直接调 `run_tools` /
> `HttpxRunner.run_scan`。把它接到 Job 链会让 **Agent 执行异步化** —— 回复里给
> `job_id` 而不是内联结果，`tests/unit/test_agent_boundary.py`（现在 monkeypatch
> `agent.action.run_tools` / `agent.action.HttpxRunner`）需同步重写。按用户约束
> 「若某一步需要改变核心数据模型或执行架构，先停下来说明具体影响再继续」，
> 阶段二开工前会先出影响说明。

### M6（收尾）— 一键环境自检 `scripts/check_env.py`

方案 M6 的最后一项。回答一个问题：**这台机器上，本机联调版能不能跑起来、能不能跑真任务？**

命令：

```powershell
python scripts/check_env.py            # 人读报告
python scripts/check_env.py --json     # 一行 JSON，便于脚本消费
python scripts/check_env.py --strict   # 有警告也按退出码 2 处理（CI 用）
```

**十四项检查，四类**：

- **解释器与依赖**：Python 版本（<3.10 fail，≥3.13 warn —— 依赖清单按 3.11 钉版本）；
  `requirement.txt` 逐项核对（缺失 = fail）；`requirement-dev.txt` 按 `>=` 判定
  （缺失 = warn，只是跑不了测试）。
- **运行期目录**：`results/`（工具产物与心跳）、`uploads/`、`exports/`、`backups/` 的权限。
- **`.env` 与安全开关**：弱/缺失 `SECRET_KEY`（warn，会话重启即失效）、
  空 `LOCAL_ADMIN_TOKEN`（warn）、`WEB_DEBUG=true`（**fail**，会暴露调试器）、
  非回环 `WEB_HOST`（warn）、`GEF_ALLOW_REAL_SCAN`（warn，改为正向提示）。
- **外部工具 / 数据库 / worker**：17 个工具在 PATH 上的可用数、两个 SQLite 库能否只读打开
  （应用库还要核对 10 张关键表是否齐全）、worker 心跳（ok / stale / missing）与队列计数。

**三条硬性质**（都有用例锁定，不是注释里的承诺）：

1. **只读**：不写任何文件、不建库、不发网络请求、不执行任何扫描工具。用例用「目录逐条目
   mtime + size 快照比对」验证；应用库不存在时只报 warn，并确认文件**真的没被创建**。
   目录权限只用 `os.access` 判定，**刻意不写探针文件再删** —— 那会在仓库里留痕
   （AGENTS.md 硬约束：脚本与测试不得污染 `results/`）。
2. **不泄密**：报告里不得出现 `SECRET_KEY` / `LOCAL_ADMIN_TOKEN` 的值。用例塞哨兵串后
   在**人读报告与 `--json` 两种输出**里各搜一遍，同时要求仍然报出「已配置」而不是装作看不见。
3. **退出码语义**：`ok` → 0、`warn` → 1、`fail` → 2，`--strict` 把 warn 也当 2（CI 用）。
   自检脚本的退出码错了，挂进 CI 等于没挂。

**设计取舍**：

- 版本比较自己实现 `_version_key`，**不引入 `packaging`** —— 它不在依赖清单里，
  而这个脚本要能在「依赖还没装」时也跑得动。顺带避开 `"3.10" < "3.9"` 为真的字符串比较坑
  （那会把合法的 Python 3.10 判成过旧），并处理 `2.0 == 2.0.0` 与 `1.0.0rc1 < 1.0.0`；
  `==` / `>=` / `~=` 等规格用 `_satisfies` 判定，认不出的规格**不误报**。
- 工具探测只用 `shutil.which`；用例把 `subprocess.run` / `Popen` / `check_output` 与
  `os.system` 全换成会抛异常的桩，证明它**不会启动任何子进程**。
- 每个 warn/fail 都必须带 `hint`（用例强制）：只说「有问题」不说「怎么办」的报告没人能用。

**顺手改动**：

- mypy 范围纳入 `scripts/`：`mypy app.py core api jobs storage.py modules scripts`
  → **63** source files（61 → 63），仍 0 error。
- `.github/workflows/ci.yml` 增加「环境自检冒烟」步骤：CI runner 上本来就没有 `.env`
  与那 17 个 Go 工具，warn（退出码 1）是**预期**结果，因此只把退出码 2 当失败 ——
  它证明的是「一台干净机器上也能跑完并给出可读结论」，而不是抛异常。
- `tests/unit/test_observability.py` 的 `_PRINT_ALLOWLIST` 登记
  `("scripts/check_env.py", "main")`：人读报告本就该走 stdout。
- **测试加载该脚本时必须先注册进 `sys.modules`**：它用了
  `from __future__ import annotations` + 冻结 dataclass，`dataclasses` 处理字符串注解时
  会去 `sys.modules[cls.__module__]` 查名字字典，没注册就拿到 `None`，
  直接 `AttributeError: 'NoneType' object has no attribute '__dict__'`。

回归测试（+26，874 → 900）：`tests/unit/test_check_env.py` —— 版本比较与规格判定、
退出码三态与 `--strict`、`--json` 可解析且字段齐全、哨兵串不进任何输出、
`WEB_DEBUG`/非回环绑定的判定、四个目录「跑完一模一样」、库不存在时不建库、
坏库与缺表分别报 fail、检查维度不可悄悄变少、warn/fail 必须带 hint、
以及「探测过程不得启动子进程」。

### M7（收尾）— 测试报告 `docs/TEST_REPORT.md`

方案 M7 的最后一项交付物：一份**测试报告**。

它按 DSH 执行方案第 23 节的里程碑格式组织（里程碑 / 分支 / 提交 / 改动文件 / 关键改动 /
新增测试 / 执行命令 / 测试结果 / 已知问题 / 未完成项 / 下一阶段），并按同一节的硬要求
把「**已验证**」与「**仅代码审查、尚未实测**」分成两个互不混淆的小节。

**报告的价值在「没测什么」，且每条都带复现方式**（不是「感觉没测」）：

- **41 条方法绑定里 40 条被真实命中**：用一次性探针包装
  `flask.Flask.full_dispatch_request` 跑全量得到命中清单，再与 `app.url_map` 求差。
  唯一没被走到的是 `GET /api/tool/<tool_name>/results`（读旧库，本机该库 20 张表全为 0 行，
  测试从设计上不碰它）；另记一条「命中但不是声明路由」的
  `GET /api/export/exp_x/../../etc/download`，那是穿越防护用例**故意打的 404**。
  > 后来随各里程碑新增路由，口径已是 **50 条绑定 / 49 条命中**（Phase 4 重跑），
  > 但「唯一没被走到的是那一条」这个结论一直没变 —— 见
  > [`docs/TEST_REPORT.md`](docs/TEST_REPORT.md) §8.2。
- **91 个业务 `.py` 里 10 个测试源码从未提及**，全部在 `agent/`
  （`providers/*`、`system_prompt`、`strategy_templates`、`target_ranker`、`plan_state`、
  `model_result`、`skills/osint_recon`）。根源是既知事实：Agent 路径不调大模型，
  这 10 个模块**没有调用方** —— 所以不是「懒得测」，而是没有可测的运行时行为；
  真接线时测试应与接线同一轮写。
- **`ANONYMOUS_READABLE` 只列了 5 条，而 `SECURITY.md` 说 7 条**：
  `/api/tool/<n>/results` 与 `/api/export/<id>/download`（后者靠 `test_export_contract.py`
  的匿名用例间接覆盖）目前没有被参数化用例直接钉住，登记为缺口并给出两条建议。
- 其余缺口：无覆盖率数字（`coverage`/`pytest-cov` 不在依赖清单，未擅自引入）、
  运维脚本（`run_local.ps1` / `install_*.ps1|sh`）无自动化测试、
  前端 JS 只有 `node --check` 与服务端字符串断言、真实外部扫描按硬约束**从未执行**。

**方案第 15 节测试矩阵逐项对照后无缺项**（API 6 项、安全 7 项、Worker 5 项、Runner 7 项、
平台 Windows + Linux），代表用例逐条列出。

**顺手修正**：`README.md` 里过期的 `warn 5` 改为实测的 `warn 4 / fail 0`，
并点明四条 warn 依次是 `.env 文件` / `SECRET_KEY` / `LOCAL_ADMIN_TOKEN` / `worker 心跳`。

### 修测试隔离：测试不再往仓库运行期目录里写

**这是写上面那份报告时抓出来的第三个真实缺陷**，也是本轮唯一的行为改动
（+1 用例：900 → 901）。逐文件跑测试、对运行期目录做逐文件 SHA-256 快照比对后，
实测出三处**稳定复现**的泄漏：

| 泄漏 | 成因 | 单跑一次的后果 |
|---|---|---|
| `exports/` 多一个空 CSV | `tests/unit/test_agent_boundary.py` 的 fixture 只 patch 了 `UPLOAD_DIR`，而 `_tool_export_results` 走 `exporter` 的**模块级** `EXPORT_DIR` | 文件数 +1 |
| **仓库** `results/local.db` 多 3 行 | `tests/unit/test_security_baseline.py` 的两个上传用例直接调 `core_uploads.save_upload()`，它内部 `db.ensure_schema()` + `db.transaction()` 用的是仓库库路径 | `audit_events` / `uploads` 各 +3 |
| `results/worker_heartbeat` 被刷新 | `test_m4_runner_result.py` / `test_observability_chain.py` / `test_jobs_executor.py` 起真实 `Worker`；`jobs/worker.py` 从 `config` 导入的 `OUTPUT_DIR` 是自己的副本，patch `core.health` 对它无效 | mtime 被改写 |

**根因是保障挂错了位置**：原先只有 `tests/conftest.py:app_module` 一个夹具在 patch，
而**绕过它的用例（只用 `local_db` 或不用任何夹具）根本不受约束**。

改法（三件一起才成立）：

1. 新增 autouse 夹具 `tests/conftest.py:_isolate_runtime_dirs` ——
   `config.LOCAL_DB_CONFIG["path"]` + `core_db.reset_schema_cache()`，
   外加 `exporter.EXPORT_DIR`、`core_uploads.UPLOAD_DIR`、`core_health.OUTPUT_DIR`、
   `jobs.worker.OUTPUT_DIR`、`core_artifacts.ARTIFACT_DIR` 五处模块属性。
   **autouse 是关键**：不再依赖用例「记得」要哪个夹具。
2. `config.py`：`OUTPUT_DIR` 支持 `GEF_OUTPUT_DIR` 环境变量改道
   （与既有的 `GEF_SCAN_DB_PATH` / `LOCAL_DB_PATH` 同规格）。
   子进程读不到父进程的 monkeypatch，这是**唯一**能拦住 `python -m jobs.worker`
   往仓库写心跳的办法；`test_jobs_executor.py` 的 kill/restart 用例据此传参。
3. 新增回归锁 `tests/unit/test_repo_layout.py::test_autouse_fixture_redirects_every_runtime_path`
   —— 逐条断言两个库路径与五处模块级目录都不在仓库目录下。
   夹具被删或漏项时立刻变红，而不是等下次提交才发现工作区脏了。

**验证方式**：全量跑一次，对 `results/`、`exports/`、`uploads/`、`backups/`
做跑前跑后的**逐文件 SHA-256 比对** —— 完全一致（`results` 5 个文件、`exports` 81 个、
`uploads` 3 个、`backups/` 不存在）。
**未动**：任何业务逻辑、Scope/Policy/审计/认证、数据库结构。
`app_module` 与 `local_db` 的既有 patch 原样保留（与 autouse 夹具叠加安全）。

### P0-6（阶段二）前置件 — Agent 同步 → 异步影响说明

**本轮零代码改动**，只新增一份评估文档 [`docs/AGENT_ASYNC_IMPACT.md`](docs/AGENT_ASYNC_IMPACT.md)。
依据：用户授权 P0-6 时的约束「**若某一步需要改变核心数据模型或执行架构，
先停下来说明具体影响再继续**」（`docs/DECISIONS.md` §3.2）。

**最重要的一个减负结论**：六个 handler 里只有 **2 个**是方案第 6 节说的
「实际扫描动作」（`subdomain` / `httpx`）；`summary` / `view_results` / `alive_results`
是只读查询、`export_results` 是导出登记，都不产生执行权，**不在方案第 6 节验收范围内**。
迁移面因此从 6 个收窄到 2 个。

**顺带查实的一处现存越权通道**（这是本次评估里最值得注意的发现）：
`tool_runner.py` 与 `modules/httpx.py` 全文**没有任何 `resolve_mode` /
`real_scan_enabled` / Scope 引用**（实测 grep 零命中）。这意味着 **Agent 这条路
不需要 `GEF_ALLOW_REAL_SCAN=true`、也不需要 Scope，就能真实外发扫描请求**；
而主链 `POST /api/jobs` 与首页都要过「环境开关 + `scope.require_active_scan()`」双重门槛。
所以 P0-6 阶段二的收益不只是「可审计」，它把一条**实际存在的越权通道**收回同一道门。
（按硬约束本项目从未用 Agent 打过真实外部目标，这是修潜在缺口，不是事故复盘。）

文档其余内容：九条逐项影响（I-1～I-9，含「上传目标上限从无限制收窄到 20」
「httpx 的 direct_domain / stored_subdomains 回退 / tech_detect 三条能力传不进 Job 链」
「`scope_id` 必须贯穿三层，Agent 全包当前零 Scope 概念」）；五个必改文件与预估；
`test_agent_boundary.py` 的 6 处 patch 目标逐条处置（含建议删掉已失去被测对象的
`test_agent_httpx_returns_metadata_items`）；三条缺失能力的补救选项 A/B/C；
以及唯一一个**需要用户拍板**的问题——Agent 的只读 handler 是否同轮改读新库。

**未动任何代码**：`agent/action.py` 994 行原样未改，`docs/DECISIONS.md` 未改
（授权口径无需变更，仍是「先出影响说明」这一步）。

### 公网授权测试模式体验版（本轮，方案第 5～11 节）

依据：`docs/milestones/GetEverything_公网授权测试模式体验版方案.md`。
一句话目标：**给「扫自己已获授权的公网目标」一个正规入口，而不是靠人手改 `.env` 与 Scope。**

新增：

- `core/tool_registry.py`：17 个 runner 的**工具权限元数据**
  （`tool_name` / `risk_level` / `internet_allowed` / `default_enabled` / `reason`）
  + 三档扫描策略模板（资产发现 / Web 基础检查 / 自定义；三个模板的缺省节奏均为 `light`，
  见「下一阶段体验优化 Phase 3」）。
  **核心不变量：没登记 = 禁止公网** —— `assert_tools_internet_allowed()` 对未知工具直接拒绝，
  绝不默认放行；公网白名单恰好是 `{httpx, subfinder}`（方案第 8 节）。
- `core/projects.py`：授权测试项目（创建 / 读取 / 关联既有 Scope / 按 Scope 反查项目）。
  **只新增 `projects` + `project_scopes` 两张表，`scopes` 表零改动**（方案第 10 节 / DECISIONS-E）。
- `api/projects.py`：`POST/GET /api/projects`、`GET /api/projects/{id}`、
  `POST /api/projects/{id}/scopes`（关联**已存在**的 Scope，幂等；不创建 Scope）。
- `api/public_scan.py`：`POST /api/public-jobs`（202 + `queued`）、
  `GET /api/scan-center`（页面元数据，**不下发任何目标清单**）。
- `app.py:scan_center()` + `web/templates/scan_center.html` + `web/static/scan_center.js`：
  扫描中心页，三块 = 项目 / 创建任务 / 任务列表（方案第 7 节）。
- `core/application.py:create_authorized_public_job()`：授权公网任务的**唯一**编排入口。
- `scripts/verify_public_scan.py`：可复跑的实机验收探针（只打 RFC 6761 保留域 `example.test`）。

变更：

- `core/db.py`：`init_schema()` 里新增两张表与两个索引（纯加法，回滚即 `DROP TABLE`）。
- `core/ids.py`：`PREFIX_PROJECT = "proj"` + `new_project_id()`。
- `core/audit.py`：新增 `project.created` / `project.scope_attached` 两个事件类型。
- `web/static/app.js`：把状态 / 步骤状态 / 错误码三张文案表挂到 `window.GEF_UI` 供扫描中心复用，
  **同一个 `error_code` 在两个页面不会显示成不同的话**。
- `web/templates/{index,assets}.html`：导航加「扫描中心」入口。

**闸门顺序**（每一步不过就立刻返回，不产生落库副作用）：
项目存在 → `scope_id` 属于该项目 → 策略模板解析 → 公网白名单校验 →
（默认 `mode=real`）转交 `create_scan_job` → 目标 / 工具 / 幂等键 / 上限 / Scope-Policy / 环境开关 → 落库 → 审计。

**一处刻意取舍**：真实扫描开关没开时**报错**，而不是静默退回 mock 给一份假数据。
「以为打了真实目标、其实拿到编的数据」比直接报错危险得多（有专门用例锁住）。
`mode=mock` 仍可显式指定，用于演练，闸门一条都不少。

**方案第 2、6 节的两条红线都有源码守卫**：`api/public_scan.py` 里不允许出现
`build_runner` / `run_tools` / `RUNNER_REGISTRY`；公网入口必须**复用** `create_scan_job`，
不得另写一条 Policy 判定（`test_public_scan_api_never_calls_runners_directly` 等）。

修复（测试隔离，**非被测代码缺陷**）：

- `tests/conftest.py` 对 `GEF_ALLOW_REAL_SCAN` / `GEF_LOG_FORMAT` 由 `setdefault` 改为**赋值**。
  本机新增 `.env` 后暴露出两处「测试跟随开发机配置」的失败：
  `test_m2_security.py` 的 `real_scan_enabled is False` 断言被 `.env` 顶掉、
  `test_observability.py` 的 JSON 解析拿到 `text` 格式。
  `load_dotenv()` 默认不覆盖已存在的环境变量，因此赋值即可钉死；
  需要 real 模式的用例仍用 `monkeypatch.setenv` 自行打开并在结束时回滚。

**未动**：`scopes` 表结构、Scope/Policy 判定逻辑、认证授权、既有 API、同步 Runner 链路、
Agent、`pyproject.toml`。**未引入**任何新依赖、React、Redis。
**未对任何真实外部目标发起扫描** —— 本轮全部实机验收都打 `127.0.0.1` 与保留域 `example.test`。

### 下一阶段体验优化（本轮，方案 Phase 1～4）

依据：《GetEverything_下一阶段体验优化与公网扫描能力演进方案》（本机过程材料，不入库）。
产品原则一句话：**保留安全边界，但降低用户操作复杂度** —— 该方案第 8 节写着
「不绕过 Policy / 不绕过 Scope / 不删除审计」，本轮的每一段改动都在这条线上。
三个阶段各自独立提交，可独立回滚。

#### Phase 1 — UI 清理（`e94b180`）

只改展示层，**不动任何闸门**。

- 全部实体 ID 从**可见文案**里消失：用户看到的是「学校官网 / www.example.cn / 已授权」，
  而不是 `scope_9f3c…`。实体 ID 仍然存在，但只作为表单 `<option value>` 与请求体字段
  （不可见）—— 提交链路一字未改。新增 `scopeLabel()` / `projectLabel()` /
  `describeScopeTargets()` / `scopeStateLabel()` 四个翻译函数，文案统一从这里出。
- 清掉 `proj_…` 裸 ID 列、`Scope N 个`、把授权说明塞进 `title` 等后台术语。
- 目标清单继续来自需登录的 `GET /api/scopes`，**不塞进** `/api/scan-center` ——
  「这条接口不下发目标清单」的既有约定与测试保持有效。

#### Phase 2 — 公网授权测试入口（`510fa41`）

- **要解决的问题**：旧实现下「越界 / 范围没开 `active_scan` / 环境总开关没开」
  这三种完全不同的情况都表现为同一个 `403 scope_violation`，用户只能靠读错误消息反推。
- 新增 `core/authorization.py`：**只读试算**。`check_target()` / `check_targets()`
  返回「目标落在哪些已授权范围内、每个范围什么状态、还缺哪一道闸门」，
  `blocker` 五档（`invalid_target` / `no_scope` / `not_authorized` / `scope_inactive` /
  `env_disabled`）。三条设计：① 匹配**复用** `Scope.match_target`（与 Policy 同源，
  不可能出现「试算说能过、真提交过不了」）；② **只读**，不写库、不写审计、不发网络；
  ③ `TargetCheck.eligible` 单独建模 —— 命中排除列表的范围不算「可执行」。
- 前端 `GET /scan-center` 改成四步：输入目标 → 确认授权范围 → 选择工具 → 执行模式与提交。

#### Phase 3 — Scan Profile = 工具组合 + 节奏（本轮）

方案第 5 节说「不要固定扫描流程」，并要求引入 **Scan Profile**。本阶段的判断是：
Scan Profile **不能只等于「换个工具组合」** —— 同一组工具在别人的资产上可以打得多快，
才是使用者真正关心的第二个问题。因此把「节奏」提升为与工具并列的一维。

- 新增 `core/pace.py`：档位 `light`（低频）/ `normal`（常规）；`PACE_LABELS` /
  `PACE_DESCRIPTIONS` 作为中文文案的**单一事实源**（页面、错误消息、接口文档同源）。
- **只能收紧**：`resolve_pace(模板档位, 请求档位)` 中任一为 `light` 即 `light`。
  三个策略模板一律 `light`，因此请求体里写 `pace=normal` **改不回来**。
- **非法值报错而非静默回退**：`normalize_pace()` 对 `"low"` 这类拼错直接
  `ValueError` → HTTP 400。写了拼错的档位却拿到常规档，是本功能最危险的错法。
  读**库里**的历史脏数据才用宽松的 `coerce_pace()`。
- **真的降速，不是文案**：
  - `LIGHT_TOOL_BUDGET` 把 `subfinder` 压到 `-t 5 -rl 3`、`httpx` 压到
    `-threads 5 -rl 10`（`modules/subfinder.py` / `modules/httpx.py` 的
    `build_command()` 只在 `config` 里真有 `rate_limit` 时才拼 `-rl`）；
  - `apply_to_runner()` 写进 Runner 的 `config` **副本** —— 绝不原地改模块级配置对象；
  - 低频档在**真实**步骤之间留出间隔（默认 1.5 秒，`GEF_PACE_LIGHT_STEP_DELAY_SEC`
    可调；`tests/conftest.py` 钉为 0，让测试不为礼貌间隔付墙钟）。等待分片进行，
    期间取消仍最多晚 1 秒生效，并在长等待前续租。
- **`normal` 与引入前逐字节一致**：不覆盖任何参数、不产生任何等待。不带模板的历史入口
  （`POST /api/jobs`、首页表单）缺省即 `normal`，老调用方不会突然变慢（有用例锁死）。
- **节奏不落成 `jobs` 表的新列**（那属 DB 结构变更，DECISIONS §1 E 限纯增量）：
  写进 `job.created` 事件 detail + 审计 detail，执行期由
  `core/jobs.py:pace_of_job()` 读回。这是**必然**而非偏好 —— worker 是独立进程，
  且任务可能被 retry 或换一个 worker 重启，节奏必须属于任务本身。
- 前端：每张策略卡片上写明**节奏**（不只是工具组合）；说明文字由
  `GET /api/scan-center` 的 `paces[]` 下发，**前端不写死任何文案**；
  提交时原样转发 `pace`（前端给错也放松不了任何东西 —— 合并规则只在服务端有一次）。
- **一处刻意的废弃**：曾尝试新增 `modules/registry.py:build_scoped_runner()`
  （第二条能带节奏的构造路径），**已移除**。`build_runner(tool_name)` 是测试替换真实
  Runner 的**唯一**接缝（`monkeypatch.setattr`），多一条构造入口就多一个
  「假 Runner 没被替换、真去执行外部命令」的机会。最终改为「构造归 registry、
  降速归 `core.pace.apply_to_runner`」两步，并由用例锁住这个分工。
- **节奏不是安全闸门**：它不参与、也不放松 Scope / `active_scan` /
  `GEF_ALLOW_REAL_SCAN` / 公网白名单中的任何一条。

**未动**：数据库核心结构（`scopes` / `jobs` 等既有表零改动）、既有 API 的语义、
同步 Runner 链路、Agent、Policy / Scope 判定逻辑。**未引入**任何新依赖、React、Redis。
**未对任何真实外部目标发起扫描** —— 本轮 `real` 模式用例全部把
`modules.registry.build_runner` 换成假 runner，目标是 RFC 6761 保留域 `example.test`。

#### Phase 4 — 结果体验：从 Job 导向结果（本轮）

方案第 6 节把 Phase 4 写成一句话：「结果体验：**从 Job 导向结果**；展示：发现资产；
服务；技术栈；风险信息。」本阶段要解决的正是这句话：**做完一次任务之后，用户看不出
到底看到了什么** —— 详情页只有「步骤 × 工具 × 结果数」，而成果（资产 / 观测）散落在
另一页，且没有任何一处会把它们整理成人能读的形状。

- 新增 `api/jobs.py:GET /api/jobs/<job_id>/results`（**本轮唯一新增路由**）。
  一次请求给出四段：`assets` / `services` / `technologies` / `risk_hints`，
  外加 `counts`（五类**截断前**真实数量）与 `notes`（必须原样展示的说明句）。
  四段形状统一为 `{total, items, truncated}`，`assets` 额外带 `by_type` 与
  `type_labels` —— 形状统一是刻意的：前端只写一份渲染函数，测试也只锁一种结构。
- 新增 `core/findings.py`（纯函数）：把 `assets` + `observations` 派生成四段。
  不碰 sqlite、不碰 Flask、不读配置、不发网络请求；别名表**复用**
  `core/assets.py:ATTRIBUTE_ALIASES`（不维护第二份），并在出参里下发
  `type_label` / `kind_label` / `level_label` —— 中文文案只有服务端一份，
  前端不写死（同 Phase 3 对节奏说明的处理）。
- **`core/assets.py:list_job_assets()`**：`assets` 表**没有** `job_id` 列
  （同一台主机被十次任务看到也只有一行），所以从 `observations` 反查。
  口径与 `/diff` 同源：**只看本次任务自己的观测**，不看该资产历史上被谁见过 ——
  否则「这次扫到了什么」会被历史观测污染，而那正是本阶段要消灭的歧义。
  于是**零 schema 变更**：`jobs` / `assets` / `observations` 三张表一字未改。

**关于「风险信息」的口径（本阶段最重要的一个决定）**

方案第 5 节提到 `nuclei`，但本项目的 `nuclei` 在
`core/tool_registry.py:KNOWN_UNAVAILABLE_TOOLS` 里、`internet_allowed=False`、
不在 `RUNNER_REGISTRY`，全仓也没有任何 CVE / CVSS / severity 数据。
**因此本阶段不假装有漏洞扫描**，而是把「风险信息」如实降级为
「**从已有观测里读出来的、值得人工看一眼的事实**」：

- `level` 只有 `info` / `notice` / `attention`，语义是「值不值得人工看一眼」，
  **不是**危险度（刻意不用 low / medium / high —— 用了就等于暗示「我们评估过危险程度」）；
- 九类提示全部是**可观察事实**：明文 HTTP、目标自身返回 5xx、401/403（存在访问控制）、
  未跟随的跳转、`Index of /` 标题、中间件默认欢迎页标题、版本号横幅、
  **未做 HTTP 探测的主机**、**终态失败的步骤**。后两类是「覆盖缺口」——
  它们把「没看」与「没问题」分开，这正是最容易骗到人的地方；
- `notes` 里**恒有**一句免责说明：「没有提示」不等于目标没有问题，
  「有提示」也不等于发现了漏洞。前端必须显示它。
  这条**不是「零提示时才补一句」**：真实链路里零提示几乎从不出现
  （只跑 subfinder 时「有子域没做 HTTP 探测」就会产生一条），
  若只在零提示时才算，反而最需要说明的那次拿不到它。
- mock 模式单独一句说明：mock **不产生观测**，所以「服务 / 技术栈 / 风险提示」
  三段为空是预期行为，不是采集失败。

**前端**：`web/static/app.js` 的任务详情面板新增「结果」区，四段各自成节。
容器**同步插入、内容异步填充** —— `renderDetail` 后面紧跟的 `loadArtifacts` 也是异步的，
若两者都往 `body` 上 append，谁先回来谁排前面，页面顺序会随机跳动。
风险级别徽标复用既有 `.risk` 系列配色，但**标签文案来自服务端 `level_label`**，
并有源码守卫禁止前端写死中文（`test_app_js_does_not_hardcode_risk_level_wording`）。

**未动**：`jobs` / `assets` / `observations` 表结构（**零 schema 变更**）、既有 9 条
`/api/jobs*` 接口的语义、Scope / Policy 判定逻辑、公网工具白名单
（**仍是 `subfinder` + `httpx`**，未因本阶段放开任何一条）、同步 Runner 链路、Agent。
**未引入**任何新依赖、React、Redis。**未对任何真实外部目标发起扫描** ——
本阶段用例全部走 mock，或把 `build_runner` 换成假 runner，目标是保留域 `example.test`。

**一处刻意不做的**：没有把四段结果做成「漏洞报告」。做不出来的东西不渲染 ——
页面上没有任何「严重程度」「CVE」「修复建议」字段，且有用例断言这些字段不出现在出参里
（`test_summarize_never_emits_a_severity_or_cve_field`）。

### 下一阶段规划方案（本轮，Phase 1～）

依据：《6GetEverything-下一阶段规划方案》（仓库根 `6GetEverything-下一阶段规划方案.md`，
本机工作单，不入库）。方案第 18 节把目标写成一句话：
**「开放能力给用户，限制风险在后端」** —— 前端可以放开工具选择，后端一条闸门都不放松。

#### Phase 1 — 前端体验重构（`548d196`）

方案第 5.2 节的四步流程落地：`输入目标 → 确认授权状态 → 选择工具 → 创建任务`。

- 「先选项目 → 再选范围」两个内部概念合并成一步「确认授权状态」；步骤 2 的三行摘要
  （目标 / 授权状态 / 授权资产）**只回显服务端试算结论**，前端不比较 `active_scan`、
  不读环境变量。
- 全部实体 ID 从可见文案里消失（退到 `<option value>` 与请求体）；新增
  `scopeLabel()` / `projectLabel()` / `describeScopeTargets()` / `scopeStateLabel()`
  四个翻译函数作为文案唯一出处。
- 新增授权确认勾选（方案第 7 节），文案里明写「这是使用者确认，不是安全边界」；
  `authorization_confirmed` 原样转发给服务端**仅供审计**，不参与任何闸门。
- 工具清单从「只在自定义模式下出现」改为**始终可见**，数据全部来自服务端
  `/api/scan-center`，前端一个工具名都不写死（方案第 9 节）。

**未动**：Policy / Scope 模型 / Job 模型（方案第 14 节 Phase 1 的「不修改」列）。

#### Phase 2 — Tool Registry（本轮）

方案第 9 节要求「不要把工具写死在前端」，并给出一个 `GET /api/tools` 的例子条目
（含 `description` / `category` / `risk`）。本阶段把它落成一个**真正的注册模型**，
同时修掉两条**当时确实存在的**参数处理缺陷。

1. **工具注册模型**（`core/tool_registry.py`）

   - `ToolPolicy` 新增 `description`（一句话说清「它能干什么」）与 `tool_group`
     （能力分组），17 个工具全部标注；`ToolGroup` 与 `TOOL_GROUPS` 承载**分组本身**
     （`key` / 中文名 / 这一栏的说明），中文文案只有服务端一份。
   - **字段名刻意叫 `tool_group` 而不是方案例子里的 `category`**：本仓库里
     `category` 已经有三重含义（`storage.TOOL_DATABASES[*]["category"]`、
     `modules.base.BaseRunner.category`、`api/tools.py` 从 runner 读它）。
     再借它当分组名，会造出一个**同名异义**的字段 —— 看接口的人永远说不清
     `category=subdomain` 到底指「观测类别」还是「能力分组」。分组与观测类别
     是两件事，字段名不共用。
   - **方案第 8 节那张五栏表是示意，不是要求填满**：`技术识别` / `漏洞检测` /
     `内容发现` 本阶段确实没有可跑的工具，因此**如实返回空栏位**，
     而不是把别的工具挪进去凑数。空栏位前端显示「本阶段暂无可用工具」——
     藏掉栏位会让使用者以为是自己没找到。
   - 新增 `group_tool_policies()`：按 `TOOL_GROUPS` 顺序分组，空分组保留；
     遇到未登记的分组**直接抛 `ValueError`**（而不是静默丢进某个兜底栏），
     因为那只会在「加了分组字段却忘了登记分组表」时发生。

2. **工具列表 API**

   - `GET /api/tools`：条目在历史键名（`name` / `category` / `database`）之外，
     补上 `tool_name` 与全部注册表字段（`description` / `tool_group` /
     `tool_group_label` / `risk_level` / `risk_label` / `internet_allowed` /
     `default_enabled` / `reason`），并新增 `groups`。`name` 与 `tool_name`
     恒等 —— 保留 `name` 是不改历史契约（脚本在用），给出 `tool_name`
     是不引入第二套命名。
   - `GET /api/scan-center` 新增 `tool_groups`（扁平 `tools` 仍是「真能跑」的工具，
     与分组**同源同集**）；`nuclei` 不在其中，它只由 `restricted_tools` 承载。
   - 两个接口的注册表字段来自**同一个** `ToolPolicy.to_dict()`，
     有用例逐字段比对 —— 各写一份取数逻辑正是「改一处漏一处」的来源。
   - `api/tools.py` 里未登记工具不再抛异常，而是**保守降级**
     （`risk_level="high"`、`internet_allowed=False`）：一个匿名只读列表接口
     为了缺一个字段而整页 500，比少一个字段更糟。

3. **Job tools 参数标准化**（`tool_runner.load_tools` + `api/scan.py`）

   这一段修的是三个**真实缺陷**，不是理论问题：

   - **静默回落**：`tools` 为空时 `load_tools` 回落到
     `SCAN_CONFIG["enabled_runners"]`（当时是 `["amass"]`）—— 用户没选任何工具，
     系统自己挑一个重的去扫。现在 `None`（未指定）与 `[]` / `""`（**明确不要**）
     严格分开：前者是 CLI 语义仍回落，后者返回空列表，由调用方明确拒绝。
     `POST /api/run` 因此不再用 `payload.get("tools") or payload.get("tool")` 判空
     （`or` 会把 `[]` 和 `""` 折叠成 `None`，正好落进回落分支），改用 `in` 判断。
   - **逗号分隔字符串被当成一个工具**：`"subfinder,httpx"` 此前被包成
     `["subfinder,httpx"]`，必然报「存在不支持的工具」—— 同一个请求体从
     `/api/jobs` 进得来、从 `/api/run` 进不来。现在两处口径一致。
   - **不去重**：`total_steps = len(targets) * len(tools)`，同一个工具写两遍
     会让任务凭空多出一倍步骤（并重复执行同一工具）。现在全链去重保序。
   - 新增 `tool_runner.normalize_tool_names()` 作为**全仓唯一一份**参数规范化实现
     （去空白、丢空项、逗号拆分、去重保序）；工具名仍然逐一过
     `get_supported_runners()` 校验，**白名单一条都没放松**。

4. **前端按能力分组动态展示**

   - 分组栏位名、每栏说明、每个工具的用途说明**全部来自服务端**；
     源码守卫禁止前端出现任何工具名与分组名的字符串字面量
     （`test_scan_center_js_never_hardcodes_tool_names`、
     `test_scan_center_js_renders_groups_from_server_metadata`）。
   - 未接入的 `nuclei` 按**它自己声明的 `tool_group`** 归进「漏洞检测」栏，
     因此前端不需要写死「nuclei 属于漏洞检测」这类映射。
   - 一处**兜底**：扁平表里有、分组表里没有的工具宁可多显示一行也不静默丢掉。

**未动**：公网工具白名单（**仍是 `subfinder` + `httpx`**，未因本阶段放开任何一条）、
`ScanStrategy` / `STRATEGIES` / `resolve_strategy_*`、Policy / Scope 判定逻辑、
`jobs` 表结构（**零 schema 变更**）、同步 Runner 链路、Agent。
**未引入**任何新依赖、React、Redis。**未对任何真实外部目标发起扫描** ——
本轮用例全部走 mock，或把 `build_runner` 换成假 runner，目标是保留域 `example.test`
与 RFC 5737 保留段。

#### Phase 3 — 公网授权测试完善（本轮）

方案第 14 节列的五项：**操作者记录 / 授权备注 / 扫描策略 / 限速配置 / 超时配置**。
五项**全部**不落成 `jobs` 表的新列 —— 它们写进那条 `job.created` 事件的 detail，
再由 `*_of_job()` 读回（`pace` 已证明可行的那条路）。**零 DDL、零迁移脚本**，
并有**反向守卫**测试锁着这件事：`jobs` 表里出现这六个列名就会红。

1. **操作者记录**（`core/jobs.py:normalize_operator`）

   - `POST /api/public-jobs` 与 `POST /api/jobs` 都新增可选字段 `operator`：
     空值退化为 `local-admin`（**不留空串** —— 审计里「谁提交的」必须有答案），
     非法形状（`true` / 数组 / 对象）或超长（> 120）→ 400 `details.field="operator"`。
   - 落三处：`job.created` detail、审计 detail、结构化日志的 `operator=`。
     方案第 7 节点名的四要素（`operator` · `target` · `timestamp` · `scope_id`）
     因此齐全 —— 时间戳由 `audit_events.created_at` 提供。
   - **它是「自称」而不是已验证身份**：本仓库的认证是一个布尔态的本地管理员
     Token（`session[SESSION_KEY] = True`），`audit.actor` 也硬编码为 `local-admin`。
     记它的价值在**问责留痕**，不在权限。真正的多用户身份属方案第 15 节
     「多租户 / SSO」暂缓项，本轮**没有**偷偷做一半。

2. **授权备注**（快照，不是第二份可写字段）

   - 取**项目上那一份授权说明的当前值**写进 `job.created`。项目说明事后被改，
     这条任务所依据的仍是创建当时的原文。上限 500（与 `core.projects` 一致）——
     快照**不得**成为绕过项目字段校验的第二条写入口。

3. **扫描策略**

   - 请求/项目解析出的模板 key 一并落到任务上（老入口无模板 → `null`）。
     只记「跑了哪些工具」不够，还得能回答「用哪个模板跑的」。

4. **限速配置 / 超时配置**（`core/job_limits.py`，新增模块）

   - 请求字段 `rate_limit`（每秒请求上限，`1 ~ 100`）与 `timeout_seconds`
     （单步超时秒数，`1 ~ SCAN_LIMITS["process_timeout"]`，本机默认 120）。
   - **唯一硬规则：只能收紧，不能放松。** 合并用 `min` 而不是覆盖，且发生在
     `pace` **之后** —— 低频档已经压下来的 `httpx -rl 10` 不会被请求里的
     `rate_limit=50` 顶回去。`timeout_seconds` 映射成 `process_timeout`
     （`modules/base.py:_timeout_seconds()` 读的键），最后还会与本机上限取一次 `min`。
   - **越界一律 400，不静默夹到边界**：写了 `100000` 却拿到 `100`，与
     `core.pace` 里「写了拼错的档位却拿到常规档」是同一种危险错法 ——
     使用者以为自己已经设好了。`RATE_LIMIT_MAX` 取 100 的理由同上：上界若高于
     工具自身默认速率（subfinder 默认 150），`rate_limit=1000` 就变成了**放松**限速。
   - 覆盖写的是 Runner 实例上的 `config` **副本**，不是模块级配置对象本身
     （原地改会污染同进程内后续所有任务与页面上的工具状态）。
   - **报错文案必须带字段名**：`core.application` 正是靠文案把 400 定位到
     `details["field"]`。修之前 `timeout_seconds="abc"` 会被报成 `rate_limit`
     有问题（`_as_int` 不区分字段）—— 使用者盯着一个自己没填过的框找错。
     现在这条不变量有单测锁着。

5. **接口与前端**

   - `GET /api/scan-center` 新增 `limits`（可填范围 + 中文说明）。前端据此
     **动态生成**输入框，不写死字段名与上下界 —— 与工具清单、分组、节奏同一口径；
     源码守卫禁止 `"rate_limit"` / `"timeout_seconds"` 以字符串字面量出现在 JS 代码里。
   - 留空 = **不加这个键**，而不是传 `0` 或 `null`：服务端把「没指定」与
     「指定了非法值」分得很开，传 `0` 会被判越界 —— 而用户什么都没填。
   - `GET /api/jobs/<id>` 平铺出 `operator` / `strategy` / `project_id` /
     `authorization` / `limits`；任务详情页显示它们，其中授权确认明写
     「使用者确认，不是安全边界」。
   - **`authorization_confirmed` 刻意不做闸门**：一个可被脚本置真的 JSON 布尔值
     不构成安全边界，把它当闸门只会制造「勾了就等于放行」的错觉。
     两种取值都能建任务，这条有测试钉住。

**未动**：`scopes` / `jobs` / `assets` / `observations` / `projects` 表结构与数据
（**零 DDL**）、Scope / Policy 判定逻辑、认证授权、公网工具白名单
（**仍是 `subfinder` + `httpx`**）、`ScanStrategy` / `STRATEGIES`、Agent、
`pyproject.toml`、`.env`。**路由总数未变**（48 规则 / 50 绑定 / 42 个 `/api/*`）——
本轮只给既有接口**加字段**，老入口 `POST /api/jobs` 的响应形状一字未改
（仍不出现 `project_id` 等公网专属字段）。
**未引入**任何新依赖、React、Redis。**未对任何真实外部目标发起扫描** ——
本轮用例全部走 mock，或把 `build_runner` 换成假 runner，目标是保留域 `example.test`
与 RFC 5737 保留段。

#### 第 6 节 — 目标自动匹配授权资产（`9224bc3`）

方案第 6 节写着「**系统后台：** 调用 `resolve_scope(target)`，自动判断」，
第 16 节① 把完整链路写成「输入目标 → **自动匹配 scope** → 选择工具 → 创建 job」。
四步流程、只读试算、工具选择中心都已落地，但「试算出结论之后**谁**把那份结论变成
下拉框里的选中项」一直没做 —— 用户仍要自己在步骤 2 再挑一次。本轮补上这一格。

- `web/static/scan_center.js:applyMatchedScope()`：四条口径 ——
  **只认服务端结论**（候选直接取试算响应的 `eligible_scope_ids`）、
  **取交集且唯一才选**、**有歧义就不猜**（保持原选择 + 如实说明）、
  **不覆盖用户的显式选择**。
- **不是扩大授权范围**（方案第 11 节 ⛔ 列表第一条）：目标集合、Scope 模型、
  Policy 全部一字未改；被选中的资产是用户自己已建好、且**服务端**已判定覆盖目标的
  那一个。方案第 6 节「**禁止**为了体验删除 Scope 校验」一字未动 ——
  真正的判定仍只在 `core/policy.py:validate_job_targets()` 里做一次。
- 顺带收敛掉**三处「第二条授权判定」**：`renderCheckResults()` /
  `renderConsentSummary()` / `refreshConsentScopeLine()` 原先各自比较
  `item.verdict === "allowed"` 与 `item.status === "ready"`；现在统一走
  `isEligibleMatch()`（读服务端 ID 集合）。三处判同一件事、判法还不一样，
  正是「改一处漏一处」的典型形态。
- 新增 3 条用例（含 §16 ① 的整条链路端到端）。

**未动**：`agent/`。方案第 16 节③ 与第 12 节要求 Agent 只能 `create_scan_job()`，
但 `agent/action.py` 仍直接调 `tool_runner.run_tools` 与 `HttpxRunner.run_scan`；
这与你上一轮对 P0-6 阶段二「先不开工」的答复一致，缺口**如实登记**在
`docs/DECISIONS.md` §3.9 第 1 条 —— 没有用 `xfail` 或「断言 Agent 确实绕过」
的测试去把缺口粉饰成预期。

#### 第 13 节 — 后端安全边界的测试缺口回填

方案第 13 节把「前端可以开放工具选择」的前提写成四行必须保留的边界
（`6GetEverything-下一阶段规划方案.md:390-399`）。§3.7～§3.9 三轮落地后，
这四行里有**两行实现是真的、却没有入口级用例**。本轮先实测确认实现，再补上
能证明它真的的用例 —— **实现一行未改**，两条新用例写下的当次就通过。

- **Job 审计六项**：`test_job_audit_records_the_six_required_fields` 按第 13 节
  原话逐项查 —— `job_id` = `audit_events.target_id`，`time` = `created_at`，
  `operator` / `target` / `tools` / `mode` 在 `detail`；并额外断言 `job.created`
  事件与审计记录对 `tools` / `mode` 的说法一致（两处同源，不能漂移）。
  补测前只钉住了 `operator` / `targets` / `scope_id` / `created_at`。
- **禁止任意字符串调用工具**：`test_unregistered_tool_name_is_rejected_by_the_registry`
  用 `strategy="custom"` + `tools=["definitely-not-a-tool"]` 打公网入口，断言
  400 + `unknown_tools`，且 `jobs_store.list_jobs() == []`（闸门在创建任务**之前**）。
  补测前只覆盖**已登记但被禁**的工具（`nmap` / `dirsearch` / `naabu` /
  `feroxbuster` / `katana`），「从未登记」这条字面场景没有入口级用例。
- **注册表两个读出点的分组视图**：`test_both_registry_readouts_agree_on_the_groups_view`
  逐分组 `==` 比对 `/api/tools.groups` 与 `/api/scan-center.tool_groups`，并断言两边
  `vuln` 栏都为空、`nuclei` 只从 `restricted_tools` 走。此前只比对过扁平清单的
  8 个字段，**分组集合没有守卫** —— 一旦有人把其中一个调用点改成默认值
  `list_all_tool_policies()`，`vuln` 栏会一个接口空、另一个接口冒出 `nuclei`，
  而扁平清单比对不会红（`nuclei` 本来就不在扁平清单里）。

为什么这两条值得单独一轮：`tools` 与 `mode` 在 `jobs` 表里也有一份，很容易被
当成「审计表里重复了」删掉，一旦删掉就再也分不清「这次开的是哪些工具、是真扫
还是 mock 演练」；未登记工具名则是「禁止任意字符串调用工具」的唯一直接证法 ——
`assert_tools_internet_allowed` 的单元测试证明了闸门函数本身，但只有入口级用例
能证明公网入口真的走到了它，而不是被别的偶然路径挡住。

**未动**：实现代码、`agent/`、全部数据库表结构与数据（零 DDL）、Scope / Policy
判定逻辑、认证授权、公网工具白名单（仍是 `subfinder` + `httpx`，`nuclei` 仍为
`internet_allowed=false` 且只作受限展示）、路由总数（48 规则 / 50 绑定 /
42 个 `/api/*`，未新增未删除）。本轮只改一个测试文件。

#### 执行期双开关复检 + Phase 1 四处审计缺口收口

方案第 13 节把「Real Mode 控制」写成必须保留的边界。创建期确实是三道闸门
（`core/application.py:327` 目标校验 → `:329` `resolve_mode()` 读 `GEF_ALLOW_REAL_SCAN`
→ `:332` `scope.require_active_scan()`），**但这三道都在「任务落库那一刻」就结束了**。
`jobs/executor.py` 的 `_execute_real_step` 此前只复检了 Scope 成员资格，
**既不 import `core/safety.py`、也不看 `active_scan`** —— 于是存在这条缝：

```text
real 任务入队（三道闸门全过）
  → 排队 / 失败重试 / worker 重启补做期间，开关被关掉、或 active_scan 被收紧
  → worker 取到任务，仍然把真实外网请求发出去（执行期没看这两件事）
```

等于「开关只管下单，不管出餐」。本轮收口（`jobs/executor.py:148-172`，+42/−4）：
调用 Runner **之前**按创建期的**同一顺序**再各读一次，顺序是有意的 ——
① `validate_step_target` → ② `real_scan_enabled()` → ③ `require_scope().require_active_scan()`
→ ④ 工具是否已登记。越界 target 连「有没有开开关」都不该被回答；而已经删掉 Scope
的任务报出的必须是「越界 / 范围不存在」，不能被一句「开关没开」盖过去 ——
后者会让人以为是环境配置问题，而真正的变化是授权范围没了。

- 错误码用 `scope_violation`（不是 `permission_denied`）：与 `core/safety.py:59-63`
  创建期口径一致，复用前端已有的「目标超出授权范围」文案，**前端零改动**；
  而 `permission_denied` 在 `modules/base.py:855-868` 已被退出码 126 占用。
- **步骤级 `scope_violation` ≠ 任务级 `scope_violation`**：全部步骤复检失败时
  `aggregate_status()` 给的是 `unknown_error`。这是既有聚合语义，本轮**没有**顺手改。
- 新增 2 条用例（注入假 runner，零外部流量）；并**修好** 1 条既有用例 ——
  `test_real_step_rechecks_target_still_in_scope` 此前**依赖「执行期不看开关」这个缺陷**
  才通过，修好之后它自己开开关，断言仍然不变。

**同轮把 Phase 1 审计查出的四处缺口一并收口**（均为「实现与页面说的不是同一件事」类）：

- **提交的是快照而不是当前输入**：`bindJobForm` 原来写
  `lastTargets.length ? lastTargets : splitList($("job-target").value)` ——
  「检查授权 → 改输入框 → 直接创建任务」提交的是**改前**的目标，而页面上的绿灯
  说的是改后的那个站。现在 `currentTargets()`（`scan_center.js:139`）是唯一事实来源，
  提交、摘要、自动重算三处都改读它；新增 `checkIsFresh()` / `invalidateCheckResult()`
  与输入框 `input` 监听，改了就让旧结论失效并要求重新检查。
- **没写协议的 URL 被当成坏网段**：第 6 节写「用户输入：域名、IP、URL」，
  但 `www.example.test/a/b`（地址栏直接复制的那种）此前掉进 CIDR 分支，报
  「非法的 CIDR: www.example.test/a/b」。`core/scope.py:68-87` 新增 `elif "/" in text:`
  分支，看 `/` **两边**再决定：`192.0.2.0/99` 仍如实报 CIDR 错，`example.test/24`
  仍按网段形状保留（**不**静默当域名），只有两边都不像网段时才取主机那一段。
- **`scope_id` 漏进可见文案**：资产详情「所属范围」直接渲染 `scope_9f3c…`，
  违反方案第 4 节原则 2。新增 `assets.js:scopeLabelById()` 读本页已渲染的下拉选项
  翻成名称，查不到时给「（该授权资产已不在列表中）」而**不是**把 ID 漏出去。
- **死代码**：`index.html` 的 `{% if scan_report %}` 块永远渲染不出来（调用点一直传
  `None`），且是全仓唯一一处把 `scope_id` 写进可见文案的地方 —— 删模板分支 +
  `app.py` 的 `scan_report` 参数与实参；`scan_center.html` 的 `#scope-list`、
  `app.css` 的 `.sc-scope-title` 都无任何引用，一并删除。
- 另把第 6 节的目标标签从「域名 / IP / 网段」补成「域名 / IP / 网段 / **URL**」——
  标签少写一种输入，用户就会以为贴 URL 会被拒。

**本轮新增 5 条 / 修复 1 条**（`tests/unit/test_jobs_executor.py` 27 → 29、
`tests/unit/test_scope.py` 35 → 39、`tests/integration/test_public_scan_mode.py`
119 → 122、`tests/integration/test_assets_api.py` 28 → 29、
`tests/integration/test_m2_page_scan.py` 10 → 11）。

**变异验证**：把新增的开关检查与 `require_active_scan()` 复检两处改成 `if False:`
→ 两条新用例同时 FAILED；还原 → PASSED；工作树无残留变异。

**一条刻意没改的**（已登记 `docs/DECISIONS.md` §3.11.5 第 1 条等你拍板）：老入口
`POST /api/jobs` 的 `mode="real"` **不装公网工具白名单** —— 实测 `tools=["nmap"]`
返回 **202** 并落库，而同样参数打 `/api/public-jobs` 是 **400 + `blocked_tools`**。
原因是 `assert_tools_internet_allowed()` 全仓只有一个生产调用点
（`core/application.py:573`，公网编排）。这是「老入口要不要也变成公网入口」的
产品口径问题，加上它会改变既有 API 可用行为（属破坏性变更），故**如实登记、未改**。

**未动**：`agent/`、全部数据库表结构与数据（**零 DDL**）、`core/policy.py`（一行未改；
`core/scope.py` 只改输入归一化，匹配语义未动）、认证授权、审计字段集合、
公网工具白名单（**仍是 `subfinder` + `httpx`**）、路由总数（48 规则 / 50 绑定 /
42 个 `/api/*`，未新增未删除）。
**未对任何真实外部目标发起扫描** —— 全部用例走 mock / 注入假 runner，
目标是 `example.test` 与 RFC 5737 保留段。

#### 第二轮只读审计：四处守卫 / 口径缺口收口

§3.11 之后又做了一次对 Phase 1～3 的只读对照审计，这次是**三条独立子代理视角**
（Phase 1 / Phase 2 / Phase 3 各一条，彼此不共享上下文）。三条都给出了判定表与
可复现证据，报出来的问题分两类：**守卫强度不足**（看着在守、实际漏守）与
**口径不一致**（同一个请求体从两条链进来得到两种解释）。本轮收口四处：

- **`tools` / `tool` 的 `or` 折叠**（`api/jobs.py:106`、`api/public_scan.py:96`）：
  原先写 `payload.get("tools") or payload.get("tool")`，`or` 把「**明确给了空选择**」
  与「没给这个键」当成同一件事。实测（探针读库不读响应体）：
  `{"tools": [], "tool": "subfinder"}` 从 `/api/run` 进来是 **400**，
  从 `/api/jobs` 进来是 **202 且落库 `tools=['subfinder']`、真的去扫**；
  `{"tools": ""}` 同样。这与 §9.25.3 记的老毛病**同因不同向**
  （那次是空选择被折叠后回落配置默认值，这次是让**别名**顶上来）。
  修法与 `api/scan.py:168-171` 逐字一致：`payload.get("tools") if "tools" in payload
  else payload.get("tool")` —— **判据是「有没有给这个键」，不是「这个键的值真不真」**。
  别名本身保留（有反向用例守着别一起删掉）。新增 4 条用例，
  且每条都同时断言 `list_jobs() == []`：只看状态码不够，「400 但留下一条 queued 任务」
  同样是越权执行。
  **改前 / 改后是跨提交实测的，不是推理**：`git worktree add --detach <tmp> c2a83b1`
  检出修复前的提交，同一份探针在两种库上跑 —— 改前
  `{"tools": [], "tool": "subfinder"}` 与 `{"tools": "", "tool": "subfinder"}` 都是
  **202 且库里真的多出 queued 任务**，改后同一脚本给 400、库为零；
  `{"tool": "subfinder"}` 两侧都是 202（别名未受影响）。两次探针都钉死
  `GEF_ALLOW_REAL_SCAN=false` 且走 mock，用完的工作树与临时目录已删除。
  详录 `docs/TEST_REPORT.md` §14.2.1。

- **前端「工具名不写死」的守卫只覆盖 7/18**：`test_scan_center_js_never_hardcodes_tool_names`
  的字面量黑名单此前是**手写的 7 个**。探针复刻该守卫逻辑后往 `scan_center.js` 注入
  `var HARDCODED = "dnsx";` → **守卫放行**；`amass` / `gospider` / `waybackurls` /
  `dirsearch` 等 **11 个**同样全部漏过。这不是实现缺陷，而是**守卫形同虚设** ——
  它看起来在守方案第 9 节，实际只守住三分之一。现在黑名单**从注册表派生**
  （`get_supported_runners() | KNOWN_UNAVAILABLE_TOOLS`），并自检读出点 ≥ 18，
  防止派生源坏掉让守卫静默变成空循环。注册表以后加一个工具，守卫自动覆盖它。

- **首屏兜底文案是后端描述的逐字副本**：`scan_center.html:164` 的 `#strategy-note`
  初始文本逐字抄了 `core/tool_registry.py:557` 的 `description`。它会被 JS 覆盖，
  肉眼几乎看不见；但后端改描述它就**静默过期**，而当时**没有任何守卫**盯着它
  （节奏说明有守卫，策略说明没有）。现在 HTML 只留中性占位，新增守卫按
  「服务端当前下发的每一段描述逐字都不在页面里」判定 —— 后端改描述它仍成立，
  谁再抄一份它立刻红。

- **资产详情的 UUID 与数据库字段**：方案第 4 节原则 2 点名的三样里，`scope_id`
  上一轮已收口，本轮补后两样 —— `assets.js` 详情标题此前写
  `idEl.textContent = asset.id`（上屏 `asset_3f9c…`），摘要此前有一行「规范化键」
  铺开 `host|example.com` 这种**列值**。现在标题给「类型 · 值」，摘要不再铺开；
  实体 ID 仍留在 `data-asset-id` 与接口里供脚本定位，只是不上屏。

**另两项如实登记、未改行为**：① `normalize_tool_names()` 的 docstring 与 §9.25.3
此前称「全仓唯一一份参数规范化实现」，实测 `core/application.py:95 split_str_list()`
是另一份且不是同一个函数 —— 端到端一致靠的是**去重与 registry 校验只有一个收口点**
（`load_tools`），不是实现唯一，措辞已校正；② `rate_limit` 的**生效面**实测只覆盖
**2/17** runner（`subfinder` / `httpx` 的 `build_command()` 才会追加 `-rl`），
而 `timeout_seconds` 是 **17/17**（唯一读取点 `modules/base.py:425`）——
公网白名单**恰好就是那两个**，所以公网链上是 2/2 全覆盖，但白名单外是**静默 no-op**
（`config` 写了、命令行里没有），而老入口的 real 模式可以走到那些工具。
未改覆盖面（属独立工作），只在 `docs/API.md` §6.3 与 `docs/CODEBASE_MAP.md` §9.30.5
写明，避免把「记录了限速」读成「限速了」。

**本轮新增 8 条 / 加强 2 条**（`tests/unit/test_tool_parameters.py` 17 → 21、
`tests/integration/test_public_scan_mode.py` 122 → 125、
`tests/integration/test_assets_api.py` 29 → 30）。其中公网入口那 2 条是**同一处折叠的另一格**：
`api/public_scan.py` 有同一行 `or`，但它的后果与老入口不同 —— 公网链多一层
`resolve_strategy_tools()`，默认模板下别名接不接管结果都一样（模板本来就要那两个工具），
**只有在 `custom` 模板下**（工具由请求体决定）`{"tools": [], "tool": "subfinder"}`
才会从 400「自定义模式必须显式选择至少一个工具」变成 **202 并真的去扫**
（跨提交实测，见 `docs/TEST_REPORT.md` §14.2.2）。

**「新增用例在修复前是红的」已实测**：把只改测试的文件复制进 `c2a83b1` 的独立工作树跑 ——
`test_custom_strategy_does_not_fall_back_to_the_tool_alias` → `assert 202 == 400` 失败；
`test_jobs_does_not_fold_...` 的 empty-list / empty-string 两例同样失败
（第三例 `"  ,  "` 改前也通过，与 §9.30.1 表里「巧合一致」那一格对得上）；
两条「别名仍可用」的反向用例修复前后都通过 —— 证明收口没有顺手删掉别名。

**变异验证**：在 `scan_center.js` 里插一行 `var MUTATION_PROBE = "dnsx";`
（恰是**旧黑名单漏过**的那一类）→ 加强后的守卫 **FAILED**；删掉还原 → **PASSED**；
工作树无残留变异。「加强前会放过、加强后会红」本身就是收口的证据。

**真起实例的第二次确认**（源码守卫的诚实边界，补 §3.12.7）：本项目没有浏览器测试，
上面那两条前端收口只是**源码级**守卫。因此本轮最后用**独立临时库**真起了一次实例，
核对**服务端发出的字节**而不是源码：`/health` 200（17/17 工具可用）、
`scripts/verify_public_scan.py` 七步全过（退出码 0）、`POST /api/jobs` 三形态
`400 / 400 / 202`、`/scan-center` 正文含中性占位且**不含**任何策略描述、
`/static/assets.js` 正文不含 `asset.canonical_key` 与 `textContent = asset.id`、
`/scan-center` 与 `/assets` 均 200。
实例的目标只有 RFC 6761 的 `example.test`，请求要么 `mock`、要么在闸门处被拒，
**无外部流量**；用完已停、临时目录已删。**仍然没做**：没点浏览器，
所以「JS 在真实 DOM 上跑出来的样子」仍未验证 —— 如实写在
`docs/TEST_REPORT.md` §14.4 与 `docs/DECISIONS.md` §3.12.7。

**未动**：`agent/`（一行未改）、全部数据库表结构与数据（**零 DDL**）、
`core/policy.py`、`core/scope.py`（本轮未改它）、认证授权、审计字段集合、
公网工具白名单（**仍是 `subfinder` + `httpx`**）、路由总数（48 规则 / 50 绑定 /
42 个 `/api/*`，未新增未删除）。

#### §1～§18 逐节对照审计 + 第 6 节 BUG 索引表行号全量刷新

这一轮**不加功能、不改行为**，只做两件事：把方案逐节与仓库对齐，把索引表与代码对齐。

**① 方案 §1～§18 逐节对照（40 条）** —— 逐条打开方案原文与被引用的源码 / 测试 /
文档核对，结论：**没有「漏做」的能力项**。所有未实现或部分实现项（§10 三档扫描模式、
§12 Agent 边界、§16③ Agent 边界用例、`rate_limit` 只覆盖 2/17 runner、§9 的读出点与
字段名差异、§13 老入口白名单）**此前都已登记为「刻意不做 / 待你拍板」**。
真正**既未实现、又未被任何文档登记**的只有一条：方案 §3.1 的
「一次扫描任务创建流程 **30 秒以内**完成」（方案 `:123`）—— 全仓无计时、无埋点、
无验收用例。它是**体验指标而非功能或安全缺口**，本轮**不改行为**，只如实登记，
并把三个选项写进 `docs/DECISIONS.md` §3.13.1。

**② §17 提交九字段格式的实测** —— 对 `origin/main..d603334`（审计当时的 18 个提交）
逐个 `git log -1 --format=%B`，按「**逐行解析第一个冒号前的字段名**」口径判定
（**合并标题也算命中**，如 `未做事项 / 风险：`）：**只有 8 个是 10/10 齐全**，
7 个缺 1～9 个字段（`8e7b8ba`/`0f5422d`/`1a53b4f`/`c2a83b1` 各 5/10、
`5417b4a` 3/10、`17dc1bd` 7/10、`652b26f` 8/10），
3 个完全没有九字段头部（`9224bc3`、`a646742`、`d057a18`）。
**这不影响任何功能**，但「方案第 17 节要求每阶段按九字段写提交」这句在仓库里
目前不成立，如实记下。

▶ **这个数字必须带口径引用，而且换口径能差一倍**：同为行首但**禁止**字段名后带括号说明
只剩 **4/18**；行首 + 允许括号说明是 **7/18**，但它会把 `1746f41` **误判成 8/10**
—— 那条提交的正文里明明写着 `未做事项 / 风险：`（两个字段名同行、用 `/` 分隔）。
四种口径的对照表与逐条明细见 `docs/CODEBASE_MAP.md` §9.31.3。

▶ **方法论上必须承认的局限**：正则审计**既有假阴性**（合并标题）**又有假阳性**
（正文里提一句字段名就算「有」），所以它**只够证明「格式漂移普遍存在」这个命题**，
**不够逐个提交宣判「它不合格」**。要点名某条提交，请打开那条 `git log` 自己读。

收尾后同口径实测为 **23 个提交 / 13 个齐全**（`c0f02d4`、`3191a75`、`cca156d`、
`b308a0b`、`3c9e5ce` 都 10/10；该数字每提交一次就动一次，全部现跑而非推算）；
且实测发现**该数字还受提交粒度影响**（把两个琐碎文档提交折回一个，分母少 1
而分子不变，齐全率立刻好转）—— 所以「每阶段独立提交」与「九字段齐全」在琐碎
文档提交上会互相干扰，**该合的就合**。
**历史提交不重写**（会改掉已出现过的 SHA），选项见 §3.13.1。

**③ BUG 索引表 29 条行号全量刷新** —— `docs/CODEBASE_MAP.md` 第 6 节是
`AGENTS.md` 指定的**改 bug 第一入口**，实测已大面积失信：**22/29 条行号漂移，
其中 9 处落进别的函数体内**（如第 4 条把 `get_tool_results` 的 `category` 失效分支
指到了 `get_view_overview`），照它排查会被引到完全不相干的代码。已逐条按当前
**LF 行号**改写，并对 3 条「说法已不存在」的（第 7 条 `nfl.com` 默认值、
第 16 条 `with self._get_connection()`、第 21 条 `record_count`）改为现状。
同一份文档里 §7.2 / §7.3 / §8 / §9.9 的同类漂移一并校正
（`http-x` **不是笔误**、`busy_timeout` **已补**、连接泄漏**已修**、
「只有 2 个测试文件」实为 **40 个**、「19 张表」实为 **20 张**、
`results/`/`uploads/`/`SecLists/` 在本仓库**都是 0 个跟踪文件**）。

**行号口径本身也实测了**：同一文件用 `Get-Content` 默认编码读出 **267 行**、
加 `-Encoding UTF8` 读出 **320 行**（偏差 **16.6%**）—— 这正是旧表系统性偏小的原因，
已写进第 6 节表头与 `docs/DECISIONS.md` §3.13.4。

**④ 两处源码改动，都是 docstring（零行为变化）**：
`storage.py:702-711` 的 `Args.category` 从「暂未在专属表查询中使用」改为
「**形参保留但当前不生效**」并指出替代入口 `get_view_results(category=...)`；
`api/tools.py:7-11` 的 `/api/databases` 描述从「（表名、记录数等）」改为
「（工具名 / 表名 / 结果列 / 分类）+ **不含任何计数**」，并指明
`get_tool_database_overview()` **没有 API 出口**。

> **顺带发现并处理的一个坑**：`storage.py` 原本带 UTF-8 BOM，本轮一次整文件读写
> 曾把它吃掉（表现为 diff 第一行出现 `-﻿"""` 的假改动）。已恢复并复核 `HEAD` blob
> 与工作树前三字节一致。**给后来者**：改这个文件请用定位替换，不要整文件重写。

**未动**（与本轮前段相同）：`agent/`、数据库结构（**零 DDL**）、认证授权、
审计字段集合、公网白名单（仍是 `subfinder` + `httpx`）、路由总数、`.env`。
**未对任何真实外部目标发起扫描**：本轮只跑本地测试与只读核对，连临时实例都没起。
**仍未推送**：等你确认后先跑七项推送前安全审计，再显式 `git push origin main`
（**不加 `--tags` / `--follow-tags`**）。

### 测试与验收基线

```text
$ python -m ruff check .     # All checks passed!
$ python -m pytest           # 1313 passed, 2 skipped, 0 failures
$ python -m mypy app.py core api jobs storage.py modules scripts   # Success: no issues found in 72 source files
$ node --check web/static/{app.js,assets.js,scan_center.js}        # 三个前端脚本语法通过
$ $env:LOCAL_ADMIN_TOKEN="<取自 .env>"; python scripts/verify_public_scan.py   # 实机验收探针：项目 → Scope → 关联 → 三道拒绝 → mock 任务，全部符合预期
```

> 探针的凭据与地址**都从环境变量读**（`LOCAL_ADMIN_TOKEN` / `GEF_VERIFY_BASE`），
> 脚本里不写死任何值；缺失时以退出码 2 退出并打印设置方法。

基线演进：公网体验版 `1004` → Phase 1 UI 清理 `1009` → Phase 2 `1036` → Phase 3 `1091`
→ Phase 4 `1149` → 规划方案 Phase 1 `1159` → 规划方案 Phase 2 `1189`
→ 规划方案 Phase 3 `1290` → 第 6 节自动匹配授权资产 `1293`
→ 规划方案第 13 节缺口回填 `1296` → 执行期双开关复检 + Phase 1 四处缺口 `1307`
→ **第二轮只读审计：四处守卫/口径缺口收口 `1315`** → **本轮（§1～§18 逐节对照审计 +
第 6 节行号刷新 + 两处 docstring 校正）：仍是 `1315 collected / 1313 passed / 2 skipped`
—— 本轮只改文档与注释，**不新增也不删除用例****。

本轮 +8（`tests/unit/test_tool_parameters.py` 17 → 21、`test_public_scan_mode.py`
122 → 125、`test_assets_api.py` 29 → 30），另有 **2 条既有用例被加强**（函数数不变、
断言变严：工具名守卫改为从注册表派生、资产页 UUID/列值拆出独立守卫）。

上一轮（§13）的 +11 里，**5 条是新增、1 条是「修复一条此前依赖缺陷才通过的既有用例」**。
逐文件差额由 `git worktree add --detach <tmp> 1746f41` 检出基线后两个工作树各跑一遍
`--collect-only -q` 求差得到（1296 → 1307），不是推算：

| 文件 | 基线 `1746f41` | 本轮 | 差额 |
|---|---|---|---|
| `tests/unit/test_jobs_executor.py` | 27 | 29 | +2 |
| `tests/unit/test_scope.py` | 35 | 39 | +4（参数化 3 例算 3 条） |
| `tests/integration/test_public_scan_mode.py` | 119 | 122 | +3 |
| `tests/integration/test_assets_api.py` | 28 | 29 | +1 |
| `tests/integration/test_m2_page_scan.py` | 10 | 11 | +1 |
| 全量 | **1296** | **1307** | **+11** |

上一轮 +3 全部落在 `tests/integration/test_public_scan_mode.py`（116 → 119）：
`test_job_audit_records_the_six_required_fields`、
`test_unregistered_tool_name_is_rejected_by_the_registry`、
`test_both_registry_readouts_agree_on_the_groups_view`。

上上轮 +3 也落在同一个文件（113 → 116）：
`test_check_endpoint_exposes_the_auto_match_contract`、
`test_scan_center_js_auto_selects_the_scope_from_server_verdict`、
`test_target_to_job_flow_uses_the_auto_matched_scope`。

规划方案 Phase 3 的 +101 构成（用 `git worktree add --detach <tmp> ce0ef22`
把规划方案 Phase 2 单独检出后**两个工作树各跑一遍 `--collect-only -q` 求差**得到，不是推算）：

| 文件 | 基线 `ce0ef22` | 本轮 | 差额 |
|---|---|---|---|
| `tests/unit/test_job_limits.py` | —（新文件） | 59 | +59 |
| `tests/unit/test_jobs_store.py` | 72 | 90 | +18 |
| `tests/integration/test_public_scan_mode.py` | 89 | 113 | +24 |
| 全量 | **1189** | **1290** | **+101** |

**没有一条既有断言被放松**：`test_normal_pace_leaves_the_runner_config_untouched`
（常规档不得改写 `config`）、`test_public_job_request_cannot_relax_the_template_pace`
（请求放松不了模板档）、`test_legacy_job_api_still_works`（旧响应体不含
`project_id`）、`test_service_delegates_to_single_job_entry`（公网编排必须复用
`create_scan_job`）等全部原样保留并通过。

测试报告的完整版见 [`docs/TEST_REPORT.md`](docs/TEST_REPORT.md)（测了什么 / 没测什么 / 为什么没测）。

### 已知仍未处理（不属 M0～M4 范围）

- 匿名只读接口：按 `docs/DECISIONS.md` D 有意保持，已用契约测试锁定。
  **共 7 条** —— `/api/tools`、`/api/databases`、`/api/results`、`/api/tool/<n>/results`、
  `/api/export`、`/api/export/<id>/download`、`/api/exports`。
  注意 `test_api_auth_contract.py` 的 `ANONYMOUS_READABLE` 目前只列了其中 5 条
  （见 `docs/TEST_REPORT.md` §3.1），差额 2 条只有间接覆盖。
- `storage.py`（旧库）仍无 WAL；已加连接级 `busy_timeout`，但 WAL 需重建库文件，属迁移范畴。
- `/api/jobs` 只有 `limit`，没有游标分页。
- 单并发 worker（`SCAN_LIMITS["max_concurrency"] = 2` 目前未使用）。
- `config.py:FEROXBUSTER_CONFIG` 的 `wordlist` 已是仓库相对路径（M5 修）；但仓库**不分发**
  `SecLists/`，所以默认字典在本机仍不存在 —— 此时任务会以 `config_error` 明确失败，
  而不是静默零结果。要真跑目录爆破需自行下载字典或用 `FEROXBUSTER_WORDLIST` 指向本机字典。
- `jobs` 表已有 `idempotency_key` / `next_attempt_at`（P0-7 落地，见上）；
  但**没有清理策略**：幂等键会随任务长期留在库里，暂不做过期回收。
- **P0-6 阶段一已完成**（任务创建编排收拢到 `core/application.py`，见上）；
  **阶段二未做**：`agent/action.py` 仍直接调 `tool_runner.run_tools` /
  `HttpxRunner.run_scan`，未走 Job Service —— 接上会让 Agent 执行异步化
  （回复改给 `job_id`），需先出影响说明（见上节与 `docs/DECISIONS.md` §3.2）。
- **P1 遗留**：旧的 `/api/run` 同步扫描链路**不产生** `assets` 观测（只有 Job 链会），
  两套模型尚未合流（方案第 11 节，改的是调用链，属架构级改动，已登记 `docs/DECISIONS.md` §3）；
  旧库历史数据已有迁移脚本但**未执行真实迁移**（DECISIONS-F：等用户手动 `--apply`；
  且本机旧库当前 17 张表全为 0 行）；`mark_stale_assets()` 已就绪但**还没有任何计划任务调用它**；
  观测的 `data_json` 在页面上仍按原样 JSON 渲染，没有按字段拆列。
- **`GET /api/export` 读的是旧库（`ScanResultStore`），不是资产模型**：因此导出的是上游候选的
  原始字面值（如 `127.0.0.1:8080/path`），而不是归一化后的 `http://…` URL。这是现状、已被
  `tests/integration/test_m7_local_e2e.py` 写成断言钉住；要不要合流同样属方案第 11 节。
- **Observability 的遗留**（方案第 19 节只要求「完成基础版本」，以下都还在）：
  `configure_logging()` 只在两个进程入口（`app.py:__main__`、`jobs/worker.py:__main__`）
  调用，因此 `waitress-serve app:app` 这类外部启动方式**不会**输出结构化日志
  （这是刻意的：在 `create_app()` 里配置会关掉 `propagate`，把 pytest 的 `caplog` 弄坏）；
  日志只写 stderr，**没有文件输出与轮转**；只有日志，**没有 metrics / trace**；
  `request_id` 只在单个进程内关联，worker 是**独立进程**，HTTP 的 `request_id` 不会传到
  worker 的日志里（要靠 `job_id` 做跨进程串联）；`/api/settings` 页面尚未暴露日志级别开关。
- **公网体验版的本轮边界**（都不是缺陷，是范围）：
  - `nuclei` 在方案第 4 节被写作 `nuclei(限制)`，但本项目 runner 里**从未接入**它。
    处理方式是如实登记进 `KNOWN_UNAVAILABLE_TOOLS` 且 `internet_allowed=False`，
    在扫描中心按「受限未开放」展示并给出原因 —— **不假装有、也不悄悄漏掉**。
  - 项目与 Scope 是**多对多**（`project_scopes`），但当前只有「项目 → 它的 Scope」正向选择；
    反向（一个 Scope 被几个项目引用）只有后端 `projects.find_by_scope`，没有界面。
  - 公网白名单是**代码常量**（`core/tool_registry.py`），不是数据库配置，
    改它需要改代码 + 过测试，这是刻意的：白名单不该是一个能被顺手改掉的运行期设置。
  - 项目**没有**归档/删除接口：一旦创建就长期存在（与既有 Scope 的现状一致）。
  - 扫描中心页面**不做**分页：项目与任务各取前若干条，量大了要另做。
- **老入口 `POST /api/jobs` 不装公网工具白名单**（实测，2026-10-03，**刻意未改**）：
  `assert_tools_internet_allowed()` 全仓只有一个生产调用点 ——
  `core/application.py:573`（公网编排 `create_authorized_public_job`）。
  因此老入口用 `tools=["nmap"]` + `mode="real"`（开关开 + `active_scan=True`）
  返回 **202 并落库**，而同样参数打 `POST /api/public-jobs` 是 **400 + `blocked_tools`**。
  这是「老入口要不要也变成公网入口」的产品口径问题，加上它会改变既有 API 可用行为
  （`test_legacy_job_api_still_works` 契约要跟着动），属**破坏性变更**而非收口，
  故如实登记在 `docs/DECISIONS.md` §3.11.5 第 1 条（含三种可选口径）等你拍板。
  现状已在 `docs/API.md` §6 与 `docs/CODEBASE_MAP.md` §9.29.6（§9.11.1 已加注指向它）写明。
- **执行期复检的错误码是步骤级、不是任务级**：`jobs/executor.py` 复检 Scope /
  环境开关 / `active_scan` 失败时，**该步骤**记 `scope_violation`，但全部步骤都失败时
  `aggregate_status()` 给的是 `unknown_error`。这是既有聚合语义（本轮刻意没有顺手改它，
  改了会影响所有既有任务的终态判定），已在 `docs/CODEBASE_MAP.md` §9.29.3 写明。
- **`rate_limit` 只对 2 / 17 个 runner 真的生效**（实测，2026-10-03）：
  读 `self.config.get("rate_limit")` 并把它变成命令行参数的只有
  `modules/subfinder.py:64` 与 `modules/httpx.py:212`（各自的 `-rl`）。
  `timeout_seconds` 则是 **17 / 17**（唯一读取点 `modules/base.py:425`）。
  公网白名单**恰好就是那两个**，所以公网链上是 2/2 全覆盖；但白名单外的 15 个
  runner 拿到它是**静默 no-op** —— `core/job_limits.apply_to_runner()` 返回 `True`
  且 `config` 里确实写进了值，`build_command()` 里却没有任何对应参数，
  从调用方看不出它没生效。老入口 `POST /api/jobs` 的 real 模式**可以**走到那些工具
  （见上一条），因此「给 nmap 设了 `rate_limit=5`」目前只改了记录、没有改命令。
  本轮**未改覆盖面**（给 15 个 runner 各加限速参数是独立工作，部分工具根本没有对应开关），
  只在 `docs/API.md` §6.3 与 `docs/CODEBASE_MAP.md` §9.30.5 如实写明，
  避免把「记录了限速」读成「限速了」。
- **`normalize_tool_names()` 不是「全仓唯一一份参数规范化实现」**（措辞已校正）：
  `core/application.py:95 split_str_list()` 是另一份，服务对象是请求字段，
  且与它不是同一个函数（`split_str_list` 不去重、非法类型抛 `BadRequestError`）。
  端到端行为一致靠的是**去重与 registry 校验只有一个收口点**（`load_tools`），
  不是实现唯一 —— 改一处时必须记得另一处。见 `docs/CODEBASE_MAP.md` §9.30.4。

