"""实机验收探针（非 pytest）：走一遍测试阶段的扫描链路。

用 Python 直接发请求，避免 PowerShell 客户端把中文请求体编码坏掉
（那会把「服务端是否正常」和「终端编码是否正确」两件事混在一起）。

用法（先起 Web 与 worker）::

    $env:GEF_VERIFY_BASE = "http://127.0.0.1:5000"   # 可选，默认就是这个
    $env:LOCAL_ADMIN_TOKEN = "<你的 .env 里的管理员 Token>"
    python scripts/verify_public_scan.py

**Token 一律从环境变量读，脚本里不写死任何凭据** —— 这是能入库的前提。
只使用 RFC 6761 保留域 example.test，不碰任何真实外部目标。

## 覆盖的口径（测试阶段）

```text
目标预检     POST /api/public-jobs/check   —— 只判格式，不判授权
开放模式     GEF_OPEN_SCAN_MODE=true       —— 不挂 scope 也能创建任务
工具闸门     未接入的 nuclei 400 / 已接入的 nmap 202
Scope 白名单 显式给 scope 时越界仍然 403（授权业务取消了，Scope 校验没有）
演练         mode=mock 创建任务（不联网）
```

第 6 步（Scope 越界 → 403）是这份探针里**最重要**的一条：它证明
「取消的是**必须授权**这件事，不是 Scope 本身」。
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
    # 0. 模式开关：先确认这台机器确实开着开放模式，否则后面全是 400，
    #    而「为什么全是 400」会淹没真正要验收的那几件事。
    status, body = call("GET", "/health")
    show("健康检查（含模式开关）", status, body)
    modes = body.get("modes") or {}
    if not modes.get("open_scan_enabled"):
        print(
            f"\n开放扫描模式未开启（{modes.get('open_scan_env', 'GEF_OPEN_SCAN_MODE')}）："
            "本探针要求这一项为 true。请在 .env 中设置后重启 Web 服务。",
            file=sys.stderr,
        )
        return 1

    # 1. 目标预检：只判格式，不判授权（项目 / 范围都不存在也照样给结论）
    status, body = call(
        "POST",
        "/api/public-jobs/check",
        {"targets": ["www.example.test", "not a host", "https://a.example.test:8443/x"]},
    )
    show("目标预检（1 个非法 / 2 个合法）", status, body)
    assert status == 200
    assert [item["valid"] for item in body["checks"]] == [True, False, True]
    assert body["checks"][0]["normalized"] == "www.example.test"
    assert body["checks"][2]["normalized"] == "a.example.test"
    # 预检不得再给授权结论 —— 这几个键出现就说明有人把授权闸门加回来了。
    for gone in ("eligible_scope_ids", "blocker", "matches"):
        assert gone not in body["checks"][0], f"预检又返回了授权结论: {gone}"

    # 2. 未接入的工具 → 400（必须在创建任务之前被拒）
    status, body = call(
        "POST",
        "/api/public-jobs",
        {"targets": ["www.example.test"], "strategy": "custom", "tools": ["nuclei"]},
    )
    show("未接入工具 nuclei（期望 400）", status, body)
    assert status == 400 and body["error_code"] == "bad_request"
    assert [item["tool_name"] for item in body["details"]["unavailable_tools"]] == ["nuclei"]

    # 3. 未登记的工具 → 400（没人写元数据不等于不受限）
    status, body = call(
        "POST",
        "/api/public-jobs",
        {"targets": ["www.example.test"], "strategy": "custom", "tools": ["definitely-not-a-tool"]},
    )
    show("未登记工具（期望 400）", status, body)
    assert status == 400 and body["error_code"] == "bad_request"

    # 4. 演练：mock 模式创建任务（不联网），且**不挂任何 scope**
    status, body = call(
        "POST",
        "/api/public-jobs",
        {"targets": ["www.example.test"], "mode": "mock"},
    )
    show("不挂 scope 创建任务（mock 演练）", status, body)
    assert status == 202 and body["mode"] == "mock", body
    assert body["scope_id"] == "open", "开放模式应当使用内部开放执行上下文"
    assert body["project_id"] is None and body["authorized_public"] is None
    # 开放范围**从不落库**，因此授权清单里不该出现它。
    status, scopes = call("GET", "/api/scopes")
    assert all(item["id"] != "open" for item in scopes["scopes"]), "开放执行上下文被落库了"

    # 5. 显式给了 scope：白名单照旧生效 —— 授权业务取消了，Scope 校验没有。
    status, scope_body = call(
        "POST",
        "/api/scopes",
        {"name": "保留域范围（探针）", "allowed_domains": ["www.example.test"], "active_scan": True},
    )
    show("创建授权 Scope", status, scope_body)
    assert status == 201
    scope_id = scope_body["scope"]["id"]

    status, body = call(
        "POST",
        "/api/public-jobs",
        {"scope_id": scope_id, "targets": ["evil.test"], "mode": "mock"},
    )
    show("显式 scope 下的越界目标（期望 403）", status, body)
    assert status == 403 and body["error_code"] == "scope_violation", body

    # 6. 已接入的高风险工具现在可以提交（工具开放），仍然走 mock 以免真的发流量。
    status, body = call(
        "POST",
        "/api/public-jobs",
        {
            "scope_id": scope_id,
            "targets": ["www.example.test"],
            "strategy": "custom",
            "tools": ["nmap"],
            "mode": "mock",
        },
    )
    show("已接入的高风险工具 nmap（期望 202）", status, body)
    assert status == 202, body
    assert body["strategy"] == "custom"

    print("\n验收链路（不含真实外网请求）全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
