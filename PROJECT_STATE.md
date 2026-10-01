# PROJECT_STATE.md — 项目状态板

> 这是给**接手者（人或 AI）**看的活文档，只讲「现在在哪、下一步做什么」。
> 设计与用法看 `README.md`；代码结构与坑看 `docs/CODEBASE_MAP.md`；历史变更看 `CHANGELOG.md`。
>
> **谁能改**：任何推进工作的人。每完成一步就更新本文件，别让它过期。
> **更新时机**：里程碑推进、验证结果变化、阻塞解除或新增、决策拍板后。

---

## 当前阶段

**Phase M4 已完成 · P0 产品化加固已完成 · P1（M5 首批：资产/观测/Diff）已完成 · M7 mypy 已清零 · M5 字典可移植性已完成 · P0-7（幂等键 + 退避）已完成 · §16 Windows CI 已落地 · M7 SQLite 并发测试已完成 · M5/M6/M7 剩余项待开工**

- 仓库：`Keqi2048905057/geteverything`（私有），分支 `main`
- 本地副本：`E:\Programmingtools\geteverything`，代码在子目录 `get_everything_framework/`
- 进度：M0 ✅ → M1 ✅ → M2 ✅ → M3 ✅ → M4 ✅ → P0 ✅ → **P1/M5 首批 ✅（含 §10 Diff 前端 + 可点详情 + §12 迁移脚本）→ M7 mypy ✅（34 → 0）→ M5 字典可移植 ✅ → P0-7 幂等键/退避 ✅ + §16 Windows CI ✅ → M7 SQLite 并发测试 ✅ → M5/M6/M7 剩余 ⬜**
- 更新日期：2026-10-02（M7 SQLite 并发测试轮）

---

## 已完成

**工程基线（M0）**
- `pyproject.toml`（pytest / ruff / mypy 配置）、`requirement-dev.txt`
- `tests/` 骨架、`.github/workflows/ci.yml`（ruff + pytest，ubuntu-latest / Python 3.11）
- `LICENSE`、`SECURITY.md`、`CONTRIBUTING.md`、`CHANGELOG.md`
- `.gitignore` 覆盖运行期产物 / 缓存 / 本地密钥 / 大体积二进制
- 仓库重建：不继承上游历史，单个孤立根提交，全程未执行 `git reset --hard` / `git clean -fd`

**本机启动与最小页面（M1）**
- `web/templates/{index.html,login.html}` + `web/static/{app.css,app.js}`（无框架、无 CDN）
- `core/auth.py`：管理员 Token + HttpOnly Session + `X-Local-Token`
- `core/security.py`：`SECRET_KEY` 弱值检测，缺失时生成进程级一次性密钥 + 告警
- `core/errors.py` / `core/errors_handlers.py`：统一错误码与 Flask 错误处理
- `core/ids.py`：带前缀 UUID4
- `app.py:create_app()` 工厂；`GET /` 不再 `TemplateNotFound`；waitress 取代 debug server；默认只绑 `127.0.0.1`

**安全边界（M2）**
- `core/scope.py` / `core/scope_store.py`：Scope 匹配（排除优先、拒绝全放行），无 Scope 即拒绝
- `core/uploads.py`：受控上传，API 只返回 `upload_id`，不再暴露服务器路径
- `core/audit.py`：登录 / Scope / 设置 / 扫描提交的审计（不记密钥明文）
- `core/safety.py`：`mock`（默认）/ `real` 双模式 + `GEF_ALLOW_REAL_SCAN`
- `/api/settings` 写 `.env` 改为原子写 + 备份 + 审计；移除写死的外部默认目标
- 真实扫描双开关：`GEF_ALLOW_REAL_SCAN=true` **且** `Scope.active_scan=true`

**异步任务与状态机（M3）**
- 双库架构：旧库 `results/scan_results.db`（`storage.py`，只读）与新库 `results/local.db`（`core/db.py`）
- 新库 WAL + `busy_timeout=5000` + `BEGIN IMMEDIATE`
- `core/jobs.py`：状态机、步骤快照、认领、租约、cancel / retry / recover_stale_jobs
- `jobs/executor.py` + `jobs/worker.py`（独立进程，心跳文件 `results/worker_heartbeat`）
- `api/jobs.py`：`/api/jobs*` 共 7 个接口；`POST /api/jobs` 立即返回 202 + `job_id`
- `/health` 增加 `queue`（只给计数）与 `worker` 状态

**统一结果与错误模型（M4）**
- `core/runner_result.py`：`Observation` / `ToolHealth` / `RunnerResult` + `scrub_command()`
- `core/artifacts.py` + `artifacts` 表：stdout / stderr / 工具输出文件三类证据落盘
- `api/jobs.py`：`GET /api/jobs/<id>/artifacts`、`GET /api/artifacts/<id>`（都不下发服务器路径）
- **17/17 runner 全部接入**统一接口 `build_command` / `parse_output` / `run`
- `run_scan(target)` 旧签名保留，旧调用方无需改动
- 修掉三个真机缺陷：失败被吞成空结果、残留输出文件冒充本次结果、超时杀不掉孙进程（Windows）/ `killpg` 误杀调用方（POSIX）

**P0 产品化加固（本轮，方案第 7 章 + 执行方案 P0 清单）**
- `core/policy.py`：**统一 Policy / Scope 引擎**——`validate_job_targets` / `validate_step_target` /
  `validate_resolved_address` / `validate_redirect_target` 四个入口，收口原先散落在
  `api/scan.py`、`api/jobs.py`、`jobs/executor.py` 的 Scope 判断（缺失 → 400，不存在/越界 → 403，整体拒绝）
- `api/scan.py` / `api/jobs.py` / `app.py` 全部改走统一入口，删掉各自的 Scope 分支
- `jobs/executor.py`：**real 步骤在调用 Runner 之前重新校验一次 Scope**——即使任务创建后 Scope 被删/被改，
  也不会继续跑（回归测试锁定）
- `agent/action.py` + `agent/planner.py`：**Agent 层不再接受任意 `file_path`**，只能引用受控 `upload_id`
- `core/exports.py` + `exports` 表：导出改为**登记制**，`/api/export` 只返回
  `export_id` / `filename` / `download_url`，**不再下发服务器路径**；新增
  `GET /api/export/<id>/download` 与 `GET /api/exports`
- `exporter.py:safe_prefix()`：导出文件名前缀过滤，堵住 `../` 穿越（有参数化测试）
- `core/jobs.py`：补齐**显式状态跃迁表** `ALLOWED_TRANSITIONS` + `can_transition()`，
  `finish_job` 拒绝 `queued → succeeded` 这类跳步；`retry_job` 增加 `MAX_ATTEMPTS = 5`
- `storage.py`（旧库）：新增 `_connect()` 上下文管理器，**连接一定会关闭**（原先 `with conn`
  只提交不关闭，泄漏文件句柄）；连接级 `busy_timeout=5000`；表结构未动
- `core/health.py`：健康检查的 SQLite 连接改为显式 `close()`
- 测试：新增 `test_policy.py`、`test_agent_boundary.py`、`test_export_contract.py`、
  `test_api_auth_contract.py`、`test_storage_connection.py`，并扩充 `test_jobs_store.py` 状态机用例

**已完成的 M6 条目**
- `scripts/run_local.ps1`（一键拉起 Web + worker，退出时收尾）
- `/health` 的完整字段（database / worker / queue / tools / modes / security）
- `/api/export` 改为**下载链接**（登记 + `download_url`），不再是服务器路径
- 导出记录（`exports` 表 + `GET /api/exports`）

**P1 = M5 首批：统一资产模型与 Diff（本轮，方案第 8、9、10 节）**
- `core/canonical.py`：`canonical_key` 规则（七种类型；host 小写/IDNA、CIDR `strict=False`、
  URL 默认端口丢弃 + query 保留 + fragment 丢弃 + 带凭据拒绝、非 `http(s)` 拒绝）
- `core/assets.py`：**两层模型**（一行 `assets` + N 行 `observations`），
  `first_seen` 永不被覆盖、`last_seen` 每次推进、`metadata` 只补缺失键、
  `mark_stale_assets()` **只改状态不删数据**
- `core/db.py`：`assets` / `observations` 新表 + 索引 + `query()` 辅助（**只新增，未改既有表**）
- `jobs/executor.py`：每步 `finish_step` 后 `ingest_step_observations()` 落观测，
  并记 `step.assets_ingested` 事件（含 `written` / `skipped` / `reasons`）
- `diff_jobs()` + `GET /api/jobs/<a>/diff/<b>`：Diff Engine（added / removed / changed / unchanged）
- `api/assets.py` + `web/templates/assets.html` + `web/static/assets.js`：资产列表页
  （筛选 / 分页 / 观测时间线；未登录不下发 Scope 名称）
- 测试：`test_canonical.py`（~60 例）、`test_assets.py`（~35 例，含方案第 10 节验收
  「A B C → A C D」与方案第 9 节验收「三个 URL 折叠成一个 key」）、
  `test_assets_api.py`（~22 例端到端）

**M7 类型收口（本轮，方案第 17 节）**
- `mypy app.py core api jobs storage.py modules`：**34 errors → Success: no issues found（59 files）**
- **没有改 `pyproject.toml` 的 `[tool.mypy]`**（方案第 17 节：不能为了绿 CI 而排除问题），改的是代码
- `modules/base.py`：基类显式声明 `run_scan` 并抛 `NotImplementedError`（原先基类没有这个方法，
  「子类忘实现」与「跑通但零结果」不可区分）；POSIX 杀进程树改 `getattr(os, ...)` 取函数
- `modules/httpx.py` / `modules/dnsx.py`：`_write_input_file` 补回基类的 `suffix` 参数
- `core/jobs.py`：新增 `get_job_or_raise()`；写路径不再把 `| None` 传染给调用方
- `api/scan.py`：`resolve_scoped_targets` 返回标注由 `"object"` 改为真实的 `Scope`
- `agent/action.py`：`self.context` 补 `Dict[str, Any]`（一处根因消掉 9 条）、
  `_handle_pending_plan` 取局部变量判空（消 5 条）、`available_tools` 补标注
- **顺带修掉一个真机缺陷**：`_tool_httpx` 的 `items` 取成了 `run_scan` 的 URL 字符串列表，
  导致「存活探测」在有结果时抛 `AttributeError: 'str' object has no attribute 'get'`
  （零结果时反而不炸，本地极易漏掉）
- 测试：+4 → **705 passed / 2 skipped**（M7 类型收口本身）

**P1 补充：Diff 条目可点进资产详情（本轮）**
- `web/static/assets.js`：带 `asset_id` 的 diff 条目加 `diff-item-clickable` 并做事件委托，
  点击复用列表页的 `openDetail()`；详情面板渲染后 `scrollIntoView`（面板在页面另一头）
- `web/static/app.css`：可点条目的虚线下划线与 hover 配色
- 测试：+2 → **707 passed / 2 skipped**（服务端 `asset_id` 可用 + 前端确实接线）

**P0-7 幂等键与重试退避（本轮，方案第 7 节 + §16）**
- `core/db.py`：`jobs` 表**纯增量补列** `idempotency_key` / `next_attempt_at`（可空，
  `ALTER TABLE ... ADD COLUMN`，既有行语义不变）；两个非唯一索引
  `idx_jobs_idempotency` / `idx_jobs_next_attempt`（必须排在 `_migrate_columns()` 之后，
  否则旧库升级 `no such column`）。授权见 `docs/DECISIONS.md` §3.1（用户弹窗逐项勾选）
- `core/jobs.py`：`create_job_with_status()` 幂等（同键的 `queued`/`running` 任务复用，
  返回同一个 `job_id` + `reused=True`，不重复插入/展开步骤/写事件；查重与插入同一
  `BEGIN IMMEDIATE` 事务）；`retry_job()` 写退避窗口；`claim_next_job()` 加退避门槛
  并在领走时清空窗口；新增 `normalize_idempotency_key()` / `retry_backoff_seconds()`
- `api/jobs.py`：`POST /api/jobs` 接受可选 `idempotency_key`（非法值 400）+ 回 `reused`；
  `retry` 回 `next_attempt_at`；幂等命中仍写审计（`detail.reused=true`）
- `web/static/app.js`：详情页新增「最早可重试」；`jobSignature()` 纳入 `attempt` /
  `next_attempt_at`（否则 retry 后页面不刷新）
- `.github/workflows/ci.yml`（§16）：`lint-and-test` 改 `matrix.os: [ubuntu-latest, windows-latest]`
  + `fail-fast: false`，两平台都跑 `ruff` + `pytest`，`mypy` 只在 ubuntu 跑
- 测试：+24 → **739 passed / 2 skipped**（含旧库缺列的增量迁移用例）

**M7 SQLite 并发测试（本轮，方案第 15 节「duplicate execution」）**
- 新增 `tests/unit/test_db_concurrency.py`（13 例），**只加测试、零产品代码改动**
- 覆盖：新库连接确为 WAL + `busy_timeout`（且 WAL 跨连接保持）；8 线程并发建任务 /
  写审计不撞锁；读写混合不读半截事务；**8 个 worker 抢 24 个任务不重不漏**；
  同一任务只有一条 `job.started`；无任务时并发认领都返回 `None`；
  **同一幂等键 8 线程并发只建 1 个任务**（`reused=True` 7 次）；锁被持有时写者是
  「等」而不是立刻 `database is locked`（另有反证用例：无 `busy_timeout` 的裸连接必须抛错）
- 关键实现点：`threading` 会把线程内异常打到 stderr 后悄悄结束线程，直接 `join()`
  会把失败读成绿色 —— 本文件用 `_run_threads()` 收集并重抛线程内异常
- 测试：+13 → **752 passed / 2 skipped**

**流程与沉淀**
- 逐里程碑验收报告（M0～M4）在本机 `docs/milestones/`，**按约定不入库**
- 给 Codex 的独立核查文档在桌面：`geteverything_项目汇总_给Codex检查.md`

---

## 部分完成

| 项 | 现状 | 差什么 |
|---|---|---|
| **`mode=real` 真实链路** | ✅ **已有本地实证（本轮）**：`tests/integration/test_m7_local_e2e.py` 用**真实 httpx 子进程**打只绑 `127.0.0.1` 的 fixture 服务，一条用例走完 target → job → worker → runner → raw artifact → parser → observation → asset → diff → export（方案第 18 节）。此外 `tool_not_found` / `timeout` / `nonzero_exit` 等失败分类都有真实子进程用例 | 仍然**从未用真实工具打真实外部目标**（按硬约束刻意不做）—— 这是设计选择，不是缺口；CI 上若无 httpx 可执行文件，该用例会 `pytest.skip` |
| **M4 观测元数据展示** | `httpx` 的 `status_code` / `title` / `webserver` / `tech` / `cdn` 已结构化落库，**并已进资产页的观测时间线** | 资产页展示的是 `data_json` 原样 JSON，**没有按字段拆列**；任务详情页那一侧仍是原样 JSON |
| **M6 导出** | `exporter.py` 能生成 CSV / JSON；`/api/export` 已改为登记制（`export_id` + `download_url`），支持 `GET /api/export/<id>/download` 与 `GET /api/exports` | 没有按时间/条件筛选导出记录的页面；没有导出清理策略 |
| **M6 本机启动文档** | `CONTRIBUTING.md` 有环境搭建说明；`scripts/run_local.ps1` 可用 | 没有面向「新开发者 10 分钟启动」的完整文档；`scripts/check_env.py` 不存在 |
| **M7 mypy** | ✅ **已完成（本轮）**：`mypy app.py core api jobs storage.py modules` → `Success: no issues found in 59 source files` | 仅 `agent/providers/*` 不在该命令范围内（无调用方，见 Known Failure #8；显式加 `agent` 会多 7 条 openai 存根报错，未为它改语义） |
| **P1 Diff 的前端** | ✅ **已完成**：`/assets` 页底部有「两次任务对比」表单（基线与对比任务下拉、可选限定范围、「含未变」开关），四类分段渲染 + 属性差异（`status_code: 200 → 403`）；**清单条目可点进资产详情**（带 `asset_id` 的条目可点，详情面板会滚入视口） | — |
| **P1 资产过期** | `mark_stale_assets(scope_id, last_seen_before=...)` 已实现且有用例 | **没有任何计划任务/接口调用它**，所以 `stale` / `gone` 目前永远是空的 |

---

## 未完成

**M5 — 统一资产与变化检测**（首批已完成，剩余项如下）
- [x] `assets` / `observations` 两张表（`artifacts` 已有）
- [x] 资产规范化（`core/canonical.py`，七种类型）
- [x] 首次发现 / 最近发现（`first_seen` 不被覆盖、`last_seen` 每次推进）
- [x] 两次扫描之间生成 diff（新增 / 删除 / 变更 / 未变 + `counts`）
- [x] 资产列表、筛选、详情页（`/assets` + `/api/assets*`）
- [x] 每条资产可追溯来源（`observations.source_tool` / `job_id` / `step_id` / `observed_at`）
- [x] 旧库 → 新库的迁移（`core/migrate.py` + `scripts/migrate_legacy_results.py`，默认 dry-run、可重跑、旧库只读；20 项单测锁定「旧库 sha256 不变」）
- [ ] **旧库真实迁移未执行**（DECISIONS-F 只授权写脚本；且本机旧库 17 张表当前确为 0 行，迁了也是空结果）
- [ ] 旧的 `/api/run` 同步扫描链路也产资产（目前**只有 Job 链**产；方案第 11 节统一执行链）
- [ ] 资产过期自动化（`mark_stale_assets()` 已就绪但无人调用）
- [x] Diff 在前端露出（`/assets` 页底部的「两次任务对比」表单，四类分段渲染 + 属性差异）
- [x] Diff 条目点进资产详情（带 `asset_id` 的条目可点 → 复用 `openDetail()` 并滚入视口）
- [ ] 观测的 `data_json` 按字段拆列展示（现在只渲染原样 JSON）

**M6 — 导出、健康检查和本机运行脚本**
- [x] `/health` 完整字段
- [x] `scripts/run_local.ps1`
- [x] `/api/export` 改为直接下载（`export_id` + `download_url`，不再返回路径）
- [x] 导出记录（`exports` 表 + `GET /api/exports`）
- [ ] `scripts/check_env.py`
- [ ] 本机启动文档

**M7 — 测试和交付**
- [x] 单元测试 / API 测试 / worker 测试（759 项，超出原计划）
- [x] Scope 拒绝测试 / 上传安全测试 / 工具失败分类测试
- [x] SQLite 并发测试（`tests/unit/test_db_concurrency.py`，13 例，含 `duplicate execution`）
- [x] **本地 fixture HTTP 测试**（本轮：`tests/fixtures/local_http_server.py` + `tests/integration/test_m7_local_e2e.py`，
  真实 httpx 打 `127.0.0.1`，一条用例走完方案第 18 节全链路；无 httpx 可执行文件时自动 skip）
- [x] **mypy 通过**（本轮：34 errors → 0，未改 mypy 配置）
- [ ] 一份测试报告

**其他待办（不在里程碑内，但已知）**
- [x] `README.md` 未同步 M1～M4（已重写鉴权表、`upload_id`、导出下载示例）
- [x] `config.py` 中 `FEROXBUSTER_CONFIG.wordlist` 是开发机绝对路径（已修：改为仓库相对路径 +
  `FEROXBUSTER_WORDLIST` 覆盖；字典缺失时以 `config_error` 明确失败，不再静默零结果）
- [ ] `/api/tools` / `/api/results` / `/api/export` / `/api/exports` 等只读接口仍匿名可读（见 DECISIONS-D，已用测试锁定现状）
- [ ] `/api/jobs` 只有 `limit`，没有游标分页
- [ ] 单并发 worker：`SCAN_LIMITS["max_concurrency"] = 2` 是未使用的配置项
- [x] `storage.py`（旧库）`with conn` 只提交不关闭（已修：`_connect()` 显式关闭 + `busy_timeout`）
- [ ] 旧库仍无 WAL（只加了连接级 `busy_timeout`；WAL 属迁移范畴，未动）
- [x] `jobs` 表 `idempotency_key` / `backoff`（已按 DECISIONS §3.1 用户弹窗授权落地：
  纯增量补列 + `retry_job()` 退避 + `claim_next_job()` 退避门槛）
- [ ] `/api/assets` 只有 `limit` / `offset`，没有游标分页（与 `/api/jobs` 同款问题）

---

## Known Existing Failures

> 这些是**已知且当前存在**的问题，不是「待办想法」。审查时不要重复报为新发现。
> **已修掉 4 条**（原 #1、#2、#7，以及新增的 #11），保留编号以便对照历史报告。

| # | 症状 | 位置 | 影响 |
|---|---|---|---|
| 1 | ~~`mypy` 报 34 个错误~~ **已清零** | 曾分布于 `agent/action.py`(20)、`modules/base.py`(5)、`jobs/executor.py`(2)、`api/scan.py`(2)、`modules/httpx.py`(2)、`config.py`(1)、`core/jobs.py`(1)、`modules/shuffledns.py`(1) | 已全部修掉，**未改 mypy 配置**；详见 CHANGELOG「M7」一节 |
| 2 | ~~`/api/export` 返回服务器文件路径~~ **已修** | `api/results.py` + `core/exports.py` | 现在只返回 `export_id` / `filename` / `download_url`；有契约测试锁定 |
| 3 | `/api/tools`、`/api/databases`、`/api/results`、`/api/export`、`/api/exports` 匿名可读 | `api/tools.py`、`api/results.py` | 未授权即可读到扫描结果与库元信息；**按 DECISIONS-D 故意保持**，已用 `test_api_auth_contract.py` 锁定现状 |
| 4 | 旧库并发写 `database is locked` | `storage.py` | **已缓解**：连接级 `busy_timeout=5000`；仍无 WAL（WAL 属迁移范畴，未动）。新库 `core/db.py` 的 WAL + `busy_timeout` + `BEGIN IMMEDIATE` **本轮已用 8 线程真机验证**（`tests/unit/test_db_concurrency.py`） |
| 5 | ~~`FEROXBUSTER_CONFIG.wordlist` 是开发机绝对路径~~ **已修** | `config.py` | 改为仓库相对路径 + `FEROXBUSTER_WORDLIST` 覆盖，路径统一按项目根解析；字典缺失时 `error_code=config_error`（**仍不分发字典**，需自行下载或指环境变量） |
| 6 | `HTTPX_CONFIG.path` 默认 `"http-x"` | `config.py` | 本机靠 `E:\GoWorkspace\bin\http-x.cmd` 包装脚本指向 `httpx.exe` 才能跑；裸环境会 `tool_not_found` |
| 7 | ~~`python -m pytest` 有 2 条 warning~~ **已清零** | — | 见下节「已修的两条 warning」 |
| 8 | `agent/client.py`、`agent/providers/*` 无任何调用方 | `agent/` | 「LLM 规划」实际由正则 + 模板决定，**不调用大模型**；「模型超时/返回格式错」类症状在当前路径不可达 |
| 9 | Agent 仍可绕过 Job/Policy 直接调 `run_tools` / `HttpxRunner.run_scan` | `agent/action.py` | Agent 层已禁止任意 `file_path`（只能 `upload_id`），但**尚未改走 Job Service**；属 P0-6 未完成项 |
| 10 | ~~`jobs` 表无 `idempotency_key`、无 `backoff`~~ **已补** | `core/jobs.py` | 已按 DECISIONS §3.1 授权**纯增量补列**：幂等键（同键未终结任务复用）+ 退避窗口（`next_attempt_at`）；`MAX_ATTEMPTS` 与显式状态跃迁表此前已补 |
| 11 | ~~`/api/artifacts/<id>` 的 `text` 只返回头 300 字符，而 `truncated` 仍是 `false`~~ **已修** | `core/artifacts.py:read_artifact`（曾用命令预览语义的 `scrub_command()`） | 证据动辄几十 KB，被截掉的正是排查「跑通了但没数据」时要看的部分，且 `truncated=false` 是对外说假话。已抽出 `scrub_text()`（只脱敏、默认不截断），`read_artifact()` 改用它；`scrub_command()` 的语义与 300 字符上限**未变**。回归：`tests/integration/test_m7_local_e2e.py` 断言证据「恰好 3 行」 |

### 已修的两条 warning（原 Known Failure #7 / DECISIONS-I）

| 原报告位置 | 真实根因 | 修法 |
|---|---|---|
| `core/db.py:244` 未关闭文件 | 标签本身是**旧的**。真正的来源是 `storage.py` 里 `with self._get_connection() as conn:` —— `sqlite3.Connection` 的 `with` **只提交事务、不关闭连接**，于是每次查询都漏一个文件句柄 | 新增 `storage.py:_connect()` 上下文管理器（事务语义不变 + `finally: conn.close()`），10 个调用点全部改用它；另有 `core/health.py` 的只读连接显式 `close()` |
| `tests/unit/test_security_baseline.py:59` | 该用例**故意**让 `SECRET_KEY` 为空，必然触发一次性密钥告警 | 改为 `pytest.warns(RuntimeWarning, ...)` 显式断言这条安全提示存在，告警不再污染输出 |
| （附带发现）`test_upload_over_limit_is_rejected` 偶发 `unclosed file` | 来自 Werkzeug 测试客户端：body > 500KB 时 `stream_encode_multipart` 建 `TemporaryFile("wb+")` 且从不关闭 | 该用例改为手工拼 multipart 字节串，走 `BytesIO` 不落盘；导出下载用例显式 `response.close()` |

回归测试：`tests/unit/test_storage_connection.py`（10 项，含异常路径不泄漏、`busy_timeout`、
表结构未变、以及用 `warnings.simplefilter("error", ResourceWarning)` 复现原始症状）。

**mypy 错误分布（历史记录：本轮已全部清零）**

`mypy app.py core api jobs storage.py modules` 曾是 **34 条 / 8 个文件**：

| 文件 | 错误数 | 当时的备注 |
|---|---|---|
| `agent/action.py` | **20** | 最大头。虽然 M7 的命令没写 `agent`，但 `app.py:21` 有 `from agent import handle_agent_message`，mypy 会顺着 import 查进来 |
| `modules/base.py` | 5 | M4 改过的文件 |
| `jobs/executor.py` | 2 | |
| `api/scan.py` | 2 | |
| `modules/httpx.py` | 2 | M4 改过的文件 |
| `config.py` | 1 | |
| `core/jobs.py` | 1 | |
| `modules/shuffledns.py` | 1 | M4 改过的文件 |
| **合计** | **34** | |

> **本轮已全部清零**（`Success: no issues found in 59 source files`），且**没有改 mypy 配置**。
> 修复顺序按方案第 17 节给的优先级：`modules/base.py` → `jobs/executor.py` / `core/jobs.py`
> → `api/*` → 最后 `agent/`。逐条对应关系见 `CHANGELOG.md` 的「M7」一节。
>
> **唯一仍在命令范围外的是 `agent/providers/*`**：显式 `mypy ... modules agent` 会多出
> 7 条 openai 存根相关的报错，而这一层**没有任何调用方**（Known Failure #8）。
> 为它改组织方式属于「改运行语义」，本轮没做。

---

## 当前阻塞

### 🔴 BLOCKED-A：公开 fork 上的敏感数据未处理（**需要用户手动操作**）

用户的 fork `Keqi2048905057/get_everything_framework` 是**公开仓库**，`main` 分支上可匿名下载 **20 个敏感条目**：

- `results/scan_results.db`（565,248 字节）
- `results/outs/小米-2026-06-12--*.json`（26,882 字节，**含真实企业名与业务描述**，已实测下载确认）
- 12 个真实目标的扫描输出 txt、2 个上传清单、3 个 exe（约 220 MB）、1 个字典

此外，**已删除分支 `codex/local-mvp` 的提交 `736ad76` 仍是 GitHub 悬空对象，可按 sha 匿名读取**，其树里含
`DSH_执行提示词.md`（1,570 字节）、`本机联调版实施方案_DSH.md`、`docs/milestones/M0～M3.md`（M3.md 11,283 字节）。

> **结论：「删分支」≠「删内容」。** 只要 sha 已知就能读到。

**为何阻塞**：删除仓库需要 `delete_repo` scope，当前 Token 没有（API 删返回 403），**只能用户手动操作**。
**解除方式（三选一）**：① 把 fork 设为 private；② 在网页 Settings 底部删除 fork；
③ 保留公开但用 `git filter-repo`/BFG 重写历史 + 联系 GitHub Support 清悬空对象（成本最高且不保证彻底）。
**补充事实**：这**不是用户引入的**——上游 `Linki4964/get_everything_framework` 同样公开着这些文件。

### 🟠 BLOCKED-B：9 项决策未拍板（**不阻塞开发，但影响 M5 开工方式**）

| ID | 待决事项 | 影响 |
|---|---|---|
| A | 公开 fork 敏感数据怎么处理 | 见上 |
| B | `SECRET_KEY` / `LOCAL_ADMIN_TOKEN` 缺失时：自动生成（现状）vs 启动即失败 | 影响首次启动体验与安全基线 |
| C | 旧 clone 是否执行 `git rm --cached`（仍跟踪 19 个敏感文件） | 不可逆操作，需确认 |
| D | `/api/tools` / `/api/results` / `/api/export` 是否加鉴权 | 影响本机脚本兼容性 |
| E | M5 的 `assets` / `observations` 表粒度：唯一资产 vs 保留观测历史 | **表结构要一次定对**，改起来涉及迁移 |
| F | 旧库历史数据（waybackurls 296 / enscan 37 等）是否迁进新库 | **已按 DECISIONS-F 落地脚本**（`scripts/migrate_legacy_results.py`，默认 dry-run）；真实迁移仍待你手动 `--apply`。注：本机旧库实测 17 张表全为 0 行 |
| G | httpx 观测元数据先在哪露出：按目标汇总页 vs 资产列表页 | 决定 M5 开工顺序 |
| H | 是否删除 `%TEMP%\gef_old_clone_full.bundle`（105.7 MB 历史备份） | 无风险，纯清理 |
| I | 两条已知 warning 是否顺手修 | 无风险，纯清理 |

> **E 已按 `docs/DECISIONS.md` 的预填答案落地**（「唯一资产 + 观测历史」双层，纯增量新增表），
> P1/M5 首批据此完成。F/I 也已落地。**A 仍需你手动处理**（见下 BLOCKED-A）；
> C / H 属需明确授权项，未执行，已登记在 `docs/DECISIONS.md` §3。

---

## 最近一次验证

```text
验证时间：2026-10-02（M7 本地 fixture HTTP 全链路 E2E 轮）
工作目录：E:\Programmingtools\geteverything\get_everything_framework

ruff:   All checks passed!
pytest: 759 passed, 2 skipped, 0 failures / 0 errors      ← junitxml 计数，PowerShell 看不到汇总行
mypy:   Success: no issues found in 59 source files        ← M7 验收命令，仍为 0
node --check web/static/{app.js,assets.js}: 语法检查通过（无前端构建链，只能做到这一步）
git diff --check: 退出码 0
```

**基线演进**：M1 `70` → M2 `142` → M3 `236` → M4 `405` → P0 加固 `538` → P1 `701` → M7 `707` → M5 字典可移植 `715` → P0-7 幂等/退避 `739` → M7 SQLite 并发 `752` → **M7 本地全链路 E2E `759`**

---

## 最近一次 commit

> **本节的写法说明**：状态板自己也会被提交，所以「记录 HEAD」天然会差一个提交。
> 下面给的是**最近一次不含本文件改动的提交**，并附上自检命令。以 `git log -1` 为准。
> 提交表里**不含**更新本文件的那些 `docs: 状态板…` 提交 —— 它们只改这一个文件。

```text
50d04cc8751ce5c0edc2b67bcc91e0aa3580ebfc   ← 最近一次代码提交（M7 本地 fixture HTTP 全链路 E2E）
50d04cc  feat: M7 本地 fixture HTTP 全链路 E2E（方案第 18 节）+ 修证据读取被预览规则截断 (2026-10-02)
```

自检：

```powershell
git log -1 --format="%H %s"     # 以这条输出为准
git status -sb                  # ## main...origin/main [ahead N] = 本地已提交、尚未 push
```

与 `origin/main` **不同步**：本地领先（`git status -sb` 会显示 `[ahead N]`，N 含本文件自身的提交，
所以这里不写死数字）。M0～M4 之后的全部里程碑提交都还在本地 —— 夜间无人值守期间
**不做 `git push`**，等你确认后再推。下表是**除本文件提交之外**的全部 15 个提交：

| 提交 | 说明 |
|---|---|
| `50d04cc` | feat: M7 本地 fixture HTTP 全链路 E2E（方案第 18 节）+ 修证据读取被预览规则截断 |
| `9eb68f1` | docs: 同步 M7 mypy 清零（代码地图 + CHANGELOG + 状态板） |
| `26c7246` | fix: M7 类型收口——mypy 34 errors 清零（未改 mypy 配置） |
| `fa8d1c7` | feat: P1 §10 Diff 前端露出 + 修正「未变」计数被明细开关清零 |
| `63e630f` | feat: P1 §12 旧库 → 新库的只读迁移脚本（含幂等与时间归一） |
| `e1025c5` | feat: P1 统一资产模型与 Diff（assets/observations、canonical_key、资产页） |
| `da1b595` | feat: P0 产品化加固（统一 Policy/Scope、Agent 边界、导出脱路径、状态机收口） |
| `364ea25` | docs: PROJECT_STATE.md 修正「最近一次 commit」的自指问题 |
| `0ddbcd2` | docs: 新增 PROJECT_STATE.md 项目状态板（本文件首次入库） |
| `536fe49` | docs: SECURITY.md 移除已过期的「多数 runner 未接入统一接口」 |
| `ab575bc` | docs: 同步 M4 增量（代码地图 + CHANGELOG） |
| `c5bae37` | feat: M4 铺开统一 runner 接口到其余 14 个 runner |
| `a311388` | fix: POSIX 下杀进程树会连调用方一起 SIGKILL |
| `61b0f9b` | chore: 建立独立仓库 geteverything 的初始提交（含 M0～M4 全部代码） |

> M0～M3 没有独立提交，全部压在根提交里——这是「只推最新代码、重开一份干净历史」的直接结果。

---

## 下一步该做什么（给接手者）

1. **先推进 C 之前的确认**：把上表 A～I 里你能定的定掉，**E 是关键路径**（已按 DECISIONS-E 落地，可回看）。
2. **M5 剩余**（§11 统一执行链需授权，见 `docs/DECISIONS.md` §3）：资产过期自动化、观测 `data_json` 按字段拆列、
   旧库真实迁移（等你手动 `--apply`）。
3. **顺手可做（不需要决策）**：
   - ~~修 `config.py` 里 `FEROXBUSTER_CONFIG.wordlist` 的开发机绝对路径~~ —— **已修（M5，
     连同「字典缺失不再静默空结果」一起）**；
   - 同步 `README.md`（`file_path` 已废弃、补鉴权与 Scope 说明）；
   - ~~M7：本地 fixture HTTP 测试~~ —— **已完成（`tests/integration/test_m7_local_e2e.py`，
     真实 httpx 打 `127.0.0.1` 全链路）**；M7 只剩**一份测试报告**（`scripts/check_env.py`
     与「10 分钟启动」文档属 M6）。
4. **改完代码记得**：刷新 `docs/CODEBASE_MAP.md` 对应章节与 `last-mapped`，更新 `CHANGELOG.md`，
   并回来更新本文件的「最近一次验证 / 最近一次 commit」。

## 复现三条基线命令

```powershell
cd E:\Programmingtools\geteverything\get_everything_framework
python -m pip install -r requirement.txt -r requirement-dev.txt

python -m ruff check .                                # 期望 All checks passed!
python -m pytest                                      # 期望 759 passed, 2 skipped
python -m mypy app.py core api jobs storage.py modules # 期望 Success: no issues found
```

**运行期产物隔离（重要）**：测试**从不**写仓库的 `results/`。`tests/conftest.py` 会 patch
`storage.SQLITE_CONFIG["path"]`、`config.LOCAL_DB_CONFIG["path"]`、`core.uploads.UPLOAD_DIR`、
`core.health.OUTPUT_DIR`。**任何一处漏 patch 都会让测试污染仓库 `results/`。**
另外 `modules/base.py`、`modules/httpx.py`、`jobs/worker.py` 的 `OUTPUT_DIR` 是**导入期**绑定的
模块级字符串，conftest 管不到 —— `tests/integration/test_m7_local_e2e.py` 里额外 patch 了三处
（因为那条用例会起**真实 httpx 子进程**，输出与心跳都会落到 `OUTPUT_DIR`）。

## 硬约束（不要违反）

- 不扫描任何**未授权的外部目标**；默认只用 mock runner 与 `127.0.0.1`
  （本地全链路 E2E 用的是 `tests/fixtures/local_http_server.py`，它**只绑 `127.0.0.1`**）
- 不执行 `git reset --hard`、`git clean -fd`
- 不覆盖 `get_everything_framework/scripts/OneForAll.exe` 的未提交差异（在旧 clone 里）
- 数据库、扫描结果、上传样本、密钥**不得提交进 Git**
- `results/scan_results.db` 只读；复现用临时库 `ScanResultStore(db_path=...)`
- 执行类过程文档（`DSH_执行提示词.md`、`本机联调版实施方案_DSH.md`、`docs/milestones/`）**留在本机，不入库**
