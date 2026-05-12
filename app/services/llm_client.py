"""
LLM client wrapper — 支持 Anthropic / OpenAI / 自定义兼容端点
"""
from __future__ import annotations
import json
from app.config import get_settings


def _resolve_key():
    """按优先级返回 API key: api_key > anthropic_api_key > env fallback"""
    s = get_settings()
    return s.api_key or s.anthropic_api_key or ""


def call_llm(
    system: str,
    user: str,
    model: str | None = None,
    max_tokens: int = 1024,
    response_format: str = "text",
) -> str:
    settings = get_settings()
    provider = (settings.llm_provider or "anthropic").lower()
    model = model or settings.llm_model or ""

    if response_format == "json":
        system = system + "\nRespond in valid JSON only. No markdown."

    if provider == "anthropic":
        return _call_anthropic(system, user, model, max_tokens)

    # OpenAI 兼容（也覆盖大部分第三方代理）
    if provider in ("openai", "custom"):
        return _call_openai_compat(system, user, model, max_tokens)

    raise ValueError(f"Unsupported LLM provider: {provider}")


def _call_anthropic(system: str, user: str, model: str, max_tokens: int) -> str:
    import anthropic
    settings = get_settings()
    api_key = _resolve_key()
    if not api_key:
        raise RuntimeError("API key not configured. Set ANTHROPIC_API_KEY or API_KEY in Settings.")
    kwargs = {"api_key": api_key}
    if settings.api_base:
        kwargs["base_url"] = settings.api_base
    client = anthropic.Anthropic(**kwargs)
    message = client.messages.create(
        model=model or "claude-sonnet-4-20250514",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return message.content[0].text


def _call_openai_compat(system: str, user: str, model: str, max_tokens: int) -> str:
    """OpenAI 兼容格式（DeepSeek / Azure 等）

    Bosch 企业网络下，httpx 无法可靠通过 HTTP_PROXY 环境变量走代理
    （httpcore 直连 → getaddrinfo failed）。
    有代理时改用 urllib.request.ProxyHandler，该方案已在生产环境验证可用。
    无代理时沿用 OpenAI SDK。
    """
    import json as _json
    import urllib.request
    import urllib.error

    settings = get_settings()
    api_key = _resolve_key()
    if not api_key:
        raise RuntimeError(
            "API key not configured. Set API_KEY or ANTHROPIC_API_KEY in Settings."
        )

    proxy = settings.https_proxy or settings.http_proxy

    # ── 企业代理路径：urllib + ProxyHandler（生产验证可用）──────────────────
    if proxy:
        api_base = (settings.api_base or "https://api.deepseek.com").rstrip("/")
        url = f"{api_base}/chat/completions"

        payload = _json.dumps(
            {
                "model": model or "deepseek-v4-flash",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max_tokens,
                "stream": False,
            },
            ensure_ascii=False,
        ).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "User-Agent": "ChaseBase/1.0",
            },
            method="POST",
        )

        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
        try:
            with opener.open(req, timeout=60) as resp:
                body = _json.loads(resp.read().decode("utf-8"))
            return body["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"DeepSeek API HTTP {e.code}: {raw[:500]}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"DeepSeek API 网络错误: {e.reason}")

    # ── 无代理路径：标准 OpenAI SDK ────────────────────────────────────────
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai package not installed. Run: pip install openai")

    kwargs = {"api_key": api_key}
    if settings.api_base:
        kwargs["base_url"] = settings.api_base

    client = OpenAI(**kwargs)
    resp = client.chat.completions.create(
        model=model or "deepseek-v4-flash",
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content or ""


def parse_email_for_eta(email_subject: str, email_body: str) -> dict:
    """
    Extract structured delivery info from unstructured supplier reply emails.

    Handles Chinese informal text, relative dates (今天/明天), and general
    statements that apply to all items.

    Returns:
      {
        "items": [
          {"po_number": str|None, "item_no": str|None,
           "new_eta": "YYYY-MM-DD"|None, "remarks": str}
        ],
        "general_remarks": str,
        "confidence": float
      }
    """
    from datetime import date, timedelta
    today      = date.today()
    today_str  = today.isoformat()
    tomorrow   = (today + timedelta(days=1)).isoformat()
    day_after  = (today + timedelta(days=2)).isoformat()
    today_mmdd = today.strftime("%m/%d")
    tmr_mmdd   = (today + timedelta(days=1)).strftime("%m/%d")

    sys_lines = [
        "You are a procurement assistant. Extract structured delivery information from supplier reply emails.",
        "Emails are often in informal Chinese. Extract PO numbers, item numbers, ETAs, and remarks.",
        "",
        f"TODAY = {today_str}  (use this to resolve relative dates)",
        f'Relative date mapping: "今天"="{today_str}", "明天"="{tomorrow}", "后天"="{day_after}"',
        '"月底" = last day of current month. "下周" = next Monday.',
        "",
        "PO NUMBER rules:",
        "- PO is typically an 8-digit number (may have leading zeros, e.g. 0080386542)",
        '- May be prefixed with "PO", "采购单", or appear bare',
        "- Preserve the number exactly as written (keep leading zeros if present)",
        "",
        "ITEM NUMBER rules:",
        '- Item is typically a number ending in 0 (e.g. 10, 20, 40, 100)',
        '- May be prefixed with "ITEM", "item", "行", "项", or appear right after PO number',
        "",
        "GENERAL STATEMENT rules:",
        '- If supplier gives ONE date for ALL items (e.g. "全部明天到", "以上物料周五发货"):',
        "  1. Set general_remarks to a summary like '明天（MM/DD）可到'",
        "  2. Apply that ETA and remark to EVERY identified item in items[]",
        "- If no items are individually identified, create one entry with po_number=null, item_no=null",
        "",
        "REMARKS rules:",
        '- Convert relative dates in remarks to concrete form: e.g. "明天" → "明天（MM/DD）"',
        '- Keep short: max 30 Chinese characters',
        "",
        "Output ONLY valid JSON:",
        '{"items":[{"po_number":"str|null","item_no":"str|null","new_eta":"YYYY-MM-DD|null","remarks":"str"}],"general_remarks":"str","confidence":0.0}',
        "",
        "--- FEW-SHOT EXAMPLES ---",
        "",
        "Input: '80386456 10项今天交'",
        f'{{"items":[{{"po_number":"80386456","item_no":"10","new_eta":"{today_str}","remarks":"今天（{today_mmdd}）交货"}}],"general_remarks":"","confidence":0.95}}',
        "",
        "Input: '0080386542 ITEM20 已发 明天到\\nPO 80386542 ITEM 40 供应商还没回复'",
        f'{{"items":[{{"po_number":"0080386542","item_no":"20","new_eta":"{tomorrow}","remarks":"已发，明天（{tmr_mmdd}）到"}},{{"po_number":"80386542","item_no":"40","new_eta":null,"remarks":"供应商还没回复"}}],"general_remarks":"","confidence":0.88}}',
        "",
        f"Input: 'PO 12345678和87654321全部明天（{tmr_mmdd}）能到'",
        f'{{"items":[{{"po_number":"12345678","item_no":null,"new_eta":"{tomorrow}","remarks":"明天（{tmr_mmdd}）可到"}},{{"po_number":"87654321","item_no":null,"new_eta":"{tomorrow}","remarks":"明天（{tmr_mmdd}）可到"}}],"general_remarks":"明天（{tmr_mmdd}）可到","confidence":0.85}}',
        "",
        "--- END EXAMPLES ---",
        "Output JSON only. No markdown, no extra text.",
    ]
    system = "\n".join(sys_lines)
    user   = f"Subject: {email_subject}\n\nBody:\n{email_body[:3000]}"
    raw    = call_llm(system, user, response_format="json")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "items": [{"po_number": None, "item_no": None,
                       "new_eta": None, "remarks": raw[:300]}],
            "general_remarks": "",
            "confidence": 0.1,
        }

    # Compat: old format {new_eta, po_number, item_nos}
    if "items" not in parsed and "new_eta" in parsed:
        item_nos = parsed.get("item_nos") or [None]
        parsed = {
            "items": [
                {"po_number": parsed.get("po_number"),
                 "item_no":   str(i) if i else None,
                 "new_eta":   parsed.get("new_eta"),
                 "remarks":   parsed.get("remarks", "")}
                for i in item_nos
            ],
            "general_remarks": parsed.get("remarks", ""),
            "confidence": parsed.get("confidence", 0.5),
        }

    return parsed


def generate_chase_email(materials: list, tone: str = "formal", template: str = "") -> str:
    lines = []
    for m in materials:
        lines.append(
            "  - PO %s line%s %s supplier:%s eta:%s" % (
                m.get("po_number", ""),
                m.get("item_no", ""),
                m.get("part_no", ""),
                m.get("supplier", ""),
                m.get("current_eta", ""),
            )
        )
    mat_lines = "\n".join(lines)
    tone_cn = "formal" if tone == "formal" else "friendly"
    system = "You are a procurement assistant. Write a " + tone_cn + " chase email body in Chinese. No subject line."
    tpl_part = ("Reference template:\n" + template) if template else ""
    user = "Materials to chase:\n" + mat_lines + "\n\n" + tpl_part
    return call_llm(system, user, max_tokens=800)
