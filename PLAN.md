# ChaseBase 实施 Plan

> 项目代号：**ChaseBase**（Chase = 催货 / Base = 数据底座）
> 目标用户：项目主采购（单人单机使用，本地数据，不共享）
> 状态：规划完成，待落地

---

## 1. 项目目标

把项目主采购原本散落在 Excel 中的"催货 + 收集回执 + 跟踪交期"工作，统一到一个本地工具中：

- 工具是**数据收发中心**，不替代 Excel 作为对外交付的载体
- 核心闭环：**Excel 导入 → 一键催货 → 拉/解析回邮 → 人工确认 → 回写 Excel**
- 锦上添花：**自然语言查询 + Dashboard + 自然语言生成 Dashboard**

---

## 2. 设计原则

1. **不替代 Excel**：所有外部沟通仍以 Excel 为载体，工具只读写它，不破坏格式。
2. **PO + Item 为复合唯一键**：所有外部数据匹配点。
3. **字段级溯源**：每个敏感字段记录"谁更新的"，决定能否被覆盖。
4. **采购回邮 > 对话指令 > Excel 导入** 的优先级，回邮内容不被 Excel 重导覆盖。
5. **LLM 永远要人工确认**：解析邮件 / 生成催货稿 / 翻译查询，全部走"建议 + 操作员确认"。
6. **本地优先**：数据库、配置、邮件解析全在本机；只有 LLM 推理走 API。
7. **可观测**：所有写入都记 `field_updates`，方便回滚与审计。

---

## 3. 已确认的技术选型

| 项 | 选型 |
|---|---|
| 前端 | 浏览器 + HTML + Alpine.js / Vue + Chart.js |
| 后端 | FastAPI + uvicorn（本地 127.0.0.1） |
| 数据库 | SQLite（单文件） |
| Excel I/O | `openpyxl`（保格式回写） + `pandas`（读取/校验） |
| Outlook 集成 | `pywin32` (`win32com.client`) + `extract-msg`（.msg 文件） |
| LLM | API 调用（默认 Anthropic SDK，封装为可替换 client） |
| 包管理 | `pip` + `venv` |
| 部署 | 纯本地单机，双击 `run.bat` 启动 |
| 邮箱 | 项目主采购个人 Outlook 邮箱 |

---

## 4. 整体架构

```
┌─────────────────────────────────────────────────────┐
│  浏览器前端 (localhost:8000)                          │
│   · 物料表 / 筛选 / 重点行                             │
│   · 一键催货 / 审批卡片                                │
│   · Dashboard / 对话框                                │
└────────────────────┬────────────────────────────────┘
                     │ HTTP
┌────────────────────▼────────────────────────────────┐
│  FastAPI                                             │
│  ┌────────────┬──────────────┬────────────────────┐ │
│  │ services/  │              │                    │ │
│  │ excel_io   │ outlook_*    │ llm_client         │ │
│  └────────────┴──────────────┴────────────────────┘ │
│  ┌──────────────────────────────────────────────┐   │
│  │ tools/  (LLM function-calling 入口)           │   │
│  └──────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────┐   │
│  │ update_policy / db / models                   │   │
│  └──────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────┐   │
│  │ SQLite (data/chasebase.db)                    │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

---

## 5. 目录结构

```
ChaseBase/
├── app/
│   ├── main.py                  # FastAPI 入口
│   ├── config.py                # 加载 .env
│   ├── db/
│   │   ├── schema.sql
│   │   ├── connection.py
│   │   └── migrations/
│   ├── models/
│   │   └── material.py          # Pydantic 模型
│   ├── services/
│   │   ├── excel_io.py
│   │   ├── outlook_send.py
│   │   ├── outlook_inbox.py
│   │   ├── msg_parser.py
│   │   ├── email_marker.py
│   │   └── llm_client.py
│   ├── tools/
│   │   ├── search.py
│   │   ├── update_material.py
│   │   ├── chase_email.py
│   │   ├── parse_inbound.py
│   │   ├── dashboard.py
│   │   └── registry.py
│   ├── api/
│   │   ├── materials.py
│   │   ├── imports.py
│   │   ├── chase.py
│   │   ├── inbox.py
│   │   ├── chat.py
│   │   └── dashboard.py
│   └── update_policy.py
├── web/
│   ├── index.html
│   ├── static/{app.js, style.css}
│   └── components/
├── config/
│   ├── excel_column_mapping.yaml   # ★ 你之后直接改这个
│   └── chase_email_templates/
│       └── default.txt
├── data/
│   ├── chasebase.db
│   └── excel_cache/
├── logs/
├── .env.example
├── requirements.txt
└── run.bat
```

---

## 6. SQLite Schema

```sql
-- 主表：物料行（PO + Item 复合唯一键）
CREATE TABLE materials (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    po_number       TEXT NOT NULL,
    item_no         TEXT NOT NULL,
    part_no         TEXT,
    description     TEXT,
    quantity        REAL,
    unit            TEXT,
    supplier        TEXT,

    original_eta            DATE,
    current_eta             DATE,
    current_eta_source      TEXT,            -- manual_import / email_reply / chat_command
    supplier_eta            DATE,            -- 采购回邮中的最新交期
    supplier_feedback_time  DATETIME,
    supplier_remarks        TEXT,
    supplier_remarks_source TEXT,

    buyer_name      TEXT,
    buyer_email     TEXT,
    status          TEXT DEFAULT 'open',     -- open/delivered/cancelled/on_hold

    is_focus        BOOLEAN DEFAULT 0,
    focus_reason    TEXT,

    chase_count     INTEGER DEFAULT 0,
    last_chase_time DATETIME,
    escalation_flag BOOLEAN DEFAULT 0,

    extra_json      TEXT,                    -- 原 Excel 多余列原样保留
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(po_number, item_no)
);

-- 字段级更新历史
CREATE TABLE field_updates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id   INTEGER NOT NULL REFERENCES materials(id),
    field_name    TEXT NOT NULL,
    old_value     TEXT,
    new_value     TEXT,
    source        TEXT NOT NULL,
    source_ref    TEXT,                       -- 邮件 entry_id / 文件 hash
    operator      TEXT,
    confirmed     BOOLEAN DEFAULT 1,
    timestamp     DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 催货邮件发出记录
CREATE TABLE chase_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    tracking_marker   TEXT NOT NULL,
    material_ids      TEXT NOT NULL,         -- JSON array
    to_address        TEXT,
    cc                TEXT,
    subject           TEXT,
    body              TEXT,
    outlook_entry_id  TEXT,
    sent_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
    sent_by_method    TEXT                    -- direct_send / draft_only
);

-- 拉取的入站邮件 + LLM 解析结果
CREATE TABLE inbound_emails (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    outlook_entry_id    TEXT UNIQUE,
    from_address        TEXT,
    subject             TEXT,
    body                TEXT,
    received_at         DATETIME,
    parsed_marker       TEXT,
    matched_material_id INTEGER REFERENCES materials(id),
    llm_extracted_json  TEXT,
    status              TEXT DEFAULT 'new',   -- new/pending_confirm/applied/rejected/escalate
    confirmed_at        DATETIME,
    operator_decision   TEXT
);

-- Excel 导入记录
CREATE TABLE imports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path    TEXT,
    file_hash    TEXT,
    rows_added   INTEGER,
    rows_updated INTEGER,
    rows_skipped INTEGER,
    imported_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 用户保存的 dashboard
CREATE TABLE dashboards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT,
    config_json TEXT,
    is_default  BOOLEAN DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_mat_po_item    ON materials(po_number, item_no);
CREATE INDEX idx_mat_buyer      ON materials(buyer_email);
CREATE INDEX idx_mat_focus      ON materials(is_focus);
CREATE INDEX idx_inbound_marker ON inbound_emails(parsed_marker);
```

---

## 7. 字段更新优先级（核心治理规则）

```python
SOURCE_PRIORITY = {
    "email_reply":   3,    # 采购真实回邮 - 最高
    "chat_command":  2,    # 主采购通过对话明确指令
    "manual_import": 1,    # Excel 导入
    "system":        0,
}

# 写入语义敏感字段时（current_eta / supplier_eta / supplier_remarks）：
if NEW.priority < CURRENT.priority:
    skip 写入，UI 上提示"被拦截，可手动覆盖"
else:
    写入 + 记录 field_updates
```

**事实字段**（导入永远胜出）：`po_number`, `item_no`, `part_no`, `quantity`, `unit`, `original_eta`, `buyer_name`, `buyer_email`, `supplier`
**语义敏感字段**（按优先级）：`current_eta`, `supplier_eta`, `supplier_remarks`, `status`, `is_focus`

---

## 8. 邮件追踪标记协议

**Subject 格式**：`[CB:{PO}/{ITEM}] {催货标题}`
- 多行合并：`[CB:PO20240501/IT010,IT020,IT030] 催交期`
- 解析正则：`\[CB:([A-Z0-9\-]+)/([A-Z0-9,]+)\]`

**回邮匹配优先级**：
1. Subject 含标记 → 直接定位（≈99% 准确）
2. 无标记 → LLM 从正文提取 PO/Item → 显示给操作员人工确认

---

## 9. Excel 列别名映射（默认值，由用户编辑）

将落到 `config/excel_column_mapping.yaml`，你之后直接改这一份。导入 Excel 时按 alias 列表模糊匹配（trim + 大小写不敏感 + 全半角统一）。匹配不到的列原样塞进 `extra_json`，不丢数据。

```yaml
# config/excel_column_mapping.yaml
# 内部字段名: [可被识别的 Excel 表头别名]

po_number:
  - PO号
  - PO Number
  - PO No
  - 采购订单号
  - 订单号
  - PO#

item_no:
  - 行号
  - Item
  - Item No
  - Line
  - Line No
  - 项次

part_no:
  - 物料号
  - 料号
  - Part No
  - Part Number
  - 物料编码
  - Material No

description:
  - 物料描述
  - 描述
  - Description
  - 品名
  - Material Description

quantity:
  - 数量
  - Qty
  - Quantity
  - 订单数量

unit:
  - 单位
  - Unit
  - UOM

supplier:
  - 供应商
  - Supplier
  - Vendor
  - 厂商

original_eta:
  - 原始交期
  - 原交期
  - 计划交期
  - Original ETA
  - Plan Date
  - 计划到货日期

current_eta:
  - 当前交期
  - 最新交期
  - Current ETA
  - 预计到货日期
  - 交货期

supplier_eta:
  - 供应商交期
  - 供应商反馈交期
  - Supplier ETA
  - Confirmed ETA

supplier_remarks:
  - 供应商备注
  - 反馈备注
  - Remarks
  - 备注

buyer_name:
  - 采购员
  - 采购
  - Buyer
  - Buyer Name

buyer_email:
  - 采购员邮箱
  - Buyer Email
  - 邮箱

status:
  - 状态
  - Status

# 不在此表中的列 → 全部进 extra_json，不丢
```

**导入流程**：
1. 读 Excel header 行
2. 每个 header 与上述 alias 匹配（归一化后比对）
3. 匹配上的列映射到内部字段
4. 未匹配的列以 `{原表头: 值}` 进 `extra_json`
5. PO + Item 缺任一行直接进"导入异常"列表，让用户决定

**回写流程**（导出 / 更新原 Excel）：
1. 用 `openpyxl.load_workbook()` 打开（保留格式/公式/合并单元格）
2. 找到 PO + Item 列，逐行匹配 SQLite 中的最新值
3. 仅写入需要更新的 cell，**不重建 sheet**
4. 另存为 `{原文件名}_chasebase_updated.xlsx` 或覆盖（用户选）

---

## 10. LLM 工具清单（function calling）

LLM 在对话中**只调用工具，不直接写数据库**。所有写操作经过 `update_policy`。

| 工具 | 描述 | 副作用 |
|---|---|---|
| `search_materials(filter)` | 多条件筛选 | 只读 |
| `get_material(po, item)` | 单行详情 | 只读 |
| `update_material_field(po, item, field, value, source)` | 单字段更新（走优先级） | 写 |
| `mark_focus(material_ids, reason)` | 重点行打标 | 写 |
| `generate_chase_drafts(material_ids, tone)` | 生成催货邮件草稿 | 只生成草稿，不发送 |
| `send_chase_drafts(draft_ids, mode)` | 发出/保存草稿 | 写 + Outlook |
| `parse_inbound_email(email_id)` | 提取交期/remarks/置信度 | 只读，写 inbound_emails.llm_extracted_json |
| `apply_inbound_decision(email_id, decision, edits)` | 操作员确认后入库 | 写 |
| `query_aggregates(group_by, filters)` | Dashboard 数据 | 只读 |
| `create_dashboard(name, config)` | 保存自然语言生成的 dashboard | 写 dashboards |

---

## 11. 实施阶段与任务清单

### 阶段 0 — 骨架（先跑起来）
- [ ] 0.1 创建目录结构、`requirements.txt`、`run.bat`、`.env.example`
- [ ] 0.2 `app/db/schema.sql` + `app/db/connection.py`（首次启动自动建表）
- [ ] 0.3 `app/main.py` FastAPI 启动 + 静态前端入口
- [ ] 0.4 `web/index.html` 占位页（确认能访问）
- [ ] 0.5 `app/config.py` 加载 `.env`（API key、Excel 默认路径）

### 阶段 1 — Excel + 数据闭环
- [ ] 1.1 `config/excel_column_mapping.yaml` 默认配置
- [ ] 1.2 `app/services/excel_io.py`：`import_excel(path)` 含别名匹配 + extra_json 兜底
- [ ] 1.3 `app/update_policy.py`：字段优先级 + `field_updates` 自动写入
- [ ] 1.4 `app/api/materials.py` + `app/api/imports.py`
- [ ] 1.5 前端主表视图：分页、筛选（PO/采购员/状态/逾期）、is_focus 切换
- [ ] 1.6 `app/services/excel_io.py`：`export_back(path)` 仅改值不破格式
- [ ] 1.7 异常导入面板（缺 PO/Item 的行）

### 阶段 2 — Outlook 催货
- [ ] 2.1 `app/services/email_marker.py`：标记编/解码
- [ ] 2.2 `app/services/outlook_send.py`：build_draft / send / save entry_id
- [ ] 2.3 `config/chase_email_templates/default.txt` 邮件模板
- [ ] 2.4 `app/api/chase.py` + 前端"勾选行 → 一键催货"
- [ ] 2.5 默认走 **草稿模式**，操作员在 Outlook 内审阅后再发；提供"直接发送"开关
- [ ] 2.6 chase_count / last_chase_time 自动累加

### 阶段 3 — Outlook 拉取 + LLM 解析
- [ ] 3.1 `app/services/llm_client.py`（封装 Anthropic SDK，可换）
- [ ] 3.2 `app/services/outlook_inbox.py`：拉取 inbox + 标记匹配 + 去重
- [ ] 3.3 `app/tools/parse_inbound.py`：LLM 结构化输出（new_eta / remarks / confidence）
- [ ] 3.4 `app/api/inbox.py` + 前端审批卡片 UI
  - 左侧：原邮件高亮（标记位置、关键词）
  - 右侧：解析字段，可编辑
  - 底部三按钮：**接受入库 / 仍需催货 / 升级**
- [ ] 3.5 `app/services/msg_parser.py` + 拖入 .msg 文件解析（同 LLM 管线）

### 阶段 4 — Dashboard + 对话
- [ ] 4.1 默认 dashboard：逾期分布、采购员负载、催货率、反馈率、本周新增 PO
- [ ] 4.2 `app/tools/registry.py` 暴露给 LLM 的工具列表
- [ ] 4.3 `app/api/chat.py`：NL → 工具调用 → 结构化回答
- [ ] 4.4 NL → SQL 安全层（只读、表/列白名单、LIMIT 强制）
- [ ] 4.5 NL → dashboard 配置 JSON（图表模板，不让 LLM 写代码）

### 阶段 5 — 打磨
- [ ] 5.1 自动 focus 规则（逾期 N 天 / 多次催未回）
- [ ] 5.2 操作日志页（基于 `field_updates` 渲染）
- [ ] 5.3 升级清单页（escalation_flag = 1）
- [ ] 5.4 备份与还原（`chasebase.db` 一键导出）

---

## 12. 配置项（.env）

```env
# LLM
ANTHROPIC_API_KEY=
LLM_MODEL=claude-sonnet-4-6
LLM_PROVIDER=anthropic        # 预留 openai 等

# Outlook
OUTLOOK_PROFILE=               # 留空使用默认 profile
INBOX_FOLDER=Inbox
INBOUND_PULL_DAYS=14           # 拉最近 N 天的邮件

# 路径
DATA_DIR=./data
EXCEL_DEFAULT_DIR=

# 行为
CHASE_DEFAULT_MODE=draft       # draft / send
SEND_INTERVAL_SECONDS=2
TIMEZONE=Asia/Shanghai

# 服务
HOST=127.0.0.1
PORT=8000
```

---

## 13. 启动方式

```
1. 双击 run.bat
   ↓
   venv 激活 → uvicorn 启动 → 浏览器自动打开 http://127.0.0.1:8000
2. 首次：在设置页填 ANTHROPIC_API_KEY、确认 Outlook 已登录
3. 导入第一份 Excel → 进入主表视图
```

`run.bat` 的内容大致：

```bat
@echo off
cd /d "%~dp0"
if not exist .venv ( python -m venv .venv )
call .venv\Scripts\activate
pip install -r requirements.txt
start "" http://127.0.0.1:8000
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

---

## 14. 后续扩展（**不在 MVP 范围**）

- 多人协作 / 共享数据库
- 网页推送 / 企业微信通知
- 与 ERP / SAP 直连
- 自动催货策略（按规则定时催）
- 移动端

---

## 15. 已确认的关键决策记录

| # | 决策 | 选择 |
|---|---|---|
| 1 | 前端形态 | 本地 FastAPI + 浏览器 |
| 2 | LLM 接入 | API（默认 Anthropic SDK，封装可替换） |
| 3 | MVP 范围 | 阶段 1 + 阶段 2 + 阶段 3 一起做 |
| 4 | Outlook 账号 | 项目主采购个人邮箱 |
| 5 | 部署 | 纯本机单机 |
| 6 | 包管理 | pip + venv |
| 7 | Excel 列名 | 由用户编辑 `config/excel_column_mapping.yaml` |

---

## 下一步

按 **阶段 0 → 阶段 1 → 阶段 2 → 阶段 3** 顺序落地，每完成一个阶段保证可运行 + 可手测。
