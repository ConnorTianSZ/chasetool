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


def _resolve_chase_log_id(conn, marker_tag):
    if not marker_tag:
        return None
    row = conn.execute(
        "SELECT id FROM chase_log WHERE marker_tag=? ORDER BY sent_at DESC LIMIT 1",
        (marker_tag,),
    ).fetchone()
    if row:
        return row[0]
    row = conn.execute(
        "SELECT id FROM chase_log WHERE subject LIKE ? ORDER BY sent_at DESC LIMIT 1",
        (f"%{marker_tag}%",),
    ).fetchone()
    return row[0] if row else None


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
    except Exception as e:
        raise HTTPException(503, f"Inbox pull failed: {e}")


@router.post("/parse_all")
def parse_all(
    project_id: str = FPath(...),
    limit: int = Query(20, ge=1, le=100),
):
    """批量解析所有 status='new' 的邮件，每封调一次 LLM。limit 防止费用失控。"""
    conn = get_connection(project_id)
    try:
        rows = conn.execute(
            "SELECT id FROM inbound_emails WHERE status='new' "
            "ORDER BY received_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        email_ids = [r["id"] for r in rows]
    finally:
        conn.close()

    parsed, failed, details = 0, 0, []
    for eid in email_ids:
        try:
            r = parse_inbound_email(eid, project_id=project_id)
            if r.get("ok"):
                parsed += 1
                details.append({"email_id": eid, "ok": True,
                                 "items": len(r.get("extracted", {}).get("items") or [])})
            else:
                failed += 1
                details.append({"email_id": eid, "ok": False, "reason": r.get("reason")})
        except Exception as e:
            failed += 1
            details.append({"email_id": eid, "ok": False, "reason": str(e)})

    return {"ok": True, "parsed": parsed, "failed": failed,
            "total": len(email_ids), "details": details}


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
            body    = msg.body or ""
            sender  = msg.sender or ""
            msg.close()
        except Exception as e:
            raise HTTPException(400, f"Cannot parse .msg file: {e}")

        from app.services.email_marker import parse_marker, marker_tag_from_subject, LegacyChaseMarker
        marker     = parse_marker(subject)
        marker_tag = marker_tag_from_subject(subject)

        conn = get_connection(project_id)
        try:
            chase_log_id = _resolve_chase_log_id(conn, marker_tag)

            mat_id = None
            if isinstance(marker, LegacyChaseMarker) and not chase_log_id:
                row = conn.execute(
                    "SELECT id FROM materials WHERE po_number=? AND item_no=?",
                    (marker.po_number, marker.item_nos[0] if marker.item_nos else ""),
                ).fetchone()
                if row:
                    mat_id = row[0]

            cur = conn.execute(
                "INSERT INTO inbound_emails "
                "(outlook_entry_id, from_address, subject, body, received_at, "
                " parsed_marker, matched_material_id, chase_log_id, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new')",
                (
                    f"msg:{file.filename}",
                    sender, subject, body,
                    datetime.utcnow().isoformat(),
                    marker_tag, mat_id, chase_log_id,
                ),
            )
            email_id = cur.lastrowid
            conn.commit()
        finally:
            conn.close()

        return {
            "ok":                  True,
            "email_id":            email_id,
            "chase_log_id":        chase_log_id,
            "matched_material_id": mat_id,
        }
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
        # 构建 ie. 前缀的 WHERE（用于 JOIN 查询）
        ie_where = ("WHERE " + " AND ".join(f"ie.{c}" for c in conditions)) if conditions else ""
        rows = conn.execute(
            f"""SELECT ie.*,
                       m.buyer_display, m.buyer_email AS mat_buyer_email
                FROM inbound_emails ie
                LEFT JOIN materials m ON ie.matched_material_id = m.id
                {ie_where}
                ORDER BY ie.received_at DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()
        total = conn.execute(
            f"SELECT COUNT(*) FROM inbound_emails ie {ie_where}", params
        ).fetchone()[0]
        return {"items": [dict(r) for r in rows], "total": total}
    finally:
        conn.close()


@router.post("/{email_id}/parse")
def parse_email(email_id: int, project_id: str = FPath(...)):
    return parse_inbound_email(email_id, project_id=project_id)


class DecisionBody(BaseModel):
    decision: str
    edits: dict | None = None
    finalize: bool = False


@router.post("/{email_id}/decide")
def decide(email_id: int, body: DecisionBody, project_id: str = FPath(...)):
    return apply_inbound_decision(
        email_id, body.decision, body.edits,
        project_id=project_id, finalize=body.finalize,
    )


# ── 不接受：发加急回复邮件 ──────────────────────────────────────────

class RejectBody(BaseModel):
    target_eta: str                   # "05/20" 或 "05/15~05/20"
    mode: str = "draft"               # "draft" | "send"
    selected_items: list[dict] = []   # [{po_number, item_no, current_eta}]


def _build_reject_body(selected_items: list[dict], target_eta: str,
                        original_subject: str) -> str:
    lines = [
        "Dear Supplier,",
        "",
        "Thank you for your feedback.",
        "",
        ("We regret to inform you that the delivery date(s) provided are not acceptable "
         "for our project schedule. We kindly request you to bring the delivery forward "
         f"to: {target_eta}"),
        "",
        "Details of affected items:",
        "",
        f"{'PO No.':<20} {'Item':<8} {'Current Reply ETA':<20}",
        "-" * 50,
    ]
    for it in selected_items:
        po  = str(it.get("po_number") or "").strip()
        itm = str(it.get("item_no") or "").strip()
        eta = str(it.get("current_eta") or it.get("new_eta") or "—").strip()
        lines.append(f"{po:<20} {itm:<8} {eta:<20}")

    lines += [
        "",
        "Please confirm the revised delivery date at your earliest convenience.",
        "Should there be any difficulties, please inform us immediately.",
        "",
        "Best regards,",
    ]
    return "\n".join(lines)


@router.post("/{email_id}/reject")
def reject_email(email_id: int, body: RejectBody, project_id: str = FPath(...)):
    """对供应商回复发加急回复邮件（交期不接受）。

    通过 win32com ReplyAll 回复原邮件，保留 To/CC/Subject/引用链。
    .msg 上传邮件无法通过 COM 检索，返回 msg_upload_no_reply 错误码。
    """
    conn = get_connection(project_id)
    try:
        row = conn.execute(
            "SELECT outlook_entry_id, subject, from_address FROM inbound_emails WHERE id=?",
            (email_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "email not found")

        entry_id = row["outlook_entry_id"] or ""
        original_subject = row["subject"] or ""

        # .msg 上传邮件无 Outlook EntryID，无法 ReplyAll
        if entry_id.startswith("msg:"):
            return {
                "ok": False,
                "error_code": "msg_upload_no_reply",
                "message": "此邮件为手动上传的 .msg 文件，无法通过系统直接回复，请从 Outlook 手动回复。",
            }

        reply_body = _build_reject_body(body.selected_items, body.target_eta, original_subject)

        try:
            import win32com.client
            outlook   = win32com.client.Dispatch("Outlook.Application")
            namespace = outlook.GetNamespace("MAPI")
            original  = namespace.GetItemFromID(entry_id)
            reply     = original.ReplyAll()
            reply.Body = reply_body
            if body.mode == "send":
                reply.Send()
            else:
                reply.Save()
        except Exception as e:
            raise HTTPException(503, f"Outlook 操作失败: {e}")

        now_iso = datetime.utcnow().isoformat()
        conn.execute(
            "UPDATE inbound_emails SET status='rejected', operator_decision='reject',"
            " reject_target_eta=?, reject_sent_at=?, reject_mode=? WHERE id=?",
            (body.target_eta, now_iso, body.mode, email_id),
        )
        conn.commit()
        return {
            "ok":   True,
            "mode": body.mode,
            "message": "草稿已保存到 Outlook" if body.mode == "draft" else "加急回复已发送",
        }
    finally:
        conn.close()
