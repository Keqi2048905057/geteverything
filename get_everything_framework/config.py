import os

from dotenv import load_dotenv

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_BASE_DIR, ".env"))


# ═══════════════════════════════════════════════════════════
# 系统配置 (敏感信息统一走 .env)
# ═══════════════════════════════════════════════════════════

class Config:
    """应用配置, 所有值从 .env 读取, 禁止硬编码"""

    # Flask 会话签名密钥。
    # 注意：这里**没有**默认值。历史版本默认 "dev-secret-key" 是固定弱密钥，
    # 属 P0 问题（方案第 1.1 节）。缺失或弱值时由 core.security.resolve_secret_key()
    # 生成进程级一次性密钥，并提示使用者写入 .env。
    SECRET_KEY = os.getenv("SECRET_KEY", "")

    # 本地 Web 服务（本机联调版硬性要求：默认只绑定 127.0.0.1，且关闭 debug）
    WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")
    WEB_PORT = int(os.getenv("WEB_PORT", "5000"))
    WEB_DEBUG = os.getenv("WEB_DEBUG", "false").lower() == "true"
    WEB_THREADS = int(os.getenv("WEB_THREADS", "8"))

    # 本地管理员 Token（M1 只在内存中生成并打印；M2 起落盘到 .env）
    LOCAL_ADMIN_TOKEN = os.getenv("LOCAL_ADMIN_TOKEN", "")

    # LLM / Agent
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "deepseek")
    LLM_MODEL_ID = os.getenv("LLM_MODEL_ID", "deepseek-chat")
    LLM_API_KEY = os.getenv("LLM_API_KEY", "")
    LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "60"))
    LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))
    LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0"))
    LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "1024"))
    LLM_JSON_MODE = os.getenv("LLM_JSON_MODE", "false").lower() == "true"

    # 外部搜索引擎 / API
    FOFA_BASE_URL = os.getenv("FOFA_BASE_URL", "https://fofa.info/api/v1/search/all")
    FOFA_EMAIL = os.getenv("FOFA_EMAIL", "")
    FOFA_KEY = os.getenv("FOFA_KEY", "")

    HUNTER_API_KEY = os.getenv("HUNTER_API_KEY", "")

    QUAKE_API_KEY = os.getenv("QUAKE_API_KEY", "")

    SHODAN_API_KEY = os.getenv("SHODAN_API_KEY", "")

    @classmethod
    def to_dict(cls, include_sensitive=True):
        """导出为字典, include_sensitive=False 时隐藏 API Key"""
        data = {
            "llm": {
                "provider": cls.LLM_PROVIDER,
                "model_id": cls.LLM_MODEL_ID,
                "api_key": cls._mask(cls.LLM_API_KEY) if include_sensitive else "",
                "base_url": cls.LLM_BASE_URL,
                "timeout": cls.LLM_TIMEOUT,
                "max_retries": cls.LLM_MAX_RETRIES,
                "temperature": cls.LLM_TEMPERATURE,
                "max_tokens": cls.LLM_MAX_TOKENS,
                "json_mode": cls.LLM_JSON_MODE,
            },
            "search": {
                "fofa_base_url": cls.FOFA_BASE_URL,
                "fofa_email": cls.FOFA_EMAIL,
                "fofa_key": cls._mask(cls.FOFA_KEY) if include_sensitive else "",
                "hunter_api_key": cls._mask(cls.HUNTER_API_KEY) if include_sensitive else "",
                "quake_api_key": cls._mask(cls.QUAKE_API_KEY) if include_sensitive else "",
                "shodan_api_key": cls._mask(cls.SHODAN_API_KEY) if include_sensitive else "",
            },
        }
        return data

    @staticmethod
    def _mask(value):
        """脱敏: 保留前4后4位, 中间用 * 替换"""
        if not value or len(value) <= 8:
            return value
        return value[:4] + "*" * (len(value) - 8) + value[-4:]


# ═══════════════════════════════════════════════════════════
# 项目路径
# ═══════════════════════════════════════════════════════════

OUTPUT_DIR = os.path.join(_BASE_DIR, "results")
os.makedirs(OUTPUT_DIR, exist_ok=True)

UPLOAD_DIR = os.path.join(_BASE_DIR, "uploads")
EXPORT_DIR = os.path.join(_BASE_DIR, "exports")
BACKUP_DIR = os.path.join(_BASE_DIR, "backups")

MAX_UPLOAD_SIZE = 2 * 1024 * 1024

# 旧扫描结果库：本机联调期间视为只读历史数据（storage.py 使用）。
# ``GEF_SCAN_DB_PATH`` 允许把它指向别处：跑验收或测试时既不必写仓库里的
# results/scan_results.db（AGENTS.md 硬约束：该库只读），又能和 Web 共用同一份。
SQLITE_CONFIG = {
    "path": os.getenv("GEF_SCAN_DB_PATH") or os.path.join(OUTPUT_DIR, "scan_results.db"),
}

# 本机应用库：承载 scopes / audit_events / uploads / jobs 等 M2、M3 实体。
LOCAL_DB_CONFIG = {
    "path": os.getenv("LOCAL_DB_PATH") or os.path.join(OUTPUT_DIR, "local.db"),
}

GO_BIN_WINDOWS = os.path.join(os.path.expanduser("~"), "go", "bin")
GO_BIN_POSIX = os.path.join(os.path.expanduser("~"), "go", "bin")

TARGET_CONFIG: dict = {
    # 默认目标必须为空：config.py 中的默认值曾指向外部域名，会在无参数调用时
    # 误扫外部资产。本机联调版要求目标全部由用户显式提供（方案第 2.3 节第 3 条）。
    "domains": [],
    "domain_file": None,
}

SCAN_CONFIG = {
    "enabled_runners": ["amass"],
}

# 工具执行与资源限制（方案第 8.3 节「本机测试建议」）。
# ``GEF_PROCESS_TIMEOUT`` 可覆盖单次子进程超时上限：本机联调时把它调小
# （例如 5 秒）可以快速验证「卡死的工具会被判 timeout 而不是拖死 worker」，
# 不需要真的等两分钟。
SCAN_LIMITS = {
    "process_timeout": int(os.getenv("GEF_PROCESS_TIMEOUT") or 120),   # 秒
    "max_concurrency": 2,
    "max_targets_per_job": 20,
    "max_output_bytes": 10 * 1024 * 1024,   # 10 MB
    "max_artifact_bytes": 2 * 1024 * 1024,  # 2 MB：单份 stdout/stderr 落盘上限
    "retry_count": 1,
}


# ═══════════════════════════════════════════════════════════
# 工具配置工厂
# ═══════════════════════════════════════════════════════════

def build_tool_config(path, category, **kwargs):
    """统一创建工具配置，自动注入 process_timeout 和 extra_args"""
    config = {
        "path": path,
        "category": category,
        "process_timeout": 300,
        "extra_args": [],
    }
    config.update(kwargs)
    return config


# ═══════════════════════════════════════════════════════════
# 子域名收集
# ═══════════════════════════════════════════════════════════

AMASS_CONFIG = build_tool_config("amass", "subdomain")

AMASS_INTEL_CONFIG = build_tool_config(
    "amass", "subdomain",
    timeout=120,
)

SUBFINDER_CONFIG = build_tool_config(
    "subfinder", "subdomain",
    threads=50,
    timeout=10,
    silent=True,
)

ASSETFINDER_CONFIG = build_tool_config(
    "assetfinder", "subdomain",
    subs_only=True,
)

SHUFFLEDNS_CONFIG = build_tool_config(
    "shuffledns", "subdomain",
    # 相对路径按项目根（``_BASE_DIR``）解析，不按当前工作目录；
    # 换机器时用 ``SHUFFLEDNS_WORDLIST`` 指向本机已有的字典即可。
    wordlist=os.getenv("SHUFFLEDNS_WORDLIST", "SecLists/subdomains-top1million-5000.txt"),
)

ALTERX_CONFIG = build_tool_config("alterx", "subdomain")

ONEFORALL_CONFIG = build_tool_config(
    "oneforall", "subdomain",
    target_flag="--target",
    run_args=["run"],
)

ENSCAN_CONFIG = build_tool_config("enscan", "subdomain")


# ═══════════════════════════════════════════════════════════
# 存活探测 / Web 探测
# ═══════════════════════════════════════════════════════════

DNSX_CONFIG = build_tool_config(
    "dnsx", "alive",
    threads=50,
    silent=True,
    resp_only=True,
)

HTTPX_CONFIG = build_tool_config(
    os.getenv("HTTPX_PATH", "http-x"), "web",
    threads=50,
    silent=True,
    title=True,
    status_code=True,
    tech_detect=False,
    follow_redirects=False,
    timeout=10,
    process_timeout=300,
)


# ═══════════════════════════════════════════════════════════
# URL 发现 / 爬虫
# ═══════════════════════════════════════════════════════════

GOSPIDER_CONFIG = build_tool_config("gospider", "url", depth=2)

KATANA_CONFIG = build_tool_config("katana", "url", depth=2)

WAYBACKURLS_CONFIG = build_tool_config("waybackurls", "url")


# ═══════════════════════════════════════════════════════════
# 目录扫描
# ═══════════════════════════════════════════════════════════

FEROXBUSTER_CONFIG = build_tool_config(
    "feroxbuster", "url",
    # 历史值是开发机绝对路径 ``D:/c4/v2/.../raft-small-directories.txt``，
    # 换机器必然失败（PROJECT_STATE.md Known Failure #5）。改为仓库相对路径，
    # 并用 ``FEROXBUSTER_WORDLIST`` 允许指向本机已有字典。
    wordlist=os.getenv("FEROXBUSTER_WORDLIST", "SecLists/raft-small-directories.txt"),
    json_output=True,
)

DIRSEARCH_CONFIG = build_tool_config(
    "dirsearch", "url",
    wordlist=None,
    json_output=True,
)


# ═══════════════════════════════════════════════════════════
# 端口扫描
# ═══════════════════════════════════════════════════════════

NAABU_CONFIG = build_tool_config("naabu", "port", silent=True)

NMAP_CONFIG = build_tool_config("nmap", "port", ports=None)


# ═══════════════════════════════════════════════════════════
# 工具分类速查表
# ═══════════════════════════════════════════════════════════

TOOL_CATEGORIES = {
    "amass": "subdomain",
    "amass_intel": "subdomain",
    "subfinder": "subdomain",
    "assetfinder": "subdomain",
    "shuffledns": "subdomain",
    "alterx": "subdomain",
    "oneforall": "subdomain",
    "enscan": "subdomain",
    "dnsx": "alive",
    "httpx": "web",
    "gospider": "url",
    "katana": "url",
    "waybackurls": "url",
    "feroxbuster": "url",
    "dirsearch": "url",
    "naabu": "port",
    "nmap": "port",
}

TOOL_COMMANDS = {
    t: c["path"]
    for t, c in [
        ("subfinder", SUBFINDER_CONFIG),
        ("dnsx", DNSX_CONFIG),
        ("httpx", HTTPX_CONFIG),
        ("naabu", NAABU_CONFIG),
        ("nmap", NMAP_CONFIG),
        ("katana", KATANA_CONFIG),
        ("gospider", GOSPIDER_CONFIG),
        ("waybackurls", WAYBACKURLS_CONFIG),
        ("feroxbuster", FEROXBUSTER_CONFIG),
        ("dirsearch", DIRSEARCH_CONFIG),
        ("oneforall", ONEFORALL_CONFIG),
        ("enscan", ENSCAN_CONFIG),
    ]
}

DEFAULT_PASSIVE_TOOLS = ["enscan", "oneforall"]
DEFAULT_SUBDOMAIN_TOOLS = ["subfinder", "oneforall"]
DEFAULT_WEB_PROBE_TOOLS = ["httpx"]
DEFAULT_CONTENT_DISCOVERY_TOOLS = ["dirsearch", "feroxbuster"]
DEFAULT_PORT_SCAN_TOOLS = ["naabu"]
