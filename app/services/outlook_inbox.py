"""Outlook inbox pull service"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timedelta
from app.db.connection import get_connection
from app.services.email_marker import parse_marker, ChaseMarker, LegacyChaseMarker

logger = logging.getLogger("chasebase.outlook_inbox")

_outlook_app = None


def _looks_like_disconnected_com_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "object is not connected" in text
        or "not connected to the server" in text
        or "-2147220995" in text
    )


def _reset_outlook_cache() -> None:
    global _outlook_app
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


def _open_inbox_items():
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            outlook = _get_outlook()
            namespace = outlook.GetNamespace("MAPI")
            inbox = namespace.GetDefaultFolder(6)
            messages = inbox.Items
            messages.Sort("[ReceivedTime]", True)
            return messages
        except Exception as exc:
            last_error = exc
            if attempt == 0 and _looks_like_disconnected_com_error(exc):
                logger.warning(
                    "pull_inbox: cached Outlook COM object disconnected; "
                    "resetting and retrying once"
                )
                _reset_outlook_cache()
                continue
            raise
    raise last_error  # type: ignore[misc]


def pull_inbox(days: int = 14, project_id: str = "default") -> dict:
    """拉取 Outlook 收件箱，将带 [CB:...] marker 的邮件入库。

    Args:
        days:       向前回溯天数，默认 14 天
        project_id: 对应项目数据库 ID

    Returns:
        {pulled, skipped_no_marker, skipped_duplicate, skipped_error}
    """
    logger.info(
        "pull_inbox START: project_id=%r days=%d", project_id, days
    )

    messages = _open_inbox_items()

    conn  = get_connection(project_id)
    pulled            = 0   # 成功入库（有 marker）
    skipped_duplicate = 0   # 已存在，跳过
    skipped_no_marker = 0   # 无 [CB:...] marker，跳过
    skipped_error     = 0   # 处理单封邮件时发生异常

    # 统一转 naive 再比较，避免 aware vs naive TypeError
    since = datetime.now() - timedelta(days=days)
    logger.debug("pull_inbox: scanning emails since %s", since.isoformat())

    def _naive(dt):
        if isinstance(dt, datetime) and dt.tzinfo is not None:
            return dt.replace(tzinfo=None)
        return dt

    try:
        try:
            message_count = int(messages.Count)
        except Exception:
            logger.exception(
                "pull_inbox: cannot read Outlook inbox message count; "
                "treating as skipped error"
            )
            skipped_error += 1
            message_count = 0

        for msg_index in range(1, message_count + 1):
            try:
                msg = messages.Item(msg_index)
                received = msg.ReceivedTime
                recv_dt = received if isinstance(received, datetime) \
                    else datetime.fromtimestamp(float(received))
                if _naive(recv_dt) < since:
                    logger.debug(
                        "pull_inbox: reached email older than cutoff (%s), stopping scan",
                        recv_dt.isoformat() if hasattr(recv_dt, "isoformat") else recv_dt,
                    )
                    break

                subject = str(msg.Subject or "")

                # ── 仅处理 subject 带 [CB:...] marker 的邮件 ──────────────
                marker = parse_marker(subject)
                if not marker:
                    logger.debug(
                        "pull_inbox: no marker in subject=%r, skipping", subject[:80]
                    )
                    skipped_no_marker += 1
                    continue

                entry_id = msg.EntryID
                if conn.execute(
                    "SELECT id FROM inbound_emails WHERE outlook_entry_id=?",
                    (entry_id,)
                ).fetchone():
                    logger.debug(
                        "pull_inbox: duplicate entry_id=%r subject=%r, skipping",
                        entry_id, subject[:80],
                    )
                    skipped_duplicate += 1
                    continue

                body        = str(msg.Body or "")
                sender      = str(msg.SenderEmailAddress or "")
                sender_name = str(msg.SenderName or "")
                marker_str = marker.to_subject_tag()

                # ── chase_log 反查关联 material_id ────────────────────────
                mat_id = None
                if isinstance(marker, LegacyChaseMarker):
                    row = conn.execute(
                        "SELECT id FROM materials WHERE po_number=? AND item_no=?",
                        (marker.po_number,
                         marker.item_nos[0] if marker.item_nos else ""),
                    ).fetchone()
                    if row:
                        mat_id = row[0]
                        logger.debug(
                            "pull_inbox: v1 marker matched material_id=%d "
                            "(po=%r item=%r)",
                            mat_id, marker.po_number,
                            marker.item_nos[0] if marker.item_nos else "",
                        )
                    else:
                        logger.warning(
                            "pull_inbox: v1 marker po=%r item=%r → "
                            "no matching material found",
                            marker.po_number,
                            marker.item_nos[0] if marker.item_nos else "",
                        )
                elif isinstance(marker, ChaseMarker):
                    lookup_tag = marker.to_subject_tag()
                    row = conn.execute(
                        "SELECT material_ids_json FROM chase_log "
                        "WHERE marker_tag=? ORDER BY sent_at DESC LIMIT 1",
                        (lookup_tag,),
                    ).fetchone()
                    if row:
                        ids = json.loads(row[0])
                        if ids:
                            mat_id = ids[0]
                            logger.debug(
                                "pull_inbox: v2 marker tag=%r → "
                                "matched material_id=%d (chase_log ids=%r)",
                                lookup_tag, mat_id, ids,
                            )
                        else:
                            logger.warning(
                                "pull_inbox: v2 marker tag=%r → "
                                "chase_log row found but material_ids_json is empty",
                                lookup_tag,
                            )
                    else:
                        logger.warning(
                            "pull_inbox: v2 marker tag=%r → "
                            "no matching chase_log entry; "
                            "project_no=%r pgr=%r — "
                            "check if project_no contains dots and regex matches correctly",
                            lookup_tag, marker.project_no, marker.pgr,
                        )

                conn.execute(
                    "INSERT INTO inbound_emails "
                    "(outlook_entry_id, from_address, from_name, subject, body, received_at, "
                    " parsed_marker, matched_material_id, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new')",
                    (entry_id, sender, sender_name, subject, body,
                     recv_dt.isoformat(), marker_str, mat_id),
                )
                logger.info(
                    "pull_inbox: inserted email subject=%r marker=%r "
                    "sender=%r material_id=%s",
                    subject[:80], marker_str, sender, mat_id,
                )
                pulled += 1

            except Exception:
                # 记录完整异常堆栈，便于排查单封邮件处理失败的原因
                # （不能静默 skip，否则真正的 bug 会被掩盖）
                logger.exception(
                    "pull_inbox: unexpected error processing one email; "
                    "skipping this message (skipped_error += 1)"
                )
                skipped_error += 1

        conn.commit()
        logger.info(
            "pull_inbox DONE: pulled=%d skipped_no_marker=%d "
            "skipped_duplicate=%d skipped_error=%d",
            pulled, skipped_no_marker, skipped_duplicate, skipped_error,
        )
    finally:
        conn.close()

    return {
        "pulled":             pulled,
        "skipped_no_marker":  skipped_no_marker,
        "skipped_duplicate":  skipped_duplicate,
        "skipped_error":      skipped_error,
    }
