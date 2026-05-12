"""
DeepSeek 连通性测试脚本
========================
不依赖 ChaseBase 完整环境，单独验证 API 调用路径。

用法：
  python test_proxy.py              # 测试直连 DeepSeek（家庭/无代理环境）
  python test_proxy.py --proxy      # 测试通过企业代理连接 DeepSeek
  python test_proxy.py --local      # 测试本地代理服务（需先启动 proxy_server.py）

测试内容：
  1. 网络可达性检查
  2. API key 有效性
  3. 邮件解析任务（模拟真实 ChaseBase 调用）
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

# ── 加载 .env（如有）──────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✓ .env loaded")
except ImportError:
    print("⚠ python-dotenv not installed, reading from environment variables only")

import os

# ── 测试配置 ──────────────────────────────────────────────────────────────────
DEEPSEEK_BASE    = os.environ.get("API_BASE", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_API_KEY = os.environ.get("API_KEY", "") or os.environ.get("ANTHROPIC_API_KEY", "")
DEEPSEEK_MODEL   = os.environ.get("LLM_MODEL", "deepseek-chat")
HTTPS_PROXY      = os.environ.get("HTTPS_PROXY", "") or os.environ.get("HTTP_PROXY", "")
LOCAL_PROXY_URL  = f"http://127.0.0.1:{os.environ.get('LOCAL_PROXY_PORT', '11434')}/v1"

# 测试用邮件（模拟真实催货回复）
TEST_EMAIL_SUBJECT = "[CB:URG-TEST-001] Re: 催货 PO 12345678"
TEST_EMAIL_BODY    = """
您好，

关于您的催货邮件：
PO 12345678 item 10，预计5月20日可以发货
PO 12345678 item 20，供应商说本月底能到

请知悉。

谢谢
"""

TEST_SYSTEM_PROMPT = (
    "You are a procurement assistant. Extract structured delivery information "
    "from supplier reply emails.\n"
    "Output ONLY valid JSON: "
    '{"items":[{"po_number":"str|null","item_no":"str|null",'
    '"new_eta":"YYYY-MM-DD|null","remarks":"str"}],'
    '"general_remarks":"str","confidence":0.0}'
)

TEST_USER_PROMPT = f"Subject: {TEST_EMAIL_SUBJECT}\n\nBody:\n{TEST_EMAIL_BODY}"


def _call_api(base_url: str, api_key: str, model: str, proxy: str = "") -> dict:
    """通用调用函数，返回 API 响应或抛出异常。"""
    url = f"{base_url}/chat/completions"
    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": TEST_SYSTEM_PROMPT},
                {"role": "user",   "content": TEST_USER_PROMPT},
            ],
            "max_tokens": 512,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept":        "application/json",
            "User-Agent":    "ChaseBase-Test/1.0",
        },
        method="POST",
    )

    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    else:
        opener = urllib.request.build_opener()

    t0 = time.monotonic()
    with opener.open(req, timeout=60) as resp:
        elapsed = time.monotonic() - t0
        body = json.loads(resp.read().decode("utf-8"))
    return body, elapsed


def run_test(mode: str) -> bool:
    """运行一次完整测试，返回是否成功。"""
    print(f"\n{'='*60}")
    print(f"  测试模式: {mode}")
    print(f"{'='*60}")

    if mode == "direct":
        base_url = DEEPSEEK_BASE
        api_key  = DEEPSEEK_API_KEY
        proxy    = ""
        model    = DEEPSEEK_MODEL
        print(f"  端点  : {base_url}")
        print(f"  模型  : {model}")
        print(f"  代理  : (无)")

    elif mode == "proxy":
        base_url = DEEPSEEK_BASE
        api_key  = DEEPSEEK_API_KEY
        proxy    = HTTPS_PROXY
        model    = DEEPSEEK_MODEL
        print(f"  端点  : {base_url}")
        print(f"  模型  : {model}")
        print(f"  代理  : {proxy or '(未配置 HTTPS_PROXY)'}")
        if not proxy:
            print("\n⚠ 未检测到 HTTPS_PROXY 环境变量，此模式无法测试")
            print("  请在 .env 中设置 HTTPS_PROXY=http://rb-proxy-apac.bosch.com:8080")
            return False

    elif mode == "local":
        base_url = LOCAL_PROXY_URL
        api_key  = "local-proxy"
        proxy    = ""
        model    = "local-proxy"
        print(f"  端点  : {base_url}")
        print(f"  代理  : (本地代理负责)")

    print(f"\n  API Key: {'(已配置 ' + api_key[:8] + '...)' if api_key and api_key != 'local-proxy' else api_key}")
    if mode != "local" and not api_key:
        print("\n✗ 未配置 API_KEY，请在 .env 中设置 API_KEY=sk-xxx")
        return False

    print("\n  正在调用 API（邮件解析测试）...")
    print(f"  测试邮件: {TEST_EMAIL_SUBJECT}")

    try:
        result, elapsed = _call_api(base_url, api_key, model, proxy)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        print(f"\n✗ HTTP 错误 {e.code}: {raw[:300]}")
        if e.code == 401:
            print("  → API Key 无效或已过期")
        elif e.code == 429:
            print("  → 请求频率超限，稍后重试")
        return False
    except urllib.error.URLError as e:
        print(f"\n✗ 网络错误: {e.reason}")
        if "getaddrinfo" in str(e.reason).lower():
            print("  → DNS 解析失败：")
            if mode == "direct":
                print("    可能需要设置 HTTPS_PROXY（公司网络），或检查网络连接")
            elif mode == "proxy":
                print("    代理地址无法解析，检查 HTTPS_PROXY 是否正确")
            elif mode == "local":
                print("    本地代理未启动，请先运行: python proxy_server.py")
        elif "Connection refused" in str(e.reason):
            print("  → 连接被拒绝")
            if mode == "local":
                print("    请先启动本地代理: python proxy_server.py")
        return False
    except Exception as e:
        print(f"\n✗ 未知错误: {e}")
        return False

    # 解析响应
    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    usage   = result.get("usage", {})

    print(f"\n✓ API 调用成功！耗时 {elapsed*1000:.0f}ms")
    print(f"  Token 用量: 输入 {usage.get('prompt_tokens','?')} / 输出 {usage.get('completion_tokens','?')}")
    print(f"\n  模型原始回复:")
    print(f"  {content[:500]}")

    # 尝试解析 JSON
    try:
        # 移除可能的 markdown 包裹
        clean = content.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        parsed = json.loads(clean.strip())
        items  = parsed.get("items", [])
        print(f"\n  解析结果: 提取到 {len(items)} 个物料行")
        for i, item in enumerate(items, 1):
            print(f"    [{i}] PO={item.get('po_number')} item={item.get('item_no')} "
                  f"eta={item.get('new_eta')} remarks={item.get('remarks','')[:30]}")
        print(f"  confidence={parsed.get('confidence', '?')}")
        print("\n✓ JSON 解析成功，结果符合预期格式")
    except json.JSONDecodeError:
        print("\n⚠ 模型回复不是有效 JSON，但 API 调用本身成功")
        print("  可能需要调整 system prompt 或升级模型")

    return True


def main():
    parser = argparse.ArgumentParser(description="DeepSeek API 连通性测试")
    group  = parser.add_mutually_exclusive_group()
    group.add_argument("--proxy", action="store_true", help="通过企业代理测试（需设 HTTPS_PROXY）")
    group.add_argument("--local", action="store_true", help="测试本地代理服务（需先启动 proxy_server.py）")
    args = parser.parse_args()

    if args.proxy:
        mode = "proxy"
    elif args.local:
        mode = "local"
    else:
        mode = "direct"

    print("\nChaseBase — LLM 连通性测试")
    print(f"日期: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    success = run_test(mode)

    print(f"\n{'='*60}")
    if success:
        print("  ✅ 测试通过")
        if mode == "direct":
            print("  下一步: 在公司环境用 --proxy 测试（或直接部署本地代理）")
        elif mode == "proxy":
            print("  下一步: 部署 proxy_server.py，然后用 --local 测试")
        elif mode == "local":
            print("  下一步: 更新 ChaseBase .env，切换到本地代理模式")
    else:
        print("  ❌ 测试失败，请根据上方错误信息排查")
    print(f"{'='*60}\n")
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
