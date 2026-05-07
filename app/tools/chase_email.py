"""LLM Tool: 生成催货草稿 / 发送"""
from __future__ import annotations
from datetime import date
from typing import Literal

from app.db.connection import get_connection
from app.services.email_marker import build_marker, ChaseMarker
from app.services.material_view import derive_material_state, enrich_material_row, load_pgr_map
from app.services.outlook_send import send_chase_email, build_chase_subject, build_email_body

# state code → chase_type 映射
_STATE_TO_CHASE_TYPE: dict[str, str] = {
    "no_oc":   "oc_confirmation",
    "overdue": "urgent",
}

# chase_type → marker purpose
_CHASE_TYPE_TO_PURPOSE: dict[str, str] = {
    "oc_confirmation": "OC",
    "urgent":          "URG",
}


def _get_key_date(project_id: str) -> str:
    conn = get_connection(project_id)
    try:
        row = conn.execute(
            "SELECT value FROM project_settings WHERE key='material_key_date'"
        ).fetchone()
        return row["value"] if row else ""
    finally:
        conn.close()


def _next_seq(conn, base_key: str) -> int:
    """当日同 base_key 的已发送记录数 + 1，用于 marker seq 去重。"""
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM chase_log "
        "WHERE marker_tag LIKE ? AND date(sent_at) = date('now')",
        (f"%{base_key}%",),
    ).fetchone()
    return (row["cnt"] if row else 0) + 1


def build_drafts(
    material_ids: list[int],
    project_id: str,
    chase_type_override: str | None = None,
) -> dict:
    """
    核心 draft 生成逻辑（v2）。

    - 自动用 derive_material_state() 推断每条物料的 chase_type
    - 分组键：(buyer_email, project_no, derived_chase_type)
    - 每组生成一封邮件，跨 PO 合并
    - 返回 {drafts: [...], skipped: [...]}

    Args:
        material_ids:        物料 DB id 列表
        project_id:          项目 id
        chase_type_override: 强制指定类型（"oc_confirmation"|"urgent"），覆盖自动推断
    """
    key_date = _get_key_date(project_id)
    pgr_map  = load_pgr_map()

    conn = get_connection(project_id)
    try:
        placeholders = ",".join("?" * len(material_ids))
        raw_rows = [
            dict(r) for r in conn.execute(
                f"SELECT * FROM materials WHERE id IN ({placeholders})",
                material_ids,
            ).fetchall()
        ]
    finally:
        conn.close()

    # --- 状态推断 & 分流 ---
    drafts_groups: dict[tuple, list[dict]] = {}
    skipped: list[dict] = []

    for row in raw_rows:
        enriched = enrich_material_row(row, key_date=key_date, pgr_map=pgr_map)
        state_code = enriched.get("material_state", "")

        if chase_type_override:
            chase_type = chase_type_override
        else:
            chase_type = _STATE_TO_CHASE_TYPE.get(state_code)

        if not chase_type:
            # delivered / normal → 跳过
            skipped.append({
                "id":          enriched.get("id"),
                "po_number":   enriched.get("po_number"),
                "item_no":     enriched.get("item_no"),
                "state":       state_code,
                "state_label": enriched.get("material_state_label", state_code),
                "reason":      "无需催促（已交货或在期内）",
            })
            continue

        buyer_email = enriched.get("buyer_email") or ""
        project_no  = enriched.get("project_no") or "UNKNOWN"
        group_key   = (buyer_email, project_no, chase_type)
        drafts_groups.setdefault(group_key, []).append(enriched)

    # --- 构建 drafts ---
    drafts = []
    conn2 = get_connection(project_id)
    try:
        for (buyer_email, project_no, chase_type), mats in drafts_groups.items():
            pgr     = (mats[0].get("purchasing_group") or "XX").strip().upper()
            purpose = _CHASE_TYPE_TO_PURPOSE[chase_type]
            mmdd    = date.today().strftime("%m%d")
            base_key = f"{project_no.upper()}-{pgr}-{purpose}-{mmdd}"
            seq      = _next_seq(conn2, base_key)

            marker  = build_marker(project_no=project_no, pgr=pgr, purpose=purpose, seq=seq)
            subject = build_chase_subject(marker)
            body    = build_email_body(chase_type, mats, key_date=key_date)

            drafts.append({
                "to_address":   buyer_email,
                "subject":      subject,
                "body":         body,
                "material_ids": [m["id"] for m in mats],
                "marker_tag":   marker.to_subject_tag(),
                "chase_type":   chase_type,
                "project_no":   project_no,
                "pgr":          pgr,
                "buyer_name":   mats[0].get("buyer_name") or "",
                "po_numbers":   sorted({m["po_number"] for m in mats}),
            })
    finally:
        conn2.close()

    return {"drafts": drafts, "skipped": skipped}


def generate_chase_drafts(
    material_ids: list[int],
    chase_type: str | None = None,
    tone: str = "",
    project_id: str = "default",
) -> dict:
    """
    LLM Tool 入口：生成催货草稿（不发送）。

    chase_type 可选，传入时强制覆盖自动推断；不传则由 derive_material_state() 自动决定。
    返回 {drafts: [...], skipped: [...]}
    """
    return build_drafts(
        material_ids=material_ids,
        project_id=project_id,
        chase_type_override=chase_type or None,
    )


def send_chase_drafts(
    drafts: list[dict],
    mode: Literal["draft", "send"] = "draft",
    project_id: str = "default",
) -> list[dict]:
    """发送或保存草稿，返回每封的结果。"""
    from app.services.email_marker import parse_marker

    results = []
    for d in drafts:
        marker  = parse_marker(d.get("marker_tag", "") or d.get("subject", ""))
        is_html = (
            d["body"].strip().startswith("<html")
            or d["body"].strip().startswith("<!DOCTYPE")
        )
        result = send_chase_email(
            to_address=d["to_address"],
            cc=d.get("cc", ""),
            subject=d["subject"],
            body=d["body"],
            material_ids=d["material_ids"],
            marker=marker,
            mode=mode,
            project_id=project_id,
            is_html=is_html,
        )
        results.append(result)
    return results
