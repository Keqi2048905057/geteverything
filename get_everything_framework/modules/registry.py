from .alterx import AlterxRunner
from .amass import AmassIntelRunner, AmassRunner
from .assetfinder import AssetfinderRunner
from .dirsearch import DirsearchRunner
from .dnsx import DnsxRunner
from .enscan import ENScanRunner
from .feroxbuster import FeroxbusterRunner
from .gospider import GospiderRunner
from .httpx import HttpxRunner
from .katana import KatanaRunner
from .naabu import NaabuRunner
from .nmap import NmapRunner
from .oneforall import OneForAllRunner
from .shuffledns import ShufflednsRunner
from .subfinder import SubfinderRunner
from .waybackurls import WaybackurlsRunner


RUNNER_REGISTRY = {
    "alterx": AlterxRunner,
    "amass": AmassRunner,
    "amass_intel": AmassIntelRunner,
    "assetfinder": AssetfinderRunner,
    "dirsearch": DirsearchRunner,
    "dnsx": DnsxRunner,
    "enscan": ENScanRunner,
    "feroxbuster": FeroxbusterRunner,
    "gospider": GospiderRunner,
    "httpx": HttpxRunner,
    "katana": KatanaRunner,
    "naabu": NaabuRunner,
    "nmap": NmapRunner,
    "oneforall": OneForAllRunner,
    "shuffledns": ShufflednsRunner,
    "subfinder": SubfinderRunner,
    "waybackurls": WaybackurlsRunner,
}


def get_supported_runners():
    return sorted(RUNNER_REGISTRY.keys())


def build_runner(tool_name):
    runner_cls = RUNNER_REGISTRY.get(tool_name)
    if not runner_cls:
        raise ValueError(f"不支持的收集器: {tool_name}")
    return runner_cls()
