# get_everything_framework — 工作约定

> 完整地图：`docs/CODEBASE_MAP.md`（560 行，含 22 条「症状 → 排查位置」索引表）
> 配套技能：`.dsh/skills/codebase-map/`、`.dsh/skills/bug-triage/`。这两个是标准 `SKILL.md` 目录 bundle，但本 profile 里 `@deepseek-ai/dsh-skill-filesystem` 与 `@deepseek-ai/dsh-tool-skill` 处于停用状态，需在「设置 → 插件」启用后才会出现在技能目录中；在此之前直接读这两个目录下的 Markdown 即可，内容自足。

## 修任何 bug 之前

1. 先查 `docs/CODEBASE_MAP.md` 第 6 节「BUG 定位索引表」。症状能对上 → 直接去它给出的 `文件:函数` 验证，**不要**从 `app.py` 重新通读。
2. 把范围压到一层：路由（`app.py`、`api/`）/ 编排（`tool_runner.py`）/ 适配器（`modules/`）/ 存储（`storage.py`）/ agent（`agent/`）。只在这一层查。
3. 造一个**秒级、确定、不发真实外网目标**的反馈循环，再动代码。现成探针代码见 `.dsh/skills/bug-triage/references/triage-playbook.md`。

## 项目根与命令

- 项目根：`get_everything_framework/`（相对本文件）；Git 仓库根是本文件所在目录。
- 测试：在项目根执行 `python -m pytest -q`（`pyproject.toml` 已配 `pythonpath = ["."]`，测试内可直接 `import storage`）
- 静态检查：`ruff check .`
- 基线：pytest 22 项通过，ruff 全过。

## 硬约束

- 不扫描任何未授权的外部目标；默认只用 mock runner 与 `127.0.0.1`。
- 不执行 `git reset --hard` / `git clean -fd`；不覆盖 `get_everything_framework/scripts/OneForAll.exe` 的未提交差异。
- 数据库、扫描结果、上传样本、密钥不得提交进 Git。
- `results/scan_results.db` 只读；复现用临时库 `ScanResultStore(db_path=...)`。

## 五个高频坑（先排除这五个）

1. **静默空结果**：工具失败/超时/未安装都被 `modules/base.py:_execute` 降级为 `return False` → Runner `return []`，与「跑通但空结果」不可区分。先确认可执行文件存在。
2. **残留输出文件**：`results/` 里按 `md5(domain)[:12]_<tool>.txt` 命名的旧输出会被 `_read_results` 当成本次结果 —— 复现前先删产物。
3. **`GET /` 必然 500**：`app.py` 指向的 `web/templates` 目录在历史提交 `5853752` 已被删除。这是已知状态，不是 bug。
4. **Agent 运行时不调用大模型**：`agent/client.py`、`agent/providers/*` 全部无调用方，规划由 `agent/intent.py:analyze_intent` 正则 + `agent/planner.py:build_plan` 模板决定。凡「模型超时/返回格式错」类症状在当前路径不可达。
5. **SQLite 并发**：`storage.py` 无 WAL、无 `busy_timeout`，每个方法新建连接且 `with conn` 只提交不关闭 —— 并发写会 `database is locked`。

## 代码改动后

跨层改动（`api/`、`modules/`、`storage.py`、`agent/` 中涉及两层以上）后，刷新 `docs/CODEBASE_MAP.md` 的对应章节并更新其 `last-mapped`；发现新的坑回填第 7 节。
