"""LLM Tool: 生成催货草稿 / 发送"""
from __future__ import annotations
from typing import Literal
from app.db.connection import get_connection
from app.services.email_marker import build_marker
from app.services.outlook_send import send_chase_email, build_chase_subject, load_template
from app.services.llm_client import generate_chase_email


def generate_chase_drafts(material_ids: list[int], tone: str = "formal") -> list[dict]:
    """
    按供应商分组，为每组生成催货草稿（不发送）。
    返回 drafts list，每项含 {to_address, subject, body, material_ids, marker}
    """
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(material_ids))
        cur = conn.execute(
            f"SELECT * FROM materials WHERE id IN ({placeholders})",
            material_ids,
        )
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    # 按供应商 + buyer_email 分组
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        key = (r.get("supplier", ""), r.get("buyer_email", ""))
        groups.setdefault(key, []).append(r)

    template = load_template()
    drafts = []
    for (supplier, buyer_email), mats in groups.items():
        # 取第一条的 PO
        po = mats[0]["po_number"]
        item_nos = [m["item_no"] for m in mats]
        marker = build_marker(po, item_nos)
        subject = build_chase_subject(marker)
        body = generate_chase_email(mats, tone=tone, template=template)
        drafts.append({
            "to_address": buyer_email,
            "subject": subject,
            "body": body,
            "material_ids": [m["id"] for m in mats],
            "marker": marker.to_subject_tag(),
        })
    return drafts


def send_chase_drafts(
    drafts: list[dict],
    mode: Literal["draft", "send"] = "draft",
) -> list[dict]:
    """发送或保存草稿，返回每封的结果"""
    results = []
    for d in drafts:
        from app.services.email_marker import parse_marker
        marker = parse_marker(d["marker"])
        result = send_chase_email(
            to_address=d["to_address"],
            cc=d.get("cc", ""),
            subject=d["subject"],
            body=d["body"],
            material_ids=d["material_ids"],
            marker=marker,
            mode=mode,
        )
        results.append(result)
    return results
