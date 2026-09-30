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
`SecLists/`、`scripts/*.exe`。

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
- `core/assets.py` 引入的 10 条 mypy 报错（缺类型标注）已补回，**mypy 仍为 34**（M7 存量，无新债）。
- `core/assets.py:diff_jobs()`：`include_unchanged=False` 原先会把 `counts["unchanged"]`
  一起抹成 0，导致「这次扫到了但没变化（未变 3）」与「这次什么都没扫到（未变 0）」
  完全不可区分 —— 而这恰是 diff 最有用的一条信息。改为**只影响明细下发、不影响计数**，
  并由 `test_diff_can_omit_unchanged_details` 与 API 侧用例双向锁定。

### 测试与验收基线

```text
$ python -m ruff check .     # All checks passed!
$ python -m pytest           # 701 passed, 2 skipped, 0 failures
$ python -m mypy app.py core api jobs storage.py modules   # 34 errors（M7 待修，P1 未新增）
```

### 已知仍未处理（不属 M0～M4 范围）

- `/api/tools`、`/api/databases`、`/api/results`、`/api/export`、`/api/exports` 仍可匿名只读
  （按 `docs/DECISIONS.md` D 有意保持，已用契约测试锁定）。
- `storage.py`（旧库）仍无 WAL；已加连接级 `busy_timeout`，但 WAL 需重建库文件，属迁移范畴。
- `/api/jobs` 只有 `limit`，没有游标分页。
- 单并发 worker（`SCAN_LIMITS["max_concurrency"] = 2` 目前未使用）。
- `config.py:FEROXBUSTER_CONFIG` 的 `wordlist` 仍是开发机绝对路径。
- `jobs` 表无 `idempotency_key` / 无 `backoff`（需新增列 = 改表结构，已登记 `docs/DECISIONS.md` §3 待授权）。
- Agent 尚未改走 Job Service（P0-6 未完成部分）。
- **P1 遗留**：旧的 `/api/run` 同步扫描链路**不产生** `assets` 观测（只有 Job 链会），
  两套模型尚未合流（方案第 11 节，改的是调用链，属架构级改动，已登记 `docs/DECISIONS.md` §3）；
  旧库历史数据已有迁移脚本但**未执行真实迁移**（DECISIONS-F：等用户手动 `--apply`；
  且本机旧库当前 17 张表全为 0 行）；`mark_stale_assets()` 已就绪但**还没有任何计划任务调用它**；
  对比结果目前只按四类清单展示，点条目还不能跳到对应资产详情。
