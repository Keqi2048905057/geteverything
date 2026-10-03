# TEST_REPORT.md — 测试报告（M7 交付项）

> ⚠️ **基线已前移：本文件 §0–§5 描述的是 M7 那一轮（901 条用例）的历史快照，
> 未逐处改写以免抹掉当时的实测记录。当前基线是 1296 条，本轮增量见文末
> [§12 方案第 13 节「后端安全边界」的测试缺口回填](#12-方案第-13-节后端安全边界的测试缺口回填1296)。**
> 两处数字不一致时，以 §12 与 `PROJECT_STATE.md` 的「最近一次验证」为准。

> **这份报告回答三件事**：**测了什么**、**没测什么**、**为什么没测**。
> §0 是方案第 23 节规定的里程碑输出；§1–§2 是可复现的事实；
> §3 是诚实的缺口清单（缺口的价值不低于覆盖）；§4 说明这套测试凭什么可信。
>
> 生成日期：2026-10-01（本机系统时间；`PROJECT_STATE.md` 与 `CODEBASE_MAP.md` 把同一轮记为 `2026-10-02`）
> 被测提交：`e19c5d5`（`32a2774` 为其文档同步）｜ 本报告对应的提交：`docs/TEST_REPORT.md` 独立提交，见 §0
> 环境：Windows 11 + Python 3.11.9，`pytest 9.0.3` / `ruff 0.16.8` / `mypy 2.3.1`
> 依据：`本机联调版实施方案_DSH.md` §M7、`GetEverything_DSH执行方案_Flask版.md` §15/§17/§18/§23

---

## 0. 里程碑输出（方案第 23 节格式）

**里程碑**：M7 —— 测试与交付（本报告即该里程碑的最后一项交付物）

**分支**：`main`

**提交**：`e19c5d5`（被测代码）／`32a2774`（配套文档同步）

**改动文件**：本轮新增 `docs/TEST_REPORT.md`，**并修掉一个由本报告实测抓出来的测试隔离缺陷**。
报告所描述的被测对象来自前面几轮的提交，主要是：

```text
get_everything_framework/core/application.py      P0-6 阶段一：Application Service 入口
get_everything_framework/api/jobs.py              P0-6 阶段一：路由改为调 Service
get_everything_framework/app.py                   P0-6 阶段一：首页扫描分支改调 Service
get_everything_framework/core/assets.py           Diff 属性别名归一（webserver / tech）
get_everything_framework/web/static/assets.js     Diff 属性中文标签
get_everything_framework/core/artifacts.py        scrub_text()：证据读取不再被预览规则截断
get_everything_framework/scripts/check_env.py     M6 环境自检（本轮报告的主要新增被测对象）
get_everything_framework/scripts/migrate_legacy_results.py
.github/workflows/ci.yml                          三态自检冒烟 + scripts 纳入 mypy
```

本轮**为了修测试隔离缺陷**动的四个文件（缺陷详情见 §4.1）：

```text
get_everything_framework/tests/conftest.py                autouse 夹具 _isolate_runtime_dirs 补齐运行期路径
get_everything_framework/tests/unit/test_agent_boundary.py  去掉重复 patch，改由 autouse 夹具统一兜底
get_everything_framework/tests/unit/test_jobs_executor.py   子进程用例补 GEF_OUTPUT_DIR
get_everything_framework/config.py                          OUTPUT_DIR 支持 GEF_OUTPUT_DIR 改道
get_everything_framework/tests/unit/test_repo_layout.py      新增回归锁：autouse 夹具必须钉住全部路径
```

**关键改动**：把「测试从不污染仓库」从**约定**变成**可执行的断言** ——
原先只有 `app_module` 一个夹具在 patch，绕过它的用例照样往仓库写；
现在 autouse 夹具兜底全部运行期路径，并由新增用例盯着它不许退化。

**新增测试**：新增 1 条用例（`test_repo_layout.py::test_autouse_fixture_redirects_every_runtime_path`），
报告覆盖 31 个 `test_*.py` / **901** 条用例，逐文件计数见 §2.1。

**执行命令**：

```powershell
cd get_everything_framework
python -m pytest -q
python -m ruff check .
python -m mypy app.py core api jobs storage.py modules scripts
python scripts/check_env.py
```

**测试结果**：`901 passed / 2 skipped`（0 failed / 0 error），ruff 全过，
mypy 0 error（63 source files），`check_env.py` 退出码 1（= warn，fail 0）。

**已知问题**：见 §3（6 类缺口：§3.1 未覆盖路由 / §3.2 未接线模块 / §3.3 运维脚本 /
§3.4 真实外部扫描 / §3.5 无覆盖率数字 / §3.6 其他未测项）。
其中风险评级为「中」的两条：前端无浏览器测试、`/api/tools` 单工具坏掉会整体 500。

**未完成项**：
① P0-6 阶段二（Agent → Job/Policy 执行链改造，需要先提交异步影响说明）；
② 导出记录清理策略 —— 实测本机 `exports/` 目录里躺着 **81 个文件、无一例外全是 47 字节**
（只有表头行，说明本机历史上每次导出都是空库导出），每个导出没有删除入口；
写入这些文件的 `core/exports.py:register_export()` 会同时写 `exports` 表，
但本机 `local.db` 的 `exports` 表当前是 **0 行**（历史轮次重建过库），
也就是说**目录与表已经不一致** —— 这正是不清理策略的后果。
③ 本报告的 §3.1 建议两项（补 `ANONYMOUS_READABLE` 的两条路径、给
`/api/tool/<n>/results` 补功能用例）。

**下一阶段**：先做 P0-6 阶段二的影响说明，再按 `GetEverything_DSH执行方案_Flask版.md`
第 26 节的执行顺序推进。

### 0.1 「已验证」与「仅代码审查、尚未实测」的分界（方案第 23 节硬要求）

**已验证（本机真跑过、可复现）**

* 901 条用例在 Windows 11 + Python 3.11.9 上全绿，`--junitxml` 计数一致；
* 41 条方法绑定里有 40 条被测试**真实命中**（探针统计，见 §3.1）；
* 8 线程真机并发（`test_db_concurrency.py`）、真实子进程（httpx、kill 进程树、worker 重启）；
* 本地全链路 E2E：真实 httpx 子进程 → `127.0.0.1` fixture → 证据 → 观测 → 资产 → diff → 导出；
* `check_env.py` 在**未配 `.env` 的本机**上退出码为 1、`fail 0`，且不改动任何文件；
* `ruff` / `mypy` / `node --check` 三条静态检查的实际输出（见 §1）。

> ⚠️ **口径提醒**：上面第一条（901）与第二条（41/40）是 **M7 当时的快照**。
> 后续里程碑只在本文件末尾**增量追加**（§6 Phase 2、§7 Phase 3、§8 Phase 4、
> §9 规划方案 Phase 2、§10 规划方案 Phase 3、§11 规划方案第 6 节），刻意不改写前面的实测记录
> —— 所以本节的数字**以对应的增量小节为准**。
> 截至规划方案第 6 节（自动匹配授权资产）：用例 **1293**（1291 passed / 2 skipped）、
> 方法绑定 **50 / 49 被命中**，未走到的仍只有 `GET /api/tool/<tool_name>/results` 一条
> （§3.1 的这条结论在各轮中一直成立，见 §8.2 与 §9.2 的重跑记录）。

**仅代码审查、尚未实测**

* Linux 侧的全部行为：CI 只在 push 后跑，**本地无法验证** Linux 上的两条 POSIX 用例
  是否真的通过（它们是「Windows 跳过、Linux 才跑」）；
* 真实工具（subfinder / amass / dnsx / naabu … 17 个）对真实外部目标的行为 ——
  **按设计从未执行，且不应执行**（硬约束）；
* 前端 JS 的运行时行为：只有语法检查与服务端字符串断言，**没有浏览器执行过**；
* `scripts/run_local.ps1` / `install_windows.ps1` / `install_linux.sh` 的实际执行；
* 长时运行行为：心跳漂移、日志增长、`exports/` 无限增长的实际影响；
* 上述「已验证」之外的任何结论（例如「并发是安全的」这种一般性判断）都是**推断**，
  只在被用例覆盖的具体场景上成立。

---

## 1. 复现方式与实测结果

四条命令，都在 `get_everything_framework/` 下执行：

> **本节是 M7 那一轮的原始实测记录（基线 901 / mypy 63 文件）**，
> 后面各轮的增量见 §6（Phase 2）、§7（Phase 3）、§8（Phase 4）、§9（规划方案 Phase 2）。
> 当前基线以 §9 与 `PROJECT_STATE.md` 的「最近一次验证」为准 ——
> 保留本节原样，是为了让「当时到底跑出了什么」可被追溯。

```powershell
python -m pytest -q                # 901 passed, 2 skipped, 0 failures / 0 errors
python -m ruff check .             # All checks passed!
python -m mypy app.py core api jobs storage.py modules scripts   # Success: no issues found in 63 source files
python scripts/check_env.py        # 环境自检（只读）；退出码 0/1/2
```

实测记录（本机系统时间 `2026-10-01 16:5x`；`PROJECT_STATE.md` / `docs/CODEBASE_MAP.md` 里
把这一轮记作 `2026-10-02`，两者指同一轮工作，日期以本机系统时间为准）：

| 项 | 结果 | 备注 |
|---|---|---|
| pytest | **901 passed / 2 skipped / 0 failed / 0 error** | 计数取自 `--junitxml`；PowerShell 管道下看不到 pytest 的汇总行，**不要**据此判断失败 |
| 全量耗时 | **约 118 秒**（wall clock） | 最慢单例 4.55 s（本地全链路 E2E） |
| ruff | All checks passed! | 规则集保守（`E4/E7/E9/F`，见 `pyproject.toml`），只拦语法/未定义名/未用导入类问题 |
| mypy | 0 error / **63 source files** | 本轮把 `scripts/` 纳入范围（61 → 63）；**未改** `pyproject.toml` 的 mypy 配置 |
| `check_env.py` | **退出码 1**（`{'ok': 14, 'warn': 4, 'fail': 0}`） | 4 条 warn 依次是 `.env 文件`、`SECRET_KEY`、`LOCAL_ADMIN_TOKEN`、`worker 心跳`，均为本机实况；**没有 fail 项** |
| `node --check` | 通过 | `web/static/app.js`、`web/static/assets.js`；项目无前端构建链，只能做语法检查 |
| `git diff --check` | 退出码 0 | 无行尾空白问题 |

**2 个 skip 是什么**：`tests/unit/test_runner_interface.py::test_run_subprocess_detaches_child_process_group`
与 `::test_kill_process_tree_never_kills_own_process_group` —— 两条都只在 **POSIX** 上成立
（用 `setsid` / `killpg` 语义验证「杀进程树不会连调用方一起杀」），在 Windows 上主动跳过，
Linux CI runner 上会真跑。**它们不是「跑不了」，是「这条机器不适用」。**

**未启用覆盖率工具**：`coverage` / `pytest-cov` 都不在依赖清单里，也未安装。
因此 §3 的「覆盖」用的是两种**粗口径代理**（路由命中、模块提及），
而不是行覆盖率百分比。这一点另列为缺口，见 §3.5。

---

## 2. 测了什么

### 2.1 规模与分布

| 层 | 文件数 | 用例数 | 说明 |
|---|---|---|---|
| `tests/unit/` | 20 | **668** | 纯函数、数据层、状态机、runner 适配器、源码守卫 |
| `tests/integration/` | 11 | **233** | Flask 测试客户端 / 真实 worker / 真实子进程 |
| `tests/fixtures/` | 1 | — | 只绑 `127.0.0.1` 的标准库 `ThreadingHTTPServer`（被 E2E 用例当服务端） |
| **合计** | **31 个 `test_*.py`** | **901** | 648 个 `def test_*`，其中 46 个带 `@pytest.mark.parametrize`，展开后为 901 条 |

用例最多的五个文件：`test_runners_m4_rollout.py`（93）、`test_canonical.py`（78）、
`test_jobs_store.py`（72）、`test_observability.py`（50）、`test_m3_jobs_api.py`（45）。
用例最少的三个：`test_m7_local_e2e.py`（2）、`test_smoke.py`（5）、`test_m2_page_scan.py`（7）
—— **数量少不等于价值低**，第一条是唯一走完整执行链的真实子进程用例。
（注意：`test_repo_layout.py` 只有 5 个 `def test_*`，但靠 `parametrize` 展开成 18 条，
所以按「函数数」和按「用例数」排序的结论不同，本报告一律用**用例数**。）

### 2.2 逐层要点（只列「为什么这条测试重要」）

**统一执行链与状态机**
* `test_jobs_store.py`（72）：`ALLOWED_TRANSITIONS` 显式跃迁表、租约恢复、cancel / retry、
  `MAX_ATTEMPTS`、退避窗口增长与封顶、幂等键复用与释放。
* `test_jobs_executor.py`（27）：mock 全场景、**real 步骤在调 runner 之前重新校验 Scope**
  （创建任务后 Scope 被改/删也不继续跑）、逐步续租、取消在步骤之间生效、
  **资产落库失败不让任务变 failed**。
* `test_db_concurrency.py`（13）：真起 8 线程 —— 并发建任务/写审计不撞锁、
  读写混合不读半截事务、**8 个 worker 抢 24 个任务不重不漏**、
  同一幂等键 8 线程只建 1 个任务、锁被持有时是「等」而不是立刻 `database is locked`
  （另有反证用例）。

**安全与权限边界**
* `test_policy.py`（40）：四个 Policy 入口全覆盖 —— 越界/排除/无中继、
  **解析后回环与私网地址默认拒绝**、重定向出界与危险 scheme。
* `test_m2_security.py`（23）：上传大小/扩展名/空文件、失败时回收目录、
  **上传目录按 UUID 隔离且两次上传绝不碰撞**、任意 `file_path` 被拒、设置写入有审计。
* `test_api_auth_contract.py`（24）：**逐条钉住匿名只读现状** —— 参数化覆盖
  5 个匿名只读绑定（`ANONYMOUS_READABLE`）与 15 个写/执行绑定的匿名拒绝（`ADMIN_ONLY`）。
  **注意**：`SECURITY.md` 说匿名只读接口共 7 个，而这份清单只列了 5 个；
  差额的 2 条目前只有间接覆盖，见 §3.1。
* `test_export_contract.py`（30）：导出不返回服务器路径、`../` 穿越被拒、
  非法 `format` 返回 400 且校验清单与 `exporter.SUPPORTED_FORMATS` **同源**。
* `test_security_baseline.py`（22）：弱 `SECRET_KEY` 检测、进程内密钥稳定、
  `config.py` 里不存在硬编码默认密钥。

**Runner 与工具失败分类**
* `test_runner_interface.py`（25）：可执行文件缺失 → `tool_not_found`；空输出 →
  `no_results`（**不是失败**）；退出码非零 → `unknown_error`；超时 → `timeout`；
  执行前清理残留输出文件；**超时会杀掉 `.cmd` 包装脚本后面的孙进程**（Windows 真实子进程）。
* `test_runners_m4_rollout.py`（93）：17 个适配器的 `build_command` / `parse_output` /
  错误码映射，含「字典配了但文件不存在 → `config_error` 且**不启动子进程**」。

**资产模型与 Diff**
* `test_canonical.py`（78）：七类 canonical key 规则 + 方案的验收原话
  （三个 URL 形式折叠成一个 key）。
* `test_assets.py`（43）：两层模型（一行资产 + N 条观测）、`first_seen` 永不覆盖、
  `last_seen` 每次推进、`metadata` 只补缺失键、**Diff 用 httpx 真实键名
  `webserver`/`tech` 也能报出变化**（这条是回归缺陷后补的，见 §2.3）、
  `mark_stale_assets` 只改状态不删数据。

**可观测性**
* `test_observability.py`（50）：脱敏规则、容器上限 20 项、`job_` ID 不被误打码、
  三条**源码守卫**（AST 扫描）。
* `test_observability_chain.py`（19）：**一条 `job_id` 串起 Web 创建 → 执行 → worker 领取
  的全部事件**（方案 P1-5 验收原话），以及反向守卫「12 个目标的整份清单不得进日志」。

**端到端**
* `test_m7_local_e2e.py`（2）：**真实 httpx 子进程**打只绑 `127.0.0.1` 的 fixture，
  一次走完 `target → job → worker → runner → raw artifact → parser → observation
  → asset → diff → export`（方案第 18 节原话）。**不碰任何外部目标。**
  本机没有 httpx 可执行文件时该用例 `skip` 而非失败。

**环境自检（本轮新增）**
* `test_check_env.py`（26）：自检脚本的**只读性**（目录 mtime+size 快照比对、
  库不存在时不建库）、**不泄密**（哨兵串在人读报告与 JSON 两种输出里都不出现）、
  退出码三态与 `--strict`、**探测过程不得启动任何子进程**（把 `subprocess.*` 换成会抛异常的桩）。

### 2.3 三个由这一轮抓出来的真实缺陷（回归价值证明）

| 缺陷 | 症状 | 为什么以前没被发现 | 现在的守卫 |
|---|---|---|---|
| Diff 属性别名不匹配 | Web Server / 技术栈变了却报不出 `changed`，**只有 `status_code`/`title`/`url` 能报** | 原用例用的是**文档体例**键名（`server`/`technology`），而 httpx 实际产出 `webserver`/`tech`，测试全绿而线上失效 | `test_assets.py::test_diff_matches_httpx_real_key_names` + 别名表不变量 |
| 证据读取被预览规则截断 | `/api/artifacts/<id>` 的 `text` 永远只有头 300 字符，`truncated` 却仍是 `False` | 这条路径此前没有端到端用例走进去 | `test_m7_local_e2e.py` 的 artifact 断言 |
| 测试自己污染仓库运行期目录 | `exports/` 每跑一次多一个空 CSV、`results/local.db` 每跑一次多 3 行、`worker_heartbeat` 被真实 Worker 刷新 | 保障只挂在 `app_module` 一个夹具上，**绕过它的用例（只用 `local_db` 或不用夹具）不受约束**；而全量跑绿与「有没有写仓库」是两件事 | autouse 夹具 `_isolate_runtime_dirs` + `test_repo_layout.py::test_autouse_fixture_redirects_every_runtime_path`（详见 §4.1） |

> 这三条是「测试报告」值得单独写一节的理由：**测试的数量不等于测试的强度**。
> 第一条是 901 条用例里的 1 条，它证明了「用例写的是真实键名还是文档体例」比
> 「用例有几条」重要得多；第三条更讽刺 —— 前两条是测试**找到**的缺陷，
> 第三条是测试**自己制造**的缺陷，而且同样在 900 条全绿的掩盖下稳定复现了很久。

### 2.4 方案第 15 节测试矩阵逐项对照

| 方案要求 | 覆盖情况 | 代表用例 |
|---|---|---|
| API：auth | ✅ | `test_api_auth_contract.py`（24 条，参数化覆盖全部方法绑定） |
| API：scope | ✅ | `test_m2_scope_enforcement.py`、`test_policy.py` |
| API：job | ✅ | `test_m3_jobs_api.py`（45 条）、`test_application_service.py`（27 条） |
| API：asset | ✅ | `test_assets_api.py`（28 条） |
| API：artifact | ✅ | `test_m4_runner_result.py`、`test_m7_local_e2e.py` |
| API：export | ✅ | `test_export_contract.py`（30 条） |
| 安全：path traversal | ✅ | `test_security_baseline.py::test_resolve_targets_file_rejects_path_traversal`（4 例参数化）、`test_export_contract.py::test_export_id_with_path_traversal_is_rejected` |
| 安全：arbitrary file path | ✅ | `test_m2_security.py::test_run_rejects_raw_file_path`（4 例）、`test_agent_boundary.py`（4 例） |
| 安全：upload overwrite | ✅ | `test_security_baseline.py::test_two_uploads_never_collide` |
| 安全：secret leakage | ✅ | `test_observability.py::test_no_print_of_secret_shaped_values_in_source`、`test_runner_result.py::test_scrub_text_redacts_secrets_within_long_text`、`test_check_env.py::test_report_never_contains_secret_values` |
| 安全：scope bypass | ✅ | `test_policy.py`、`test_jobs_executor.py::test_real_step_rechecks_scope_before_calling_runner` |
| 安全：redirect bypass | ✅ | `test_policy.py`（重定向出界 / 排除域 / 危险 scheme 参数化） |
| 安全：local/private IP | ✅ | `test_policy.py::test_resolved_loopback_is_rejected_unless_explicit`、`::test_resolved_private_address_is_rejected` |
| Worker：restart | ✅ | `test_jobs_executor.py::test_worker_process_killed_then_restarted_does_not_lose_job`（真实子进程） |
| Worker：stale lease | ✅ | `test_jobs_store.py::test_recover_stale_jobs_marks_interrupted`、`::test_recover_stale_jobs_keeps_live_lease` |
| Worker：retry | ✅ | `test_jobs_store.py` 的 retry 系列 + `test_m3_jobs_api.py` 的接口层 |
| Worker：cancel | ✅ | `test_jobs_store.py` + `test_jobs_executor.py`（步骤之间生效） |
| Worker：duplicate execution | ✅ | `test_db_concurrency.py::test_concurrent_claim_takes_each_job_exactly_once`、`::test_concurrent_claim_records_exactly_one_started_event` |
| Runner：success / no result / tool missing / timeout / non-zero / parse error / stale output | ✅ 七项齐全 | `test_runner_interface.py`、`test_runner_result.py`、`test_runners_m4.py`、`test_runners_m4_rollout.py` |
| 平台：Windows + Linux | ✅ | `.github/workflows/ci.yml` 的 `matrix.os: [ubuntu-latest, windows-latest]` + `fail-fast: false`；`mypy` 只在 ubuntu 跑一次 |

**矩阵无缺项。** 下面 §3 列的是矩阵**之外**的缺口。

---

## 3. 没测什么，以及为什么

### 3.1 唯一一条没有任何用例走到的路由

用一次性探针（`flask.Flask.full_dispatch_request` 包装 + 全量跑一遍）统计**测试真实命中**的
方法绑定，与 `app.url_map` 声明的 41 条方法绑定对比：

```text
声明的方法绑定: 41
测试命中的:     40

== 没有被任何用例走到的方法绑定 ==
    GET /api/tool/<tool_name>/results
```

* **它是什么**：按工具名查旧库（`ScanResultStore`）里的历史结果。实测匿名 GET 返回
  **200**（未知工具名返回 400），属 `SECURITY.md` 登记的 7 个匿名只读接口之一。
* **为什么没测**：它读的是 `results/scan_results.db`，而测试从设计上就不碰仓库里的旧库
  （`AGENTS.md` 硬约束：该库只读；且本机该库 **20 张表全为 0 行**，实测确认，测了也是空结果）。
  **注意一处文档缺口**：`tests/integration/test_api_auth_contract.py` 的
  `ANONYMOUS_READABLE` 清单里**没有**列出这条路径（只列了 `/api/tools`、`/api/databases`、
  `/api/results`、`/api/export`、`/api/exports` 五条），而 `SECURITY.md` / README 的说法是 7 条
  （多出 `/api/tool/<n>/results` 与 `/api/export/<id>/download`）。
  也就是说：**「7 个匿名只读」这个数字目前只有 5 条被参数化用例直接钉住**，
  另外 2 条是靠 `test_export_contract.py` 的 `/api/export` 匿名用例与代码审阅间接覆盖。
* **风险评级**：低。它是只读查询，无写路径、无目标解析、无执行。
* **建议**（两条，都很便宜）：
  ① 把 `/api/tool/subfinder/results` 与 `/api/export/<id>/download` 补进
  `ANONYMOUS_READABLE`，让「7 条」这个数字与代码一致；
  ② 若要覆盖功能本身，写一条「用临时 `ScanResultStore` 灌 1 行 → 查得到」的集成用例，
  约 20 行。**本轮两条都未做** —— 前者会改动既有契约测试的语义，
  后者需要额外搭旧库 fixture，均超出「补一份测试报告」的范围，故如实登记为缺口。

> 另有 1 条命中但不在声明路由里的记录：`GET /api/export/exp_x/../../etc/download`
> —— 那是穿越防护用例**故意打的 404 路径**，属预期。

### 3.2 测试源码从未提及的模块（10 个，全部在 `agent/`）

```text
agent/model_result.py            agent/providers/{deepseek,ollama,openai_compat,qwen}.py
agent/plan_state.py              agent/skills/osint_recon.py
agent/strategy_templates.py      agent/system_prompt.py
agent/target_ranker.py
```

* **根源是一条已知事实**：Agent 运行路径**不调用任何大模型**。
  `agent/client.py` 与 `agent/providers/*` 都没有调用方，
  `AgentAction.__init__(client=...)` 只赋值不用，
  `SYSTEM_PROMPT` 只被塞进 `conversation_history[0]`。
  规划完全由 `agent/intent.py` 的正则 + `agent/planner.py` 的模板决定。
* **因此不测的理由不是「懒得测」，而是「没有可测的运行时行为」**：
  给一套未被接线的 provider 写单测，测的是「这段死代码本身能不能跑」，
  而不是「系统会不会按预期工作」。这类测试会给出一张好看但具有误导性的覆盖图。
* **已在文档中标注**：`CODEBASE_MAP.md` §9.13 末说明了为什么不把 `agent` 加进 mypy 范围
  （会多出 7 条 openai 存根报错），`AGENTS.md` 的「五个高频坑」第 4 条也点名了这一点。
* **什么时候该补**：真要把 LLM 接进规划时 —— 那时这 10 个模块会同时获得调用方，
  测试必须与接线同一轮写，而不是提前写。

### 3.3 脚本类：只有 `check_env.py` 与 `migrate_legacy_results.py` 有自动化测试

| 脚本 | 测试 | 说明 |
|---|---|---|
| `scripts/check_env.py` | ✅ 26 条 | 本轮新增，含只读性与不泄密的硬断言 |
| `scripts/migrate_legacy_results.py` | ✅ 5 条 | 动态加载模块后跑 dry-run / apply / 幂等 / 同库拒绝 / `--limit` |
| `scripts/run_local.ps1` | ❌ | 交互式进程管理脚本（起两个进程、Ctrl+C 收尾），自动化成本远高于收益 |
| `scripts/install_windows.ps1` / `install_linux.sh` | ❌ | 会联网装系统级依赖与 Go 工具；**不应**在测试里跑 |

**未测的风险**：`run_local.ps1` 若写错（例如 worker 与 Web 用了不同 `.env` 导致
落在两个库上），本地表现是「任务一直 queued」，而这**恰好**是 `DEPLOYMENT.md` §8.1
第一个排查项。也就是说：这个脚本的失败模式有文档兜底，但没有测试兜底。
**接受这个缺口**，理由：`ps1` 的行为在 CI 上不可移植验证，而它做的事
（设环境变量 + `Start-Process` + `finally` 收尾）本身极短。

### 3.4 真实外部扫描：从未测，且是**故意的**

* 全部 901 条用例**没有任何一条**访问外部网络目标。
* `mode=real` 的「真实链路」用两种方式验证：
  ① `test_m7_local_e2e.py` 用**真实 httpx 子进程**打只绑 `127.0.0.1` 的本地 fixture；
  ② `test_m4_runner_result.py` 的 `real_mode` 用例在打开双开关后用 monkeypatch 替身。
* **没测什么**：真实工具（subfinder / amass / dnsx …）对真实外部域名的行为、
  真实网络抖动下的超时与重试、真实 CDN/WAF 的响应差异。
* **为什么**：硬约束「不扫描任何未授权的外部目标」。
  这不是「测试没写完」，是**设计选择**——把这条写进报告，是为了让后来者不要
  把「本地全绿」误读成「真实扫描已验证」。

### 3.5 没有覆盖率百分比

* `coverage` / `pytest-cov` **不在依赖清单里**，也未安装；本轮**没有**为了出一张
  覆盖率图而新增依赖（`CONTRIBUTING`/方案都强调不擅自扩大依赖面）。
* 代理指标：**40/41 方法绑定被真实走到**（§3.1）、**81/91 个业务 `.py` 被测试源码提及**（§3.2）。
* **这两项都是粗口径**：路由命中不代表分支覆盖，模块被提及不代表逻辑被断言。
  想要真实覆盖率数字，需先决定是否引入 `coverage` 依赖（属依赖变更，需要确认）。

### 3.6 其他已知未测项

| 未测项 | 现状 | 风险 |
|---|---|---|
| 前端 JS 行为 | 只做 `node --check` 语法检查 + 服务端断言「关键函数/选择器确实出现在产物里」 | 中：无浏览器测试，交互回归只能靠人点 |
| `/health` 的 `worker` 字段在真实长期运行下的漂移 | 用临时心跳文件与 mtime 断言，未做长时间运行测试 | 低 |
| 单并发 worker 的并发度 | `SCAN_LIMITS["max_concurrency"] = 2` **是死配置**（无调用方） | 低（配置项未被使用） |
| 日志文件输出/轮转 | 功能本身未实现（只写 stderr），因此无测试 | — |
| Windows 上 `python` 命令缺失 | 文档说明，无测试（环境问题） | 低 |
| `GET /api/tools` 在某个 adapter 依赖缺失时整体 500 | `modules/registry.py` import 期全量加载，**未测**「单工具坏掉不影响其他」 | 中：已在 `ARCHITECTURE.md` §12 第 9 条登记 |

---

## 4. 测试隔离与可重复性（这套测试凭什么可信）

### 4.1 从不污染仓库

**这一节是写这份报告时唯一发现并修掉的实质缺陷 —— 而且它原先被「900 条全绿」完全掩盖。**

`AGENTS.md` 硬约束：测试不得写仓库的 `results/`、`exports/`。原先的保障只有一个
`tests/conftest.py:app_module` 夹具，它把**两个数据库、上传目录、心跳目录、证据目录、
导出目录**改指 `tmp_path`：`storage.SQLITE_CONFIG["path"]`、`config.LOCAL_DB_CONFIG["path"]`、
`core_uploads.UPLOAD_DIR`、`core_health.OUTPUT_DIR`、`core_artifacts.ARTIFACT_DIR`、
`exporter.EXPORT_DIR`。

问题是：**绕过 `app_module` 的用例根本不受它约束**。逐文件跑测试、比对运行期目录的
文件集合与哈希之后，实测出三处稳定泄漏：

| 泄漏 | 成因 | 单跑一次的后果 |
|---|---|---|
| `exports/` 多一个空 CSV | `test_agent_boundary.py` 的 fixture 只 patch 了 `UPLOAD_DIR`；`_tool_export_results` 走的是 `exporter` 的**模块级** `EXPORT_DIR` | 文件数 +1 |
| `results/local.db` 多 3 行 | `test_security_baseline.py` 的两个上传用例直接调 `core_uploads.save_upload()`，它内部 `db.ensure_schema()` + `db.transaction()` 用的是仓库库路径 | `audit_events` 309 → 312、`uploads` 309 → 312 |
| `results/worker_heartbeat` 被刷新 | `test_m4_runner_result.py` / `test_observability_chain.py` / `test_jobs_executor.py` 起真实 `Worker`；`jobs/worker.py` 从 `config` 导入的 `OUTPUT_DIR` 是自己的副本，patch `core.health` 对它无效 | mtime 被改写 |

**修法**（本轮改的 5 个文件，见 §0）：

* 新增 autouse 夹具 `tests/conftest.py:_isolate_runtime_dirs`，与 `app_module` **并行兜底**：
  `config.LOCAL_DB_CONFIG["path"]` + `core_db.reset_schema_cache()`，
  以及 `exporter.EXPORT_DIR`、`core_uploads.UPLOAD_DIR`、`core_health.OUTPUT_DIR`、
  `jobs.worker.OUTPUT_DIR`、`core_artifacts.ARTIFACT_DIR` 五处模块级属性。
  autouse 是关键 —— 它不再依赖用例「记得」要哪个夹具。
* 子进程没法继承父进程的 monkeypatch，因此给 `config.py` 的 `OUTPUT_DIR` 加了
  `GEF_OUTPUT_DIR` 环境变量出口；`test_jobs_executor.py` 的 kill/restart 用例把它传给子进程。
* 新增回归锁 `test_repo_layout.py::test_autouse_fixture_redirects_every_runtime_path`：
  逐条断言这些路径都不在仓库目录下。夹具被删或漏项时立刻变红，而不是等下次提交
  才发现工作区脏了。

**验证方式**（不是「看起来没问题」）：全量跑一次，对 `results/`、`exports/`、`uploads/`、
`backups/` 四个目录做**逐文件 SHA-256 快照比对** —— 跑前跑后完全一致
（`results` 5 个文件、`exports` 81 个、`uploads` 3 个，`backups/` 不存在）。

另有一个**仍然成立**的脆弱点，写在这里以免下一个人踩：`modules/base.py`、
`modules/httpx.py`、`jobs/worker.py`、`modules/dnsx.py` 的 `OUTPUT_DIR` 都是**导入期绑定**
的模块级字符串，autouse 夹具只覆盖了 `jobs.worker` 这一处；
`test_m7_local_e2e.py` 与 `test_runners_m4*.py` / `test_runner_interface.py` 各自额外 patch
`modules.base` / `modules.httpx` / `modules.dnsx`，因为它们要起**真实子进程**。
**任何新增「真实子进程」用例都要照做**（父进程 patch + 子进程传 `GEF_OUTPUT_DIR`），
否则产物仍会落到仓库 `results/`。

### 4.2 用例之间互相独立

* autouse fixture `_reset_observability_context` 在每条用例前后清空四个 contextvar
  （`request_id`/`job_id`/`step_id`/`worker_id`）。
  这条是**踩坑后加的**：`Worker` 曾只 `startup()` 不 `shutdown()`，
  `worker_id` 泄漏到同线程的下一条用例 —— **单独跑绿、全量跑炸**。
  现在 E2E 里统一用 `with Worker(...)`。
* 测试进程用固定强 `SECRET_KEY`（`conftest.py` 顶部 `os.environ.setdefault`），
  避免每条用例都触发一次性密钥告警；针对弱密钥本身的断言在用例内 monkeypatch 覆盖。

### 4.3 已知的输出噪音（**不是失败**）

* pytest 结尾会有一条 `ResourceWarning`（Werkzeug 在超大 multipart 场景下创建
  未关闭的 `TemporaryFile`，来自测试工具本身，已在
  `test_upload_over_limit_is_rejected` 的 docstring 里记录规避手法）。
* Windows 下子进程读取线程会打印一条 `UnicodeDecodeError: 'gbk' codec ...` 线程异常告警。
* 两者**退出码仍为 0**。想跑严格模式可加 `-W error::ResourceWarning`。

### 4.4 标记与子集

```powershell
python -m pytest -m "not slow"    # 跳过起真实子进程/等租约过期的用例
python -m pytest -m slow          # 只跑这 2 条（test_m7_local_e2e、test_jobs_executor 各 1）
```

---

## 5. 结论

* **方案第 15 节的测试矩阵无缺项**；矩阵之外有 6 类已知缺口，全部在上面写清了「为什么」。
* **最强的部分**：权限边界（Policy/Scope 四入口、双开关、执行期复检）、
  失败分类（七种 runner 结果）、并发正确性（8 线程真机）、
  以及「一个 `job_id` 串起全链」的端到端验收。
* **最弱的部分**：前端无浏览器测试、无覆盖率数字、Agent 的未接线模块无测试、
  `run_local.ps1` 等运维脚本无测试。**四类都不会让当前功能「悄悄坏掉而不报警」**，
  因为前两类有服务端断言兜底，后两类没有运行时行为或失败模式已有文档覆盖。
* **最该记住的一条**：`901 passed` 本身不构成信心 ——
  §2.3 那个 Diff 别名缺陷在 847 条全绿的用例下面藏了很久。
  **测试写的是真实输入还是文档体例，比测试的数量更重要。**

---

## 附：与其它文档的关系

| 想知道 | 看哪 |
|---|---|
| 怎么装、怎么起、出问题先看哪 | [`DEPLOYMENT.md`](DEPLOYMENT.md) |
| 分层与设计取舍（为什么这么测） | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| 逐接口契约（测的就是这些） | [`API.md`](API.md) |
| 逐文件代码地图 + 29 条「症状 → 排查位置」 | [`CODEBASE_MAP.md`](CODEBASE_MAP.md) |
| 哪些安全项已修、哪些仍开着 | [`../SECURITY.md`](../SECURITY.md) |
| 现在的阶段与下一步 | [`../PROJECT_STATE.md`](../PROJECT_STATE.md) |

---

## 6. 公网授权测试模式体验版（本轮增量，1004）

> **这一节与 §0–§5 的关系**：上面是 M7 那一轮的历史快照（当时 901 条），
> 本节只记本轮增量，**不改写上面已经发布过的实测记录**。
> 依据：`docs/milestones/GetEverything_公网授权测试模式体验版方案.md` §9（五类测试）与 §11（验收标准）。

### 6.1 实测结果

```powershell
cd get_everything_framework
python -m pytest                    # 1004 passed, 2 skipped, 0 failures / 0 errors（约 110 秒）
python -m ruff check .              # All checks passed!
python -m mypy app.py core api jobs storage.py modules scripts   # Success: no issues found in 68 source files
node --check web/static/{app.js,assets.js,scan_center.js}        # 三个前端脚本语法通过
```

| 项 | 上一轮 | 本轮 |
|---|---|---|
| 用例总数 | 901 | **1004**（+105） |
| `test_*.py` 文件 | 31 | **34** |
| `tests/unit/` 用例 | 668 | **728**（22 个文件） |
| `tests/integration/` 用例 | 233 | **278**（12 个文件） |
| mypy 源文件 | 63 | **68**（新增 5 个） |
| 声明的方法绑定 | 41 | **48** |
| 被用例真实命中的绑定 | 40 | **47**（口径见 §6.4） |

**+105 的构成（逐文件，可复算）**：

| 文件 | 用例数 |
|---|---|
| `tests/unit/test_tool_registry.py`（新） | 34 |
| `tests/unit/test_projects.py`（新） | 26 |
| `tests/integration/test_public_scan_mode.py`（新） | 45 |
| **合计** | **+105** |

> 采集数 1006、执行数 1004 —— 差额 2 条是 POSIX-only 的 skip（同 §1），不是失败。
> **排除这三个新文件后采集数仍为 901**，与上一轮逐条相等 ——
> 即本轮没有删改任何既有用例。
> （`test_observability.py` 本轮也动过，但只往两个白名单集合里**加了条目**，
> 没有增删测试函数：前后都是 50 条，已实测核对。）

### 6.2 方案第 9 节「五类测试」的逐条落点

| 方案原文 | 落在哪 |
|---|---|
| Scope —— 公网域名创建成功 | `test_public_scan_mode.py::test_public_domain_scope_can_be_created` + `::test_public_ip_target_is_checked_against_allowed_cidrs`（另见 `test_projects.py` 的 Scope-项目关联与 `test_scope.py` 的域名/CIDR 匹配规则） |
| Policy —— 未授权域名拒绝 | `::test_out_of_scope_target_is_403`（`error_code == "scope_violation"`） |
| Job —— 公网任务进入 Job 队列 | `::test_public_job_enters_queue_with_audit_record`（同时断言 `job.created` 审计事件） |
| Tool —— 禁止工具无法提交 | `::test_blocked_tool_cannot_be_submitted`（5 个参数化：`nmap` / `dirsearch` / `naabu` / `feroxbuster` / `katana`）+ `test_tool_registry.py` 的 34 条元数据用例 |
| Worker —— 任务正常执行 | `::test_worker_executes_public_job`（真实 `Worker` + 假 runner，走到 `succeeded 100%`） |

> **Scope 那类为什么是两条**：方案第 4 节把目标类型写成「域名 / IP / CIDR」，
> 而第一版只有域名那条覆盖到了**公网入口链路上**（`allowed_domains` 那条分支）。
> 补的这条走 `allowed_cidrs`（域名留空）：网段内 IP → 202、网段外 IP → 403 且不落库。
> 网段用 **RFC 5737 文档保留段**（`192.0.2.0/24` / `198.51.100.0/24`）——
> 与用 `example.test` 是同一个思路：即便真发出去也不指向任何人的资产。

**方案第 11 节验收标准的逐条落点**：六步链路 `test_public_job_enters_queue_with_audit_record`
→ `test_worker_executes_public_job`；三条「不会」各有一条**源码守卫**
（`test_public_scan_api_never_calls_runners_directly`、`test_public_job_orchestration_is_in_application_service`、
`test_service_delegates_to_single_job_entry`）。

### 6.3 本轮测试里最重要的两条：源码守卫

功能用例保证「现在是对的」，**源码守卫保证「以后不容易变错」**。方案第 2、6 节写着
「禁止 Web → Runner」「不得绕过 Scope / Policy」，本轮把这两句话变成了可执行断言：

* `test_public_scan_api_never_calls_runners_directly`：`api/public_scan.py` 的源码里
  不得出现 `build_runner` / `run_tools` / `RUNNER_REGISTRY`；
* `test_public_job_orchestration_is_in_application_service`：视图层不得内联
  `assert_tools_internet_allowed` / `validate_job_targets` / `create_job_with_status` / `resolve_mode`
  —— 否则就等于「另写了第二条 Policy 判定」；
* `test_projects.py::test_projects_tables_are_additive_and_scopes_untouched`：
  把 `PRAGMA table_info(scopes)` 的列名集合**写死**断言，锁住「只加表、不动 `scopes`」。

### 6.4 本轮重跑的覆盖口径（数字与上一轮同一算法）

用一次性探针重跑 `flask.Flask.full_dispatch_request`（§3.1 的原方法）后：

```text
声明的方法绑定: 48
测试命中的:     47

== 没有被任何用例走到的方法绑定 ==
    GET /api/tool/<tool_name>/results      ← 仍然是同一条，理由见 §3.1
```

业务模块口径：`含 scripts` 共 96 个 `.py`，测试源码提及 **86** 个；
未提及的仍是**同样那 10 个、全部在 `agent/`**（§3.2 的理由不变）。
也就是说本轮新增的 5 个源文件**全部**被测试提及，缺口清单**没有变长**。

### 6.5 本轮新增的缺口（诚实登记）

| 未测项 | 现状 | 风险 |
|---|---|---|
| 扫描中心前端交互 | 仍是 `node --check` + 服务端字符串断言（**方案要求的「前端」到此为止，没有浏览器测试**） | 中：三个步骤表单的联动（项目 → Scope 下拉）只有人手点过 |
| 真实公网目标 | 本项目**从未**对真实外部目标发起扫描（硬约束）；本轮 `real` 模式用例全部把 `modules.registry.build_runner` 换成假 runner，目标是 RFC 6761 保留域 `example.test` | 设计选择，不是缺口 —— 真实扫描何时/对哪个已授权目标发起，由使用者决定 |
| `scripts/verify_public_scan.py` | 有实机验收记录，**没有自动化用例**（它需要真实 Web + worker 两个进程） | 低：它是验收工具，本身不是产品代码 |
| 项目的归档 / 删除、扫描中心分页 | 功能未实现，因此无测试 | 低：属体验版刻意留白 |
| `nuclei` 的真实行为 | 未接入 `RUNNER_REGISTRY`，只登记在 `KNOWN_UNAVAILABLE_TOOLS` 且 `internet_allowed=False` | 低：界面上如实显示为「受限未开放」，不假装可用 |

### 6.6 一句话结论

本轮的测试价值**不在 +105 这个数字**，而在两件事：
① 把方案第 2、6 节的安全原则写成了**源码守卫**（功能对错之外的「结构对错」）；
② 实测暴露出「测试会读开发机 `.env`」这个**与被测代码无关的失败源**并修掉
（`tests/conftest.py` 由 `setdefault` 改为赋值，详见 `CODEBASE_MAP.md` §7 第 37 条）。
第二件事正是本报告 §4.1 精神的延续：**测试的可信度取决于它不受环境影响，而不取决于条数。**

---

## 7. Phase 3：Scan Profile = 工具组合 + 节奏（本轮增量，1091）

> 本轮依据《下一阶段体验优化与公网扫描能力演进方案》第 5、6 节 Phase 3
> 「工具编排：引入 Scan Profile」。§6 的实测记录不改写。

### 7.1 实测结果

```powershell
cd get_everything_framework
python -m pytest                    # 1091 passed, 2 skipped, 0 failures / 0 errors（约 130 秒）
python -m ruff check .              # All checks passed!
python -m mypy app.py core api jobs storage.py modules scripts   # Success: no issues found in 70 source files
node --check web/static/scan_center.js                          # 语法通过
```

| 项 | Phase 2 | 本轮 |
|---|---|---|
| 用例总数（`--collect-only` 汇总） | 1036 | **1091**（+55） |
| `test_*.py` 文件 | 35 | **36**（新增 `tests/unit/test_pace.py`） |
| `tests/unit/` | 745（23 文件） | **782**（24 文件） |
| `tests/integration/` | 291（12 文件） | **309**（12 文件） |
| mypy 源文件 | 69 | **70**（新增 `core/pace.py`） |
| `app.url_map` 规则总数 / 方法绑定 | 47 / 49 | **47 / 49**（+0，纯内部改动） |

**+55 的构成（逐文件，可复算）**：

| 文件 | Phase 2 收集 | 本轮收集 | 增量 |
|---|---|---|---|
| `tests/unit/test_pace.py`（新） | — | 37 | **+37** |
| `tests/integration/test_public_scan_mode.py` | 58 | 76 | **+18** |
| **合计** | | | **+55** |

> 计数口径一律用 `python -m pytest --collect-only -q` 的**逐文件汇总**，
> 不用「`^def test_` 出现次数」——后者会漏掉 `@pytest.mark.parametrize`
> 展开出来的用例（本文件里 58 → 76 的差别正是如此）。
> 校验：`1091 − 37 − 18 = 1036`，与 Phase 2 提交 `510fa41` 的实测基线**逐条相等**，
> 即本轮没有删改任何既有用例。
>
> 三个提交的实测基线（本轮用 `git worktree` 逐条核对，不是推算）：
> Phase 1 `e94b180` = **1009** → Phase 2 `510fa41` = **1036**（+27 = `test_authorization.py` 17
> + `test_public_scan_mode.py` 48→58 的 10）→ Phase 3（本轮）= **1091**（+55）。

### 7.2 本轮最重要的一条：低频必须是**可执行**约束

「引入 Scan Profile」最容易做成一个**纯展示**功能 —— 页面上多一行「低频」，
发出去的请求一个字节都没变。因此本轮的核心用例不是「接口返回了 `pace`」，
而是**Runner 真正拿到的 `config` 变了**：

| 要证明的事 | 用例 | 断言的可观察对象 |
|---|---|---|
| 低频真的降并发与速率 | `test_light_pace_reaches_the_runner_config` | Runner `run()` 里读到的 `config["threads"]` / `config["rate_limit"]` |
| 常规档一个参数都不改 | `test_normal_pace_leaves_the_runner_config_untouched` | 同上，`config` 与构造时**逐键相等** |
| 低频真的在步骤之间等 | `test_light_pace_waits_between_real_steps` | 用 `perf_counter` 量整段执行墙钟 ≥ 间隔 |
| 常规档一次等待都没有 | `test_normal_pace_does_not_wait_between_real_steps` | 即使把低频间隔设成 5 秒，耗时仍 < 2 秒 |
| 命令行确实多出参数 | `test_pace.py::test_subfinder_command_only_gains_rl_when_budget_is_applied` 等 2 条 | `build_command()` 的 `-t` / `-rl` / `-threads` |
| 不污染模块级配置 | `test_pace.py::test_apply_to_runner_copies_config_and_does_not_mutate_the_original` | `runner.config is not SUBFINDER_CONFIG` 且原对象相等 |

### 7.3 「只能收紧」的双向锁定

| 方向 | 用例 |
|---|---|
| 请求**不能**放松模板档位 | `test_public_job_request_cannot_relax_the_template_pace`（请求写 `pace=normal`，落库仍是 `light`） |
| 请求**可以**显式收紧 | `test_public_job_accepts_an_explicit_light_pace`、`test_legacy_job_entry_accepts_an_explicit_light_pace` |
| 非法档位**报错**而非静默回退 | `test_public_job_rejects_an_unknown_pace`、`test_legacy_job_entry_rejects_an_unknown_pace`（两条都断言 **400 + `details.field == "pace"` + 未落库**） |
| 纯函数层的合并规则 | `test_pace.py::test_resolve_lets_the_profile_pace_win` / `..._allows_tightening_a_normal_profile` / `..._rejects_invalid_requested_value` |

### 7.4 节奏的持久化口径：不加列，写事件

`pace` **没有**成为 `jobs` 表的新列（那属于 DB 结构变更，`docs/DECISIONS.md` §1 E 限纯增量），
而是写进 `job.created` 事件的 `detail` + 审计 detail，执行期由 `core/jobs.py:pace_of_job()` 读回。
这样做的**必然性**值得一条测试：worker 是独立进程，且任务可能被 retry 或换一个 worker 重启 ——
节奏必须属于任务本身。对应用例：

* `test_public_job_defaults_to_the_template_pace` —— 创建事件与**审计 detail** 都记着 `pace=light`；
* `test_retry_keeps_the_original_pace` —— retry 之后读回仍是 `light`；
* `test_pace_of_job_falls_back_for_legacy_rows` —— 没有创建事件的老任务读回缺省档，而不是抛异常；
* `test_job_detail_reports_the_pace` —— `GET /api/jobs/<id>` 答得出「这个任务什么节奏」。

### 7.5 本轮新增的缺口与一处刻意的**不作为**

| 未测项 | 现状 | 风险 |
|---|---|---|
| 低频档的**实际外发速率** | 只测到「命令行参数正确」与「步骤之间有等待」，**没有**对真实工具计量请求数 | 低：工具自身对小并发/限速参数负责；本项目不在测试期打真实外部目标 |
| 前端卡片上的节奏标签 | **本轮补了两条源码守卫**（`test_scan_center_frontend_shows_and_forwards_the_pace` / `test_scan_center_js_does_not_hardcode_pace_wording`），另用一次性 DOM 桩人工核对过渲染结果；但**仍没有浏览器测试**（与 §6.5 同一缺口） | 低：显示与转发都有守卫，且节奏的正确性最终由服务端与 Runner 层用例保证 |
| 按工具细分的节奏预算 | `LIGHT_TOOL_BUDGET` 只覆盖公网白名单内的 `subfinder` / `httpx` | 设计如此：白名单外的工具根本进不了公网入口，为其定预算等于为不可达路径写代码 |

**一处刻意的被动**：实现过程中曾尝试新增 `modules/registry.py:build_scoped_runner()`（多一条
能带节奏的构造路径），**已废弃并移除**。原因是 `build_runner(tool_name)` 是测试替换真实 Runner 的
唯一接缝（`monkeypatch.setattr("modules.registry.build_runner", ...)`）：多一条构造入口，
就等于多一个「假 Runner 没被替换、真去执行外部命令」的机会 —— 那正是本项目硬约束
（测试期不许打真实目标）最容易被绕过的地方。最终改成**构造归 registry、降速归 `core.pace.apply_to_runner`**
两步，并由 `test_pace.py::test_apply_to_runner_works_on_the_registry_seam` 把这个分工锁住。

### 7.6 一句话结论

本轮的价值同样不在 +55，而在**把「低频」从文案变成了可观察的执行事实**：
能证明它的不是接口 JSON，而是 Runner 收到的 `config`、多出来的 `-rl`，以及真实测到的等待时间。

---

## 8. Phase 4：结果体验 —— 从 Job 导向结果（1149）

> 本轮依据《下一阶段体验优化与公网扫描能力演进方案》第 6 节 Phase 4
> 「结果体验：从 Job 导向结果；展示：发现资产；服务；技术栈；风险信息」。
> §1～§7 的实测记录不改写。

### 8.1 实测结果

```powershell
cd get_everything_framework
python -m pytest                    # 1149 passed, 2 skipped, 0 failures / 0 errors
python -m ruff check .              # All checks passed!
python -m mypy app.py core api jobs storage.py modules scripts   # Success: no issues found in 71 source files
node --check web/static/app.js                                  # 语法通过
git diff --check                                                # 退出码 0
```

| 项 | Phase 3 | 本轮 |
|---|---|---|
| 用例总数（`--collect-only` 汇总 + `--junitxml` 复核，两者一致） | 1091 | **1149**（+58；junit `tests="1149"`，其中 **1147 passed / 2 skipped**） |
| `test_*.py` 文件 | 36 | **38**（新增 `tests/unit/test_findings.py`、`tests/integration/test_job_results_api.py`） |
| mypy 源文件 | 70 | **71**（新增 `core/findings.py`） |
| `app.url_map` 规则总数 / 方法绑定 | 47 / 49 | **48 / 50**（+1 路由，即 Phase 4 的入口本身） |
| 被用例真实命中的方法绑定 | 47 / 48 | **49 / 50**（探针重跑，见 8.2） |

**+58 的构成（逐文件，可复算）**：

| 文件 | Phase 3 收集 | 本轮收集 | 增量 |
|---|---|---|---|
| `tests/unit/test_findings.py`（新） | — | 35 | **+35** |
| `tests/integration/test_job_results_api.py`（新） | — | 20 | **+20** |
| `tests/integration/test_api_auth_contract.py` | 24 | 26 | **+2** |
| `tests/integration/test_m3_jobs_api.py` | 45 | 46 | **+1** |
| **合计** | | | **+58** |

> 后两处是**补登记既有接口**，不是新增功能用例：`ADMIN_ONLY` 补上了
> `GET /api/jobs/job_x`（详情本身）与 `GET /api/jobs/job_x/results`。
> 详情那条自 Phase 2 起就一直缺失（`/steps`、`/events`、`/artifacts` 都在，唯独详情不在），
> 属顺手补齐的覆盖缺口 —— 该接口本来就是需登录的，不是行为变更。
> 校验：`1091 + 58 = 1149`。
>
> **这四行是逐文件实测出来的，不是推算** —— 用 `git worktree add --detach <tmp> 5960bc0`
> 把 Phase 3 的提交单独检出，两个工作树各跑一遍 `--collect-only -q` 后逐文件求差，
> 差额**恰好只有这四行**。这一步值得写在报告里：本报告 §1 开头那句
> 「901 条用例全绿」曾经掩盖过一个真实缺陷（§2.3 的 Diff 别名 bug），
> 所以「+58 是怎么来的」必须能被复算，而不是相信一个净增量数字。

### 8.2 路由覆盖重跑：结论未变，仍是同一条未走到

用与 §3.1 / §6.4 相同的一次性探针（包装 `flask.Flask.full_dispatch_request` 跑全量）：

```text
声明的方法绑定: 50
测试命中的:     49

== 没有被任何用例走到的方法绑定 ==
    GET /api/tool/<tool_name>/results
```

新路由 `GET /api/jobs/<job_id>/results` **被真实走到**（`test_job_results_api.py` 里
有正常路径的 200 用例）。`docs/TEST_REPORT.md` §3.1、`docs/CODEBASE_MAP.md` §9.21.1、
`PROJECT_STATE.md`、`CHANGELOG.md` 里那句「唯一没被任何用例走到的是
`GET /api/tool/<tool_name>/results`」**在新增一条路由之后依然成立**。

### 8.3 本轮最重要的一条：把「风险信息」如实降级，而不是编造

方案第 5 节提到 `nuclei`，但本次实现**没有**、也不可能给出漏洞结论：

* `nuclei` 在 `core/tool_registry.py:KNOWN_UNAVAILABLE_TOOLS` 里，`internet_allowed=False`，
  **不在** `RUNNER_REGISTRY`；
* 全仓没有任何 CVE / CVSS / severity 数据表、字段或解析器。

因此本轮的「风险提示」= **从已有观测里读出来的、值得人工看一眼的事实**。
要证明的不是「这个功能有返回值」，而是**它不会把「没看」说成「没问题」**：

| 要证明的事 | 用例 | 断言的可观察对象 |
|---|---|---|
| 免责说明每次都在（不是只在零提示时） | `test_every_result_carries_the_no_vulnerability_scanning_disclaimer` | 有 6 条提示的那次响应里仍有「不做漏洞扫描」「不等于」 |
| 零提示也要带说明 | `test_zero_hints_still_ships_the_not_a_clean_bill_notice` | `counts` 全 0 的响应里 `notes` 仍非空 |
| 不存在危险度分级 | `test_no_vulnerability_severity_concept_exists` | `RISK_LEVELS == ("info","notice","attention")`，且不含 low/medium/high/critical |
| 出参里没有漏洞字段 | `test_summarize_never_emits_a_severity_or_cve_field` | 每条 hint 都没有 `severity` / `cve` |
| 两类**覆盖缺口**被说出来 | `test_unprobed_hosts_are_reported_as_a_coverage_gap`、`test_failed_step_is_reported_as_incomplete_coverage` | `unprobed_hosts` / `incomplete_coverage` 两条 hint |
| 任务还在跑时**不**报覆盖不完整 | `test_unfinished_or_successful_steps_do_not_report_incomplete_coverage` | `pending` / `running` 四种状态都不产生该 hint |

### 8.4 「只看本次任务」的口径

这是 Phase 4「从 Job 导向结果」的核心，也是最容易被写成「查全表」的地方：

| 要证明的事 | 用例 |
|---|---|
| 上一次任务的资产不混进这次 | `test_results_only_include_this_jobs_observations` |
| 与 `/diff` 完全同源 | `test_results_tail_does_not_shadow_the_diff_route`（两条尾段各自命中自己的视图） |

`assets` 表没有 `job_id` 列，所以 `list_job_assets()` 从 `observations` 反查 ——
这恰好就是两层模型（`Asset ↓ Observation ↓ Job/Run/Tool`）存在的理由。
**零 schema 变更**：`jobs` / `assets` / `observations` 一字未改。

### 8.5 只读性与鉴权

| 要证明的事 | 用例 |
|---|---|
| 匿名 → 401 | `test_results_endpoint_requires_admin` + `test_api_auth_contract.py:ADMIN_ONLY` 的 16 条参数 |
| 未知任务 → 404 | `test_results_endpoint_unknown_job_is_404` |
| 连调两次不改任何东西 | `test_results_endpoint_is_read_only`（资产 / 观测 / 审计 / 事件四类行数全部不变） |
| 不下发服务器路径 | `test_results_response_exposes_no_server_path` |
| 非法 query 回落而不 400 | `test_results_observations_limit_is_clamped` |

### 8.6 前端：本轮补了**第二次**一次性 DOM 桩人工核对

项目没有浏览器测试（方案第 2.4 节：不引入前端框架 / 构建链），源码守卫只能证明
「字符串在文件里」。因此本轮在 Node 里搭了一个最小 DOM 桩，加载**真实的**
`web/static/app.js`，走**真实的点击路径**（`.job-detail` → 委托监听 → `refreshDetail`
→ `renderDetail` → `fetch /results`），再断言渲染结果。

实测输出（两份夹具，一份有数据、一份 mock 空结果）：

```text
有数据那份：  headings = 发现资产（4） / 服务（2） / 技术栈（3） / 风险提示（6）
              riskBadges = [risk-high 建议人工确认 ×2, risk-medium 可留意 ×3, risk-low 信息 ×1]
              notes = 1 条（免责说明）
              fetchCalls 含 /api/jobs/<id>/results
mock 空那份： headings = 发现资产（0） / 服务（0） / 技术栈（0） / 风险提示（0）
              notes = 2 条（免责说明 + mock 说明）
              emptyHints = 四条「本次任务没有这一类结果。」
```

**桩本身也踩过一次坑并被修正**：第一版桩只给了 `className` 没有 `classList`，
而 `bindJobActions` 的委托监听用 `target.classList.contains(...)` 判定按钮 ——
于是整条详情路径被静默跳过，探针输出「什么都没渲染」。
若不修桩就会把**桩的缺陷**误读成**被测代码坏了**。这一点记在这里，
因为「探针本身也会撒谎」是这类一次性核对最容易忽略的风险。

对应源码级守卫（可随 CI 长期跑）：

* `test_job_detail_frontend_renders_all_four_sections` —— `app.js` 里四段齐全 + `/results` 被请求 + `.job-results` / `.result-note` 在 CSS 里；
* `test_job_detail_frontend_shows_the_notes` —— `notes` 真的被渲染；
* `test_app_js_does_not_hardcode_risk_level_wording` —— 前端使用服务端 `level_label`，且不含写死的中文；
* `test_runtime_copy_contains_no_markdown_markers` —— 运行时文案里不得出现 `**`
  （渲染走 `textContent`，出现星号就会原样显示成半成品排版）。

### 8.7 本轮新增的缺口

| 未测项 | 现状 | 风险 |
|---|---|---|
| `job_events` 的页面渲染 | 结果区只渲染资产 / 观测派生物；事件流仍只能看 `GET /api/jobs/{id}/events` | 低：事件是排查用的，不是给人读的结论；本轮没有把它列为必做 |
| `mock` 模式下三段为空 | 有 `MOCK_NOTICE` 明说这是预期行为 | 低：mock 本来就不产生观测，说清楚比伪造数据好 |
| 四段列表的**截断路径** | 单测用 `monkeypatch` 把上限压到 2 覆盖了 `truncated` 与 `count`；**没有**造一份真实的大数据 | 低：截断逻辑与计数口径都已被断言，缺的只是规模 |
| 前端结果区的**浏览器**渲染 | DOM 桩 + 源码守卫；仍无浏览器测试（与 §6.5 / §7.5 同一缺口） | 低：桩走的是真实代码路径 |
| 真实 httpx 属性进入四段的端到端 | `_observe()` 用的是**真实形状**的 httpx 观测（`status_code` / `title` / `webserver` / `tech`），但不是真跑 httpx 子进程 | 低：形状与 `modules/httpx.py:_read_json_results` 一致，且有 `test_diff_matches_httpx_real_key_names` 同源锁定 |

### 8.8 一句话结论

本轮的价值不在 +58，而在**拒绝了一个看起来更漂亮的做法**：
方案标题里写着「风险信息」，而项目**没有**漏洞扫描能力 ——
于是本阶段交付的是「可观察事实 + 明确的免责说明 + 覆盖缺口提示」，
并有用例禁止任何 `severity` / `cve` 字段出现。
**一个把「没看」和「没问题」分开的页面，比一个看起来很专业的漏洞列表更有用。**

---

## 9. 下一阶段规划方案 Phase 2：Tool Registry（1189）

> 本轮依据仓库根的工作单 `6GetEverything-下一阶段规划方案.md`（本机材料，不入库）
> 第 8、9、13、14 节。§1～§8 的实测记录不改写。

### 9.1 实测结果

```powershell
cd get_everything_framework
python -m pytest                    # 1189 collected / 1187 passed, 2 skipped, 0 failures / 0 errors
python -m ruff check .              # All checks passed!
python -m mypy app.py core api jobs storage.py modules scripts   # Success: no issues found in 71 source files
node --check web/static/scan_center.js                          # 语法通过
git diff --check                                                # 退出码 0
```

| 项 | 规划方案 Phase 1 | 本轮 |
|---|---|---|
| 用例总数（`--collect-only` 汇总 + `--junitxml` 复核，两者一致） | 1159 | **1189**（+30；junit `tests="1189"` failures=0 errors=0 skipped=2） |
| `test_*.py` 文件 | 38 | **39**（新增 `tests/unit/test_tool_parameters.py`） |
| mypy 源文件 | 71 | **71**（本轮未新增/删除源文件） |
| `app.url_map` 规则总数 / 方法绑定 / `/api/*` | 48 / 50 / 42 | **48 / 50 / 42**（**未新增路由**，只给既有两条加字段） |
| 被用例真实命中的方法绑定 | 49 / 50 | **49 / 50**（探针重跑，见 9.2） |

**+30 的构成（逐文件，可复算）**：

| 文件 | Phase 1 收集 | 本轮收集 | 增量 |
|---|---|---|---|
| `tests/unit/test_tool_parameters.py`（新） | — | 17 | **+17** |
| `tests/unit/test_tool_registry.py` | 34 | 41 | **+7** |
| `tests/integration/test_public_scan_mode.py` | 83 | 89 | **+6** |
| **合计** | | | **+30** |

> 校验：`1159 + 30 = 1189`。
>
> **这三行是逐文件实测出来的，不是推算** —— 用
> `git worktree add --detach <tmp> 548d196` 把规划方案 Phase 1 单独检出，
> 两个工作树各跑一遍 `--collect-only -q` 后逐文件求差，差额**恰好只有这三行**。
> 同时确认**没有任何一条既有断言被放松**：`test_split_str_list_rejects_non_list`、
> `test_create_scan_job_requires_tools`、`test_scan_center_js_never_hardcodes_tool_names`
> 等原样保留并通过。

### 9.2 路由覆盖重跑：结论未变

用与 §3.1 / §6.4 / §8.2 相同的一次性探针（包装 `flask.Flask.full_dispatch_request` 跑全量）：

```text
声明的方法绑定: 50
测试命中的:     49

== 没有被任何用例走到的方法绑定 ==
    GET /api/tool/<tool_name>/results
```

本轮**没有新增路由**（`/api/tools` 与 `/api/scan-center` 只是加了字段），
因此「唯一没被任何用例走到的是 `GET /api/tool/<tool_name>/results`」这句
**在第三轮之后依然成立**。

### 9.3 本轮最重要的一条：`load_tools` 的三个真实缺陷

这一轮的测试不是在验证「新功能有返回值」，而是在钉死**三条当时确实存在的错误路径**。
三条都能在改前的代码里指出来，不是假想：

| 缺陷 | 改前位置 | 可观察的后果 | 钉死它的用例 |
|---|---|---|---|
| 空工具**静默回落** | `tool_runner.py:74` `cli_tools or SCAN_CONFIG["enabled_runners"]` | 用户没选任何工具 → 系统拿配置默认值（当时是 `["amass"]`）去扫 | `test_run_rejects_empty_tools_instead_of_falling_back`（四种空形态逐一断言 400）、`test_load_tools_falls_back_to_config_only_when_unspecified` |
| 逗号串被当成**一个**工具 | `api/scan.py:175-176` `isinstance(tools, str) → [tools]` | `"subfinder,httpx"` 变成名叫 `"subfinder,httpx"` 的工具 → 必然「存在不支持的工具」；同一个请求体从 `/api/jobs` 进得来、从 `/api/run` 进不来 | `test_run_accepts_comma_separated_tools`、`test_jobs_and_run_agree_on_tool_parsing` |
| 全链**不去重** | `load_tools` 与 `core.application.split_str_list` 都没有 | `total_steps = len(targets) * len(tools)`，同一工具写两遍 → 步骤数翻倍且重复执行 | `test_run_dedupes_repeated_tools`（断言 `len(outcomes) == 1`）、`test_jobs_and_run_agree_on_tool_parsing`（断言 `total_steps == 2`） |

**`test_run_rejects_empty_tools_instead_of_falling_back` 的写法值得记一笔**：
它一次断言 `{"tools":[]}` / `{"tools":""}` / `{"tools":"  ,  "}` / **不给 tools**
四种形态都得 400，并在注释里写明「只要出现 200，就说明『用户没选工具，系统自己挑了一个』
这条路径又回来了」。空选择与「未指定」的区别是本轮最容易被后续改动抹平的一处
（`payload.get("tools") or payload.get("tool")` 写起来太顺手了），所以用四种形态钉死。

`test_load_tools_fallback_result_is_itself_normalized` 另锁一条边界：
**回落值本身也要过一遍规范化** —— 配置里写重复或带空格时不能漏。

### 9.4 分组不是权限：`test_tool_group_never_changes_permission`

方案第 8 节引入「能力分组」，最危险的方向是让人以为**分组即权限**
（「归到辅助能力那栏的工具是不是就能随便用了？」）。用例直接钉死不变量：

| 要证明的事 | 用例 | 断言的可观察对象 |
|---|---|---|
| 分组**不改变**任何权限字段 | `test_tool_group_never_changes_permission` | 给同一工具换一个 `tool_group`，`risk_level` / `internet_allowed` 逐一不变 |
| 每个工具都必须声明**已登记**的分组 | `test_every_policy_declares_a_registered_group` | 17 个工具的 `tool_group ∈ TOOL_GROUPS` |
| 每个工具都必须有用途说明 | `test_every_policy_declares_a_description` | `description` 非空 |
| 分组覆盖**恰好一次**，不重不漏 | `test_group_tool_policies_covers_every_policy_exactly_once` | 各组工具名展平后与 `list_tool_policies()` 等集 |
| 空栏位保留且顺序固定 | `test_group_tool_policies_keeps_empty_groups_in_plan_order` | 顺序 `recon/service/tech/content/vuln/assist`，`tech` 为空 |
| 未登记分组**直接抛错** | `test_group_tool_policies_rejects_unregistered_group` | `pytest.raises(ValueError, match="未登记")` |
| 子集输入可用 | `test_group_tool_policies_accepts_a_subset` | 传入 2 个策略 → 其余组为空 |

「未登记分组直接抛错」是一条**刻意的严格**：静默丢进兜底栏会让新工具悄悄消失，
而这只会在「加了 `tool_group` 字段却忘了往 `TOOL_GROUPS` 里登记」时发生。

### 9.5 两个接口不可能漂移

`/api/tools` 与 `/api/scan-center` 的注册表字段来自**同一个** `ToolPolicy.to_dict()`。
用例逐字段比对（`test_tools_api_and_scan_center_agree_on_registry_fields`），
并另锁两条历史契约：

* `test_tools_api_keeps_the_historical_name_key` —— `name == tool_name`，
  `category ∈ {subdomain,url,alive,web,port}`，且 **`category != tool_group`**
  （观测类别与能力分组是两件事，字段名不共用）；
* `test_tools_api_groups_cover_only_registered_runners` —— 匿名接口的 `groups`
  只含已接入 runner 的工具，`nuclei` 不出现在这里。

后端侧另有 `test_scan_center_metadata_carries_tool_groups` 断言
分组里的条目与扁平列表**恒等**（`flat[tool_name] == tool`）——
同一个工具不可能「在清单里一个说明、在分组里另一个」。

### 9.6 前端：本轮补了**第三次**一次性 DOM 桩人工核对

项目没有浏览器测试（不引入前端框架 / 构建链），源码守卫只能证明「字符串在文件里」。
因此本轮在 Node 里用最小 DOM 桩加载**真实的** `web/static/scan_center.js`，
喂服务端**真实形状**的 `/api/scan-center`（含 `tool_groups`）+ `/api/scopes`
+ `/api/public-jobs/check` 响应，走真实渲染路径。

实测输出：

```text
分组顺序: recon → service → tech → content → vuln → assist   （全部来自服务端）
  资产发现 1 个 | 被动收集子域与 URL，不发主动探测。 | 条目 1
  服务识别 2 个 | 探测存活、端口与响应特征。 | 条目 2
  技术识别 本阶段暂无可用工具 | 识别框架与组件指纹。 | 条目 0
  内容发现 本阶段暂无可用工具 | 目录与路径探测。 | 条目 0
  漏洞检测 本阶段暂无可用工具（仅列出未开放项） | 漏洞验证能力。 | 条目 1   ← nuclei 归位
  辅助能力 本阶段暂无可用工具 | 解析、字典与流程编排。 | 条目 0

工具清单（模板 = 资产发现）：subfinder ✓ subfinder·httpx（4/4 全部来自服务端）
切到自定义模式：subfinder / httpx 变可勾选，nmap / nuclei 仍禁用
试算返回后：授权状态「已授权，可以真实扫描」，授权资产「培正学院公网资产 · …」
未勾选提交：job-feedback 拦下，**不发任何请求**；勾选后发出 POST /api/public-jobs
源码守卫：工具名零字面量、分组名零字面量、摘要读 BLOCKER_LABELS（不自行判定）
```

**探针本身又踩过一次坑**（记在这里，与前两轮同因）：第一版桩用的断言字符串是
`renderToolList(center.tools, center.restricted_tools, activeStrategyOf(`，
而本轮为了让分组参数进来，调用被拆成了多行 —— 于是**源码级断言报 MISS**，
但页面渲染其实完全正常。这与 §8.6 的 `classList` 缺、§7.5 的 `input.name` 不反射
是同一类风险：**探针自己会撒谎**。判别方法是把断言放宽到「数据来自哪个对象」
（`center.tools,` 与 `metadata.tool_groups`）而不是「调用怎么写在一行里」。

### 9.7 本轮新增的缺口

| 未测项 | 现状 | 风险 |
|---|---|---|
| 前端分组渲染的**浏览器**验证 | DOM 桩 + 源码守卫；仍无浏览器测试（与 §6.5 / §7.5 / §8.6 同一缺口） | 低：桩走的是真实代码路径，且断言了分组顺序与空栏位文案 |
| `/api/tools` 单工具坏掉整体 500 | `api/tools.py` 仍为每个工具 `build_runner()` 实例化（BUG 索引第 21 条），本轮**未改** | 中：与「单个 adapter 依赖缺失」是同一根因，属另一件事，已在 `docs/ARCHITECTURE.md` §12 第 9 条登记 |
| `normalize_tool_names` 对**极端输入**的行为 | 覆盖了 `None` / 字符串 / 数组 / 元组 / 数字 / 字典 / `object()`；未测超大列表与超长单项 | 低：纯内存变换，无 IO、无上限语义（工具数受 registry 收敛到 17） |
| 扫描模式（方案第 10 节） | **未实现**（信息收集 / 基础检测 / 深度测试） | 低：留 Phase 3，且需先与公网白名单口径对齐 |
| Agent 仍可直接调 `tool_runner.run_tools` | 本轮未动（方案第 12 节） | 中：参数标准化**没有**放宽它的能力（工具名仍受 registry 校验），但「Agent 不拥有最终执行权」这条目前只对 Web 入口成立 —— 见 P0-6 阶段二 |

### 9.8 一句话结论

本轮的价值不在 +30，而在**把「用户没选工具」与「系统替用户挑工具」彻底分开**：
前者现在是 400，后者只剩 CLI 一条可达路径。
配套的注册模型让「工具说明与分组」有了唯一供源 —— 前端一个工具名、一个栏位名都不写死，
而后端**一条闸门都没放松**（公网白名单仍是 `subfinder` + `httpx`）。

---

## 10. 下一阶段规划方案 Phase 3：公网授权测试完善（1290）

> 本轮依据同一份工作单第 14 节 Phase 3（`6GetEverything-下一阶段规划方案.md:428-436`）：
> **操作者记录 / 授权备注 / 扫描策略 / 限速配置 / 超时配置**。
> §1～§9 的实测记录不改写。

### 10.1 实测结果

```powershell
cd get_everything_framework
$env:PYTHONIOENCODING="utf-8"; python -m pytest -o addopts="" -q
                                    # 1290 collected / 1288 passed, 2 skipped, 0 failures / 0 errors
ruff check .                        # All checks passed!
mypy app.py core api jobs storage.py modules scripts   # Success: no issues found in 72 source files
node --check web/static/scan_center.js ; node --check web/static/app.js   # 两个都通过
```

| 项 | Phase 2（`ce0ef22`） | 本轮 |
|---|---|---|
| 用例总数（`--collect-only -q` 汇总） | 1189 | **1290**（+101） |
| `test_*.py` 文件 | 39 | **40**（新增 `tests/unit/test_job_limits.py`） |
| mypy 源文件 | 71 | **72**（新增 `core/job_limits.py`） |
| `app.url_map` 规则 / 方法绑定 / `/api/*` | 48 / 50 / 42 | **48 / 50 / 42**（**未新增路由**，只给既有接口加字段） |
| 被用例真实命中的方法绑定 | 49 / 50 | **49 / 50**（探针重跑，见 10.2） |

**+101 的构成（逐文件，可复算）**：

| 文件 | Phase 2 收集 | 本轮收集 | 增量 |
|---|---|---|---|
| `tests/unit/test_job_limits.py`（新） | — | 59 | **+59** |
| `tests/unit/test_jobs_store.py` | 72 | 90 | **+18** |
| `tests/integration/test_public_scan_mode.py` | 89 | 113 | **+24** |
| **合计** | | | **+101** |

> 校验：`1189 + 101 = 1290`。这三行同样是**逐文件实测**出来的，不是推算 ——
> `git worktree add --detach <tmp> ce0ef22` 检出规划方案 Phase 2，
> 两个工作树各跑一遍 `--collect-only -q` 后逐文件求差，差额**恰好只有这三行**。
> 同时确认**没有任何一条既有断言被放松**：`test_normal_pace_leaves_the_runner_config_untouched`、
> `test_public_job_request_cannot_relax_the_template_pace`、`test_legacy_job_api_still_works`、
> `test_service_delegates_to_single_job_entry` 等原样保留并通过。

### 10.2 路由覆盖重跑：结论未变（连续第四轮）

用与 §3.1 / §6.4 / §8.2 / §9.2 相同的一次性探针（包装 `flask.Flask.full_dispatch_request` 跑全量）：

```text
declared: 50
hit:      49
== never hit ==
    GET /api/tool/<tool_name>/results
```

本轮**没有新增路由**，因此「唯一没被任何用例走到的是 `GET /api/tool/<tool_name>/results`」
这句在**第四轮之后依然成立**。

### 10.3 本轮补了**第四次**一次性 DOM 桩人工核对（这次真跑通了）

项目没有浏览器测试，源码守卫只能证明「字符串在文件里」。本轮在 Node 里用最小
DOM 桩加载**真实的** `web/static/scan_center.js` 与 `app.js`，喂服务端**真实形状**的
`/api/scan-center`（含本轮新增的 `limits`），走真实渲染路径。实测输出：

```text
① 由服务端 limits 生成的输入框: 2 个
   field=rate_limit        min=1 max=100 name=rate_limit
   field=timeout_seconds   min=1 max=120 name=timeout_seconds
   （前端一个字段名、一个上下界都没写死 —— 全部来自 describe_limits()）

② 留空提交的请求体键:
   ["authorization_confirmed","pace","project_id","scope_id","strategy","targets"]
   含 rate_limit? false / 含 timeout_seconds? false / 含 operator? false
   ← 留空 = **不加键**，而不是传 0 或 null

③ 填好后的请求体: {"operator":"keqi","rate_limit":"3","timeout_seconds":"20"}
   ← 键名取自 input[data-limit-field]（= 服务端 spec.field），前端不参与改名

④ app.js renderDetail 真实渲染出的元数据行（Phase 3 新增的五行加粗）：
   状态 / 模式 / 进度 / 尝试次数 / 错误码 / 错误说明 / 创建 / 开始 / 结束 / worker
   **操作者 keqi**
   **扫描策略 asset_discovery**
   **授权确认 已确认（使用者确认，不是安全边界）**
   **本次收紧 每秒请求上限 3 · 单步超时 20 秒**
   目标: www.peizheng.edu.cn · 工具: subfinder, httpx
   **授权依据: 校内书面授权（2026）**
   尚无步骤记录。
```

**探针自己又踩了三个坑，全部记下来（与前几轮同因：探针会撒谎）**：

| 现象 | 真正原因 | 教训 |
|---|---|---|
| 第一次跑：`form.onsubmit` 是 `undefined`，一次请求都没发出 | 真实代码用的是 `addEventListener("submit", …)`，桩里没实现 `addEventListener` | 桩缺一个方法，症状看起来像「被测代码不工作」 |
| 第二次跑：`window.location` undefined，脚本直接抛 | `scan_center.js` 创建成功后跳 `/scan-center?job_id=…` | 桩要把**全部**被用到的宿主对象补齐，缺一个就是假失败 |
| 第三次跑：输入框 0 个、`strategy-list` 空 | 元素 id 表漏了 `strategy-list`，`DOMContentLoaded` 里 `if (!$("strategy-list")) return;` 提前退出 | **漏一个 id，整页初始化静默不发生**；而这次输出「0 个输入框」看起来像**真缺陷** |

第三个坑最值得记：如果只跑一次就下结论，会写成「前端没生成输入框」——
而真实缺陷一个都没有。判别方法是**把初始化链的中间产物也打出来**
（`strategy-list` 子节点数、`tool-list` 子节点数、`check-summary` 文案），
看到 `3 / 3` 才能确认元数据链真的跑完了。

`app.js` 的 `renderDetail` 是 IIFE 内部函数、只对外暴露三张文案表，因此探针在
**内存里**给源码插一句把它挂出来（不改仓库文件），并在找不到挂载点时以退出码 2 明确失败 ——
避免「探针静默失效、结果看起来像通过」。

### 10.4 本轮最重要的一条：**零 DDL** 与它的反向守卫

五项全部写进 `job.created` 事件的 detail，**没有**给 `jobs` 表加一列：

```sql
-- 有测试直接读它，断言这六个列名不存在
PRAGMA table_info(jobs)
```

`test_phase3_context_does_not_add_columns_to_jobs` 是**反向**守卫：它证明的不是
「功能能用」，而是「结构没被动过」。这类守卫比正向断言更重要 ——
功能测试红了会有人看，结构偷偷变了没人看得出来，而它正是
[`DECISIONS.md`](DECISIONS.md) §1 E「只允许纯增量」的边界。

### 10.5 限速 / 超时：两条不变量各自有反向用例

1. **只能收紧**：`apply_to_runner` 用 `min` 合并。用例把低频档已写下的 `httpx -rl 10`
   摆好，再传 `rate_limit=50`，断言**最终仍是 10**；反向（传 3、已有 10）则断言变成 3。
2. **缺省路径一根手指都不碰**：`test_normal_pace_leaves_the_runner_config_untouched`
   仍断言 `seen == [{"threads": 50, "timeout": 10}]` 且 `"rate_limit" not in seen[0]` ——
   保证靠的是 `JobLimits.is_empty` 在碰 `config` **之前**就返回 `False`。

另有一条**实测出来的**缺陷进了回归：`_as_int()` 原先的报错文案没有字段名，
`core/application.py` 只能猜，于是 `timeout_seconds="abc"` 被报成
`details.field = "rate_limit"`。现在字段名写进文案，并有 6 条参数化用例锁住
（`test_format_errors_name_the_offending_field`）。这就是「错误消息要能被机器读」的实例。

### 10.6 `authorization_confirmed` 为什么不是闸门（本轮的中心判断）

它是页面上那个复选框，服务端**如实记录、不参与判定**：`false` / `true` / 不给（→ `null`）
三种取值**都能创建任务**（`test_authorization_confirmation_is_recorded_but_is_not_a_gate`）。

一个可被脚本置真的 JSON 布尔值不构成安全边界。把它当闸门只会制造
「勾了就等于放行」的错觉 —— 授权仍由 Scope 命中 / `Scope.active_scan` /
`GEF_ALLOW_REAL_SCAN` 三道闸门判定。这属于**需要用户确认的语义选择**，
已按无人值守规则登记在 `docs/DECISIONS.md` §3.8 第 2 条。

### 10.7 本轮新增的缺口

| 未测项 | 现状 | 风险 |
|---|---|---|
| 限速的**实际外发速率** | 只测到「命令行参数正确」（`rate_limit` / `process_timeout` 落进 config）与「合并方向正确」（`min`）；真实工具对参数的解释由工具自身负责 | 低（与 §7.5 / §9.7 同一条边界，非本轮引入） |
| `RATE_LIMIT_MAX = 100` 的取值 | 是判断而非推导（依据：上界须低于工具默认速率 150，而低频档实际只用 3 / 10） | 低：值本身有参数化边界用例；**判断依据需用户确认**（§3.8 第 1 条） |
| `operator` 的**身份真实性** | 只能记自称；本仓库认证是布尔态本地 Token，`audit.actor` 仍硬编码 `local-admin` | 中：属「多租户 / SSO」范畴（方案第 15 节暂缓），本轮**没有**偷偷做一半（§3.8 第 3 条） |
| `timeout_seconds` 上界随环境变量变 | 上界每次读 `SCAN_LIMITS["process_timeout"]`（本机 120） | 低：刻意的（否则 `GEF_PROCESS_TIMEOUT` 失效）；意味着换机器上界会变 |
| 浏览器端渲染 | 第四次 DOM 桩核对；仍无浏览器测试 | 低：本次桩跑通了完整渲染路径，逐行核对了元数据输出 |
| 扫描模式（方案第 10 节） | **仍未实现**（信息收集 / 基础检测 / 深度测试） | 低：限速/超时是**数值维度**而非第三档模式，需先与白名单口径对齐 |

### 10.8 一句话结论

本轮把「这次任务是谁、依据什么授权、用哪个模板、要了多快」四件事**全部落到任务本身**，
并且**一条闸门都没放松、一个列都没加**：
记录类字段只记录，限速类字段只收紧，授权确认**刻意**不成为安全边界。
新增的 101 条用例里有 6 条是**反向**守卫（结构没变、缺省路径没碰 config、
不用授权确认也能建任务、越界不被静默夹边界）。

---

## 11. 下一阶段规划方案第 6 节：目标自动匹配授权资产（1293）

> 本轮依据同一份工作单第 6 节（「**系统后台：** 调用 `resolve_scope(target)`，自动判断」，
> `6GetEverything-下一阶段规划方案.md:241`）与第 16 节①
> （「验证完整链路：**输入目标 → 自动匹配 scope → 选择工具 → 创建 job**」，`:466-468`）。
> §1～§10 的实测记录不改写。

### 11.1 实测结果

```powershell
cd get_everything_framework
$env:PYTHONIOENCODING="utf-8"; python -m pytest -o addopts="" -q
                                    # 1293 collected / 1291 passed, 2 skipped, 0 failures / 0 errors
ruff check .                        # All checks passed!
mypy app.py core api jobs storage.py modules scripts   # Success: no issues found in 72 source files
node --check web/static/scan_center.js                # 通过（本轮改的就是它）
# 路由覆盖探针（算法同 §3.1 / §6.4 / §8.2 / §9.2）：
#   declared: 50 / hit: 49 / never hit: GET /api/tool/<tool_name>/results
```

| 项 | Phase 3（`8e94662`） | 本轮 |
|---|---|---|
| 用例总数（`--collect-only -q` 汇总） | 1290 | **1293**（+3） |
| `test_*.py` 文件 | 40 | 40（未新增文件） |
| mypy 源文件 | 72 | **72**（未新增源文件） |
| `app.url_map` 规则 / 方法绑定 / `/api/*` | 48 / 50 / 42 | **48 / 50 / 42**（**未新增路由**） |
| 被用例真实命中的方法绑定 | 49 / 50 | **49 / 50**（探针重跑，第五轮结论不变） |

**+3 的构成（单文件）**：`tests/integration/test_public_scan_mode.py` 113 → **116**。

### 11.2 为什么这一格必须补，而且必须由**服务端结论**驱动

四步流程（Phase 1）、只读试算（Phase 2）、工具选择中心（Phase 2）都已落地，
但「试算出结论之后**谁**把那份结论变成下拉框里的选中项」一直没做 ——
用户仍要自己在步骤 2 再挑一次。方案第 6 节要的正是这一步。

驱动它的数据必须是服务端的 `eligible_scope_ids`（`core/authorization.py:TargetCheck.eligible`
的 `verdict == allowed` 集合），理由与「Play Profile 文案只能有一份」同源：
**前端自己比对 `verdict` / `allowed_domains` / `active_scan` 就是第二条授权判定。**
本轮实测发现这种「第二条判定」在 `scan_center.js` 里已经有三处：

| 位置 | 改前 | 改后 |
|---|---|---|
| `renderCheckResults()` | `item.verdict === "allowed"` | `isEligibleMatch(check, item)` |
| `renderConsentSummary()` | `item.status === "ready" \|\| item.status === "scope_inactive"` | 同上 |
| `refreshConsentScopeLine()` | 同上 | 同上 |

三处判同一件事、判法还不一样 —— 这正是「改一处漏一处」的形态。现在判法只有一份。
源码守卫 `test_scan_center_js_auto_selects_the_scope_from_server_verdict`
把这些写法列进禁止清单（`item.verdict ===`、`item.status === "ready"`、
`scope.allowed_domains.indexOf`、`scope.active_scan &&`），**注释里出现不算**。

### 11.3 §16 ① 的整条链路是**端到端**测的，不是靠源码守卫

`test_target_to_job_flow_uses_the_auto_matched_scope` 把链路跑完并断言终局：

```text
建两份授权资产（各挂一个项目，其中一份覆盖目标、另一份不覆盖）
  → POST /api/public-jobs/check          断言 eligible_scope_ids == [覆盖的那一份]
  → 用 eligible_scope_ids[0] 当 scope_id  POST /api/public-jobs（strategy=asset_discovery）
  → 断言 202 + status=queued + mode=real + strategy 正确 + scope_id == 被选中的那一份
  → 断言 jobs 表里 scope_id / tools(subfinder+httpx) / targets 正确，且只落一条任务
```

把「自动匹配的输出」直接当「创建任务的输入」，是为了让前端改口径时**这条会红** ——
而不是像纯源码守卫那样「字符串还在就算过」。

### 11.4 一次性 DOM 桩（不入库）：四条口径逐条核对

项目没有浏览器测试（不引入前端框架 / 构建链）。本轮除源码守卫外，另用最小
`document` / `fetch` 桩加载**真实的** `scan_center.js`，喂服务端真实形状的响应，
走真实渲染路径核对四件事（与 §6.5 / §7.5 / §8.6 / §9.7 / §10.7 同一口径）：

| 场景 | 期望 | 实测 |
|---|---|---|
| 唯一命中 `scope_x` | 自动选中并切项目 | `job-project=proj_a` / `job-scope=scope_x`，摘要带「已自动选中…可在步骤 2 改选」 |
| 两目标命中不同资产 | 不猜，保持原选择 | `job-scope` 保持 `scope_x`，摘要带「多个目标命中的授权资产不一致…」 |
| 目标命中排除列表 | 不选中（`eligible_scope_ids == []`） | 保持原选择，摘要「0 / 1 可以真实扫描」，状态「这个目标不在任何已授权范围内」 |
| 自动匹配是否改目标 | 一字不改 | 目标输入框仍是 `secret.blocked.test` |

### 11.5 本轮新增的缺口

| 未测项 | 现状 | 风险 |
|---|---|---|
| **方案第 16 节③「Agent 只能 `create_scan_job()`」当前不成立** | `agent/action.py` 仍直接调 `tool_runner.run_tools`（`:419`/`:437`）与 `HttpxRunner().run_scan`（`:503`/`:507`/`:511`），**没有**走 Job Service；本轮**未动** `agent/` | 中：与你上一轮对 P0-6 阶段二「先不开工」的答复一致。**没有**写 `xfail`、也**没有**写「断言 Agent 确实绕过」的测试去把缺口粉饰成预期 —— 缺口如实登记在 `docs/DECISIONS.md` §3.9 第 1 条 |
| 自动匹配的**浏览器**验证 | 第五次 DOM 桩核对；仍无浏览器测试（与 §6.5 / §7.5 / §8.6 / §9.7 / §10.7 同一缺口） | 低：桩跑的是真实代码路径，且四件事逐条核对过 |
| 「唯一才选」这条口径本身 | 是判断而非推导 | 低：另一种做法（选第一个）会在提交时撞服务端的项目→范围校验，得到一个页面上看不出原因的 403；已登记 §3.9 第 2 条等确认 |

### 11.6 一句话结论

本轮的价值不在 +3，而在**把「隐藏 Scope」这件事真正做实，同时一步都没删 Scope**：
目标集合、Scope 模型、Policy 一字未改，被选中的是用户自己建好、且**服务端**已判定
覆盖目标的那一份资产；判定仍然只在 `core/policy.py:validate_job_targets()` 里做一次。
顺带把散落三处的「第二条授权判定」收敛成一份。
唯一没做到的（Agent 边界，方案第 12/16 节③）**明写在这里**，不粉饰。

---

## 12. 方案第 13 节「后端安全边界」的测试缺口回填（1296）

> 本轮依据同一份工作单第 13 节四行表（`6GetEverything-下一阶段规划方案.md:390-399`）
> 与第 16 节②（`:470-475`）。§1～§11 的实测记录不改写。
>
> 同轮另有一次对工作单 Phase 1～3 的**独立只读审计**，报出两处真实口径问题
> （`category` 同名异义 / 两个读出点的 `groups` 视图无守卫），一并收口在 §12.6。
> 审计的另外四条判定为设计取舍或文档已说明，不改行为。

### 12.1 实测结果

```powershell
cd get_everything_framework
$env:PYTHONIOENCODING="utf-8"; python -m pytest -o addopts="" -q
                                    # 1296 collected / 1294 passed, 2 skipped, 0 failures / 0 errors
ruff check .                        # All checks passed!
mypy app.py core api jobs storage.py modules scripts   # Success: no issues found in 72 source files
node --check web/static/scan_center.js                # 通过（本轮未改前端）
node --check web/static/app.js                        # 通过
# 路由覆盖探针（算法同 §3.1 / §6.4 / §8.2 / §9.2 / §11.1）：
#   declared: 50 / hit: 49 / never hit: GET /api/tool/<tool_name>/results
```

| 项 | §6 自动匹配（`9224bc3`） | 本轮 |
|---|---|---|
| 用例总数（`--collect-only -q` 汇总） | 1293 | **1296**（+3） |
| `test_*.py` 文件 | 40 | 40（未新增文件） |
| mypy 源文件 | 72 | **72**（未新增源文件） |
| `app.url_map` 规则 / 方法绑定 / `/api/*` | 48 / 50 / 42 | **48 / 50 / 42**（**未新增路由**） |
| 被用例真实命中的方法绑定 | 49 / 50 | **49 / 50**（探针重跑，第六轮结论不变） |

**+3 的构成（单文件）**：`tests/integration/test_public_scan_mode.py` 116 → **119**。

### 12.2 为什么是「补缺口」而不是「改实现」

§11 收尾时第 13 节的四行边界里，有两行**实现是真的、但没有入口级用例**。
「没测试」不等于「没实现」，所以本轮先实测确认，再补上能证明它真的的用例：

| 第 13 节边界 | 实现位置 | 补测前 | 补测后 |
|---|---|---|---|
| Scope 校验 `target ∈ scope` | `core/policy.py:validate_job_targets()` | ✅ `test_out_of_scope_target_is_403` | 不变 |
| Real Mode 控制 | `core/safety.py:REAL_SCAN_ENV` | ✅ `test_real_mode_without_env_switch_is_403_and_does_not_fall_back_to_mock` | 不变 |
| Job 审计六项 | `core/application.py:365-389` | ⚠️ 只钉住 `operator` / `targets` / `scope_id` / `created_at` | ✅ 新增用例 |
| 工具白名单（任意字符串） | `core/tool_registry.py:assert_tools_internet_allowed()` 的 `unknown` 分支 | ⚠️ 只覆盖**已登记但被禁**的 `nmap` 等 | ✅ 新增用例 |

### 12.3 六项审计字段逐项可查，且两个来源必须一致

`test_job_audit_records_the_six_required_fields` 按第 13 节的**原话**逐项查：

```text
job_id  = audit_events.target_id
time    = audit_events.created_at
operator / target / tools / mode  = audit_events.detail 的 operator / targets / tools / mode
  → 再断言 job.created 事件与审计记录对 tools / mode 的说法一致（两处同源，不能漂移）
```

刻意**不**写成「detail 里有哪些键」的白名单断言：那样每加一个 Phase 3 字段都要改测试，
反而会诱导后人把这条边界顺手删掉。只查第 13 节点名的那六项。

**这条用例不是「跑一遍看起来对」就算数** —— 本轮做了一次**变异验证**
（先破坏实现，确认用例真的会红，再把实现原样改回）：

```text
变异：把 core/application.py:365-389 的 detail 里 "tools": selected_tools 一行删掉
结果：test_job_audit_records_the_six_required_fields  FAILED（1 failed in 0.88s）
还原：把该行加回 → 同一用例 PASSED（1 passed in 0.56s）
复核：git status --short get_everything_framework/core/application.py 无输出（实现零改动）
```

也就是说这条断言真的**钉住了** `tools` 落审计，而不是「恰好通过」。

`tools` 与 `mode` 在 `jobs` 表里也有一份，很容易被当成「审计表里重复了」删掉；
一旦删掉，事后就再也分不清「这次开的是哪些工具、是真扫还是 mock 演练」——
这是本条用例的真实价值，不是凑数。

### 12.4 「禁止任意字符串调用工具」的唯一直接证法

`test_unregistered_tool_name_is_rejected_by_the_registry` 用 `strategy="custom"` +
`tools=["definitely-not-a-tool"]` 打公网入口，断言：

```text
400 + error_code=bad_request + details.field=tools + details.unknown_tools=["definitely-not-a-tool"]
  → 且 jobs_store.list_jobs() == []（闸门在创建任务**之前**）
```

它和 `test_blocked_tool_cannot_be_submitted`（`nmap` / `dirsearch` / `naabu` / `feroxbuster` / `katana`）
是两件事：那条验「已登记但被禁」，这条验「**从未登记**」。后者才是「任意字符串」的字面场景 ——
`assert_tools_internet_allowed` 的单元测试证明了闸门函数本身，但**只有入口级用例**能证明
公网入口真的走到了那个闸门（而不是在别处被 `404` / `KeyError` 之类的偶然路径挡住）。

### 12.5 本轮**没有**新增的缺口

§11.5 的三条缺口（Agent 边界不成立 / 无浏览器测试 / 「唯一才选」是判断而非推导）**一条都没变**，
本轮未触碰与之相关的任何文件。前两条的处置口径与 §11.5 完全相同：
**不写 `xfail`、不写「断言 Agent 确实绕过」的测试**去把缺口粉饰成预期。

### 12.6 独立审计的两处发现：一处**会静默给错值**，一处**此前没有守卫**

对工作单 Phase 1～3 的逐条只读审计报了六条，其中两条是真实缺陷（另外四条是
设计取舍或文档已说明，见 §12.7）：

**① `category` 同名异义 —— 键存在、不报错、值是错的。**
方案第 9 节的示例是 `{"name":"httpx","category":"service"}`，`category` 指**能力分组**；
本仓的分组字段叫 `tool_group`，而 `/api/tools` 响应里**确实有一个 `category`**，
装的是运行器自报的**观测类别**（`subdomain` / `url` / `web` …）。
照方案字面读 `entry["category"]` 会拿到 `"subdomain"` 而不是 `"service"`。
既有的 `assert entry["category"] != entry["tool_group"]` 只锁住「两者不同」，
**锁不住「谁对应方案的 `category`」**。

收口方式**不改行为**（改键名会破坏历史契约）：在 `api/tools.py` 模块 docstring 与
`docs/API.md` 写出逐字段对照 —— `方案 name → tool_name`、`方案 category → tool_group`、
`方案 risk → risk_level` + `risk_label` —— 让按方案实现的人第一步就看到映射。

**② 两个读出点的 `groups` 视图此前没有守卫。**
`/api/tools` 与 `/api/scan-center` 各写一次 `group_tool_policies(list_tool_policies())`，
是**两个独立调用点**；已有的比对用例只比**扁平清单**的 8 个字段。一旦有人把其中一处
改成默认值 `list_all_tool_policies()`，`vuln` 栏会一个接口空、另一个接口冒出 `nuclei`，
而扁平清单比对**不会红**（`nuclei` 本来就不在扁平清单里）。

收口方式：新增 `test_both_registry_readouts_agree_on_the_groups_view` ——
逐分组 `==` 比对，并断言两边的 `vuln` 栏都为空、`nuclei` 只从 `restricted_tools` 走。

### 12.7 审计里判定为「不是问题」的四条

| 审计意见 | 判定 | 依据 |
|---|---|---|
| 方案第 8 节五个分组 vs 本仓 6 个 | 设计取舍，不改 | 多一栏「内容发现」；技术识别 / 漏洞检测**故意留空**并如实显示「本阶段暂无可用工具」，理由写在 `core/tool_registry.py:112-119` |
| 前端读 `/api/scan-center` 而非方案第 9 节写的 `/api/tools` | 有意为之，已写明 | 两者**不等价**：`/api/tools` 匿名、不含 `restricted_tools` / `projects` / `strategies` / `paces` / `limits`；切过去会丢受限工具说明与整个流程的元数据（见 §12.6 ① 与 `docs/API.md`） |
| `risk` 拆成 `risk_level` + `risk_label` | 语义没丢 | 机器值 + 中文展示值，`test_policy_to_dict_shape` 锁 9 键形状 |
| `name` 只在 `/api/tools` 有 | 文档已补 | 历史键名别名；`/api/scan-center` 只有 `tool_name` —— 已写进 §12.6 ① 的对照表 |

### 12.8 一句话结论

本轮 +3 的价值不在数量，而在两类**很难靠「跑一遍看起来对」发现的问题**：
一是第 13 节的四行边界从「三行有用例」补成**四行都有入口级用例**，
且补的是**最容易在重构中被静默删掉**的那两行（审计六项里的 `tools` / `mode`、
以及「未登记工具名」这条任意字符串路径）；
二是审计查出的「同名异义会静默给错值」与「两个读出点的分组视图无守卫」——
前者补文档映射、后者补一条逐分组 `==` 的守卫。**实现代码一行未改**：
三条新用例在写下的当次就通过，两处收口只动 docstring 与文档。

---

## 13. 执行期双开关复检 + Phase 1 四处审计缺口收口（1305）

> 本轮是**第一次改动执行期闸门**（`jobs/executor.py`）与**第一次同时跨三层**
> （`core/` + `jobs/` + `web/`）。依据同一份工作单第 13 节「Real Mode 控制」
> （`6GetEverything-下一阶段规划方案.md:397`）、第 6 节目标输入（`:228-251`）、
> 第 4 节原则 2（`:158-191`）。设计判断记在 `docs/DECISIONS.md` §3.11、
> 代码地图记在 `docs/CODEBASE_MAP.md` §9.29。

### 13.1 实测结果

```powershell
cd get_everything_framework
$env:PYTHONIOENCODING="utf-8"; python -m pytest -o addopts="" -q
                                    # 1307 collected / 1305 passed, 2 skipped, 0 failures / 0 errors
ruff check .                        # All checks passed!
mypy app.py core api jobs storage.py modules scripts   # Success: no issues found in 72 source files
node --check web/static/scan_center.js                # 通过（本轮改动）
node --check web/static/assets.js                     # 通过（本轮改动）
node --check web/static/app.js                        # 通过（本轮未改）
# 路由覆盖探针（算法同 §3.1 / §6.4 / §8.2 / §9.2 / §11.1 / §12.1）：
#   declared: 50 / hit: 49 / never hit: GET /api/tool/<tool_name>/results
```

| 项 | §12 第 13 节缺口回填（`1746f41`） | 本轮 |
|---|---|---|
| 用例总数（`--collect-only -q` 汇总） | 1296 | **1307**（+11） |
| 其中「修复既有用例」 | — | 1 条（`test_real_step_rechecks_target_still_in_scope`） |
| `test_*.py` 文件 | 40 | 40（未新增文件） |
| mypy 源文件 | 72 | **72**（未新增源文件） |
| `app.url_map` 规则 / 方法绑定 / `/api/*` | 48 / 50 / 42 | **48 / 50 / 42**（**未新增路由**） |
| 被用例真实命中的方法绑定 | 49 / 50 | **49 / 50**（探针重跑，第七轮结论不变） |

**+11 的构成**（差额由 `git worktree add --detach <tmp> 1746f41` 检出基线后两个工作树各跑一遍
`--collect-only -q` 求差得到，不是推算：1296 → 1307）：

| 文件 | 用例 | 守什么 |
|---|---|---|
| `tests/unit/test_jobs_executor.py`（27 → **29**） | `test_real_step_rechecks_env_switch_at_execution_time` | 执行期复检 `GEF_ALLOW_REAL_SCAN`：删掉开关检查 → 红 |
| 同上 | `test_real_step_rechecks_active_scan_at_execution_time` | 执行期复检 `Scope.active_scan`：删掉复检 → 红 |
| `tests/unit/test_scope.py`（35 → **39**） | `test_normalize_target_accepts_schemeless_url`（参数化 3 例） | `www.example.test/a/b` → `www.example.test` |
| 同上 | `test_normalize_target_still_reports_a_broken_cidr_as_cidr` | `192.0.2.0/99` 仍报 CIDR 错（不许修成域名） |
| `tests/integration/test_public_scan_mode.py`（119 → **122**） | `test_scan_center_js_submits_the_current_target_input_not_a_stale_snapshot` | 提交取当前输入、输入变更使旧结论失效（四条口径） |
| 同上 | `test_scan_center_target_label_mentions_url` | 第 6 节标签如实写「域名 / IP / 网段 / URL」 |
| 同上 | `test_public_url_target_is_normalized_to_its_host` | 带协议 / 不带协议 / 带路径 → 落库同一个主机 |
| `tests/integration/test_assets_api.py`（28 → **29**） | `test_assets_js_never_renders_a_raw_scope_id_as_text` | 详情面板不再出现 `scope_9f3c…` |
| `tests/integration/test_m2_page_scan.py`（10 → **11**） | `test_page_has_no_dead_scan_report_block` | 摸不到的 `{% if scan_report %}` 分支不再回来 |

### 13.2 唯一被修复的既有用例：它此前**依赖缺陷**

`test_real_step_rechecks_target_still_in_scope`（`tests/unit/test_jobs_executor.py:241`）
原先只把 Scope 建好就断言 `STATUS_SUCCEEDED` —— 它能通过，**恰好是因为执行期不看环境开关**。
本轮把开关复检补上之后它第一个变红。**修法不是放松断言**：用例自己
`monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")` 并建一个 `active_scan=True` 的 Scope，
断言仍然是 `STATUS_SUCCEEDED` —— 它守的那件事（「目标仍在范围内就放行」）一字未变，
变的是它不再靠一个缺陷才成立。

### 13.3 变异验证（两条新用例不是「恰好通过」）

```text
变异：把 jobs/executor.py 新增的 real_scan_enabled() 检查与 require_active_scan() 复检
      两处都改成 `if False:`
结果：test_real_step_rechecks_env_switch_at_execution_time      FAILED
      test_real_step_rechecks_active_scan_at_execution_time     FAILED
还原：两处改回原样 → 同一对用例 PASSED
复核：工作树里没有残留变异（git diff 中无 if False）
```

### 13.4 本轮**没测**的东西（如实列出）

| 没测的 | 为什么 |
|---|---|
| 前端交互（改输入框 → 摘要有反应 → 提交被拦） | 项目**没有浏览器测试**（无 `package.json`、无 `tests/` 下 `.js`）。本轮新增的是**源码级守卫**，只能证明「代码里存在这四条口径」，不能证明「浏览器里真的这么跑」。渲染路径另用一次性 DOM 桩人工核对过，但那不是回归测试。 |
| 真实外网目标 | 硬约束：不扫未授权目标。全部用例走 mock / 注入假 runner，目标是 `example.test` 与 RFC 5737 保留段。 |
| 老入口 `POST /api/jobs` 不装公网白名单 | **刻意不改**（产品口径问题，会改既有 API 可用行为）。实测证据与三种可选口径记在 `docs/DECISIONS.md` §3.11.5 第 1 条。 |
| `category` 同名异义 | §12.6 已定：不改行为，只补文档映射。本轮未动。 |
| Agent 边界（方案第 12/16 节③） | §3.9 已挂「先不开工」，本轮**未触碰** `agent/` 任何文件。 |
| 同轮审计判定为「设计取舍」的五条（两级选择 / `resolve_scope` 函数名 / 旧步骤名 / 「无法选工具」的前提 / `#job-consent` 的表单归属） | 逐条实测核对后判定**不是行为缺陷**，完整核对表与实测依据在 `docs/DECISIONS.md` §3.11.6。写下来是为了让下一个人不必重新推一遍，**不是**把它们当待办留着。 |

