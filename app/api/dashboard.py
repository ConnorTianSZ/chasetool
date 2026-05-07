"""API: Dashboard with project ID"""
from __future__ import annotations

from datetime import date
from typing import Optional
from fastapi import APIRouter, Path as FPath, Query, HTTPException
from pydantic import BaseModel
from app.db.connection import get_connection

router = APIRouter(prefix="/api/projects/{project_id}/dashboard", tags=["dashboard"])


@router.get("/overview")
def overview(project_id: str = FPath(...)):
    conn = get_connection(project_id)
    try:
        row = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) as open_count,
                SUM(CASE WHEN current_eta < date('now') AND status='open' THEN 1 ELSE 0 END) as overdue_count,
                SUM(CASE WHEN (current_eta IS NULL OR current_eta='') AND status='open' THEN 1 ELSE 0 END) as no_eta_count,
                SUM(CASE WHEN is_focus=1 THEN 1 ELSE 0 END) as focus_count,
                SUM(CASE WHEN status='delivered' THEN 1 ELSE 0 END) as delivered_count
            FROM materials
        """).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


@router.get("/aggregates")
def aggregates(
    project_id:  str = FPath(...),
    group_by:    str = Query("status"),
    status:      str = Query(None),
    buyer_email: str = Query(None),
):
    allowed = {"status", "supplier", "buyer_email", "buyer_name", "station_no", "purchasing_group"}
    if group_by not in allowed:
        group_by = "status"

    conditions, params = [], []
    if status:
        conditions.append("status=?")
        params.append(status)
    if buyer_email:
        conditions.append("buyer_email=?")
        params.append(buyer_email)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    conn = get_connection(project_id)
    try:
        rows = conn.execute(
            f"SELECT {group_by} as g, COUNT(*) as cnt "
            f"FROM materials {where} GROUP BY {group_by} ORDER BY cnt DESC",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── 时间节点管理（Dashboard 交期预测）────────────────────────────────────


@router.get("/overdue_by_supplier")
def overdue_by_supplier(project_id: str = FPath(...)):
    conn = get_connection(project_id)
    try:
        rows = conn.execute("""
            SELECT supplier, COUNT(*) as overdue_count
            FROM materials
            WHERE status='open' AND current_eta IS NOT NULL AND current_eta < date('now')
            GROUP BY supplier ORDER BY overdue_count DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/chase_stats")
def chase_stats(project_id: str = FPath(...)):
    conn = get_connection(project_id)
    try:
        rows = conn.execute("""
            SELECT
                COALESCE(buyer_name, '未知') as buyer_name,
                COUNT(*) as total,
                SUM(chase_count) as total_chases,
                SUM(CASE WHEN supplier_feedback_time IS NOT NULL THEN 1 ELSE 0 END) as replied
            FROM materials
            GROUP BY buyer_name
            ORDER BY total DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


class TimeNodeCreate(BaseModel):
    label: str
    node_date: date
    color: str = "#2563eb"
    sort_order: int = 0


class TimeNodeUpdate(BaseModel):
    label: Optional[str] = None
    node_date: Optional[date] = None
    color: Optional[str] = None
    sort_order: Optional[int] = None


@router.get("/time_nodes")
def list_time_nodes(project_id: str = FPath(...)):
    conn = get_connection(project_id)
    try:
        rows = conn.execute(
            "SELECT * FROM time_nodes ORDER BY sort_order ASC, node_date ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/time_nodes")
def create_time_node(project_id: str = FPath(...), body: TimeNodeCreate = None):
    conn = get_connection(project_id)
    try:
        cur = conn.execute(
            "INSERT INTO time_nodes (label, node_date, color, sort_order) VALUES (?, ?, ?, ?)",
            (body.label, body.node_date.isoformat(), body.color, body.sort_order),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM time_nodes WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.put("/time_nodes/{node_id}")
def update_time_node(
    project_id: str = FPath(...), node_id: int = FPath(...), body: TimeNodeUpdate = None
):
    fields = {}
    if body.label is not None:
        fields["label"] = body.label
    if body.node_date is not None:
        fields["node_date"] = body.node_date.isoformat()
    if body.color is not None:
        fields["color"] = body.color
    if body.sort_order is not None:
        fields["sort_order"] = body.sort_order
    if not fields:
        raise HTTPException(400, "No fields to update")

    set_clause = ", ".join(f"{k}=?" for k in fields)
    conn = get_connection(project_id)
    try:
        conn.execute(
            f"UPDATE time_nodes SET {set_clause} WHERE id=?",
            [*fields.values(), node_id],
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM time_nodes WHERE id=?", (node_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Time node not found")
        return dict(row)
    finally:
        conn.close()


@router.delete("/time_nodes/{node_id}")
def delete_time_node(project_id: str = FPath(...), node_id: int = FPath(...)):
    conn = get_connection(project_id)
    try:
        conn.execute("DELETE FROM time_nodes WHERE id=?", (node_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.get("/time_node_stats")
def time_node_stats(project_id: str = FPath(...)):
    """返回每个时间节点的物料交期对比统计"""
    conn = get_connection(project_id)
    try:
        nodes = conn.execute(
            "SELECT * FROM time_nodes ORDER BY sort_order ASC, node_date ASC"
        ).fetchall()

        total_open = conn.execute(
            "SELECT COUNT(*) FROM materials WHERE status='open'"
        ).fetchone()[0]

        result = []
        for node in nodes:
            n = dict(node)
            due = conn.execute(
                "SELECT COUNT(*) FROM materials WHERE status='open' AND current_eta IS NOT NULL AND current_eta <= ?",
                (n["node_date"],),
            ).fetchone()[0]
            no_eta = conn.execute(
                "SELECT COUNT(*) FROM materials WHERE status='open' AND (current_eta IS NULL OR current_eta='')"
            ).fetchone()[0]
            overdue = conn.execute(
                "SELECT COUNT(*) FROM materials WHERE status='open' AND current_eta IS NOT NULL AND current_eta < date('now')"
            ).fetchone()[0]
            by_supplier = conn.execute(
                """SELECT supplier, COUNT(*) as cnt FROM materials
                   WHERE status='open' AND current_eta IS NOT NULL AND current_eta <= ?
                   GROUP BY supplier ORDER BY cnt DESC LIMIT 5""",
                (n["node_date"],),
            ).fetchall()

            n["due_count"] = due
            n["no_eta_count"] = no_eta
            n["overdue_count"] = overdue
            n["total_open"] = total_open
            n["by_supplier"] = [dict(r) for r in by_supplier]
            result.append(n)

        return result
    finally:
        conn.close()


@router.get("/time_node_drilldown")
def time_node_drilldown(
    project_id: str = FPath(...),
    group_by: str = "buyer_name",
):
    """返回每个时间节点按采购员或供应商的到期物料分组统计"""
    allowed = {"buyer_name", "supplier"}
    if group_by not in allowed:
        group_by = "buyer_name"

    conn = get_connection(project_id)
    try:
        nodes = conn.execute(
            "SELECT * FROM time_nodes ORDER BY sort_order ASC, node_date ASC"
        ).fetchall()

        result = []
        for node in nodes:
            n = dict(node)
            rows = conn.execute(
                f"""SELECT {group_by} as name, COUNT(*) as due_count
                    FROM materials
                    WHERE status='open' AND current_eta IS NOT NULL AND current_eta <= ?
                    GROUP BY {group_by}
                    ORDER BY due_count DESC
                    LIMIT 20""",
                (n["node_date"],),
            ).fetchall()
            n["groups"] = [dict(r) for r in rows]
            result.append(n)

        return result
    finally:
        conn.close()
