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
- `api/jobs.py`：`/api/jobs*` 共 7 个接口。

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

### 测试与验收基线

```text
$ python -m ruff check .     # All checks passed!
$ python -m pytest           # 838 passed, 2 skipped, 0 failures
$ python -m mypy app.py core api jobs storage.py modules   # Success: no issues found in 60 source files
```

### 已知仍未处理（不属 M0～M4 范围）

- `/api/tools`、`/api/databases`、`/api/results`、`/api/export`、`/api/exports` 仍可匿名只读
  （按 `docs/DECISIONS.md` D 有意保持，已用契约测试锁定）。
- `storage.py`（旧库）仍无 WAL；已加连接级 `busy_timeout`，但 WAL 需重建库文件，属迁移范畴。
- `/api/jobs` 只有 `limit`，没有游标分页。
- 单并发 worker（`SCAN_LIMITS["max_concurrency"] = 2` 目前未使用）。
- `config.py:FEROXBUSTER_CONFIG` 的 `wordlist` 已是仓库相对路径（M5 修）；但仓库**不分发**
  `SecLists/`，所以默认字典在本机仍不存在 —— 此时任务会以 `config_error` 明确失败，
  而不是静默零结果。要真跑目录爆破需自行下载字典或用 `FEROXBUSTER_WORDLIST` 指向本机字典。
- `jobs` 表已有 `idempotency_key` / `next_attempt_at`（P0-7 落地，见上）；
  但**没有清理策略**：幂等键会随任务长期留在库里，暂不做过期回收。
- Agent 尚未改走 Job Service（P0-6 未完成部分）。
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
