"""LLM 工具注册表 — 暴露给 chat API 的工具列表"""
from app.tools.search import search_materials, get_material
from app.tools.update_material import update_material_field, mark_focus
from app.tools.chase_email import generate_chase_drafts, send_chase_drafts
from app.tools.parse_inbound import parse_inbound_email, apply_inbound_decision
from app.tools.dashboard import query_aggregates

TOOLS = {
    "search_materials":      search_materials,
    "get_material":          get_material,
    "update_material_field": update_material_field,
    "mark_focus":            mark_focus,
    "generate_chase_drafts": generate_chase_drafts,
    "send_chase_drafts":     send_chase_drafts,
    "parse_inbound_email":   parse_inbound_email,
    "apply_inbound_decision": apply_inbound_decision,
    "query_aggregates":      query_aggregates,
}


def call_tool(name: str, args: dict):
    fn = TOOLS.get(name)
    if fn is None:
        return {"error": f"未知工具: {name}"}
    return fn(**args)
