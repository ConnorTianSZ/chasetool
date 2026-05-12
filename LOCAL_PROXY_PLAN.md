# 本地转发代理方案 — 可行性分析与实施计划

> 目标：在公司内网跑一个轻量 OpenAI-compatible 转发代理，解决 Bosch APAC proxy 下  
> httpx/openai-sdk 不稳定问题，同时保持 DeepSeek 为后端 LLM（成本最优）。

---

## 一、现状诊断

### 当前调用链路

```
ChaseBase App
  └─ llm_client.py  _call_openai_compat()
       ├─ [有代理] urllib.ProxyHandler → Bosch APAC proxy (rb-proxy-apac:8080)
       │                                    → DeepSeek API (api.deepseek.com)
       └─ [无代理] openai.OpenAI SDK ─────────────────────────────────────────┘
```

### 已知问题

| 问题 | 根因 | 频率 |
|------|------|------|
| `getaddrinfo failed` | httpx 底层不走 HTTP_PROXY 环境变量 | 高频 |
| OpenAI SDK 超时 | httpcore 直连被 Bosch 防火墙拦截 | 中频 |
| urllib 路径可用 | urllib 正确读取 ProxyHandler → 已验证可用 | 稳定 ✅ |

**结论：** urllib 路径本身是稳定的，问题在于 OpenAI SDK 和 httpx 绕过了系统代理。  
最小改动方案：**把 urllib 路径提升为独立代理服务**，让 ChaseBase 连接 `http://127.0.0.1:PORT`（无需系统代理），代理进程内部用 urllib 走 Bosch proxy。

---

## 二、方案对比

### 方案 A — 自建最小代理（推荐 ✅）

**架构：**
```
ChaseBase App
  └─ openai.OpenAI(base_url="http://127.0.0.1:11434/v1")  ← 标准 SDK，无代理
       └─ proxy_server.py (FastAPI, 本机 port 11434)
            └─ urllib.ProxyHandler → Bosch proxy → DeepSeek API
```

**优点：**
- 复用现有 `_call_openai_compat` 中已验证的 urllib 逻辑
- 无第三方依赖（只需 fastapi + uvicorn，已在 requirements 中）
- `llm_client.py` 几乎不改：只需把 `provider` 切到 `openai`，`api_base` 改为 `http://127.0.0.1:11434/v1`
- 可在同一台 Windows 机器或局域网任意机器上运行
- 后续换 LLM（Ollama/Azure）只改代理配置，应用代码零修改

**缺点：**
- 多一个进程需要启动（可加入 `run.bat` 自动启动）

**可行性评级：** ★★★★★ — 技术风险极低，代码量 ~100 行

---

### 方案 B — LiteLLM 开源代理

```
ChaseBase → LiteLLM proxy (port 4000) → DeepSeek
```

**优点：** 功能丰富，支持多模型路由、缓存、日志  
**缺点：** 需要额外安装 `litellm[proxy]`（>50 MB），且其内部也走 httpx，在 Bosch 代理下需额外配置 `HTTPS_PROXY`（又回到老问题）  
**可行性评级：** ★★★☆☆ — 引入新依赖，代理问题未必解决

---

### 方案 C — 直接修复 llm_client.py（不加代理层）

在现有 urllib 路径基础上增加重试、超时优化。

**优点：** 最少变动  
**缺点：** 每台机器仍需配置 `HTTPS_PROXY`，多用户部署麻烦；无法统一管理 API key  
**可行性评级：** ★★★☆☆ — 短期修补，不适合长期

---

## 三、推荐方案 A — 详细实施计划

### Phase 0：可行性验证（今天，30 分钟）

**目标：** 确认 urllib → Bosch proxy → DeepSeek 路径通畅，DeepSeek API Key 有效。

步骤：
1. 运行 `test_proxy.py`（见附件），直接用 urllib 调 DeepSeek `/chat/completions`
2. 确认返回 JSON 且 content 有意义
3. 若失败，排查：API_KEY 是否填写、proxy 地址是否正确、防火墙是否允许

**成功标准：** 控制台打印 DeepSeek 的回复内容，无报错。

---

### Phase 1：搭建本地代理服务（1-2 小时）

新建文件 `proxy_server.py`，核心功能：

```
POST /v1/chat/completions  ← OpenAI 兼容格式输入
  │
  ├─ 解析 request body（model, messages, max_tokens）
  ├─ 用 urllib.ProxyHandler 调 DeepSeek
  └─ 返回 OpenAI 兼容格式响应
```

关键设计决策：
- **端口：** 默认 `11434`（与 Ollama 同，方便将来无缝切换本地 LLM）
- **认证：** 代理服务读取本机 `.env` 中的 `API_KEY`，ChaseBase 侧不需要配 API_KEY
- **重试：** 内置 3 次重试 + exponential backoff（解决 Bosch proxy 偶发超时）
- **健康检查：** `GET /health` 返回 `{"status":"ok","backend":"deepseek"}`

---

### Phase 2：ChaseBase 适配（30 分钟）

`.env` 修改（仅 3 行）：

```bash
# 之前
LLM_PROVIDER=custom
API_BASE=https://api.deepseek.com
API_KEY=sk-xxx

# 之后（本地代理模式）
LLM_PROVIDER=openai          # 走标准 openai SDK，无代理
API_BASE=http://127.0.0.1:11434/v1
API_KEY=local-proxy           # 任意字符串（代理服务不验证）
# API_KEY 真实值只存在 proxy_server.py 的 .env 里（或同一 .env）
```

`llm_client.py` 改动：无需改动（`openai` provider 路径已存在，且现在连 `localhost` 无需系统代理）

---

### Phase 3：集成与测试（1 小时）

测试矩阵：

| 场景 | 方法 | 预期 |
|------|------|------|
| 代理服务未启动 | ChaseBase 调 LLM | 返回 ConnectionRefused，友好报错 |
| DeepSeek API Key 错误 | 代理服务启动，Key 错 | 返回 401，ChaseBase 日志可见 |
| Bosch proxy 断开 | 正常使用中断网 | 3 次重试后超时，ChaseBase 显示错误 |
| 正常解析邮件 | 收件箱有标记邮件 | 解析结果与现有一致 |
| 生成催货邮件 | 选中物料生成 | 邮件正文质量与现有一致 |

---

### Phase 4：打包与部署（30 分钟）

修改 `run.bat`，启动顺序：

```bat
@echo off
echo [1/2] Starting local LLM proxy...
start "LLM Proxy" python proxy_server.py

echo [2/2] Starting ChaseBase...
timeout /t 2 /nobreak > nul
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

可选（更健壮）：用 Windows 任务计划程序把 `proxy_server.py` 设为开机自启服务。

---

## 四、文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `proxy_server.py` | **新建** | 本地 OpenAI-compatible 转发代理，~100 行 |
| `test_proxy.py` | **新建** | 独立连通性测试，不依赖 ChaseBase 完整环境 |
| `.env.example` | **更新** | 新增本地代理配置块，注明两种模式 |
| `run.bat` | **更新** | 先启动代理再启动应用 |
| `app/llm_client.py` | **不改** | openai provider 路径已支持自定义 base_url |
| `requirements.txt` | **确认** | fastapi/uvicorn 已在列，无需新增 |

---

## 五、风险与对策

| 风险 | 概率 | 对策 |
|------|------|------|
| Bosch proxy 地址变更 | 低 | 代理地址集中在 proxy_server.py 的 .env，一处修改 |
| DeepSeek 服务不可用 | 低 | 代理层加 fallback（返回明确错误），ChaseBase 展示友好提示 |
| 端口 11434 被占用 | 中 | .env 中可配置 `LOCAL_PROXY_PORT`，默认 11434 |
| 多人共用一台 proxy 机器 | — | 监听 `0.0.0.0:11434`，.env 改 `API_BASE=http://[proxy机IP]:11434/v1` |
| Windows 防火墙阻止 11434 | 中 | 改用 8001/8002 等常见端口，或放行规则 |

---

## 六、后续演进路径

```
现在（Phase 0-4）           未来可选
────────────────────        ─────────────────────
ChaseBase                   ChaseBase
  └─ proxy_server.py          └─ proxy_server.py
       └─ DeepSeek                  ├─ DeepSeek（备用/测试）
                                    ├─ Azure OpenAI（公司合规）
                                    └─ Ollama local（完全离线）
```

proxy_server.py 作为统一入口，未来切换后端 LLM 只需改代理配置，ChaseBase 应用代码**零修改**。

---

*生成时间：2026-05-12*
