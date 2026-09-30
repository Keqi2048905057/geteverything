# 变更日志

本文件记录本仓库的阶段性变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased] — 本机联调版

基线：`main` / `d86578a`（fork 自 `Linki4964/get_everything_framework`）。

> 执行依据（执行提示词、实施方案、逐里程碑验收报告）是**本机过程材料，不入库**，
> 仅保存在开发机上。仓库内的项目说明见 `README.md` 与 `docs/CODEBASE_MAP.md`。

### M0 — 保护工作区与建立基线（已完成）

新增：

- `get_everything_framework/pyproject.toml`：pytest / ruff / mypy 工具链配置（不声明可安装包元数据）。
- `get_everything_framework/requirement-dev.txt`：开发依赖（pytest / ruff / mypy）。
- `get_everything_framework/tests/`：`conftest.py`、`unit/`（冒烟与仓库布局测试）、`integration/`、`fixtures/` 占位。
- `.github/workflows/ci.yml`：push / PR 上运行 `ruff check .` 与 `pytest -q`。
- `LICENSE`（MIT + 授权使用声明）、`SECURITY.md`、`CONTRIBUTING.md`、本文件。
- `.gitignore`：补齐运行期产物、缓存与本地密钥的忽略规则。

变更：

- 仓库远端拓扑调整为 `origin` = 个人仓库、`upstream` = 原仓库（不改动代码）。
- 新建工作分支 `codex/local-mvp`。

保留不动：

- `get_everything_framework/scripts/OneForAll.exe` 的未提交差异。

已知问题（后续里程碑处理）：

- `main` 缺 `web/templates/index.html`，首页 `TemplateNotFound`（M1）。
- 所有 API 无认证、可匿名写 `.env`（M2）。
- 扫描阻塞 Flask 请求线程（M3）。
- 工具失败返回 `[]`，与零结果不可区分（M4）。
