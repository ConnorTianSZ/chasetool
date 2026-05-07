"""
邮件追踪标记协议

新格式（v2）：[CB:{project_no}-{pgr}-{purpose}-{MMDD}-{seq}] {标题}
  purpose: OC | URG
  示例：[CB:P2024001-L11-OC-0507-1]

旧格式（v1，兼容解析）：[CB:{PO}/{ITEM_NOS}]
  示例：[CB:PO20240501/IT010,IT020]
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from datetime import date

# v2 新格式：[CB:项目号-PGR-purpose-MMDD-seq]
_V2_RE = re.compile(
    r"\[CB:([A-Z0-9\-_]+)-([A-Z0-9]+)-(OC|URG)-(\d{4})-(\d+)\]",
    re.IGNORECASE,
)

# v1 旧格式：[CB:PO/ITEMS]（保留用于历史邮件解析）
_V1_RE = re.compile(r"\[CB:([A-Z0-9\-]+)/([A-Z0-9,]+)\]", re.IGNORECASE)


@dataclass
class ChaseMarker:
    """v2 新格式 Marker。

    v1 旧格式通过 LegacyChaseMarker 保持兼容。
    """
    project_no: str          # 项目号，如 P2024001
    pgr: str                 # 采购组代码，如 L11
    purpose: str             # OC | URG
    mmdd: str                # 发送日期 MMDD，如 0507
    seq: int = 1             # 同日同 base 的序号，防重复

    # 反向关联：发送时填入，供 inbox 匹配用
    material_ids: list[int] = field(default_factory=list)

    def to_subject_tag(self) -> str:
        purpose = self.purpose.upper()
        return f"[CB:{self.project_no}-{self.pgr}-{purpose}-{self.mmdd}-{self.seq}]"

    def base_key(self) -> str:
        """不含 seq 的唯一标识，用于当日去重计数。"""
        return f"{self.project_no}-{self.pgr}-{self.purpose.upper()}-{self.mmdd}"

    @property
    def is_oc(self) -> bool:
        return self.purpose.upper() == "OC"

    @property
    def is_urgent(self) -> bool:
        return self.purpose.upper() == "URG"


@dataclass
class LegacyChaseMarker:
    """v1 旧格式，仅用于兼容解析历史邮件，不再用于新发送。"""
    po_number: str
    item_nos: list[str]

    def to_subject_tag(self) -> str:
        items = ",".join(self.item_nos)
        return f"[CB:{self.po_number}/{items}]"


def build_marker(
    project_no: str,
    pgr: str,
    purpose: str,          # "oc" | "urg"
    seq: int = 1,
    send_date: date | None = None,
) -> ChaseMarker:
    """构造新格式 ChaseMarker。

    Args:
        project_no: 项目号（取 materials.project_no）
        pgr:        采购组代码（取 materials.purchasing_group）
        purpose:    "oc" 或 "urg"
        seq:        当日序号（由调用方查 chase_log 计算）
        send_date:  发送日期，默认 today
    """
    d = send_date or date.today()
    mmdd = d.strftime("%m%d")
    return ChaseMarker(
        project_no=project_no.upper(),
        pgr=pgr.upper(),
        purpose=purpose.upper(),
        mmdd=mmdd,
        seq=seq,
    )


def build_legacy_marker(po_number: str, item_nos: list[str]) -> LegacyChaseMarker:
    """构造 v1 旧格式 marker（仅用于兼容测试，不用于新发送）。"""
    return LegacyChaseMarker(
        po_number=po_number.upper(),
        item_nos=[i.upper() for i in item_nos],
    )


def parse_marker(subject: str) -> ChaseMarker | LegacyChaseMarker | None:
    """从邮件 subject 中提取 marker。

    优先尝试 v2 新格式，fallback v1 旧格式，均不匹配返回 None。
    """
    # v2
    m = _V2_RE.search(subject)
    if m:
        return ChaseMarker(
            project_no=m.group(1).upper(),
            pgr=m.group(2).upper(),
            purpose=m.group(3).upper(),
            mmdd=m.group(4),
            seq=int(m.group(5)),
        )
    # v1 fallback
    m = _V1_RE.search(subject)
    if m:
        po = m.group(1).upper()
        items = [i.strip().upper() for i in m.group(2).split(",") if i.strip()]
        return LegacyChaseMarker(po_number=po, item_nos=items)
    return None


def marker_tag_from_subject(subject: str) -> str | None:
    """从 subject 提取 marker tag 字符串（用于 chase_log 查询）。"""
    m2 = _V2_RE.search(subject)
    if m2:
        return m2.group(0)
    m1 = _V1_RE.search(subject)
    if m1:
        return m1.group(0)
    return None
