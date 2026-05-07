"""API: Materials CRUD with project ID"""
from __future__ import annotations
from datetime import date
from typing import Optional
from fastapi import APIRouter, Query, HTTPException, Path
from pydantic import BaseModel
from app.db.connection import get_connection
from app.models.material import MaterialUpdate
from app.update_policy import bulk_update_fields, try_update_field
from app.services.material_view import clean_date_value, enrich_material_row, load_pgr_map

router = APIRouter(prefix="/api/projects/{project_id}/materials", tags=["materials"])


class KeyDateBody(BaseModel):
    key_date: Optional[str] = None


def _get_material_key_date(conn) -> str:
    row = conn.execute(
        "SELECT value FROM project_settings WHERE key='material_key_date'"
    ).fetchone()
    return clean_date_value(row["value"]) if row and row["value"] else date.today().isoformat()


def _set_material_key_date(conn, key_date: str | None) -> str:
    cleaned = clean_date_value(key_date) or date.today().isoformat()
    conn.execute(
        """INSERT INTO project_settings (key, value, updated_at)
           VALUES ('material_key_date', ?, CURRENT_TIMESTAMP)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP""",
        (cleaned,),
    )
    return cleaned


def _query_values(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        values = [value]
    else:
        values = list(value)
    result: list[str] = []
    for item in values:
        for part in str(item).split(","):
            part = part.strip()
            if part:
                result.append(part)
    return result


def _pgr_codes_for_buyer(key: str, pgr_map: dict[str, dict]) -> list[str]:
    key = key.strip().lower()
    codes = []
    for pg, info in pgr_map.items():
        email = (info.get("email") or "").strip().lower()
        name = (info.get("name") or "").strip().lower()
        if key.startswith("email:") and email and key == "email:" + email:
            codes.append(pg)
        elif key.startswith("name:") and name and key == "name:" + name:
            codes.append(pg)
    return codes


def _add_buyer_filter(
    conditions: list[str],
    params: list,
    buyer_keys,
    pgr_map: dict[str, dict],
) -> None:
    keys = _query_values(buyer_keys)
    if not keys:
        return

    clauses: list[str] = []
    for raw_key in keys:
        key = raw_key.strip().lower()
        if key.startswith("email:"):
            clauses.append("LOWER(COALESCE(buyer_email, '')) = ?")
            params.append(key.removeprefix("email:"))
        elif key.startswith("name:"):
            clauses.append("LOWER(COALESCE(buyer_name, '')) = ?")
            params.append(key.removeprefix("name:"))
        else:
            clauses.append("(LOWER(COALESCE(buyer_email, '')) = ? OR LOWER(COALESCE(buyer_name, '')) = ?)")
            params.extend([key, key])

        pgr_codes = _pgr_codes_for_buyer(key, pgr_map)
        if pgr_codes:
            placeholders = ",".join("?" * len(pgr_codes))
            clauses.append(f"UPPER(COALESCE(purchasing_group, '')) IN ({placeholders})")
            params.extend(pgr_codes)

    if clauses:
        conditions.append("(" + " OR ".join(clauses) + ")")


def _not_delivered_sql() -> str:
    return "(open_quantity_gr IS NULL OR CAST(open_quantity_gr AS REAL) <> 0)"


def _delivered_sql() -> str:
    return "(open_quantity_gr IS NOT NULL AND CAST(open_quantity_gr AS REAL) = 0)"


def _add_material_state_filter(
    conditions: list[str],
    params: list,
    material_state: str | None,
    key_date: str,
) -> None:
    if material_state == "delivered":
        conditions.append(_delivered_sql())
    elif material_state == "no_oc":
        conditions.append(_not_delivered_sql())
        conditions.append("(current_eta IS NULL OR current_eta = '')")
    elif material_state == "overdue":
        conditions.append(_not_delivered_sql())
        conditions.append("current_eta IS NOT NULL AND current_eta <> '' AND date(current_eta) < date(?)")
        params.append(key_date)
    elif material_state == "normal":
        conditions.append(_not_delivered_sql())
        conditions.append("current_eta IS NOT NULL AND current_eta <> '' AND date(current_eta) >= date(?)")
        params.append(key_date)


@router.get("")
def list_materials(
    project_id:  str            = Path(...),
    po_number:   Optional[str]  = Query(None),
    buyer_email: Optional[str]  = Query(None),
    buyer_key:   Optional[list[str]] = Query(None),
    supplier:    Optional[str]  = Query(None),
    status:      Optional[str]  = Query(None),
    material_state: Optional[str] = Query(None),
    station_no:  Optional[str]  = Query(None),
    purchasing_group: Optional[str] = Query(None),
    is_focus:    Optional[bool] = Query(None),
    overdue:     bool           = Query(False),
    no_eta:      bool           = Query(False),
    search:      Optional[str]  = Query(None),
    key_date:    Optional[str]  = Query(None),
    page:        int            = Query(1, ge=1),
    page_size:   int            = Query(50, ge=1, le=500),
):
    conditions, params = [], []
    pgr_map = load_pgr_map()

    conn = get_connection(project_id)
    try:
        effective_key_date = clean_date_value(key_date) or _get_material_key_date(conn)

        if po_number:
            conditions.append("po_number LIKE ?")
            params.append(f"%{po_number}%")
        if buyer_email:
            conditions.append("buyer_email = ?")
            params.append(buyer_email)
        _add_buyer_filter(conditions, params, buyer_key, pgr_map)
        if supplier:
            conditions.append("supplier LIKE ?")
            params.append(f"%{supplier}%")
        if status:
            conditions.append("status = ?")
            params.append(status)
        _add_material_state_filter(conditions, params, material_state, effective_key_date)
        if station_no:
            conditions.append("station_no = ?")
            params.append(station_no)
        if purchasing_group:
            conditions.append("purchasing_group = ?")
            params.append(purchasing_group.upper())
        if is_focus is not None:
            conditions.append("is_focus = ?")
            params.append(1 if is_focus else 0)
        if overdue:
            conditions.append(_not_delivered_sql())
            conditions.append("current_eta IS NOT NULL AND current_eta <> '' AND date(current_eta) < date(?)")
            params.append(effective_key_date)
        if no_eta:
            conditions.append(_not_delivered_sql())
            conditions.append("(current_eta IS NULL OR current_eta = '')")
        if search:
            conditions.append(
                "(po_number LIKE ? OR part_no LIKE ? OR description LIKE ? OR supplier LIKE ?)"
            )
            like = f"%{search}%"
            params += [like, like, like, like]

        where  = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        offset = (page - 1) * page_size

        total = conn.execute(f"SELECT COUNT(*) FROM materials {where}", params).fetchone()[0]
        rows  = conn.execute(
            f"SELECT * FROM materials {where} "
            f"ORDER BY "
            f"CASE WHEN {_delivered_sql()} THEN 1 ELSE 0 END ASC, "
            f"CASE WHEN current_eta IS NULL OR current_eta = '' THEN 1 ELSE 0 END ASC, "
            f"date(current_eta) ASC, po_number ASC, item_no ASC "
            f"LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "key_date": effective_key_date,
            "items": [enrich_material_row(dict(r), effective_key_date, pgr_map) for r in rows],
        }
    finally:
        conn.close()


@router.get("/filter_options")
def filter_options(project_id: str = Path(...)):
    pgr_map = load_pgr_map()
    conn = get_connection(project_id)
    try:
        stations  = [r[0] for r in conn.execute(
            "SELECT DISTINCT station_no FROM materials WHERE station_no IS NOT NULL ORDER BY station_no"
        ).fetchall()]
        pgs = [r[0] for r in conn.execute(
            "SELECT DISTINCT purchasing_group FROM materials WHERE purchasing_group IS NOT NULL ORDER BY purchasing_group"
        ).fetchall()]
        suppliers = [r[0] for r in conn.execute(
            "SELECT DISTINCT supplier FROM materials WHERE supplier IS NOT NULL ORDER BY supplier"
        ).fetchall()]
        buyer_rows = conn.execute(
            """SELECT DISTINCT buyer_name, buyer_email, purchasing_group
               FROM materials
               WHERE buyer_name IS NOT NULL OR buyer_email IS NOT NULL OR purchasing_group IS NOT NULL"""
        ).fetchall()
        buyers_by_key: dict[str, dict[str, str]] = {}
        for row in buyer_rows:
            enriched = enrich_material_row(dict(row), pgr_map=pgr_map)
            key = enriched["buyer_key"]
            buyers_by_key.setdefault(key, {
                "key": key,
                "name": enriched.get("buyer_name") or "",
                "email": enriched.get("buyer_email") or "",
            })
        buyers = sorted(
            buyers_by_key.values(),
            key=lambda b: ((b.get("name") or b.get("email") or "").lower()),
        )
        return {"stations": stations, "purchasing_groups": pgs, "suppliers": suppliers, "buyers": buyers}
    finally:
        conn.close()


@router.get("/key_date")
def get_key_date(project_id: str = Path(...)):
    conn = get_connection(project_id)
    try:
        key_date = _get_material_key_date(conn)
        return {"key_date": key_date, "display_key_date": key_date.replace("-", "/")}
    finally:
        conn.close()


@router.put("/key_date")
def update_key_date(body: KeyDateBody, project_id: str = Path(...)):
    conn = get_connection(project_id)
    try:
        key_date = _set_material_key_date(conn, body.key_date)
        conn.commit()
        return {"key_date": key_date, "display_key_date": key_date.replace("-", "/")}
    finally:
        conn.close()


@router.get("/{material_id}")
def get_material(project_id: str = Path(...), material_id: int = Path(...)):
    conn = get_connection(project_id)
    try:
        row = conn.execute("SELECT * FROM materials WHERE id=?", (material_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Material not found")
        return dict(row)
    finally:
        conn.close()


@router.patch("/{material_id}")
def update_material(
    material_id: int,
    body: MaterialUpdate,
    project_id: str = Path(...),
    source: str = "chat_command",
):
    updates = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if not updates:
        return {"ok": True, "updated": 0}
    conn = get_connection(project_id)
    try:
        results = bulk_update_fields(conn, material_id, updates, source=source)
        conn.commit()
        return {"ok": True, "results": {k: {"ok": ok, "reason": r} for k, (ok, r) in results.items()}}
    finally:
        conn.close()


@router.delete("/{material_id}")
def delete_material(project_id: str = Path(...), material_id: int = Path(...)):
    conn = get_connection(project_id)
    try:
        conn.execute("DELETE FROM materials WHERE id=?", (material_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.get("/{material_id}/history")
def material_history(project_id: str = Path(...), material_id: int = Path(...)):
    """获取单行物料的所有字段更新历史"""
    conn = get_connection(project_id)
    try:
        rows = conn.execute(
            "SELECT * FROM field_updates WHERE material_id=? ORDER BY timestamp DESC LIMIT 50",
            (material_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/{material_id}/toggle_focus")
def toggle_focus(project_id: str = Path(...), material_id: int = Path(...)):
    """切换 is_focus 标记"""
    conn = get_connection(project_id)
    try:
        row = conn.execute("SELECT is_focus FROM materials WHERE id=?", (material_id,)).fetchone()
        if not row:
            from fastapi import HTTPException
            raise HTTPException(404, "Material not found")
        new_val = 0 if row["is_focus"] else 1
        conn.execute("UPDATE materials SET is_focus=? WHERE id=?", (new_val, material_id))
        conn.commit()
        return {"ok": True, "is_focus": bool(new_val)}
    finally:
        conn.close()
