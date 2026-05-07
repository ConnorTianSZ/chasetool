# Changelog

## 2026-05-07 — 邮件模板重构 & Bug 修复

### 新增

- **双模板系统**：新增两种催货邮件模板，用户可在前端选择使用
  - `确认 OC` 模板：针对尚未确认交期的物料，生成 HTML 格式邮件，表格包含 WBS Element
  - `交期加急` 模板：针对已逾期物料，显示目标日期（key\_date），生成 HTML 格式邮件
- **HTML 邮件支持**：`send_chase_email()` 新增 `is_html` 参数，自动使用 Outlook `HTMLBody` 替代纯文本 Body
- **直接模板渲染**：新增 `build_email_body()` 函数，直接将模板渲染为 HTML，不再依赖 LLM 生成邮件正文，输出更稳定、速度更快

### 修改

- **`config/chase_email_templates/`**
  - `oc_confirmation.txt` — 新增：确认 OC 模板（HTML）
  - `urgent.txt` — 新增：交期加急模板（HTML）
- **`app/services/outlook_send.py`**
  - `load_template()` — 修复路径 Bug：原查找 `config/chase_template.txt`，现改为 `config/chase_email_templates/{type}.txt`；新增 `template_type` 参数
  - `send_chase_email()` — 新增 `is_html` 参数
  - 新增 `build_email_body()` 函数
- **`app/api/chase.py`**
  - `ChaseRequest` — 字段 `tone` 替换为 `chase_type`
  - `_build_drafts()` — 改用 `build_email_body()` 渲染正文
- **`app/tools/chase_email.py`**
  - `generate_chase_drafts()` — 参数 `tone` 替换为 `chase_type`，保留旧参数向后兼容
  - `send_chase_drafts()` — 自动检测 HTML 并传递 `is_html`
- **`app/api/chat.py`**
  - 系统提示中工具 #7 参数更新为 `chase_type`
- **`web/static/app.js`**
  - 新增 `chaseType` 状态
  - API 调用传递 `chase_type`
- **`web/index.html`**
  - 催货预览 Modal 新增模板类型单选按钮（确认 OC / 交期加急）

### 修复

- **`load_template()` 路径 Bug**：代码查找 `config/chase_template.txt`，但实际文件位于 `config/chase_email_templates/default.txt`，导致模板从未被正确加载，始终走硬编码 fallback
