"""
邮件追踪标记协议

新格式（v2）：[CB:{project_no}-{pgr}-{purpose}-{MMDD}-{seq}] {标题}
  purpose: OC | URG
  示例：[CB:P2024001-L11-OC-0507-1]
  示例（含点号项目）：[CB:M.6001515-L11-OC-0507-1]

旧格式（v1，兼容解析）：[CB:{PO}/{ITEM_NOS}]
  示例：[CB:PO20240501/IT010,IT020]

已知兼容行为：
  - project_no 允许包含点号（如 M.6001515），字符集为 [A-Z0-9\-_.]+
  - 供应商邮件客户端有时将主题中的 [CB: 变为 [CB.，解析时自动标准化前缀
"""
from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field
from datetime import date

logger = logging.getLogger("chasebase.email_marker")

# v2 新格式：[CB:项目号-PGR-purpose-MMDD-seq]
# project_no 字符集含点号（\.），支持 M.6001515 等格式
# [CB[:.] 兼容供应商邮件将冒号改为点号的情况
_V2_RE = re.compile(
    r"\[CB[:.]([A-Z0-9\-_.]+)-([A-Z0-9]+)-(OC|URG)-(\d{4})-(\d+)\]",
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
        project_no: 项目号（取 materials.project_no），允许包含点号（如 M.6001515）
        pgr:        采购组代码（取 materials.purchasing_group）
        purpose:    "oc" 或 "urg"
        seq:        当日序号（由调用方查 chase_log 计算）
        send_date:  发送日期，默认 today
    """
    d = send_date or date.today()
    mmdd = d.strftime("%m%d")
    marker = ChaseMarker(
        project_no=project_no.upper(),
        pgr=pgr.upper(),
        purpose=purpose.upper(),
        mmdd=mmdd,
        seq=seq,
    )
    logger.debug(
        "build_marker: project_no=%r pgr=%r purpose=%r seq=%d mmdd=%s → tag=%r",
        project_no, pgr, purpose, seq, mmdd, marker.to_subject_tag(),
    )
    return marker


def build_legacy_marker(po_number: str, item_nos: list[str]) -> LegacyChaseMarker:
    """构造 v1 旧格式 marker（仅用于兼容测试，不用于新发送）。"""
    return LegacyChaseMarker(
        po_number=po_number.upper(),
        item_nos=[i.upper() for i in item_nos],
    )


def parse_marker(subject: str) -> ChaseMarker | LegacyChaseMarker | None:
    """从邮件 subject 中提取 marker。

    优先尝试 v2 新格式，fallback v1 旧格式，均不匹配返回 None。

    兼容行为：
      - 项目号含点号（如 M.6001515）可正常匹配
      - 供应商邮件将 [CB: 变为 [CB. 时仍可解析（regex 前缀写法为 [CB[:.] ）
    """
    # v2
    m = _V2_RE.search(subject)
    if m:
        marker = ChaseMarker(
            project_no=m.group(1).upper(),
            pgr=m.group(2).upper(),
            purpose=m.group(3).upper(),
            mmdd=m.group(4),
            seq=int(m.group(5)),
        )
        logger.debug(
            "parse_marker v2: subject=%r → project_no=%r pgr=%r purpose=%r mmdd=%s seq=%d",
            subject[:80], marker.project_no, marker.pgr, marker.purpose,
            marker.mmdd, marker.seq,
        )
        return marker

    # v1 fallback
    m = _V1_RE.search(subject)
    if m:
        po = m.group(1).upper()
        items = [i.strip().upper() for i in m.group(2).split(",") if i.strip()]
        logger.debug(
            "parse_marker v1 (legacy): subject=%r → po=%r items=%r",
            subject[:80], po, items,
        )
        return LegacyChaseMarker(po_number=po, item_nos=items)

    logger.debug("parse_marker: no marker found in subject=%r", subject[:80])
    return None


def marker_tag_from_subject(subject: str) -> str | None:
    """从 subject 提取 marker tag 字符串（用于 chase_log 查询）。

    标准化规则：
      供应商邮件客户端有时将 [CB: 变为 [CB.，此函数仅替换开头的 [CB. 前缀，
      不影响项目号自身包含的点号（如 M.6001515 中的点不会被误替换）。
    """
    m2 = _V2_RE.search(subject)
    if m2:
        tag = m2.group(0)
        # 只标准化开头前缀 [CB. → [CB:，避免影响项目号内部的点
        if tag.startswith("[CB."):
            tag = "[CB:" + tag[4:]
        logger.debug("marker_tag_from_subject v2: %r → %r", subject[:80], tag)
        return tag
    m1 = _V1_RE.search(subject)
    if m1:
        tag = m1.group(0)
        logger.debug("marker_tag_from_subject v1: %r → %r", subject[:80], tag)
        return tag
    logger.debug("marker_tag_from_subject: no tag found in %r", subject[:80])
    return None
