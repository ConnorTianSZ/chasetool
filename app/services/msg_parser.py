"""
.msg 文件解析（extract-msg）→ 标准 dict，供 LLM 管线处理
"""
from __future__ import annotations
from pathlib import Path
import extract_msg


def parse_msg_file(msg_path: str | Path) -> dict:
    """解析 .msg 文件，返回 {subject, from_address, body, received_at}"""
    msg_path = Path(msg_path)
    msg = extract_msg.openMsg(str(msg_path))
    try:
        return {
            "subject":      msg.subject or "",
            "from_address": msg.sender or "",
            "body":         msg.body or "",
            "received_at":  str(msg.date) if msg.date else None,
        }
    finally:
        msg.close()
