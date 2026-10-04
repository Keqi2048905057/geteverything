# 安全策略

## 支持范围

当前处于**本机联调阶段**，只支持在单台开发机上以 `127.0.0.1` 绑定方式运行。
不提供公网部署支持，公网暴露导致的问题不在处理范围内。

## 报告安全问题

请**不要**通过公开 Issue 提交安全漏洞。请使用 GitHub 的私密漏洞报告功能：

1. 打开本仓库的 **Security** 标签页；
2. 选择 **Report a vulnerability**；
3. 在报告中说明：受影响版本/提交、复现步骤、影响面、你建议的修复方向。

如果你在 fork 上工作，也可以直接联系仓库所有者。

## 已知的、正在处理的安全问题

立项时的安全基线缺口大部分已在 M1–M4 修掉。下表按**当前代码的真实状态**维护，
未修复的项请勿视为已缓解：**不要**把本服务暴露到 `127.0.0.1` 之外。

已修复：

| 已修复 | 问题 | 修复方式 |
|---|---|---|
| M1 | 首页 `TemplateNotFound`（`web/templates` 曾被删） | 补回模板与静态资源 |
| M1 | `app.run(debug=True)` 未关闭 | 改为 waitress，`WEB_DEBUG` 默认 false |
| M2 | 所有 API 无认证，`/api/settings` 可匿名写 `.env` | 本地管理员 Token + HttpOnly Session（`X-Local-Token`） |
| M2 | `SECRET_KEY` 有固定默认值 `dev-secret-key` | 移除默认值，缺失/过弱则生成进程级一次性密钥并告警 |
| M2 | `/api/run` 接受任意 `file_path` 并直接打开 | 只接受受控上传 ID，禁止任意绝对路径 |
| M2 | 默认目标为外部域名 `nfl.com` | 移除外部默认目标，需显式指定且过 Scope 校验 |
| M2 | 上传文件未使用受控 ID 与 UUID 存储 | 受控 upload id + 独立目录 + 大小/类型校验 |
| M3 | 扫描在 Flask 请求线程内同步阻塞 | 改为异步任务队列 + 独立 worker 进程 |
| M4 | 工具失败返回 `[]`，与“无结果”不可区分 | 统一 `RunnerResult`，失败/超时/零结果分开记录 |
| M4 | 工具无超时、命令预览泄露 API Key | 强制超时（含进程树终止）+ 命令预览脱敏 |
| P0-2 | Scope 判定散落在各 API，执行期不再复检 | 统一 `core/policy.py` 四个入口：Job 创建 / Step 执行前 / 解析后地址 / 重定向 |
| P0-3 | Agent 仍接受任意 `file_path` 并直接交给编排层 | Agent 只接受受控 `upload_id`，任意路径直接拒绝 |
| P0-5 | `/api/export` 返回服务器文件路径，泄露本机目录结构 | 改为 `export_id` + 下载路由；导出记录入 `exports` 表可追溯 |
| P0-5 | 导出文件名前缀可由请求参数拼接，存在 `../` 穿越写盘风险 | `exporter.py:safe_prefix()` 白名单字符 + 折叠 `..` + 长度上限，并有参数化测试 |
| P0-7 | 任务状态机缺少显式跃迁表，`queued` 可被直接写成终态 | `core/jobs.py:ALLOWED_TRANSITIONS` + `can_transition()`，`finish_job` 拒绝非法跃迁 |
| P0-7 | 任务可被无限次 retry，队列会被刷爆 | `MAX_ATTEMPTS = 5`，超限 retry 返回 400 |
| DECISIONS-I | 旧结果库 `with conn` 只提交不关闭，句柄持续泄漏 | `storage.py:_connect()` 显式关闭 + 连接级 `busy_timeout`；`core/health.py` 同样显式关闭 |

仍待处理：

| 状态 | 问题 |
|---|---|
| 已知项（`docs/DECISIONS.md` D） | `/api/tools`、`/api/databases`、`/api/results`、`/api/tool/<n>/results`、`/api/export`、`/api/export/<id>/download`、`/api/exports` 共 **7 个**仍可**匿名读取**。这是**有意保持的现状**（不破坏本机脚本兼容性），已在 `tests/integration/test_api_auth_contract.py` 与 `test_export_contract.py` 用测试锁定该契约；后续若要收口鉴权需用户明确授权。<br>**口径提醒**：`test_api_auth_contract.py` 的 `ANONYMOUS_READABLE` 目前只列了其中 **5 条**，`/api/tool/<n>/results` 与 `/api/export/<id>/download` 只有间接覆盖 —— 见 [`docs/TEST_REPORT.md`](docs/TEST_REPORT.md) §3.1 |
| 待修复 | `storage.py` 旧结果库**仍无 WAL**（已加连接级 `busy_timeout=5000`，但 WAL 需重建库文件，属迁移范畴，未在无人值守期间执行） |
| 待修复 | `scripts/*.exe` 等工具二进制不进仓库，需自行按 `README.md` 准备，缺失时报 `tool_not_found` |
| 已缓解（P0-6 阶段一） | 创建扫描任务的编排已收拢到**唯一入口** `core/application.py:create_scan_job()`（`POST /api/jobs` 与首页表单共用），视图函数不再内联 Policy/落库/审计，也不再有第二份 Scope 判定；三条源码守卫（`tests/unit/test_application_service.py`）阻止其退化 |
| 已修复（2026-10-04） | **匿名 `POST /` 的 `action=chat` 曾可达真实执行**：`app.py:index()` 的认证守卫原在 `if action in _SCAN_ACTIONS:` **内部**，`action=chat` 分支无认证，而 Agent 会直接调 `run_tools` / `HttpxRunner.run_scan`（绕过 `GEF_ALLOW_REAL_SCAN` 与 Scope）。已把守卫提到 `action` 分支之前，**所有 POST 动作一律需登录**；同一轮修掉**匿名首页下发整份授权资产清单**（范围名 + `allowed_domains` + `allowed_cidrs` + 状态）。实测证据与回归用例见 `docs/CODEBASE_MAP.md` §9.33 与 `docs/TEST_REPORT.md` §15 |
| 已修复（2026-10-04，第二轮） | **匿名首页下发扫描汇总与目标明细**：`app.py:build_page_context()` 当时**没有**登录态判断就调 `get_global_summary()` / `get_results_by_domain()` / `get_domain_summary()`，模板「汇总」面板也没有 `is_authenticated` 守卫 —— 匿名访客能读到「跑过几次 / 覆盖几个目标 / 命中多少条」，`GET /?domain=<目标>` 还会回显目标名。已补齐到与 `scopes` / `recent_jobs` **同一口径**（详见 `docs/DECISIONS.md` §3.16.1）。**同轮还修掉导出结果行重复计数**（`row_count` 与 CSV 都重复，§3.16.2） |
| 未完成（P0-6 阶段二，用户已授权 · 已明确「先不开工」） | Agent 层已禁止任意 `file_path`（只能引用受控 `upload_id`），但**仍直接调用 `run_tools` / runner，未改走 Job Service**——即 Agent 提议与执行尚未彻底分离。**注意：2026-10-04 只堵住了「匿名」这个入口，Agent 内部的绕过行为原样保留。** 授权见 `docs/DECISIONS.md` §3.2；开工前需先确认「Agent 执行异步化」的影响（详见 `docs/CODEBASE_MAP.md` §9.19.5） |

> 已修复（曾登记在本表）：`GET /api/export?format=<非 csv/json>` 原返回 **500 `unknown_error`**，
> 现为 **400 `bad_request`** + `details.supported`（校验清单与 `exporter.SUPPORTED_FORMATS` 同源）。

> **Phase 4 新增接口的安全口径**：`GET /api/jobs/<job_id>/results` **需管理员身份**
> （已登记进 `test_api_auth_contract.py:ADMIN_ONLY`），且**只读**——它从既有的
> `assets` / `observations` / `job_steps` 行派生结果，不写库、不写审计、不发任何网络请求、
> 不触发任何扫描（`tests/integration/test_job_results_api.py::test_results_endpoint_is_read_only`
> 断言连调两次后四类行数全部不变），响应里不含服务器路径。
> **它不是漏洞扫描结论**：本框架未接入任何漏洞扫描器（`nuclei` 不在 `RUNNER_REGISTRY`），
> 页面上的「风险提示」是**可观察事实**（明文 HTTP、5xx、目录列表标题、未探测主机…），
> 级别只有 `info` / `notice` / `attention` 三档，且每次都带免责说明。
> **请勿把这一节展示给第三方时省略那段说明** —— 「没有提示」不等于目标没有问题。

## 使用约定

- 默认只跑 `mode=mock`，或对 `127.0.0.1` fixture server 做验证；
- 真实外部扫描必须显式开启（`GEF_ALLOW_REAL_SCAN=true`），且目标必须落在已创建且
  `active_scan=true` 的 Scope 内，否则返回 403 `scope_violation`；
- `.env`、`results/`、`uploads/`、`exports/`、`*.db` 一律不得提交进 Git；
- 发现密钥已进入 Git 历史时，先轮换密钥，再处理历史。

## 部署前的安全自检

`python scripts/check_env.py` 在装完依赖、还没启动服务时就能跑，会直接点出
`SECRET_KEY` 是否仍是弱值/未配置、`LOCAL_ADMIN_TOKEN` 是否为空、`WEB_DEBUG` 是否被打开
（这一项判为**阻塞**，因为它会暴露调试器）、`WEB_HOST` 是否绑到了非回环地址、
以及真实扫描总开关是否被打开。

它**只读且不泄密**：不写文件、不建库、不发网络请求、不执行任何扫描工具；
报告里只出现「已配置 / 未配置 / 为弱值」这类状态串，**不含任何密钥的值**。
这两点由 `tests/unit/test_check_env.py` 用哨兵串与目录快照锁定。

> 注意它**不是**鉴权层的一部分，也不改变任何既有安全行为 —— 只是把「配置错了」提前暴露出来。
