"""Helpers for material list display and derived states."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any
import re

import yaml

_PGR_PATH = Path(__file__).parent.parent.parent / "config" / "purchasing_group_mapping.yaml"
_DATE_RE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")


def load_pgr_map() -> dict[str, dict[str, str]]:
    if not _PGR_PATH.exists():
        return {}
    with open(_PGR_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {str(k).upper(): v for k, v in data.items() if isinstance(v, dict)}


def clean_date_value(value: Any) -> str | None:
    """Return YYYY-MM-DD, stripping Excel/Pandas midnight times."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none", "null"}:
        return None

    match = _DATE_RE.search(text)
    if match:
        year, month, day = match.groups()
        try:
            return date(int(year), int(month), int(day)).isoformat()
        except ValueError:
            return None

    try:
        return datetime.fromisoformat(text.replace("/", "-")).date().isoformat()
    except ValueError:
        return None


def format_display_date(value: Any) -> str:
    cleaned = clean_date_value(value)
    return cleaned.replace("-", "/") if cleaned else ""


def _format_mmdd(value: Any) -> str:
    display = format_display_date(value)
    return display[5:] if len(display) >= 10 else ""


def _is_zero_quantity(value: Any) -> bool:
    if value is None or value == "":
        return False
    try:
        return float(value) == 0
    except (TypeError, ValueError):
        return False


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def derive_material_state(row: dict[str, Any], key_date: str | date | datetime | None = None) -> dict[str, str]:
    effective_key_date = clean_date_value(key_date) or date.today().isoformat()
    today = date.today().isoformat()

    if _is_zero_quantity(row.get("open_quantity_gr")):
        return {"code": "delivered", "label": "已交货", "badge": "badge-delivered"}

    current_eta = clean_date_value(row.get("current_eta"))
    if not current_eta:
        return {"code": "no_oc", "label": "无OC", "badge": "badge-no-eta"}

    # current_eta 是否本身需要加急
    current_needs_chase = (current_eta < today) or (current_eta > effective_key_date)

    # 采购员手工录入的催后更新交期
    urgent_eta = clean_date_value(row.get("urgent_feedback_eta"))

    if urgent_eta and current_needs_chase:
        urgent_needs_chase = (urgent_eta < today) or (urgent_eta > effective_key_date)
        if not urgent_needs_chase:
            # SAP 交期看起来有问题，但采购员已确认催后新交期可接受
            # → 不触发催货，显示"交期与反馈不一致"提示其他部门
            return {"code": "eta_mismatch", "label": "交期与反馈不一致", "badge": "badge-eta-mismatch"}
        else:
            # 连催后确认的交期也超期 → 按 urgent_eta 判断严重程度
            if urgent_eta < today:
                return {"code": "overdue_now", "label": "应交未交", "badge": "badge-overdue-now"}
            return {"code": "overdue_keydate", "label": "晚于节点", "badge": "badge-overdue-keydate"}

    # 无 urgent_feedback_eta，走原有逻辑
    # 供应商已超过自身承诺日期（应交未交）
    if current_eta < today:
        return {"code": "overdue_now", "label": "应交未交", "badge": "badge-overdue-now"}

    # OC 日期晚于项目关键节点（将来交货但晚于项目节点，无法满足项目需求）
    if current_eta > effective_key_date:
        return {"code": "overdue_keydate", "label": "晚于节点", "badge": "badge-overdue-keydate"}

    return {"code": "normal", "label": "正常", "badge": "badge-open"}


def derive_chase_status(row: dict[str, Any]) -> dict[str, str]:
    chase_count = _to_int(row.get("chase_count"))
    feedback_time = row.get("supplier_feedback_time")

    if feedback_time:
        mmdd = _format_mmdd(feedback_time)
        feedback_count = _to_int(row.get("last_feedback_chase_count")) or chase_count
        if feedback_count:
            return {
                "code": "feedback",
                "label": f"已于 {mmdd} 第 {feedback_count} 次反馈",
                "badge": "badge-delivered",
            }
        return {"code": "feedback", "label": f"已于 {mmdd} 反馈", "badge": "badge-delivered"}

    if chase_count > 0:
        mmdd = _format_mmdd(row.get("last_chased_at") or row.get("last_chase_time"))
        date_part = f"于 {mmdd} " if mmdd else ""
        return {
            "code": "chased_no_feedback",
            "label": f"已第 {chase_count} 次催{date_part}未反馈",
            "badge": "badge-on_hold",
        }

    return {"code": "not_chased", "label": "未催", "badge": "badge-cancelled"}


def buyer_key(name: str | None, email: str | None) -> str:
    email_text = (email or "").strip()
    if email_text:
        return "email:" + email_text.lower()
    return "name:" + (name or "未知").strip().lower()


def enrich_material_row(
    row: dict[str, Any],
    key_date: str | date | datetime | None = None,
    pgr_map: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    item = dict(row)
    pgr_map = pgr_map or {}

    pg = str(item.get("purchasing_group") or "").strip().upper()
    pgr_info = pgr_map.get(pg, {})
    if not (item.get("buyer_name") or "").strip() and pgr_info.get("name"):
        item["buyer_name"] = pgr_info.get("name")
    if not (item.get("buyer_email") or "").strip() and pgr_info.get("email"):
        item["buyer_email"] = pgr_info.get("email")

    for field in (
        "order_date",
        "original_eta",
        "current_eta",
        "supplier_eta",
        "statical_delivery_date",
        "urgent_feedback_eta",
    ):
        if field in item:
            item[field] = clean_date_value(item.get(field))

    state = derive_material_state(item, key_date=key_date)
    chase = derive_chase_status(item)

    item["material_state"] = state["code"]
    item["material_state_label"] = state["label"]
    item["material_state_badge"] = state["badge"]
    item["chase_state"] = chase["code"]
    item["chase_label"] = chase["label"]
    item["chase_badge"] = chase["badge"]
    item["buyer_key"] = buyer_key(item.get("buyer_name"), item.get("buyer_email"))
    item["buyer_display"] = item.get("buyer_name") or item.get("buyer_email") or "未知"
    item["display_order_date"] = format_display_date(item.get("order_date"))
    item["display_current_eta"] = format_display_date(item.get("current_eta"))
    item["display_supplier_eta"] = format_display_date(item.get("supplier_eta"))
    item["display_last_chased_at"] = format_display_date(item.get("last_chased_at"))
    item["display_supplier_feedback_time"] = format_display_date(item.get("supplier_feedback_time"))
    item["display_urgent_feedback_eta"] = format_display_date(item.get("urgent_feedback_eta"))
    return item
