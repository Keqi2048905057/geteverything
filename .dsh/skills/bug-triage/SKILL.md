---
name: bug-triage
description: 在 get_everything_framework 里定位并修复 bug 的固定流程 —— 先查 docs/CODEBASE_MAP.md 的症状索引，再造可复现的反馈循环，再单变量插桩。当用户报告报错、结果不对、扫描无输出、页面/接口异常、或说"帮我看看哪里出问题"时使用。Use when debugging or diagnosing a bug, error, wrong result, or regression in this project.
whenToUse: 用户报告 bug / 异常 / 结果不符 / 行为回归，需要在 api、modules、storage、agent 中定位根因时
---

# Bug 定位（bug-triage）

核心主张：**先查表，再造环，最后才读代码。** 这个项目 46 个提交、62 个 py 文件、17 个适配器长得几乎一样，靠通读代码定位 bug 一定慢。

## 第 0 步 — 查地图（不要跳过）

1. 打开 `docs/CODEBASE_MAP.md` 第 6 节「BUG 定位索引表」（22 条症状）。
2. 症状能对上 → 直接去它给的 `文件:函数` 验证，而不是从 `app.py` 重新读起。
3. 对不上 → 用第 1 步的「四层探针」把范围压到一层，再回第 6 节找相邻症状。
4. 地图不存在或明显过时（`git log` 显示上次 `last-mapped` 之后动过结构）→ 先跑 `codebase-map` skill。

症状索引只是**候选**，不是结论。每条都要在代码里点开确认，别照着表格改。

## 第 1 步 — 把范围压到一层

先回答「这个症状属于哪一层」，只在这一层里查：

| 症状面 | 层 | 首选探针 |
|---|---|---|
| HTTP 4xx/5xx、页面打不开、接口返回格式错 | 路由层 | Flask `test_client()` 直接打，看 `status_code` + `data` |
| 接口 200 但条数/内容不对 | 编排层 / 存储层 | 先看 `tool_runner.py:run_tools` 的返回值，再查 SQLite |
| 扫描没结果、结果假、卡住 | 适配器层 | 单独实例化 Runner，查可执行文件与输出文件 |
| Agent 回复不对、计划不对 | agent 层 | 直接调 `agent/intent.py:analyze_intent` 看 `UserIntent` |

`references/triage-playbook.md` 有每层的确定性探针代码与命令，直接抄。

## 第 2 步 — 造反馈循环（这一步是全部价值所在）

要求：**快（秒级）、确定（同样输入同样结果）、不打真实外网目标**。

本项目可用的四种，按优先级：

1. **纯函数级**：`target_parser.normalize_target`、`intent.analyze_intent`、`planner.build_plan`、`storage._normalize_results` 都可直接调用，无副作用。
2. **存储级**：`ScanResultStore(db_path=tmp_path/"t.db")` 建临时库，绕过 HTTP 验证落库/查询/去重逻辑。
3. **HTTP 级**：`flask.test_client()` 打 `/api/*`，不启动 5000 端口。注意 `GET /` 因为 `web/templates` 缺失**必然 500**，那不是 bug 现场。
4. **适配器级（需要伪造）**：monkeypatch `modules.registry.RUNNER_REGISTRY` 或 `tool_runner.build_runner`，塞一个返回固定列表的假 Runner —— 这样才能在不执行真实扫描的前提下测「编排 → 落库」链路。

**硬约束**：不要为了让 bug 复现而扫描任何真实域名。项目文档（`DSH_执行提示词.md` 第 2 条）明确要求只用 mock runner 与 `127.0.0.1` fixture。需要「真工具跑通」的证据时，用 `httpx`/`subfinder` 对 `127.0.0.1` 或自建本地服务。

有一个**跑得动、失败得清楚**的循环，bug 就修了一半。

## 第 3 步 — 复现

跑循环，确认复现的是**用户描述的那个症状**，不是旁边另一个错误。记下确切报错文本/错误数值，后面用它验收。

## 第 4 步 — 3–5 个可证伪假设，排序后给用户看

每个假设写成「如果是 X，那么改 Y 会让症状消失 / 改 Z 会让它更严重」。说不出预测的就是感觉，丢掉。

给用户看排序后的列表，成本极低，往往一句「那个我们上周刚改过」就能重排。

## 第 5 步 — 单变量插桩

- 一次只改一个变量。
- 优先用调试器/REPL 断点，其次打日志，**不要**「全打日志再 grep」。
- 日志统一加唯一前缀（如 `[DBG-a4f2]`），收尾 `grep` 一次全清。
- 在这个项目里注意：`modules/base.py` 用 `capture_output=True` 捕获了子进程 stdout，你 print 的东西**不会**出现在工具输出里 —— 想在适配器内观察，得 print 到自己的进程 stdout（web 场景看 Flask 控制台）或直接写文件。

## 第 6 步 — 先写回归测试，再修

- 测试放到 `get_everything_framework/tests/unit/` 或 `integration/`，在**项目根**（不是仓库根）执行：`python -m pytest -q`。
- `pyproject.toml` 已配好 `pythonpath = ["."]`，测试里可直接 `import storage` / `import tool_runner`。
- 想清楚 seam 对不对：如果 bug 出在调用方（例如 `tool_runner.run_tools` 没接住 `httpx` 抛的 `RuntimeError`），只测 `HttpxRunner` 内部是假信心 —— 要在调用点测。
- 找不到合适的 seam，本身就是发现：在报告里写明「架构挡住了锁死这个 bug 的测试」，并把结论回填到 `docs/CODEBASE_MAP.md` 第 7 节。

## 第 7 步 — 收尾

- [ ] 原始症状不再复现（重跑第 2 步的循环）
- [ ] 回归测试通过
- [ ] `[DBG-...]` 插桩全部删除
- [ ] 提交信息里写清「真正生效的那个假设」，让下一个调试的人少走一遍
- [ ] 地图需要更新的（新发现的坑、新的调用链）回填 `docs/CODEBASE_MAP.md`

## 本项目最容易踩的 5 个坑（先排除这些）

1. **静默空结果**：`modules/base.py:_execute` 把失败/超时/未安装全降级成 `return False` → Runner `return []`，和「跑通但没结果」不可区分。怀疑假空结果时，先直接 `Get-Command <工具名>` 确认可执行文件存在。
2. **残留输出文件**:`_read_results` 只判断文件存在、不看时间戳，`results/` 里按 `md5(domain)[:12]_<tool>.txt` 命名的旧文件会被当成本次结果。先删产物再复现。
3. **首页 500 是预期的**：`app.py:26` 指向的 `web/templates` 目录在历史提交里被删除，`GET /` 必 TemplateNotFound。不要把它当 bug 修，除非用户明确要恢复前端。
4. **无鉴权、无 Scope**：任何「为什么能/不能扫这个目标」的问题，看 `agent/action.py:_validate_domain`（黑名单在 agent 侧，API 侧完全没有）。
5. **SQLite 锁**：`storage.py` 无 WAL、无 `busy_timeout`，并发写会 `database is locked`；每个方法新建连接且 `with conn` 不关闭，长跑会句柄泄漏。
