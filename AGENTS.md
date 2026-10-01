# get_everything_framework — 工作约定

> 完整地图：`docs/CODEBASE_MAP.md`（1800+ 行，含 27 条「症状 → 排查位置」索引表）
> 配套技能：`.dsh/skills/codebase-map/`、`.dsh/skills/bug-triage/`。这两个是标准 `SKILL.md` 目录 bundle，但本 profile 里 `@deepseek-ai/dsh-skill-filesystem` 与 `@deepseek-ai/dsh-tool-skill` 处于停用状态，需在「设置 → 插件」启用后才会出现在技能目录中；在此之前直接读这两个目录下的 Markdown 即可，内容自足。

## 修任何 bug 之前

1. 先查 `docs/CODEBASE_MAP.md` 第 6 节「BUG 定位索引表」。症状能对上 → 直接去它给出的 `文件:函数` 验证，**不要**从 `app.py` 重新通读。
2. 把范围压到一层：路由（`app.py`、`api/`）/ 编排（`tool_runner.py`）/ 适配器（`modules/`）/ 存储（`storage.py`）/ agent（`agent/`）。只在这一层查。
3. 造一个**秒级、确定、不发真实外网目标**的反馈循环，再动代码。现成探针代码见 `.dsh/skills/bug-triage/references/triage-playbook.md`。

## 项目根与命令

- 项目根：`get_everything_framework/`（相对本文件）；Git 仓库根是本文件所在目录。
- 测试：在项目根执行 `python -m pytest -q`（`pyproject.toml` 已配 `pythonpath = ["."]`，测试内可直接 `import storage`）
- 静态检查：`ruff check .`；类型检查：`mypy app.py core api jobs storage.py modules`
- 基线：pytest **838 项通过 / 2 skipped**，ruff 全过，mypy 0 error（60 source files）。
  基线会随每轮推进变化，**以 `PROJECT_STATE.md` 的「最近一次验证」为准**（本行容易过期）。

## 硬约束

- 不扫描任何未授权的外部目标；默认只用 mock runner 与 `127.0.0.1`。
- 不执行 `git reset --hard` / `git clean -fd`；不覆盖 `get_everything_framework/scripts/OneForAll.exe` 的未提交差异（README 与 PROJECT_STATE 里仍写着这条；该文件受 `*.exe` 规则忽略，历史上确曾在旧 clone 里存在过未提交差异，保留这条约束作为防御）。
- 数据库、扫描结果、上传样本、密钥不得提交进 Git。
- `results/scan_results.db` 只读；复现用临时库 `ScanResultStore(db_path=...)`。

## 五个高频坑（先排除这五个）

1. **静默空结果**：工具失败/超时/未安装都被 `modules/base.py:_execute` 降级为 `return False` → Runner `return []`，与「跑通但空结果」不可区分。先确认可执行文件存在。
   ▶ **M4 起已可区分**：Runner 层统一返回 `RunnerResult` 带 `error_code`（`tool_not_found` / `timeout` /
   `parse_error` / `no_results` …）。但**旧同步链 `tool_runner.py` 仍只看 `bool`**，在那条链上本坑依然成立。
2. **残留输出文件**：`results/` 里按 `md5(domain)[:12]_<tool>.txt` 命名的旧输出会被 `_read_results` 当成本次结果 —— 复现前先删产物。
   ▶ **M4 起已在执行前主动清理**（`test_run_removes_stale_output_file_before_execution`），但手工比对时仍应先看时间戳。
3. ~~**`GET /` 必然 500**~~ ▶ **M1 已修**：`web/templates/{index.html,login.html,assets.html}` 与
   `web/static/{app.css,app.js,assets.js}` 都已随仓库提供，`GET /` 返回 200。
   若你手上是**旧 clone**，那里 `web/templates/` 在历史提交 `5853752` 被删过，本坑只在旧 clone 上成立。
4. **Agent 运行时不调用大模型**：`agent/client.py`、`agent/providers/*` 全部无调用方，规划由 `agent/intent.py:analyze_intent` 正则 + `agent/planner.py:build_plan` 模板决定。凡「模型超时/返回格式错」类症状在当前路径不可达。
5. **SQLite 并发**：**新库**（`core/db.py` → `results/local.db`）已有 WAL + `busy_timeout=5000` +
   `BEGIN IMMEDIATE`，并用 8 线程真机用例验证过（`tests/unit/test_db_concurrency.py`）。
   **旧库**（`storage.py` → `results/scan_results.db`）只加了连接级 `busy_timeout`，**仍无 WAL**；
   且 `with conn` 只提交不关闭的老毛病已用 `storage.py:_connect()` 修掉。
   在旧库上做并发写仍可能 `database is locked`（WAL 属迁移范畴，未动）。

## 代码改动后

跨层改动（`api/`、`modules/`、`storage.py`、`agent/` 中涉及两层以上）后，刷新 `docs/CODEBASE_MAP.md` 的对应章节并更新其 `last-mapped`；发现新的坑回填第 7 节。
