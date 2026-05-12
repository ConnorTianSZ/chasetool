"""Activity logging — operation frequency + per-material chase analytics.

Two tables:
  activity_log        — one row per user action (all event types)
  chase_material_log  — one row per material per chase action
                        (supplier/manufacturer denormalized for easy GROUP BY)

Usage:
    from app.db.activity import log_activity, log_chase_action

    # Simple event:
    log_activity("inbox_pull", project_id, meta={"emails_found": 5, "days": 7})

    # Chase event (writes both tables):
    log_chase_action("chase_sent", project_id, drafts=drafts, skipped=skipped)
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("chasebase")

# ── event_type constants ────────────────────────────────────────────────────
EVT_STARTUP           = "startup"
EVT_IMPORT_EXCEL      = "import_excel"
EVT_ETA_UPLOAD        = "eta_update_upload"
EVT_CHASE_DRAFT       = "chase_draft"
EVT_CHASE_SENT        = "chase_sent"
EVT_INBOX_PULL        = "inbox_pull"
EVT_CHAT_QUERY        = "chat_query"
EVT_ETA_MANUAL        = "eta_update_manual"


# ── public API ──────────────────────────────────────────────────────────────

def log_activity(
    event_type: str,
    project_id: str,
    *,
    meta: dict[str, Any] | None = None,
) -> int | None:
    """Write one activity_log row.  Never raises — errors go to logger only.

    Returns the inserted row id, or None on failure.
    """
    from app.db.connection import get_connection
    try:
        conn = get_connection(project_id)
        try:
            cur = conn.execute(
                "INSERT INTO activity_log (event_type, project_id, meta_json)"
                " VALUES (?,?,?)",
                (
                    event_type,
                    project_id,
                    json.dumps(meta, ensure_ascii=False, default=str) if meta else None,
                ),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()
    except Exception:
        logger.exception(
            "activity_log write failed (event=%s project=%s)", event_type, project_id
        )
        return None


def log_chase_action(
    event_type: str,
    project_id: str,
    *,
    drafts: list[dict],
    skipped: list[dict],
) -> None:
    """Log a chase_draft or chase_sent event.

    Writes:
      • One activity_log row with aggregate counts
      • One chase_material_log row per material in each draft
        (supplier + manufacturer denormalized from materials table)

    Never raises.
    """
    from app.db.connection import get_connection

    all_mat_ids: list[int] = []
    for d in drafts:
        all_mat_ids.extend(d.get("material_ids") or [])

    try:
        conn = get_connection(project_id)
        try:
            # 1. activity_log aggregate row
            cur = conn.execute(
                "INSERT INTO activity_log (event_type, project_id, meta_json)"
                " VALUES (?,?,?)",
                (
                    event_type,
                    project_id,
                    json.dumps(
                        {
                            "draft_count":    len(drafts),
                            "skipped_count":  len(skipped),
                            "material_count": len(all_mat_ids),
                            "material_ids":   all_mat_ids,
                        },
                        default=str,
                    ),
                ),
            )
            act_id = cur.lastrowid

            # 2. Fetch supplier / manufacturer for all involved materials
            mat_map: dict[int, dict] = {}
            if all_mat_ids:
                ph = ",".join("?" * len(all_mat_ids))
                rows = conn.execute(
                    f"SELECT id, po_number, item_no, supplier, manufacturer"
                    f" FROM materials WHERE id IN ({ph})",
                    all_mat_ids,
                ).fetchall()
                mat_map = {r["id"]: dict(r) for r in rows}

            # 3. chase_material_log — one row per material per draft
            for draft in drafts:
                chase_type = draft.get("chase_type")
                to_address = draft.get("to_address")
                for mid in (draft.get("material_ids") or []):
                    m = mat_map.get(mid, {})
                    conn.execute(
                        "INSERT INTO chase_material_log"
                        " (activity_log_id, project_id, event_type, material_id,"
                        "  po_number, item_no, supplier, manufacturer,"
                        "  chase_type, to_address)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (
                            act_id, project_id, event_type, mid,
                            m.get("po_number"), m.get("item_no"),
                            m.get("supplier"),  m.get("manufacturer"),
                            chase_type, to_address,
                        ),
                    )

            conn.commit()

            logger.debug(
                "log_chase_action OK event=%s project=%s drafts=%d materials=%d",
                event_type, project_id, len(drafts), len(all_mat_ids),
            )
        finally:
            conn.close()

    except Exception:
        logger.exception(
            "log_chase_action failed (event=%s project=%s)", event_type, project_id
        )


# ── analytics query helpers (for dashboard / future API) ────────────────────

SUPPLIER_CHASE_FREQ_SQL = """
-- 供应商加急频次（按催货次数降序）
SELECT
    supplier,
    manufacturer,
    COUNT(*)                          AS chase_times,
    COUNT(DISTINCT po_number)         AS po_count,
    COUNT(DISTINCT material_id)       AS material_count,
    MIN(created_at)                   AS first_chased_at,
    MAX(created_at)                   AS last_chased_at
FROM chase_material_log
WHERE event_type = 'chase_sent'
  AND chase_type IN ('urgent_now', 'urgent_keydate')
  {where_extra}
GROUP BY supplier, manufacturer
ORDER BY chase_times DESC;
"""

OPERATION_FREQ_SQL = """
-- 操作频次（按月 + 事件类型汇总）
SELECT
    strftime('%Y-%m', created_at)     AS month,
    event_type,
    COUNT(*)                          AS executions
FROM activity_log
  {where_extra}
GROUP BY month, event_type
ORDER BY month DESC, event_type;
"""
