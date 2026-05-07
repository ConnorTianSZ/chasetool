# Chatbot 改进 2026-05-07

## 问题修复

### 工具调用 JSON 提取失败

**根因**: `app/api/chat.py` 中使用 `json.loads(raw.strip())` 要求 LLM 的整段响应必须是合法 JSON。但 LLM 经常会输出混合文本，如：

```
好的，我来为您查询当前物料概况。{"tool":"get_overview","args":{}}
```

此时 `json.loads` 抛出 `JSONDecodeError`，整个响应原样返回给用户，工具未被调用。

**解决方案**: 新增 `_extract_tool_call()` 函数（`app/api/chat.py`），采用括号匹配算法：
1. 先尝试全文 JSON 解析（纯 JSON 路径，快速成功）
2. 失败后寻找 `"tool"` 关键字，定位外层 `{}` 边界，提取 JSON 块
3. 返回 `(parsed_json, cleaned_text)` 元组，剩余文本可作为回答的补充

## 功能增强

### 1. 系统提示词改进

重新编写了 Chat API 的系统提示词（`SYSTEM_PROMPT`）：
- **明确规则**: "当需要查询或操作数据时，只输出 JSON，不要添加任何其他文字"
- 给出清晰的正例和反例
- 工具描述增加了参数说明和示例用法

### 2. 暴露更多工具给 Chatbot

之前 Chat 接口只暴露了 4 个只读工具。现在增加到 **7 个**：

| 工具 | 说明 | 类型 |
|---|---|---|
| `get_overview()` | 项目概览 | 只读 |
| `search_materials(...)` | 多条件搜索物料 | 只读 |
| `get_material(po_number, item_no)` | 物料详情 | 只读 |
| `query_aggregates(group_by, filters)` | 聚合统计 | 只读 |
| `update_material_field(po_number, item_no, field, value)` | 更新物料字段 | **写入** |
| `mark_focus(po_number, item_no, focus)` | 标记/取消重点 | **写入** |
| `generate_chase_drafts(material_ids, tone)` | 生成催货邮件 | 生成 |

后 3 个为新暴露的工具，使 chatbot 可以从对话中直接更新数据和生成催货邮件。

### 3. `mark_focus` 工具增强

`app/tools/update_material.py`:
- 新增 `po_number` + `item_no` 参数，支持从 Chat 直接按采购单号标记重点
- 新增 `focus` (bool) 参数，支持取消重点标记
- 保留原 `material_ids` 接口，向后兼容

### 4. 前端 Markdown 渲染

`web/index.html`:
- 添加 `marked.js` CDN (`marked` library)
- 将 assistant 消息的显示从 `x-text` 改为 `x-html`，使用 `marked.parse()` 渲染 Markdown
- 用户消息保持 `x-text` 纯文本显示

`web/static/style.css`:
- 新增 `.chat-bubble-md` 相关样式：代码块、表格、列表、引用等

## 文件变更清单

| 文件 | 变更类型 |
|---|---|
| `app/api/chat.py` | 重写 |
| `app/tools/update_material.py` | 增强 |
| `web/index.html` | 修改 |
| `web/static/style.css` | 新增样式 |
| `changelog/2026-05-07_chatbot-improvement.md` | 新增 |

## 后续可改进

- 支持多轮工具调用链式执行
- 支持流式输出（SSE）
- 为 Anthropic 启用原生 Tool Use API
- 前端 Chat UI 增强（对话树、历史管理等）
