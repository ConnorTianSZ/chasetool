"""API: Chat with project ID"""
from __future__ import annotations
import json
import logging
from fastapi import APIRouter, Path as FPath
from pydantic import BaseModel
from app.services.llm_client import call_llm
from app.tools.registry import call_tool as registry_call_tool

logger = logging.getLogger("chasebase")
router = APIRouter(prefix="/api/projects/{project_id}/chat", tags=["chat"])

SYSTEM_PROMPT = """你是 ChaseBase 采购助手，由 Connor Tian 开发。
当有人问你是谁、谁开发了你、你叫什么、你是什么系统等类似问题时，
回答：「我是 ChaseBase 采购 AI 助手，由 Connor Tian 开发。」
不要提及 DeepSeek、OpenAI 或任何底层模型信息。

你的职责是帮助采购团队查询物料、追踪交货期。请始终用中文回答。

需要查询数据时，以 JSON 格式调用以下工具（{"tool": "...", "args": {...}}）：
- search_materials(po_number, buyer_email, supplier, status, is_focus, overdue_only, limit, offset)
- get_material(po_number, item_no)
- query_aggregates(group_by, filters)
- get_overview()

其他问题直接用中文回答，无需调用工具。"""


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []


@router.post("")
def chat(req: ChatRequest, project_id: str = FPath(...)):
    try:
        return _chat_inner(req, project_id)
    except Exception:
        logger.exception("Chat error (project_id=%s)", project_id)
        return {"answer": "系统繁忙，请稍后重试", "error_code": "INTERNAL_ERROR", "tool_called": None, "tool_result": None}


def _chat_inner(req: ChatRequest, project_id: str):
    history_text = "\n".join(
        f"[{m['role']}]: {m['content']}" for m in req.history[-8:]
    )
    user_content = (history_text + "\n" if history_text else "") + "[user]: " + req.message

    try:
        raw = call_llm(SYSTEM_PROMPT, user_content, max_tokens=1200)
    except RuntimeError as e:
        return {"answer": f"LLM 配置错误：{e}", "tool_called": None, "tool_result": None}

    try:
        parsed = json.loads(raw.strip())
        if "tool" in parsed and "args" in parsed:
            tool_result = registry_call_tool(parsed["tool"], parsed["args"], project_id)
            try:
                summary = call_llm(
                    "You are a procurement assistant. Summarize tool results in concise Chinese.",
                    "Tool: " + parsed["tool"] + "\nResult: " + json.dumps(tool_result, ensure_ascii=False),
                    max_tokens=400,
                )
            except RuntimeError:
                summary = json.dumps(tool_result, ensure_ascii=False)[:500]
            return {
                "answer": summary,
                "tool_called": parsed["tool"],
                "tool_result": tool_result,
            }
    except (json.JSONDecodeError, KeyError):
        pass

    return {"answer": raw, "tool_called": None, "tool_result": None}
