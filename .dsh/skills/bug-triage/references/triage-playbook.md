# 分层探针手册

每节的代码都可直接粘进 `python` REPL 或临时脚本。**工作目录均为项目根** `get_everything_framework/`。

准备：

```powershell
cd E:\Programmingtools\get_everything_framework\get_everything_framework
python -c "import sys; print(sys.version)"
python -m pytest -q          # 基线：M0 冒烟 + 仓库布局，应全绿
ruff check .                 # 基线：静态检查
```

---

## A. 路由层

不启动服务器，直接打接口：

```python
import json
from app import app
c = app.test_client()

r = c.get("/api/tools")
print(r.status_code, r.get_data(as_text=True)[:500])

r = c.post("/api/run", json={"domain": "127.0.0.1", "tools": ["subfinder"]})
print(r.status_code, r.get_data(as_text=True)[:500])

r = c.get("/api/results?domain=example.com&limit=5")
print(r.status_code, json.dumps(r.get_json(), ensure_ascii=False, indent=2))
```

判读：

- `/api/run` 抛出的不是 JSON 而是 HTML 堆栈 → 未捕获异常冒泡（例如 `modules/httpx.py` 的 `RuntimeError`、`modules/shuffledns.py` 未包裹的 `subprocess`）。
- `GET /` 返回 500 `TemplateNotFound` → **预期行为**，`web/templates` 已不存在。
- 400 且 `{"error": "存在不支持的工具: xxx"}` → 是 `tool_runner.load_tools` 的校验，不是 bug。

列出真实路由表（核对文档第 1.1 节）：

```python
from app import app
for r in sorted(app.url_map.iter_rules(), key=lambda x: x.rule):
    print(f"{','.join(sorted(r.methods - {'HEAD','OPTIONS'})):12} {r.rule}")
```

---

## B. 编排层

`run_tools` 的输入输出可以脱离 HTTP 单独看：

```python
from tool_runner import load_targets, load_tools
print("targets:", load_targets(domain="example.com"))
print("tools  :", load_tools(["subfinder", "httpx"]))
try:
    load_tools(["nuclei"])          # 未注册 → ValueError
except ValueError as e:
    print("expected:", e)
```

空目标时的兜底（**注意会落到 `config.py:TARGET_CONFIG` 的硬编码目标**）：

```python
print(load_targets())   # 无参 → ['nfl.com']
```

检查某个工具在整条链上是否「三处都注册了」—— 少一处就表现为「跑通但不入库」或直接 ValueError：

```python
from modules.registry import get_supported_runners
from storage import TOOL_DATABASES
from config import TOOL_CATEGORIES

registry = set(get_supported_runners())
db       = set(TOOL_DATABASES)
cats     = set(TOOL_CATEGORIES)

print("registry - db  :", sorted(registry - db))    # 能跑但落库会退到通用表
print("db - registry  :", sorted(db - registry))    # 表建了但永远没有 runner 写
print("registry - cat :", sorted(registry - cats))
```

---

## C. 适配器层

**先确认可执行文件**（这是「没结果」的头号原因）：

```powershell
foreach ($t in 'subfinder','httpx','dnsx','nmap','naabu','katana','gospider','waybackurls','feroxbuster','dirsearch','amass','oneforall','enscan','alterx','assetfinder','shuffledns') {
  $p = Get-Command $t -ErrorAction SilentlyContinue
  "{0,-14} {1}" -f $t, ($(if ($p) { $p.Source } else { '缺失' }))
}
```

检查配置里写死的路径是否真的存在（`config.py` 里已知有两处可疑）：

```python
from config import (HTTPX_CONFIG, FEROXBUSTER_CONFIG, SHUFFLEDNS_CONFIG,
                    DIRSEARCH_CONFIG, ONEFORALL_CONFIG)
import os, shutil
for name, cfg in [("httpx", HTTPX_CONFIG), ("feroxbuster", FEROXBUSTER_CONFIG),
                  ("shuffledns", SHUFFLEDNS_CONFIG)]:
    path = cfg["path"]
    print(f"{name:12} path={path!r:24} which={shutil.which(path)}")
    wl = cfg.get("wordlist")
    if wl:
        print(f"{'':12} wordlist={wl!r} exists={os.path.exists(wl)}")
```

已核实的两个坑：`HTTPX_CONFIG["path"]` 默认值是 `"http-x"`（疑似笔误）；`FEROXBUSTER_CONFIG["wordlist"]` 是作者本机绝对路径 `D:/c4/...`。

**单独跑一个 Runner（会对真实目标发包，只在授权范围内用）**：

```python
from modules.subfinder import SubfinderRunner
r = SubfinderRunner()
print("output file:", r._build_output_file("example.com"))
print(r._resolve_command([r.config["path"], "-d", "example.com"]))
```

**不发包地验证「命令拼装」是否正确** —— 覆盖 `_execute` 后观察 cmd：

```python
from unittest.mock import patch
from modules.httpx import HttpxRunner

h = HttpxRunner()
seen = {}
def fake_exec(cmd, domain):
    seen["cmd"] = cmd
    return False                     # 让 run_scan 提前返回，不发包
with patch.object(h, "_execute", fake_exec):
    out = h.run_scan("example.com", candidates=["127.0.0.1"])
print(seen["cmd"])
print("returned:", out)
```

**残留输出文件导致的假结果**：Runner 的输出文件按 `md5(domain)[:12]_<tool>.txt` 固定命名（`modules/base.py:_build_output_file`），`_read_results` 不检查时间戳。复现「明明没跑却有结果」：

```powershell
Get-ChildItem E:\Programmingtools\get_everything_framework\get_everything_framework\results -Filter "*_subfinder.txt" |
  Sort-Object LastWriteTime -Descending | Select-Object -First 5 Name,Length,LastWriteTime
```

---

## D. 存储层

用临时库验证，别动 `results/scan_results.db`：

```python
import tempfile, os
from storage import ScanResultStore

db = os.path.join(tempfile.mkdtemp(), "t.db")
s = ScanResultStore(db_path=db)

print(s.save_dedicated_results("example.com", "subfinder", "subdomain", ["a.example.com", "a.example.com", "b.example.com"]))
print(s.save_dedicated_results("example.com", "subfinder", "subdomain", ["a.example.com"]))   # inserted_count 应为 0
print(s.get_results_by_domain("example.com"))
print(s.get_domain_summary("example.com"))
print(s.get_global_summary())
```

查真实库的表与行数（只读）：

```powershell
$db = "E:\Programmingtools\get_everything_framework\get_everything_framework\results\scan_results.db"
python -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); [print(r[0]) for r in c.execute(\"select name from sqlite_master where type='table' order by name\")]" $db
python -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); [print(r) for r in c.execute('select domain,tool_name,result_count,created_at from scan_runs order by id desc limit 10')]" $db
```

并发写失败的复现（对应 `database is locked`）：

```python
import threading, tempfile, os
from storage import ScanResultStore
db = os.path.join(tempfile.mkdtemp(), "t.db")
ScanResultStore(db_path=db)
def worker(i):
    ScanResultStore(db_path=db).save_dedicated_results("e.com", "subfinder", "subdomain", [f"h{i}.e.com"])
ts = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
[t.start() for t in ts]; [t.join() for t in ts]
print("done")
```

---

## E. Agent 层

分三步插桩，别一上来就看 `Action.run`：

```python
from agent.intent import analyze_intent
from agent.planner import build_plan

intent = analyze_intent("帮我收集 example.com 的子域名", has_uploaded_file=False, context_state={})
print(intent)
plan = build_plan(intent, {})
print(plan.to_dict() if plan else None)
```

确认状态迁移：

```python
from agent.plan_state import is_confirm, is_cancel, is_meaningful_new_intent
for t in ["确认执行", "执行", "取消", "算了", "改成只做 subfinder"]:
    print(f"{t:16} confirm={is_confirm(t)} cancel={is_cancel(t)}")
```

整条链（不碰网络 —— 只读意图，例如查看已有结果）：

```python
from agent.service import handle_agent_message
from storage import ScanResultStore
r = handle_agent_message("查看 example.com 的已有结果", store=ScanResultStore())
print(r["message"][:800])
print("plan_status:", r["plan_status"])
```

**注意**：本项目运行时不调用任何大模型（`agent/client.py`、`agent/providers/*` 无调用方）。凡是「模型超时 / model 返回格式不对」类症状，在当前代码路径上不可达 —— 先确认调用方是否真的接上了 provider，再怀疑网络。

---

## F. 复现时不要做的事

- 不要扫描任何非授权的外部域名。默认只用 mock runner 与 `127.0.0.1`。
- 不要 `git reset --hard` / `git clean -fd`（项目文档明令禁止）。
- 不要覆盖 `get_everything_framework/scripts/OneForAll.exe` 的未提交差异。
- 不要在 `results/scan_results.db` 上做写操作来复现 bug，用临时库。
