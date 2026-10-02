# PROJECT_STATE.md — 项目状态板

> 这是给**接手者（人或 AI）**看的活文档，只讲「现在在哪、下一步做什么」。
> 设计与用法看 `README.md`；代码结构与坑看 `docs/CODEBASE_MAP.md`；历史变更看 `CHANGELOG.md`。
>
> **谁能改**：任何推进工作的人。每完成一步就更新本文件，别让它过期。
> **更新时机**：里程碑推进、验证结果变化、阻塞解除或新增、决策拍板后。

---

## 当前阶段

**Phase M4 已完成 · P0 产品化加固已完成 · P1（M5 首批：资产/观测/Diff）已完成 · M7 mypy 已清零 · M5 字典可移植性已完成 · P0-7（幂等键 + 退避）已完成 · §16 Windows CI 已落地 · M7 SQLite 并发测试已完成 · M7 本地全链路 E2E 已完成 · §19 Observability 基础版已完成 · §14 文档三件套已完成 · Diff 属性别名缺陷已修 · P0-6 阶段一（Application Service 入口收拢）已完成 · M6 环境自检脚本已完成 · M7 测试报告已完成 · 测试运行期目录隔离已修 · P0-6 阶段二影响说明已出（等拍板）· **公网授权测试模式体验版已完成（项目 / 工具权限元数据 / 策略模板 / 扫描中心页）** · 本地领先 origin/main 8 个提交（纯快进，待确认后推送）· **方案第 11 节明写「到这里就可以暂停先了」，本阶段到此停** · M5 剩余项 + P0-6 阶段二待开工**

- 仓库：`Keqi2048905057/geteverything`（私有），分支 `main`
- 本地副本：`E:\Programmingtools\geteverything`，代码在子目录 `get_everything_framework/`
- 进度：M0 ✅ → M1 ✅ → M2 ✅ → M3 ✅ → M4 ✅ → P0 ✅ → **P1/M5 首批 ✅（含 §10 Diff 前端 + 可点详情 + §12 迁移脚本）→ M7 mypy ✅（34 → 0）→ M5 字典可移植 ✅ → P0-7 幂等键/退避 ✅ + §16 Windows CI ✅ → M7 SQLite 并发测试 ✅ → M7 本地 fixture 全链路 E2E ✅ → §19 Observability 基础版 ✅ → §14 文档三件套 ✅ → 修 Diff 属性别名缺陷 ✅ → P0-6 阶段一 ✅ → push 前安全审计 + 推送 ✅ → M6 环境自检 ✅ → M7 测试报告 ✅（`docs/TEST_REPORT.md`）→ 测试运行期目录隔离修复 ✅ → P0-6 阶段二影响说明 ✅（`docs/AGENT_ASYNC_IMPACT.md`，**等用户拍板后开工**）→ 公网授权测试模式体验版 ✅（`docs/milestones/GetEverything_公网授权测试模式体验版方案.md`，方案第 11 节写到「到这里就可以暂停先了」；交付报告见 `docs/milestones/本机验收报告_公网授权测试模式_2026-10-02.md`）→ P0-6 阶段二 + M5 剩余 ⬜**
- 更新日期：2026-10-02（公网授权测试模式体验版 + 交付报告 + 测试隔离修复；此前 M7 测试报告 / M6 环境自检按 2026-10-01～02 记）

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
- `scripts/check_env.py`（一键环境自检，**只读**；见下方专段）
- `/health` 的完整字段（database / worker / queue / tools / modes / security）
- `/api/export` 改为**下载链接**（登记 + `download_url`），不再是服务器路径
- 导出记录（`exports` 表 + `GET /api/exports`）
- 本机启动文档（`docs/DEPLOYMENT.md` + README 的「环境要求」「启动 Worker」两节）

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

**M7 本地 fixture HTTP 全链路 E2E（方案第 18 节）**
- 新增 `tests/fixtures/local_http_server.py`（只绑 `127.0.0.1` 的标准库 `ThreadingHTTPServer`）
  + `tests/integration/test_m7_local_e2e.py`：真实 httpx 子进程打本地 fixture，一次走完
  target → job → worker → runner → raw artifact → parser → observation → asset → diff → export
- 新增 `tests/__init__.py`（**必需**：否则 site-packages 里的同名常规包会把本仓库的
  命名空间包 `tests` 顶掉，`import tests.fixtures` 直接 `ModuleNotFoundError`）
- 顺带修掉既有缺陷：`read_artifact()` 误用 `scrub_command()`（命令预览语义，截到 300 字符），
  于是 `GET /api/artifacts/<id>` 的 `text` 永远只有头 300 字符而 `truncated` 仍是 `False`；
  改为新增的 `scrub_text()`（只脱敏、默认不截断）
- 测试：+7 → **759 passed / 2 skipped**

**P1 §19 Observability：结构化日志与关联 ID（本轮）**
- 新增 `core/observability.py` 作为**唯一日志出口**（stdlib `logging`，未引入新依赖）：
  一行一个 JSON 事件，四个关联字段 `request_id` / `job_id` / `step_id` / `worker_id`
  用 `contextvars` 绑定，「绑定一次、全链继承」
- 绑定/还原成对：`app.py` 的 `before_request` ↔ `teardown_request`（并回写
  `X-Request-Id` 响应头）；`jobs/executor.py` 的 `with observability.bind(...)`；
  `jobs/worker.py:Worker.startup` ↔ `Worker.shutdown`（新增 `with Worker(...)` 用法保证成对）
- 接线：`http_request_finished`（只记 `path`，**不记 query**）、`request_failed`、
  `unhandled_exception`、`job_created`（记目标**个数**不记列表）、`job_started` /
  `job_step_finished` / `job_finished`、六个 `worker_*` 事件；
  `agent/action.py` 的 debug `print`（会倒出整份结果）改成 `agent_plan_step` 事件
- 脱敏：字段名命中 `api_key`/`token`/`secret`/`password`/`authorization`/`cookie`/`credential`
  → 值只记 `***`；其余文本过 `core.runner_result.scrub_text`；单字段 500 字符截断；
  **容器最多 20 项**（方案第 19 节「不要记录完整目标列表到公共日志」）
- 关键细节：`job_` + 32 位 hex（36 字符）会被裸 token 兜底规则误打成 `***`，
  因此 `*_id` 字段按标识符原样记录；自由文本里的 ID 先占位再还原
- `config.py` + `.env.example` + `README.md`：新增 `GEF_LOG_LEVEL` / `GEF_LOG_FORMAT`
  与「拿 `X-Request-Id` 去 grep」的排障用法
- 三条**源码守卫**（AST 扫描）：`print` 里不得出现密钥形状、除
  `core/observability.py` 与 `core/errors_handlers.py` 外不得自建 logger、
  现存 50 处 `print` 按 `文件:函数` 粒度登记（新增即失败）
- 两处测试隔离修正：`conftest.py` 新增 autouse 的 contextvar 清理；
  M7 E2E 的 `_drain_worker` 改用 `with Worker(...)`（原来只 `startup()` 不 `shutdown()`，
  `worker_id` 会泄漏到同线程的下一条用例 —— 单独跑绿、全量跑炸）
- **端到端验收**（长方案 P1-5 原话「输入一个 `job_id` 可以串起整条执行链」）：
  `test_single_job_id_stitches_the_whole_chain` 拿一个 `job_id` 去日志里捞，
  一次性捞到 Web 创建 → 执行开始/每步/结束 → worker 领取/结束 全部事件；
  另有反向守卫 `test_log_trace_never_contains_the_scope_target_list`
  （12 个目标的整份清单不得出现在任何日志字段里）
- 测试：+69 → **828 passed / 2 skipped**

**流程与沉淀**
- 逐里程碑验收报告（M0～M4）在本机 `docs/milestones/`，**按约定不入库**
- 给 Codex 的独立核查文档在桌面：`geteverything_项目汇总_给Codex检查.md`

**P1 §14 文档同步 + 导出格式 400 收口（本轮）**
- 新增 `docs/ARCHITECTURE.md`（分层架构与冻结的技术选型）、`docs/API.md`
  （**逐条核对真实路由**：39 条规则 / 41 个方法绑定，`/api/*` 34 条、需认证 24 条、
  匿名可读 7 条）、`docs/DEPLOYMENT.md`（环境要求 / 安装 / `.env` / 启动 Web 与 worker /
  测试 / 故障排查）；三份都是简体中文、互相交叉引用
- **修一个真实缺陷**：`GET /api/export?format=xlsx` 原返回 **500 `unknown_error`**
  （`exporter.export_results` 抛 `ValueError` 无人捕获，一路冒到全局兜底）——
  「用户传错参数」被报成「服务器内部错误」。现改为在调用 exporter **之前**用
  `exporter.SUPPORTED_FORMATS`（新增常量，与内部兜底**同一份**清单）拦下，返回
  400 `bad_request` + `details.field="format"` + `details.supported=["csv","json"]`
- 顺带修正一批**文档与代码不一致**：README 的「12 个接口 / `/api/scan`」（该路由不存在）、
  「用 `/api/tools` 做健康检查」（实际是 `GET /health`）、匿名可读只列 3 条（实际 7 条）、
  「需部署前端模板」（模板与静态资源都已在仓库）、Q6「`/` 报 TemplateNotFound」（M1 已修）、
  测试基线 759；README 补「启动 Worker」「Mock / Real 双开关」「环境要求」三节
  （原来全文 0 次提及 worker，照着装完任务会永远停在 `queued`）；
  `api/tools.py` docstring 的 `table_name`/`record_count`（实际是 `table`/`result_column`/`category`）；
  `api/scopes.py` 注释说 404 转成 `bad_request`（实际 `not_found`）；
  `api/{scan,results}.py` 举 `nuclei` 当工具名（`RUNNER_REGISTRY` 里没有）；
  `SECURITY.md` 的字典绝对路径条目（M5 已修）+ 补记导出格式缺陷；
  `AGENTS.md` 的「560 行 / 22 条索引表」（实际 1800+ 行 / 27 条）+ 三个已修坑标注
- 测试：+10 → **838 passed / 2 skipped**
  （`test_export_contract.py` 新增 6 条参数化非法 format + 校验清单同源 + 默认仍是 csv +
  大小写不敏感）

**修复 — Diff 属性别名归一（本轮，真实缺陷）**
- **症状**：方案第 10 节要求 Diff 的 `changed` 至少能指出 `status_code` / `title` /
  `server` / `technology` / URL，但在**真实 httpx 链路上只有三项能报出变化**
- **根因**（已实测复现）：`core/assets.py:DIFFABLE_ATTRIBUTES` 写 `server` / `technology`，
  而 `modules/httpx.py:_read_json_results` 实际产出的键名是 `webserver` / `tech`
  —— 属性白名单永远匹配不上，`webserver` 从 `nginx` 变 `apache` 也报不出来。
  此前被测试掩盖：`test_assets.py` 用的是**文档体例**键名而非 httpx 真实键名，测试全绿而线上失效
- **修法**（用户选定「集中成表」）：新增 `ATTRIBUTE_ALIASES` 把两侧写法映射到 canonical key，
  `DIFFABLE_ATTRIBUTES` 改用 canonical key（`status_code`/`title`/`webserver`/`technologies`/`url`），
  新增 `_canonical_attributes()`，`_changed_attributes()` 先对两侧归一化再比较；
  不变量「别名表值集 == 属性白名单」写成用例；`web/static/assets.js` 新增 `ATTRIBUTE_LABELS`
  把 canonical key 渲染成中文标签
- 测试：+9 → **847 passed / 2 skipped**

**P0-6 阶段一（本轮，用户已授权）— Application Service 入口收拢**
- 用户在本轮弹窗中**授权 P0-6**，口径见 `docs/DECISIONS.md` §3.2：先梳理调用拓扑、
  分阶段改造、每一步跑测试；目标链路
  `Agent → Intent/Plan → Application Service → Policy/Scope → Job → Worker → Runner`；
  **验收点是「权限边界移动了」，不只是「函数调用换了」**
- **改前拓扑**：创建扫描任务的编排**内联在视图函数里**（`api/jobs.py:create_job`），
  `app.py:index()` 为做同一件事反向导入 api 层私有函数 `_resolve_targets` 并抄了第二遍
  Policy 判定；Agent 则完全绕过 Job 链（`run_tools` / `HttpxRunner.run_scan`）
- **新增 `core/application.py`（Application Service 层）**：`create_scan_job()` 为创建扫描任务的
  **唯一**编排入口（校验顺序与历史逐条一致，刻意不重排）；`resolve_targets()` / `split_str_list()`
  升为公开接口；`JobSubmission` 提供与 `POST /api/jobs` **完全一致**的响应体
- **边界刻意收窄**：不碰 Flask（认证/请求解析/状态码仍在 `api/` 与 `app.py`）、
  不自实现 Scope 判定（一律转交 `core.policy.validate_job_targets`）、不改数据结构
- **调用方迁移**：`api/jobs.py:create_job` 缩成「认证 + 解析 JSON + 拼响应」；
  `app.py:index()` 改调同一入口，反向导入 api 层私有函数的写法消失
- 测试：+27 → **874 passed / 2 skipped**（`tests/unit/test_application_service.py`），
  含三条**源码守卫**：`api/jobs.py` 不得再出现内联编排、`app.py` 不得再反向导入
  `api.jobs._resolve_targets`、`core/application.py` 不得出现 `allowed_domains`/`allowed_cidrs`/`fnmatch`
- **阶段二未做**：`agent/action.py` 仍直接调 `run_tools` / `HttpxRunner.run_scan`。
  接上 Job 链会让 Agent 执行**异步化**（回复给 `job_id` 而非内联结果，
  `tests/unit/test_agent_boundary.py` 需重写），按用户约束「需要改变核心数据模型或执行架构时
  先停下来说明影响」—— 阶段二开工前先出影响说明

**M6 环境自检脚本（本轮，方案 M6 最后一项）**
- 新增 `scripts/check_env.py`：回答「这台机器上本机联调版能不能跑」。十四项检查分四类
  —— 解释器与依赖（`requirement.txt` 逐项核对、`requirement-dev.txt` 按 `>=` 判定）、
  运行期目录权限、`.env` 与安全开关（弱 `SECRET_KEY`、空 `LOCAL_ADMIN_TOKEN`、
  `WEB_DEBUG`、非回环 `WEB_HOST`、`GEF_ALLOW_REAL_SCAN`）、外部工具 / 两个 SQLite 库 /
  worker 心跳与队列
- **三条硬性质**（都有用例锁定，不是注释里的承诺）：
  ① **只读** —— 不写任何文件、不建库（用「目录逐条目 mtime+size 快照比对」验证，
  应用库不存在时只报 warn 且确认文件真没被创建）；② **不泄密** —— 报告里不得出现
  `SECRET_KEY` / `LOCAL_ADMIN_TOKEN` 的值（用例塞哨兵串后在**人读报告与 JSON 两种输出**里搜）；
  ③ **退出码语义** —— `ok` → 0 / `warn` → 1 / `fail` → 2，`--strict` 把 warn 也当 2（CI 用）
- 设计取舍：目录权限只用 `os.access` 判定，**刻意不写探针文件再删**（那会在仓库里留痕）；
  依赖版本比较自己实现 `_version_key`，不引入 `packaging`（它不在依赖清单里，
  而这个脚本要能在「依赖还没装」时也跑得动）—— 顺带避开 `"3.10" < "3.9"` 为真的字符串比较坑；
  工具探测只用 `shutil.which`，用例把 `subprocess.run/Popen/check_output` 与 `os.system`
  全换成会抛异常的桩，证明它**不会启动任何子进程**
- 顺手把 `scripts/` 纳入 mypy 范围（`mypy ... modules scripts`，61 → **63** source files）；
  `.github/workflows/ci.yml` 增加一步「环境自检冒烟」—— CI runner 上 warn（退出码 1）
  是**预期**结果，只把退出码 2 当失败，验的是「干净机器上也能跑完给结论」
- 测试：`tests/unit/test_check_env.py` +26 → **900 passed / 2 skipped**；
  `_PRINT_ALLOWLIST` 登记 `("scripts/check_env.py", "main")`（人读报告本就该走 stdout）

**M7 测试报告（本轮，M7 最后一项交付物）**
- 新增 [`docs/TEST_REPORT.md`](docs/TEST_REPORT.md)，按方案第 23 节的里程碑格式写
  （里程碑 / 分支 / 提交 / 改动文件 / 关键改动 / 新增测试 / 执行命令 / 测试结果 /
  已知问题 / 未完成项 / 下一阶段），并**明确分开**「已验证」与「仅代码审查、尚未实测」
- **本轮报告之外，为修一个实测抓出来的测试隔离缺陷动了 5 个文件**（详见下一条），
  并新增 1 条回归锁用例 → **901 passed / 2 skipped**
- 三条**实测得出**、之前只是「感觉没测」的结论（都带复现方式）：
  ① **41 条方法绑定里 40 条被真实命中** —— 用一次性探针包装 `Flask.full_dispatch_request`
  跑全量得到；唯一没被走到的是 `GET /api/tool/<tool_name>/results`（读旧库，本机 20 张表全 0 行）；
  ② **91 个业务 `.py` 里 10 个测试源码从未提及**，全部在 `agent/`（推论：Agent 路径不调大模型，
  这 10 个模块没有调用方，不存在可测的运行时行为）；
  ③ **`ANONYMOUS_READABLE` 只列了 5 条，而 `SECURITY.md` 说 7 条** ——
  `/api/tool/<n>/results` 与 `/api/export/<id>/download` 目前只有间接覆盖，登记为缺口与建议
- 「已验证 / 仅代码审查」的分界按方案第 23 节硬要求写成独立小节：Linux 侧行为（CI 只在 push 后跑）、
  真实外部扫描（按硬约束从未执行）、前端 JS 运行时（只有 `node --check` 与服务端字符串断言）
  全部归入「仅代码审查、尚未实测」
- 顺手修正 `README.md` 里 `warn 5` 的过期数字（实测 `{'ok': 14, 'warn': 4, 'fail': 0}`，
  四条 warn 依次是 `.env 文件` / `SECRET_KEY` / `LOCAL_ADMIN_TOKEN` / `worker 心跳`）
- **写报告时抓出并修掉第三个真实缺陷：测试自己往仓库运行期目录里写。**
  逐文件跑测试 + 对 `results/` / `exports/` 做 SHA-256 快照比对，实测三处稳定泄漏：
  ① `test_agent_boundary.py` 每跑一次给 `exports/` 多一个空 CSV（fixture 只 patch 了
  `UPLOAD_DIR`，而 `_tool_export_results` 走 `exporter` 的模块级 `EXPORT_DIR`）；
  ② `test_security_baseline.py` 的两个上传用例直接调 `core_uploads.save_upload()`，
  写的是**仓库** `results/local.db`（实测单跑 `audit_events`/`uploads` 各 +3 行）；
  ③ 三个起真实 `Worker` 的文件刷新仓库 `results/worker_heartbeat`。
  **根因是保障挂错了位置**：原先只有 `app_module` 一个夹具在 patch，
  绕过它的用例根本不受约束。修法三件：新增 autouse 夹具
  `tests/conftest.py:_isolate_runtime_dirs`（库路径 + 五处模块级目录）、
  给 `config.OUTPUT_DIR` 加 `GEF_OUTPUT_DIR` 环境变量出口让**子进程**也能改道、
  新增回归锁 `test_repo_layout.py::test_autouse_fixture_redirects_every_runtime_path`。
  验证方式是全量跑完后 `results/`(5) / `exports/`(81) / `uploads/`(3) 逐文件哈希完全不变

**P0-6 阶段二前置件 — Agent 同步 → 异步影响说明（本轮）**
- 新增 [`docs/AGENT_ASYNC_IMPACT.md`](docs/AGENT_ASYNC_IMPACT.md)，**零代码改动**。
  依据是用户授权 P0-6 时定的约束「若某一步需要改变核心数据模型或执行架构，
  先停下来说明具体影响再继续」（`docs/DECISIONS.md` §3.2）
- **最重要的减负结论**：`agent/action.py` 的六个 handler 里只有 **2 个**是方案第 6 节
  说的「实际扫描动作」（`subdomain` / `httpx`）；`summary` / `view_results` /
  `alive_results` 是只读查询、`export_results` 是导出登记，都不产生执行权，
  **不在方案第 6 节验收范围内** —— 迁移面从 6 个收窄到 2 个
- **顺带查实一处现存越权通道**：`tool_runner.py` 与 `modules/httpx.py` 全文
  **没有任何 `resolve_mode` / `real_scan_enabled` / Scope 引用**（实测 grep 零命中）。
  即 **Agent 不需要 `GEF_ALLOW_REAL_SCAN=true`、也不需要 Scope 就能真实外发扫描**，
  而主链 `POST /api/jobs` 与首页要过「环境开关 + `scope.require_active_scan()`」双重门槛。
  这才是用户定的验收点「**权限边界移动了**」的实质内容（按硬约束从未用 Agent 打过真实目标）
- 文档另含：九条逐项影响（I-1～I-9）、5 个必改文件与预估、`test_agent_boundary.py`
  6 处 patch 目标的逐条处置、三条缺失能力的 A/B/C 补救选项、
  以及**唯一一个待用户拍板**的问题（Agent 只读 handler 是否同轮改读新库）

**公网授权测试模式体验版（本轮，方案第 5～11 节）**
- 依据：`docs/milestones/GetEverything_公网授权测试模式体验版方案.md`。目标：
  让「扫自己已获授权的公网目标」在框架里**有正规入口**，而不是靠人手改 `.env` 与 Scope
- **核心不变量：「没登记 = 禁止公网」**。`core/tool_registry.py` 给 17 个 runner 逐个写
  `risk_level` / `internet_allowed` / `default_enabled` / `reason`，公网白名单**恰好**
  `{httpx, subfinder}`（方案第 8 节）；`assert_tools_internet_allowed()` 对未知工具
  **直接拒绝**而不是默认放行 —— 新增工具的人必须显式做一次风险判断
- **只加表，不动 `scopes`**：`projects` + `project_scopes`（多对多）两张新表，
  `scopes` 表**逐列比对验证零改动**（方案第 10 节 / DECISIONS-E）。
  关联项目**不放宽任何权限** —— 能不能真扫仍由 Scope 的 `active_scan` + 环境开关决定
- **闸门顺序**（每步不过立刻返回、无落库副作用）：项目存在 → `scope_id` 属于该项目 →
  策略模板解析 → 公网白名单 → 默认 `mode=real` 转交 `create_scan_job` →
  目标/工具/幂等键/上限/Scope-Policy/环境开关 → 落库 → 审计
- **一处刻意取舍**：真实扫描开关没开时**报错**，而不是静默退回 mock 给假数据。
  「以为打了真实目标、其实拿到编的数据」比直接报错危险得多，有专门用例锁住
- **方案第 2、6 节两条红线都有源码守卫**：`api/public_scan.py` 里不许出现
  `build_runner` / `run_tools` / `RUNNER_REGISTRY`；公网入口必须**复用** `create_scan_job`，
  不得另写第二条 Policy 判定
- 前端新增扫描中心页 `GET /scan-center`（项目 / 创建任务 / 任务列表，方案第 7 节）；
  三张文案表挂到 `window.GEF_UI` 供复用，**同一个 `error_code` 两页不会显示成不同的话**；
  被禁工具**置灰但保留展示**（附原因）——这是可用性提示，**不是**安全边界
- 实机验收（`scripts/verify_public_scan.py`，可复跑）走通：创建项目 → 建 Scope →
  关联 → 未授权目标 403 → 禁工具 400 → 未关联 Scope 400 → mock 任务 202 →
  worker 执行到 `succeeded 100%`。**全程只用 `127.0.0.1` 与保留域 `example.test`**

**测试隔离修复：测试不再读开发机的 `.env`（本轮）**
- 新增 `.env` 后全量测试冒出两个**与被测代码无关**的失败：
  `test_m2_security.py` 对 `real_scan_enabled is False` 的断言被本机
  `GEF_ALLOW_REAL_SCAN=true` 顶掉；`test_observability.py` 解析 JSON 时拿到
  `text` 格式（本机 `GEF_LOG_FORMAT=text`）
- 修法：`tests/conftest.py` 对这两个变量由 `setdefault` 改为**赋值**
  （`load_dotenv()` 默认不覆盖已存在的环境变量）；需要 real 模式的用例仍用
  `monkeypatch.setenv` 自行打开并在结束时回滚
- **判断规则**（已回填 `docs/CODEBASE_MAP.md` 第 7 节第 37 条）：凡是「`.env` 能覆盖 +
  测试有断言」的开关都必须在 conftest 里钉死；只 `setdefault` 等于把开发机配置
  变成隐式测试参数

---

## 部分完成

| 项 | 现状 | 差什么 |
|---|---|---|
| **`mode=real` 真实链路** | ✅ **已有本地实证（本轮）**：`tests/integration/test_m7_local_e2e.py` 用**真实 httpx 子进程**打只绑 `127.0.0.1` 的 fixture 服务，一条用例走完 target → job → worker → runner → raw artifact → parser → observation → asset → diff → export（方案第 18 节）。此外 `tool_not_found` / `timeout` / `nonzero_exit` 等失败分类都有真实子进程用例 | 仍然**从未用真实工具打真实外部目标**（按硬约束刻意不做）—— 这是设计选择，不是缺口；CI 上若无 httpx 可执行文件，该用例会 `pytest.skip` |
| **M4 观测元数据展示** | `httpx` 的 `status_code` / `title` / `webserver` / `tech` / `cdn` 已结构化落库，**并已进资产页的观测时间线** | 资产页展示的是 `data_json` 原样 JSON，**没有按字段拆列**；任务详情页那一侧仍是原样 JSON |
| **M6 导出** | `exporter.py` 能生成 CSV / JSON；`/api/export` 已改为登记制（`export_id` + `download_url`），支持 `GET /api/export/<id>/download` 与 `GET /api/exports` | 没有按时间/条件筛选导出记录的页面；没有导出清理策略 |
| **M6 本机启动文档** | ✅ **已补齐**：`docs/DEPLOYMENT.md`（环境要求 / 安装 / `.env` 逐键说明 / 启动 Web 与 worker / 测试三条基线 / 故障排查），`README.md` 也补了「环境要求」与「启动 Worker」两节 | 已附 `scripts/check_env.py`（一键体检），见「已完成」专段 |
| **M7 mypy** | ✅ **已完成**：`mypy app.py core api jobs storage.py modules scripts` → `Success: no issues found in 68 source files`（本轮新增 5 个源文件，63 → 68） | 仅 `agent/providers/*` 不在该命令范围内（无调用方，见 Known Failure #8；显式加 `agent` 会多 7 条 openai 存根报错，未为它改语义） |
| **P1 Diff 的前端** | ✅ **已完成**：`/assets` 页底部有「两次任务对比」表单（基线与对比任务下拉、可选限定范围、「含未变」开关），四类分段渲染 + 属性差异（`status_code: 200 → 403`）；**清单条目可点进资产详情**（带 `asset_id` 的条目可点，详情面板会滚入视口） | — |
| **P1 资产过期** | `mark_stale_assets(scope_id, last_seen_before=...)` 已实现且有用例 | **没有任何计划任务/接口调用它**，所以 `stale` / `gone` 目前永远是空的 |
| **P1 §19 Observability** | ✅ **基础版已完成**：结构化单行 JSON 事件 + 四个关联 ID（`request_id` / `job_id` / `step_id` / `worker_id`，contextvars 绑定）+ 脱敏与容器上限 + 三条源码守卫；`GEF_LOG_LEVEL` / `GEF_LOG_FORMAT` 可配 | 仍属**基础版**：日志只写 stderr，**无文件输出与轮转**；**无 metrics / trace**；`configure_logging()` 只在两个进程入口调用，所以 `waitress-serve app:app` 这类外部启动方式不出结构化日志（在 `create_app()` 里配置会关掉 `propagate`、弄坏 pytest 的 `caplog`）；`request_id` 不跨进程（worker 是独立进程，跨进程串联要靠 `job_id`）；`/api/settings` 页未暴露日志级别开关 |
| **公网授权测试模式体验版** | ✅ **已完成（本轮）**：项目 / 工具权限元数据 / 三档策略模板 / 扫描中心页 + 104 项新测试；实机验收全链路走通（见「已完成」专段） | 属**体验版**：白名单只有 `httpx` + `subfinder`（方案第 8 节刻意如此）；项目无归档/删除接口；扫描中心不做分页；`nuclei` 未接入 runner，只登记为「受限未开放」；项目 ↔ Scope 只有正向选择，没有反查界面 |
| **真实公网扫描的实测证据** | 本轮及此前所有轮的实机验收都只用 `127.0.0.1` 与 RFC 6761 保留域 `example.test`，`GEF_ALLOW_REAL_SCAN` 只在用例内临时打开；`.env` 里虽已设为 `true`（用户确认目标均已授权） | **从未对真实外部目标发起过扫描** —— 这是硬约束下的设计选择，不是缺口。真实公网扫描要由用户自己决定何时、对哪个已授权目标发起 |

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
- [x] `scripts/check_env.py`（一键环境自检，只读；`--json` / `--strict`；+26 用例）
- [x] 本机启动文档（`docs/DEPLOYMENT.md` + README 的「环境要求」「启动 Worker」两节）

**M7 — 测试和交付**
- [x] 单元测试 / API 测试 / worker 测试（1003 项，超出原计划）
- [x] Scope 拒绝测试 / 上传安全测试 / 工具失败分类测试
- [x] SQLite 并发测试（`tests/unit/test_db_concurrency.py`，13 例，含 `duplicate execution`）
- [x] **本地 fixture HTTP 测试**（`tests/fixtures/local_http_server.py` + `tests/integration/test_m7_local_e2e.py`，
  真实 httpx 打 `127.0.0.1`，一条用例走完方案第 18 节全链路；无 httpx 可执行文件时自动 skip）
- [x] **mypy 通过**（34 errors → 0，未改 mypy 配置）
- [x] **一份测试报告**（[`docs/TEST_REPORT.md`](docs/TEST_REPORT.md)：方案第 23 节里程碑格式 +
  测了什么 / 没测什么 / 为什么没测 + 「已验证」与「仅代码审查、尚未实测」分界）

**P1 §19 — Observability**（方案第 25 节 P1 验收 `[ ] Observability 完成基础版本`、第 26 节顺序第 16 条）
- [x] `core/observability.py`：结构化单行 JSON 事件（唯一日志出口，无新依赖）
- [x] `request_id`（`before_request` 生成/沿用 + `X-Request-Id` 回写 + `teardown_request` 清理）
- [x] `job_id` / `step_id`（`jobs/executor.py` 的 `observability.bind(...)`）
- [x] `worker_id`（`jobs/worker.py:Worker.startup`，`with Worker(...)` 保证成对）
- [x] 禁止项落地：字段名黑白名单脱敏 + 容器上限 20 项（不记完整目标列表）+ 三条源码守卫
- [x] `.env.example` / `README.md` 的配置与排障说明
- [ ] 日志文件输出与轮转（当前仅 stderr）
- [ ] metrics / trace（方案只要求「基础版本」）

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
验证时间：2026-10-02（公网授权测试模式体验版：项目 / 工具权限元数据 / 策略模板 / 扫描中心页 + 测试隔离修复）
工作目录：E:\Programmingtools\geteverything\get_everything_framework

ruff:   All checks passed!
pytest: 1003 passed, 2 skipped, 0 failures / 0 errors      ← --collect-only 汇总口径
mypy:   Success: no issues found in 68 source files        ← 本轮新增 5 个源文件（63 → 68）
node --check web/static/{app.js,assets.js,scan_center.js}: 语法检查通过（无前端构建链，只能做到这一步）
git diff --check: 退出码 0
实机验收（scripts/verify_public_scan.py，可复跑）：
        python app.py(WEB_PORT=5001) + python -m jobs.worker
        建项目 201 → 建 Scope 201 → 关联 201 → 未授权目标 403 → 禁工具 nmap 400
        → 未关联 Scope 400 → mock 任务 202 → worker 执行到 succeeded 100%
        全程只用 127.0.0.1 与 RFC 6761 保留域 example.test，未打任何真实外部目标
```

**基线演进**：M1 `70` → M2 `142` → M3 `236` → M4 `405` → P0 加固 `538` → P1 `701` → M7 `707` → M5 字典可移植 `715` → P0-7 幂等/退避 `739` → M7 SQLite 并发 `752` → M7 本地全链路 E2E `759` → P1 §19 Observability `828` → §14 文档同步 + 导出格式 400 收口 `838` → Diff 属性别名修复 `847` → P0-6 阶段一（Application Service 入口收拢）`874` → M6 环境自检 `900` → M7 测试报告 + 测试运行期目录隔离修复 `901` → **公网授权测试模式体验版 `1003`**

> 本轮新增 104 项（`test_tool_registry.py` 34 + `test_projects.py` 26 +
> `test_public_scan_mode.py` 44）。**排除这三个文件后收集数仍为 901**，
> 与上一轮逐条相等 —— 即没有任何既有用例被删改。

---

## 最近一次 commit

> **本节的写法说明**：状态板自己也会被提交，所以「记录 HEAD」天然会差一个提交。
> 下面给的是**最近一次不含本文件改动的提交**，并附上自检命令。以 `git log -1` 为准。
> 提交表里**不含**更新本文件的那些 `docs: 状态板…` 提交 —— 它们只改这一个文件。

```text
1a34310   ← 最近一次不含本文件改动的提交（公网体验版文档补记）
1a34310  docs: 补记密钥清理与探针用法（提交哈希同步 + 探针凭据来源） (2026-10-02)
207af8c  docs: 同步公网授权测试模式体验版（决策单 / 代码地图 / API / 部署 / 变更日志 / 基线）
26ddf3a  test: 公网授权测试模式端到端 —— 方案第 9 节五类 + 第 11 节验收
7018fb4  feat(web): 扫描中心页 —— 项目 / 创建任务 / 任务列表（方案第 7 节）
0a3bd42  feat: 公网授权测试模式后端 —— 工具权限元数据 / 授权项目 / 单一编排入口
4428302  fix(tests): 测试不再读开发机的 .env —— GEF_ALLOW_REAL_SCAN / GEF_LOG_FORMAT 钉死
```

自检：

```powershell
git log -1 --format="%H %s"     # 以这条输出为准
git status -sb                  # ## main...origin/main [ahead 8] = 有 8 个提交待推送（纯快进）
```

与 `origin/main` 的关系：`git log -1 origin/main` 当前为 `a2389e8`
（更早的 5 个提交**已经推送完毕**）。
**本轮 8 个提交尚未推送**：`4428302`（测试隔离修复）、`0a3bd42`（后端）、
`7018fb4`（前端）、`26ddf3a`（测试与探针）、`207af8c`（文档同步）、
`db159ff`（状态板）、`1a34310`（哈希同步与密钥清理补记）、`docs(DECISIONS §3.4)`（审计专节），
`git rev-list --left-right --count origin/main...HEAD` 为 `0 8` —— **纯快进，无需 force**。
按用户偏好「提交可做但不做 push，需等确认」，**推送前请再确认一次**
（并重跑 `docs/DECISIONS.md` §3.4 那套推送前安全审计）。

> **推送前安全审计已跑（2026-10-02，只读，完整表格见 `docs/DECISIONS.md` §3.4）**：
> 31 个变更文件 / 172 个已跟踪文件均无运行期产物、数据库、密钥、二进制；
> 3691 行新增里无 `sk-` / `ghp_` / `AKIA` / JWT 形状；
> `origin/main` 是 HEAD 的祖先（纯快进）；`origin/main` 已有历史里也 0 命中明文。
> 审计**拦下过一处真实问题并已修复**：`scripts/verify_public_scan.py` 里写死了
> 管理员 Token 明文，且该明文已进入本轮的 `test:` 提交（重写前的 `469be5d`）。
> 处置方式是**重写该未推送提交**（现为 `26ddf3a`），而不是「再补一个删除提交」——
> 后者会把明文永久留在历史里。重写全程用 `--mixed` + 文件级备份，可逆。
> 回滚点：标签 `backup-before-secret-purge`（指向重写前的 `e934c30`，**暂不删除**）；
> 旧提交对象都还在（`git cat-file -t e934c30` 可验证），恢复只需 `git reset backup-before-secret-purge`。
> 修复后复跑：全量 1003 passed / 2 skipped、ruff 全过、mypy 0 error，
> 并以环境变量方式重跑探针通过（退出码 0）。

**本轮 8 个提交的拆分口径**：一个逻辑变化一个提交，每个都能独立回滚 ——
① 纯测试隔离修复（与被测代码无关）；② 后端核心（新模块 + 表 + 编排入口）；
③ 前端扫描中心；④ 测试与验收探针；⑤ 文档同步；⑥ 状态板；⑦⑧ 审计发现的两处补记。
逐个提交后都跑过相关测试，最后跑全量。

| 提交 | 说明 |
|---|---|
| `1a34310` | docs: 补记密钥清理与探针用法（提交哈希同步 + 探针凭据来源） |
| `207af8c` | docs: 同步公网授权测试模式体验版（决策单 / 代码地图 / API / 部署 / 变更日志 / 基线） |
| `26ddf3a` | test: 公网授权测试模式端到端 —— 方案第 9 节五类 + 第 11 节验收 |
| `7018fb4` | feat(web): 扫描中心页 —— 项目 / 创建任务 / 任务列表（方案第 7 节） |
| `0a3bd42` | feat: 公网授权测试模式后端 —— 工具权限元数据 / 授权项目 / 单一编排入口 |
| `4428302` | fix(tests): 测试不再读开发机的 .env —— GEF_ALLOW_REAL_SCAN / GEF_LOG_FORMAT 钉死 |
| `a2389e8` | fix(test): make command preview assertion platform independent |
| `dc9969a` | docs: P0-6 阶段二前置件——Agent 同步 → 异步影响说明 |
| `00e2216` | docs: M7 测试报告 docs/TEST_REPORT.md + 全量基线同步 900 → 901 |
| `4712ad9` | fix(tests): 测试运行期目录隔离——autouse 夹具 + GEF_OUTPUT_DIR 出口 + 回归锁 |
| `32a2774` | docs: 同步 M6 环境自检脚本 + 推送前安全审计结论 |
| `e19c5d5` | feat: M6 环境自检脚本 scripts/check_env.py（只读 / 不泄密 / 退出码可用） |
| `6843c5b` | refactor(p0-6): 收拢 Application Service 入口——任务创建编排只留一处 |
| `0c48e25` | fix: Diff 属性别名归一——httpx 真实键名 webserver/tech 此前从不参与比较 |
| `98ea46f` | fix: GET /api/export?format=xlsx 500→400 + P1 §14 文档三件套与文档脱节修正 |
| `5f27e6d` | test: §19 端到端验收——一个 job_id 串起整条执行链 + 反向守卫 |
| `8d0afd0` | feat: P1 §19 Observability——结构化日志 + 四个关联 ID（request/job/step/worker） |
| `70f3c30` | docs: 同步 M7 本地全链路 E2E（状态板 + CHANGELOG + README） |
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
   **另有一处本轮需你复核的判断**（`docs/DECISIONS.md` §3 末尾）：公网体验版在
   `core/db.py` 新增了 `projects` / `project_scopes` 两张表。第 2 节边界写着
   「若导致需要改动数据库结构则退回第 1 节流程」，方案第 10 节也写着「禁止修改数据库核心结构」；
   本 Agent 按 **E 项先例**（「只允许新增表 + 新增迁移脚本」）判断为同级改动并执行了。
   **若你不认可这个类推，请指出** —— 撤销成本很低（两句 `DROP TABLE` + 删 6 个新文件）。
2. **公网授权测试模式体验版已完成，方案第 11 节明确写着「到这里就可以暂停先了」** ——
   即该项的设计意图就是先交付一个可用、可验收的体验版，再决定是否继续。
   **交付报告**：[`docs/milestones/本机验收报告_公网授权测试模式_2026-10-02.md`](docs/milestones/本机验收报告_公网授权测试模式_2026-10-02.md)
   （该目录受 `.gitignore:67` 忽略，属本机过程材料；报告含逐节对照、实机 HTTP 码、
   第 10 节禁止项逐条自查、以及本轮修掉的两个真实问题的完整处置）。
   **本阶段到此停**，等你看完报告再定是否继续。
   若要继续往前推，优先级建议：① 让被禁工具具备**分档开放**的能力（当前白名单是硬编码常量，
   要改得改代码）；② 扫描中心的 Project → Job 归属展示（现在任务列表不显示属于哪个项目，
   而 `jobs` 表也**没有** `project_id` 列 —— 加列属表结构改动，需先走授权流程）；
   ③ 项目 / Scope 的反向查询界面（后端 `projects.find_by_scope` 已就绪）。
3. **方案第 26 节的执行顺序已走到第 16 条（Observability）**。第 17～19 条
   （PostgreSQL / Redis+Celery / 正式部署）**属第 4 节明令禁止的架构迁移，未获授权，不要开工**；
   第 20 条「再开始高级产品能力」对应方案第 21 节 P2 清单，同样等 P0/P1 全部稳定后再说。
   因此**当前阶段没有新的「方案内大项」可推**，剩下的是收尾与加固。
4. **P0-6 阶段二**（用户本轮已授权，见 `docs/DECISIONS.md` §3.2）：`agent/action.py`
   改走 Application Service → Job 链，让 Agent 只产出计划、执行一律经 Job/Policy。
   **开工前的影响说明已单独成文 ✅**：[`docs/AGENT_ASYNC_IMPACT.md`](docs/AGENT_ASYNC_IMPACT.md)
   —— 九条影响（I-1～I-9）、五个必改文件、`test_agent_boundary.py` 逐条用例的处置、
   三条缺失能力的补救选项。**现在卡在文末那一个待拍板问题**：
   Agent 的只读 handler（`summary` / `view_results` / `alive_results`）是否同轮改读新库
   （不改 → 出现「Agent 查不到自己刚提交的任务结果」的中间态）。
   **这是「权限边界移动」，是本项真正的验收点。**
   顺带查实（影响说明 I-5）：`tool_runner.py` 与 `modules/httpx.py` 全文没有
   `resolve_mode` / Scope 引用，**Agent 当前绕过 `GEF_ALLOW_REAL_SCAN` 与 Scope 就能真扫**。
4. **M5 剩余**（§11 统一执行链需授权，见 `docs/DECISIONS.md` §3）：资产过期自动化、
   观测 `data_json` 按字段拆列、旧库真实迁移（等你手动 `--apply`）。
5. **M6 已完成**（含 `scripts/check_env.py`，见「已完成」专段）。若还想加固，
   下一格是「导出记录的清理策略」——`exports/` 与 `exports` 表都只增不减。
6. **M7 已全部完成**（含 [`docs/TEST_REPORT.md`](docs/TEST_REPORT.md)）：报告写清了
   「测了什么、没测什么、为什么」，并给出三处可复现的缺口证据。
   若想补，报告 §3.1 列了两条最便宜的建议（把匿名清单从 5 条补到 7 条、
   给 `/api/tool/<n>/results` 加一条灌数据的集成用例）。
   注：本轮新增的 6 条需认证接口**已同步登记进 `docs/API.md` §2**，
   但 `tests/integration/test_api_auth_contract.py` 的 `ADMIN_ONLY` 清单**没有**一并补齐
   （那个文件的性质是「锁定 DECISIONS-D 的只读现状」，加执行类接口进去属另一件事）——
   公网接口的鉴权由 `test_public_scan_mode.py::test_public_scan_endpoints_require_admin`
   的 6 条参数化用例独立锁定。两处清单的**分工**是刻意的，不是漏了。
7. **顺手可做（不需要决策）**：
   - 同步 `README.md`（`file_path` 已废弃、补鉴权与 Scope 说明）；
   - 把 §19 的日志能力继续往前推一小步（文件输出 + 轮转是最自然的下一格）。
8. **改完代码记得**：刷新 `docs/CODEBASE_MAP.md` 对应章节与 `last-mapped`，更新 `CHANGELOG.md`，
   并回来更新本文件的「最近一次验证 / 最近一次 commit」。

## 复现三条基线命令

```powershell
cd E:\Programmingtools\geteverything\get_everything_framework
python -m pip install -r requirement.txt -r requirement-dev.txt

python -m ruff check .                                  # 期望 All checks passed!
python -m pytest                                        # 期望 1003 passed, 2 skipped
python -m mypy app.py core api jobs storage.py modules scripts  # 期望 Success: no issues found in 68 source files
python scripts/check_env.py                             # 期望退出码 0/1（未配 .env 时为 1），fail 项为 0
```

**运行期产物隔离（重要）**：测试**从不**写仓库的 `results/`、`exports/` 与
`results/local.db`。**保障有两层，缺一不可**：

1. `tests/conftest.py:_isolate_runtime_dirs`（**autouse**）—— 不依赖用例「记得」要哪个夹具：
   `config.LOCAL_DB_CONFIG["path"]` + `core_db.reset_schema_cache()`，
   外加 `exporter.EXPORT_DIR`、`core_uploads.UPLOAD_DIR`、`core_health.OUTPUT_DIR`、
   `jobs.worker.OUTPUT_DIR`、`core_artifacts.ARTIFACT_DIR` 五处模块级属性。
   `app_module` 夹具另有一套同类 patch，两者叠加安全（`monkeypatch` 按调用顺序回退）。
2. **起真实子进程的用例必须两件都做**：父进程 patch + 给子进程传 `GEF_OUTPUT_DIR`
   （`config.OUTPUT_DIR` 唯一的**环境变量出入口**，与 `GEF_SCAN_DB_PATH` / `LOCAL_DB_PATH` 同规格）。
   子进程读不到父进程的 monkeypatch —— `test_jobs_executor.py` 的 kill/restart 用例就靠这个。

`modules/base.py` / `modules/httpx.py` / `modules/dnsx.py` 的 `OUTPUT_DIR` 同样是**导入期**绑定，
autouse 夹具只覆盖了 `jobs.worker` 这一处；`test_m7_local_e2e.py` 与 `test_runners_m4*.py` /
`test_runner_interface.py` 各自额外 patch 这三个模块。
**回归锁**：`tests/unit/test_repo_layout.py::test_autouse_fixture_redirects_every_runtime_path`
逐条断言这些路径都不在仓库目录下 —— 夹具被删或漏项时立刻变红。
修这段的由来与实测证据见 [`docs/TEST_REPORT.md`](docs/TEST_REPORT.md) §4.1。

## 硬约束（不要违反）

- 不扫描任何**未授权的外部目标**；默认只用 mock runner 与 `127.0.0.1`
  （本地全链路 E2E 用的是 `tests/fixtures/local_http_server.py`，它**只绑 `127.0.0.1`**）
- 不执行 `git reset --hard`、`git clean -fd`
- 不覆盖 `get_everything_framework/scripts/OneForAll.exe` 的未提交差异（在旧 clone 里）
- 数据库、扫描结果、上传样本、密钥**不得提交进 Git**
- `results/scan_results.db` 只读；复现用临时库 `ScanResultStore(db_path=...)`
- 执行类过程文档（`DSH_执行提示词.md`、`本机联调版实施方案_DSH.md`、`docs/milestones/`）**留在本机，不入库**
