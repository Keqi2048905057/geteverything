"""SQLite 数据库层 — 管理扫描结果的持久化存储。

本模块提供 ScanResultStore 类，负责创建/维护所有工具的专用表、
写入扫描结果、以及提供多维度查询接口（按域名/工具/分类等）。
"""

import sqlite3
from datetime import datetime

from config import SQLITE_CONFIG


# ── 工具数据库元信息映射 ───────────────────────────────────
# 每个工具对应一张专属表和结果字段名，category 用于分类查询
TOOL_DATABASES = {
    "amass": {
        "table": "amass_results",
        "column": "subdomain",
        "category": "subdomain",
    },
    "amass_intel": {
        "table": "amass_intel_results",
        "column": "subdomain",
        "category": "subdomain",
    },
    "subfinder": {
        "table": "subfinder_results",
        "column": "subdomain",
        "category": "subdomain",
    },
    "assetfinder": {
        "table": "assetfinder_results",
        "column": "subdomain",
        "category": "subdomain",
    },
    "shuffledns": {
        "table": "shuffledns_results",
        "column": "subdomain",
        "category": "subdomain",
    },
    "alterx": {
        "table": "alterx_results",
        "column": "subdomain",
        "category": "subdomain",
    },
    "oneforall": {
        "table": "oneforall_results",
        "column": "subdomain",
        "category": "subdomain",
    },
    "enscan": {
        "table": "enscan_results",
        "column": "subdomain",
        "category": "subdomain",
    },
    "gospider": {
        "table": "gospider_results",
        "column": "url",
        "category": "url",
    },
    "dnsx": {
        "table": "dnsx_results",
        "column": "hostname",
        "category": "alive",
    },
    "httpx": {
        "table": "httpx_results",
        "column": "endpoint",
        "category": "web",
    },
    "naabu": {
        "table": "naabu_results",
        "column": "port_result",
        "category": "port",
    },
    "nmap": {
        "table": "nmap_results",
        "column": "port_result",
        "category": "port",
    },
    "feroxbuster": {
        "table": "feroxbuster_results",
        "column": "url",
        "category": "url",
    },
    "dirsearch": {
        "table": "dirsearch_results",
        "column": "url",
        "category": "url",
    },
    "waybackurls": {
        "table": "waybackurls_results",
        "column": "url",
        "category": "url",
    },
    "katana": {
        "table": "katana_results",
        "column": "url",
        "category": "url",
    },
}


class ScanResultStore:
    """扫描结果 SQLite 存储管理器。

    负责：
    - 初始化 scan_runs 和 tool_results 通用表
    - 为每个已注册工具创建专属结果表
    - 写入扫描结果（去重 + 标准化）
    - 提供按域名/工具/分类查询的接口
    """

    def __init__(self, db_path=None):
        """初始化存储实例。

        Args:
            db_path: SQLite 数据库文件路径，默认使用 config 中的配置。
        """
        self.db_path = db_path or SQLITE_CONFIG["path"]
        self._init_db()

    def _get_connection(self):
        """创建新的 SQLite 数据库连接（每次调用返回新连接）。"""
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        """初始化数据库表结构。

        创建：
        - scan_runs：记录每次扫描运行的元信息
        - tool_results：通用工具结果表（非专属工具使用）
        - 各工具的专属结果表
        """
        with self._get_connection() as conn:
            # 扫描运行记录表
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scan_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    result_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_scan_runs_domain
                ON scan_runs(domain)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_scan_runs_tool
                ON scan_runs(tool_name)
                """
            )
            # 通用工具结果表（非专属工具的回退存储）
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    domain TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    value TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(domain, tool_name, category, value),
                    FOREIGN KEY(run_id) REFERENCES scan_runs(id)
                )
                """
            )
            # 为每个注册的工具创建专属结果表
            for meta in TOOL_DATABASES.values():
                self._create_tool_table(conn, meta["table"], meta["column"])

    def _create_tool_table(self, conn, table_name, result_column):
        """为指定工具创建专属结果表。

        Args:
            conn: 数据库连接对象。
            table_name: 表名。
            result_column: 结果字段名（如 subdomain、url 等）。
        """
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                domain TEXT NOT NULL,
                {result_column} TEXT NOT NULL,
                raw_result TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(domain, {result_column}),
                FOREIGN KEY(run_id) REFERENCES scan_runs(id)
            )
            """
        )
        conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{table_name}_domain
            ON {table_name}(domain)
            """
        )

    def _normalize_results(self, results):
        """标准化结果列表：去除空白值、去重、strip 首尾空白。

        Args:
            results: 原始结果字符串列表。

        Returns:
            去重且 trim 后的结果列表，保持原有顺序。
        """
        normalized_results = []
        seen = set()
        for item in results:
            value = item.strip()
            if not value or value in seen:
                continue
            normalized_results.append(value)
            seen.add(value)
        return normalized_results

    def _create_scan_run(self, cursor, domain, tool_name, result_count, created_at):
        """向 scan_runs 表插入一条扫描运行记录。

        Args:
            cursor: 数据库游标。
            domain: 目标域名。
            tool_name: 工具名称。
            result_count: 结果数量。
            created_at: ISO 格式时间戳。

        Returns:
            新插入记录的 run_id。
        """
        cursor.execute(
            """
            INSERT INTO scan_runs (domain, tool_name, result_count, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (domain, tool_name, result_count, created_at),
        )
        return cursor.lastrowid

    # ── 写入方法 ───────────────────────────────────────────

    def save_dedicated_results(self, domain, tool_name, category, results):
        """将结果写入工具的专属表（同时记录 scan_runs）。

        Args:
            domain: 目标域名。
            tool_name: 工具名称（必须在 TOOL_DATABASES 中注册）。
            category: 结果分类。
            results: 原始结果列表。

        Returns:
            {"run_id": int, "scan_count": int, "inserted_count": int}
        """
        normalized_results = self._normalize_results(results)
        created_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        meta = TOOL_DATABASES.get(tool_name)

        # 如果工具未注册，回退到通用 tool_results 表
        if not meta:
            return self.save_tool_results(domain, tool_name, category, normalized_results)

        table_name = meta["table"]
        result_column = meta["column"]

        with self._get_connection() as conn:
            cursor = conn.cursor()
            run_id = self._create_scan_run(
                cursor, domain, tool_name, len(normalized_results), created_at
            )

            inserted_count = 0
            for value in normalized_results:
                cursor.execute(
                    f"""
                    INSERT OR IGNORE INTO {table_name}
                    (run_id, domain, {result_column}, raw_result, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (run_id, domain, value, value, created_at),
                )
                inserted_count += cursor.rowcount

            return {
                "run_id": run_id,
                "scan_count": len(normalized_results),
                "inserted_count": inserted_count,
            }

    def save_results(self, domain, tool_name, results):
        """兼容旧接口 — 自动判断走专属表还是通用表。

        Args:
            domain: 目标域名。
            tool_name: 工具名称。
            results: 原始结果列表。

        Returns:
            写入结果摘要字典。
        """
        if tool_name in TOOL_DATABASES:
            return self.save_dedicated_results(
                domain, tool_name, TOOL_DATABASES[tool_name]["category"], results
            )
        # 未知工具回退到通用表，category 默认为 "unknown"
        return self.save_tool_results(domain, tool_name, "unknown", self._normalize_results(results))

    def save_tool_results(self, domain, tool_name, category, results):
        """通用写入入口：已注册工具走专属表，否则写入通用 tool_results 表。

        该方法被 agent/action.py 等外部调用方使用。

        Args:
            domain: 目标域名。
            tool_name: 工具名称。
            category: 结果分类。
            results: 标准化后的结果列表。

        Returns:
            {"run_id": int, "scan_count": int, "inserted_count": int}
        """
        # 已注册工具直接走专属表逻辑
        if tool_name in TOOL_DATABASES:
            return self.save_dedicated_results(domain, tool_name, category, results)

        normalized_results = self._normalize_results(results)
        created_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"

        with self._get_connection() as conn:
            cursor = conn.cursor()
            run_id = self._create_scan_run(
                cursor, domain, tool_name, len(normalized_results), created_at
            )

            inserted_count = 0
            for value in normalized_results:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO tool_results
                    (run_id, domain, tool_name, category, value, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (run_id, domain, tool_name, category, value, created_at),
                )
                inserted_count += cursor.rowcount

            return {
                "run_id": run_id,
                "scan_count": len(normalized_results),
                "inserted_count": inserted_count,
            }

    # ── 查询方法 ───────────────────────────────────────────

    def _query_subdomain_tables(self, domain=None, tool_name=None):
        """跨所有子域名工具表做 UNION ALL 查询，返回统一格式结果。

        Args:
            domain: 可选，按域名筛选。
            tool_name: 可选，按工具名筛选（只查该工具表）。

        Returns:
            列表，每行为 (domain, value, tool_name, created_at)。
        """
        subdomain_tools = [
            (t, m)
            for t, m in TOOL_DATABASES.items()
            if m["category"] == "subdomain"
        ]
        queries = []
        params = []
        for tool, meta in subdomain_tools:
            table = meta["table"]
            col = meta["column"]
            q = f"SELECT domain, {col} AS value, '{tool}' AS tool_name, created_at FROM {table}"
            conditions = []
            qparams = []
            if domain:
                conditions.append("domain = ?")
                qparams.append(domain)
            # 如果指定了工具名且与当前表不匹配，跳过此表
            if tool_name and tool_name != tool:
                continue
            if conditions:
                q += " WHERE " + " AND ".join(conditions)
            queries.append(q)
            params.extend(qparams)

        if not queries:
            return []

        union_sql = " UNION ALL ".join(queries) + " ORDER BY value ASC"
        with self._get_connection() as conn:
            return conn.execute(union_sql, params).fetchall()

    def get_results_by_domain(self, domain):
        """按域名获取所有子域名查询结果。

        Args:
            domain: 目标域名。

        Returns:
            列表，每项为 (value, tool_name, created_at)。
        """
        rows = self._query_subdomain_tables(domain=domain)
        return [(value, tool_name, created_at) for _, value, tool_name, created_at in rows]

    def get_global_summary(self):
        """获取全局汇总信息。

        Returns:
            字典包含 total_runs、total_domains、total_subdomains、tool_stats、recent_runs。
        """
        with self._get_connection() as conn:
            total_runs = conn.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0]

            rows = self._query_subdomain_tables()
            domains = {row[0] for row in rows}
            values = {(row[0], row[1]) for row in rows}

            # 统计每个工具的结果数量
            tool_counts = {}
            for row in rows:
                tool = row[2]
                tool_counts[tool] = tool_counts.get(tool, 0) + 1
            tool_stats = sorted(tool_counts.items(), key=lambda x: (-x[1], x[0]))

            # 最新 10 次扫描记录
            recent_runs = conn.execute(
                "SELECT domain, tool_name, result_count, created_at "
                "FROM scan_runs ORDER BY id DESC LIMIT 10"
            ).fetchall()

            return {
                "total_runs": total_runs,
                "total_domains": len(domains),
                "total_subdomains": len(values),
                "tool_stats": tool_stats,
                "recent_runs": recent_runs,
            }

    def get_domain_summary(self, domain):
        """获取单个域名的汇总信息。

        Args:
            domain: 目标域名。

        Returns:
            字典包含 domain、total_subdomains、last_scan_at、tool_stats、results。
        """
        rows = self._query_subdomain_tables(domain=domain)
        results = [(value, tool_name, created_at) for _, value, tool_name, created_at in rows]

        tool_counts = {}
        last_scan_at = None
        for value, tool_name, created_at in results:
            tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
            if not last_scan_at or created_at > last_scan_at:
                last_scan_at = created_at

        return {
            "domain": domain,
            "total_subdomains": len({v for v, _, _ in results}),
            "last_scan_at": last_scan_at,
            "tool_stats": sorted(tool_counts.items(), key=lambda x: (-x[1], x[0])),
            "results": results,
        }

    def get_dedicated_results(self, tool_name, domain=None, limit=200):
        """查询指定工具专属表中的结果。

        Args:
            tool_name: 工具名称。
            domain: 可选，按域名筛选。
            limit: 最大返回数量，默认 200。

        Returns:
            字典列表，每项含 domain、tool_name、table、result_column、value、raw_result、created_at。

        Raises:
            ValueError: 工具名未在 TOOL_DATABASES 中注册。
        """
        meta = TOOL_DATABASES.get(tool_name)
        if not meta:
            raise ValueError(f"不支持的工具数据库: {tool_name}")

        table_name = meta["table"]
        result_column = meta["column"]
        query = [
            f"SELECT domain, {result_column}, raw_result, created_at",
            f"FROM {table_name}",
        ]
        params = []

        if domain:
            query.append("WHERE domain = ?")
            params.append(domain)

        query.append("ORDER BY id DESC")
        query.append("LIMIT ?")
        params.append(limit)

        with self._get_connection() as conn:
            cursor = conn.execute("\n".join(query), params)
            return [
                {
                    "domain": row_domain,
                    "tool_name": tool_name,
                    "table": table_name,
                    "result_column": result_column,
                    "value": value,
                    "raw_result": raw_result,
                    "created_at": created_at,
                }
                for row_domain, value, raw_result, created_at in cursor.fetchall()
            ]

    def get_tool_databases(self):
        """获取所有已注册工具数据库的元信息列表。

        Returns:
            字典列表，每项含 tool_name、table、result_column、category。
        """
        return [
            {
                "tool_name": tool_name,
                "table": meta["table"],
                "result_column": meta["column"],
                "category": meta["category"],
            }
            for tool_name, meta in sorted(TOOL_DATABASES.items())
        ]

    def get_tool_database_overview(self):
        """获取所有工具数据库的统计概览（记录数、域名数、最近扫描时间）。

        Returns:
            字典列表，每项含 tool_name、table、category、result_column、total_count、domain_count、last_scan_at。
        """
        overview = []
        with self._get_connection() as conn:
            for tool_name, meta in sorted(TOOL_DATABASES.items()):
                table_name = meta["table"]
                result_column = meta["column"]
                cursor = conn.execute(
                    f"""
                    SELECT
                        COUNT(*) AS total_count,
                        COUNT(DISTINCT domain) AS domain_count,
                        MAX(created_at) AS last_scan_at
                    FROM {table_name}
                    """
                )
                total_count, domain_count, last_scan_at = cursor.fetchone()
                overview.append(
                    {
                        "tool_name": tool_name,
                        "table": table_name,
                        "category": meta["category"],
                        "result_column": result_column,
                        "total_count": total_count or 0,
                        "domain_count": domain_count or 0,
                        "last_scan_at": last_scan_at,
                    }
                )
        return overview

    def get_view_results(self, domain=None, tool_name=None):
        """获取子域名视图的明细结果（仅供 viewer 使用）。

        Args:
            domain: 可选，按域名筛选。
            tool_name: 可选，按工具名筛选。

        Returns:
            列表，每行为 (domain, value, tool_name, created_at)。
        """
        rows = self._query_subdomain_tables(domain=domain, tool_name=tool_name)
        return [(row[0], row[1], row[2], row[3]) for row in rows]

    def get_view_overview(self, domain=None, tool_name=None):
        """获取子域名视图的概览统计（按域名+工具分组计数）。

        Args:
            domain: 可选，按域名筛选。
            tool_name: 可选，按工具名筛选。

        Returns:
            列表，每行为 (domain, tool_name, total_count, last_scan_at)。
        """
        rows = self._query_subdomain_tables(domain=domain, tool_name=tool_name)
        grouped = {}
        for item_domain, subdomain, item_tool, created_at in rows:
            key = (item_domain, item_tool)
            current = grouped.get(key)
            if not current:
                grouped[key] = {
                    "domain": item_domain,
                    "tool_name": item_tool,
                    "total_count": 1,
                    "last_scan_at": created_at,
                }
                continue
            current["total_count"] += 1
            if created_at > current["last_scan_at"]:
                current["last_scan_at"] = created_at

        return [
            (item["domain"], item["tool_name"], item["total_count"], item["last_scan_at"])
            for item in sorted(grouped.values(), key=lambda x: (x["domain"], x["tool_name"]))
        ]

    def get_alive_results(self, domain=None):
        """查询 dnsx 存活检测结果。

        Args:
            domain: 可选，按域名筛选。

        Returns:
            列表，每行为 (domain, value, tool_name, created_at)。
        """
        query = ["SELECT domain, hostname AS value, 'dnsx' AS tool_name, created_at FROM dnsx_results"]
        params = []
        if domain:
            query.append("WHERE domain = ?")
            params.append(domain)
        query.append("ORDER BY domain ASC, value ASC")
        with self._get_connection() as conn:
            return conn.execute("\n".join(query), params).fetchall()

    def get_alive_overview(self, domain=None):
        """获取存活检测结果的概览统计。

        Args:
            domain: 可选，按域名筛选。

        Returns:
            列表，每行为 (domain, tool_name, total_count, last_scan_at)。
        """
        rows = self.get_alive_results(domain=domain)
        grouped = {}
        for item_domain, hostname, tool_name, created_at in rows:
            key = (item_domain, tool_name)
            current = grouped.get(key)
            if not current:
                grouped[key] = {
                    "domain": item_domain,
                    "tool_name": tool_name,
                    "total_count": 1,
                    "last_scan_at": created_at,
                }
                continue
            current["total_count"] += 1
            if created_at > current["last_scan_at"]:
                current["last_scan_at"] = created_at

        return [
            (item["domain"], item["tool_name"], item["total_count"], item["last_scan_at"])
            for item in sorted(grouped.values(), key=lambda x: (x["domain"], x["tool_name"]))
        ]

    def get_tool_results(self, domain=None, tool_name=None, category=None, limit=200):
        """通用工具结果查询 — 查所属分类的专属表。

        Args:
            domain: 可选，按域名筛选。
            tool_name: 可选，按工具名筛选。
            category: 可选，按分类筛选（暂未在专属表查询中使用）。
            limit: 最大返回数量，默认 200。

        Returns:
            列表，每行为 (domain, tool_name, category, value, created_at)。
        """
        if tool_name and tool_name in TOOL_DATABASES:
            meta = TOOL_DATABASES[tool_name]
            table = meta["table"]
            col = meta["column"]
            cat = meta["category"]
            query = [
                "SELECT domain, ? AS tool_name, ? AS category, "
                + col + " AS value, created_at FROM " + table
            ]
            params = [tool_name, cat]
            conditions = []
            if domain:
                conditions.append("domain = ?")
                params.append(domain)
            if conditions:
                query.append("WHERE " + " AND ".join(conditions))
            query.append("ORDER BY id DESC LIMIT ?")
            params.append(limit)
            with self._get_connection() as conn:
                return conn.execute("\n".join(query), params).fetchall()

        return self._get_tool_results_fallback(tool_name, domain, limit)

    def _get_tool_results_fallback(self, tool_name=None, domain=None, limit=200):
        """回退查询：UNION ALL 所有专属表，返回统一格式结果。

        当未指定工具名或工具未注册时调用。

        Args:
            tool_name: 可选，按工具名筛选。
            domain: 可选，按域名筛选。
            limit: 最大返回数量，默认 200。

        Returns:
            列表，每行为 (domain, tool_name, category, value, created_at)。
        """
        results = []
        filters = list(TOOL_DATABASES.items())
        if tool_name and tool_name in TOOL_DATABASES:
            filters = [(tool_name, TOOL_DATABASES[tool_name])]

        with self._get_connection() as conn:
            for tool, meta in filters:
                table = meta["table"]
                col = meta["column"]
                cat = meta["category"]
                query = [
                    "SELECT domain, ? AS tool_name, ? AS category, "
                    + col + " AS value, created_at FROM " + table
                ]
                params = [tool, cat]
                conditions = []
                if domain:
                    conditions.append("domain = ?")
                    params.append(domain)
                if conditions:
                    query.append("WHERE " + " AND ".join(conditions))
                query.append("ORDER BY id DESC LIMIT ?")
                params.append(limit)
                results.extend(conn.execute("\n".join(query), params).fetchall())
                # 如果已收集足够数量，提前退出
                if len(results) >= limit:
                    break

        return results[:limit]
