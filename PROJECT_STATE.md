# PROJECT_STATE.md — 项目状态板

> 这是给**接手者（人或 AI）**看的活文档，只讲「现在在哪、下一步做什么」。
> 设计与用法看 `README.md`；代码结构与坑看 `docs/CODEBASE_MAP.md`；历史变更看 `CHANGELOG.md`。
>
> **谁能改**：任何推进工作的人。每完成一步就更新本文件，别让它过期。
> **更新时机**：里程碑推进、验证结果变化、阻塞解除或新增、决策拍板后。

---

## 当前阶段

**Phase M4 已完成 · Phase M5 未开始 · 等 9 项决策拍板后再开工**

- 仓库：`Keqi2048905057/geteverything`（私有），分支 `main`，与 `origin/main` 同步
- 本地副本：`E:\Programmingtools\geteverything`，代码在子目录 `get_everything_framework/`
- 进度：M0 ✅ → M1 ✅ → M2 ✅ → M3 ✅ → M4 ✅ → **M5 ⬜ → M6 ⬜ → M7 ⬜**
- 更新日期：2026-10-01

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

**提前完成的 M6 条目**
- `scripts/run_local.ps1`（一键拉起 Web + worker，退出时收尾）
- `/health` 的完整字段（database / worker / queue / tools / modes / security）

**流程与沉淀**
- 逐里程碑验收报告（M0～M4）在本机 `docs/milestones/`，**按约定不入库**
- 给 Codex 的独立核查文档在桌面：`geteverything_项目汇总_给Codex检查.md`

---

## 部分完成

| 项 | 现状 | 差什么 |
|---|---|---|
| **`mode=real` 真实链路** | 代码层与真实子进程用例齐备（`tool_not_found` / `timeout` / `nonzero_exit` 都有测试）；状态机、错误码、超时、进程树清理都已验证 | **从未用真实工具打真实目标**（按硬约束刻意不做），「工具 + 真实目标」这条链路零实证 |
| **M4 观测元数据展示** | `httpx` 的 `status_code` / `title` / `webserver` / `tech` / `cdn` 已结构化落库 | 只在任务详情的证据面板里能看到，**没有专门的资产视图**（属 M5） |
| **M6 导出** | `exporter.py` 存在，能生成 CSV / JSON 文件 | `/api/export` 仍只返回**服务器路径**，不是流式下载；没有导出记录 |
| **M6 本机启动文档** | `CONTRIBUTING.md` 有环境搭建说明；`scripts/run_local.ps1` 可用 | 没有面向「新开发者 10 分钟启动」的完整文档；`scripts/check_env.py` 不存在 |
| **M7 mypy** | 其余验收项（pytest / ruff）已达标 | **mypy 仍有 34 errors**，见下 |

---

## 未完成

**M5 — 统一资产与变化检测**（下一阶段，尚未动工）
- [ ] `assets` / `observations` 两张表（`artifacts` 已有）
- [ ] 资产规范化
- [ ] 首次发现 / 最近发现
- [ ] 两次扫描之间生成 diff（新增 / 删除 / 变更）
- [ ] 资产列表、筛选、详情页
- [ ] 每条资产可追溯来源

**M6 — 导出、健康检查和本机运行脚本**
- [x] `/health` 完整字段
- [x] `scripts/run_local.ps1`
- [ ] `/api/export` 改为直接下载（当前只返回路径）
- [ ] 导出记录
- [ ] `scripts/check_env.py`
- [ ] 本机启动文档

**M7 — 测试和交付**
- [x] 单元测试 / API 测试 / worker 测试（405 项，超出原计划）
- [x] Scope 拒绝测试 / 上传安全测试 / 工具失败分类测试
- [ ] SQLite 并发测试
- [ ] 本地 fixture HTTP 测试
- [ ] **mypy 通过**（当前 34 errors）
- [ ] 一份测试报告

**其他待办（不在里程碑内，但已知）**
- [ ] `README.md` 未同步 M1～M4：仍写 `file_path` 上传后直接喂 `/api/run`（M2 起已废弃），未提鉴权 / Token / Scope
- [ ] `config.py` 中 `FEROXBUSTER_CONFIG.wordlist` 是开发机绝对路径，换机器必失败
- [ ] `/api/tools` / `/api/results` / `/api/export` 三个只读接口仍匿名可读（是否加鉴权待定）
- [ ] `/api/jobs` 只有 `limit`，没有游标分页
- [ ] 单并发 worker：`SCAN_LIMITS["max_concurrency"] = 2` 是未使用的配置项
- [ ] `storage.py`（旧库）无 WAL / 无 `busy_timeout`，`with conn` 只提交不关闭

---

## Known Existing Failures

> 这些是**已知且当前存在**的问题，不是「待办想法」。审查时不要重复报为新发现。

| # | 症状 | 位置 | 影响 |
|---|---|---|---|
| 1 | `mypy` 报 34 个错误 | 见下方分布 | M7 验收命令不通过；不影响运行 |
| 2 | `/api/export` 返回服务器文件路径 | `api/results.py:253` | 泄露本机目录结构 |
| 3 | `/api/tools`、`/api/results`、`/api/export` 匿名可读 | `api/tools.py`、`api/results.py` | 未授权即可读到扫描结果与库元信息 |
| 4 | 旧库并发写 `database is locked` | `storage.py:_get_connection` | 仅 `mode=real` 多任务并发时触发 |
| 5 | `FEROXBUSTER_CONFIG.wordlist` 是开发机绝对路径 | `config.py:236` | 换机器后 feroxbuster 直接失败 |
| 6 | `HTTPX_CONFIG.path` 默认 `"http-x"` | `config.py` | 本机靠 `E:\GoWorkspace\bin\http-x.cmd` 包装脚本指向 `httpx.exe` 才能跑；裸环境会 `tool_not_found` |
| 7 | `python -m pytest` 有 2 条 warning | `core/db.py:244`、`tests/unit/test_security_baseline.py:59` | 良性：FileIO 未关闭 + `SECRET_KEY` 未配置的预期告警 |
| 8 | `agent/client.py`、`agent/providers/*` 无任何调用方 | `agent/` | 「LLM 规划」实际由正则 + 模板决定，**不调用大模型**；「模型超时/返回格式错」类症状在当前路径不可达 |

**mypy 错误分布**（`mypy --no-incremental app.py core api jobs storage.py modules`，共 34 条 / 8 个文件）：

| 文件 | 错误数 | 备注 |
|---|---|---|
| `agent/action.py` | **20** | **最大头**。虽然 M7 的命令没写 `agent`，但 `app.py:21` 有 `from agent import handle_agent_message`，mypy 会顺着 import 查进来 |
| `modules/base.py` | 5 | M4 改过的文件 |
| `jobs/executor.py` | 2 | |
| `api/scan.py` | 2 | |
| `modules/httpx.py` | 2 | M4 改过的文件 |
| `config.py` | 1 | |
| `core/jobs.py` | 1 | |
| `modules/shuffledns.py` | 1 | M4 改过的文件 |
| **合计** | **34** | `checked 53 source files` |

> **把 `agent` 显式加进命令并不会让它变多很多**：`mypy ... modules agent` → **41 errors in 9 files**
> （多出的是 `agent/providers/openai_compat.py` 的 7 条）。也就是说 34 条里**已有 20 条来自 agent**。
>
> **想让它绿，得先啃 `agent/action.py`**——那是 20 条。若只图「M7 命令通过」，
> 因为 `app.py` 会 import agent，绕不开。

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
| F | 旧库历史数据（waybackurls 296 / enscan 37 等）是否迁进新库 | 决定 M5 是否顺带修 `storage.py` 并发 |
| G | httpx 观测元数据先在哪露出：按目标汇总页 vs 资产列表页 | 决定 M5 开工顺序 |
| H | 是否删除 `%TEMP%\gef_old_clone_full.bundle`（105.7 MB 历史备份） | 无风险，纯清理 |
| I | 两条已知 warning 是否顺手修 | 无风险，纯清理 |

> **只要 E 拍板，M5 就可以开工**；其余项可以并行推进或延后。

---

## 最近一次验证

```text
验证时间：2026-10-01 01:58
工作目录：E:\Programmingtools\geteverything\get_everything_framework

ruff:   All checks passed!
pytest: 405 passed, 2 skipped, 2 warnings in 43.12s
mypy:   Found 34 errors in 8 files (checked 53 source files)     ← M7 待修
        （含 agent 时为 41 errors in 9 files / 76 source files）

CI:     GitHub Actions 最近一次 success（提交 536fe49）
```

两条 warning 均为已知良性：`ResourceWarning: unclosed file`（`core/db.py:244`）、
`RuntimeWarning: 未检测到强 SECRET_KEY`（`tests/unit/test_security_baseline.py:59`）。

**基线演进**：M1 `70` → M2 `142` → M3 `236` → **M4 `405`**

---

## 最近一次 commit

> **本节的写法说明**：状态板自己也会被提交，所以「记录 HEAD」天然会差一个提交。
> 下面给的是**最近一次不含本文件改动的提交**，并附上自检命令。以 `git log -1` 为准。

```text
536fe49173e08aa2208c915f98cba2c4fc3273bc   ← 最近一次代码/其他文档提交
536fe49  docs: SECURITY.md 移除已过期的「多数 runner 未接入统一接口」 (2026-10-01)
```

自检：

```powershell
git log -1 --format="%H %s"     # 以这条输出为准
git status -sb                  # ## main...origin/main 且无改动 = 已同步
```

与 `origin/main` **同步**，工作区**干净**。累计 6 个提交：

| 提交 | 说明 |
|---|---|
| `0ddbcd2` | docs: 新增 PROJECT_STATE.md 项目状态板（本文件首次入库） |
| `536fe49` | docs: SECURITY.md 移除已过期的「多数 runner 未接入统一接口」 |
| `ab575bc` | docs: 同步 M4 增量（代码地图 + CHANGELOG） |
| `c5bae37` | feat: M4 铺开统一 runner 接口到其余 14 个 runner |
| `a311388` | fix: POSIX 下杀进程树会连调用方一起 SIGKILL |
| `61b0f9b` | chore: 建立独立仓库 geteverything 的初始提交（含 M0～M4 全部代码） |

> M0～M3 没有独立提交，全部压在根提交里——这是「只推最新代码、重开一份干净历史」的直接结果。

---

## 下一步该做什么（给接手者）

1. **先推进 C 之前的确认**：把上表 A～I 里你能定的定掉，**E 是关键路径**。
2. **M5 第一步**（E 拍板后）：`core/db.py:init_schema` 加 `assets` / `observations` 表 → 写迁移 → 从
   `RunnerResult.data[]` 落观测 → 再做 diff 与页面。
3. **顺手可做（不需要决策）**：
   - 修 `config.py:236` 的绝对路径 `wordlist`；
   - 同步 `README.md`（`file_path` 已废弃、补鉴权与 Scope 说明）；
   - 清 mypy 的 34 个错误（从 `modules/httpx.py` 和 `modules/base.py` 开始，共 15 条）。
4. **改完代码记得**：刷新 `docs/CODEBASE_MAP.md` 对应章节与 `last-mapped`，更新 `CHANGELOG.md`，
   并回来更新本文件的「最近一次验证 / 最近一次 commit」。

## 复现三条基线命令

```powershell
cd E:\Programmingtools\geteverything\get_everything_framework
python -m pip install -r requirement.txt -r requirement-dev.txt

python -m ruff check .                                # 期望 All checks passed!
python -m pytest                                      # 期望 405 passed, 2 skipped
python -m mypy app.py core api jobs storage.py modules # 期望当前 34 errors（M7 待修）
```

**运行期产物隔离（重要）**：测试**从不**写仓库的 `results/`。`tests/conftest.py` 会 patch
`storage.SQLITE_CONFIG["path"]`、`config.LOCAL_DB_CONFIG["path"]`、`core.uploads.UPLOAD_DIR`、
`core.health.OUTPUT_DIR`。**任何一处漏 patch 都会让测试污染仓库 `results/`。**

## 硬约束（不要违反）

- 不扫描任何**未授权的外部目标**；默认只用 mock runner 与 `127.0.0.1`
- 不执行 `git reset --hard`、`git clean -fd`
- 不覆盖 `get_everything_framework/scripts/OneForAll.exe` 的未提交差异（在旧 clone 里）
- 数据库、扫描结果、上传样本、密钥**不得提交进 Git**
- `results/scan_results.db` 只读；复现用临时库 `ScanResultStore(db_path=...)`
- 执行类过程文档（`DSH_执行提示词.md`、`本机联调版实施方案_DSH.md`、`docs/milestones/`）**留在本机，不入库**
