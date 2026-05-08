"""
API: 催货邮件 — 带项目 ID
"""
from __future__ import annotations
import traceback
from typing import Literal
from fastapi import APIRouter, Path as FPath
from pydantic import BaseModel
from app.tools.chase_email import build_drafts
from app.services.email_marker import parse_marker
from app.services.outlook_send import send_chase_email

router = APIRouter(prefix="/api/projects/{project_id}/chase", tags=["chase"])


class ChaseRequest(BaseModel):
    material_ids: list[int]
    chase_type: str | None = None
    mode: Literal["draft", "send"] = "draft"


@router.post("/generate")
def generate_drafts(req: ChaseRequest, project_id: str = FPath(...)):
    try:
        return build_drafts(
            material_ids=req.material_ids,
            project_id=project_id,
            chase_type_override=req.chase_type,
        )
    except Exception as e:
        import logging
        logging.getLogger("chasebase").exception(
            "生成催货草稿失败 project=%s ids=%s",
            project_id, req.material_ids,
        )
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=500,
            content={"detail": f"生成催货草稿失败: {e}"},
        )


@router.post("/send")
def send_drafts(req: ChaseRequest, project_id: str = FPath(...)):
    try:
        result = build_drafts(
            material_ids=req.material_ids,
            project_id=project_id,
            chase_type_override=req.chase_type,
        )
        drafts  = result["drafts"]
        skipped = result["skipped"]

        send_results = []
        for d in drafts:
            marker  = parse_marker(d.get("marker_tag", "") or d.get("subject", ""))
            is_html = (
                d["body"].strip().startswith("<html")
                or d["body"].strip().startswith("<!DOCTYPE")
            )
            r = send_chase_email(
                to_address=d["to_address"],
                cc="",
                subject=d["subject"],
                body=d["body"],
                material_ids=d["material_ids"],
                marker=marker,
                mode=req.mode,
                project_id=project_id,
                is_html=is_html,
            )
            send_results.append(r)

        return {"ok": True, "drafts_result": send_results, "skipped": skipped}
    except Exception as e:
        import logging
        logging.getLogger("chasebase").exception(
            "发送催货邮件失败 project=%s ids=%s",
            project_id, req.material_ids,
        )
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=500,
            content={"detail": f"发送催货邮件失败: {e}"},
        )


@router.get("/log")
def chase_log(project_id: str = FPath(...), limit: int = 50):
    from app.db.connection import get_connection
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
    from app.db.connection import get_connection
    conn = get_connection(project_id)
    try:
        row = conn.execute("SELECT MAX(sent_at) as t FROM chase_log").fetchone()
        return {"last_sent_at": row["t"] if row else None}
    finally:
        conn.close()
