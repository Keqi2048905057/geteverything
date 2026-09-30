# 贡献指南

## 当前阶段

本仓库处于**本机联调阶段**，目标是在一台 Windows 开发机上跑通
「创建授权范围 → 提交扫描 → 查看状态 → 查看结果 → 导出」闭环。
里程碑执行依据（执行提示词与实施方案）是本地过程材料，不随仓库分发。

**当前阶段不接受“接入更多扫描工具”类贡献**，优先事项见 `CHANGELOG.md` 的已知问题列表。

## 环境准备

```powershell
cd get_everything_framework
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirement.txt
pip install -r requirement-dev.txt
```

## 提交前必须通过

```powershell
pytest -q
ruff check .
```

M7 起额外要求：

```powershell
mypy app.py core api jobs storage modules
```

## 代码约定

- 注释与文档字符串使用简体中文，标识符与命令保持英文；
- 工具执行一律 `shell=False` + 参数列表，禁止把用户输入拼进命令字符串；
- 新增 runner 必须返回结构化 `RunnerResult`，失败时禁止只返回 `[]`；
- 所有扫描任务必须关联 `scope_id`，目标执行前必须过 Scope 校验；
- 扫描任务必须异步执行，不允许阻塞 Flask 请求线程；
- 任何文件打开都要来自受控 upload id，禁止接受任意绝对路径。

## 禁止提交的内容

- `results/`、`uploads/`、`exports/` 下的任何产物；
- `*.db` / `*.sqlite*`；
- `.env` 及任何真实密钥、Token、Cookie；
- `SecLists/` 字典与 `scripts/*.exe` 工具二进制（体积大、随上游变化，请用安装脚本获取）；
- 本机过程材料：执行提示词、实施方案、逐里程碑验收报告（`.gitignore` 已忽略）；
- 与当前里程碑无关的大规模重构（请拆成独立 PR）。

## 提交信息

沿用现有风格，一行说清“改了什么”：

```text
feat: 新增独立 worker 与 SQLite job 队列
fix: 关闭 debug 并移除默认 dev-secret-key
test: 补充 Scope 拒绝路径的集成测试
docs: 更新本机启动步骤
```

## 分支与 PR

- 功能分支从 `main` 切出，命名 `feature/<主题>` 或 `fix/<主题>`；
- PR 描述里写清：改动文件、完成的验收项、执行过的命令、测试结果、已知问题；
- 每个 PR 只对应一个里程碑内的小步改动，不混合无关变更。
