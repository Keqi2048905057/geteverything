"""资产与观测数据层（P1，方案第 8 节；DECISIONS-E 预授权）。

**两层模型**（方案第 8 节原文）::

    Asset
      ↓
    Observation
      ↓
    Job / Run / Tool

* :func:`record_observation` —— 一条「某工具在某次任务里看到了什么」；
* ``assets`` 表里**每个 canonical_key 只有一行**，重复观测只更新
  ``last_seen``（`first_seen` 永不被覆盖，那就是「首次发现时间」）；
* ``observations`` 表里每个观测一行，于是「谁发现的 / 何时发现的 /
  当时的属性」都能回答；
* :func:`diff_jobs` —— 方案第 10 节的 Diff Engine（added / removed /
  changed / unchanged）。

**为什么要有这一层**：加固前「每个工具一张表」（`storage.py` 的
`TOOL_DATABASES`），同一台机器被 subfinder 和 httpx 各发现一次就是两条
互不相干的记录，无法回答「这个资产是谁先发现的」，也无法做可靠的 diff。

**边界**：本模块只写 `assets` / `observations` 两张**新表**，
不改 `storage.py` 的既有表，也不动旧库数据（DECISIONS-E 的约束）。
"""

from __future__ import annotations

import json
from typing import Any

from core import db
from core.canonical import CanonicalError, canonical_key, normalize
from core.ids import new_asset_id, new_observation_id


def _now() -> str:
    """观测时间戳：与 jobs 用同一套 UTC 格式，便于跨表排序。"""
    from core.jobs import _now as jobs_now

    return jobs_now()

# ── 资产状态 ──────────────────────────────────────────────

STATUS_ACTIVE = "active"
STATUS_STALE = "stale"
STATUS_GONE = "gone"

STATUSES = (STATUS_ACTIVE, STATUS_STALE, STATUS_GONE)

#: 资产的「可变属性」白名单：只有这些键参与 diff 的 ``changed`` 判定。
#:
#: 为什么需要白名单：``data_json`` 里还有各种一次性字段（时间戳、耗时、
#: 响应长度），把它们算进 diff 会让**每次扫描都报 changed**，
#: diff 结果直接失去意义。方案第 10 节点名要看的就是这几项。
DIFFABLE_ATTRIBUTES = (
    "status_code",
    "title",
    "server",
    "technology",
    "url",
)

#: diff 里每个资产的属性最多列出多少个变化项（防止 metadata 巨大时刷屏）。
MAX_DIFF_ATTRIBUTE_ITEMS = 20

#: runner 的 ``Observation.category`` → 资产类型。
#:
#: ``category`` 是**旧代码就有的**字段（`storage.py:TOOL_DATABASES` 与各
#: runner 的 config 里都在用），取值是 `subdomain` / `url` / `web` /
#: `alive` / `port` / `dns`。``core.canonical`` 定义的是**资产类型**
#: (`subdomain` / `host` / `ip` / `url` / `port` / …)。两者名字相似但
#: 语义不同，必须显式映射 —— 直接拿 category 当 type 会让
#: `web` / `alive` / `dns` 三种 category 变成非法类型而被整批丢掉。
#:
#: 未列出的 category 一律**跳过**（宁可少收，也不猜错类型：
#: 猜错类型会制造出「同一资产两个类型两份记录」的假重复）。
CATEGORY_TO_TYPE = {
    "subdomain": "subdomain",
    "host": "host",
    "hostname": "host",
    "dns": "host",  # dnsx 的 hostname 解析结果就是主机
    "alive": "host",  # dnsx 的存活结果同样是主机
    "url": "url",
    "web": "url",  # httpx 的 endpoint 是 URL
    "port": "port",
    "service": "service",
}


def asset_type_for_category(category: str | None) -> str | None:
    """把 runner 的 ``Observation.category`` 翻译成资产类型。

    Returns:
        str | None: 资产类型；``None`` 表示该 category 不落资产
        （调用方应跳过这条观测，而不是硬塞一个类型）。
    """
    if not category:
        return None
    return CATEGORY_TO_TYPE.get(str(category).strip().lower())


def _json(value) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, default=str)


def _loads(text: str | None) -> dict:
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _row_to_asset(row) -> dict:
    record = dict(row)
    record["metadata"] = _loads(record.pop("metadata_json", "{}"))
    record["asset_id"] = record["id"]
    return record


def _row_to_observation(row) -> dict:
    record = dict(row)
    record["data"] = _loads(record.pop("data_json", "{}"))
    record["observation_id"] = record["id"]
    return record


# ── 写入 ──────────────────────────────────────────────────


class InvalidAsset(ValueError):
    """规范化失败导致的写入拒绝。

    与 :class:`core.canonical.CanonicalError` 同源：调用方按「跳过这条数据」
    处理，而不是让整次采集失败。
    """


def record_observation(
    asset_type: str,
    value,
    *,
    scope_id: str | None = None,
    job_id: str | None = None,
    step_id: str | None = None,
    run_id: str | None = None,
    source_tool: str | None = None,
    observed_at: str | None = None,
    parser_version: str | None = None,
    raw_artifact_id: str | None = None,
    data: dict | None = None,
    metadata: dict | None = None,
    confidence: str | None = None,
) -> dict:
    """登记一次观测，并顺带维护对应的唯一资产行。

    Args:
        asset_type: :data:`core.canonical.ASSET_TYPES` 之一。
        value: 原始值（会先规范化）。
        scope_id: 所属范围（可空；同一资产在不同 Scope 下算不同行）。
        job_id / step_id / run_id / source_tool: 溯源信息
            （``step_id`` 是「哪个 job 的哪一步」，排查单步异常时最有用）。
        observed_at: 观测时间，默认当前时间（UTC）。
        parser_version: 产出该观测的解析器版本。
        raw_artifact_id: 原始证据的 artifact id。
        data: 结构化属性（status_code / title / technology …）。
        metadata: 合并进资产行的附加属性（只补新键，不覆盖已有键）。
        confidence: 置信度标记（如 ``high`` / ``tentative``）。

    Returns:
        dict: ``{"asset_id", "observation_id", "type", "value", "canonical_key",
        "asset_created", "observed_at"}``。

    Raises:
        InvalidAsset: 类型未知或值无法归一（**调用方应跳过这条记录**）。
    """
    try:
        key = canonical_key(asset_type, value)
        normalized = normalize(asset_type, value)
    except CanonicalError as exc:
        raise InvalidAsset(str(exc)) from exc

    # ``canonical_key`` 形如 ``type|value``，NormalizedAsset 的类型统一小写。
    key_type = key.split("|", 1)[0]
    observed_at = observed_at or _now()
    metadata = metadata or {}

    db.ensure_schema()
    with db.transaction() as conn:
        # Scope 也要进唯一性：同一台主机在两个 Scope 下是两条独立资产，
        # 否则收紧 Scope 会连带影响另一个 Scope 的资产列表。
        existing = conn.execute(
            "SELECT id, first_seen, metadata_json FROM assets WHERE canonical_key = ? AND "
            "IFNULL(scope_id, '') = IFNULL(?, '')",
            (key, scope_id),
        ).fetchone()

        asset_created = existing is None
        if existing is None:
            asset_id = new_asset_id()
            conn.execute(
                """
                INSERT INTO assets (
                    id, scope_id, canonical_key, type, value,
                    first_seen, last_seen, status, confidence, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    scope_id,
                    key,
                    key_type,
                    normalized,
                    observed_at,
                    observed_at,
                    STATUS_ACTIVE,
                    confidence,
                    _json(metadata),
                ),
            )
        else:
            asset_id = existing["id"]
            merged = _loads(existing["metadata_json"])
            for meta_key, meta_value in metadata.items():
                # 只补新键：重跑同一任务不该把人工订正过的属性冲掉。
                merged.setdefault(meta_key, meta_value)
            conn.execute(
                """
                UPDATE assets
                   SET last_seen = ?, status = ?, metadata_json = ?,
                       confidence = COALESCE(?, confidence)
                 WHERE id = ?
                """,
                (observed_at, STATUS_ACTIVE, _json(merged), confidence, asset_id),
            )

        observation_id = new_observation_id()
        conn.execute(
            """
            INSERT INTO observations (
                id, asset_id, job_id, step_id, run_id, source_tool,
                observed_at, parser_version, raw_artifact_id, data_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observation_id,
                asset_id,
                job_id,
                step_id,
                run_id,
                source_tool,
                observed_at,
                parser_version,
                raw_artifact_id,
                _json(data or {}),
            ),
        )

    return {
        "asset_id": asset_id,
        "observation_id": observation_id,
        "type": key_type,
        "value": normalized,
        "canonical_key": key,
        "asset_created": asset_created,
        "observed_at": observed_at,
    }


def record_observations(items: list[dict], **common) -> tuple[list[dict], list[dict]]:
    """批量登记观测。

    Args:
        items: 每项至少含 ``type`` 与 ``value``；可覆盖 ``common`` 里的任何参数。
        **common: 该项所有观测共享的参数（``job_id`` / ``source_tool`` …）。

    Returns:
        tuple[list[dict], list[dict]]: ``(written, skipped)``。
        ``skipped`` 每项形如 ``{"type", "value", "error"}`` —— 单条数据不合格
        **不能**让整批采集失败（工具输出里混着脏数据是常态）。
    """
    written: list[dict] = []
    skipped: list[dict] = []
    for item in items or []:
        payload = {**common, **item}
        asset_type = payload.pop("type", None)
        value = payload.pop("value", None)
        if asset_type is None or value is None:
            skipped.append({"type": asset_type, "value": value, "error": "缺少 type 或 value"})
            continue
        try:
            written.append(record_observation(asset_type, value, **payload))
        except InvalidAsset as exc:
            skipped.append({"type": asset_type, "value": value, "error": str(exc)})
    return written, skipped


def ingest_step_observations(
    step: dict,
    outcome: dict,
    *,
    scope_id: str | None = None,
) -> dict:
    """把一个已完成的 job 步骤的观测落进 ``assets`` / ``observations``（方案第 8 节）。

    **只用步骤自己的结构化观测**（``outcome["observations"]``），**不**读数据库、
    不重新解析原始输出：执行期的数据已经在手里，再绕一圈只会引入不一致。

    数据来源优先级：

    1. ``outcome["observations"]`` —— M4 起 runner 给的结构化观测
       （``{"category", "value", "data", "parser_version"}``）；
    2. 为空时退化到 ``outcome["results"]`` —— 旧签名给的纯字符串列表，
       此时 ``category`` 只能取 ``source_tool`` 的默认类别，
       由 :data:`CATEGORY_TO_TYPE` 里少数几个映射兜住（例如 ``subdomain``）。

    Args:
        step: ``job_steps`` 行（需 ``id`` / ``tool_name`` / ``target``）。
        outcome: :func:`jobs.executor` 产出的步骤结果 dict。
        scope_id: 目标所属 Scope（资产落在该范围下）。

    Returns:
        dict: ``{"written": int, "skipped": int, "reasons": [...]}``。
        本函数**不抛异常**：落观测失败绝不能让一个已经跑完的任务变成失败
        （观测是派生产物，原始结果早已写进 ``job_steps`` 与 ``artifacts``）。
    """
    result: dict[str, Any] = {"written": 0, "skipped": 0, "reasons": []}
    try:
        tool_name = step.get("tool_name")
        raw_items = outcome.get("observations") or []

        items: list[dict] = []
        if raw_items:
            for raw in raw_items:
                if not isinstance(raw, dict):
                    # 形状不对也要计数：否则「少了几条」在事件里看不出来。
                    result["skipped"] += 1
                    result["reasons"].append(f"观测项不是对象: {type(raw).__name__}")
                    continue
                asset_type = asset_type_for_category(raw.get("category"))
                value = raw.get("value")
                if asset_type is None or not value:
                    result["skipped"] += 1
                    if asset_type is None:
                        result["reasons"].append(f"未知 category: {raw.get('category')!r}")
                    continue
                items.append(
                    {
                        "type": asset_type,
                        "value": value,
                        "data": raw.get("data") or {},
                        "parser_version": raw.get("parser_version"),
                    }
                )
        else:
            # 无结构化观测：只有明确知道「这个工具的字符串是什么」才落。
            # 形态不确定的工具（httpx 的 URL / naabu 的 ``ip:port``）宁可跳过 ——
            # 把 ``1.2.3.4:80`` 当 subdomain 存进去比不存更糟。
            fallback_type = _fallback_type(tool_name)
            if fallback_type is None:
                result["skipped"] += len(outcome.get("results") or [])
                result["reasons"].append(f"{tool_name}: 无结构化观测且无法确定资产类型，已跳过")
            else:
                for value in outcome.get("results") or []:
                    if not value:
                        continue
                    items.append({"type": fallback_type, "value": value})

        written, skipped = record_observations(
            items,
            scope_id=scope_id,
            job_id=step.get("job_id"),
            step_id=step.get("id"),
            source_tool=tool_name,
            raw_artifact_id=outcome.get("artifact_id"),
            observed_at=None,
        )
        result["written"] = len(written)
        result["skipped"] += len(skipped)
        result["reasons"].extend(item.get("error", "") for item in skipped)
    except Exception as exc:  # noqa: BLE001 - 观测是派生产物，不能影响任务结果
        result["reasons"].append(f"{type(exc).__name__}: {exc}")
    return result


#: 没有结构化观测时的兜底类型：只有明确知道「这个工具的字符串是什么」才落，
#: 其余工具（httpx / naabu / nmap / 各类 url 工具）一律不落 ——
#: 把 ``1.2.3.4:80`` 当 subdomain 存进去比不存更糟。
_FALLBACK_TYPE_BY_TOOL = {
    "subfinder": "subdomain",
    "amass": "subdomain",
    "amass_intel": "subdomain",
    "assetfinder": "subdomain",
    "oneforall": "subdomain",
    "alterx": "subdomain",
    "shuffledns": "subdomain",
    "dnsx": "host",
}


def _fallback_type(tool_name: str | None) -> str | None:
    """字符串结果的资产类型；返回 ``None`` 表示「不要落，落不准」。"""
    return _FALLBACK_TYPE_BY_TOOL.get(str(tool_name or "").lower())


def mark_stale_assets(scope_id: str, *, last_seen_before: str) -> list[str]:
    """把长期未再出现的资产标成 ``stale``（**不删除**）。

    方案明确禁止「删除/清空历史数据」；资产消失只改状态，
    观测时间线完整保留，随时可回溯。
    """
    db.ensure_schema()
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT id FROM assets WHERE IFNULL(scope_id, '') = IFNULL(?, '') "
            "AND status = ? AND last_seen < ?",
            (scope_id, STATUS_ACTIVE, last_seen_before),
        ).fetchall()
        ids = [row["id"] for row in rows]
        for asset_id in ids:
            conn.execute("UPDATE assets SET status = ? WHERE id = ?", (STATUS_STALE, asset_id))
    return ids


# ── 读取 ──────────────────────────────────────────────────


def get_asset(asset_id: str, *, include_observations: bool = True) -> dict | None:
    """按 ID 读资产；可选带观测时间线（倒序，最近的在最前）。"""
    db.ensure_schema()
    rows = db.query("SELECT * FROM assets WHERE id = ?", (asset_id,))
    if not rows:
        return None
    asset = _row_to_asset(rows[0])
    if include_observations:
        asset["observations"] = list_observations(asset_id=asset_id)
    return asset


def get_asset_by_key(canonical: str, *, scope_id: str | None = None) -> dict | None:
    """按 canonical_key 读资产。"""
    db.ensure_schema()
    rows = db.query(
        "SELECT * FROM assets WHERE canonical_key = ? AND IFNULL(scope_id, '') = IFNULL(?, '')",
        (canonical, scope_id),
    )
    return _row_to_asset(rows[0]) if rows else None


def list_assets(
    *,
    scope_id: str | None = None,
    asset_type: str | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """列出资产（按 ``last_seen`` 倒序）。

    ``search`` 只做 ``LIKE`` 子串匹配并对 ``%`` / ``_`` 转义 —— 资产值来自
    外部工具输出，不转义的通配符会让 ``a_c`` 意外匹配到 ``abc``。
    """
    limit = max(1, min(int(limit or 100), 1000))
    offset = max(0, int(offset or 0))

    clauses = ["1=1"]
    params: list = []
    if scope_id is not None:
        clauses.append("IFNULL(scope_id, '') = IFNULL(?, '')")
        params.append(scope_id)
    if asset_type:
        clauses.append("type = ?")
        params.append(str(asset_type).lower())
    if status:
        clauses.append("status = ?")
        params.append(str(status).lower())
    if search:
        escaped = str(search).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("value LIKE ? ESCAPE '\\'")
        params.append(f"%{escaped}%")

    params.extend([limit, offset])
    db.ensure_schema()
    rows = db.query(
        f"SELECT * FROM assets WHERE {' AND '.join(clauses)} "
        "ORDER BY last_seen DESC, rowid DESC LIMIT ? OFFSET ?",
        tuple(params),
    )
    return [_row_to_asset(row) for row in rows]


def count_assets(
    *,
    scope_id: str | None = None,
    asset_type: str | None = None,
    status: str | None = None,
) -> int:
    """按条件统计资产数量（给列表页做「共 N 条」）。"""
    clauses = ["1=1"]
    params: list = []
    if scope_id is not None:
        clauses.append("IFNULL(scope_id, '') = IFNULL(?, '')")
        params.append(scope_id)
    if asset_type:
        clauses.append("type = ?")
        params.append(str(asset_type).lower())
    if status:
        clauses.append("status = ?")
        params.append(str(status).lower())

    db.ensure_schema()
    rows = db.query(f"SELECT COUNT(*) AS n FROM assets WHERE {' AND '.join(clauses)}", tuple(params))
    return int(rows[0]["n"]) if rows else 0


def list_observations(
    *,
    asset_id: str | None = None,
    job_id: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """列出观测（倒序，最近的在最前）。``asset_id`` 与 ``job_id`` 可组合。"""
    limit = max(1, min(int(limit or 200), 2000))
    clauses = ["1=1"]
    params: list = []
    if asset_id:
        clauses.append("asset_id = ?")
        params.append(asset_id)
    if job_id:
        clauses.append("job_id = ?")
        params.append(job_id)
    params.append(limit)

    db.ensure_schema()
    rows = db.query(
        f"SELECT * FROM observations WHERE {' AND '.join(clauses)} "
        "ORDER BY observed_at DESC, rowid DESC LIMIT ?",
        tuple(params),
    )
    return [_row_to_observation(row) for row in rows]


def asset_summary(scope_id: str | None = None) -> dict:
    """按类型统计（资产列表页顶部的分类型计数）。"""
    db.ensure_schema()
    clauses = "WHERE IFNULL(scope_id, '') = IFNULL(?, '')" if scope_id is not None else ""
    params = (scope_id,) if scope_id is not None else ()
    rows = db.query(f"SELECT type, COUNT(*) AS n FROM assets {clauses} GROUP BY type", params)
    by_type = {row["type"]: int(row["n"]) for row in rows}
    return {
        "by_type": by_type,
        "total": sum(by_type.values()),
        "scopes": count_assets(scope_id=scope_id),
    }


# ── Diff（方案第 10 节） ──────────────────────────────────


def _latest_data_by_asset(job_id: str) -> dict[str, dict]:
    """取某次任务里每个资产的**最后一条**观测属性。"""
    rows = db.query(
        "SELECT asset_id, data_json, observed_at, source_tool FROM observations "
        "WHERE job_id = ? ORDER BY observed_at ASC, rowid ASC",
        (job_id,),
    )
    latest: dict[str, dict] = {}
    for row in rows:
        latest[row["asset_id"]] = {
            "data": _loads(row["data_json"]),
            "observed_at": row["observed_at"],
            "source_tool": row["source_tool"],
        }
    return latest


def _changed_attributes(before: dict, after: dict) -> dict:
    """比较两次观测的可变属性，返回 ``{属性: {"from": ..., "to": ...}}``。

    口径：只看 :data:`DIFFABLE_ATTRIBUTES` 里的键，值不等就算变化
    —— 包含「从有到无」（``200 → None``）与「从无到有」。
    「以前报 200、这次什么也没报」确实是一次值得注意的变化，
    而两边都为 ``None`` 时不算（属性缺失 ≠ 属性被改）。
    """
    changes: dict[str, dict] = {}
    for attribute in DIFFABLE_ATTRIBUTES:
        old = before.get(attribute)
        new = after.get(attribute)
        if old == new:
            continue
        # 两边都缺该属性时不算变化（工具没报 ≠ 属性被改）。
        if old is None and new is None:
            continue
        changes[attribute] = {"from": old, "to": new}
        if len(changes) >= MAX_DIFF_ATTRIBUTE_ITEMS:
            break
    return changes


def diff_jobs(
    before_job_id: str,
    after_job_id: str,
    *,
    scope_id: str | None = None,
    include_unchanged: bool = True,
) -> dict:
    """比较两次任务的观测结果（方案第 10 节）。

    分类口径：

    * ``added``     —— 只在 after 出现；
    * ``removed``   —— 只在 before 出现；
    * ``changed``   —— 两次都有，但 :data:`DIFFABLE_ATTRIBUTES` 里有变化；
    * ``unchanged`` —— 两次都有且属性一致。

    **只看这两次任务自己的观测**，不看「某个资产历史上被谁见过」——
    否则「A B C」对「A C D」的验收会给不出 ``added=D / removed=B``。

    Args:
        before_job_id: 基线任务。
        after_job_id: 对比任务。
        scope_id: 只比较该范围下的资产（``None`` = 不按范围过滤）。
        include_unchanged: 是否返回 ``unchanged`` 明细（默认返回数量与明细）。

    Returns:
        dict: ``{"before_job_id", "after_job_id", "added", "removed",
        "changed", "unchanged", "counts"}``。每项形如
        ``{"asset_id", "type", "value", "canonical_key", ...}``。
    """
    db.ensure_schema()
    before = _latest_data_by_asset(before_job_id)
    after = _latest_data_by_asset(after_job_id)

    if not before and not after:
        return {
            "before_job_id": before_job_id,
            "after_job_id": after_job_id,
            "added": [],
            "removed": [],
            "changed": [],
            "unchanged": [],
            "counts": {"added": 0, "removed": 0, "changed": 0, "unchanged": 0},
        }

    asset_ids = set(before) | set(after)
    assets = _assets_by_ids(asset_ids, scope_id=scope_id)

    # 按范围过滤后，另一侧可能整批消失 —— 这时应该什么都不报，而不是
    # 把「范围外资产」误报成 removed。
    if scope_id is not None:
        before = {key: value for key, value in before.items() if key in assets}
        after = {key: value for key, value in after.items() if key in assets}

    added: list[dict] = []
    removed: list[dict] = []
    changed: list[dict] = []
    unchanged: list[dict] = []

    for asset_id in sorted(after.keys() - before.keys()):
        added.append(_diff_item(assets.get(asset_id), after[asset_id]))

    for asset_id in sorted(before.keys() - after.keys()):
        removed.append(_diff_item(assets.get(asset_id), before[asset_id]))

    for asset_id in sorted(before.keys() & after.keys()):
        item = _diff_item(assets.get(asset_id), after[asset_id])
        differences = _changed_attributes(before[asset_id]["data"], after[asset_id]["data"])
        if differences:
            item["changes"] = differences
            changed.append(item)
        elif include_unchanged:
            unchanged.append(item)

    result: dict[str, Any] = {
        "before_job_id": before_job_id,
        "after_job_id": after_job_id,
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged": unchanged,
    }
    result["counts"] = {key: len(result[key]) for key in ("added", "removed", "changed", "unchanged")}
    return result


def _assets_by_ids(asset_ids: set[str], *, scope_id: str | None = None) -> dict[str, dict]:
    if not asset_ids:
        return {}
    placeholders = ",".join("?" for _ in asset_ids)
    params: list = list(asset_ids)
    sql = f"SELECT * FROM assets WHERE id IN ({placeholders})"
    if scope_id is not None:
        sql += " AND IFNULL(scope_id, '') = IFNULL(?, '')"
        params.append(scope_id)
    rows = db.query(sql, tuple(params))
    return {row["id"]: _row_to_asset(row) for row in rows}


def _diff_item(asset: dict | None, observation: dict) -> dict:
    """统一 diff 条目的形状：资产信息 + 本次观测到的属性。"""
    return {
        "asset_id": asset["id"] if asset else None,
        "type": asset["type"] if asset else None,
        "value": asset["value"] if asset else None,
        "canonical_key": asset["canonical_key"] if asset else None,
        "scope_id": asset.get("scope_id") if asset else None,
        "first_seen": asset.get("first_seen") if asset else None,
        "last_seen": asset.get("last_seen") if asset else None,
        "source_tool": observation.get("source_tool"),
        "observed_at": observation.get("observed_at"),
        "data": observation.get("data", {}),
    }
