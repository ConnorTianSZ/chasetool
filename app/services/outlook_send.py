"""Outlook email send service (pywin32)"""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from typing import Literal
from app.db.connection import get_connection
from app.services.email_marker import ChaseMarker, LegacyChaseMarker
from app.config import get_settings

_outlook_app = None


def _get_outlook():
    global _outlook_app
    if _outlook_app is None:
        try:
            import win32com.client
            _outlook_app = win32com.client.Dispatch("Outlook.Application")
        except Exception as e:
            raise RuntimeError(f"Cannot connect to Outlook: {e}") from e
    return _outlook_app


def send_chase_email(
    to_address:   str,
    cc:           str,
    subject:      str,
    body:         str,
    material_ids: list[int],
    marker,
    mode         = None,
    project_id:   str = "default",
    is_html:      bool = False,
) -> dict:
    settings = get_settings()
    mode = mode or settings.chase_default_mode

    outlook = _get_outlook()
    mail = outlook.CreateItem(0)
    mail.To = to_address
    if cc:
        mail.CC = cc
    mail.Subject = subject
    if is_html:
        mail.HTMLBody = body
    else:
        mail.Body = body

    entry_id = None
    if mode == "send":
        mail.Send()
        method = "direct_send"
    else:
        mail.Save()
        entry_id = mail.EntryID
        method = "draft"

    marker_tag = None
    if marker is not None:
        marker_tag = marker.to_subject_tag()

    conn = get_connection(project_id)
    try:
        cur = conn.execute(
            "INSERT INTO chase_log "
            "(material_ids_json, to_address, cc, subject, body, method, "
            " outlook_entry_id, marker_tag, sent_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                json.dumps(material_ids),
                to_address, cc, subject, body, method,
                entry_id,
                marker_tag,
                datetime.utcnow().isoformat(),
            ),
        )
        log_id = cur.lastrowid
        now_iso = datetime.utcnow().isoformat()
        for mid in material_ids:
            conn.execute(
                "UPDATE materials SET last_chased_at=?, chase_count=chase_count+1 WHERE id=?",
                (now_iso, mid),
            )
        conn.commit()
    finally:
        conn.close()

    return {"ok": True, "method": method, "chase_log_id": log_id, "marker_tag": marker_tag}


def build_chase_subject(marker, chase_type: str = "") -> str:
    tag = marker.to_subject_tag()
    if isinstance(marker, ChaseMarker):
        if marker.is_oc:
            return f"{tag} OC Confirmation / 请确认订单交期"
        elif chase_type == "urgent_keydate":
            return f"{tag} Delivery Expedite / 请确认能否提前交货"
        else:
            return f"{tag} Urgent Delivery / 请加急确认最新交期"
    return f"{tag} Delivery Chase / 请确认交货期"


def build_email_body(template_type: str, materials: list, key_date: str = "") -> str:
    template = load_template(template_type)
    # urgent_now / urgent_keydate 都展示当前 OC 日期；oc_confirmation 展示原始 ETA
    eta_field = "current_eta" if template_type.startswith("urgent") else "original_eta"
    rows = []
    for m in materials:
        eta = m.get(eta_field, "") or ""
        rows.append(
            "<tr>"
            f"<td>{m.get('po_number', '')}</td>"
            f"<td>{m.get('item_no', '')}</td>"
            f"<td>{m.get('wbs_element', '')}</td>"
            f"<td>{m.get('part_no', '')}</td>"
            f"<td>{m.get('description', '')}</td>"
            f"<td>{m.get('quantity', '')}</td>"
            f"<td>{m.get('unit', '')}</td>"
            f"<td>{m.get('supplier', '')}</td>"
            f"<td>{eta}</td>"
            "</tr>"
        )
    material_rows = "\n".join(rows)
    first = materials[0]
    body = template.replace("{material_rows}", material_rows)
    body = body.replace("{buyer_name}", first.get("buyer_name", ""))
    body = body.replace("{buyer_email}", first.get("buyer_email", ""))
    body = body.replace("{project_no}", first.get("project_no", ""))
    body = body.replace("{key_date}", key_date if key_date else "")
    return body


def load_template(template_type: str = "oc_confirmation") -> str:
    tpl_dir = Path(__file__).parent.parent.parent / "config" / "chase_email_templates"
    tpl_path = tpl_dir / f"{template_type}.txt"
    if tpl_path.exists():
        return tpl_path.read_text(encoding="utf-8")
    default = tpl_dir / "default.txt"
    if default.exists():
        return default.read_text(encoding="utf-8")
    return (
        "Dear Supplier,\n\n"
        "Please confirm the delivery date for the following items.\n\n"
        "{material_rows}\n\n"
        "Best regards"
    )
