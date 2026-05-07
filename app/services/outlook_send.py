"""Outlook email send service (pywin32)"""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from typing import Literal
from app.db.connection import get_connection
from app.services.email_marker import ChaseMarker
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
    marker:       ChaseMarker,
    mode:         Literal["draft", "send"] | None = None,
    project_id:   str = "default",
) -> dict:
    settings = get_settings()
    mode = mode or settings.chase_default_mode

    outlook = _get_outlook()
    mail = outlook.CreateItem(0)
    mail.To = to_address
    if cc:
        mail.CC = cc
    mail.Subject = subject
    mail.Body = body

    entry_id = None
    if mode == "send":
        mail.Send()
        method = "direct_send"
    else:
        mail.Save()
        entry_id = mail.EntryID
        method = "draft"

    conn = get_connection(project_id)
    try:
        cur = conn.execute(
            "INSERT INTO chase_log "
            "(material_ids_json, to_address, cc, subject, body, method, "
            " outlook_entry_id, sent_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                json.dumps(material_ids),
                to_address, cc, subject, body, method,
                entry_id,
                datetime.utcnow().isoformat(),
            ),
        )
        log_id = cur.lastrowid
        for mid in material_ids:
            conn.execute(
                "UPDATE materials SET last_chased_at=?, chase_count=chase_count+1 WHERE id=?",
                (datetime.utcnow().isoformat(), mid),
            )
        conn.commit()
    finally:
        conn.close()

    return {"ok": True, "method": method, "chase_log_id": log_id}


def build_chase_subject(marker: "ChaseMarker") -> str:
    """Build a chase email subject line from a ChaseMarker."""
    tag = marker.to_subject_tag()
    return f"{tag} Delivery Chase / 请确认交货期"


def load_template() -> str:
    """Load the chase email template from config, or return a default."""
    from pathlib import Path
    tpl_path = Path(__file__).parent.parent.parent / "config" / "chase_template.txt"
    if tpl_path.exists():
        return tpl_path.read_text(encoding="utf-8")
    return (
        "Dear Supplier,\n\n"
        "Please confirm the delivery date for the following items.\n\n"
        "{items_table}\n\n"
        "Best regards"
    )
