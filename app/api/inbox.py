"""API: Inbox pull + approval with project ID"""
from __future__ import annotations
from datetime import datetime
import shutil, tempfile, os

from fastapi import APIRouter, Query, HTTPException, Path as FPath, UploadFile, File
from pydantic import BaseModel

from app.db.connection import get_connection
from app.services.outlook_inbox import pull_inbox
from app.tools.parse_inbound import parse_inbound_email, apply_inbound_decision

router = APIRouter(prefix="/api/projects/{project_id}/inbox", tags=["inbox"])


@router.post("/pull")
def pull(
    project_id: str = FPath(...),
    days: int = Query(None),
    deep: bool = Query(False),
):
    if deep:
        effective_days = 90
    elif days is not None:
        effective_days = days
    else:
        conn = get_connection(project_id)
        try:
            row = conn.execute("SELECT MAX(sent_at) as t FROM chase_log").fetchone()
            last_sent = row["t"] if row else None
        finally:
            conn.close()
        if last_sent:
            try:
                dt = datetime.fromisoformat(last_sent)
                effective_days = max(1, (datetime.utcnow() - dt).days + 1)
            except Exception:
                effective_days = 14
        else:
            effective_days = 14

    try:
        result = pull_inbox(effective_days, project_id=project_id)
        result["pulled_days"] = effective_days
        return result
    except RuntimeError as e:
        raise HTTPException(503, str(e))


@router.post("/upload_msg")
async def upload_msg(
    project_id: str = FPath(...),
    file: UploadFile = File(...),
):
    if not (file.filename or "").lower().endswith(".msg"):
        raise HTTPException(400, "Only .msg files are supported")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".msg") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        try:
            import extract_msg
            msg = extract_msg.Message(tmp_path)
            subject = msg.subject or ""
            body = msg.body or ""
            sender = msg.sender or ""
            msg.close()
        except Exception as e:
            raise HTTPException(400, f"Cannot parse .msg file: {e}")

        from app.services.email_marker import parse_marker
        marker = parse_marker(subject)
        marker_str = marker.to_subject_tag() if marker else None

        mat_id = None
        conn = get_connection(project_id)
        try:
            if marker:
                row = conn.execute(
                    "SELECT id FROM materials WHERE po_number=? AND item_no=?",
                    (marker.po_number, marker.item_nos[0] if marker.item_nos else ""),
                ).fetchone()
                if row:
                    mat_id = row[0]

            cur = conn.execute(
                "INSERT INTO inbound_emails "
                "(outlook_entry_id, from_address, subject, body, received_at, "
                " parsed_marker, matched_material_id, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'new')",
                (
                    f"msg:{file.filename}",
                    sender, subject, body,
                    datetime.utcnow().isoformat(),
                    marker_str, mat_id,
                ),
            )
            email_id = cur.lastrowid
            conn.commit()
        finally:
            conn.close()

        return {"ok": True, "email_id": email_id, "matched_material_id": mat_id}
    finally:
        os.unlink(tmp_path)


@router.get("/list")
def list_emails(
    project_id: str = FPath(...),
    status: str = Query(None),
    limit: int = Query(50),
    offset: int = Query(0),
):
    conn = get_connection(project_id)
    try:
        conditions, params = [], []
        if status:
            conditions.append("status=?")
            params.append(status)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = conn.execute(
            f"SELECT * FROM inbound_emails {where} "
            f"ORDER BY received_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        total = conn.execute(f"SELECT COUNT(*) FROM inbound_emails {where}", params).fetchone()[0]
        return {"items": [dict(r) for r in rows], "total": total}
    finally:
        conn.close()


@router.post("/{email_id}/parse")
def parse_email(email_id: int, project_id: str = FPath(...)):
    return parse_inbound_email(email_id, project_id=project_id)


class DecisionBody(BaseModel):
    decision: str
    edits: dict | None = None


@router.post("/{email_id}/decide")
def decide(email_id: int, body: DecisionBody, project_id: str = FPath(...)):
    return apply_inbound_decision(email_id, body.decision, body.edits, project_id=project_id)
