# 资产收集框架 (Asset Collection Framework)

> 一站式子域名 / 端口 / URL 收集与探测框架，集成 20+ 款主流安全工具，提供 **Web API** 入口与 **LLM Agent** 增强

![Python](https://img.shields.io/badge/Python-3.10%2B-blue) ![Flask](https://img.shields.io/badge/Flask-3.x-green) ![License](https://img.shields.io/badge/License-MIT-orange)

---

## 📑 目录

- [项目简介](#-项目简介)
- [核心特性](#-核心特性)
- [快速部署](#-快速部署)
- [运行方式](#-运行方式)
- [API 接口](#-api-接口)
- [侦察流程](#-侦察流程)
- [目录结构](#-目录结构)
- [模块说明](#-模块说明)
- [支持的工具](#-支持的工具)
- [配置说明](#-配置说明)
- [常见问题](#-常见问题)

---

## 🎯 项目简介

本框架是一个**模块化的资产收集与侦察平台**，整合了 20+ 款主流安全工具（subfinder、amass、httpx、nmap、katana 等），为渗透测试和红队评估提供从 **根域名发现** 到 **URL 漏洞扫描** 的完整工作流。

- **Web API 优先**：所有功能通过 Flask RESTful 接口暴露，前端解耦
- **数据持久化**：所有扫描结果落地 SQLite，支持增量更新与历史回溯
- **模块化设计**：每个工具独立封装为 Runner，可单独调用或组合编排
- **LLM Agent 增强**：内置 Agent 解释器，支持自然语言驱动扫描任务

---

## ✨ 核心特性

| 特性 | 说明 |
|------|------|
| 🚀 **一键部署** | 提供 Windows / Linux 自动安装脚本，Go 工具、Python 依赖、系统工具一站搞定 |
| 🔧 **20+ 工具集成** | subfinder / amass / dnsx / httpx / nmap / naabu / katana / gospider / waybackurls / dirsearch / feroxbuster / ENScan ... |
| 📊 **统一数据存储** | SQLite 数据库 + JSONL / TXT 多格式输出，跨工具结果自动合并去重 |
| 🌐 **Web API** | 12 个 RESTful 接口：`/api/tools`、`/api/scan`、`/api/results`、`/api/upload` ... |
| 🤖 **LLM Agent** | DeepSeek 等大模型接入，可自然语言描述扫描任务 |
| 📤 **多格式导出** | 支持按域名 / 工具 / 分类导出 CSV / JSON |
| 🎯 **目标管理** | 支持手动输入 + 批量导入 + 配置文件管理 |
| 🔌 **配置面板** | Web 端可改 LLM API Key / FOFA / Hunter / Quake / Shodan / ENScan Cookie |

---

## 🚀 快速部署

项目提供 **Windows** 和 **Linux** 两套自动安装脚本，自动安装 Go、Python 依赖、系统工具以及本框架依赖的全部安全工具。

### Linux / WSL (Bash)

```bash
# 完整安装
bash scripts/install_linux.sh

# 仅检查环境（不安装）
bash scripts/install_linux.sh --check-only

# 同时安装可选工具（feroxbuster、dirsearch）
bash scripts/install_linux.sh --with-optional
```

### Windows (PowerShell)

```powershell
# 完整安装
powershell -ExecutionPolicy Bypass -File scripts/install_windows.ps1

# 仅检查环境
powershell -ExecutionPolicy Bypass -File scripts/install_windows.ps1 -CheckOnly

# 同时安装可选工具
powershell -ExecutionPolicy Bypass -File scripts/install_windows.ps1 -WithOptional
```

### 安装说明

| 步骤 | 内容 |
|------|------|
| 1️⃣ **系统工具** | Windows 通过 `winget` 安装 Python / Go / Git / Nmap / Amass；Linux 识别 `apt/dnf/yum/pacman/zypper/apk` 安装对应包 |
| 2️⃣ **Go 工具链** | `subfinder` `dnsx` `httpx` `naabu` `katana` `alterx` `shuffledns` `assetfinder` `gospider` `waybackurls` 通过 `go install` 编译安装 |
| 3️⃣ **可选工具** | `feroxbuster`（Windows 从 GitHub Release 下载）和 `dirsearch` 需显式开启 `--with-optional` |
| 4️⃣ **Amass** | Linux 下从 GitHub Release 下载 `amass_Linux_amd64.zip`（不依赖 snap） |

> ⚠️ 安装完成后如命令找不到，请重开终端。脚本会把 Go 的 bin 目录加入当前会话并尝试写入用户 PATH。

### Python 依赖

```bash
pip install -r requirement.txt
```

最低依赖（不含可选库）：`Flask` `python-dotenv` `openai` `openpyxl` `tqdm` `httpx`

---

## 💻 运行方式

### 启动 Web 服务

```bash
python app.py
# 默认监听 http://127.0.0.1:5000
```

启动后访问 `http://127.0.0.1:5000/` 即可使用前端页面（需在 `web/templates/index.html` 部署前端模板）。

### LLM Agent CLI 模式

```bash
python agent_cli.py
# 进入 REPL 多轮对话模式,输入 quit/exit 退出
```

### 健康检查

```bash
# 检查服务是否正常
curl http://127.0.0.1:5000/api/tools
```

---

## 🔌 API 接口

所有接口统一前缀 `/api`，数据格式 JSON。完整文档可调用 `GET /api/tools` 自行探查。

**鉴权现状（重要）**：除 `/health` 外，**修改/执行类**接口都要求本地管理员
身份（请求头 `X-Local-Token: <Token>`，或先 `POST /api/auth/login` 建立会话）。
以下三个**只读**接口目前仍然**匿名可读**，这是有意保持的现状，属已知项：
`GET /api/tools`、`GET /api/results`、`GET /api/export`。
它们的契约由 `tests/integration/test_export_contract.py` 锁定；若要收口鉴权，
需先改动该测试并同步 `SECURITY.md`。

### 工具与数据库

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/api/tools` | 匿名可读（已知项） | 列出所有可用扫描工具及其数据库信息 |
| GET | `/api/databases` | 匿名可读（已知项） | 列出所有工具数据库表的元信息 |

### 扫描执行

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | `/api/run` | 需登录 | 批量扫描（多工具编排，目标用 `domain` 或受控 `upload_id`） |
| POST | `/api/tool/<tool_name>/run` | 需登录 | 单工具扫描（指定目标域名） |

> `file_path` 已废弃：传任意服务器路径一律 400，必须先用 `POST /api/upload`
> 拿到受控 `upload_id`。

### 任务（Job，M3 起为异步执行）

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | `/api/jobs` | 需登录 | 创建任务（必填 `scope_id`，目标须在 Scope 内；可选 `idempotency_key`） |
| GET | `/api/jobs` | 需登录 | 任务列表 |
| GET | `/api/jobs/<job_id>` | 需登录 | 任务详情（含进度与错误码） |
| POST | `/api/jobs/<job_id>/cancel` | 需登录 | 请求取消 |
| POST | `/api/jobs/<job_id>/retry` | 需登录 | 重试（interrupted / failed）；返回 `next_attempt_at`（退避窗口） |
| GET | `/api/jobs/<job_id>/steps` | 需登录 | 步骤与结构化观测 |
| GET | `/api/jobs/<job_id>/events` | 需登录 | 任务事件流 |
| GET | `/api/jobs/<job_id>/artifacts` | 需登录 | 原始证据登记（不含服务器路径） |
| GET | `/api/artifacts/<artifact_id>` | 需登录 | 读取**截断 + 脱敏**后的证据文本 |

> **幂等（P0-7a）**：带 `idempotency_key` 重复 `POST /api/jobs` 时，只要上一个同键任务
> **还没终结**（`queued` / `running`），就返回**同一个** `job_id` 且响应里 `reused=true`。
> 键的语义是「防重复提交」，**不是「永久只跑一次」**——任务落到终态后同一个键可以再次创建。
>
> **重试退避（P0-7b）**：`retry` 后任务立刻回到 `queued`，但要等 `next_attempt_at`
> 之后 worker 才会领它（5 / 10 / 20 / 40… 秒，封顶 300 秒）。所以「排队中却迟迟不执行」
> 通常是退避窗口未到，任务详情页会显示「最早可重试」。

### 结果查询与导出

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/api/results` | 匿名可读（已知项） | 通用结果查询（domain/tool/category 多维过滤） |
| GET | `/api/tool/<tool_name>/results` | 匿名可读（已知项） | 单工具专属表查询 |
| GET | `/api/export` | 匿名可读（已知项） | 生成导出文件，返回 `export_id` + `download_url`（**不返回服务器路径**） |
| GET | `/api/export/<export_id>/download` | 匿名可读（已知项） | 下载导出文件（流式，`Content-Disposition: attachment`） |
| GET | `/api/exports` | 匿名可读（已知项） | 导出记录列表（可追溯：格式 / 行数 / 大小 / sha256） |

### 配置管理

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/api/settings` | 需登录 | 读取系统配置（API Key 脱敏） |
| POST | `/api/settings` | 需登录 | 保存配置到 `.env` |
| GET | `/api/settings/enscan` | 需登录 | 读取 ENScan 数据源 Cookie |
| POST | `/api/settings/enscan` | 需登录 | 保存 ENScan Cookie 到 `config.yaml` |

### 授权范围（Scope）

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | `/api/scopes` | 需登录 | 创建授权范围（域名 / CIDR / 排除项 / `active_scan`） |
| GET | `/api/scopes` | 需登录 | 列出授权范围 |
| GET | `/api/scopes/<scope_id>` | 需登录 | 查看单个授权范围 |

### 目标管理

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | `/api/upload` | 需登录 | 上传目标文件（`.txt`/`.csv`/`.xlsx`/`.json`），返回受控 `upload_id` |

### 调用示例

**创建授权范围（Scope）：**
```bash
curl -X POST http://127.0.0.1:5000/api/scopes \
  -H "Content-Type: application/json" \
  -H "X-Local-Token: <你的 Token>" \
  -d '{"name":"本地测试范围","allowed_domains":["example.test"],"active_scan":false}'
```

**批量扫描：**
```bash
curl -X POST http://127.0.0.1:5000/api/run \
  -H "Content-Type: application/json" \
  -H "X-Local-Token: <你的 Token>" \
  -d '{"domain": "example.test", "tools": ["subfinder", "dnsx"], "scope_id": "<scope_id>"}'
```

**结果查询：**
```bash
curl "http://127.0.0.1:5000/api/results?domain=example.test&category=subdomain&limit=100"
```

**目标文件上传 + 扫描（受控 upload_id，不再有 file_path）：**
```bash
# 1. 上传，返回 {"upload_id": "upload_<uuid4hex>", "target_count": N, ...}
curl -H "X-Local-Token: <你的 Token>" -F "file=@targets.txt" http://127.0.0.1:5000/api/upload

# 2. 用 upload_id 创建任务
curl -X POST http://127.0.0.1:5000/api/jobs \
  -H "Content-Type: application/json" \
  -H "X-Local-Token: <你的 Token>" \
  -d '{"scope_id": "<scope_id>", "upload_id": "upload_<uuid4hex>", "tools": ["subfinder"], "mode": "mock"}'
```

**导出并下载（响应里没有服务器路径）：**
```bash
# 1. 生成导出：{"export_id": "exp_...", "download_url": "/api/export/exp_.../download", ...}
curl "http://127.0.0.1:5000/api/export?format=csv&domain=example.test"

# 2. 直接下载
curl -OJ "http://127.0.0.1:5000/api/export/exp_<...>/download"
```

---

## 🔍 侦察流程

### 公司信息收集

#### 企业信息收集

- **ENScan_GO**（推荐）：[ENScan_GO](https://github.com/wgpsec/ENScan_GO.git) 自动化收集企业基本信息、ICP 备案、微信小程序、公众号、App 信息、软件著作权、招聘信息、控股企业
- **手动收集**：
  - 小蓝本：https://www.xiaolanben.com/
  - 爱企查：https://www.aiqicha.com/
  - 天眼查：https://www.tianyancha.com/

#### 根域名收集

1. **Amass intel** — ASN 区域号收集
2. **Google Hacking** — 谷歌语法生成
3. **CRT** — https://crt.sh/ 证书透明度核查
4. **Reverse Whois** — https://www.whoxy.com/reverse-whois/ whois 反查
5. **Shodan / Fofa / Hunter** — 网络空间搜索引擎语法生成
6. **根域名合并** — 去重、规范化
7. **攻击面建模** — 汇总到数据库

#### 云资产收集

> 通过云厂商 API + bucket 命名规则枚举 OSS / S3 / COS 等云存储桶（详见 `modules/enscan.py`）

---

### 子域名收集模块

#### 子域名枚举

| 工具 | 类别 | 说明 |
|------|------|------|
| **subfinder** | 被动枚举 | 多数据源聚合（证书、DNS 数据库、API） |
| **assetfinder** | 被动枚举 | 公开数据源 + 正则规范化 |
| **amass** | 主动 + 被动 | 深度枚举，含 ASN 关联 |
| **amass_intel** | 主动 + 被动 | Amass ASN 区域号收集 |
| **one-for-all** | 主动枚举 | 综合 Python 工具 |
| **enscan** | 主动 + 被动 | 企业信息收集 + 根域名提取 |

#### 子域名爆破

| 工具 | 说明 |
|------|------|
| **shuffledns** | DNS 字典爆破（需配合 resolvers） |
| **alterx** | 基于已知子域生成变体字典 |
| **SecLists** | 字典库（**需自行下载**，见下方说明） |

> **字典不再随仓库分发。** 早期版本把 `SecLists/` 直接提交进了 Git，同一个单词表
> 会在每次改动时重复占用仓库体积。请从
> <https://github.com/danielmiessler/SecLists> 下载后放到 `SecLists/` 目录，
> 或把 `config.py` 里各工具的 `wordlist` 指向你本机已有的字典。
> `.gitignore` 已忽略该目录，下载后不会误提交。

字典路径有两种合法形态，**都按项目根解析**（与启动时的当前工作目录无关）：

| 方式 | 写法 | 适用 |
|------|------|------|
| 仓库相对路径 | `wordlist="SecLists/raft-small-directories.txt"`（默认值） | 字典就放在仓库的 `SecLists/` 下 |
| 环境变量覆盖 | `SHUFFLEDNS_WORDLIST=D:\dicts\subdomains.txt` | 字典在别处，且不想改 `config.py` |

`dirsearch` 的 `wordlist` 允许为 `None`：此时不加 `-w`，由工具使用自带字典。
**配置了字典但文件不存在时会直接失败**（`error_code=config_error`），不会再像历史版本那样
打完一行提示就返回空结果 —— 「字典没配好」和「跑通但零结果」必须能分开。

#### 爬虫收集

- **gospider** — 快速 Web 爬虫
- **katana** — Next-gen 爬虫，支持 JS 渲染

#### 清洗 / 存活探测

1. **合并去重** — 多源子域结果合并
2. **dnsx** — DNS 批量解析验证（CNAME / A 记录）
3. **httpx** — HTTP/HTTPS 存活探测 + 标题 / 状态码 / 指纹
4. **observer_ward** — 指纹识别（可选）

#### 端口扫描

- **naabu** — 快速 SYN 端口扫描
- **nmap** — 深度服务版本探测

---

### URL 分析模块

#### URL 发现

| 工具 | 类型 | 说明 |
|------|------|------|
| **katana** | 爬虫 | 现代 Web 爬虫 |
| **gospider** | 爬虫 | 多线程爬虫 |
| **waybackurls** | 历史 URL | 从 Wayback Machine 提取历史 URL |
| **feroxbuster** | 目录爆破 | 递归目录扫描（可选） |
| **dirsearch** | 目录扫描 | 经典目录扫描（可选） |

#### 漏洞扫描

- **nuclei** — 模板化自动化漏洞扫描（可通过 LLM Agent 调用）

---

## 📂 目录结构

```text
framework-main/
├── app.py                    # Flask Web 入口 + Blueprint 注册
├── config.py                 # 全局配置（从 .env 读取）+ 工具参数
├── target_parser.py          # 目标解析（.txt/.csv/.xlsx/.json）
├── tool_runner.py            # 工具调度执行器
├── storage.py                # SQLite 数据库操作层（ScanResultStore）
├── exporter.py               # 结果导出（CSV/JSON）
├── agent_cli.py              # LLM Agent CLI 入口
├── requirement.txt           # Python 依赖锁定
│
├── api/                      # Flask RESTful API
│   ├── __init__.py           # Blueprint 注册
│   ├── tools.py              # /api/tools, /api/databases
│   ├── scan.py               # /api/run, /api/tool/<name>/run
│   ├── results.py            # /api/results, /api/tool/<name>/results, /api/export
│   ├── upload.py             # /api/upload (目标文件上传)
│   └── settings.py           # /api/settings, /api/settings/enscan
│
├── modules/                  # 工具封装模块
│   ├── base.py               # 扫描器基类
│   ├── registry.py           # 扫描器注册中心
│   ├── subfinder.py          # 被动子域枚举
│   ├── assetfinder.py        # 公开数据源查找
│   ├── amass.py              # 深度子域枚举 + intel
│   ├── oneforall.py          # 综合 Python 工具
│   ├── alterx.py             # 变体字典生成
│   ├── shuffledns.py         # DNS 字典爆破
│   ├── dnsx.py               # DNS 批量解析验证
│   ├── httpx.py              # HTTP 存活探测 + 指纹
│   ├── port_tools.py         # 端口扫描器合集（Naabu + Nmap）
│   ├── naabu.py              # 入口重导出
│   ├── nmap.py               # 入口重导出
│   ├── gospider.py           # Web 爬虫
│   ├── katana.py             # 现代爬虫
│   ├── waybackurls.py        # 历史 URL 提取
│   ├── url_tools.py          # URL 处理工具集
│   ├── dirsearch.py          # 目录扫描
│   ├── feroxbuster.py        # 递归目录扫描
│   └── enscan.py             # 企业信息收集
│
├── agent/                    # LLM Agent 模块
│
├── web/                      # 前端模板（由开发者自行填充）
│   └── templates/
│       └── index.html        # 占位文件，需部署 dashboard
│
├── static/                   # Flask 静态资源（CSS/JS）
│
├── results/                  # 扫描结果（数据库 + 原始输出）
├── uploads/                  # 目标上传文件
├── exports/                  # 导出文件
│
├── scripts/                  # 安装脚本
│   ├── install_linux.sh
│   ├── install_windows.ps1
│   └── run_local.ps1         # 本机联调版一键拉起 Web + worker
│                             # （可选工具二进制请用安装脚本获取，不入库）
│
├── SecLists/                 # 字典库（自行下载，不入库）
└── venv/                     # Python 虚拟环境
```

---

## 📦 模块说明

### 根目录文件

| 文件 | 说明 |
|------|------|
| `app.py` | Flask 应用入口，注册 Blueprint，启动 Web 服务；`/` 路由渲染前端模板 |
| `config.py` | 全局配置：`Config` 类（LLM / Flask / API Key）+ 工具配置工厂 + 路径常量 |
| `tool_runner.py` | 工具调度核心：加载目标、加载工具、运行、保存结果 |
| `storage.py` | SQLite 数据访问层（`ScanResultStore`），含 23+ 个查询方法 |
| `exporter.py` | 导出扫描结果到 CSV / JSON / TXT |
| `target_parser.py` | 解析目标输入（.txt / .csv / .xlsx / .json 四种格式） |
| `agent_cli.py` | LLM Agent 终端 REPL 入口 |
| `requirement.txt` | Python 依赖锁定 |
| `README.md` | 本文档 |

### `api/` 目录（Web API）

| 文件 | 路由 | 说明 |
|------|------|------|
| `__init__.py` | - | Blueprint 注册入口 |
| `tools.py` | `/api/tools`, `/api/databases` | 工具列表、数据库元信息 |
| `scan.py` | `/api/run`, `/api/tool/<name>/run` | 扫描执行（全量 / 单工具） |
| `results.py` | `/api/results`, `/api/tool/<name>/results`, `/api/export` | 结果查询与导出 |
| `upload.py` | `/api/upload` | 目标文件上传（4 种格式） |
| `settings.py` | `/api/settings`, `/api/settings/enscan` | 系统配置 + ENScan Cookie |

### `modules/` 目录（工具封装）

每个工具对应一个 `*Runner` 类，继承自 `BaseRunner`（`modules/base.py`）：

| 模块 | Runner 类 | 分类 | 功能 |
|------|-----------|------|------|
| `base.py` | `BaseRunner` | - | 基类：命令解析、执行、读写、临时文件管理 |
| `subfinder.py` | `SubfinderRunner` | subdomain | 被动子域枚举 |
| `assetfinder.py` | `AssetfinderRunner` | subdomain | 公开数据源 + 正则规范化 |
| `amass.py` | `AmassRunner` / `AmassIntelRunner` | subdomain | 深度枚举 + ASN 收集 |
| `oneforall.py` | `OneForAllRunner` | subdomain | 综合 Python 工具 |
| `alterx.py` | `AlterxRunner` | subdomain | 变体字典生成 |
| `shuffledns.py` | `ShufflednsRunner` | subdomain | DNS 字典爆破 |
| `dnsx.py` | `DnsxRunner` | alive | DNS 批量解析验证 |
| `httpx.py` | `HttpxRunner` | web | HTTP 存活探测 + 指纹（JSONL） |
| `port_tools.py` | `NaabuRunner` / `NmapRunner` | port | 端口 / 服务扫描 |
| `naabu.py` | - | - | 重导出 `port_tools.NaabuRunner` |
| `nmap.py` | - | - | 重导出 `port_tools.NmapRunner` |
| `gospider.py` | `GospiderRunner` | url | Web 爬虫 |
| `katana.py` | `KatanaRunner` | url | 现代爬虫 |
| `waybackurls.py` | `WaybackurlsRunner` | url | 历史 URL 提取 |
| `url_tools.py` | - | - | URL 处理工具集 |
| `dirsearch.py` | `DirsearchRunner` | url | 目录扫描 |
| `feroxbuster.py` | `FeroxbusterRunner` | url | 递归目录扫描 |
| `enscan.py` | `ENScanRunner` | subdomain | 企业信息收集 |
| `registry.py` | - | - | Runner 注册中心（17 个工具） |

### `results/` 目录

- `scan_results.db` — SQLite 数据库，保存所有扫描记录
- `*_subfinder.txt` / `*_amass.txt` — 工具原始输出
- `tmp*_httpx_input.txt` — 临时输入文件（任务结束自动删除）

---

## 🛠️ 支持的工具

| 分类 | 工具 |
|------|------|
| **企业信息 / 根域** | ENScan_GO, amass_intel |
| **子域枚举** | subfinder, assetfinder, amass, one-for-all |
| **子域爆破** | shuffledns, alterx |
| **字典** | SecLists（自行下载，不随仓库分发） |
| **DNS 探测** | dnsx |
| **HTTP 探测** | httpx, observer_ward |
| **端口扫描** | naabu, nmap |
| **爬虫** | gospider, katana |
| **目录扫描** | dirsearch, feroxbuster |
| **历史 URL** | waybackurls |
| **漏洞扫描** | nuclei（通过 LLM Agent） |

---

## ⚙️ 配置说明

所有配置通过项目根目录的 **`.env`** 文件管理（参考 `config.py`）：

```ini
# Flask
SECRET_KEY=your-secret-key

# LLM / Agent
LLM_PROVIDER=deepseek
LLM_MODEL_ID=deepseek-chat
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_TIMEOUT=60
LLM_MAX_TOKENS=1024

# 外部搜索引擎 API
FOFA_BASE_URL=https://fofa.info/api/v1/search/all
FOFA_EMAIL=xxx@xxx.com
FOFA_KEY=xxx

HUNTER_API_KEY=xxx
QUAKE_API_KEY=xxx
SHODAN_API_KEY=xxx
```

ENScan 的数据源 Cookie 通过 Web 面板的"设置"页写入，存到 `~/.config/enscan/config.yaml`。

> ⚠️ **禁止**将 `.env` 提交到 Git，仓库已添加 `.gitignore` 默认忽略。

---

## ❓ 常见问题

**Q1: 安装后命令找不到？**
请重开终端让 PATH 生效。Linux 脚本会写入 `~/.bashrc` / `~/.zshrc`；Windows 脚本会更新用户 PATH。

**Q2: Windows 下 feroxbuster 安装失败？**
脚本会从 GitHub Release 下载 `x86_64-windows-feroxbuster.exe.zip`，如失败可手动下载放入 Go bin 目录。

**Q3: 数据库文件在哪儿？**
`results/scan_results.db`，可通过 `GET /api/databases` 查看表清单。

**Q4: 如何新增自定义工具？**
1. 在 `modules/` 下新建 `your_tool.py`，继承 `BaseRunner`
2. 实现 `run_scan(domain)` 和结果解析方法
3. 在 `modules/registry.py` 的 `RUNNER_REGISTRY` 中注册
4. 在 `config.py` 添加对应的 `*_CONFIG` 配置块

**Q5: LLM Agent 怎么用？**
- Web 模式：通过前端"对话"标签页使用
- CLI 模式：`python agent_cli.py`，自然语言描述任务即可

**Q6: 启动后访问 `/` 报 TemplateNotFound？**
当前 `web/templates/index.html` 是占位文件。前端开发者需要把 dashboard 页面放进去，或在 `app.py` 中改为纯 API 模式。

**Q7: 缺 `python-dotenv` 模块？**
`pip install -r requirement.txt` 即可，或单独 `pip install python-dotenv`。

---

## 📜 License

MIT License — 仅供合法安全测试与研究使用。

---

## 🙏 致谢

本框架集成的所有第三方工具归原作者所有，详见各项目 GitHub 仓库。
