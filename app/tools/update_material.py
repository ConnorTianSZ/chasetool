"""LLM Tool: 更新物料字段 / 打重点标"""
from __future__ import annotations
from app.db.connection import get_connection
from app.update_policy import try_update_field


ALLOWED_CHAT_UPDATE_FIELDS = {
    "supplier_eta",
    "supplier_remarks",
    "status",
    "is_focus",
}


def update_material_field(
    po_number: str,
    item_no: str,
    field: str,
    value,
    source: str = "chat_command",
    operator: str | None = None,
    project_id: str = "default",
) -> dict:
    """更新单字段（走优先级治理）"""
    if field == "current_eta":
        return {
            "ok": False,
            "reason": "current_eta 来自 SAP Excel 导入，请改用 supplier_eta 记录供应商反馈交期",
        }
    if field not in ALLOWED_CHAT_UPDATE_FIELDS:
        allowed = ", ".join(sorted(ALLOWED_CHAT_UPDATE_FIELDS))
        return {"ok": False, "reason": f"字段 {field} 不允许通过 Chat 更新；允许字段: {allowed}"}

    conn = get_connection(project_id)
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


def mark_focus(
    material_ids: list[int] | None = None,
    po_number: str | None = None,
    item_no: str | None = None,
    focus: bool = True,
    reason: str = "",
    project_id: str = "default",
) -> dict:
    """标记/取消重点

    支持两种方式指定物料：
    - material_ids: 数据库 ID 列表（批量，registry 调用兼容）
    - po_number + item_no: 按采购单和行号指定（chat 调用用）
    focus=True 打标，focus=False 取消。
    """
    conn = get_connection(project_id)
    try:
        targets: list[int] = []
        if material_ids is not None:
            targets = list(material_ids)
        elif po_number is not None and item_no is not None:
            cur = conn.execute(
                "SELECT id FROM materials WHERE po_number=? AND item_no=?",
                (po_number, item_no),
            )
            row = cur.fetchone()
            if not row:
                return {"ok": False, "reason": "物料不存在"}
            targets = [row[0]]
        else:
            return {"ok": False, "reason": "请提供 material_ids 或 po_number+item_no"}

        fv = 1 if focus else 0
        fr = reason if focus else ""
        for mid in targets:
            conn.execute(
                "UPDATE materials SET is_focus=?, focus_reason=? WHERE id=?",
                (fv, fr, mid),
            )
        conn.commit()
        return {"ok": True, "updated": len(targets)}
    finally:
        conn.close()
