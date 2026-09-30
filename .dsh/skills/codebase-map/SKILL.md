---
name: codebase-map
description: 为 get_everything_framework 建立或刷新「项目逻辑地图」docs/CODEBASE_MAP.md —— 入口、调用链、数据模型、症状→位置索引。修 bug 前、接手陌生模块前、或改动跨越 api/modules/storage/agent 时先跑它。Use when mapping this codebase, refreshing the architecture map, or preparing to debug an unfamiliar area.
whenToUse: 需要理解本项目结构、为修 bug 建立定位依据、或改动后刷新代码地图时
---

# 代码地图（codebase-map）

唯一目标：把「看到某个症状 → 该去看哪个文件哪个函数」变成**查表**，而不是每次重读代码。

## 何时跑

- 用户报告 bug、但还不确定问题在哪一层 → 先建地图，再进入 `bug-triage`。
- 改动跨了 `api/` `modules/` `storage.py` `agent/` 中的两层以上 → 改完刷新对应章节。
- `docs/CODEBASE_MAP.md` 不存在，或 `git log` 显示地图上次更新后代码已明显变动。

## 产出物

| 路径 | 内容 |
|---|---|
| `docs/CODEBASE_MAP.md` | 地图正文（仓库根 `E:\Programmingtools\get_everything_framework\docs\`） |
| `docs/CODEBASE_MAP.md` 第 6 节 | **症状 → 排查位置** 索引表，这是整份文档的价值所在 |
| `CONTEXT.md`（可选） | 只放领域术语（Scope、Runner、专属表、工具分类），供 `bug-triage` 引用 |

写之前先读 `references/skeleton.md`：那是本仓库已核实的骨架（分层、关键文件、三条调用链、表结构、已知坑），用它做起点和校对基准，**不要凭目录名重新猜一遍**。

## 必须覆盖的七节

1. **定位与技术栈**：Flask 3.x + SQLite + 外部 CLI 工具 + LLM agent。启动：`python app.py`（`app.py:165` → `127.0.0.1:5000`）。依赖见 `requirement.txt`。
2. **分层与数据流**：HTTP 路由 → Blueprint → `tool_runner` 编排 → `modules/*` 适配器 → 子进程 → `_read_results` → `storage` 落库。逐层标出目录/文件。
3. **模块职责表**：列 = 文件 | 职责 | 关键函数 | 被谁调用 | 依赖谁。17 个适配器可合并成一行共性，但必须点明 `modules/base.py:BaseRunner` 的约定与 `modules/registry.py:RUNNER_REGISTRY` 的静态注册方式。
4. **三条关键调用链**（写成 `文件:函数` 的逐步路径，并标注每步的数据形态）：
   - 提交扫描：`api/scan.py:execute_scan` → `tool_runner.py:run_tools` → `load_targets/load_tools` → `modules/registry.py:build_runner` → `runner.run_scan` → `modules/base.py:_execute` → `storage.py:save_dedicated_results`
   - 适配器加载：`registry.py:RUNNER_REGISTRY`（手写字典，非自动发现）→ `build_runner` → `BaseRunner._resolve_command/_execute/_read_results`
   - Agent 规划：`agent/service.py:handle_agent_message` → `agent/action.py:AgentAction.run` → `agent/intent.py:analyze_intent` → `agent/planner.py:build_plan` → `_execute_plan` → `_execute_tool`
   与代码不符时以代码为准，并写明真实情况。
5. **数据模型**：`storage.py:TOOL_DATABASES` 的映射（tool → 专属表 / 结果列 / category）；`scan_runs`、`tool_results`、每工具专属表 `<tool>_results` 的字段与 `UNIQUE` 约束；结果分类枚举（subdomain / alive / web / url / port）。
6. **症状 → 位置索引表**（**必写，≥15 行**）：列 = 典型症状 | 最可能的 2–4 个排查位置（`文件:函数`） | 该处典型失败模式。可直接以 `references/symptom-index.md` 为种子，但**每条都要回到代码里核实当前是否仍成立**，不成立的标为「已修复」而不是删掉。
7. **已知薄弱点**：读代码时发现的真实问题（异常被吞、路径硬编码、缺超时、状态未回滚、全局可变状态、类型不一致、缺鉴权/缺 Scope），每条给 `文件:函数`。

## 生成步骤

1. 记录基线：`git rev-parse HEAD` 与 `git status --short`，写进文档头部的元信息块（含 `last-mapped`）。
2. 用**并行子代理**分头挖，别串行读几十个文件：
   - A：入口与 API 层（`app.py`、`api/*.py`）→ 第 2、3 节
   - B：工具适配器与编排（`tool_runner.py`、`modules/*`）→ 第 3、4 节
   - C：存储与导出（`storage.py`、`exporter.py`、`target_parser.py`）→ 第 5 节
   - D：Agent 链（`agent/*`）→ 第 4 节第三链
   - E：横切审计（为什么修 bug 慢：吞异常、硬编码、无 error_code、同步阻塞、无 Scope/鉴权）→ 第 7 节
3. 汇总去重，第 6 节由你来写（子代理给的症状条目只当候选，必须亲自核对函数名）。
4. 只写实际读到的内容。读不到的文件（例如某个被删的模板目录）要**明说缺失**，不要编造行号。

## 刷新（增量）

- 文档存在且元信息有 `last-mapped`：跑 `git diff <sha>..HEAD --name-status` 与 `git diff HEAD --name-status`，合并未跟踪文件，只有受影响的章节重跑对应子代理。
- 无结构性变化就输出「自上次地图以来无结构性变化」并停下，不要整篇重写。
- 每次刷新后更新 `last-mapped`。

## 质检清单

- [ ] 每条「调用链」都能在代码里点到对应的函数名
- [ ] 第 6 节 ≥15 条症状，且症状来自本项目的真实失败模式（不是通用套话）
- [ ] 缺失的文件 / 未验证的推断被显式标注
- [ ] 没有把「应该是什么」写成「实际是什么」
