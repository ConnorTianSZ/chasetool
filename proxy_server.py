"""
ChaseBase 本地 LLM 转发代理
==============================
监听 http://127.0.0.1:11434/v1，接收 OpenAI-compatible 请求，
通过 urllib.ProxyHandler 转发至 DeepSeek（或其他 OpenAI-compatible API）。

解决 Bosch APAC 企业网络下 httpx/openai-sdk 无法可靠走系统代理的问题。

启动方式：
  python proxy_server.py

然后在 ChaseBase .env 中设置：
  LLM_PROVIDER=openai
  API_BASE=http://127.0.0.1:11434/v1
  API_KEY=local-proxy   (任意字符串，代理不做验证)
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

# ── 配置（优先读 .env，再读环境变量）──────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv 未安装时，直接读环境变量

UPSTREAM_API_BASE: str = os.environ.get("API_BASE", "https://api.deepseek.com").rstrip("/")
UPSTREAM_API_KEY: str  = os.environ.get("API_KEY", "") or os.environ.get("ANTHROPIC_API_KEY", "")
UPSTREAM_MODEL: str    = os.environ.get("LLM_MODEL", "deepseek-chat")
HTTPS_PROXY: str       = os.environ.get("HTTPS_PROXY", "") or os.environ.get("HTTP_PROXY", "")
LISTEN_HOST: str       = os.environ.get("LOCAL_PROXY_HOST", "127.0.0.1")
LISTEN_PORT: int       = int(os.environ.get("LOCAL_PROXY_PORT", "11434"))
MAX_RETRIES: int       = 3
RETRY_BASE_DELAY: float = 1.5   # seconds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [proxy] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("llm_proxy")

app = FastAPI(title="ChaseBase LLM Proxy", version="1.0")


# ── 核心转发函数 ──────────────────────────────────────────────────────────────

def _forward_to_upstream(payload: dict) -> dict:
    """
    用 urllib + ProxyHandler 把请求转发到上游 API。
    在 Bosch 企业网络下，urllib 是唯一可靠走代理的方式。
    """
    url = f"{UPSTREAM_API_BASE}/chat/completions"

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {UPSTREAM_API_KEY}",
            "Accept":        "application/json",
            "User-Agent":    "ChaseBase-Proxy/1.0",
        },
        method="POST",
    )

    if HTTPS_PROXY:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": HTTPS_PROXY, "https": HTTPS_PROXY})
        )
        logger.debug("Using proxy: %s", HTTPS_PROXY)
    else:
        opener = urllib.request.build_opener()
        logger.debug("No proxy configured, direct connection")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with opener.open(req, timeout=90) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            logger.error("HTTP %d from upstream (attempt %d): %s", e.code, attempt, raw[:300])
            if e.code in (400, 401, 403):
                raise  # 不重试认证/参数错误
            if attempt == MAX_RETRIES:
                raise
        except urllib.error.URLError as e:
            logger.warning("Network error (attempt %d/%d): %s", attempt, MAX_RETRIES, e.reason)
            if attempt == MAX_RETRIES:
                raise
        # Exponential backoff
        delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
        logger.info("Retrying in %.1fs...", delay)
        time.sleep(delay)

    raise RuntimeError("All retries exhausted")  # 理论上不会到这里


# ── API 路由 ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "upstream": UPSTREAM_API_BASE,
        "model":    UPSTREAM_MODEL,
        "proxy":    HTTPS_PROXY or "(none)",
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> JSONResponse:
    """OpenAI-compatible chat completions endpoint."""
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # 使用上游默认模型（如果请求中没有指定或指定的是占位符）
    if not body.get("model") or body["model"] in ("local-proxy", "proxy"):
        body["model"] = UPSTREAM_MODEL

    if not UPSTREAM_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Proxy not configured: API_KEY is empty. Set API_KEY in .env",
        )

    logger.info(
        "→ %s | %d msgs | max_tokens=%s",
        body.get("model"),
        len(body.get("messages", [])),
        body.get("max_tokens", "?"),
    )

    t0 = time.monotonic()
    try:
        result = _forward_to_upstream(body)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
        logger.error("Upstream HTTP %d: %s", e.code, raw[:200])
        raise HTTPException(status_code=e.code, detail=f"Upstream error {e.code}: {raw[:200]}")
    except urllib.error.URLError as e:
        logger.error("Upstream unreachable: %s", e.reason)
        raise HTTPException(
            status_code=503,
            detail=f"Cannot reach upstream API: {e.reason}. Check proxy settings.",
        )
    except Exception as e:
        logger.error("Unexpected error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    elapsed = time.monotonic() - t0
    usage = result.get("usage", {})
    logger.info(
        "← %dms | in=%s out=%s tokens",
        int(elapsed * 1000),
        usage.get("prompt_tokens", "?"),
        usage.get("completion_tokens", "?"),
    )

    return JSONResponse(content=result)


# ── 启动 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"""
╔══════════════════════════════════════════════════════════╗
║          ChaseBase LLM Proxy  v1.0                      ║
╠══════════════════════════════════════════════════════════╣
║  Listen : http://{LISTEN_HOST}:{LISTEN_PORT}/v1               ║
║  Backend: {UPSTREAM_API_BASE:<44} ║
║  Model  : {UPSTREAM_MODEL:<44} ║
║  Proxy  : {(HTTPS_PROXY or '(none)'):<44} ║
╚══════════════════════════════════════════════════════════╝

Set in ChaseBase .env:
  LLM_PROVIDER=openai
  API_BASE=http://{LISTEN_HOST}:{LISTEN_PORT}/v1
  API_KEY=local-proxy

Health check: http://{LISTEN_HOST}:{LISTEN_PORT}/health
""")
    uvicorn.run(app, host=LISTEN_HOST, port=LISTEN_PORT, log_level="warning")
