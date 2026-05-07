"""
API: 催货邮件 — 带项目 ID
"""
from __future__ import annotations
from typing import Literal
from fastapi import APIRouter, Path as FPath
from pydantic import BaseModel
from app.db.connection import get_connection
from app.services.email_marker import build_marker
from app.services.outlook_send import send_chase_email, build_chase_subject, load_template
from app.services.llm_client import generate_chase_email

router = APIRouter(prefix="/api/projects/{project_id}/chase", tags=["chase"])


class ChaseRequest(BaseModel):
    material_ids: list[int]
    tone: str = "formal"
    mode: Literal["draft", "send"] = "draft"


def _build_drafts(material_ids: list[int], project_id: str, tone: str = "formal") -> list[dict]:
    conn = get_connection(project_id)
    try:
        placeholders = ",".join("?" * len(material_ids))
        rows = [dict(r) for r in conn.execute(
            f"SELECT * FROM materials WHERE id IN ({placeholders})", material_ids
        ).fetchall()]
    finally:
        conn.close()

    template = load_template()
    drafts = []
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        key = (r.get("supplier", ""), r.get("buyer_email", ""))
        groups.setdefault(key, []).append(r)

    for (supplier, buyer_email), mats in groups.items():
        po       = mats[0]["po_number"]
        item_nos = [m["item_no"] for m in mats]
        marker   = build_marker(po, item_nos)
        subject  = build_chase_subject(marker)
        body     = generate_chase_email(mats, tone=tone, template=template)
        drafts.append({
            "to_address":   buyer_email,
            "subject":      subject,
            "body":         body,
            "material_ids": [m["id"] for m in mats],
            "marker":       marker.to_subject_tag(),
        })
    return drafts


@router.post("/generate")
def generate_drafts(req: ChaseRequest, project_id: str = FPath(...)):
    return {"drafts": _build_drafts(req.material_ids, project_id, tone=req.tone)}


@router.post("/send")
def send_drafts(req: ChaseRequest, project_id: str = FPath(...)):
    from app.services.email_marker import parse_marker
    drafts = _build_drafts(req.material_ids, project_id, tone=req.tone)
    results = []
    for d in drafts:
        marker = parse_marker(d["marker"])
        r = send_chase_email(
            to_address=d["to_address"], cc="",
            subject=d["subject"], body=d["body"],
            material_ids=d["material_ids"], marker=marker,
            mode=req.mode, project_id=project_id,
        )
        results.append(r)
    return {"ok": True, "results": results}


@router.get("/log")
def chase_log(project_id: str = FPath(...), limit: int = 50):
    conn = get_connection(project_id)
    try:
        rows = conn.execute(
            "SELECT * FROM chase_log ORDER BY sent_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/last_sent_at")
def last_sent_at(project_id: str = FPath(...)):
    """返回最近一次催件时间（用于收件箱默认查找范围）"""
    conn = get_connection(project_id)
    try:
        row = conn.execute("SELECT MAX(sent_at) as t FROM chase_log").fetchone()
        return {"last_sent_at": row["t"] if row else None}
    finally:
        conn.close()
