"""LLM Tool: parse inbound email and apply ETA updates"""
from __future__ import annotations
from datetime import datetime
import json
from app.db.connection import get_connection
from app.services.llm_client import parse_email_for_eta
from app.services.material_view import derive_material_state
from app.update_policy import bulk_update_fields


def _enrich_items(conn, items: list) -> list:
    """
    For each extracted item, query the materials table and attach
    matched: {material_id, part_no, supplier, current_eta, state_label}
    or matched: null if no row is found.
    """
    enriched = []
    for item in items:
        po_number = (item.get("po_number") or "").strip()
        item_no   = (item.get("item_no")   or "").strip()

        mat_row = None

        if po_number and item_no:
            # 1. Exact match
            mat_row = conn.execute(
                "SELECT id, part_no, supplier, current_eta, open_quantity_gr "
                "FROM materials WHERE po_number=? AND item_no=?",
                (po_number, item_no),
            ).fetchone()

            if not mat_row:
                # 2. Fuzzy: leading-zero difference (cast both sides to INTEGER)
                mat_row = conn.execute(
                    "SELECT id, part_no, supplier, current_eta, open_quantity_gr "
                    "FROM materials "
                    "WHERE CAST(po_number AS INTEGER)=CAST(? AS INTEGER) AND item_no=?",
                    (po_number, item_no),
                ).fetchone()

        elif po_number and not item_no:
            # PO only — take first match
            mat_row = conn.execute(
                "SELECT id, part_no, supplier, current_eta, open_quantity_gr "
                "FROM materials WHERE po_number=? LIMIT 1",
                (po_number,),
            ).fetchone()

            if not mat_row:
                mat_row = conn.execute(
                    "SELECT id, part_no, supplier, current_eta, open_quantity_gr "
                    "FROM materials "
                    "WHERE CAST(po_number AS INTEGER)=CAST(? AS INTEGER) LIMIT 1",
                    (po_number,),
                ).fetchone()

        if mat_row:
            state = derive_material_state(dict(mat_row))
            matched = {
                "material_id":  mat_row["id"],
                "part_no":      mat_row["part_no"],
                "supplier":     mat_row["supplier"],
                "current_eta":  mat_row["current_eta"],
                "state_label":  state["label"],
                "state_code":   state["code"],
            }
        else:
            matched = None

        enriched.append({**item, "matched": matched})

    return enriched


def parse_inbound_email(email_id: int, project_id: str = "default") -> dict:
    """
    Call LLM to parse a supplier reply email, extracting per-item ETA data.
    Each item is then enriched with matched material info from the DB.
    Result stored in llm_extracted_json; status set to pending_confirm.
    """
    conn = get_connection(project_id)
    try:
        row = conn.execute("SELECT * FROM inbound_emails WHERE id=?", (email_id,)).fetchone()
        if not row:
            return {"ok": False, "reason": "email not found"}

        extracted = parse_email_for_eta(row["subject"] or "", row["body"] or "")

        # Enrich each item with matched material info
        if extracted.get("items"):
            extracted["items"] = _enrich_items(conn, extracted["items"])

        conn.execute(
            "UPDATE inbound_emails SET llm_extracted_json=?, status='pending_confirm' WHERE id=?",
            (json.dumps(extracted, ensure_ascii=False), email_id),
        )
        conn.commit()
        return {"ok": True, "email_id": email_id, "extracted": extracted}
    finally:
        conn.close()


def _apply_single_item(conn, item: dict, source_ref, now_iso: str) -> dict:
    """
    Try to match one extracted item to a material row and apply ETA/remarks.
    Returns a result dict with status "applied" or "unmatched".
    """
    po_number = (item.get("po_number") or "").strip()
    item_no   = (item.get("item_no")   or "").strip()
    new_eta   = item.get("new_eta")
    remarks   = item.get("remarks", "")

    if not po_number and not item_no:
        return {
            "status": "unmatched", "material_id": None,
            "po_number": None, "item_no": None,
            "new_eta": new_eta, "remarks": remarks,
            "reason": "po_number and item_no both empty; cannot auto-match",
        }

    # Use pre-matched material_id from enrichment if available
    matched = item.get("matched")
    mat_id = None
    chase_count = 0

    if matched and matched.get("material_id"):
        mat_id = matched["material_id"]
        mat_row = conn.execute(
            "SELECT id, chase_count FROM materials WHERE id=?", (mat_id,)
        ).fetchone()
        if mat_row:
            chase_count = int(mat_row["chase_count"] or 0)
        else:
            mat_id = None  # stale reference, fall through to DB lookup

    if not mat_id:
        # Exact match
        mat_row = conn.execute(
            "SELECT id, chase_count FROM materials WHERE po_number=? AND item_no=?",
            (po_number, item_no),
        ).fetchone()

        # Fallback: leading-zero difference
        if not mat_row and po_number:
            mat_row = conn.execute(
                "SELECT id, chase_count FROM materials "
                "WHERE CAST(po_number AS INTEGER)=CAST(? AS INTEGER) AND item_no=?",
                (po_number, item_no),
            ).fetchone()

        # Fallback: po only (when item_no not identified)
        if not mat_row and po_number and not item_no:
            mat_row = conn.execute(
                "SELECT id, chase_count FROM materials WHERE po_number=? LIMIT 1",
                (po_number,),
            ).fetchone()

        if not mat_row:
            return {
                "status": "unmatched", "material_id": None,
                "po_number": po_number or None, "item_no": item_no or None,
                "new_eta": new_eta, "remarks": remarks,
                "reason": f"No material found for PO={po_number} item={item_no}",
            }

        mat_id = mat_row["id"]
        chase_count = int(mat_row["chase_count"] or 0)

    updates = {
        "supplier_feedback_time":    now_iso,
        "last_feedback_chase_count": chase_count,
    }
    if new_eta:
        updates["supplier_eta"] = new_eta
        updates["current_eta"]  = new_eta
    if remarks:
        updates["supplier_remarks"] = remarks

    bulk_update_fields(conn, mat_id, updates, source="email_reply", source_ref=source_ref)

    return {
        "status": "applied", "material_id": mat_id,
        "po_number": po_number, "item_no": item_no,
        "new_eta": new_eta, "remarks": remarks,
    }


def apply_inbound_decision(
    email_id:   int,
    decision:   str,
    edits:      dict | None = None,
    project_id: str = "default",
) -> dict:
    """
    Apply human decision to a parsed inbound email.

    decision:
      "apply"  - match each extracted item to a material and update ETA/remarks
      "ignore" - mark as ignored
      "manual" - mark for manual handling

    edits (optional): override extracted content; supports {"items": [...]} to
      replace the items list with human-corrected/selected values.

    Returns: {ok, email_id, decision, applied: [...], unmatched: [...]}
    """
    conn = get_connection(project_id)
    try:
        row = conn.execute("SELECT * FROM inbound_emails WHERE id=?", (email_id,)).fetchone()
        if not row:
            return {"ok": False, "reason": "email not found"}

        if decision == "apply":
            extracted = json.loads(row["llm_extracted_json"] or "{}")
            if edits:
                if "items" in edits:
                    extracted["items"] = edits["items"]
                else:
                    extracted.update(edits)

            items = extracted.get("items") or []
            if not items:
                return {"ok": False, "reason": "No items in llm_extracted_json; parse the email first"}

            now_iso    = datetime.utcnow().isoformat(timespec="seconds")
            source_ref = row["outlook_entry_id"]
            applied, unmatched = [], []

            for item in items:
                result = _apply_single_item(conn, item, source_ref, now_iso)
                if result["status"] == "applied":
                    applied.append(result)
                else:
                    unmatched.append(result)

            new_status = "applied" if applied else "manual"
            conn.execute(
                "UPDATE inbound_emails SET status=?, operator_decision=? WHERE id=?",
                (new_status, decision, email_id),
            )
            conn.commit()
            return {
                "ok": True, "email_id": email_id, "decision": decision,
                "applied": applied, "unmatched": unmatched,
            }

        elif decision == "ignore":
            conn.execute(
                "UPDATE inbound_emails SET status='ignored', operator_decision=? WHERE id=?",
                (decision, email_id),
            )
        elif decision == "manual":
            conn.execute(
                "UPDATE inbound_emails SET status='manual', operator_decision=? WHERE id=?",
                (decision, email_id),
            )
        else:
            return {"ok": False, "reason": f"unknown decision: {decision}"}

        conn.commit()
        return {"ok": True, "email_id": email_id, "decision": decision}
    finally:
        conn.close()
