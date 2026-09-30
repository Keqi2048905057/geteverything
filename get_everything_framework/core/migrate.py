"""旧库 → 新库的**只读**迁移计划与执行（P1，方案第 12 节；DECISIONS-F 预授权）。

**范围（DECISIONS-F 的约束，逐条对应）**：

* 只写脚本 + 单元测试，**不执行真实迁移** —— 真实迁移由用户手动运行 CLI；
* 旧库 ``results/scan_results.db`` 以 ``mode=ro`` 打开，**一个字节都不改**；
* 新库只新增 ``assets`` / ``observations`` 两张表的内容，**不改任何既有表结构**；
* 不删除任何数据：迁移是「追加读」，旧库原样保留。

**迁移语义**：

```text
旧库（17 张工具表 + tool_results）
   ↓  每行 → 一条 observation
新库（assets 去重 + observations 时间线）
```

* 资产类型由 ``storage.TOOL_DATABASES[tool]["category"]`` 经
  :data:`core.assets.CATEGORY_TO_TYPE` 翻译（``web``→``url``、``alive``/``dns``→``host``）；
* 观测 ID 是**确定性**的（由旧库行身份哈希而来），因此迁移脚本可以安全重跑 ——
  已经迁过的那行会被 :func:`apply_migration` 跳过，而不是又插一条；
* ``observed_at`` 统一成与 ``core.jobs`` 相同的 ISO-8601 格式（旧库写的是
  ``...Z`` 后缀），否则 ``ORDER BY observed_at`` 会在两种写法之间错乱；
* 迁移**按时间升序**写入，这样 ``last_seen`` 自然停在最新那一次观测上；
* ``scope_id`` 留空（NULL）：旧库年代没有 Scope 概念，不能事后编一个。

**为什么放 ``core/`` 而不是 ``scripts/``**：本模块是纯数据逻辑（可被 worker、
CLI、测试直接调用），``scripts/migrate_legacy_results.py`` 只是一层参数解析。
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Iterator

from core import assets as assets_store
from core.canonical import CanonicalError
from core.canonical import canonical_key as build_canonical_key

#: 迁移产生的观测 ID 前缀：与运行时观测（``obs_<uuid4>``）区分开，
#: 出问题时能一眼看出「这条来自旧库迁移」。
LEGACY_OBSERVATION_PREFIX = "obs_mig_"

#: 泛型结果表的表名（未注册工具的回退落点）。
GENERIC_TABLE = "tool_results"


class MigrationError(RuntimeError):
    """迁移前置条件不满足（路径不存在、源与目标同一个库等）。"""


def legacy_observation_id(*parts: object) -> str:
    """由旧库行的身份算出一个确定性观测 ID。

    同一个旧库行无论迁多少次，得到的 ID 都一样 —— 这是「可安全重跑」的落点。
    """
    raw = "\x1f".join(str(part) for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    return f"{LEGACY_OBSERVATION_PREFIX}{digest[:24]}"


def _ro_uri(path: str) -> str:
    """把本地路径转成 SQLite 的只读 URI（``mode=ro``，缺文件不自动创建）。"""
    return f"{Path(path).resolve().as_uri()}?mode=ro"


def open_legacy(legacy_path: str) -> sqlite3.Connection:
    """以**只读**方式打开旧库。

    Raises:
        MigrationError: 文件不存在（只读 URI 不会创建文件，但报错信息不友好，
            这里先自己判一次，避免用户看到 ``unable to open database file``）。
    """
    if not Path(legacy_path).is_file():
        raise MigrationError(f"旧库不存在: {legacy_path}")
    conn = sqlite3.connect(_ro_uri(legacy_path), uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _normalize_timestamp(value: object) -> str:
    """旧库时间戳 → 与 ``core.jobs`` 一致的形式。

    旧库写的是 ``datetime.utcnow().isoformat(timespec="seconds") + "Z"``
    （如 ``2026-01-01T00:00:00Z``），新库写的是带 ``+00:00`` 的时区形式。
    两种写法混在一列里，字符串排序会把 ``Z`` 排到 ``+`` 之后 —— 必须归一。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if text.endswith("Z"):
        return f"{text[:-1]}+00:00"
    return text


def _legacy_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row["name"] for row in rows}


def iter_legacy_rows(legacy_path: str, *, table_map: dict[str, dict] | None = None) -> Iterator[dict]:
    """按 ``created_at`` 升序遍历旧库的每一行结果。

    产出形如::

        {"source_table", "tool_name", "category", "domain", "value",
         "created_at", "row_id", "run_id"}

    Args:
        legacy_path: 旧库路径。
        table_map: 工具元信息映射；默认用 ``storage.TOOL_DATABASES``
            （测试可以传一个小映射，避免依赖完整工具表）。

    Yields:
        dict: 一行旧数据。
    """
    if table_map is None:
        from storage import TOOL_DATABASES

        table_map = TOOL_DATABASES

    conn = open_legacy(legacy_path)
    try:
        existing = _legacy_tables(conn)

        # 1) 各工具的专属表。表可能不存在（旧库是渐进长出来的），跳过即可。
        for tool_name, meta in table_map.items():
            table = meta["table"]
            if table not in existing:
                continue
            column = meta["column"]
            sql = (
                f"SELECT id AS row_id, run_id, domain, {column} AS value, created_at "
                f"FROM {table} ORDER BY created_at ASC, id ASC"
            )
            for row in conn.execute(sql):
                yield {
                    "source_table": table,
                    "tool_name": tool_name,
                    "category": meta["category"],
                    "domain": row["domain"],
                    "value": row["value"],
                    "created_at": row["created_at"],
                    "row_id": row["row_id"],
                    "run_id": row["run_id"],
                }

        # 2) 泛型回退表。category 直接从行里取（非注册工具才有这一行）。
        if GENERIC_TABLE in existing:
            sql = (
                "SELECT id AS row_id, run_id, domain, tool_name, category, value, created_at "
                f"FROM {GENERIC_TABLE} ORDER BY created_at ASC, id ASC"
            )
            for row in conn.execute(sql):
                yield {
                    "source_table": GENERIC_TABLE,
                    "tool_name": row["tool_name"],
                    "category": row["category"],
                    "domain": row["domain"],
                    "value": row["value"],
                    "created_at": row["created_at"],
                    "row_id": row["row_id"],
                    "run_id": row["run_id"],
                }
    finally:
        conn.close()


def row_to_item(row: dict) -> dict:
    """旧库行 → :func:`core.assets.record_observation` 的参数。

    Returns:
        dict: ``{"ok": bool, "item": {...}}`` 或 ``{"ok": False, "reason": str}``。
        不合格的行（类型翻译不出来、值无法归一）**不抛异常**：旧库里混着
        脏数据是常态，一条脏数据不该让整次迁移失败。
    """
    raw_category = row.get("category")
    asset_type = assets_store.asset_type_for_category(raw_category)
    if asset_type is None:
        return {"ok": False, "reason": f"未知 category: {raw_category!r}"}

    value = row.get("value")
    if value is None or str(value).strip() == "":
        return {"ok": False, "reason": "空值"}

    try:
        build_canonical_key(asset_type, value)
    except CanonicalError as exc:
        return {"ok": False, "reason": str(exc)}

    observed_at = _normalize_timestamp(row.get("created_at"))
    return {
        "ok": True,
        "item": {
            "asset_type": asset_type,
            "value": value,
            "observed_at": observed_at or None,
            "source_tool": row.get("tool_name"),
            "data": {
                "legacy_domain": row.get("domain"),
                "legacy_run_id": row.get("run_id"),
                "legacy_table": row.get("source_table"),
            },
            "metadata": {"legacy_source": row.get("source_table")},
            "observation_id": legacy_observation_id(
                row.get("source_table"), row.get("tool_name"), row.get("domain"),
                row.get("value"), row.get("created_at"), row.get("row_id"),
            ),
        },
    }


def plan_migration(legacy_path: str, *, table_map: dict[str, dict] | None = None) -> dict:
    """**只读**地算出「这次迁移会做什么」，不写任何库。

    ``items`` 会按 ``observed_at`` **升序重排**。这不是装饰：:func:`iter_legacy_rows`
    只能保证「每张表内部按时间升序」（跨表顺序取决于 ``table_map`` 的遍历顺序），
    而 :func:`core.assets.record_observation` 的 ``first_seen`` 是「第一次写入时
    定下、之后永不覆盖」。若不做全局排序，「同一资产在 A 表最早出现、在 B 表次早出现」
    这种最常见的旧库形态就会把 ``first_seen`` 记成两张表里先遍历到的那张的时间。

    Returns:
        dict: ``{"items", "skipped", "counts": {"total", "planned", "skipped"}}``。
    """
    items: list[dict] = []
    skipped: list[dict] = []
    total = 0
    for row in iter_legacy_rows(legacy_path, table_map=table_map):
        total += 1
        converted = row_to_item(row)
        if converted["ok"]:
            items.append(converted["item"])
        else:
            skipped.append(
                {
                    "source_table": row.get("source_table"),
                    "tool_name": row.get("tool_name"),
                    "value": row.get("value"),
                    "reason": converted["reason"],
                }
            )

    # 时间升序（同年月日时分秒的用 observation_id 兜底，保证可重跑时顺序稳定）。
    items.sort(key=lambda item: (item.get("observed_at") or "", item["observation_id"]))

    return {
        "items": items,
        "skipped": skipped,
        "counts": {"total": total, "planned": len(items), "skipped": len(skipped)},
    }


def apply_migration(plan: dict, *, path: str | None = None) -> dict:
    """把 :func:`plan_migration` 的结果写进新库（**可重跑**）。

    已经存在同 ID 的观测会被跳过 —— 所以中断后重跑不会产生重复时间线。

    Returns:
        dict: ``{"written", "already", "failed", "reasons"}``。
    """
    written = 0
    already = 0
    failed = 0
    reasons: list[str] = []

    for item in plan.get("items", []):
        observation_id = item["observation_id"]
        if assets_store.observation_exists(observation_id, path=path):
            already += 1
            continue
        payload = dict(item)
        asset_type = payload.pop("asset_type")
        value = payload.pop("value")
        try:
            assets_store.record_observation(asset_type, value, path=path, **payload)
        except Exception as exc:  # noqa: BLE001 - 单条失败不该中断整次迁移
            failed += 1
            reasons.append(f"{observation_id}: {type(exc).__name__}: {exc}")
            continue
        written += 1

    return {"written": written, "already": already, "failed": failed, "reasons": reasons[:20]}
