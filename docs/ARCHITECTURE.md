# ARCHITECTURE.md — 架构说明

> **本文回答一个问题**：这套系统由哪些层组成、一次扫描请求怎么在层与层之间流动、哪些边界是刻意冻结的。
> 涉及具体接口字段请看 [`API.md`](API.md)；如何在本机把它跑起来请看 [`DEPLOYMENT.md`](DEPLOYMENT.md)；
> 逐文件的代码地图与「症状 → 排查位置」索引表在 [`CODEBASE_MAP.md`](CODEBASE_MAP.md)。
>
> 本文只描述**当前代码的真实结构**（工作树 `<仓库根>/get_everything_framework`，分支 `main`）。
> 凡与 README 或其它文档冲突处，以代码为准，并在文末「已知不一致」列出。

---

## 1. 一句话定位

一个**本机联调版**资产收集框架：Flask + Waitress 暴露 Web/API，`modules/` 把 17 个外部安全工具
各自封装成一个 `BaseRunner` 子类，任务以「单表即队列」的 SQLite 异步队列交给**独立 worker 进程**
串行执行，原始证据落 artifact、结构化观测归并成 asset/observation 两层模型，可做两次任务的 Diff 与导出。

技术栈（全部来自实际文件）：Python 3.10+（CI 用 3.11）、Flask 3.1.3 + Werkzeug 3.1.8 + Jinja2 3.1.6、
Waitress 3.0.2、SQLite（标准库 `sqlite3`，无 ORM）、原生 JS（无框架、无 CDN、无打包）。

---

## 2. 分层架构

```
入口层   app.py（模块级 app = Flask(...)；__main__ 调 waitress.serve）
         app.py:create_app()（供 waitress / 测试复用的工厂，返回同一个单例）
         jobs/worker.py（独立进程入口：python -m jobs.worker）
   │
Web/API  api/__init__.py  api_bp = Blueprint("api", url_prefix="/api")
         api/auth.py（登录/登出/会话）· api/health.py（独立 bp，挂根路径 /health，免登录）
         api/scopes.py · api/jobs.py · api/assets.py · api/scan.py
         api/results.py · api/tools.py · api/upload.py · api/settings.py
         页面路由：GET|POST / · GET /login · GET /assets（Jinja2 模板）
   │
Auth     core/auth.py（单一本地管理员 Token / HttpOnly Session / X-Local-Token）
         core/security.py（弱 SECRET_KEY → 进程级一次性密钥，绝不回落固定值）
         core/errors_handlers.py（统一把 AppError / HTTPException 转成带 error_code 的 JSON）
   │
Policy   core/policy.py 唯一判定入口，四个时机各判一次：
Scope      ① Job 创建 validate_job_targets  ② Step 执行前 validate_step_target
           ③ DNS 解析后 validate_resolved_address  ④ 重定向后 validate_redirect_target
         core/scope.py（排除优先、拒 * 全放行、环回/私网默认拒）
         core/safety.py（mock/real 解析 + GEF_ALLOW_REAL_SCAN 环境开关）
   │
Job      core/jobs.py 单表即队列：claim_next_job（BEGIN IMMEDIATE）、租约、
         幂等键、退避、cancel/retry/recover_stale_jobs、显式状态跃迁表
         core/db.py WAL + busy_timeout=5000 + BEGIN IMMEDIATE
   │   ← Web 进程与 worker 进程之间只通过这个数据库通信
Worker   jobs/worker.py（轮询认领、心跳文件、信号处理、空闲退出）
         jobs/executor.py（execute_job：与进程无关的执行逻辑，测试可同步直调）
   │
Runner   modules/registry.py（RUNNER_REGISTRY，17 个工具，import 期全量加载）
         modules/base.py（build_command / parse_output / run / _resolve_path /
                        require_wordlist / _run_subprocess）
         tool_runner.py（编排：load_targets / load_tools / run_tools / run_single_tool）
   │   subprocess（带超时、杀进程树、命令预览脱敏）
Artifact core/artifacts.py（stdout/stderr/输出文件三类落盘 + 登记；read 只回截断+脱敏内容）
         core/runner_result.py（RunnerResult / Observation / ToolHealth + scrub_text）
   │
Asset    core/canonical.py（subdomain/host/ip/cidr/url/port/service 归一化规则）
Obs      core/assets.py（唯一资产 + N 条观测；record_observation / diff_jobs）
         core/migrate.py（旧库 → 新库只读迁移）
   │
存储     旧库 results/scan_results.db（storage.py，按工具建表 19 张，只读历史数据）
         新库 results/local.db（core/db.py：scopes/audit_events/uploads/jobs/job_steps/
                              job_events/artifacts/exports/assets/observations）
```

17 个 Runner：`subfinder assetfinder amass amass_intel oneforall alterx shuffledns dnsx httpx
naabu nmap gospider katana waybackurls feroxbuster dirsearch enscan`。

---

## 3. 各层职责与关键文件

| 层 | 关键文件 | 职责 | 不做什么 |
|---|---|---|---|
| 入口 | `app.py`、`jobs/worker.py` | 起 WSGI / 起 worker 循环；两个进程入口都调 `observability.configure_logging()` | 不做业务判定 |
| Web/API | `api/*.py` | 解析请求、调 `require_admin()`、拼目标、把结果 jsonify | 不自己写 Scope 判定（一律转交 `core/policy.py`） |
| Auth | `core/auth.py`、`core/security.py` | 单一 Token 校验（`hmac.compare_digest`）、Session、弱密钥处理 | **无 RBAC、无用户表、无权限分级** |
| Policy/Scope | `core/policy.py`、`core/scope.py`、`core/safety.py` | 目标规范化与授权判定；`mock/real` 模式解析 | 不写数据库、不碰 Flask（便于 worker/CLI 复用） |
| Application Service | `core/application.py` | **执行类业务的统一入口**：`create_scan_job()` 编排 目标解析 → 查重 → 限流 → Policy → 模式 → 落库 → 审计 → 结构化日志 | 不碰 Flask（不解析请求、不决定状态码）、不自实现 Scope 判定、不直接执行工具 |
| Job | `core/jobs.py` | 队列、状态机、租约、幂等键、退避 | 不执行任何工具 |
| Worker | `jobs/worker.py`、`jobs/executor.py` | 认领任务、逐步执行、写进度与心跳 | 不提供 HTTP 接口 |
| Runner | `modules/`、`tool_runner.py` | 构造命令行、跑子进程、解析输出为 `RunnerResult` | 不做 Scope 判定（由 executor 在调用前复检） |
| Artifact | `core/artifacts.py` | 原始证据落盘 + 登记 + 安全读取 | 不把服务器路径写进任何响应 |
| Asset/Obs | `core/canonical.py`、`core/assets.py` | 归一化去重、观测时间线、Diff | 不改旧库表结构 |
| 存储 | `core/db.py`、`storage.py` | 新库（可写）/ 旧库（只读） | 旧库不新增表、不加 WAL |

---

## 4. 一次扫描请求的完整流动

以 `POST /api/jobs`（`mode=mock`）为例：

```text
1. app.py:before_request        → 绑定 request_id（沿用入站 X-Request-Id 或新生成）
2. app.py:after_request         → 把同一个 X-Request-Id 回写响应头
3. api/jobs.py:create_job()     ← 视图层只做「认证 + 解析 JSON + 拼响应」
   a. require_admin()                      → 无 Session / X-Local-Token → 401 unauthenticated
   b. 交给 core/application.py:create_scan_job()   ← Application Service（唯一编排入口）
        · resolve_targets / _split_list    → targets 显式列表 或 upload_id（受控上传）
        · load_tools(tools)                → 不在 RUNNER_REGISTRY 一律 400
        · 超过 SCAN_LIMITS["max_targets_per_job"]（20）→ 400
        · validate_job_targets(scope_id, ...) → 缺失 400 / Scope 不存在 403 / 任一越界 403（整体拒绝）
        · resolve_mode(mode)               → real 需 GEF_ALLOW_REAL_SCAN=true，否则 403
        · mode == real → scope.require_active_scan()   ← 第二道开关
        · jobs_store.create_job_with_status() → 落 jobs + 展开 job_steps 快照（BEGIN IMMEDIATE）
        · audit.record(job.created) + observability.log_event(job_created)
   c. 返回 202 {ok, job_id, status:"queued", mode, total_steps, scope_id, reused}
   ※ 到这里为止**从未执行任何工具**
4. worker 进程（另一个操作系统进程）：
   jobs/worker.py:Worker.tick()
   → jobs_store.claim_next_job(worker_id)   → BEGIN IMMEDIATE + UPDATE ... WHERE status='queued'
   → jobs/executor.py:execute_job(job_id)
       · observability.bind(job_id=...)     → 之后所有事件自动带 job_id
       · 逐步：bind(step_id=...)
           mock → core/mock.py:run_mock()   （确定性假数据，绝不调用真实工具）
           real → validate_step_target() 复检 Scope
                  → modules.registry.build_runner() → BaseRunner.run(target)
                  → 落 artifact（stdout/stderr/output）
                  → 写 observations_json
       · 每步 renew_lease + 刷心跳文件
   → jobs_store.finish_job(status=...)      → 走 ALLOWED_TRANSITIONS 校验
5. P1 汇聚：观测经 core/canonical.py 归一化 → core/assets.py 写 assets / observations
6. 前端：app.js 每 3 秒轮询 /api/jobs 与 /health；资产页 assets.js 调 /api/assets
```

关键点：

* **创建与执行是两个进程**。`POST /api/jobs` 只落库即返回，Web 进程不会阻塞；worker 没启动时任务会
  一直停在 `queued`（这正是 `/health` 要能区分「Web 正常但 worker 未启动」的原因）。
* **Scope 判四次**，不只判创建时那一次：任务创建后 Scope 被删或被收紧，执行期复检会拦下它，
  Runner 根本不会被调用，该步骤记 `scope_violation`。
* **编排只有一处**（P0-6 阶段一）。上面 3.b 的那些步骤原先内联在 `api/jobs.py:create_job` 里，
  首页表单为了做同一件事还得反向导入 api 层的私有函数 `_resolve_targets` 并把 Policy 判定抄一遍；
  现在统一收在 `core/application.py:create_scan_job()`，视图函数只剩参数与响应。
  三条源码守卫（`tests/unit/test_application_service.py`）阻止它退化回内联。

---

## 5. 三条扫描入口的差别

| 入口 | 异步 | Scope | 真实工具 | 备注 |
|---|---|---|---|---|
| `POST /api/jobs` | ✅ 立即 202 + `job_id` | 必填 `scope_id` | `mock`（默认）不调用；`real` 需双开关 | 推荐入口 |
| `POST /api/run` | ❌ 同步阻塞在请求线程里 | 必填 `scope_id` | 同上 | 历史入口，仍保留 |
| 首页表单 `POST /` | ✅ 创建 job | 必填 Scope（下拉框） | 固定 `mock` | `app.py:index()` |

前两者**共用 `core/application.py:create_scan_job()` 这同一个编排入口**（P0-6 阶段一；
`/api/run` 是同步旧链，尚未合流，见第 11 节），Scope 判定一律转交 `core/policy.py`，
不各写一份。

---

## 6. Agent 只能提出计划，不能直接执行

`agent/` 是一条**独立于主链的旁路**：

```text
首页 action=chat → agent/service.handle_agent_message()
  → agent/action.py:AgentAction.run()
    → agent/intent.py:analyze_intent()   正则关键词判定意图
    → agent/planner.py:build_plan()      固定模板拼步骤
    → _execute_tool()                    复用 tool_runner / storage / exporter
```

两条必须掌握的边界：

1. **运行时完全不调用大模型**。`agent/client.py`、`agent/providers/*`、`agent/model_result.py`
   全都没有调用方；`AgentAction(client=...)` 只赋值不用；`SYSTEM_PROMPT` 只被塞进
   `conversation_history[0]` 就再也没有下文。规划 = 正则 + 模板。因此「模型超时 / 返回格式错」
   这类症状在当前路径**不可达**。
2. **Agent 不接受任意文件路径**（P0-3）。请求里出现 `file_path` 直接拒绝，只收受控 `upload_id`
   （经 `core/uploads.py:resolve_targets_file()` 换取真实路径）。

**P0-6 阶段一已完成，阶段二未做**：

* **已做**：创建扫描任务的编排收拢到 `core/application.py:create_scan_job()`
  （`POST /api/jobs` 与首页表单共用），视图函数不再内联 Policy/落库/审计。
* **未做**：Agent 依旧直接调 `tool_runner.run_tools` / `HttpxRunner.run_scan`，没有改走 Job Service。
  也就是说「提出计划」与「发起执行」在 Agent 这条路上还没有彻底分离——目标仍然要过 Scope，
  但绕过的是 Job/队列这一层。主链（`/api/jobs`）不受此影响。
* **阶段二的影响**（开工前需确认）：接上 Job 链意味着 **Agent 执行异步化** ——
  回复里给 `job_id` 而不是内联结果，`agent_cli.py` 与首页 `action=chat` 的交互语义随之改变，
  `tests/unit/test_agent_boundary.py`（现在 monkeypatch `agent.action.run_tools` /
  `agent.action.HttpxRunner`）需按新边界重写。
* 本项真正的验收点是**「权限边界移动了」**，而不只是「函数调用换了」。

---

## 7. Worker 与 Web 解耦

| 维度 | Web 进程 | Worker 进程 |
|---|---|---|
| 启动 | `python app.py`（waitress） | `python -m jobs.worker` |
| 通信 | 只读写 `results/local.db` | 同左 |
| 阻塞关系 | 互不阻塞：worker 挂了 Web 照常用，只是任务停在 `queued` | 同左 |
| 存活信号 | `/health` 的 `database` / `queue` 字段 | 心跳文件 `results/worker_heartbeat`（mtime 30 秒内算 `ok`） |
| 崩溃后果 | — | 租约到期 → 新 worker 启动时 `recover_stale_jobs()` 把任务标 `interrupted`，**不会静默消失** |

刻意不做分布式 worker、不做消息队列、不做 Redis/Celery（见第 11 节）。

---

## 8. 数据模型

### 8.1 双库（**最容易踩的坑**）

| 库 | 路径（可用环境变量覆盖） | 归属 | 写入方 |
|---|---|---|---|
| 旧扫描结果库 | `results/scan_results.db`（`GEF_SCAN_DB_PATH`） | `storage.py`，按工具建表 19 张 | 仅 `mode=real` 的 `tool_runner.run_tools`；本机联调期间视为**只读历史数据** |
| 新本机应用库 | `results/local.db`（`LOCAL_DB_PATH`） | `core/db.py` | scopes / audit_events / uploads / jobs / job_steps / job_events / artifacts / exports / assets / observations |

新库：`PRAGMA journal_mode=WAL` + `busy_timeout=5000` + `BEGIN IMMEDIATE`。
旧库：连接显式关闭（`storage.py:_connect()`）+ 连接级 `busy_timeout=5000`，但**仍无 WAL**。

### 8.2 任务状态机

```
queued ──claim──> running ──┬─> succeeded    （全部步骤成功；绝对终态，不可重跑）
                            ├─> partial      （部分失败，error_code=partial_success）
                            ├─> failed       （全部失败）
                            ├─> timeout      （全部超时）
                            ├─> cancelled    （用户在步骤边界取消）
                            └─> interrupted  （租约过期 / worker 优雅退出，可 retry）
```

约束：`core/jobs.py:ALLOWED_TRANSITIONS` + `can_transition()` 拒绝跳步（例如 `queued → succeeded`）；
同名状态视为幂等；`MAX_ATTEMPTS = 5`；retry 后写 `next_attempt_at`（5/10/20/40… 封顶 300 秒），
退避中的任务仍是 `queued`。

### 8.3 资产两层模型

```
Asset（每个 canonical_key 一行；first_seen 永不被覆盖）
  ↓
Observation（每次「某工具在某次任务里看到了什么」一行）
  ↓
Job / Run / Tool
```

Diff 只看 `DIFFABLE_ATTRIBUTES`，且比较的是**归一化后的 canonical key**
（`status_code` / `title` / `webserver` / `technologies` / `url`）：
`core/assets.py:ATTRIBUTE_ALIASES` 把工具两侧的不同写法（如 `server` / `webserver` /
`web_server`、`technology` / `technologies` / `tech`）先映射到同一个名字再比。
**注意这不是可有可无的兼容层**：`modules/httpx.py` 实际产出的键名是 `webserver` / `tech`，
早期白名单写的是 `server` / `technology`，于是这两项在真实链路上**永远报不出变化**
（已修，见 `CHANGELOG.md`）。时间戳之类的易变字段一律排除，否则每次扫描都会报 changed。

---

## 9. 认证与授权（架构视角）

* 只有**一个**本地管理员身份：`LOCAL_ADMIN_TOKEN`。三种携带方式（详见 [`API.md`](API.md) §2）：
  登录页换 HttpOnly Session、`X-Local-Token` 请求头、POST 表单 / JSON 体的 `token` 字段。
* **没有 RBAC、没有用户表、没有多账号**。「已认证」是唯一的权限档位。
* `core/auth.py:require_admin()` 是所有写/执行类接口的唯一守卫；**25 处调用点全部显式写出**
  （24 个 `api/*` 路由 + 页面 `app.py:_require_admin_for_page`），
  没有装饰器、没有集中白名单——因此**新增接口必须自己记得加**。
* 真实扫描是**双开关**：环境变量 `GEF_ALLOW_REAL_SCAN=true` **且** 目标所属 Scope 的
  `active_scan=true`，缺一即 403 `scope_violation`。
* **没有 Scope 就拒绝扫描**：`scope_id` 缺失 → 400，Scope 不存在或任一目标越界 → 403（整体拒绝，
  不部分执行）。不存在「隐式全放行」。

当前**有意保持**的匿名只读接口见 [`API.md`](API.md) §4 与 [`SECURITY.md`](../SECURITY.md)。

---

## 10. Observability（方案第 19 节）

`core/observability.py` 是**唯一日志出口**，一行一个 JSON 事件：

```json
{"ts":"2026-10-01T02:27:38+00:00","level":"INFO","event":"job_step_finished",
 "job_id":"job_xxx","worker_id":"host-1234","step_id":"step_xxx",
 "tool":"httpx","target":"example.test","status":"succeeded","found_count":3,"duration_ms":1200}
```

* **四个关联字段自动带上**，用 `contextvars` 绑定（waitress 多线程下互不串号）：

  | 字段 | 绑定位置 | 还原因素 |
  |---|---|---|
  | `request_id` | `app.py:_bind_request_context`（`before_request`） | `teardown_request` |
  | `job_id` | `jobs/executor.py:execute_job` | `with bind(...)` 退出 |
  | `step_id` | `jobs/executor.py` 步骤循环 | `with bind(...)` 退出 |
  | `worker_id` | `jobs/worker.py:Worker.startup` | `Worker.shutdown` |

* **配置**：`GEF_LOG_LEVEL`（默认 `INFO`）、`GEF_LOG_FORMAT`（默认 `json`，可选 `text`）。
  值非法时退化为默认值，不让日志配置错误把服务搞挂。
* **脱敏**：字段名命中 `api_key/token/secret/password/...` 只记占位符；其余文本过
  `core.runner_result.scrub_text()`；单字段超 500 字符截断；容器最多记 20 项
  （不把完整目标列表写进公共日志）。
* **排障方法**：拿响应头 `X-Request-Id` 去 grep 日志即可捞出这一跳的全部事件。

### 10.1 已知局限（务必如实掌握）

1. **只有两个进程入口调 `configure_logging()`**：`app.py` 的 `__main__` 与 `jobs/worker.py` 的
   `__main__`。用 `waitress-serve app:app` 这类外部方式启动 **不会**输出结构化日志
   （`create_app()` 刻意不配置，以免破坏 pytest 的 `caplog`）。
2. **只写 stderr**：没有文件输出、没有轮转；要落盘请在外层重定向或交给采集器。
3. **`request_id` 不跨进程**：worker 是独立进程，无法继承 Web 的 `request_id`；
   跨进程串联靠 `job_id`（Web 记 `job_created`，worker 记 `worker_claimed_job` / `job_started` /
   `job_step_finished` / `job_finished`，同一条链路共享 `job_id`）。
4. **`job_step_finished` 的 `duration_ms` 与库里的 `job_steps.duration_ms` 语义不同**：前者是墙钟测量
   （mock 步骤也会补），后者在 mock 下保持 `NULL`（契约要求 mock 不写假数据）。

---

## 11. 架构被刻意冻结

当前架构是**有意选定的稳定态**，不是「还没来得及升级」。以下迁移在方案与预授权单里都被明确
**禁止在无人值守/未经授权期间执行**（见 [`DECISIONS.md`](DECISIONS.md) §4 红线）：

| 冻结项 | 现状 | 不做的原因 |
|---|---|---|
| Flask → FastAPI | Flask 3 + Waitress | 迁移只换壳，不解决任何当前缺口，却会动全部路由与错误模型 |
| SQLite → PostgreSQL | 两个 SQLite 文件 | 本机单机联调，新库已 WAL + `busy_timeout` + `BEGIN IMMEDIATE` |
| Worker → Redis / Celery | `jobs` 单表即队列 + 独立 worker 进程 | 引入中间件会让「一条命令跑起来」失效 |
| Jinja2 → React | 服务端出骨架 + 原生 JS 取数 | 无构建链、无 CDN、同源请求，够用且安全 |
| 手写 SQL → SQLAlchemy + Alembic | 手写 SQL + 纯增量补列 | 重写整个数据访问层，风险等级同上 |

**可以继续做的**：修 bug、补测试、补文档、在既有边界内完善 Job/Scope/Policy/Runner 逻辑。
**需要先获授权的**：任何改动数据库结构、认证授权、Scope/Policy 判定语义或删除数据的变更。

---

## 12. 已知不一致与薄弱点（架构相关）

文档与代码不一致（详细清单也在 [`API.md`](API.md) §8）：

1. `README.md` 说「12 个 RESTful 接口」，实际 `/api/*` 路由为 **34 条**（见 [`API.md`](API.md)）。
2. `README.md` 的「鉴权现状」只列了 **3 个**匿名只读接口，实际为 **5 个**
   （还含 `/api/databases`、`/api/exports`）。
3. `README.md` 的常见问题 Q6 仍在说「`/` 报 `TemplateNotFound`，index.html 是占位文件」——
   该问题已在 M1 修复，模板与静态资源都在，`GET /` 返回 200。
4. `README.md` 的测试基线写 `759 passed`，实际为 `826 passed, 2 skipped`
   （[`CODEBASE_MAP.md`](CODEBASE_MAP.md) §9.8 已是 826）。
5. `docs/SECURITY.md` **不存在**——方案建议的四份文档里，安全文档实际位于仓库根
   [`SECURITY.md`](../SECURITY.md)。
6. `api/scopes.py` 注释称 404 会被转成 `error_code=bad_request`，实测为 `not_found`
   （与 `core/errors_handlers.py` 的状态码映射表一致，注释过时）。
7. `api/tools.py` 的 docstring 举例 `database` 含 `table_name` / `record_count`，实际键是
   `table` / `result_column` / `category`（以 `storage.py:get_tool_databases()` 为准）。
8. `config.py` 中 `TOOL_COMMANDS`、`DEFAULT_*_TOOLS`、`GO_BIN_WINDOWS/POSIX`、
   `SCAN_LIMITS["max_concurrency"/"retry_count"/"max_output_bytes"]` 均无调用方（定义即死值）。
9. `modules/registry.py` 在 import 期全量加载 17 个 adapter：任何单个 adapter 的依赖缺失都会让
   `/api/tools`、`/api/run` 等一起 500，没有按需加载或容错注册。
10. `storage.py` 旧库仍无 WAL；`modules/shuffledns.py` 硬编码 `"dnsx"` 命令名且 `_WILDCARD_CACHE`
    永不清理；`agent/action.py:RATE_LIMIT_CACHE` 是进程级全局限流且只增不删。

其余运行期坑（残留输出文件、`GET /api/artifacts` 截断、字典缺失报 `config_error` 等）见
[`DEPLOYMENT.md`](DEPLOYMENT.md) 的故障排查一节。

---

## 13. 相关文档

| 文档 | 回答什么 |
|---|---|
| [`API.md`](API.md) | 每条接口的方法/路径/认证/参数/响应/错误码 |
| [`DEPLOYMENT.md`](DEPLOYMENT.md) | 环境、安装、`.env`、启动 Web/Worker、健康检查、故障排查 |
| [`CODEBASE_MAP.md`](CODEBASE_MAP.md) | 逐文件代码地图 + 29 条「症状 → 排查位置」+ 36 条已知薄弱点 |
| [`DECISIONS.md`](DECISIONS.md) | 预授权单与永不授权的红线 |
| [`../SECURITY.md`](../SECURITY.md) | 安全策略、已修/待修问题清单 |
| [`../PROJECT_STATE.md`](../PROJECT_STATE.md) | 项目状态板（当前阶段、下一步） |
