# DEPLOYMENT.md — 部署与运维

> **本文回答一个问题**：在一台干净的机器上，怎样把 Web 服务与异步 worker 跑起来，配好 `.env`，
> 验证它真的健康，以及出问题时先看哪里。
> 接口细节读 [`API.md`](API.md)；分层与设计取舍读 [`ARCHITECTURE.md`](ARCHITECTURE.md)；
> 逐文件代码地图与故障索引表在 [`CODEBASE_MAP.md`](CODEBASE_MAP.md)。

---

## 1. 环境要求

| 项 | 要求 | 依据 |
|---|---|---|
| Python | **3.10+**（CI 用 3.11；`pyproject.toml` `target-version = "py310"`） | `pyproject.toml:29` |
| 操作系统 | **Windows 是本机联调环境**；Linux/macOS 同样可跑（CI 覆盖 ubuntu + windows） | `.github/workflows/ci.yml` |
| 磁盘 | 源码 + 依赖约 200 MB；`results/` 会随任务增长 | — |
| 网络 | 默认**不需要**外网：`mode=mock` 全程离线 | `core/mock.py` |
| 外部工具 | **可选**。17 个扫描工具都不装也能启动，`/health` 会把它们标成 `missing` | `core/health.py:tools_health()` |

> ⚠️ 真实扫描**必须**同时满足两个开关（详见 §5.1），且目标必须落在已创建的 Scope 内。
> 未授权目标一律不要在本文档流程里尝试。

---

## 2. 安装依赖

```powershell
cd <仓库根>\get_everything_framework

# 运行依赖（27 个包，全部锁定版本）
python -m pip install -r requirement.txt

# 开发依赖（pytest / ruff / mypy，仅在跑测试与静态检查时需要）
python -m pip install -r requirement-dev.txt
```

`requirement.txt` 的关键项：Flask 3.1.3、Werkzeug 3.1.8、Jinja2 3.1.6、Waitress 3.0.2、
python-dotenv 1.1.0、openpyxl 3.1.5、httpx 0.28.1、pydantic 2.13.4、openai 2.38.0、tqdm 4.67.3。
SQLite 用标准库，**不需要**单独安装，也没有 ORM/迁移框架。

**注意 `openai` 装了但运行时不用**：Agent 路径不调用任何大模型（见 §8.1），`openai` 只是依赖清单里的
既存项，不要因为「没配 API Key」而以为服务起不来。

不想手写命令的用安装脚本（会一并处理系统层依赖与 Go 工具，可能联网）：

```powershell
# 只检查现状、不安装
powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1 -CheckOnly
# 完整安装（含 Go 工具，耗时长）
powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
```

可用开关：`-CheckOnly`、`-SkipSystem`、`-SkipPythonDeps`、`-SkipGoTools`、`-WithOptional`、`-DryRun`。
Linux 对应 `scripts/install_linux.sh`。

---

## 3. 配置 `.env`

`.env` 放在**项目根**（`get_everything_framework/`，与 `config.py` 同级）；
`config.py:6` 用 `load_dotenv(<项目根>/.env)` 加载，**与当前工作目录无关**。
从模板开始：

```powershell
Copy-Item .env.example .env
```

> 🔒 **绝不把 `.env` 提交进 Git**，也不要把里面的值贴进 issue / 日志 / 文档。仓库 `.gitignore` 已忽略它。
> 本文只解释每个键的含义，不给出任何真实密钥。

### 3.1 键位说明

| 键 | 默认值 | 作用 | 本机联调建议 |
|---|---|---|---|
| `SECRET_KEY` | 空 | Session 签名密钥 | **必须改**成随机值，见下 |
| `LOCAL_ADMIN_TOKEN` | 空 | 本地管理员登录凭据 | 建议固定；留空则每次启动打印临时 Token |
| `WEB_HOST` | `127.0.0.1` | Web 绑定地址 | **保持 127.0.0.1**，不要 `0.0.0.0` |
| `WEB_PORT` | `5000` | 监听端口 | 端口冲突时改这里 |
| `WEB_DEBUG` | `false` | Flask debug | **必须保持 false**（debug 会暴露调试器） |
| `WEB_THREADS` | `8` | Waitress 线程数 | 够用 |
| `GEF_ALLOW_REAL_SCAN` | `false` | 真实扫描总开关 | **保持 false**，除非明确要打真实目标 |
| `GEF_LOG_LEVEL` | `INFO` | 日志级别 | 排障时临时 `DEBUG` |
| `GEF_LOG_FORMAT` | `json` | `json` / `text` | `json` 便于 grep，`text` 便于人读 |
| `GEF_PROCESS_TIMEOUT` | `120` | 子进程超时（秒） | 大目标可调大 |
| `GEF_SCAN_DB_PATH` | `results/scan_results.db` | **旧库**路径 | 一般不动 |
| `LOCAL_DB_PATH` | `results/local.db` | **新库**路径 | 一般不动 |
| `HTTPX_PATH` | `http-x` | httpx 可执行文件名 | Windows 下见 §8.6 |
| `SHUFFLEDNS_WORDLIST` | `SecLists/subdomains-top1million-5000.txt` | 字典路径 | 字典不随仓库分发，见 §8.4 |
| `FEROXBUSTER_WORDLIST` | `SecLists/raft-small-directories.txt` | 字典路径 | 同上 |
| `ENSCAN_CONFIG_PATH` | 空 | enscan `config.yaml` 路径 | 留空自动探测 |
| `LLM_*`（9 个键） | — | 大模型配置 | **当前运行时不用**，可全部留空 |
| `FOFA_*` / `HUNTER_API_KEY` / `QUAKE_API_KEY` / `SHODAN_API_KEY` | 空 | 外部搜索引擎凭据 | 留空即可 |

生成两个随机值（**不要用模板里的 `dev-secret-key`**）：

```powershell
# 会话密钥
python -c "import secrets; print(secrets.token_urlsafe(32))"
# 管理员 Token
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

把输出分别写进 `.env` 的 `SECRET_KEY=` 与 `LOCAL_ADMIN_TOKEN=`。

### 3.2 弱密钥与缺失 Token 的行为

* **`SECRET_KEY` 缺失、为 `dev-secret-key`/`changeme`/`secret`/`flask-secret`、或短于 16 字符**：
  服务**不会**回落到固定值，而是为本进程生成一次性随机密钥（`core/security.py`），并打印
  `RuntimeWarning`。后果是**重启后所有登录会话失效，需重新登录**。`/health` 的
  `security.secret_key` 会显示 `ephemeral`。
* **`LOCAL_ADMIN_TOKEN` 留空**：启动时控制台打印一个仅本进程有效的 Token，
  `GET /api/auth/session` 的 `token_is_ephemeral=true`。重启即换。
* 程序**不会**自动改写你的 `.env`（只有 `POST /api/settings` 会写，且需管理员 + 原子写 + 写前备份）。

---

## 4. 启动

### 4.1 Web 服务

```powershell
cd <仓库根>\get_everything_framework
python app.py
```

启动后控制台会打印访问地址、登录方式，以及（未配置 Token 时）本次进程的临时 Token。
默认地址 `http://127.0.0.1:5000/`。

* 使用 **Waitress**（生产级 WSGI），不是 Flask 开发服务器；`WEB_THREADS` 控制线程数。
* `python app.py` 是**唯一会配置结构化日志**的两种启动方式之一（另一种是 worker）。
  用 `waitress-serve app:app` 或 `flask run` 启动 **不会**有 §6 的 JSON 日志。
* 改 `WEB_HOST` 前想清楚：`0.0.0.0` 会把管理接口暴露到局域网。

### 4.2 Worker（**必须单独启动**）

`POST /api/jobs` 只把任务写进队列就返回，**执行发生在另一个进程里**。不启动 worker，
任务会永远停在 `queued`。

```powershell
cd <仓库根>\get_everything_framework
python -m jobs.worker
```

参数（`jobs/worker.py:build_parser()`）：

| 参数 | 默认 | 含义 |
|---|---|---|
| `--worker-id` | `hostname-pid` | 自定义 worker 标识（会写进日志与心跳文件） |
| `--poll` | `2.0` | 空闲轮询间隔（秒） |
| `--heartbeat` | `5.0` | 心跳文件写入间隔（秒） |
| `--lease` | `jobs_store.DEFAULT_LEASE_SECONDS` | 任务租约（秒）；超时未续租视为 worker 死亡 |
| `--idle-exit` | `0` | 空闲多少秒后自动退出；**0 = 一直等** |
| `--once` | 关 | **只跑一轮就退出**（验收 / 测试用） |
| `--step-delay` | `0.0` | 每步之间人为等待秒数（演示进度与取消） |
| `--quiet` | 关 | 不打人读进度行（结构化日志照旧） |

常用形态：

```powershell
# 单次消费：处理完当前队列就退出，适合脚本化验证
python -m jobs.worker --once

# 观察多步任务的进度与取消：每步等 3 秒
python -m jobs.worker --step-delay 3

# 只作为一次性消费者、日志安静
python -m jobs.worker --once --quiet
```

行为要点：

* **优雅退出**：`Ctrl+C`（SIGINT/SIGTERM）时把正在跑的任务标成 `interrupted` 再退出，
  任务不会静默消失，可用 `POST /api/jobs/<id>/retry` 重跑。
* **租约与恢复**：worker 被强杀（`Stop-Process -Force`）时任务会留在 `running`，
  下一个 worker 启动时 `recover_stale_jobs()` 把过期租约的任务标 `interrupted`。
* **心跳**：每 `--heartbeat` 秒写一次 `results/worker_heartbeat`；`/health` 读它的 mtime，
  超过 **30 秒**判为 `stale`。
* **Web 与 worker 互不阻塞**：worker 挂了 Web 照常用；Web 重启不影响 worker 正在跑的任务。

### 4.3 一键脚本（推荐首次使用）

```powershell
cd <仓库根>
powershell -ExecutionPolicy Bypass -File get_everything_framework\scripts\run_local.ps1
```

它做的事：可选装依赖 → 起 worker → 起 Web（前台）→ `Ctrl+C` 时依次收掉两个进程。
开关：`-SkipInstall`、`-NoWorker`、`-Host`（默认 `127.0.0.1`）、`-Port`（默认 `5000`）、`-StepDelay`。
脚本会把 `WEB_HOST` / `WEB_PORT` / `WEB_DEBUG=false` 注入**两个**子进程的环境——
两个进程必须共用同一份 `.env`（即同一个数据库路径），否则 worker 会去另一个库认领任务。

> `-NoWorker` 的提示很直白：**任务会一直停在 `queued`**。

### 4.4 健康检查

```powershell
curl http://127.0.0.1:5000/health
```

`GET /health` 挂在**根路径**、**免登录**、只读无副作用（应用库不存在时返回 `missing`，
不会顺手建库）。要重点看的四个字段：

| 字段 | 期望 | 不对时说明什么 |
|---|---|---|
| `database` | `ok` | `missing` = 应用库还没建（先随便调一次需登录的接口或跑个任务）；`error` = 库文件损坏/不可写 |
| `worker` | `ok` | `missing` = worker 从没启动过；`stale` = 心跳超过 30 秒，worker 卡死或已退出 |
| `queue.status` | `ok` | `backlog` = 任务堆积，worker 消费不过来 |
| `tools_summary.missing` | 0（若要用全部工具） | 缺哪个工具在 `tools` 里逐项列出 |

`modes.real_scan_enabled` 反映 `GEF_ALLOW_REAL_SCAN`；`security.secret_key` 反映
`SECRET_KEY` 是 `configured` 还是进程级 `ephemeral`。

> ⚠️ README 建议用 `curl /api/tools` 当健康检查，**那是不对的**：它在历史接口里，
> 既不反映 worker 与队列，也不反映数据库状态。

---

## 5. 首次跑通一条链路（5 步）

```powershell
# 1) 建 Scope（把 Token 换成你自己 .env 里的值）
curl -X POST http://127.0.0.1:5000/api/scopes ^
  -H "Content-Type: application/json" -H "X-Local-Token: <Token>" ^
  -d "{\"name\":\"本机联调\",\"allowed_domains\":[\"example.test\"],\"active_scan\":false}"

# 2) 创建 mock 任务（默认 mode=mock，绝不调用真实工具）
curl -X POST http://127.0.0.1:5000/api/jobs ^
  -H "Content-Type: application/json" -H "X-Local-Token: <Token>" ^
  -d "{\"scope_id\":\"<上一步返回的 scope_id>\",\"targets\":[\"example.test\"],\"tools\":[\"subfinder\"]}"
# → 202 {"ok":true,"job_id":"job_...","status":"queued",...}

# 3) 看队列（worker 未启动时应看到 queued:1）
curl http://127.0.0.1:5000/health

# 4) 启动 worker（若还没启动）
python -m jobs.worker --once

# 5) 查任务与证据
curl -H "X-Local-Token: <Token>" http://127.0.0.1:5000/api/jobs/<job_id>
curl -H "X-Local-Token: <Token>" http://127.0.0.1:5000/api/jobs/<job_id>/steps
curl -H "X-Local-Token: <Token>" http://127.0.0.1:5000/api/assets
```

第 5 步的资产一般能看到前面 mock 步骤落下的观测；`/api/jobs/<id>/events` 里的
`step.assets_ingested` 事件会告诉你落了几条、跳过了几条、为什么跳。

### 5.1 开启真实扫描（确认已获授权后再做）

```text
① .env: GEF_ALLOW_REAL_SCAN=true   → 重启 Web（环境变量是启动时读的）
② Scope 的 active_scan 必须为 true （创建时 active_scan:true，或另建一个）
③ 目标必须落在该 Scope 的 allowed_domains / allowed_cidrs 内
```

缺任何一项：`mode=real` 一律 **403 `scope_violation`**（实测未开环境开关时 `details.env =
"GEF_ALLOW_REAL_SCAN"`）。**没有 Scope 就没有扫描**，不存在隐式全放行。

---

## 6. 日志与可观测性

结构化日志默认开启（`GEF_LOG_FORMAT=json`），**一行一个 JSON 事件，只写 stderr**：

```json
{"ts":"2026-10-01T02:27:38+00:00","level":"INFO","event":"job_step_finished","job_id":"job_x",
 "worker_id":"host-1234","step_id":"step_x","tool":"httpx","target":"example.test",
 "status":"succeeded","found_count":3,"duration_ms":1200}
```

**排障方法**：拿任一响应头里的 `X-Request-Id` 去 grep 日志，能捞出这一跳的全部事件
（请求进入、任务创建、失败原因）。`job_id` 则能把 Web 与 worker 两个进程的记录串起来。

已知边界（**必须知道，否则会误判「日志功能坏了」**）：

1. 只有 `python app.py` 与 `python -m jobs.worker` 这两个入口调 `configure_logging()`。
2. 只写 stderr，**没有文件输出、没有轮转**；要留档请重定向或接采集器。
3. `request_id` **不跨进程**（worker 是独立进程）；跨进程靠 `job_id`。
4. 401 的错误信息里 `X-Local-Token` 之后的词会被脱敏规则打码，这是**故意**的，不是日志丢内容。

---

## 7. 测试与静态检查

```powershell
cd <仓库根>\get_everything_framework
python -m ruff check .        # 期望：All checks passed!
python -m pytest              # 期望：826 passed, 2 skipped
```

* 2 个 skip 是 `tests/integration/test_m7_local_e2e.py` 在**本机没有 httpx 可执行文件**时跳过，
  属正常，不是失败。
* 测试会把两个数据库、`uploads/`、`artifacts/`、`exports/` 全部重定向到临时目录
  （`tests/conftest.py`），**不会污染你的 `results/`**。
* 测试过程中会输出一个 `ResourceWarning` 与一条 `UnicodeDecodeError: 'gbk' codec ...`
  的线程异常警告——它们是 Windows 下子进程读取线程的既有噪音，**退出码仍为 0**，可忽略。
* 冒烟子集：`-m "not slow"` 跳过需要真实子进程/等待租约过期的用例。
* 想跑全量严格模式可加 `-W error::ResourceWarning`（`tests/unit/test_storage_connection.py`
  就是这么复现旧库连接泄漏的）。

---

## 8. 故障排查

> 下面每条都来自 `docs/CODEBASE_MAP.md` 第 6 节（27 条症状索引）与第 7 节（36 条已知薄弱点）
> 的**实际代码阅读**，并标注了当前是否已修复。排查原则：**先看事件，再看代码**。

### 8.1 任务一直 `queued`，永远不执行

按顺序排除：

1. **worker 没启动** —— 最常见。看 `/health` 的 `worker` 字段。
2. **worker 启动了但没在消费** —— 看 `worker` 是 `stale`（心跳超 30 秒）还是 `ok`。
3. **在退避窗口里** —— `retry` 之后任务立刻回到 `queued`，但要等 `next_attempt_at`
   之后才会被领（5/10/20/40… 秒，封顶 300 秒）。任务详情里的这个字段会告诉你最早何时可领。
4. **两个进程用了不同的库** —— 手动起了 worker 却没带同一份 `.env`（或用了一键脚本之外的方式），
   worker 在另一个 `local.db` 上轮询。确认 `LOCAL_DB_PATH` 一致。

### 8.2 任务 `interrupted`

租约过期（worker 被强杀/机器睡死）或 worker 优雅退出时正在跑这个任务。
**这不是失败**，任务与已完成的步骤快照都还在，直接 `POST /api/jobs/<id>/retry` 即可
（成功的步骤不会重跑）。注意 `MAX_ATTEMPTS = 5`，超过后 `retry` 会返回 400。

### 8.3 步骤 `succeeded` 但资产页一条都没有

这是**合法状态**——资产是派生产物，落库失败不会把任务标成 failed。先看
`/api/jobs/<id>/events` 里的 `step.assets_ingested` 事件，它的 `written` / `skipped` / `reasons`
直接说明原因。三种常见原因：

1. 工具的 `Observation.category` 不在 `CATEGORY_TO_TYPE` 映射里 → 整条跳过（**故意不猜类型**）；
2. 步骤只有字符串结果且工具是 `httpx` / `naabu` / `nmap` → 形态不确定，**故意不落**；
3. 值本身非法（例如把本地路径当 URL）。

### 8.4 子域爆破类工具（shuffledns / feroxbuster / dirsearch）总是失败

`config_error`，**这是配置问题不是工具问题**。仓库**不分发 `SecLists/`**，
默认字典路径在本机不存在。现在的行为是**显式失败且不启动子进程**（M5 起），
不再静默返回空结果。两种解法：

* 把字典放到项目根 `SecLists/` 下；
* 或用 `SHUFFLEDNS_WORDLIST` / `FEROXBUSTER_WORDLIST` 指向本机已有文件（绝对路径可用）。

字典路径一律**按项目根解析**，与当前工作目录无关。`dirsearch` 的 `wordlist=None` 是合法形态
（不加 `-w`，用工具自带字典），**不算配置错误**。

### 8.5 结果对不上 / 看起来是上一次的

**残留输出文件**：`results/` 里按 `md5(domain)[:12]_<tool>.txt` 命名的旧输出会被当成本次结果。
复现 bug 前先清 `results/` 下的产物文件（M4 起执行前会删同名旧文件，删不掉时写
`stale_output_warning`，但手工残留仍可能干扰判断）。

### 8.6 工具报 `tool_not_found`，但明明装了

* **Windows 上 `httpx` 指的是微软的 `httpx.exe`**，与 ProjectDiscovery 的 httpx 同名冲突。
  项目用 `HTTPX_PATH`（默认 `http-x`）指代真正的那个，安装脚本会建 `http-x` 别名。
  若你手动装工具，确认 PATH 里有 `http-x`。
* `scripts/*.exe` **不会**被自动发现：`config.py` 只给裸命令名，`GO_BIN_WINDOWS/POSIX`
  这两个常量定义了但从未用于注入 PATH。
* `modules/shuffledns.py` 内部硬编码命令名 `"dnsx"`，即使改了 `path` 配置也不生效。

### 8.7 `database is locked`

* **新库**（`local.db`）：已经是 WAL + `busy_timeout=5000` + `BEGIN IMMEDIATE`，正常情况下不该见到；
  见到就是有长事务或外部进程在写同一个文件。
* **旧库**（`scan_results.db`）：**仍无 WAL**，只有连接级 `busy_timeout=5000`。
  并发写旧库仍可能锁等待。旧库在本机联调期间视为**只读历史数据**，不要拿它做并发写入测试。

### 8.8 `GET /` 打不开 / 登录页报错

历史上 `GET /` 必然 500（`TemplateNotFound: index.html`）。**该问题已修复**：
`web/templates/` 与 `web/static/` 都在（`index.html` / `login.html` / `assets.html` +
`app.css` / `app.js` / `assets.js`），`GET /` 返回 200。
README 常见问题 Q6 仍在描述旧状态，**以实际响应为准**。

### 8.9 登录不上 / 一重启就要重新登录

* 重启后要重新登录 = `SECRET_KEY` 是**进程级一次性**的（`/health` 的
  `security.secret_key` 显示 `ephemeral`）。把随机值写进 `.env` 的 `SECRET_KEY` 即可固定。
* Token 每次都变 = `LOCAL_ADMIN_TOKEN` 留空。固定方式见 §3.1。
* 401 且确认 Token 没错：检查是不是把 Token 放在了错误的字段名（应为 `token`）或
  忘记了 `X-Local-Token` 这个头名。

### 8.10 日志里找不到结构化事件

见 §6 的四条边界。最高发的一条是**启动方式不对**：`waitress-serve app:app` / `flask run`
不会配置日志。其次是只调了 `Worker.startup()` 没调 `shutdown()`，导致 `worker_id`
一直挂在同线程上下文里。

### 8.11 导出报 500

`GET /api/export?format=<非 csv/json>` 会 **500 `unknown_error`**，这是**已知缺陷**：
`api/results.py:export_data` 没有捕获 `exporter.export_results` 抛出的 `ValueError`。
只传 `csv` 或 `json`。
另注：`?category=` 在旧库专属表分支里被静默忽略，按分类过滤对已注册工具**不生效**。

### 8.12 上传 `.xlsx` 报 500

`target_parser._parse_xlsx` 需要 `openpyxl`，缺依赖时抛 `ImportError` 而 `api/upload.py`
不捕获 → 500 而不是 400。确认 `openpyxl==3.1.5` 已安装（在 `requirement.txt` 里）。

### 8.13 Windows 上 `python` 命令找不到

用绝对路径调用解释器（本机示例 `python`），
或在安装脚本跑完后**重开终端**让 PATH 生效。

---

## 9. 数据与备份

| 内容 | 位置 | 说明 |
|---|---|---|
| 新应用库（任务/资产/审计） | `results/local.db` | WAL 模式，热备要连 `-wal` / `-shm` 一起拷 |
| 旧扫描结果库 | `results/scan_results.db` | 已跟踪进 Git 的历史数据，视为只读 |
| 原始证据 | `results/artifacts/` | 单个上限 2 MB（`SCAN_LIMITS["max_artifact_bytes"]`） |
| 导出文件 | `exports/` | 可在 `/api/exports` 里查记录（含 sha256） |
| 上传的目标文件 | `uploads/` | 上限 2 MB；`upload_id` 是唯一入口 |
| worker 心跳 | `results/worker_heartbeat` | 可随时删除，worker 会重写 |

**备份**：停掉 Web 与 worker 后整目录复制 `results/` 与 `exports/` 最稳妥。
`/api/exports` 里的 `sha256` 可用于校验导出文件完整性。

**迁移脚本**（旧库 → 新库，只读旧库、可重复执行）：

```powershell
python scripts\migrate_legacy_results.py            # dry-run，只报告
python scripts\migrate_legacy_results.py --apply    # 真正写入
```

它保证**旧库 sha256 前后不变**、重跑幂等（观测 ID 确定性生成），并按退出码区分
「无数据 / 成功 / 部分失败」。先跑 dry-run 看清要写多少条再 `--apply`。

---

## 10. 生产化路径（长期，不是现在）

当前是**本机联调版**，以下迁移在方案与预授权单里都被明确列为**需要先获授权**
（`docs/DECISIONS.md` §4 红线），**不要在没有明确指示时自行实施**：

| 现状 | 长期目标 | 触发条件 |
|---|---|---|
| Windows 直接跑源码 | Linux 容器化（Dockerfile + 独立 worker 容器） | 需要多机/常驻部署时 |
| SQLite `local.db` | PostgreSQL | 单机写并发成为瓶颈时 |
| 单表队列 + 独立 worker 进程 | Redis / Celery 等任务队列 | 需要多 worker 横向扩展、优先级、定时任务时 |
| 手写 SQL | ORM + 迁移框架 | 表结构变更开始频繁时 |
| 服务端模板 + 原生 JS | 前端构建链 | 前端复杂度真正需要时 |

判据是**实际瓶颈**，不是「新技术更现代」。当前架构的取舍理由见
[`ARCHITECTURE.md`](ARCHITECTURE.md) §11。

---

## 11. 与文档的不一致（部署相关）

1. **README 的测试基线是 `759 passed`**，实际为 `826 passed, 2 skipped`。
2. **README 的健康检查写成 `curl /api/tools`**，正确入口是 `GET /health`。
3. **README 完全没提 worker 的启动方式**（全文无 `jobs.worker`）、没提 `LOCAL_ADMIN_TOKEN`、
   没提 `GEF_ALLOW_REAL_SCAN`、没提 `WEB_HOST`/`WEB_PORT`、没提 `local.db`——
   也就是说照着 README 装完，任务会永远停在 `queued`。
4. **README 的 Q6 仍在说首页 `TemplateNotFound`**（已修复）。
5. **`README.md:111` 说「需在 `web/templates/index.html` 部署前端模板」**——
   模板已在仓库里，不需要额外部署。
6. **`docs/SECURITY.md` 不存在**（方案建议的四份文档之一），安全文档实际在仓库根 `SECURITY.md`。
7. **`SECURITY.md` 的「仍待处理」仍把 `FEROXBUSTER_CONFIG` 的字典写成开发机绝对路径**——
   `config.py:247` 现在已是仓库相对路径 + `FEROXBUSTER_WORDLIST` 覆盖，该条目已过期。
8. **`PROJECT_STATE.md` 的硬约束提到 `scripts/OneForAll.exe` 的未提交差异**——
   `.gitignore` 忽略 `*.exe`，当前 `scripts/` 下没有该文件，该约束文本已失效。
