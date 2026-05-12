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

        # 用邮件接收日期作为相对日期参考，而非今天的日期
        email_ref_date = None
        if row.get("received_at"):
            try:
                email_ref_date = datetime.fromisoformat(row["received_at"]).date()
            except Exception:
                pass

        extracted = parse_email_for_eta(row["subject"] or "", row["body"] or "",
                                        email_date=email_ref_date)

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
    finalize:   bool = False,
) -> dict:
    """
    Apply human decision to a parsed inbound email.

    decision:
      "apply"  - match selected items to materials and update ETA/remarks.
                 Sets status to "partial" if unprocessed items remain,
                 or "applied" if all items are now done (or finalize=True).
      "ignore" - mark as ignored
      "manual" - mark for manual handling

    finalize (bool):
      True  → force status to "applied" regardless of remaining items.
               Used when operator clicks "完成此邮件".
      False → (default) status becomes "partial" if items still remain open.

    edits (optional): {"items": [...]} — only the selected rows this round,
      with user-edited new_eta / remarks. Unselected rows are skipped but
      preserved in llm_extracted_json for future processing.

    Returns: {ok, email_id, decision, status, applied, unmatched, full_extracted}
    """
    conn = get_connection(project_id)
    try:
        row = conn.execute("SELECT * FROM inbound_emails WHERE id=?", (email_id,)).fetchone()
        if not row:
            return {"ok": False, "reason": "email not found"}

        if decision == "apply":
            extracted = json.loads(row["llm_extracted_json"] or "{}")
            full_items = list(extracted.get("items") or [])

            # ── finalize only: just close the email, no new items to process ──
            if finalize and not edits:
                conn.execute(
                    "UPDATE inbound_emails SET status='applied', operator_decision='apply' WHERE id=?",
                    (email_id,),
                )
                conn.commit()
                return {"ok": True, "email_id": email_id, "decision": "finalize",
                        "status": "applied", "applied": [], "unmatched": []}

            # ── build lookup: which items are selected this round ──
            if edits and "items" in edits:
                selected_list = edits["items"]
            else:
                # No edits → apply all not-yet-applied items
                selected_list = [it for it in full_items if not it.get("_applied")]

            # Two lookup structures:
            # 1. key_map: (po_upper, item_upper) → selected item  (for normal rows with PO/Item)
            # 2. manual_queue: ordered list of manually-added rows with empty PO+Item
            key_map: dict[tuple, dict] = {}
            manual_queue: list[dict] = []
            for it in selected_list:
                po  = str(it.get("po_number") or "").strip().upper()
                ino = str(it.get("item_no")   or "").strip().upper()
                if not po and not ino:
                    # Manually-added row with no identifiers — process by position
                    manual_queue.append(it)
                else:
                    key_map[(po, ino)] = it

            if not key_map and not manual_queue:
                return {"ok": False, "reason": "No items selected; please check at least one row"}

            now_iso    = datetime.utcnow().isoformat(timespec="seconds")
            source_ref = row["outlook_entry_id"]
            applied, unmatched = [], []
            _manual_idx = 0  # pointer into manual_queue

            for i, full_item in enumerate(full_items):
                if full_item.get("_applied"):
                    continue  # Already done in a previous round

                po  = str(full_item.get("po_number") or "").strip().upper()
                ino = str(full_item.get("item_no")   or "").strip().upper()

                if not po and not ino:
                    # Manual row: consume from queue in order
                    if _manual_idx >= len(manual_queue):
                        continue  # No more manual items selected this round
                    sel = manual_queue[_manual_idx]
                    _manual_idx += 1
                else:
                    key = (po, ino)
                    if key not in key_map:
                        continue  # Not selected this round — leave for later
                    sel = key_map[key]

                # Merge user-edited fields (new_eta, remarks) from the selected version
                item_to_process = {
                    **full_item,
                    **{k: v for k, v in sel.items() if k in ("new_eta", "remarks", "matched")},
                }

                result = _apply_single_item(conn, item_to_process, source_ref, now_iso)
                if result["status"] == "applied":
                    full_items[i] = {**full_items[i], "_applied": True}
                    applied.append(result)
                else:
                    # For manual rows with remarks/eta even if unmatched, still mark as applied
                    # so they don't block the email from completing
                    if full_item.get("_manual") and (sel.get("new_eta") or sel.get("remarks")):
                        full_items[i] = {**full_items[i], "_applied": True}
                    unmatched.append(result)

            # ── Process any manual rows from edits that were NOT in full_items ──
            # (manually-added rows only exist in frontend memory until first submit)
            remaining_manual = manual_queue[_manual_idx:]  # unconsumed manual items
            for sel in remaining_manual:
                # Also process keyed items from edits not found in full_items (new manual rows)
                pass  # already handled below via direct-process path

            # Direct-process: edits items flagged _manual that couldn't be matched
            # to any full_item row (e.g. when llm_extracted_json was empty)
            if edits and "items" in edits:
                processed_keys: set = set()
                for fi in full_items:
                    if fi.get("_applied"):
                        k = (str(fi.get("po_number") or "").strip().upper(),
                             str(fi.get("item_no")   or "").strip().upper())
                        processed_keys.add(k)

                for sel in edits["items"]:
                    if not sel.get("_manual"):
                        continue
                    po  = str(sel.get("po_number") or "").strip().upper()
                    ino = str(sel.get("item_no")   or "").strip().upper()
                    if (po, ino) in processed_keys:
                        continue  # already handled above
                    # This manual item was not in full_items at all — process it directly
                    result = _apply_single_item(conn, sel, source_ref, now_iso)
                    new_entry = {
                        "po_number": sel.get("po_number", ""),
                        "item_no":   sel.get("item_no", ""),
                        "new_eta":   sel.get("new_eta", ""),
                        "remarks":   sel.get("remarks", ""),
                        "_manual":   True,
                        "_applied":  result["status"] == "applied" or bool(sel.get("new_eta") or sel.get("remarks")),
                    }
                    full_items.append(new_entry)
                    if result["status"] == "applied":
                        applied.append(result)
                    else:
                        unmatched.append(result)

            # Write back full items list with _applied markers
            extracted["items"] = full_items

            # Determine new email status
            has_remaining = any(not it.get("_applied") for it in full_items)
            if finalize or not has_remaining:
                new_status = "applied"
            else:
                new_status = "partial"

            conn.execute(
                "UPDATE inbound_emails SET llm_extracted_json=?, status=?, operator_decision='apply' WHERE id=?",
                (json.dumps(extracted, ensure_ascii=False), new_status, email_id),
            )
            conn.commit()
            return {
                "ok": True, "email_id": email_id, "decision": decision,
                "status": new_status,
                "applied": applied, "unmatched": unmatched,
                "full_extracted": extracted,
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
