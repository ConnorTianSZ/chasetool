"""LLM Tool: 多条件筛选物料"""
from __future__ import annotations
import sqlite3
from app.db.connection import get_connection


def search_materials(
    po_number: str | None = None,
    buyer_email: str | None = None,
    supplier: str | None = None,
    status: str | None = None,
    is_focus: bool | None = None,
    overdue_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """多条件筛选，只读"""
    conditions = []
    params: list = []

    if po_number:
        conditions.append("po_number LIKE ?")
        params.append(f"%{po_number}%")
    if buyer_email:
        conditions.append("buyer_email = ?")
        params.append(buyer_email)
    if supplier:
        conditions.append("supplier LIKE ?")
        params.append(f"%{supplier}%")
    if status:
        conditions.append("status = ?")
        params.append(status)
    if is_focus is not None:
        conditions.append("is_focus = ?")
        params.append(1 if is_focus else 0)
    if overdue_only:
        conditions.append("current_eta < date('now') AND status = 'open'")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"SELECT * FROM materials {where} ORDER BY current_eta ASC LIMIT ? OFFSET ?"
    params += [limit, offset]

    conn = get_connection()
    try:
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_material(po_number: str, item_no: str) -> dict | None:
    """单行详情，只读"""
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT * FROM materials WHERE po_number=? AND item_no=?",
            (po_number, item_no),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
