"""API: Dashboard with project ID"""
from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Optional
from fastapi import APIRouter, Path as FPath, Query, HTTPException
from pydantic import BaseModel, Field
from app.db.connection import get_connection
from app.services.material_view import clean_date_value, enrich_material_row, load_pgr_map
from app.services.outlook_send import create_display_draft

router = APIRouter(prefix="/api/projects/{project_id}/dashboard", tags=["dashboard"])


STATE_LABELS = {
    "no_oc": "无OC",
    "overdue_now": "应交未交",
    "overdue_keydate": "晚于节点",
    "chased_no_feedback": "已催未回复",
    "eta_mismatch": "交期不一致",
}

EXPORT_STATE_ORDER = [
    "no_oc",
    "overdue_now",
    "overdue_keydate",
    "chased_no_feedback",
    "eta_mismatch",
]


class LeadBuyerExportDraftRequest(BaseModel):
    buyer_keys: list[str] = Field(default_factory=list)
    include_states: list[str] = Field(default_factory=lambda: list(EXPORT_STATE_ORDER))
    eta_source: str = "current_eta"
    key_date: Optional[str] = None
    to: str = ""
    cc: str = ""
    subject: str = ""


def _get_dashboard_key_date(conn, key_date: str | None = None) -> str:
    cleaned = clean_date_value(key_date)
    if cleaned:
        return cleaned
    row = conn.execute(
        "SELECT value FROM project_settings WHERE key='material_key_date'"
    ).fetchone()
    return clean_date_value(row["value"]) if row and row["value"] else date.today().isoformat()


def _apply_eta_source(row: dict, eta_source: str) -> dict:
    """当 eta_source='supplier_eta' 时，用 supplier_eta 替换 current_eta（空则 fallback）。"""
    if eta_source != "supplier_eta":
        return row
    s_eta = clean_date_value(row.get("supplier_eta"))
    c_eta = clean_date_value(row.get("current_eta"))
    row = dict(row)
    row["current_eta"] = s_eta if s_eta else c_eta
    return row


def _load_enriched_materials(
    project_id: str,
    key_date: str | None = None,
    eta_source: str = "current_eta",
) -> tuple[list[dict], str]:
    pgr_map = load_pgr_map()
    conn = get_connection(project_id)
    try:
        effective_key_date = _get_dashboard_key_date(conn, key_date)
        rows = conn.execute("SELECT * FROM materials").fetchall()
    finally:
        conn.close()
    enriched = []
    for r in rows:
        row = _apply_eta_source(dict(r), eta_source)
        enriched.append(enrich_material_row(row, effective_key_date, pgr_map))
    return enriched, effective_key_date


def _is_open_material(item: dict) -> bool:
    return (
        (item.get("status") or "open") == "open"
        and item.get("material_state") != "delivered"
    )


def _is_risk_material(item: dict) -> bool:
    return (
        item.get("material_state") in {"no_oc", "overdue_now", "overdue_keydate", "eta_mismatch"}
        or item.get("chase_state") == "chased_no_feedback"
        or bool(item.get("is_focus"))
    )


def _counter_top(counter: Counter, limit: int = 5) -> list[dict]:
    return [
        {"name": name, "count": count}
        for name, count in sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0]).lower()))[:limit]
    ]


def _display_name(value: str | None, fallback: str) -> str:
    text = (value or "").strip()
    return text if text else fallback


def _selected_labels(item: dict, include_states: set[str]) -> list[str]:
    return [STATE_LABELS[state_id] for state_id in _selected_state_ids(item, include_states)]


def _selected_state_ids(item: dict, include_states: set[str]) -> list[str]:
    labels = []
    state = item.get("material_state")
    if state in include_states and state in STATE_LABELS:
        labels.append(state)
    if "chased_no_feedback" in include_states and item.get("chase_state") == "chased_no_feedback":
        labels.append("chased_no_feedback")
    return labels


def _html_escape(value) -> str:
    import html

    return html.escape("" if value is None else str(value))


def _build_lead_buyer_html(materials: list[dict], include_states: set[str], key_date: str) -> str:
    by_buyer: dict[str, list[dict]] = {}
    for item in materials:
        labels = _selected_labels(item, include_states)
        if not labels:
            continue
        item = dict(item)
        item["_dashboard_labels"] = labels
        item["_dashboard_state_ids"] = _selected_state_ids(item, include_states)
        by_buyer.setdefault(item.get("buyer_display") or "未知", []).append(item)

    styles = (
        "font-family:Arial,'Microsoft YaHei',sans-serif;font-size:13px;color:#111827;"
    )
    table_style = "border-collapse:collapse;width:100%;font-size:12px;margin:8px 0 18px;"
    th_style = "border:1px solid #d1d5db;background:#f3f4f6;padding:6px;text-align:left;"
    td_style = "border:1px solid #e5e7eb;padding:5px;vertical-align:top;"
    html_parts = [
        f"<html><body style=\"{styles}\">",
        f"<p>各位好，以下为 Dashboard 导出的项目交期风险清单，KEY DATE: <strong>{_html_escape(key_date)}</strong>。</p>",
        "<p>请对应采购员优先处理无 OC、应交未交、晚于节点及已催未回复的物料。</p>",
    ]

    headers = ["采购员", "状态", "PO", "Item", "供应商", "制造商", "料号", "描述", "当前交期", "催货状态"]
    for buyer in sorted(by_buyer):
        rows = by_buyer[buyer]
        html_parts.append(f"<h3 style=\"margin:18px 0 6px;\">{_html_escape(buyer)} ({len(rows)} 条)</h3>")
        html_parts.append(f"<table style=\"{table_style}\"><thead><tr>")
        for h in headers:
            html_parts.append(f"<th style=\"{th_style}\">{_html_escape(h)}</th>")
        html_parts.append("</tr></thead><tbody>")
        for item in sorted(
            rows,
            key=lambda m: (
                EXPORT_STATE_ORDER.index(m["_dashboard_state_ids"][0]) if m["_dashboard_state_ids"] else 99,
                m.get("current_eta") or "",
                m.get("po_number") or "",
                m.get("item_no") or "",
            ),
        ):
            values = [
                item.get("buyer_display") or "",
                " / ".join(item["_dashboard_labels"]),
                item.get("po_number") or "",
                item.get("item_no") or "",
                item.get("supplier") or "",
                item.get("manufacturer") or "",
                item.get("part_no") or "",
                item.get("description") or "",
                item.get("display_current_eta") or "",
                item.get("chase_label") or "",
            ]
            html_parts.append("<tr>")
            for value in values:
                html_parts.append(f"<td style=\"{td_style}\">{_html_escape(value)}</td>")
            html_parts.append("</tr>")
        html_parts.append("</tbody></table>")

    html_parts.append("<p>Best regards</p>")
    html_parts.append("</body></html>")
    return "".join(html_parts)


def _filter_export_materials(
    materials: list[dict],
    buyer_keys: set[str],
    include_states: set[str],
) -> list[dict]:
    selected = []
    for item in materials:
        if not _is_open_material(item):
            continue
        if buyer_keys and item.get("buyer_key") not in buyer_keys:
            continue
        if _selected_labels(item, include_states):
            selected.append(item)
    return selected


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


@router.get("/lead_buyer")
def lead_buyer(
    project_id: str = FPath(...),
    key_date: str | None = Query(None),
    eta_source: str = Query("current_eta"),
    evidence_by: str = Query("manufacturer"),
):
    if eta_source not in {"current_eta", "supplier_eta"}:
        eta_source = "current_eta"
    if evidence_by not in {"supplier", "manufacturer"}:
        evidence_by = "manufacturer"

    materials, effective_key_date = _load_enriched_materials(project_id, key_date, eta_source)
    buyer_map: dict[str, dict] = {}
    summary = {
        "no_oc": 0,
        "overdue_now": 0,
        "overdue_keydate": 0,
        "chased_no_feedback": 0,
        "eta_mismatch": 0,
        "focus": 0,
    }
    global_manufacturers: Counter = Counter()
    late_evidence: Counter = Counter()

    for item in materials:
        if not _is_open_material(item):
            continue

        buyer_key = item.get("buyer_key") or "name:未知"
        buyer = buyer_map.setdefault(
            buyer_key,
            {
                "buyer_key": buyer_key,
                "buyer_name": item.get("buyer_name") or item.get("buyer_email") or "未知",
                "buyer_email": item.get("buyer_email") or "",
                "buyer_display": item.get("buyer_display") or "未知",
                "open_count": 0,
                "no_oc_count": 0,
                "overdue_now_count": 0,
                "overdue_keydate_count": 0,
                "chased_no_feedback_count": 0,
                "eta_mismatch_count": 0,
                "focus_count": 0,
                "_suppliers": Counter(),
                "_manufacturers": Counter(),
            },
        )
        buyer["open_count"] += 1

        state = item.get("material_state")
        if state == "no_oc":
            buyer["no_oc_count"] += 1
            summary["no_oc"] += 1
        elif state == "overdue_now":
            buyer["overdue_now_count"] += 1
            summary["overdue_now"] += 1
        elif state == "overdue_keydate":
            buyer["overdue_keydate_count"] += 1
            summary["overdue_keydate"] += 1
            evidence_name = item.get(evidence_by) if evidence_by == "supplier" else item.get("manufacturer")
            late_evidence[_display_name(evidence_name, "unknown")] += 1
        elif state == "eta_mismatch":
            buyer["eta_mismatch_count"] += 1
            summary["eta_mismatch"] += 1

        if item.get("chase_state") == "chased_no_feedback":
            buyer["chased_no_feedback_count"] += 1
            summary["chased_no_feedback"] += 1
        if item.get("is_focus"):
            buyer["focus_count"] += 1
            summary["focus"] += 1

        if _is_risk_material(item):
            supplier = _display_name(item.get("supplier"), "unknown supplier")
            manufacturer = _display_name(item.get("manufacturer"), "未知制造商")
            buyer["_suppliers"][supplier] += 1
            buyer["_manufacturers"][manufacturer] += 1
            global_manufacturers[manufacturer] += 1

    buyer_rows = []
    for buyer in buyer_map.values():
        row = dict(buyer)
        suppliers = row.pop("_suppliers")
        manufacturers = row.pop("_manufacturers")
        row["top_suppliers"] = _counter_top(suppliers, limit=3)
        row["top_manufacturers"] = _counter_top(manufacturers, limit=3)
        buyer_rows.append(row)

    buyer_rows.sort(
        key=lambda r: (
            -r["overdue_now_count"],
            -r["no_oc_count"],
            -r["overdue_keydate_count"],
            -r["chased_no_feedback_count"],
            str(r["buyer_display"]).lower(),
        )
    )

    cards = [
        {"id": "no_oc", "label": "无OC", "value": summary["no_oc"], "tone": "warning"},
        {"id": "overdue_now", "label": "应交未交", "value": summary["overdue_now"], "tone": "danger"},
        {"id": "overdue_keydate", "label": "晚于节点", "value": summary["overdue_keydate"], "tone": "danger"},
        {"id": "chased_no_feedback", "label": "已催未回复", "value": summary["chased_no_feedback"], "tone": "warning"},
        {"id": "eta_mismatch", "label": "交期不一致", "value": summary["eta_mismatch"], "tone": "warning"},
        {"id": "focus", "label": "重点关注", "value": summary["focus"], "tone": "primary"},
    ]

    return {
        "key_date": effective_key_date,
        "summary_cards": cards,
        "buyer_rows": buyer_rows,
        "late_evidence": _counter_top(late_evidence, limit=10),
        "top_manufacturers": _counter_top(global_manufacturers, limit=10),
        "config": {"eta_source": eta_source},
    }


@router.post("/lead_buyer/export_draft")
def export_lead_buyer_draft(
    body: LeadBuyerExportDraftRequest,
    project_id: str = FPath(...),
):
    include_states = {s for s in body.include_states if s in STATE_LABELS}
    if not include_states:
        include_states = set(EXPORT_STATE_ORDER)

    eta_source = body.eta_source if body.eta_source in {"current_eta", "supplier_eta"} else "current_eta"
    materials, effective_key_date = _load_enriched_materials(project_id, body.key_date, eta_source)
    selected = _filter_export_materials(
        materials,
        buyer_keys=set(body.buyer_keys or []),
        include_states=include_states,
    )

    subject = body.subject.strip() or f"Dashboard follow up - {effective_key_date}"
    html_body = _build_lead_buyer_html(selected, include_states, effective_key_date)
    draft = create_display_draft(
        to_address=body.to,
        cc=body.cc,
        subject=subject,
        html_body=html_body,
    )
    return {
        "ok": True,
        "material_count": len(selected),
        "draft": draft,
    }


class CustomExportDraftRequest(BaseModel):
    html_body: str
    to: str = ""
    cc: str = ""
    subject: str = ""


@router.post("/export_custom_draft")
def export_custom_draft(
    body: CustomExportDraftRequest,
    project_id: str = FPath(...),
):
    """接收前端组装好的 HTML 正文，直接创建 Outlook 草稿。"""
    subject = body.subject.strip() or "物料概览"
    draft = create_display_draft(
        to_address=body.to,
        cc=body.cc,
        subject=subject,
        html_body=body.html_body,
    )
    return {"ok": True, "draft": draft}


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


# ── Pivot A：Buyer × Doc.Date × Value ────────────────────────────────────────


@router.get("/pivot_buyer_docdate")
def pivot_buyer_docdate(
    project_id: str = FPath(...),
    value_type: str = Query("overdue_keydate"),
    eta_source: str = Query("current_eta"),
    key_date: str | None = Query(None),
):
    """
    行 = Buyer，列 = Doc.Date（MM/DD），值 = 指定 value_type 的物料数。
    value_type: no_oc | overdue_now | overdue_keydate
    """
    if value_type not in {"no_oc", "overdue_now", "overdue_keydate"}:
        value_type = "overdue_keydate"
    if eta_source not in {"current_eta", "supplier_eta"}:
        eta_source = "current_eta"

    materials, effective_key_date = _load_enriched_materials(project_id, key_date, eta_source)

    cells: dict[str, dict[str, int]] = {}
    date_set: set[str] = set()
    buyer_order: list[str] = []

    for item in materials:
        if not _is_open_material(item):
            continue
        state = item.get("material_state")
        hit = (state == value_type)
        if not hit:
            continue

        buyer = item.get("buyer_display") or "未知"
        raw_date = item.get("order_date") or ""
        if raw_date and len(raw_date) >= 10:
            try:
                from datetime import date as _date
                d = _date.fromisoformat(raw_date[:10])
                col = f"{d.month:02d}/{d.day:02d}"
            except ValueError:
                col = raw_date[:10]
        else:
            col = raw_date or "无日期"

        if buyer not in cells:
            cells[buyer] = {}
            buyer_order.append(buyer)
        cells[buyer][col] = cells[buyer].get(col, 0) + 1
        date_set.add(col)

    def _sort_mmdd(s: str):
        try:
            m, d = s.split("/")
            return (int(m), int(d))
        except Exception:
            return (99, 99)

    dates = sorted(date_set, key=_sort_mmdd)
    row_totals = {b: sum(cells[b].values()) for b in buyer_order}
    col_totals = {dt: sum(cells[b].get(dt, 0) for b in buyer_order) for dt in dates}
    buyer_order.sort(key=lambda b: -row_totals[b])

    return {
        "key_date": effective_key_date,
        "value_type": value_type,
        "eta_source": eta_source,
        "buyers": buyer_order,
        "dates": dates,
        "cells": cells,
        "row_totals": row_totals,
        "col_totals": col_totals,
    }


# ── Pivot B：Buyer → Manufacturer × 晚于节点 ─────────────────────────────────


@router.get("/pivot_buyer_manufacturer")
def pivot_buyer_manufacturer(
    project_id: str = FPath(...),
    eta_source: str = Query("current_eta"),
    key_date: str | None = Query(None),
):
    """行 = Buyer（可展开）→ Manufacturer，值 = 晚于节点数。"""
    if eta_source not in {"current_eta", "supplier_eta"}:
        eta_source = "current_eta"

    materials, effective_key_date = _load_enriched_materials(project_id, key_date, eta_source)

    buyer_map: dict[str, dict] = {}

    for item in materials:
        if not _is_open_material(item):
            continue
        if item.get("material_state") != "overdue_keydate":
            continue

        buyer = item.get("buyer_display") or "未知"
        buyer_email = item.get("buyer_email") or ""
        buyer_key_val = item.get("buyer_key") or ("name:" + buyer.lower())
        mfr = _display_name(item.get("manufacturer"), "未知制造商")

        if buyer_key_val not in buyer_map:
            buyer_map[buyer_key_val] = {
                "buyer": buyer,
                "buyer_key": buyer_key_val,
                "buyer_email": buyer_email,
                "total": 0,
                "_mfr": Counter(),
            }
        buyer_map[buyer_key_val]["total"] += 1
        buyer_map[buyer_key_val]["_mfr"][mfr] += 1

    result = []
    for entry in buyer_map.values():
        mfr_counter = entry.pop("_mfr")
        entry["manufacturers"] = [
            {"name": name, "count": cnt}
            for name, cnt in sorted(mfr_counter.items(), key=lambda kv: -kv[1])
        ]
        result.append(entry)

    result.sort(key=lambda r: -r["total"])

    return {
        "key_date": effective_key_date,
        "eta_source": eta_source,
        "rows": result,
    }
