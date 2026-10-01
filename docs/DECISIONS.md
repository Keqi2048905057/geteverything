# DECISIONS.md — 夜间无人值守预授权单

> **用途**：无人值守（夜间）执行时，Agent 遇到「按技能第 4 节应停止并标记」的事项，
> 必须先来本文件查是否已预授权。**已预授权的项按「预填答案」直接执行并记入报告；
> 未预授权的项写入下方「本次未授权项」区并跳过，继续做其他任务。**
>
> **本文件由用户维护。** Agent 只读、只追加「本次未授权项」区，不修改用户填写的预填答案。
>
> 建立时间：2026-10 ｜ 依据：`docs/../PROJECT_STATE.md` 的 BLOCKED-B 九项决策
> 授权模式：**A 预填授权 + 白名单默认同意**（用户于 2026-10 选定）

---

## 0. 使用规则（Agent 必读）

1. **开工前先读本文件**，再读 `PROJECT_STATE.md` 与执行方案。
2. 命中「预授权 = ✅」的项：按预填答案执行，**不再等待用户**，但必须在 Nightly Execution Report 里逐条列出「我替你拍了哪个板」。
3. 命中「预授权 = ⚠️ 需明确授权」的项：**不执行**，写入本文件第 3 节的「本次未授权项」，然后**继续下一个任务**——不要停下来等，也不要为了"让任务继续"去绕过限制。
4. **永不预授权**的红线见第 4 节，无论本文件怎么写都不得执行。
5. 只有一种情况允许整轮停下：**所有剩余任务都需要 ⚠️/⬛ 授权**，此时输出报告并结束本轮。

---

## 1. 九项决策预授权表

| ID | 事项 | 风险 | 预授权 | 预填答案（Agent 照此执行） | 回滚方法 |
|---|---|---|---|---|---|
| A | 公开 fork 上的敏感数据（20 个条目 + 悬空提交 736ad76） | 🔴 高 | ⬛ **不可授权** | **不执行**。需 `delete_repo` scope，当前 Token 无权（API 返回 403）。仅记录为 BLOCKED-USER 并在报告中提醒。 | 不适用（用户手动：设 private / 删 fork） |
| B | `SECRET_KEY` / `LOCAL_ADMIN_TOKEN` 缺失时的行为 | 🟡 中 | ✅ | **保持现状**（自动生成 + 现有 warning）。只允许补测试与文档说明，**不得改变默认启动行为**。 | 恢复 `config.py` 默认分支 |
| C | 旧 clone（`E:\Programmingtools\get_everything_framework`）执行 `git rm --cached` 移除 19 个敏感文件 | 🟠 中高 | ⚠️ 需明确授权 | **默认不执行**。仅生成待执行清单（含文件列表与命令），不改索引。 | `git reset` 恢复索引（工作区文件不受影响） |
| D | `/api/tools` `/api/results` `/api/export` 是否加鉴权 | 🟠 中 | ✅（限文档与测试） | **不改鉴权行为**（避免破坏本机脚本兼容性）。允许：补测试锁定当前契约、在 README/SECURITY 写明「这三个接口当前匿名可读、属已知项」。 | 回退文档改动 |
| E | M5 的 `assets` / `observations` 表粒度 | 🟡 中 | ✅（限纯增量） | **采用「唯一资产 + 观测历史」双层**：`assets` 存去重后的唯一资产，`observations` 存每次观测（含 `first_seen` / `last_seen` / 来源 runner）。**只允许新增表 + 新增迁移脚本，不得改动现有表结构、不得删除既有数据**；旧库数据不受影响。 | `DROP TABLE assets/observations` 即可回到现状 |
| F | 旧库历史数据是否迁进新库 | 🟡 中 | ✅（限脚本） | **不执行真实迁移**。只写迁移脚本 + 单元测试（用临时库验证），旧库保持只读；真实迁移留待用户手动运行。 | 删除脚本与测试 |
| G | httpx 观测元数据先在哪露出 | 🟢 低 | ✅ | 先做**资产列表页**（`assets` 优先），再按目标汇总页补 httpx 元数据列。顺序可按实际实现成本调整。 | 回退页面改动 |
| H | 删除 `%TEMP%\gef_old_clone_full.bundle`（105.7 MB 历史备份） | 🟠 中 | ⚠️ 需明确授权 | **不删除**。只在报告中登记其路径与大小。若确需清理，改为移动到回收站并等待确认。 | 不适用（删除不可逆） |
| I | 两条已知 warning（`core/db.py:244` 未关闭文件、`test_security_baseline.py:59`） | 🟢 低 | ✅ | **允许顺手修复**，但必须补测试；修不了就记录原因，不得为消 warning 而扩大改动。 | 回退对应改动 |

---

## 2. 授权白名单（无需逐条预授权，一律可自主执行）

以下属技能第 3 节的低风险类别，夜间可直接做，只需记入报告：

- Bug 修复、异常处理、日志改进、资源释放（关闭连接 / 文件句柄）
- 补充与更新测试；修 mypy 类型错误（不改运行语义的行为）
- 边界条件修复；已有 API / Job / Runner / Scope / Policy 逻辑完善
- 小规模重复代码整理（不改业务边界）
- 文档与实际代码同步（README / CHANGELOG / CODEBASE_MAP / PROJECT_STATE）
- **只读操作**：`git status` / `git diff` / `git log` / 运行 pytest、ruff、mypy
- **普通 commit**（不含 push --force、不改写历史）

**边界**：以上任何一项若导致需要改动数据库结构、认证授权、Scope/Policy 判定或删除数据，则退回第 1 节流程。

---

## 3. 本次未授权项（Agent 追加，每轮更新）

> 格式：`[时间] ID — 摘要 — 为何需要授权 — 建议`

- `[本轮] P0-7a — jobs 表新增 idempotency_key 列 — 属第 2 节边界「需要改动数据库结构」，第 1 节九项均未覆盖 — 建议：单开一项预授权「jobs/job_steps 纯增量补列（ADD COLUMN，不动既有列与数据）」，与 E 项同规格。`
- `[本轮] P0-7b — 重试退避（backoff）需要 next_attempt_at 列 + worker 领取条件改动 — 同上：改表结构 + 改认领 SQL 语义 — 建议：与 P0-7a 合并预授权后再做；当前已实现 max attempts（5 次）作为兜底。`
- `[本轮] P1 §11 — 统一旧执行链（Legacy API → Adapter → Job Service → New Runner）—— 现状是 api/scan.py 同步扫描与 Job 链并存，两套模型不产同一份资产 — 合流要改的是「调用链拓扑」，不是单点实现，按第 4 节属大型重构 — 建议：单开一项预授权，并明确「旧同步接口保留、只是内部改走 Job Service」这一验收口径。`
- `[DEFERRED] P1 §13 — SQLAlchemy + Alembic 替换手写 SQL + 迁移 —— 引入新依赖并重写整个数据访问层，风险等级与第 4 节的「SQLite → PostgreSQL」同类 — **本机联调版明确不做**（见 3.1 本轮口径），从「待授权」改为「已延期」，不再逐轮追问。`
- `[DEFERRED] P1 §20（生产迁移门槛相关项）—— 属方案第 25 节「P1 后续生产迁移门槛」，已在执行方案里标注 `[DEFERRED]`，当前阶段不列入验收。`

> **已从本节移出**：`P0-6`（Agent 改走 Job Service）—— 用户本轮已授权，见 3.1。

### 3.2 用户本轮追加（2026-10-02，第二次弹窗确认）已授权的项

> 与 3.1 同规格：用户明确勾选后才生效，执行时按此口径，并在报告中复述。

| 项 | 授权口径（用户原话要点） | 记录 |
|---|---|---|
| **P0-6** | ✅ **授权**：改造 Agent → Job/Policy 执行链。**当前是人工监督执行**，不再要求把这项任务限制为夜间保守模式。约束：① 先梳理现有调用拓扑，再分阶段改造，不要一次性大范围重构；② 每完成一个关键步骤就运行相关测试，确认通过后再继续；③ 目标链路 `Agent → Intent / Plan → Application Service → Policy / Scope → Job → Worker → Runner`；④ `agent/action.py` **不得**再直接调用 `run_tools` 或具体 Runner 的 `run_scan`；⑤ 保持 Flask 架构（禁 FastAPI / PostgreSQL / Redis-Celery / React）；⑥ **若某一步需要改变核心数据模型或执行架构，先停下来说明具体影响再继续**。**验收点：「权限边界移动了」，而不只是「函数调用换了」。** | 用户弹窗选择 2（授权） |
| **Diff 属性别名** | ✅ **选择 1**：集中成 `ATTRIBUTE_ALIASES = {"server": "webserver", "webserver": "webserver", "technology": "technologies", "tech": "technologies"}` 这样的表，Diff 统一比较 canonical key，**禁止**写成散落的 `if key == "server": if key == "webserver": ...`。 | 用户弹窗选择 1 |
| **§25 与 P1 验收口径冲突** | ✅ **选择 1**：拆成「### P1 当前阶段验收」与「### P1 后续生产迁移门槛（当前延期，不属于本阶段验收）」，后者带 `[DEFERRED]` 标记；**并顺手把文档表述修掉**。 | 用户弹窗选择 1 |
| **未跟踪文件** | ✅ **维持现状，三者都不入库**：`.dsh/skills/geteverythingskill/`、`GetEverything_长期产品化总方案_Flask版.md`、`GetEverything_DSH执行方案_Flask版.md`。 | 用户弹窗多选结果 |
| **推送** | ⏸ **先做 push 前安全审计，再 push**（选 2）—— 与 3.1 的「不 push」不同：本轮允许推送，但必须先产出安全审计结论。 | 用户弹窗选择 2 |

### 3.1 用户本轮（2026-10-02，弹窗确认）已授权的项

> 以下是 Agent 主动弹出询问后、**用户明确勾选授权**的项，效力等同第 1 节预授权，
> 执行时按此口径，并在报告中复述。

| 项 | 授权口径 | 记录 |
|---|---|---|
| **P0-7a / P0-7b** | ✅ **授权**：`jobs` 表**纯增量补列**（`idempotency_key` / `next_attempt_at`），规格与 E 项相同——只 `ADD COLUMN`，**不动既有列、不删既有数据**；迁移脚本必须幂等且可空。 | 用户弹窗勾选「✅ 授权 P0-7a/7b：jobs 表纯增量补列」 |
| **P1 §16** | ✅ **授权**：CI 增加 `windows-latest` runner，只跑 `ruff check .` 与 `pytest -q`，不涉及任何密钥。 | 用户弹窗勾选「✅ 授权 §16：CI 加 windows-latest runner」 |
| **字典（wordlist）改法** | ✅ 用户选定：**环境变量可覆盖 + 仓库相对默认值**（`SHUFFLEDNS_WORDLIST` / `FEROXBUSTER_WORDLIST`）。 | 用户弹窗选项一 |
| **缺失字典的错误码** | ✅ 用户选定：**新增 `config_error`**（而非复用 `tool_not_found`）。 | 用户弹窗选项二 |
| **推送** | ❌ **不 push**：本轮结束时本地提交即可，等用户回来确认。 | 用户弹窗选项「不 push（推荐）」 |
| **未跟踪文件** | ✅ 仅 `docs/DECISIONS.md` 纳入 Git；`.dsh/skills/geteverythingskill/` 与两份 `GetEverything_*_Flask版.md` 方案文档**继续不跟踪**。 | 用户弹窗多选结果 |
| **P0-6** | ⏳ 本轮（3.1 时点）未勾选 → **已在 3.2 授权**，见下节。 | 同上 |

> **本轮已完成（P0-7a / P0-7b，按上表 3.1 口径）**：
> `core/db.py` 对 `jobs` 表**纯增量补列** `idempotency_key` / `next_attempt_at`
> （写进 `CREATE TABLE` 与 `_COLUMN_MIGRATIONS`，`ALTER TABLE ... ADD COLUMN`，
> 两列可空、既有行语义不变），并补了两个**非唯一**索引
> `idx_jobs_idempotency` / `idx_jobs_next_attempt`（刻意不用 UNIQUE：唯一性由
> `BEGIN IMMEDIATE` 事务内的「查重 + 插入」保证，避免给旧库升级引入
> 「历史脏数据导致建索引失败」的风险面；索引语句放在 `_migrate_columns` **之后**，
> 否则旧库升级会 `no such column`）。
> `core/jobs.py`：`create_job_with_status()` 幂等（同键的未终结任务返回同一个
> `job_id`，`reused=True`；键只挡 `queued`/`running`，任务落终态后键自动释放）、
> `retry_job()` 写 `next_attempt_at` 退避窗口、`claim_next_job()` 增加退避门槛
> 并在领走时清空窗口；新增 `normalize_idempotency_key()` 与
> `retry_backoff_seconds()`。`api/jobs.py` 接受可选 `idempotency_key`
> 并回 `reused`。
> **未动**：既有列、既有数据、Scope/Policy 判定、认证授权、状态机跃迁表。
> **可回滚**：`ALTER TABLE jobs DROP COLUMN` 在 SQLite 3.35+ 可用，
> 或直接忽略这两列（全为 NULL 即等价于改造前）。

> **本轮已完成（P1 §16，按上表 3.1 口径）**：`.github/workflows/ci.yml` 的
> `lint-and-test` 改为 `matrix.os: [ubuntu-latest, windows-latest]`
> （`fail-fast: false`），两个平台都跑 `ruff check .` + `pytest -q`；
> `mypy` 步骤只在 ubuntu 上跑（Windows 上的 mypy 结果与 Linux 一致，
> 没必要让 CI 时间翻倍）。**未涉及任何密钥、未改仓库 Settings**。

> **本轮已完成（DECISIONS-F，「限脚本」范围内）**：`core/migrate.py` +
> `scripts/migrate_legacy_results.py` + `tests/unit/test_migrate_legacy.py`。
> 只写了脚本与临时库测试，**未执行任何真实迁移**，旧库全程 `mode=ro`。
> 附带实测：本机 `results/scan_results.db` 的 17 张工具表 + `tool_results` **当前全为 0 行**，
> 所以即便执行 `--apply` 也是空结果 —— 真实迁移的收益要等旧库重新积累数据后才有。
>
> **本轮已完成（M5 字典可移植性，全落在第 2 节白名单内）**：
> 修 `config.py` 两个 `wordlist` 的开发机绝对路径/缺失文件（Bug 修复）+ 新增
> `ErrorCode.CONFIG_ERROR` + `BaseRunner.require_wordlist()` + 6 例回归测试 +
> README/`.env.example`/CODEBASE_MAP/CHANGELOG/PROJECT_STATE 同步。
> **未动**：数据库结构、认证授权、Scope/Policy 判定、任何既有数据；
> **未下载、未分发任何字典**（`SecLists/` 仍然缺失，修的是「路径怎么解析、缺失怎么报」）。
> 用户已在弹窗中确认改法（env 可覆盖 + 仓库相对默认）与错误码（新增 `config_error`），见 3.1。
>
> **本轮（M7 mypy 清零）无新增未授权项**：全部落在第 2 节白名单内 ——
> 「修 mypy 类型错误（不改运行语义的行为）」+「Bug 修复」。
> 唯一两处改了运行语义的，都是修**已被测试证明是缺陷**的行为，不是放宽边界：
> ① `modules/base.py` 的基类 `run_scan` 由「不存在」改为「抛 `NotImplementedError`」
> （原先子类漏实现时抛 `AttributeError`，与「跑通但零结果」不可区分）；
> ② `agent/action.py:_tool_httpx` 的 `items` 由 URL 字符串列表改为元数据字典列表
> （原先在有存活结果时必抛 `AttributeError`）。两者都补了回归测试。
> **未动**：Scope / Policy / 审计 / 认证 / 数据库结构 / `pyproject.toml` 的 mypy 配置。
>
> **本轮已完成（M7 本地全链路 E2E，全落在第 2 节白名单内）**：
> 新增 `tests/fixtures/local_http_server.py`（只绑 `127.0.0.1` 的确定性 fixture 服务）、
> `tests/integration/test_m7_local_e2e.py`（方案第 18 节要求的 target → job → worker →
> runner → raw artifact → parser → observation → asset → diff → export 全链路）、
> 以及必需的 `tests/__init__.py`（修 site-packages 的常规包 `tests` 顶掉本仓库命名空间包
> 导致 `import tests.fixtures...` 失败）。
> 期间发现并修复一个**既有缺陷**：`core/artifacts.py:read_artifact()` 用
> `scrub_command()` 处理原始证据，而该函数的语义是「命令预览」（末尾截到 300 字符），
> 于是 `/api/artifacts/<id>` 返回的 `text` 永远只有头 300 字符 —— 既违反方案第 6.2 节
> 「结果详情能看到原始证据」，也让 `truncated=False` 变成假信息。
> 改法：抽出 `_redact()`，新增 `scrub_text()`（只脱敏、默认不截断，可选 `limit`），
> `read_artifact()` 改用它；`scrub_command()` 行为与默认长度**未变**（既有 9 条用例原样通过）。
> **未动**：Scope / Policy / 审计 / 认证 / 数据库结构 / 脱敏正则本身 / `MAX_COMMAND_PREVIEW`。
> **未扫任何外部目标**：E2E 的目标是 fixture 自己监听的 `127.0.0.1`，Scope 也只放行
> `127.0.0.0/8`，`GEF_ALLOW_REAL_SCAN` 只在用例内 `monkeypatch.setenv`。
>
> **本轮已完成（P1 §19 Observability，全落在第 2 节白名单内）**：
> 方案第 19 节要求的四件套全部落地，**第 2 节白名单第 1 条「日志改进」直接覆盖**，
> 因此**没有新增未授权项**。新增 `core/observability.py` 作为唯一日志出口
> （stdlib `logging`，**未引入任何新依赖**）：一行一个 JSON 事件 +
> `request_id` / `job_id` / `step_id` / `worker_id` 四个关联字段（`contextvars` 绑定）+
> 字段名黑白名单脱敏 + 容器上限 20 项。
> 接线都在既有文件里加事件，未改任何业务判定：`app.py`（`before_request` /
> `after_request` / `teardown_request` + `X-Request-Id` 回写）、`core/errors_handlers.py`、
> `api/jobs.py`、`jobs/executor.py`、`jobs/worker.py`、`agent/action.py`
> （原来那句会倒出整份结果的 debug `print` 改成结构化事件）。
> **未动**：Scope / Policy / 审计 / 认证 / 数据库结构 / 脱敏正则本身 / 既有 `print` 的人读输出
> （启动横幅、工具适配器提示、迁移脚本 stdout 全部保留原样）。
> **刻意保留的现状**：401 的 `error_message` 里 `X-Local-Token` 后面的词会被脱敏规则打码
> （失败即关闭的取舍，完整原文仍在 HTTP 响应体与 `audit_events` 里）；
> 结构化日志只写 stderr，无文件输出与轮转。
> 期间修掉一处**测试间污染**：M7 E2E 的 `_drain_worker` 只 `startup()` 不 `shutdown()`，
> `worker_id` 上下文泄漏到同线程的下一条用例（单独跑绿、全量跑炸）；
> 现改用 `with Worker(...)`，并在 `conftest.py` 加了 autouse 的 contextvar 清理兜底。

---

## 4. 永不预授权的红线

无论本文件如何填写，以下操作在无人值守期间**一律不执行**：

- `git reset --hard`、`git clean -fd`、`git push --force`、`git filter-repo`、大规模 history rewrite
- 删除远程仓库、删除数据库、删除扫描结果、删除上传样本、删除历史备份
- 放宽 / 绕过 Scope、Policy、审计、认证或权限边界
- 默认允许真实外部目标扫描；对非本地目标执行扫描；使用真实凭证 / API Key 测试
- 允许任意文件路径访问或任意命令执行
- Flask → FastAPI、SQLite → PostgreSQL、Worker → Redis/Celery、Jinja → React 等大型迁移

> 这些即使被写进「预填答案」也不生效——本节的效力高于第 1 节。

---

## 5. 早晨验收清单（用户回来看这里）

1. 打开 Nightly Execution Report：看「完成的任务」与「未完成的问题（BLOCKED / DEFERRED）」。
2. 看本文件第 3 节「本次未授权项」：需要你拍板的都在这里，逐条回复即可。
3. 看 `PROJECT_STATE.md` 的「最近一次验证」是否被更新为最新时间与数字。
4. 若 E 项落地：确认只新增了表与迁移脚本，旧表与旧数据未被动过。
5. `git log --oneline -10` 检查提交粒度是否为「一个逻辑变化 + 对应测试」。
