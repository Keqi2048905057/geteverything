# TEST_REPORT.md — 测试报告（M7 交付项）

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
