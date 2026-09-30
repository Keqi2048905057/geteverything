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

仍待处理：

| 状态 | 问题 |
|---|---|
| 待修复 | `/api/tools`、`/api/results`、`/api/export` 仍可匿名读取 |
| 待修复 | `/api/export` 返回服务器文件路径，会泄露本机目录结构 |
| 待修复 | `storage.py` 旧结果库无 WAL、无 `busy_timeout`，并发写会 `database is locked` |
| 待修复 | `config.py` 中 `FEROXBUSTER_CONFIG` 的 `wordlist` 是开发机绝对路径，换机器会失败 |
| 待修复 | `scripts/*.exe` 等工具二进制不进仓库，需自行按 `README.md` 准备，缺失时报 `tool_not_found` |

## 使用约定

- 默认只跑 `mode=mock`，或对 `127.0.0.1` fixture server 做验证；
- 真实外部扫描必须显式开启（`GEF_ALLOW_REAL_SCAN=true`），且目标必须落在已创建且
  `active_scan=true` 的 Scope 内，否则返回 403 `scope_violation`；
- `.env`、`results/`、`uploads/`、`exports/`、`*.db` 一律不得提交进 Git；
- 发现密钥已进入 Git 历史时，先轮换密钥，再处理历史。
