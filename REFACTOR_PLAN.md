# 催货邮件逻辑重构计划

> 基于代码库完整阅读后输出，覆盖业务可行性 + 技术实现路径 + 文件级修改清单。

---

## 总体可行性结论

| 需求 | 可行性 | 复杂度 | 备注 |
|------|--------|--------|------|
| ① 加急后反馈最新交期字段 | ✅ 完全可行 | 低 | 走现有 migration 机制，不影响 Excel 导入 |
| ② 仅拉有 marker 邮件 + 统计 + 一键解析 | ✅ 完全可行 | 中 | 后端一行过滤；前端加统计栏+批量按钮 |
| ③ 去掉置信度显示 | ✅ 完全可行 | 极低 | 纯前端删一行 |
| ④ 不接受 → 加急回复子界面 | ✅ 完全可行 | 中高 | win32com ReplyAll 已有先例，需新增 API + 前端 modal |

---

## 需求 ① — 新增字段：加急后反馈最新交期（`urgent_feedback_eta`）

### 业务逻辑
- 采购员加急跟进后，供应商口头/邮件外承诺了新交期，但 SAP 未更新
- 其他部门（计划、项目）无法从 SAP 或 `current_eta`（Excel 来的）看到这个信息
- 需要一个"采购员人工记录"字段，**Excel 重新导入不能覆盖它**

### 字段设计
```
urgent_feedback_eta   DATE     -- 加急后供应商反馈的最新交期
urgent_feedback_note  TEXT     -- 可选备注（例如 "口头确认，等书面"）
urgent_feedback_at    DATETIME -- 记录时间（自动填写）
```

### 技术实现

**1. `app/db/connection.py` — `_MIGRATION_STMTS` 追加**
```python
"ALTER TABLE materials ADD COLUMN urgent_feedback_eta DATE",
"ALTER TABLE materials ADD COLUMN urgent_feedback_note TEXT",
"ALTER TABLE materials ADD COLUMN urgent_feedback_at DATETIME",
```
→ 每次启动自动 migrate，对旧库安全（忽略 "duplicate column" 异常）

**2. `app/update_policy.py`**
- `urgent_feedback_eta / note / at` **不加入** `FACT_FIELDS`
- 加入 `SENSITIVE_FIELDS`（保证 source priority 保护）
- 在 `SOURCE_PRIORITY` 中，`"buyer_manual": 4`（高于 `email_reply:3`），防止邮件解析入库误覆盖该字段

**3. `app/services/excel_io.py`**
- 现有逻辑中 `FACT_FIELDS` 的字段走直接 `UPDATE SET`，其余走 `bulk_update_fields`
- `urgent_feedback_eta` 不在 `FACT_FIELDS` → Excel 导入**完全不会触碰**它
- 无需修改 `excel_io.py`，天然隔离 ✅

**4. `app/models/material.py` + `app/api/materials.py`**
- `MaterialUpdate` 模型加入三个新字段（Optional）
- PATCH endpoint 允许更新这三个字段（source="buyer_manual"）

**5. `web/index.html` — 物料表**
- 列表新增一列"加急交期"（`urgent_feedback_eta`），标注橙色/加粗以区分 `current_eta`
- 详情面板（右侧滑出或 modal）新增可编辑区域：
  - 日期选择器（urgent_feedback_eta）
  - 文本框（urgent_feedback_note）
  - 保存按钮 → PATCH /materials/{id}

---

## 需求 ② — 收件审批改造：仅拉 marker 邮件 + 统计 + 一键解析

### 业务逻辑
- 当前 `pull_inbox` 把收件箱所有邮件都存入 DB，大量无关邮件干扰审批
- 只需处理 subject 里带 `[CB:...]` 标记的供应商回邮
- 拉取后希望看到"找到 X 封"，以及一个按钮批量触发 LLM 解析

### 技术实现

**1. `app/services/outlook_inbox.py` — `pull_inbox()` 加过滤**
```python
# 现在：所有邮件都存
marker = parse_marker(subject)
marker_str = marker.to_subject_tag() if marker else None
# 改为：无 marker 直接 skip
marker = parse_marker(subject)
if not marker:
    skipped += 1
    continue
marker_str = marker.to_subject_tag()
```
→ 单行改动，`skipped` 计数器已有，返回值里 `skipped` 含义更新一下注释

**2. `app/api/inbox.py` — `/pull` 返回更丰富的统计**
```python
# 现在返回：{"pulled": N, "pulled_days": D}
# 改为：{"pulled": N, "skipped_no_marker": M, "pulled_days": D}
```
拆分 `skipped_no_marker`（无标记跳过）和 `skipped_duplicate`（已存在跳过）

**3. 新增 batch parse endpoint**
```
POST /api/projects/{project_id}/inbox/parse_all
```
- 查询所有 `status='new'` 且 `parsed_marker IS NOT NULL` 的邮件
- 逐个调用 `parse_inbound_email()`
- 返回 `{ok: true, parsed: N, failed: F, details: [...]}`
- 可加 `limit` 参数防止一次过多

**4. `web/index.html` + `web/static/app.js`**

拉取完成后，在工具栏下方显示统计 banner：
```
✉️ 本次拉取到 12 封带标记的回邮（跳过 48 封无标记邮件）
```

新增按钮"🤖 一键解析所有"：
- `@click="parseAll()"` → 调用 `/inbox/parse_all`
- 显示进度 toast
- 完成后 `this.load()` 刷新列表

---

## 需求 ③ — 去掉置信度显示

### 技术实现（仅前端，1 行）

**`web/index.html` 第 512 行附近，删除：**
```html
<span style="color:#9ca3af;" x-text="'置信度 ' + ((item.llm_extracted_json.confidence||0)*100).toFixed(0)+'%'"></span>
```
→ DB 里 `llm_extracted_json` 仍保留 `confidence` 字段，不影响数据完整性

---

## 需求 ④ — "不接受"按钮 + 加急回复子界面

### 业务逻辑
- 收件审批中，供应商回复了一个采购方认为不可接受的交期
- 采购员要直接**回复**那封邮件，告知对方"我们需要交期提前至 [目标日期]"
- 支持保存草稿（先在 Outlook 里看一眼）或直接发送

### 技术可行性分析

**win32com ReplyAll 机制：**
```python
namespace = outlook.GetNamespace("MAPI")
original_msg = namespace.GetItemFromID(entry_id)  # 用 outlook_entry_id 取回原邮件
reply = original_msg.ReplyAll()                    # 创建回复（保留 To/CC/Subject/引用）
reply.Body = new_body
reply.Save()   # 草稿
# 或 reply.Send()  # 直接发
```
- `outlook_entry_id` 对 COM 拉取的邮件完全有效 ✅
- 对 `.msg` 上传的邮件（`entry_id` 以 `"msg:"` 开头），无法 ReplyAll → **前端提示用户需从 Outlook 手动回复，或新建邮件**

**`inbound_emails` 表新增字段：**
```sql
ALTER TABLE inbound_emails ADD COLUMN reject_target_eta TEXT;  -- 目标交期，用户输入
ALTER TABLE inbound_emails ADD COLUMN reject_sent_at DATETIME; -- 回复时间
ALTER TABLE inbound_emails ADD COLUMN reject_mode TEXT;        -- "draft" | "sent"
```
加入 `_MIGRATION_STMTS`

### 技术实现

**1. 新增 API endpoint**
```
POST /api/projects/{project_id}/inbox/{email_id}/reject
Body: {
  "target_eta": "05/20",       # MM/DD 格式，或 "05/15-05/20" 区间
  "mode": "draft" | "send",
  "selected_items": [...]      # 选中行的 po_number/item_no 列表（用于邮件正文）
}
```

实现逻辑（`app/api/inbox.py` 或新文件 `app/tools/reject_email.py`）：
```python
def reject_inbound(email_id, target_eta, mode, selected_items, project_id):
    row = conn.execute("SELECT * FROM inbound_emails WHERE id=?", ...)
    entry_id = row["outlook_entry_id"]
    
    # 构建正文
    body = build_reject_body(selected_items, target_eta)
    
    if entry_id.startswith("msg:"):
        return {"ok": False, "reason": "msg_upload_no_reply", 
                "message": "此邮件为手动上传，无法通过系统直接回复，请从 Outlook 手动回复"}
    
    outlook = _get_outlook()
    namespace = outlook.GetNamespace("MAPI")
    original = namespace.GetItemFromID(entry_id)
    reply = original.ReplyAll()
    reply.Body = body
    
    if mode == "send":
        reply.Send()
    else:
        reply.Save()
    
    # 更新 inbound_emails
    conn.execute("""UPDATE inbound_emails 
        SET status='rejected', operator_decision='reject',
            reject_target_eta=?, reject_sent_at=?, reject_mode=?
        WHERE id=?""", (target_eta, datetime.utcnow().isoformat(), mode, email_id))
    conn.commit()
    return {"ok": True, "mode": mode}
```

**回复正文模板：**
```
Dear [from_name],

感谢您的反馈。

对于以下物料，目前确认的交期无法满足我方需求，请确认是否可以将交期提前至 [target_eta]：

PO号        行号    当前回复交期
XXXXXXX     010     2025-07-01
...

请尽快确认，如有困难请及时说明。

谢谢。
```
（可配置为 HTML body 以便格式化）

**2. 前端 — 新增"❌ 不接受"按钮 + 子界面 modal**

在 `inbox-card-footer` 区域新增按钮（与"✅ 入库选中行"并列）：
```html
<button class="btn btn-warning btn-sm" @click="openReject(item)"
  x-show="item.status==='pending_confirm'">❌ 不接受</button>
```

`openReject(item)` 设置 `rejectTarget = item` 并打开 modal

**Modal 结构（仿现有催货邮件预览风格）：**
```
┌─────────────────────────────────────────────────────────┐
│  ❌ 回复：交期不接受                               [×]  │
├─────────────────────────────────────────────────────────┤
│  针对邮件：[subject]                                     │
│  选中物料（可取消勾选）：                                 │
│  ┌──────────────────────────────────────────────┐       │
│  │ ☑ PO号       行号    提取交期    物料号       │       │
│  │ ☑ 4500001234 010     2025-07-01  ABC-001     │       │
│  └──────────────────────────────────────────────┘       │
│                                                          │
│  目标交期：                                              │
│  ○ 指定日期    [MM/DD 日期选择器]                        │
│  ○ 时间段      [开始 MM/DD] ~ [结束 MM/DD]               │
│                                                          │
│  发送方式：  ○ 保存草稿    ○ 直接发送                   │
│                                                          │
│  [预览正文]                          [取消] [确认发送]   │
└─────────────────────────────────────────────────────────┘
```

`app.js` inbox 对象新增：
```javascript
rejectTarget: null,
rejectTargetEta: '',
rejectEtaMode: 'single',    // 'single' | 'range'
rejectEtaStart: '',
rejectEtaEnd: '',
rejectSendMode: 'draft',
rejectLoading: false,

openReject(item) {
  this.rejectTarget = item;
  // 默认勾选所有已选中的行
},
async submitReject() {
  const item = this.rejectTarget;
  const target_eta = this.rejectEtaMode === 'single'
    ? this.rejectTargetEta
    : `${this.rejectEtaStart}~${this.rejectEtaEnd}`;
  const selected_items = (item.llm_extracted_json?.items || [])
    .filter((_, i) => item._itemSelections?.[i]?.selected !== false)
    .map(ei => ({ po_number: ei.po_number, item_no: ei.item_no,
                  current_eta: item._itemSelections[i]?.new_eta || ei.new_eta }));
  
  const r = await api('POST', this.purl(`/inbox/${item.id}/reject`), {
    target_eta, mode: this.rejectSendMode, selected_items
  });
  if (r.ok) {
    toast(r.mode === 'send' ? '回复已发送' : '草稿已保存到 Outlook', 'success');
    this.rejectTarget = null;
    this.load();
  } else {
    toast(r.message || '操作失败', 'error');
  }
}
```

---

## 修改文件清单

| 文件 | 修改类型 | 内容摘要 |
|------|----------|----------|
| `app/db/connection.py` | 追加 | `_MIGRATION_STMTS` 加 5 条 ALTER TABLE |
| `app/db/schema.sql` | 更新 | 同步新字段（文档用，不影响运行） |
| `app/update_policy.py` | 追加 | `buyer_manual` source priority；`urgent_*` 加入 SENSITIVE_FIELDS |
| `app/models/material.py` | 追加 | `MaterialUpdate` 加 3 个 Optional 字段 |
| `app/api/materials.py` | 小改 | PATCH 支持 `urgent_feedback_*` 字段 |
| `app/services/outlook_inbox.py` | 小改 | `pull_inbox` 加 `if not marker: continue` + 统计拆分 |
| `app/api/inbox.py` | 中改 | `/pull` 返回更丰富统计；新增 `/parse_all` endpoint；新增 `/reject` endpoint |
| `app/tools/reject_email.py` | 新建 | `build_reject_body()` + `send_reject_reply()` 逻辑 |
| `web/index.html` | 中改 | ①物料列/详情面板加新字段；②pull统计banner；③删置信度；④不接受按钮+modal |
| `web/static/app.js` | 中改 | inbox 组件加 `parseAll()`、`openReject()`、`submitReject()`、`rejectTarget` 等状态 |

---

## 实施顺序建议

1. **① DB migration + update_policy**（无前端，可先上线，不破坏现有功能）
2. **③ 删置信度**（一行删除，立刻生效）
3. **② pull 过滤 + 统计 + parse_all**（后端先改，前端接着跟）
4. **① 前端 urgent_feedback_eta 展示/编辑**
5. **④ reject 后端 API + 前端 modal**（最复杂，最后做）

---

## 边界情况 & 风险点

| 风险 | 说明 | 处置 |
|------|------|------|
| `.msg` 上传邮件无法 ReplyAll | `outlook_entry_id` 以 `msg:` 开头，无法通过 COM 检索 | 前端显示友好提示"请从 Outlook 手动回复" |
| `outlook_entry_id` 过期 | Outlook EntryID 在某些操作后可能变化 | 捕获异常，返回 503，提示用户手动回复 |
| 批量解析 LLM 费用 | `parse_all` 可能一次触发大量 LLM 调用 | 加 `limit=20` 默认上限；前端显示预计解析数量确认框 |
| `urgent_feedback_eta` 覆盖规则 | 采购员多次更新时，应取最新值 | 用 `buyer_manual` source，priority=4 > email_reply=3，始终可覆盖 |
| 回复邮件正文语言 | 中英文混用邮件场景 | 模板可做双语，或在 project_settings 里配置语言偏好 |

