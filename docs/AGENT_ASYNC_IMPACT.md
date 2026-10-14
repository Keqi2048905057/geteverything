# Agent 同步 → 异步影响说明（P0-6 阶段二前置件）

> **本文档的用途**：按用户约束「**若某一步需要改变核心数据模型或执行架构，先停下来说明
> 具体影响再继续**」，在动手把 `agent/action.py` 接到 Job 链之前，把影响一次说清。
>
> **状态**：⏸ **未开工**。本文档不含任何代码改动，只做影响盘点与选项分析，等用户拍板。
> 依据：`GetEverything_DSH执行方案_Flask版.md` 第 6 节（P0 Agent 执行边界）与第 11 节
> （P1 统一旧执行链）；授权记录见 [`docs/DECISIONS.md`](DECISIONS.md) §3.2 的 P0-6 行。

---

> **▶ 本轮变化（测试阶段改造，2026-10）—— 页面入口消失，Agent 绕过行为一字未改**
>
> 测试阶段改造把旧首页连同**页面级 Agent Chat UI** 一起整体退役：
>
> - `web/templates/index.html` **已删除**（`git status` 显示 `D`）；
> - `app.py` 里 `POST /` 已**没有处理器** —— 路由层直接 **405**，`action=chat`
>   分支随之消失（连「未知操作」页面也回不出来了）；
> - `handle_agent_message` **不再被 `app.py` import**
>   （`from agent import handle_agent_message` 已删）；
> - 于是 I-5 补记实测的「**未登录 HTTP 请求走通 Agent → 真实扫描**」这条路径，
>   其**页面入口已不存在**：没有 `index()`，没有 `action` 分发，
>   也没有那个「只有手搓 POST 才能触发的死代码分支」。
>
> **但这不等于「Agent 的问题修好了」** —— 两件事必须分开读：
>
> - `agent/` 模块**仍在仓库里**，本轮**一行未改**（核对：`git status --porcelain -- agent/`
>   无输出），其内部绕过行为**原样保留**：`agent/action.py:419` / `:437` 直接调
>   `run_tools()`；`:498` 直接实例化 `HttpxRunner()`，`:503` / `:507` / `:511` 直接调
>   `runner.run_scan()` —— **全程不走 Job Service**；`agent/` 包对
>   `core/policy.py` / `core/scope.py` / `core/safety.py` 依旧**没有任何 import**
>   （在 `agent/` 下 grep `scope` / `policy` / `safety` 命中的 3 处全是 `dashscope`
>   的子串，不是真实引用，与原 §0 / I-5 的实测口径一致）；
> - **其它调用点仍在**：`agent_cli.py`（进程内 REPL）与
>   `agent/service.py:handle_agent_message()` 依旧直接可达 —— Agent 绕过
>   `GEF_ALLOW_REAL_SCAN` 与 Scope 的**能力本身没有消失**，少的只是**一个**
>   （而且本来就是零 UI 入口的）HTTP 触发面；
> - 按用户口径，**P0-6 阶段二仍未开工，本轮不碰**（「先不开工」）。
>
> ★ 所以本文件**其余结论全部继续有效**，本次只追加「载体没了」这一层事实。
> 措辞请一律读作「**入口消失**」，而不是「**问题修好了**」——
> `agent/` 自身的收口才是 P0-6 阶段二（见 §6 二-A）的验收内容。

---

## 0. 结论先行

| 问题 | 结论 |
|---|---|
| 迁移量有多大？ | **小**。`create_scan_job()` 是**同步落库**函数（只写 `jobs` + `job_steps`，不执行扫描），所以迁移**不会**让请求变慢，也**不引入**线程/队列/新依赖。真正要改的是 `agent/action.py` 里 2 个 handler + 3 处文案，约 100 行。 |
| 有没有破坏性接口变更？ | **有且只有一处**：`AgentAction` 必须拿到 `scope_id`。当前整个 `agent/` 包**没有任何 Scope 概念**（实测 grep `scope` / `policy` / `safety` 零命中），而 `create_scan_job` 的第一道就是「无 `scope_id` 直接 400」。 |
| 会不会动核心数据模型？ | **不会**。`jobs` / `job_steps` / `artifacts` / `observations` / `assets` 五张表早已存在且在用，本次不新增列、不改结构。 |
| 会不会动执行架构？ | **会，但只动 Agent 这一支**。这是本次改造的**目的**，不是副作用：Agent 的 httpx 路径**当前绕过 `GEF_ALLOW_REAL_SCAN` 双开关**（见 §3.3），迁移后被 Job 链自动卡住。 |
| 最需要用户拍板的是什么？ | **一个**：Agent 迁移后，它的只读能力（`summary` / `view_results` / `alive_results`，读**旧库**）是否同轮改读**新库**。不改的话会出现「Agent 刚提交的任务，Agent 自己查不到结果」的中间态。见 §5。 |
| 用户可见的降级？ | **有**。Agent 不再能「一句话说完就把 `发现 N 条 / 新增入库 M 条` 念出来」，改为回 `job_id` + `queued`。这是权限边界移动的代价，方案第 6 节要的就是这个。 |

---

## 1. 现状：直连点与它们各自在做什么

### 1.1 已确认的直连点（用户已核对）

| 位置 | 代码 | 作用 |
|---|---|---|
| `agent/action.py:14` | `from modules.httpx import HttpxRunner` | 拿到具体 Runner 类 |
| `agent/action.py:16` | `from tool_runner import run_tools` | 拿到旧同步编排函数 |
| `agent/action.py:419` | `run_tools(file_path=..., tools=[scan_tool], store=self.store)` | upload_id 场景的子域扫描 |
| `agent/action.py:437` | `run_tools(domain=..., tools=[scan_tool], store=self.store)` | 单域名场景的子域扫描 |
| `agent/action.py:498` | `runner = HttpxRunner()` | 直接实例化 Runner |
| `agent/action.py:503` / `507` / `511` | `runner.run_scan(...)` | 三种模式的存活探测 |

### 1.2 关键区分：6 个 handler 里只有 2 个是「实际扫描动作」

方案第 6 节的验收口径是「**所有实际扫描动作**都进入 Job」。`available_tools`
（`agent/action.py:83-114`）注册了 6 个 handler，按这个口径分类：

| handler | 是否「实际扫描动作」 | 现状 | 迁移后 |
|---|---|---|---|
| `subdomain` | ✅ **是** | `run_tools()` 同步真扫 | → `create_scan_job()` |
| `httpx` | ✅ **是** | `HttpxRunner.run_scan()` 同步真扫 | → `create_scan_job()` |
| `summary` | ❌ 否（只读查询） | 读**旧库** `self.store` | **保持同步** |
| `view_results` | ❌ 否（只读查询） | 读**旧库** | **保持同步** |
| `alive_results` | ❌ 否（只读查询） | 读**旧库** | **保持同步** |
| `export_results` | ⚠️ 半执行类 | `exporter.export_results()` 写文件 + 登记 `exports` 表 | **保持现状**（不属 §6 范围；方案 §5.2 的 `create_export` 是另一项未收拢动作） |

> **这条分类把迁移面从「6 个 handler」收窄到「2 个 handler」**，是本次评估里最大的
> 一个减负结论。只读查询与导出不产生「执行权」，不在 §6 的验收范围内。

---

## 2. 迁移后 Agent 的执行链（目标形态）

```text
用户一句话
  ↓
agent/intent.py:analyze_intent()        ← 不动
  ↓
agent/planner.py:build_plan()           ← 不动（PlanStep.tool 仍是 "subdomain"/"httpx"）
  ↓
agent/action.py:_execute_plan()         ← 不动（逐步遍历）
  ↓
agent/action.py:_execute_tool()         ← 不动（查 available_tools 表）
  ↓
agent/action.py:_tool_subdomain()       ← ★ 改：不再 run_tools，改调 create_scan_job()
agent/action.py:_tool_httpx()           ← ★ 改：不再 HttpxRunner，改调 create_scan_job()
  ↓
core/application.py:create_scan_job()   ← 已存在，勿重做
  ↓
core/policy.py:validate_job_targets()   ← Scope 唯一判定点
  ↓
core/jobs.py:create_job_with_status()   ← 写入 jobs + job_steps
  ↓
（返回 JobSubmission，Agent 渲染成「任务已创建」）
  ↓
jobs/worker.py → jobs/executor.py → modules/registry.py:build_runner() → Runner.run()
  ↓
job_steps.results_json / artifacts / observations / assets
```

**注意最后两行**：真正的执行发生在**另一个进程**（`python -m jobs.worker`），
且**不在 Agent 的调用栈里**。这就是「同步 → 异步」的全部含义。

---

## 3. 逐环节影响

### I-1 【语义】`total_found` / `total_inserted` 在创建时不可知

`_format_single_tool_result()`（`agent/action.py:704-762`）的 `subdomain` 分支现在念：

```
使用 subfinder 对 `a.test` 完成子域名收集，发现 N 条，新增入库 M 条。
```

这两个数字**只在扫描跑完之后才有**。迁移后创建任务时它们必然不存在，必须改文案为
「任务已创建，完成后查看」。**这是用户可见的最大变化。**

同一个 `AgentAction.run()` 的返回值结构（`_build_response`，`agent/action.py:802-820`）
也要新增字段，否则调用方拿不到 `job_id`：

```python
# 现在（8 个键）
{"message", "focus_domain", "conversation_history", "steps", "pending_plan",
 "plan_status", "export_url", "context_state"}

# 迁移后建议新增
{"job_ids": ["job_xxx", ...]}     # 一次 plan 可能有多步，每步一个 job
```

### I-2 【能力收窄】上传目标数上限从「无限制」变成 20

- 现在：`run_tools(file_path=...)`（`tool_runner.py:100`）对文件行数**没有任何上限**，
  上传 500 行的清单能一口气全跑。
- 迁移后：`create_scan_job()` 走 `SCAN_LIMITS["max_targets_per_job"]`
  （`config.py:143`，值 **20**），超了报 400「单个任务最多 20 个目标」。

这是**真实的行为收窄**，需要用户确认可接受（我认为可接受：20 与 `POST /api/jobs`
同口径，且上传超大清单本来就该拆批）。

### I-3 【能力缺口】httpx 的两种「来源模式」与 `tech_detect` 传不进 Job 链

`_tool_httpx` 现在按 `source` 分三种模式（`agent/action.py:500-513`）：

| 模式 | 现状行为 | 迁移后能否保留 |
|---|---|---|
| `existing_subdomains`（严格只用库里的） | 显式传 `candidates=[...]`，库里没有就报错 | ⚠️ **部分保留**（且原记录有误）：Job 链的 `executor.py` 只调 `runner.run(target)`，**不传 options**。原文写的「`HttpxRunner._load_candidates()` 自己会去旧库捞候选，所以天然成立」**经实测不成立** —— 旧库 `ScanResultStore` 是旧同步链的库，异步任务链从不写它，于是同任务里 subfinder 扫到 62 条、httpx 仍报「没有目标」。现已由执行器显式注入本任务上游观测（`jobs/executor.py:_upstream_candidates_for`）修好；「库里没有时」报的是 `no_input` + `status=skipped`（不是 `no_results`） |
| `stored_subdomains`（有就用，没有就退回域名本身） | Agent 里判空 | ❌ **做不到**：Job 链里由 Runner 单方面决定，Agent 无法施加「有则用、无则退回域名」的分支 |
| `direct_domain`（只探域名本身，**刻意不用**已有子域名） | `candidates=[domain]` | ❌ **做不到**：`HttpxRunner` 总是先捞库，除非给它加 option |
| `tech_detect=True` | 透传 `-tech-detect` | ❌ **传不进去**：`BaseRunner.run(target)` 的 `options` 形参（`modules/base.py:195`）在 `executor.py` 里没有被使用 |

**这三条是本次影响说明里技术含量最高的部分**。补救方案见 §4.3。

### I-4 【数据落点】结果从旧库搬到新库（这条最容易漏）

| | 现在 Agent 的扫描结果落到 | 迁移后落到 |
|---|---|---|
| 存储 | **旧库** `results/scan_results.db`（`tool_runner.save_runner_results()` → `storage.save_dedicated_results()`） | **新库** `results/local.db` 的 `job_steps.results_json` + `artifacts` + `observations` + `assets` |
| 可审计 | ❌ 无 job / 无 step / 无 artifact / 无审计事件 | ✅ 全有 |
| 可取消 / 可重试 | ❌ 无 | ✅ 有 |
| 执行期 Scope 复检 | ❌ 只在创建时判一次（而且现在是 0 次，见 I-5） | ✅ 每个 step 执行前复检（`jobs/executor.py:285-286`） |

后果：**Agent 之后的 `_tool_view_results` / `_tool_summary` 读的还是旧库**，
于是「Agent 刚提交的任务，Agent 自己查不到结果」。这正是 §5 那个待拍板问题。
`docs/DECISIONS.md` §3 已把「统一旧执行链」（方案 §11）登记为待授权项。

### I-5 【安全收益】当前 Agent 的两条执行路径都**绕过真实扫描开关**

这是我这次读代码时发现的、值得单独指出的**现存缺口**：

- `agent/action.py:437` → `run_tools(domain=...)` → `tool_runner.py:143` → `runner.run(target)`。
  **`tool_runner.py` 全文没有 `resolve_mode` / `real_scan_enabled` / `scope` 的任何引用。**
- `agent/action.py:498-511` → `HttpxRunner.run_scan(...)`。
  **`modules/httpx.py` 同样不检查 safety。**

也就是说：**只要目标能过 Agent 自己的域名正则（`DOMAIN_PATTERN`）与黑名单
（`AGENT_BLOCKED_DOMAINS` / `AGENT_BLOCKED_SUFFIXES`），Agent 就能真实外发扫描请求，
不需要 `GEF_ALLOW_REAL_SCAN=true`，也不需要任何 Scope。**

对比主链：`POST /api/jobs` 与首页都要过 `resolve_mode()`（查环境开关）
+ `scope.require_active_scan()`（查 Scope 声明）**双重**门槛。
（▶ 测试阶段后「首页」这一半载体已删除，但 `POST /api/jobs` 这一半不变，
本段结论不受影响。）

> 所以 P0-6 阶段二的收益**不只是**「可审计」——它把 Agent 这条**实际存在的越权通道**
> 收回到与主链同一道门上。对应用户在 `docs/DECISIONS.md` §3.2 里定的验收点：
> 「**真正的验收点是『权限边界移动了』，而不只是『函数调用换了』**」。
> 而且按硬约束，本项目**从未**用 Agent 打过真实外部目标，所以这是「修潜在缺口」，
> 不是「事故复盘」。

#### I-5 补记（2026-10-04）：这条通道**曾经是匿名的** —— 已修

上面写的「绕过真实扫描开关」当时只说到「Agent 能绕」，**漏掉了更严重的一半**：
`app.py:index()` 的认证守卫原本**只护 `action=scan`**（`if action in _SCAN_ACTIONS:`
里才调 `_require_admin_for_page()`），而 `elif action == "chat":` 分支**没有任何认证调用**。
两者叠加的后果是：**未登录的 HTTP 请求也能走通这条路**。

实测证据（2026-10-04，全部只读、`GEF_ALLOW_REAL_SCAN=false`、目标为保留域
`example.test`、`BaseRunner._run_subprocess` 被断言装载过的桩替换）：

| 探针 | 场景 | 结果 |
|---|---|---|
| `test_client` 两步（带 cookie） | 匿名 `action=chat` 两步后 | `agent.action.run_tools` 被调用 **1 次** |
| 真起 waitress（`127.0.0.1:5089`） | 同上 | 同上，**1 次** |
| `test_client` 桩住 `_run_subprocess` | 同上 | 子进程入口被触达 **1 次**，命令为 `subfinder -d example.test …` |
| 同上，**单请求不带 cookie** | 只发第一步 | 触达 **0 次** —— 必须带 cookie 走完「意图 → 确认执行」两步 |
| 对照：匿名 `POST /api/run`、`/api/tool/subfinder/run`、`/api/jobs` | — | 全部 **401** |

三点必须写清楚：

1. **它是两步、不是一步**：`_handle_pending_plan` 要读到上一步存进 Flask session 的
   `pending_plan`，所以必须带 cookie 连续请求（浏览器里就是「先问一句、再回『确认执行』」）。
   **跨站表单直发打不通**（无 cookie 时 0 次触达）——这一点降低了「被动挨打」的风险，
   但没有降低「本机任何能发 HTTP 的程序」的风险。
2. **`GEF_ALLOW_REAL_SCAN=false` 拦不住它**，Scope 也拦不住，且**不产生 `job.created` 审计**。
   `.env` 里本机 `GEF_ALLOW_REAL_SCAN=true`、Token 已配非空，所以这不是「测试环境特例」。
3. **零测试覆盖**：`tests/` 里 `action=chat` 的命中数是 **0**；
   `test_api_auth_contract.py` 的 `ADMIN_ONLY` **从未列出 `("POST", "/")`**。
   同一条分支自初始提交 `61b0f9b` 起就是这个形状（`git log -S` 复核），不是近期回归。

**已修**（2026-10-04，独立提交）：把守卫**提到 `action` 分支之前**，使 `POST /` 的两个动作
一并需登录；补两条回归用例（匿名 chat → 401、匿名首页不渲染授权资产）。
详见 `docs/DECISIONS.md` §3.14。**注意这不是「新增边界」，是补上 `core/auth.py` 自己
docstring 里早就写明的边界** —— 所以它不触碰「不放宽 / 不绕过认证边界」那条红线，方向相反。

> **顺带修掉的第二条**：同一轮实测发现匿名 `GET /` 会下发**整份授权资产清单**
> （范围名称 + `allowed_domains` + `allowed_cidrs` + 状态）—— `app.py` 当时无条件执行
> `context["scopes"] = _load_scope_options()`，而 Phase 1 又把目标渲染进了资产卡片。
> 资产页 `/assets` 一直是按登录态过滤的（`scopes=_load_scope_options() if is_authenticated else []`），
> 首页漏了同一个判断。现已统一。

> **▶ 测试阶段补充（2026-10，只追加、不改上文）—— 这两条记录的 HTTP 载体已消失**：
> I-5 补记里那个「匿名 `action=chat`」的载体是 `app.py:index()`。旧首页退役后
> `index()` 已**整体删除**（`GET /` 只剩 302 → `/scan-center`），
> `_require_admin_for_page()` 与 `action` 分支本身也一并从 `app.py` 中删除。
> 守卫因此**退化为**「**根路径只做 302、`POST /` 路由层 405**」——
> 不再有「先进守卫、再分动作」这个结构，也就没有了「把守卫提到分支之前」的对象。
> 相关用例：`tests/integration/test_web_baseline.py:21` `test_root_redirects_to_scan_center`、
> `tests/integration/test_m2_page_scan.py:59` `test_retired_page_actions_are_405_and_create_no_job`、
> `tests/integration/test_m2_page_scan.py:100` `test_home_page_template_file_is_gone`、
> `tests/integration/test_m2_page_scan.py:157` `test_root_path_only_redirects`。
>
> ⚠️ **不要读成「这条通道已修好」**：消失的是**HTTP 页面入口**。
> `agent/action.py:419` / `:437`（`run_tools`）、`:498`（`HttpxRunner()` 实例化）与
> `:503` / `:507` / `:511`（`runner.run_scan`）**全部还在**，
> `agent_cli.py` 这个进程内入口也还在，
> Agent 依旧没有 Scope 概念、依旧不读 `GEF_ALLOW_REAL_SCAN`。
> 收口属 **P0-6 阶段二**（§6 二-A），本轮未开工。

### I-6 【新接线】`scope_id` 必须贯穿三层（唯一破坏性接口变更）

`create_scan_job()` 的第一道是 `validate_job_targets(scope_id, targets)`
→ `require_scope()`（`core/policy.py:95-107`），`scope_id` 为空**直接 400**。

> **▶ 测试阶段行号更正（2026-10，实测重读）**：`def require_scope` 现在在
> **`core/policy.py:104`**（上文 `:95-107` 是写入时的旧值）。开放模式分支见
> **`core/policy.py:104-138`**：未开开关且缺 `scope_id` 时 **400**，
> `details={"field":"scope_id","env":"GEF_OPEN_SCAN_MODE"}`，文案直接点名
> `GEF_OPEN_SCAN_MODE=true`，**不静默降级成 mock**；显式 `scope_id == "open"`
> 且开关开启时返回 `open_scope()`（`:135`），其它显式 scope 一律
> `scope_store.require(...)`，不存在**始终 403**（开放模式也不放过）。
>
> ⚠️ **这条更正不改变本文件的核心结论**：Agent 仍然**完全没有 Scope 概念**
> —— `require_scope()` 是 `create_scan_job()` 里的判定，而 Agent 根本**不走**
> `create_scan_job()`（见 I-5：它直连 `run_tools` / `HttpxRunner`），
> 所以这个判定对 Agent 一次都不会被触发。
>
> 两件事必须分开，**不要误读成「Agent 的问题被开放模式顺带解决了」**：
> 开放模式（`GEF_OPEN_SCAN_MODE`）让「**免授权**」在 **Web 入口**（`POST /api/public-jobs`）
> 变成**合法行为** —— 那是**放宽**业务要求；而 Agent 的绕过是**根本不经过**这道判定 ——
> 那是**旁路**。前者是「门开着让人进」，后者是「墙上有个洞」。两者结果看似都是
> 「没 Scope 也能扫」，成因与修法完全不同；后者仍要靠 P0-6 阶段二把 Agent 接上
> `create_scan_job()` 才能收口。

而 Agent 当前**完全没有** Scope 概念。所以需要：

```text
app.py:index() 的 action=chat 分支        ← 从表单/会话取 scope_id
  → agent/service.py:handle_agent_message(scope_id=...)   ← 新增参数（透传）
    → AgentAction.__init__(..., scope_id=...)             ← 新增参数
      → _tool_subdomain() / _tool_httpx()                 ← 用 self.scope_id 调 create_scan_job()
```

建议 **`scope_id` 必填、不给默认值**：给了默认值 `None` 会把「忘记传」推迟到
运行期变成 400，而必填能让 mypy 与测试**立刻**报出来。

另需注意首页 UI：改后 `app.py` 的 `action=chat` 分支**前端没有任何入口**
（实测 `web/templates/index.html` 与 `web/static/*.js` 里 grep `chat` / `agent` 零命中，
没有任何 `action=chat` 的表单）。**这是一段只有手搓 POST 才能触发的死代码**——
它该不该连带补一个带 Scope 下拉的聊天框，属可选项，建议**不同轮做**。

> **2026-10-04 行号与认证口径更新**：chat 分支现在是 `app.py:294-324`
> （`elif action == "chat":` 在 `:294`），守卫调用在 `:264`、守卫定义在 `:211`
> —— 即 `POST /` 的两个动作**都要登录**，见 I-5 补记。
> 「前端无入口」这一条**仍然成立**（`web/` 里 grep `chat` 依旧零命中）——
> 也正因如此，它此前是**零测试覆盖 + 无 UI 入口 + 无认证**三者叠加的盲区。
>
> ⚠️ **行号会继续漂移**：本节写于 2026-10-04，此后 `app.py` 任何一次增删都会再次移动它。
> 开工时**按函数名定位**（`index()` 里的 `elif action == "chat":`），不要直接照行号跳。

> **▶ 测试阶段（2026-10）**：以上「前端无入口」的判断**保留原样**（它记录的是当时的实测）。
> 该页面与 `action=chat` 分支现已**整体删除** —— `web/templates/index.html` 已删、
> `app.py` 的 `index()` 与 `elif action == "chat":` 已删、`POST /` 现在返回 **405**。
> 因此本节末尾那句「它该不该连带补一个带 Scope 下拉的聊天框」**先失去载体**：
> 没有页面可补；若将来要做，是**新建**一个独立 Agent 页面（`app.py` 顶部 docstring
> 已写明「将来需要独立 Agent 页面时单独设计 `/agent`」），而不是「在首页上补」。
> 上面 `app.py:294-324` / `:264` / `:211` 这些**当轮行号全部不再适用**，
> 相关位置**按函数名 / 分支名定位**；分支本身也已不存在。

### I-7 【双白名单】Agent 自己的域名策略与 Scope 会互相打脸

Agent 现在有第二套白名单（`AGENT_BLOCKED_DOMAINS` / `AGENT_BLOCKED_SUFFIXES` /
`AGENT_ALLOWED_SUFFIXES`，`agent/action.py:77-80`）+ 自己的限流
（`AGENT_TOOL_MIN_INTERVAL_SEC`，`_enforce_rate_limit`）。迁移后**唯一权威是 Scope**，
两套并存会出现四种组合：

| Agent 判定 | Scope 判定 | 结果 |
|---|---|---|
| 放行 | 放行 | ✅ 创建任务 |
| 放行 | 拒绝 | 403（Scope 生效，正确） |
| 拒绝 | 放行 | Agent 先抛 400（**用户会觉得 Scope 白配了**） |
| 拒绝 | 拒绝 | 400（信息比 403 少） |

建议：**保留 Agent 侧作为「前置友好提示」，但在文档与回复里明确 Scope 是唯一权威**，
且 `_tool_httpx` / `_tool_subdomain` 的拒绝文案要写明「这是对话层的预检，
真正的授权判定在 Scope」。**不要**为了让两套一致而把 Scope 判定抄进 Agent ——
那正是 `core/application.py` docstring 第 32 行列出的「同一套判定有两份实现」老问题。

### I-8 【交互语义】三条入口各自会怎么变

| 入口 | 现状 | 迁移后 |
|---|---|---|
| `agent_cli.py`（`python agent_cli.py` 进 REPL） | 一行输入 → 直接打印「发现 N 条，新增入库 M 条」 | 打印「任务已创建：`job_xxx`（`queued`）」。**数字要再发一轮「查看结果」才看得到**，且必须先启动 `python -m jobs.worker`，否则任务永远停在 `queued`。CLI 还缺 `scope_id` 入口（见 I-6） |
| 首页 `action=chat` | 同上（但**前端无入口**，见 I-6） | 同上，影响面实际为零 |
| `pending_plan` 二次确认（`_handle_pending_plan`，`agent/action.py:191-260`） | 用户回「确认执行」→ **同步跑完并回报** | 用户回「确认执行」→ **创建任务并回报 `job_id`** |

> **▶ 测试阶段（2026-10）**：上表三行里，第二行「首页 `action=chat`」这个入口
> **已随旧首页退役而消失**（`web/templates/index.html` 已删、`POST /` 返回 **405**）——
> 表格保留当时的分类，不作改写。剩下两条入口**都还在**：`agent_cli.py` 与
> `pending_plan` 二次确认，且它们的同步直连行为**一字未改**。
> 所以 I-8 描述的「同步 → 异步」改造需求**依然成立**，只是少了一条 HTTP 触发路径。

**明确不做的一件事**：不在请求线程里轮询等 worker 跑完（那等于把异步硬拗回同步：
占住 Flask 线程、worker 没启动时会挂住、还丢了「可取消」的意义）。
Agent 的「执行」从此一律是「**提交**」。

### I-9 【不可逆点】回复语义一旦改，就回不去了

「Agent 一句话把结果念出来」是当前最直观的功能。迁移后再想回到同步，
就要把 Job 链再拆掉一次。所以**这项决定值得单独确认**，而不是当作技术细节带过。

---

## 4. 必须新增/修改的代码清单（预估）

### 4.1 必改（不批就不动）

| 文件 | 改动 | 预估 |
|---|---|---|
| `agent/action.py` | 删 `:14` / `:16` 两行 import；`_tool_subdomain`（`:401-445`）与 `_tool_httpx`（`:490-528`）改调 `create_scan_job()`；`_format_single_tool_result`（`:714-738`）的 subdomain/httpx 分支改文案；`_attach_storage_info`（`:542-549`）的 storage 指向；`_build_response` 加 `job_ids` | ~90 行 |
| `agent/action.py:__init__` | 新增 `scope_id` 参数 | ~5 行 |
| `agent/service.py` | `handle_agent_message()` 透传 `scope_id` | ~3 行 |
| `app.py:294-324` | `action=chat` 分支传 `scope_id`（行号为 2026-10-04 值，**按函数名定位**） | ~5 行 |
| `agent_cli.py` | 加 `--scope-id`（或环境变量），否则 CLI 无法提交任何任务 | ~10 行 |
| `tests/unit/test_agent_boundary.py` | 见 §4.2 | ~120 行 |

> **▶ 测试阶段（2026-10）对上面这张清单的影响**：表格**保持原样**，只追加两点：
>
> 1. `app.py:294-324` 那一行**已无落点** —— 旧首页退役后 `app.py` 页面层
>    （`index()` / `_require_admin_for_page()` / `_SCAN_ACTIONS` / `action=chat` 分支）
>    已整体删除，**没有 `action=chat` 分支可传 `scope_id`**。
>    将来若做独立 Agent 页面，那是**新增** `GET/POST /agent`，不是改首页；
>    这一项的 ~5 行估计**转移**到新页面，而不是省掉。
> 2. 表格里所有 `app.py` 行号**均为 2026-10-04 值，不再适用**，
>    定位一律**按函数名 / 分支名**（而其中多数函数名现在已不存在）。
>
> ⚠️ 这一变化**不缩小二-A 的实质工作量**：`agent/action.py`、`agent/service.py`、
> `agent_cli.py`、`tests/unit/test_agent_boundary.py` 四项**原样待办**，
> 也正是「Agent 收口」的真正内容。**页面没了不等于 Agent 收口了。**

### 4.2 测试必须同轮改（否则会以「patch 打空」的形式静默或直接报错）

`tests/unit/test_agent_boundary.py` 现在 patch 的正是这两个**将要消失**的符号：

| 行 | 现状 | 迁移后 |
|---|---|---|
| 54、89、101 | `monkeypatch.setattr("agent.action.run_tools", ...)` | patch 目标消失。`raising` 默认为 `True` 会直接 `AttributeError`；**若有人写成 `raising=False` 就会静默走偏**（这正是用户提醒的那个风险）。应改为 patch `create_scan_job` |
| 74 | 同上，桩函数返回 `run_tools` 的 report dict | 桩函数要返回 `JobSubmission`（或它的 `to_dict()`） |
| 208 | `monkeypatch.setattr("agent.action.HttpxRunner", _FakeHttpxRunner)` | 同左，patch 目标消失 |

逐条用例的处置：

| 用例 | 处置 |
|---|---|
| `test_agent_subdomain_rejects_arbitrary_file_path`（+4 参数化） | **保留核心断言**（「一次都没真正跑」），检测手段换成 `create_scan_job` 未被调用 |
| `test_agent_subdomain_accepts_controlled_upload_id` | 改断言：`create_scan_job` 收到的是 `upload_id`，**不再**是解析后的 `file_path`（`file_path` 不进服务层，这是好事） |
| `test_agent_subdomain_rejects_unknown_upload_id` / `..._traversal_upload_id` | 保留，改 patch 目标 |
| `test_agent_httpx_returns_metadata_items` | ⚠️ **被测对象整个消失了**（`_tool_httpx` 不再有 `items`、不再调 `run_scan`）。它的价值（「items 必须是元数据字典」）已由 `tests/integration/test_m7_local_e2e.py` 在 Job 链上覆盖。**建议删除并在文件头注明去向**，而不是硬改成一条没有意义的用例 |
| `test_agent_export_*`（2 条） | 导出不迁，**保持绿** |
| `test_agent_execution_records_steps`（只读路径） | **保持绿** |
| `test_agent_unknown_tool_is_reported_not_executed` | 保留 |

**同轮必须新增**（这是「权限边界移动了」的证据）：

1. Agent 提交后 `jobs` 表里确有该任务，`scope_id` / `mode` / `targets` 逐字段正确；
2. **越界目标**：Agent 用不在 Scope 内的域名 → 403，且 `jobs` 表**零新增**；
3. **无 `scope_id`** → 400，同样零新增；
4. **`real` 模式未开 `GEF_ALLOW_REAL_SCAN`** → 403（这是迁移**新增**的安全锁，见 I-5）；
5. 源码守卫（与 `tests/unit/test_application_service.py` 的三条守卫同规格）：
   `agent/action.py` 里不出现 `run_tools` / `HttpxRunner` / `run_scan`；
6. Agent 回复里不再出现 `total_found` 数字，且**带 `job_id`**。

### 4.3 I-3 那三条缺失能力的补救选项（需用户选）

| 选项 | 做法 | 代价 |
|---|---|---|
| **A（推荐，最小）** | 迁移**只做**「域名 → httpx」这一种，Agent 的 `source` 分支**收敛为一句话**：无论哪种 source，都提交 `tools=["httpx"]`、`targets=[domain]`；由 Runner 自己决定候选来源。同时在回复里说明「httpx 会自动使用库中已有的子域名」 | 丢失 `direct_domain`（强制只探域名本身）与 `tech_detect` |
| **B** | 给 `job_steps` 加一个 `options_json` 列，让 `executor.py` 把 options 传给 `runner.run(target, options=...)` | **改动数据模型**（新增列）→ 触发「先停下来说明」的约束，且要动 `core/db.py` 迁移 |
| **C** | 用 `scenario` 之外的既有字段曲线救国（例如把 `tech_detect` 塞进 `target` 的 query） | ❌ **不做**，属于往字段里塞语义的脏做法 |

我倾向 **A**：它满足方案 §6 的三条验收，不动数据模型，代价是把两个边缘能力明确登记为
「迁移后暂不支持」。若用户要保住 `tech_detect`，那就得走 B 并单独授权。

---

## 5. ★ 需要用户拍板的那一个问题

> **Agent 的只读 handler（`summary` / `view_results` / `alive_results`）要不要同轮改读新库？**

背景见 I-4：Agent 的**写**搬到新库，**读**还在旧库。

| 选项 | 做法 | 结果 | 改动面 |
|---|---|---|---|
| **A（我推荐）** | **不改**。只读继续读旧库，接受中间态；Agent 回复里主动说明「结果已进入任务 `job_xxx`，可在任务详情 / 资产页查看」 | 「Agent 查不到自己刚跑的结果」，但**用户被明确告知去哪儿看**；数据合流交给方案 §11 那一项单独授权 | 最小，不碰 `storage.py` 与新库查询 |
| **B** | 同轮把三个只读 handler 改成读新库（`core/jobs.py` + `core/assets.py`） | Agent 自洽，但**回复口径全变**（「子域名 N 条」→「资产 N 条」），且要新写三个新库查询函数 | 大，且与 §11 的授权范围重叠 |
| **C** | 只读能力**暂时下线**，Agent 只保留「提交任务」 | 最干净，但 Agent 少了一半功能 | 中（还要改 intent / planner 的拒绝文案） |

**推荐 A 的理由**：方案 §6 的验收是「所有**实际扫描动作**都进入 Job」+「都再次通过 Scope」，
只读查询既不是扫描动作、也不涉及权限边界，**不在这个里程碑的验收范围内**。
把它一起做会把「权限边界移动」这个清晰的目标，混成一次「数据层大合流」，
违反用户定的「不要一次性大范围重构」。

---

## 6. 建议的分阶段（若批准）

| 阶段 | 内容 | 是否需新授权 |
|---|---|---|
| **二-A** | I-1 / I-4 / I-5 / I-6 / I-7 / I-8 全部落地：`scope_id` 贯穿、两个 handler 改调 `create_scan_job`、文案与 `job_ids` 改写、测试同轮改（§4.2 全部 6 条新用例） | ⏳ 本次待批 |
| **二-B** | 只读 handler 改读新库（等价于方案 §11 的一部分） | ❌ 需单独授权（§5 选项 B） |
| **二-C** | `agent_cli.py` 加 `--scope-id` 与可选的「提交后轮询并打印结果」；首页补带 Scope 下拉的聊天框 | ⏳ 可并入二-A 或单独 |

> **▶ 测试阶段（2026-10）**：本轮（测试阶段改造）**未**触碰 P0-6 阶段二的任何一项
> —— 二-A / 二-B / 二-C 三行的内容与授权状态**全部不变**（二-A ⏳ 待批）。
> 本轮改的是**页面层**（旧首页退役），`agent/` 一行未改。
> 仅一处措辞需要按现状理解：**二-C 里「首页补带 Scope 下拉的聊天框」的载体已不存在**
> （`web/templates/index.html` 已删、`POST /` 返回 405）；
> 将来要做的话是**新增**独立 Agent 页面（`app.py` docstring 已记「单独设计 `/agent`」），
> 这一项的**授权状态与工作量均未因此调整**。

**二-A 的验收（严格对应方案 §6 三条）**：

1. 「Agent 只能生成业务动作」→ 源码守卫：`agent/action.py` 里 grep 不到
   `run_tools` / `HttpxRunner` / `run_scan`；
2. 「所有实际扫描动作都进入 Job」→ Agent 每执行一次，`jobs` 表必有对应记录
   （用真实 `create_scan_job` + 临时库断言，不用 mock）；
3. 「所有实际扫描动作都再次通过 Scope」→ 越界目标用例返回 403 且**零 job 落库**；
   外加 I-5 那条：未开 `GEF_ALLOW_REAL_SCAN` 的 `real` 请求返回 403。

**二-A 明确不做的事**：

- 不在请求线程里等 worker（I-8）；
- 不改 `jobs` / `job_steps` 表结构（否则触发「先停下来说明」）；
- 不删 `tool_runner.py` / `modules/httpx.py:run_scan`（方案 §11 明确「不要直接删除」，
  旧同步接口 `POST /api/run` 仍在用）；
- 不把 Scope 判定抄进 Agent（I-7）；
- 不动 `intent.py` / `planner.py` / `plan_state.py` 的骨架。

---

## 7. 影响面速查

```text
必改代码文件     5 个（agent/action.py、agent/service.py、app.py、agent_cli.py、
                       tests/unit/test_agent_boundary.py）
预计增删         ~230 行（其中测试 ~120 行，业务 ~110 行）
数据模型         不动
数据库结构       不动
新增依赖         无
新增必填参数     1 个（scope_id）
用户可见降级     1 处（不再一句话念出结果数字）
用户可见收窄     1 处（上传目标上限 20）
迁移后暂不支持   3 项（httpx 的 direct_domain / stored_subdomains 回退 / tech_detect）
安全收益         1 项（收回 Agent 绕过 GEF_ALLOW_REAL_SCAN 的通道，见 I-5）
```
