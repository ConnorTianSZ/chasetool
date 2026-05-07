"""LLM Tool: 更新物料字段 / 打重点标"""
from __future__ import annotations
from app.db.connection import get_connection
from app.update_policy import try_update_field


def update_material_field(
    po_number: str,
    item_no: str,
    field: str,
    value,
    source: str = "chat_command",
    operator: str | None = None,
) -> dict:
    """更新单字段（走优先级治理）"""
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT id FROM materials WHERE po_number=? AND item_no=?",
            (po_number, item_no),
        )
        row = cur.fetchone()
        if not row:
            return {"ok": False, "reason": "物料不存在"}
        ok, reason = try_update_field(
            conn, row[0], field, value, source, operator=operator
        )
        conn.commit()
        return {"ok": ok, "reason": reason}
    finally:
        conn.close()


def mark_focus(material_ids: list[int], reason: str = "") -> dict:
    """批量打重点标"""
    conn = get_connection()
    try:
        for mid in material_ids:
            conn.execute(
                "UPDATE materials SET is_focus=1, focus_reason=? WHERE id=?",
                (reason, mid),
            )
        conn.commit()
        return {"ok": True, "updated": len(material_ids)}
    finally:
        conn.close()
