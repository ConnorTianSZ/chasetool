"""Outlook inbox pull service"""
from __future__ import annotations
from datetime import datetime, timedelta
from app.db.connection import get_connection
from app.services.email_marker import parse_marker

_outlook_app = None


def _get_outlook():
    global _outlook_app
    if _outlook_app is None:
        try:
            import win32com.client
            _outlook_app = win32com.client.Dispatch("Outlook.Application")
        except Exception as e:
            raise RuntimeError("Cannot connect to Outlook: " + str(e)) from e
    return _outlook_app


def pull_inbox(days: int = 14, project_id: str = "default") -> dict:
    outlook   = _get_outlook()
    namespace = outlook.GetNamespace("MAPI")
    inbox     = namespace.GetDefaultFolder(6)
    messages  = inbox.Items
    messages.Sort("[ReceivedTime]", True)

    since = datetime.now() - timedelta(days=days)
    conn  = get_connection(project_id)
    pulled, skipped = 0, 0

    try:
        for msg in messages:
            try:
                received = msg.ReceivedTime
                recv_dt = received if isinstance(received, datetime) \
                    else datetime.fromtimestamp(float(received))
                if recv_dt < since:
                    break

                entry_id = msg.EntryID
                if conn.execute(
                    "SELECT id FROM inbound_emails WHERE outlook_entry_id=?",
                    (entry_id,)
                ).fetchone():
                    skipped += 1
                    continue

                subject  = str(msg.Subject or "")
                body     = str(msg.Body or "")
                sender   = str(msg.SenderEmailAddress or "")
                marker   = parse_marker(subject)
                marker_str = marker.to_subject_tag() if marker else None

                mat_id = None
                if marker:
                    row = conn.execute(
                        "SELECT id FROM materials WHERE po_number=? AND item_no=?",
                        (marker.po_number,
                         marker.item_nos[0] if marker.item_nos else ""),
                    ).fetchone()
                    if row:
                        mat_id = row[0]

                conn.execute(
                    "INSERT INTO inbound_emails "
                    "(outlook_entry_id, from_address, subject, body, received_at, "
                    " parsed_marker, matched_material_id, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'new')",
                    (entry_id, sender, subject, body,
                     recv_dt.isoformat(), marker_str, mat_id),
                )
                pulled += 1
            except Exception:
                skipped += 1

        conn.commit()
    finally:
        conn.close()

    return {"pulled": pulled, "skipped": skipped}
