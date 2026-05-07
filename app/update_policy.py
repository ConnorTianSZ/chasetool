"""Field update priority governance"""
import sqlite3
import json
from typing import Any

SOURCE_PRIORITY: dict[str, int] = {
    "email_reply":   3,
    "chat_command":  2,
    "manual_import": 1,
    "system":        0,
}

SENSITIVE_FIELDS = {
    "current_eta",
    "supplier_eta",
    "supplier_remarks",
    "status",
    "is_focus",
}

FACT_FIELDS = {
    "po_number", "item_no", "part_no", "quantity",
    "unit", "original_eta", "buyer_name", "buyer_email", "supplier",
    "wbs_element", "project_no", "station_no", "purchasing_group", "order_date",
    "description", "plant", "supplier_code", "manufacturer", "manufacturer_part_no",
    "open_quantity_gr", "net_order_price", "currency", "net_order_value",
    "position_text1", "position_text2", "statical_delivery_date",
}

_SOURCE_COLUMNS = {"current_eta_source", "supplier_remarks_source"}


def _get_priority(source: str) -> int:
    return SOURCE_PRIORITY.get(source, 0)


def try_update_field(
    conn: sqlite3.Connection,
    material_id: int,
    field_name: str,
    new_value: Any,
    source: str,
    source_ref: str | None = None,
    operator: str | None = None,
    current_source: str | None = None,
) -> tuple[bool, str]:
    src_col = field_name + "_source" if (field_name + "_source") in _SOURCE_COLUMNS else None
    select_expr = f"{field_name}, {src_col}" if src_col else f"{field_name}, NULL"
    cur = conn.execute(
        f"SELECT {select_expr} FROM materials WHERE id = ?",
        (material_id,),
    )
    row = cur.fetchone()
    if row is None:
        return False, f"material_id={material_id} not found"

    old_value = row[0]
    old_source = current_source or row[1]

    if field_name in SENSITIVE_FIELDS and field_name not in FACT_FIELDS:
        if old_source and _get_priority(source) < _get_priority(old_source):
            return False, (
                f"field {field_name} blocked: existing source={old_source} "
                f"(priority={_get_priority(old_source)}) > new source={source} "
                f"(priority={_get_priority(source)})"
            )

    new_str = json.dumps(new_value, ensure_ascii=False) if not isinstance(new_value, str) else new_value
    old_str = json.dumps(old_value, ensure_ascii=False) if not isinstance(old_value, str) else (old_value or "")

    conn.execute(
        f"UPDATE materials SET {field_name} = ? WHERE id = ?",
        (new_value, material_id),
    )

    if src_col:
        conn.execute(
            f"UPDATE materials SET {src_col} = ? WHERE id = ?",
            (source, material_id),
        )

    conn.execute(
        """INSERT INTO field_updates
           (material_id, field_name, old_value, new_value, source, source_ref, operator, confirmed)
           VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
        (material_id, field_name, old_str, new_str, source, source_ref, operator),
    )

    return True, "ok"


def bulk_update_fields(
    conn: sqlite3.Connection,
    material_id: int,
    updates: dict[str, Any],
    source: str,
    source_ref: str | None = None,
    operator: str | None = None,
) -> dict[str, tuple[bool, str]]:
    results: dict[str, tuple[bool, str]] = {}
    for field, value in updates.items():
        ok, reason = try_update_field(
            conn, material_id, field, value, source, source_ref, operator
        )
        results[field] = (ok, reason)
    return results
