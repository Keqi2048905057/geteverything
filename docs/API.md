# API.md — HTTP 接口说明

> **本文回答一个问题**：这套系统对外暴露了哪些 HTTP 接口，每个接口怎么调、要不要管理员身份、成功/失败分别返回什么。
> 想看「为什么这样分层」读 [`ARCHITECTURE.md`](ARCHITECTURE.md)；想在本机跑起来读 [`DEPLOYMENT.md`](DEPLOYMENT.md)。
>
> **本文的接口清单不是抄 README，而是用 `app.url_map` 实测枚举出来的**（见文末 §7 的核对方法）。
> README 的接口表已与代码脱节，凡冲突处以本文为准。

---

## 1. 概览

| 计数项 | 数值 |
|---|---|
| `app.url_map` 规则总数 | **48** |
| 含方法展开的路由绑定 | 50（`/` 与 `/login` 各支持 GET + POST） |
| `/api/*` 接口 | **42** |
| 其中**需要本地管理员认证** | **32** |
| 其中**匿名可读**（有意保持，契约测试锁定） | 7 |
| 其中**登录相关**（公开） | 3 |
| 非 `/api` 路由 | 6（`GET /health` + 5 条页面/静态） |

统一约定：

* API 一律挂在 `/api` 前缀下（`api/__init__.py: api_bp = Blueprint("api", url_prefix="/api")`），
  **唯一例外是 `GET /health`**——它在仓库根路径、免登录，设计成给探针/脚本用。
* 数据格式为 JSON。`api/auth.py`、`api/upload.py`、`api/settings.py` 也接受表单/multipart。
* 成功响应使用 `{"ok": true, ...}` 信封；**`api/results.py`、`api/tools.py` 的历史接口例外**
  （`{"results": [...]}` / `{"tools": [...]}`），它们早于错误模型统一，为兼容本机脚本保留原样。
* **任何响应都不含服务器文件路径**：导出只给 `export_id` + `download_url`，证据只给
  `artifact_id` + 内容，上传只给 `upload_id`。这是被测试钉住的契约。

---

## 2. 认证

只有一个本地管理员身份，没有用户表、没有角色、没有权限分级。

**三种携带方式**（任一即可，`core/auth.py`）：

| 方式 | 用法 | 场景 |
|---|---|---|
| HttpOnly Session | 先 `POST /api/auth/login`，浏览器自动带 Cookie | Web 页面 |
| 请求头 | `X-Local-Token: <LOCAL_ADMIN_TOKEN>` | 脚本 / curl |
| 表单或 JSON 字段 | `token=<LOCAL_ADMIN_TOKEN>`（字段名 `core/auth.py:TOKEN_FORM_FIELD`） | 登录页表单 |

* Token 来源：`.env` 的 `LOCAL_ADMIN_TOKEN`。**未配置时进程启动会生成一次性随机 Token 并打印到控制台**，
  重启即失效；`GET /api/auth/session` 的 `token_is_ephemeral` 会告诉你当前是不是这种状态。
* 比对用 `hmac.compare_digest`（定长比较，避免时序侧信道）。
* 未认证的统一返回 **401** + `error_code=unauthenticated`；**不回显 Token 值**。

**需要认证的 32 条**：`/api/settings*`（4）、`/api/scopes*`（3）、`/api/jobs*`（10，
含 Phase 4 新增的 `GET /api/jobs/<job_id>/results`）、`/api/artifacts/<id>`（1）、
`/api/assets*` 与 `/api/observations`（4）、`/api/run` 与 `/api/tool/<n>/run`（2）、
`/api/upload`（1）、**`/api/projects*`（4）、`/api/public-jobs`、`/api/public-jobs/check`
与 `/api/scan-center`（3）**。

**匿名可读的 7 条**（有意保持的现状，由 `tests/integration/test_api_auth_contract.py` 与
`tests/integration/test_export_contract.py` 锁定）：`/api/tools`、`/api/databases`、`/api/results`、
`/api/tool/<n>/results`、`/api/export`、`/api/export/<id>/download`、`/api/exports`。

另有 3 条**登录相关**的公开接口（不属于上面两类）：`POST /api/auth/login`、
`POST /api/auth/logout`、`GET /api/auth/session`。

> ⚠️ README 只列了其中 **3** 条。若要给这 7 条收口鉴权，必须先改上面两个测试并同步
> `SECURITY.md`，否则 CI 会红——这是刻意的「改契约要有人知道」。

---

## 3. 错误模型

统一由 `core/errors_handlers.py` 处理，错误响应形状：

```json
{"ok": false, "error_code": "bad_request", "error_message": "人读说明", "details": {"field": "scope_id"}}
```

状态码 ↔ `error_code` 映射（`core/errors_handlers.py`）：

| HTTP | error_code | 典型触发 |
|---|---|---|
| 400 | `bad_request` | 缺参、格式错、非法枚举值 |
| 401 | `unauthenticated` | 未带 Token / Session |
| 403 | `permission_denied` | 通用越权 |
| 403 | `scope_violation` | 目标越界；真实扫描开关未开；Scope 的 `active_scan=false` |
| 404 | `not_found` | 任务 / 资产 / Scope / 证据 / 导出记录不存在 |
| 405 | `method_not_allowed` | 方法不对 |
| 500 | `unknown_error` | 未捕获异常，响应体不含堆栈（详情只在服务端日志） |

工具/执行层的业务错误码另有一层 `core/errors.py:ErrorCode`（如 `no_results`、`config_error`、
`tool_not_found`、`timeout`、`parse_error`、`partial_success`、`interrupted`），出现在
Job 步骤、`RunnerResult` 与导出数据的字段里，**不改 HTTP 状态码**。

---

## 4. 页面与静态资源

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| GET | `/` | 无 | 首页：任务列表 + 扫描表单 + Agent 对话 + 登录入口 |
| POST | `/` | **执行类动作需登录** | 表单 `action=scan` 创建 mock 任务（未登录 401）；`action=chat` 可匿名 |
| GET | `/assets` | 无 | 资产页骨架。匿名可打开但只显示提示，**不下发 Scope 名称**；数据由 `static/assets.js` 调 `/api/assets` |
| GET | `/scan-center` | 无 | 扫描中心页骨架（公网授权测试模式）。匿名可打开但只显示提示；数据由 `static/scan_center.js` 调 `/api/scan-center`。**页面上的一切提交都只是转发到 `/api/…`**，闸门全在服务端 |
| GET | `/login` | 无 | 登录页；已登录时重定向到 `/` |
| POST | `/login` | 无 | 提交 `token` 表单字段；成功 302 → `/`，失败重渲染并提示 |
| GET | `/static/<path:filename>` | 无 | `web/static/` 下的 `app.css` / `app.js` / `assets.js` / `scan_center.js` |

---

## 5. 系统

### GET /health

免登录、挂在**根路径**（不是 `/api/health`）。只读、无副作用：应用库文件不存在时直接返回 `missing`，
**不会**顺手建库（避免探针把 schema 建出来）。

```json
{
  "ok": true,
  "version": "local-0.1.0",
  "database": "ok",
  "worker": "ok",
  "queue": {"queued": 0, "running": 1, "total": 1, "status": "ok"},
  "tools_summary": {"available": 17, "missing": 0, "total": 17},
  "tools": {"subfinder": "available", "nmap": "missing"},
  "modes": {"default_mode": "mock", "supported_modes": ["mock", "real"],
            "real_scan_enabled": false, "real_scan_env": "GEF_ALLOW_REAL_SCAN"},
  "security": {"secret_key": "configured", "bind_host_default": "127.0.0.1",
               "debug_enabled": false, "session_cookie_httponly": true,
               "session_cookie_samesite": "Lax"}
}
```

| 字段 | 取值 | 含义 |
|---|---|---|
| `database` | `ok` / `missing` / `error` | `missing` = 应用库还没建（首次启动前正常） |
| `worker` | `ok` / `stale` / `missing` | 读心跳文件 `results/worker_heartbeat` 的 mtime；超过 30 秒算 `stale` |
| `queue.status` | `ok` / `backlog` | 队列是否堆积 |
| `tools` | 17 项 available/missing | 按 PATH 探测可执行文件 |
| `modes.real_scan_enabled` | 布尔 | 就是环境变量 `GEF_ALLOW_REAL_SCAN` |
| `security.secret_key` | `configured` / `ephemeral` | `ephemeral` = 进程级一次性密钥，重启需重新登录 |

**这是唯一的健康检查入口**。README 里 `curl /api/tools` 当健康检查的写法是错的。

---

## 6. 接口明细

### 6.1 认证（3 条，公开）

| 方法 | 路径 | 认证 | 请求 | 成功响应 | 主要错误 |
|---|---|---|---|---|---|
| POST | `/api/auth/login` | 无 | JSON/表单/请求头提供 `token` | 200 `{"ok":true,"authenticated":true}` | 401 `unauthenticated` |
| POST | `/api/auth/logout` | 无 | — | 200 `{"ok":true,"authenticated":false}`（幂等，未登录也 200） | — |
| GET | `/api/auth/session` | 无 | — | 200 `{"ok":true,"authenticated":bool,"auth_header":"X-Local-Token","token_is_ephemeral":bool}` | — |

> `token` 的取值优先级：JSON 体 → 表单 → `X-Local-Token` 请求头（`api/auth.py:40-44`）。

### 6.2 工具与数据库（2 条，**匿名**）

| 方法 | 路径 | 认证 | 请求 | 成功响应 | 主要错误 |
|---|---|---|---|---|---|
| GET | `/api/tools` | 匿名 | — | `{"tools":[{"name","tool_name","category","description","tool_group","tool_group_label","risk_level","risk_label","internet_allowed","default_enabled","reason","database"}],"groups":[...]}` | — |
| GET | `/api/databases` | 匿名 | — | `{"databases":[{"tool_name","table","result_column","category"}]}` —— **不含任何计数** | — |

> `/api/databases` 的 `category` 是 **`storage.TOOL_DATABASES` 里的「观测类别」**
> （`subdomain` / `url` / `web` / …），与 `/api/tools` 的 `tool_group`（能力分组）
> **同名不同义**，两者都不是方案 §9 示例里的那个 `category`；对照关系见本节下方
> 的逐字段表。想拿「记录数 / 域名数 / 最近扫描时间」的是
> `storage.ScanResultStore.get_tool_database_overview()` —— 它**当前没有任何 API 出口**
> （全仓只有测试直接调它），所以这里**没有** `record_count` 这类字段。
> 该方法的「无出口」现状同时写在 `api/tools.py` 的模块 docstring 里。

> **Tool Registry（下一阶段规划方案 §9）**：工具能力元数据的**唯一来源**是
> `core/tool_registry.py`；`/api/tools` 与 `/api/scan-center` 是它的**两个读出点**。
> `description` / `tool_group` / `tool_group_label` / `risk_*` /
> `internet_allowed` / `default_enabled` / `reason` 全部来自
> `core/tool_registry.py:ToolPolicy.to_dict()`，与 `/api/scan-center` 的
> `tools[]` 是**同一次调用的结果**（`test_tools_api_and_scan_center_agree_on_registry_fields`
> 逐字段比对）。
>
> **两个读出点不等价，不要互换**：方案第 9 节写的是「前端动态读取 `GET /api/tools`」，
> 实现走的是 `/api/scan-center`。原因是两者覆盖范围不同：
> `/api/tools` **匿名可读**，只给「工具清单 + 分组 + 数据库表信息」，
> 不含 `restricted_tools` / `projects` / `strategies` / `paces` / `limits`；
> `/api/scan-center` **需登录**，除工具清单外还带四步流程所需的全部元数据，
> 并把未接入的 `nuclei` 放进 `restricted_tools` 单独说明。
> 让前端改读 `/api/tools` 会同时丢掉受限工具说明与整个流程的元数据。
>
> * `name` 与 `tool_name` **恒等**：前者是历史键名（脚本在用），后者是注册表与
>   全仓统一键名；保留两个是不改历史契约、也不引入第二套命名。
>   **注意 `name` 只在本接口存在**，`/api/scan-center` 的 `tools[]` 只有 `tool_name`。
> * `category` 是**观测类别**（`subdomain` / `url` / `alive` / `web` / `port`，
>   由运行器自报、决定结果落哪张表），**不是**能力分组 —— 分组字段叫
>   `tool_group`。两者同名异义会在接口层面永远说不清，故字段名不共用。
>   ⚠️ **方案第 9 节示例里的 `category` 指的是能力分组**（`"category":"service"`），
>   对应本仓的 `tool_group`（`"service"`）；照方案字面读 `entry["category"]`
>   会拿到 `"subdomain"` 这类观测类别 —— **键存在、不报错、值是错的**，
>   而 `test_public_scan_mode.py` 里那条 `entry["category"] != entry["tool_group"]`
>   只锁住了「两者不同」，锁不住「谁对应方案的 `category`」。逐字段对照：
>   `方案 name → 本仓 tool_name`（`/api/tools` 另给 `name` 别名）、
>   `方案 category → 本仓 tool_group`、`方案 risk → 本仓 risk_level` + `risk_label`。
> * `groups` 供界面按能力分组渲染（`资产发现` / `服务识别` / …）；
>   它**只含已接入 runner 的工具**，`nuclei` 不在此接口出现 ——
>   本接口匿名可读，没必要把「还差哪些工具」一并公开。
>   因此从本接口渲染 `vuln` 栏只会看到空栏位；要如实显示「nuclei 存在但受限」，
>   必须用 `/api/scan-center` 的 `restricted_tools`。
> * 未登记的工具不抛异常，而是保守降级（`risk_level="high"`、
>   `internet_allowed=False`）：匿名只读列表接口为缺一个字段整页 500 更糟。
>
> 这两个接口的 docstring 示例键名（`table_name` / `record_count`）**与实现不符**，
> 实际以 `storage.get_tool_databases()` 为准：`tool_name` / `table` / `result_column` / `category`。

### 6.3 扫描执行（2 条，需认证，**同步阻塞**）

| 方法 | 路径 | 认证 |
|---|---|---|
| POST | `/api/run` | 需认证 |
| POST | `/api/tool/<tool_name>/run` | 需认证 |

请求体（`/api/run`）：

```json
{"domain": "example.test", "tools": ["subfinder", "dnsx"],
 "upload_id": "upload_xxx", "scope_id": "scope_xxx", "mode": "mock", "scenario": "success"}
```

* `domain` 与 `upload_id` 至少给一个；给 `file_path` 一律 **400**（已废弃，须先上传换 `upload_id`）。
* `scope_id` **必填**：缺失 → 400 `bad_request`；不存在或目标越界 → 403 `scope_violation`（整体拒绝）。
* `mode` 默认 `mock`；`real` 需要 `GEF_ALLOW_REAL_SCAN=true` **且** `scope.active_scan=true`。
* `tools`（或历史别名 `tool`）接受**字符串数组**或**逗号分隔字符串**，两者等价；
  逐项去空白、丢空项、**去重保序**（`tool_runner.normalize_tool_names()`）。
  > **口径校正**：这里此前写「全仓唯一一份实现」是**过头话** ——
  > `core/application.py:95 split_str_list()` 是另一份（服务对象是请求字段），
  > 且与它不是同一个函数（不去重、非法类型抛 `BadRequestError`）。端到端一致靠的是
  > **去重与 registry 校验只有一个收口点**（`load_tools`），不是实现唯一。
  > 见 `docs/CODEBASE_MAP.md` §9.30.4。
* **同时给了 `tools` 与别名 `tool` 时以 `tools` 为准**，且判据是「**键在不在**」而不是
  「值真不真」：`{"tools": [], "tool": "subfinder"}` 是**明确的空选择** → 400，
  不会拿别名去建任务。`/api/run`、`/api/jobs`、`/api/public-jobs` 三条链现在同口径
  （此前 `/api/jobs` 与 `/api/public-jobs` 用 `or` 折叠，会返回 202 并真的扫别名那个工具，
  见 `docs/CODEBASE_MAP.md` §9.30.1）。
* 工具名逐一过 `get_supported_runners()`，未登记者 → 400。
* **空 `tools`（`[]` / `""` / 不给）一律 400 `bad_request`**，绝不回落到
  `SCAN_CONFIG["enabled_runners"]`。回落等于「用户没选工具，系统自己挑一个去扫」——
  这条路径在 `docs/CODEBASE_MAP.md` BUG 索引第 7 条里记过，已修。
  回落语义只保留在 **CLI/Agent** 一侧（`load_tools(None)`，未指定 ≠ 明确不要）。
* `mock` 响应：200 `{"ok":true,"mode":"mock","scope_id","targets","tools","outcomes":[...]}`；
  `real` 响应：`tool_runner.run_tools` 的 report + `mode` + `scope_id`。

`/api/tool/<tool_name>/run` 只接受 `domain`（必填）/ `scope_id`（必填）/ `mode` / `scenario`，
未知工具名 → 400。

> ⚠️ 这两条是**历史同步入口**：真实扫描期间请求线程会被占住（进程超时默认 120 秒，
> `GEF_PROCESS_TIMEOUT` 可调）。新代码请用 `POST /api/jobs`。

### 6.4 任务 Job（10 条，需认证）

| 方法 | 路径 | 请求 | 成功响应 | 主要错误 |
|---|---|---|---|---|
| POST | `/api/jobs` | JSON 见下 | **202** `{"ok":true,"job_id","status":"queued","mode","total_steps","scope_id","pace","pace_label","reused"}` | 400 参/工具/目标数/pace；403 Scope 或真实扫描开关 |
| GET | `/api/jobs` | `?limit`（默认 50）、`?status` | `{"ok":true,"jobs":[...],"counts":{...}}` | 400 未知状态 |
| GET | `/api/jobs/<job_id>` | — | `{"ok":true,"job":{...,"pace","steps":[],"events":[]}}` | 404 `not_found` |
| GET | `/api/jobs/<job_id>/results` | `?scope_id`、`?observations_limit`（默认 500，上限 2000） | `{"ok":true,"job_id","mode","scope_id","counts","assets","services","technologies","risk_hints","notes"}` | 404 |
| GET | `/api/jobs/<job_id>/steps` | — | `{"ok":true,"job_id","steps":[...]}` | 404 |
| GET | `/api/jobs/<job_id>/events` | `?limit`（默认 200） | `{"ok":true,"job_id","events":[...]}` | 404 |
| GET | `/api/jobs/<job_id>/artifacts` | `?limit`（默认 100） | `{"ok":true,"job_id","artifacts":[{id,kind,size,sha256,...}]}`（**无 path**） | 404 |
| POST | `/api/jobs/<job_id>/cancel` | — | `{"ok":true,"job_id","status","cancel_requested"}` | 404 |
| POST | `/api/jobs/<job_id>/retry` | — | `{"ok":true,"job_id","status","attempt","next_attempt_at"}` | 400 不可重试；404 |
| GET | `/api/jobs/<before>/diff/<after>` | `?scope_id`、`?include_unchanged=0` | `{"ok":true,"added","removed","changed","unchanged","counts"}` | 404 任一任务不存在 |

`POST /api/jobs` 请求体：

```json
{"scope_id": "scope_xxx", "targets": ["example.test"], "tools": ["subfinder"],
 "mode": "mock", "scenario": "success", "idempotency_key": "my-key",
 "upload_id": "upload_xxx"}
```

要点（全部实测）：

* **202 而不是 200**：请求已接受但**尚未执行**。这里不执行任何工具。
* `targets` 支持数组或逗号分隔字符串；也能用 `upload_id` 追加目标；两者可叠加。
* `tools` 也可写作 `tool`；单个字符串会按逗号切分。任一工具名不在 `RUNNER_REGISTRY` → 400。
  **同时给了两者时以 `tools` 为准，且判据是「键在不在」**：`{"tools": [], "tool": "subfinder"}`
  → 400（明确的空选择），不会回落别名。见 §6.3 的同一段说明与
  `docs/CODEBASE_MAP.md` §9.30.1。
* 单任务目标数上限 `SCAN_LIMITS["max_targets_per_job"] = 20`（`config.py`，可由 `GEF_*` 调）→ 超限 400。
* `idempotency_key`（≤200 字符，非法直接 400 不静默截断）：同键任务**尚在 `queued`/`running`** 时
  重复提交 → 返回同一个 `job_id` 且 `reused=true`。语义是「防重复提交」，不是「永久只跑一次」。
* `scenario` 仅 `mock` 有效，取值 `success / empty / tool_not_found / timeout / non_zero_exit /
  parse_error / partial`，非法 → 400。
* `mode=real` 未开环境开关时实测：**403** `{"error_code":"scope_violation",
  "details":{"env":"GEF_ALLOW_REAL_SCAN"}}`。
* `mode=real` 且 Scope `active_scan=false` → 同样 403 `scope_violation`（第二道开关）。
* **编排位置**：以上全部校验与落库都在 `core/application.py:create_scan_job()` 里完成
  （P0-6 阶段一）。视图函数 `api/jobs.py:create_job` 只做「认证 + 解析 JSON + 拼响应」，
  首页表单走的是同一个入口。因此错误文案与判定顺序对两个入口**天然一致**。

`GET /api/jobs` 的 `status` 合法值 = `core/jobs.py:ALL_STATUSES`：
`queued / running / succeeded / partial / failed / timeout / cancelled / interrupted`。

`retry` 之后任务立刻回到 `queued`，但要等 `next_attempt_at` 之后 worker 才会领（5/10/20/40… 秒，
封顶 300 秒）——「排队中却迟迟不执行」通常是退避窗口未到。`MAX_ATTEMPTS = 5`。

`GET /api/jobs/<a>/diff/<b>` 的 `changed` 条目形如
`{"asset_id":"asset_xxx","changes":{"status_code":{"from":200,"to":403}}}`；只比较
`status_code / title / webserver / technologies / url` 这几个 **canonical key**
（`core/assets.py:ATTRIBUTE_ALIASES` 会把 `server` / `web_server` 归一到 `webserver`，
把 `technology` / `tech` 归一到 `technologies` —— 因为 `modules/httpx.py` 实际产出的就是
后者），时间戳这类易变字段被刻意排除。

`GET /api/jobs/<job_id>/results`（Phase 4「结果体验」新增）把一次任务的
`assets` + `observations` 整理成四段可直接展示的内容：

| 字段 | 内容 | 形状 |
|---|---|---|
| `assets` | 发现资产（按类型分组，带「谁发现的」） | `{total, by_type, type_labels, items, truncated}` |
| `services` | 服务（哪台主机上出现了什么协议 / 端口） | `{total, items, truncated}` |
| `technologies` | 技术栈（Web 服务器 / 组件 / CDN） | `{total, items, truncated}` |
| `risk_hints` | 风险提示 | `{total, items, truncated}` |
| `counts` | 五类**截断前**的真实数量 | `{assets, observations, services, technologies, risk_hints}` |
| `notes` | 必须原样展示给用户的说明句 | `[...]` |

> ⚠️ **`risk_hints` 不是漏洞结论**。本框架不做漏洞扫描：没有 `nuclei`（它在
> `core/tool_registry.py:KNOWN_UNAVAILABLE_TOOLS` 里，不在 `RUNNER_REGISTRY`），
> 全仓也没有任何 CVE / CVSS / 严重级别数据。因此：
>
> * 每一条 hint 都是从已有观测里读出来的**可观察事实**（明文 HTTP、5xx、
>   `Index of /` 标题、未做 HTTP 探测的主机……），`code` 是稳定的机器可读标识；
> * `level` 只有 `info` / `notice` / `attention` 三档，语义是「值不值得人工看一眼」，
>   **不是**危险度；中文文案在 `level_label` **由服务端下发**（前端不得写死）；
> * `notes` 里恒有一条免责说明：**「没有提示」不等于目标没有问题，「有提示」也不等于
>   发现了漏洞**。前端必须显示它（`tests/integration/test_job_results_api.py` 锁住）。
>
> 口径与 `/diff` 同源：**只看本次任务自己的观测**（`observations.job_id`），
> 不看该资产历史上被谁见过 —— 否则「这次扫到了什么」会被历史观测污染。
>
> 其它实测行为：`mode=mock` 的任务会带 `MOCK_NOTICE`（mock 不产生观测，
> 「服务 / 技术栈 / 风险提示」三段为空是预期行为，不是采集失败）；
> `observations_limit` 非法值回落到默认值而不 400；接口**只读**，
> 不写库、不写审计、不触发任何扫描。

### 6.5 原始证据（1 条，需认证）

| 方法 | 路径 | 请求 | 成功响应 | 主要错误 |
|---|---|---|---|---|
| GET | `/api/artifacts/<artifact_id>` | `?limit`（默认 64 KB，上限 1 MB） | `{"ok":true,"artifact":{content,truncated,sha256,...}}`（**无 path**） | 404 `not_found` |

内容做了**截断 + 凭据脱敏**；`truncated` 明确告诉你是否被截断。若 `truncated=false` 却内容异常短，
那是 bug 不是配置问题（历史上真出现过把证据按「命令预览」的 300 字符规则处理）。

### 6.6 资产与观测（4 条，需认证）

| 方法 | 路径 | 请求 | 成功响应 | 主要错误 |
|---|---|---|---|---|
| GET | `/api/assets` | `?scope_id&type&status&search&limit（默认100，上限1000）&offset` | `{"ok":true,"assets":[...],"total","limit","offset","types":[...]}` | 400 非法 `type`/`status` |
| GET | `/api/assets/summary` | `?scope_id` | `{"ok":true,"by_type":{...},"total":N,"scopes":N}` | — |
| GET | `/api/assets/<asset_id>` | `?observations_limit`（默认 50，上限 500） | `{"ok":true,"asset":{...,"observations":[...]}}` | 404 |
| GET | `/api/observations` | `?asset_id` **或** `?job_id`（二选一必填）、`?limit`（默认 200，上限 2000） | `{"ok":true,"observations":[...],"total":N}` | 400 两者都不给 |

`type` 合法值 = `core/canonical.py:ASSET_TYPES`：
`subdomain / host / ip / cidr / url / port / service`；`status` 合法值 = `active / stale / gone`。
`search` 做值子串匹配，`%` 与 `_` 已转义。

> `/api/observations` **拒绝无条件全表扫描**（不给 `asset_id`/`job_id` → 400
> `bad_request`，实测）。`/api/assets/summary` 不会被 `/api/assets/<asset_id>` 吃掉——
> Werkzeug 静态段优先，且有专门测试锁定。

### 6.7 结果查询与导出（5 条，**匿名**）

| 方法 | 路径 | 请求 | 成功响应 | 主要错误 |
|---|---|---|---|---|
| GET | `/api/results` | `?domain&tool&category&limit` | `{"results":[...]}`（**无 `ok` 信封**） | — |
| GET | `/api/tool/<tool_name>/results` | `?domain&limit` | `{"results":[...]}` | **400** `{"error":"不支持的工具数据库: xxx"}`（注意：这一条不走统一错误模型） |
| GET | `/api/export` | `?domain&tool&category&format（csv/json，默认 csv）&limit（默认1000，上限5000）` | `{"ok":true,"export_id","filename","format","row_count","size","sha256","created_at","download_url"}` | 非法 `format` → **500**（见下） |
| GET | `/api/export/<export_id>/download` | — | 文件字节流，`Content-Disposition: attachment` | 404 记录不存在或文件已被清理 |
| GET | `/api/exports` | `?limit` | `{"ok":true,"exports":[...]}`（同样**无 path**） | — |

> ⚠️ **`?format=xlsx` / `?format=TXT` 返回 500 `unknown_error`，不是 400。**
> `api/results.py:export_data` 没有捕获 `exporter.export_results` 抛出的 `ValueError`
> （`exporter.py:128`），实测已复现。调用方只能传 `csv` 或 `json`。
> 另注：`category` 参数在旧库的专属表分支里**被静默忽略**（`storage.get_tool_results`），
> `/api/results?category=web` 对已注册工具不生效。

### 6.8 上传（1 条，需认证）

| 方法 | 路径 | 请求 | 成功响应 | 主要错误 |
|---|---|---|---|---|
| POST | `/api/upload` | `multipart/form-data`，字段名 `file` | `{"ok":true,"upload_id","target_count","targets_preview","label"}`（**无路径**） | 400 空文件/无有效目标/超限 |

* 支持 `.txt` / `.csv` / `.xlsx` / `.json`；大小上限 `MAX_UPLOAD_SIZE = 2 MB`。
* 返回的 `upload_id` 是**文件目标的唯一入口**：`/api/run` 与 `/api/jobs` 都只接受 `upload_id`，
  传 `file_path` 一律拒绝。

### 6.9 配置（4 条，需认证）

| 方法 | 路径 | 请求 | 成功响应 | 主要错误 |
|---|---|---|---|---|
| GET | `/api/settings` | — | `{"ok":true,"settings":{"llm":{...},"search":{...}}}`（Key 已掩码） | 500 |
| POST | `/api/settings` | JSON 白名单字段 | `{"ok":true,"message","updated_fields","backup"}` | 400 空体/无可更新字段；500 |
| GET | `/api/settings/enscan` | — | `{"ok":true,"config_path","cookies":{"aqc","tyc","icp"}}`（掩码） | 500 |
| POST | `/api/settings/enscan` | JSON `enscan_{aqc,tyc,icp}_cookie` | `{"ok":true,"config_path","message"}` | 400 无有效 Cookie；500 |

> `POST /api/settings` 写 `.env`：**原子写**（临时文件 + fsync + `os.replace`）+ 写前备份 +
> 审计（只记字段名，不记值）。`updated_fields` 是实际发生变化的字段。
> `GET /api/settings/enscan` 的 `config_path` 是**唯一**会回显服务端路径的字段，
> 且它需要管理员身份——这是既有的有意设计，不是路径泄露。

### 6.10 授权测试项目与公网任务（7 条，需认证）

公网授权测试模式的入口。设计要点：**项目是授权证据的组织单位，不是权限开关** ——
关联项目**不会**放宽任何限制，能不能真实扫描仍由 Scope 的 `active_scan` 与环境开关决定。

| 方法 | 路径 | 请求 | 成功响应 | 主要错误 |
|---|---|---|---|---|
| POST | `/api/projects` | JSON `{name, authorization_note, owner?, scope_ids?}` | **201** `{"ok":true,"project":{...}}` | 400 `name`/`authorization_note` 缺失或过短；400 `scope_ids` 里有不存在的 Scope |
| GET | `/api/projects` | — | `{"ok":true,"projects":[{...,"scope_ids","scope_count"}]}` | — |
| GET | `/api/projects/<project_id>` | — | `{"ok":true,"project":{...}}` | 404 `not_found` |
| POST | `/api/projects/<project_id>/scopes` | JSON `{scope_id}` | **201** `{"ok":true,"project":{...}}`（幂等） | 400 缺 `scope_id` 或 Scope 不存在；404 项目不存在 |
| POST | `/api/public-jobs/check` | JSON `{targets, project_id?}` | **200** `{"ok":true,"checks":[{raw,normalized,kind,valid,error_message,matches,eligible_scope_ids,candidates,real_scan_enabled,resolved_check_deferred,ready,blocker}],"summary":{"total","ready","blocked"},"suggested_mode","project_id"}` | 400 缺 `targets` / 请求体不是 JSON 对象 |
| POST | `/api/public-jobs` | JSON `{project_id, scope_id, targets?, upload_id?, strategy?, tools?, pace?, mode?, operator?, authorization_confirmed?, rate_limit?, timeout_seconds?, idempotency_key?}` | **202** `{"ok":true,"job_id","status":"queued","mode","strategy","project_id","project_name","scope_id","total_steps","pace","pace_label","operator","authorization_confirmed","limits","authorized_public":true,"reused"}` | 400 未关联 Scope / 工具被禁 / 未登记工具 / 模板与工具不符 / 目标数超限 / `pace` 非法 / `operator` 非法 / `rate_limit` / `timeout_seconds` 越界 / **`tools` 与别名 `tool` 同时给出且 `tools` 为空**；403 越界或真实扫描开关未开；404 项目不存在 |
| GET | `/api/scan-center` | — | `{"ok":true,"projects","strategies","paces","tools","tool_groups","restricted_tools","limits","internet_allowed_tools"}` | 401 |

`strategy` 合法值 = `core/tool_registry.py:STRATEGIES`：`asset_discovery`（默认，= `subfinder` + `httpx`）、
`web_fingerprint`（= `httpx`，`nuclei` 只作「受限未开放」展示）、`custom`（**必须**显式给 `tools`）。

**`POST /api/public-jobs/check` 是只读试算**（规划方案第 6 节「自动判断」的服务端半边）：

* **不创建任务、不写库、不发网络请求、不写审计** —— 它是查询，有用例断言连调后
  任务数 / 审计事件数一字不变；
* `eligible_scope_ids` 是**唯一**的放行结论，只含 `verdict == allowed` 的范围。
  `matches` 还会带上「命中排除列表」的范围（`verdict = "excluded"`）—— 那是**诊断信息**，
  用户需要知道「是你自己的排除列表挡住了」，但它**不构成授权**，所以**不进** `eligible_scope_ids`；
* 前端的「自动匹配授权资产」（`scan_center.js:applyMatchedScope`）**直接吃这个字段**，
  绝不自己比对 `allowed_domains` / `active_scan`。多个目标命中不同资产时它会**保持
  用户当前选择并如实说明**，不替他猜一个；
* `blocker` 五档：`no_scope`（一个范围都没建）/ `not_authorized`（没覆盖）/
  `scope_inactive`（覆盖但没开真实扫描）/ `env_disabled`（环境开关没开）/
  `invalid_target`（格式非法）；空串表示可以扫。`resolved_check_deferred` 恒为 `true`：
  执行期还会做一次真实 DNS 解析校验，本接口**如实声明**它没做。

**Scan Profile = 工具组合 + 节奏**（`core/pace.py`）。`pace` 合法值恰好两个：

| 值 | 标签 | 行为 |
|---|---|---|
| `light` | 低频 | 覆盖工具的并发与每秒请求数（`subfinder -t 5 -rl 3`、`httpx -threads 5 -rl 10`），并在**真实**步骤之间留出间隔（默认 1.5 秒，见 `GEF_PACE_LIGHT_STEP_DELAY_SEC`） |
| `normal` | 常规 | 完全沿用工具自身配置，不额外等待 —— **与引入 Scan Profile 之前逐字节一致** |

> **`pace` 只能收紧，不能放松**：最终档位 = `resolve_pace(模板档位, 请求档位)`，
> 任一为 `light` 即 `light`。三个策略模板当前都是 `light`，因此请求体里写
> `pace=normal` **改不回来**（`test_public_job_request_cannot_relax_the_template_pace` 锁住）。
> 非法值（如 `"low"`）返回 400 而不是静默回退 —— 拼错的档位拿到常规档是最危险的错法。
> 三个模板的档位与说明随 `GET /api/scan-center` 的 `strategies[].pace` / `pace_label`
> 与 `paces[]` 一起下发，前端不写死任何文案。
>
> **节奏不是安全闸门**：它不参与、也不放松 Scope / `active_scan` / `GEF_ALLOW_REAL_SCAN` /
> 公网白名单中的任何一条。它只回答「这一步等多长、用多低的并发」。
>
> **`pace` 不落成 `jobs` 表的新列**（那属于数据库结构改动，DECISIONS §1 E 限纯增量）。
> 它写进 `job.created` 事件的 `detail` 与审计 detail，执行期由
> `core/jobs.py:pace_of_job(job_id)` 读回 —— worker 是**独立进程**，且任务可能被 retry
> 或换一个 worker 重启，节奏必须是任务自身的属性而不是某次调用的参数。

### 6.3 Phase 3：操作者 / 授权备注 / 限速 / 超时（规划方案第 14 节）

四个新请求字段，**全部只被记录或只被收紧，不构成任何权限判定**：

| 字段 | 取值范围 | 落点 | 语义 |
|---|---|---|---|
| `operator` | 字符串，≤ 120 字符；缺省/空 → `local-admin` | `job.created` detail + 审计 detail + 结构化日志 | **自称**，不是已验证身份（本仓无身份体系）。非法形状（`true` / 数组 / 对象）→ 400 `details.field = "operator"` |
| `authorization_confirmed` | 布尔；不给 → `null` | `job.created` detail | 页面上的授权确认复选框。**刻意不是闸门** —— 授权由 Scope / Policy / 环境开关判定 |
| `rate_limit` | 整数 `1 ~ 100` | `job.created` detail + 执行期 `Runner.config["rate_limit"]` | 每秒请求上限。**只能收紧** |
| `timeout_seconds` | 整数 `1 ~ SCAN_LIMITS["process_timeout"]`（本机默认 120） | 同上，映射为 `Runner.config["process_timeout"]` | 单步超时秒数。**只能收紧** |

`limits` 出参恒有两个键（`{"rate_limit": null, "timeout_seconds": null}` 表示没指定），
`GET /api/scan-center` 的 `limits` 给出可填范围与中文说明（前端据此生成输入框，
不写死上下界）。

> **`rate_limit` / `timeout_seconds` 只能收紧，不能放松**：合并规则是 `min` 而不是覆盖
> （`core/job_limits.py:apply_to_runner`），且发生在 `pace` **之后** —— 因此低频档已经压下来的
> 速率（`httpx -rl 10`）不会被请求里的 `rate_limit=50` 顶回去。
> 越界一律 **400**，不静默夹到边界：写了 `100000` 却拿到 `100`，与拼错的档位拿到常规档
> 是同一种危险错法。`details.field` 指到具体那一项。
>
> ⚠️ **两项的「生效面」差得很远，必须分开说**（实测，2026-10-03）：
>
> | 字段 | 真的变成命令行参数的 runner | 覆盖率 |
> |---|---|---|
> | `timeout_seconds` | 全部 | **17 / 17** —— 唯一读取点是 `modules/base.py:425 _timeout_seconds()`，所有 runner 都从它取超时 |
> | `rate_limit` | `modules/subfinder.py:64`、`modules/httpx.py:212` | **2 / 17** —— 只有这两个 runner 的 `build_command()` 会追加 `-rl` |
>
> 关键在于：**公网白名单恰好就是这两个**（`{httpx, subfinder}`），所以公网授权测试
> 这条链上 `rate_limit` 是 **2/2 全覆盖**的。但白名单**之外**的 15 个 runner 拿到
> `rate_limit` 后是**静默 no-op**：`apply_to_runner` 会如实把值写进 `Runner.config`
> 并返回 `True`，`build_command()` 里却没有任何对应参数 —— 从调用方看不出它没生效。
> 老入口 `POST /api/jobs` 的 real 模式**可以**走到这些工具（见上一条 ⚠️），
> 因此「给 nmap 设了 `rate_limit=5`」这件事目前只改了库里的记录，没有改命令。
> 本轮**未改**这个覆盖面（给 15 个 runner 各加一个限速参数是独立工作，且部分工具
> 根本没有对应开关）；如实记在这里，避免把「记录了」读成「限速了」。
> 详见 `docs/CODEBASE_MAP.md` §9.30.5。
>
> **这四项同样不落成 `jobs` 表的列**：写在同一条 `job.created` 事件 detail 里，
> 由 `operator_of_job` / `project_id_of_job` / `strategy_of_job` / `authorization_of_job` /
> `limits_of_job` 读回，`GET /api/jobs/<id>` 把它们平铺在 `job` 上
> （`operator` / `strategy` / `project_id` / `authorization` / `limits`）。
> 有一条反向守卫测试锁着「`jobs` 表里不得出现这六个列名」。
>
> **`authorization_note` 是项目授权说明的**快照**：项目说明事后被改，历史任务的授权依据
> 仍是创建当时那一份原文（`job.authorization.note`）。
>
> **老入口 `POST /api/jobs` 也接受 `operator` / `rate_limit` / `timeout_seconds`**，
> 口径完全相同；它的响应形状**未变**（不出现 `project_id` 等公网专属字段）。
>
> ⚠️ **老入口不做公网白名单判定**（实测，2026-10-03）：`assert_tools_internet_allowed()`
> 在本仓**只有一个生产调用点** —— `core/application.py:573`（公网编排
> `create_authorized_public_job`）。因此 `POST /api/jobs` 用 `tools=["nmap"]` +
> `mode="real"`（环境开关开 + `active_scan=True`）会返回 **202 并落库**，
> 而同样参数打 `POST /api/public-jobs` 是 **400 + `blocked_tools`**。
> 这是**当前的有意现状**（老入口定位为内网联调入口，白名单只在公网入口生效），
> 不是漏洞修复遗漏；要不要一并收口属产品口径问题，已登记在
> `docs/DECISIONS.md` §3.11.5 第 1 条等你拍板。
>
> **执行期会再复检一次**（`jobs/executor.py`）：每个 real 步骤在调用 Runner **之前**，
> 除 Scope 成员资格外还会重读 `GEF_ALLOW_REAL_SCAN` 与 `Scope.active_scan`。
> 任一不满足时该步骤以 `error_code=scope_violation` 失败、Runner **不会被调用**
> （顺序、错误码与「步骤级 `scope_violation` 不等于任务级」三条判断见
> `docs/CODEBASE_MAP.md` §9.29）。也就是说：任务排队期间开关被关掉或范围被收紧，
> 那些步骤不会真的发出去。

> **公网白名单恰好是 `{httpx, subfinder}`**（方案第 8 节），是代码常量而非运行期配置。
> 核心不变量是「**没登记 = 禁止公网**」：`assert_tools_internet_allowed()` 对未登记工具
> **直接拒绝**而不是默认放行。被禁工具的报错体里带 `blocked_tools`（含 `reason` 与
> `risk_level`）与 `internet_allowed_tools`，调用方能直接看出该换成什么。
>
> **`mode` 默认是 `real` 且不会静默降级**：环境开关没开时返回 403，而不是退回 mock 给一份假数据。
> 「以为打了真实目标、其实拿到编的数据」比直接报错危险得多。演练请显式传 `mode=mock`。
>
> 本接口**不创建 Scope** —— 那仍然只有 `POST /api/scopes` 一处。视图层不做任何闸门判断，
> 全部转交 `core/application.py:create_authorized_public_job`（该函数再转交 `create_scan_job`），
> 有源码守卫测试锁住「禁止 Web → Runner」与「不得另写第二条 Policy 判定」。

---

## 7. 本文的核对方法

```powershell
# 在 get_everything_framework/ 下，项目根已配 pythonpath=["."]
python -c "import app; [print(sorted(r.methods - {'HEAD','OPTIONS'}), r.rule) for r in app.app.url_map.iter_rules()]"
```

统计口径（实测）：`len(list(app.app.url_map.iter_rules())) == 48`；含方法展开的绑定 50
（`/` 与 `/login` 各 2 个方法）；其中 `/api/*` 为 **42** 条（需认证 32 / 匿名 7 / 登录相关 3）。

匿名/需认证的划分以**实测响应码**为准（用 `app.test_client()` 逐个匿名请求），
并与 `tests/integration/test_api_auth_contract.py` 的
`ANONYMOUS_READABLE` / `ADMIN_ONLY` 两份清单交叉验证。

## 8. 文档与代码不一致（API 相关）

1. **README 说「12 个 RESTful 接口」**：实际 `/api/*` 为 **42** 条。
2. **README 的匿名只读清单只有 3 条**：实际 7 条（多出 `/api/databases`、`/api/exports`、
   `/api/tool/<n>/results`、`/api/export/<id>/download`）。
3. **README 提议用 `curl /api/tools` 做健康检查**：健康检查是 `GET /health`，README 全文
   只出现过 1 次 `/health` 字样且不在 API 章节。
4. `api/tools.py` 的 docstring 键名（`table_name` / `record_count`）与实现
   （`table` / `result_column` / `category`）不符。
5. `api/scopes.py:110` 注释称 404 会转成 `error_code=bad_request`，实测为 `not_found`
   （注释与 `core/errors_handlers.py` 映射表不一致，映射表是对的）。
6. `api/scan.py` 的 docstring 举例工具名 `nuclei`——`RUNNER_REGISTRY` 里没有 `nuclei`
   （`api/tools.py`、`api/results.py` 的示例里也有同样的错）。实际 17 个工具见 `/api/tools`。
7. `GET /api/export?format=<非法值>` 返回 **500**，接口文档期望的是 400：
   `api/results.py` 未捕获 `exporter.py` 的 `ValueError`，属真实缺陷（`docs/CODEBASE_MAP.md` §7.1 第 5 条）。
8. `GET /api/tool/<n>/results` 的错误响应是 `{"error": "..."}` 而**不是**统一错误信封，
   是为兼容旧脚本保留的例外，容易被误当成 `ok=false` 缺失。
