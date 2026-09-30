"""把旧结果库（``results/scan_results.db``）迁进统一资产模型（P1，方案第 12 节）。

> **默认只做 dry-run**：不加 ``--apply`` 时一个字节都不会写。
> 真实迁移需要显式加 ``--apply``，由用户手动运行（DECISIONS-F）。

用法::

    # 1) 看这次会迁什么（只读，安全）
    python scripts/migrate_legacy_results.py

    # 2) 确认无误后真正写入
    python scripts/migrate_legacy_results.py --apply

    # 3) 指定路径（默认取 config.SQLITE_CONFIG / LOCAL_DB_CONFIG）
    python scripts/migrate_legacy_results.py --legacy results/scan_results.db --target results/local.db

退出码：0 = 成功；2 = 前置条件不满足（旧库不存在、源与目标是同一个库等）。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 允许 `python scripts/migrate_legacy_results.py` 直接运行（脚本不在项目根下）。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _default_paths() -> tuple[str, str]:
    from config import LOCAL_DB_CONFIG, SQLITE_CONFIG

    return str(SQLITE_CONFIG["path"]), str(LOCAL_DB_CONFIG["path"])


def main(argv: list[str] | None = None) -> int:
    legacy_default, target_default = _default_paths()

    parser = argparse.ArgumentParser(
        description="旧结果库 → 统一资产模型（assets / observations）的只读迁移",
    )
    parser.add_argument("--legacy", default=legacy_default, help="旧库路径（只读打开）")
    parser.add_argument("--target", default=target_default, help="新库路径（写入 assets/observations）")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正写入。不加此参数时只做 dry-run，不写任何库。",
    )
    parser.add_argument("--limit", type=int, default=0, help="最多迁移多少行（0 = 不限制，调试用）")
    args = parser.parse_args(argv)

    from core import migrate

    legacy = str(Path(args.legacy).resolve())
    target = str(Path(args.target).resolve())
    if os.path.abspath(legacy) == os.path.abspath(target):
        print("[!] 源库与目标库是同一个文件，拒绝执行（会把旧表和新表混在一起）。", file=sys.stderr)
        return 2

    try:
        plan = migrate.plan_migration(legacy)
    except migrate.MigrationError as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 2

    counts = plan["counts"]
    if args.limit > 0:
        plan["items"] = plan["items"][: args.limit]

    print("== 旧库 → 统一资产模型 ==")
    print(f"  旧库（只读）: {legacy}")
    print(f"  新库（写入）: {target}")
    print(f"  扫描到 {counts['total']} 行：可迁移 {counts['planned']}，跳过 {counts['skipped']}")
    for item in plan["skipped"][:10]:
        print(f"    - 跳过 {item['tool_name']} {item['value']!r}: {item['reason']}")

    if not args.apply:
        print("\n[dry-run] 未写任何库。确认无误后加 --apply 执行。")
        return 0

    # 迁移脚本显式指定目标库，避免依赖进程环境。
    result = migrate.apply_migration(plan, path=target)
    print(
        f"\n[apply] 新写入 {result['written']} 条观测，"
        f"已存在跳过 {result['already']} 条，失败 {result['failed']} 条。"
    )
    for reason in result["reasons"]:
        print(f"    - 失败: {reason}")
    print("旧库未做任何修改；资产页 / GET /api/assets 现在可以看到迁移结果。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
