"""LLM Tool: Dashboard 聚合查询"""
from __future__ import annotations
from app.db.connection import get_connection


def query_aggregates(group_by: str = "status", filters: dict | None = None) -> list[dict]:
    """
    聚合查询（只读），支持按 status / supplier / buyer_email 分组。
    """
    allowed_group_by = {"status", "supplier", "buyer_email", "buyer_name"}
    if group_by not in allowed_group_by:
        group_by = "status"

    conditions = []
    params: list = []
    if filters:
        if filters.get("status"):
            conditions.append("status=?")
            params.append(filters["status"])
        if filters.get("buyer_email"):
            conditions.append("buyer_email=?")
            params.append(filters["buyer_email"])

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        SELECT {group_by} as group_key,
               COUNT(*) as total,
               SUM(CASE WHEN current_eta < date('now') AND status='open' THEN 1 ELSE 0 END) as overdue,
               SUM(CASE WHEN is_focus=1 THEN 1 ELSE 0 END) as focus_count,
               AVG(chase_count) as avg_chase_count
        FROM materials {where}
        GROUP BY {group_by}
        ORDER BY total DESC
        LIMIT 50
    """
    conn = get_connection()
    try:
        cur = conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_overview() -> dict:
    """首页概览数据"""
    conn = get_connection()
    try:
        cur = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) as open_count,
                SUM(CASE WHEN current_eta < date('now') AND status='open' THEN 1 ELSE 0 END) as overdue_count,
                SUM(CASE WHEN is_focus=1 THEN 1 ELSE 0 END) as focus_count,
                SUM(CASE WHEN status='delivered' THEN 1 ELSE 0 END) as delivered_count
            FROM materials
        """)
        row = cur.fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()
