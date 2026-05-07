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

你的职责是帮助采购团队查询物料、追踪交货期、更新数据。请始终用中文回答。

## 工具调用规则

当需要查询或操作数据时，**只输出 JSON，不要添加任何其他文字**，格式为：
{"tool": "工具名", "args": {"参数名": "参数值"}}

例如：{"tool": "get_overview", "args": {}}
例如：{"tool": "search_materials", "args": {"supplier": "华为", "overdue_only": true}}

不需要使用工具时，直接用中文回答。

## 可用工具

1. get_overview() — 获取项目概览（总物料数、进行中、逾期数、重点数、已交货数）
2. search_materials(po_number, buyer_email, supplier, status, is_focus, overdue_only, limit, offset)
   — 多条件搜索物料，所有参数可选。status: open/delivered/cancelled/on_hold
3. get_material(po_number, item_no) — 获取单个物料详情
4. query_aggregates(group_by, filters) — 聚合统计，group_by 可选 status/supplier/buyer_email/buyer_name
5. update_material_field(po_number, item_no, field, value)
   — 更新物料字段。field 可选: current_eta, supplier_eta, supplier_remarks, status, is_focus
   — 示例：{"tool": "update_material_field", "args": {"po_number": "4500012345", "item_no": "10", "field": "current_eta", "value": "2025-06-30"}}
6. mark_focus(po_number, item_no, focus)
   — 标记或取消重点物料。focus=true 打标，focus=false 取消。
   示例：{"tool": "mark_focus", "args": {"po_number": "4500012345", "item_no": "10", "focus": true}}
7. generate_chase_drafts(material_ids, tone)
   — 生成催货邮件草稿（按供应商分组），material_ids 是数据库 ID 列表，tone: formal/friendly"""


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []


def _extract_tool_call(text: str) -> tuple[dict | None, str]:
    """从 LLM 原始回复中提取工具调用 JSON。

    返回 (parsed, cleaned_text):
    - parsed: 解析后的工具调用 dict（含 tool/args），或 None
    - cleaned_text: 移除 JSON 后的剩余显示文本
    """
    stripped = text.strip()

    # 尝试 1: 全文就是合法 JSON
    try:
        obj = json.loads(stripped)
        if isinstance(obj, dict) and "tool" in obj and "args" in obj:
            return obj, ""
    except json.JSONDecodeError:
        pass

    # 尝试 2: 从混合文本中提取 {"tool": ..., "args": {...}}
    idx = stripped.find('"tool"')
    if idx >= 0:
        start = idx
        while start >= 0 and stripped[start] != "{":
            start -= 1
        if start >= 0 and stripped[start] == "{":
            depth = 0
            for end in range(start, len(stripped)):
                if stripped[end] == "{":
                    depth += 1
                elif stripped[end] == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = stripped[start:end+1]
                        try:
                            obj = json.loads(candidate)
                            if "tool" in obj and "args" in obj:
                                cleaned = (stripped[:start] + stripped[end+1:]).strip()
                                return obj, cleaned
                        except json.JSONDecodeError:
                            pass
                        break

    return None, text


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

    # 提取工具调用
    tool_call, text_part = _extract_tool_call(raw)

    if tool_call:
        try:
            tool_result = registry_call_tool(tool_call["tool"], tool_call["args"], project_id)
            # 将工具结果送 LLM 总结成中文
            try:
                summary = call_llm(
                    "You are a procurement assistant. Summarize tool results in concise Chinese.",
                    "Tool: " + tool_call["tool"] + "\nResult: " + json.dumps(tool_result, ensure_ascii=False),
                    max_tokens=400,
                )
            except RuntimeError:
                summary = json.dumps(tool_result, ensure_ascii=False)[:500]

            # 如果 LLM 原回复在 JSON 前有思考文本，附在回答前
            if text_part:
                summary = text_part + "\n\n" + summary

            return {
                "answer": summary,
                "tool_called": tool_call["tool"],
                "tool_result": tool_result,
            }
        except Exception:
            logger.exception("Tool call failed (project_id=%s, tool=%s)", project_id, tool_call["tool"])
            return {
                "answer": f"工具调用失败，请稍后重试",
                "tool_called": tool_call["tool"],
                "tool_result": None,
            }

    # 纯文本回复
    return {"answer": raw, "tool_called": None, "tool_result": None}
