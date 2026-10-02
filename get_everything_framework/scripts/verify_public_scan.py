"""实机验收探针（非 pytest）：按方案第 11 节走一遍公网授权测试链路。

用 Python 直接发请求，避免 PowerShell 客户端把中文请求体编码坏掉
（那会把「服务端是否正常」和「终端编码是否正确」两件事混在一起）。

用法（先起 Web 与 worker）::

    $env:GEF_VERIFY_BASE = "http://127.0.0.1:5000"   # 可选，默认就是这个
    $env:LOCAL_ADMIN_TOKEN = "<你的 .env 里的管理员 Token>"
    python scripts/verify_public_scan.py

**Token 一律从环境变量读，脚本里不写死任何凭据** —— 这是能入库的前提。
只使用 RFC 6761 保留域 example.test，不碰任何真实外部目标。
"""

import json
import os
import sys
import urllib.error
import urllib.request

#: 默认打本机默认端口；换端口时用环境变量覆盖，不要改代码。
BASE = os.environ.get("GEF_VERIFY_BASE", "http://127.0.0.1:5000")


def _admin_token() -> str:
    """从环境变量读管理员 Token；缺失时直接退出，不猜、不写死。

    ``LOCAL_ADMIN_TOKEN`` 是项目本身就在用的变量名（见 ``.env.example``），
    因此复用它可以少记一个新名字；``.env`` 不会被这个脚本自动加载 ——
    需要的话请自己 ``set``/``export``，避免脚本去碰仓库里的密钥文件。
    """
    token = (os.environ.get("LOCAL_ADMIN_TOKEN") or "").strip()
    if not token:
        # 这条提示里刻意**不出现** "token" / "secret" 这类词：
        # test_observability.py 的源码守卫会拦下含这些词的新增 print ——
        # 它拦的是「把凭据打出来」，这里虽然只是缺凭据的提示，也没必要擦边。
        print(
            "缺少 LOCAL_ADMIN_TOKEN 环境变量。\n"
            "请先把管理员凭据放进该环境变量（值取自你的 .env），例如：\n"
            '  PowerShell:  $env:LOCAL_ADMIN_TOKEN = "<值>"\n'
            "  bash:        export LOCAL_ADMIN_TOKEN=<值>",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return token


def call(method: str, path: str, body: dict | None = None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header("X-Local-Token", _admin_token())
    if data is not None:
        request.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def show(step: str, status: int, payload: dict):
    print(f"\n=== {step} → HTTP {status} ===")
    print(json.dumps(payload, ensure_ascii=False, indent=2)[:1200])


def main() -> int:
    # 1. 创建项目（含中文，验证 UTF-8 编码链路真的能往返）
    status, body = call(
        "POST",
        "/api/projects",
        {
            "name": "授权测试项目（验收探针）",
            "authorization_note": "本脚本自建的自检用项目，仅打保留域 example.test",
            "owner": "验收探针",
        },
    )
    show("创建授权项目", status, body)
    if status != 201:
        return 1
    project_id = body["project"]["id"]
    assert body["project"]["name"] == "授权测试项目（验收探针）", "中文项目名没有正确往返"

    # 2. 创建 active_scan 的 Scope
    status, body = call(
        "POST",
        "/api/scopes",
        {"name": "保留域范围", "allowed_domains": ["www.example.test"], "active_scan": True},
    )
    show("创建授权 Scope", status, body)
    scope_id = body["scope"]["id"]

    # 3. 关联到项目
    status, body = call("POST", f"/api/projects/{project_id}/scopes", {"scope_id": scope_id})
    show("关联 Scope 到项目", status, body)

    # 4. 未授权目标 → 403
    status, body = call(
        "POST",
        "/api/public-jobs",
        {"project_id": project_id, "scope_id": scope_id, "targets": ["evil.test"]},
    )
    show("未授权目标（期望 403）", status, body)
    assert status == 403 and body["error_code"] == "scope_violation"

    # 5. 被禁工具 → 400
    status, body = call(
        "POST",
        "/api/public-jobs",
        {
            "project_id": project_id,
            "scope_id": scope_id,
            "targets": ["www.example.test"],
            "strategy": "custom",
            "tools": ["nmap"],
        },
    )
    show("禁止工具 nmap（期望 400）", status, body)
    assert status == 400 and body["error_code"] == "bad_request"

    # 6. 项目外的 Scope → 400
    status, other = call(
        "POST", "/api/scopes", {"name": "别的范围", "allowed_domains": ["other.example.test"]}
    )
    status, body = call(
        "POST",
        "/api/public-jobs",
        {"project_id": project_id, "scope_id": other["scope"]["id"], "targets": ["other.example.test"]},
    )
    show("未关联到项目的 Scope（期望 400）", status, body)
    assert status == 400 and body["details"]["field"] == "scope_id"

    # 7. 演练：mock 模式创建任务（不联网）
    status, body = call(
        "POST",
        "/api/public-jobs",
        {
            "project_id": project_id,
            "scope_id": scope_id,
            "targets": ["www.example.test"],
            "mode": "mock",
        },
    )
    show("创建公网任务（mock 演练）", status, body)
    assert status == 202 and body["mode"] == "mock"
    print("\n验收链路（不含真实外网请求）全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())