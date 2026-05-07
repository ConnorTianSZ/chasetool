"""
邮件追踪标记协议

Subject 格式：[CB:{PO}/{ITEM}] {催货标题}
多行合并：   [CB:PO20240501/IT010,IT020] 催交期
"""
import re
from dataclasses import dataclass

MARKER_RE = re.compile(r"\[CB:([A-Z0-9\-]+)/([A-Z0-9,]+)\]", re.IGNORECASE)


@dataclass
class ChaseMarker:
    po_number: str
    item_nos: list[str]

    def to_subject_tag(self) -> str:
        items = ",".join(self.item_nos)
        return f"[CB:{self.po_number}/{items}]"


def build_marker(po_number: str, item_nos: list[str]) -> ChaseMarker:
    return ChaseMarker(po_number=po_number.upper(), item_nos=[i.upper() for i in item_nos])


def parse_marker(subject: str) -> ChaseMarker | None:
    """从邮件 subject 提取标记，失败返回 None"""
    m = MARKER_RE.search(subject)
    if not m:
        return None
    po = m.group(1).upper()
    items = [i.strip().upper() for i in m.group(2).split(",") if i.strip()]
    return ChaseMarker(po_number=po, item_nos=items)
