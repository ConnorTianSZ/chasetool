# Material State Overdue 拆分重构

> 变更日期：2026-05-08  
> 涉及模块：`material_view` / `chase_email` / `outlook_send` / `materials API` / 前端 / 测试

---

## 背景与问题

原 `derive_material_state()` 只用 `key_date` 做单一阈值，将两种截然不同的逾期情况压入同一个 `"overdue"` code，导致：

| 情况 | 实际含义 | 原 code | 原症状 |
|------|---------|---------|--------|
| `current_eta < today` | 供应商自己承诺的日期已过（**应交未交**） | `overdue` | 邮件语气不区分 |
| `today ≤ current_eta < key_date` | 将来才交但**晚于项目节点** | `overdue` | 催货诉求不对 |

---

## 新状态码总览

| code | label | badge CSS | 触发条件 | 催货类型 |
|------|-------|-----------|---------|---------|
| `delivered` | 已交货 | `badge-delivered` | `open_quantity_gr == 0` | — |
| `no_oc` | 无OC | `badge-no-eta` | `current_eta` 为空 | `oc_confirmation` |
| `overdue_now` | 应交未交 | `badge-overdue-now` | `current_eta < today` | `urgent_now` |
| `overdue_keydate` | 晚于节点 | `badge-overdue-keydate` | `today ≤ current_eta < key_date` | `urgent_keydate` |
| `normal` | 正常 | `badge-open` | `current_eta ≥ key_date` | — |

---

## 关键词索引

以下关键词在代码库中的含义均随本次重构发生变化，搜索时需同步关注。

### 状态码（material_state code）

| 关键词 | 出现文件 | 变更说明 |
|--------|---------|---------|
| `overdue` | `material_view.py`, `materials.py`, `app.js`, `index.html`, `chase_email.py` | **废弃**，拆分为 `overdue_now` / `overdue_keydate`；CSS class 保留作兼容旧数据 |
| `overdue_now` | `material_view.py`, `materials.py`, `chase_email.py`, `app.js`, `index.html`, `style.css`, `test_materials_view.py` | **新增**：OC 早于今日（应交未交） |
| `overdue_keydate` | `material_view.py`, `materials.py`, `chase_email.py`, `app.js`, `index.html`, `style.css`, `test_materials_view.py` | **新增**：OC 在今日之后但晚于 KEYDATE |
| `delivered` | 全局 | 不变 |
| `no_oc` | 全局 | 不变 |
| `normal` | 全局 | 不变 |

### 催货类型（chase_type）

| 关键词 | 出现文件 | 变更说明 |
|--------|---------|---------|
| `urgent` | `chase_email.py`, `outlook_send.py`, `config/chase_email_templates/urgent.txt` | **废弃**（模板文件保留作历史备份），拆分为下两项 |
| `urgent_now` | `chase_email.py`, `outlook_send.py`, `config/chase_email_templates/urgent_now.txt` | **新增**：应交未交催货，措辞为"已超过承诺交期，请立即确认" |
| `urgent_keydate` | `chase_email.py`, `outlook_send.py`, `config/chase_email_templates/urgent_keydate.txt` | **新增**：晚于节点催货，措辞为"请确认能否提前至 {key_date} 前交货" |
| `oc_confirmation` | 全局 | 不变 |

### Badge CSS 类名

| 关键词 | 出现文件 | 变更说明 |
|--------|---------|---------|
| `badge-overdue` | `style.css`, `index.html` | 保留，兼容旧数据；新数据不再生成此 class |
| `badge-overdue-now` | `style.css`, `index.html` | **新增**，深红 `#991b1b`，对应 `overdue_now` |
| `badge-overdue-keydate` | `style.css`, `index.html` | **新增**，橙色 `#c2410c`，对应 `overdue_keydate` |

### Marker / Email Subject

| 关键词 | 出现文件 | 变更说明 |
|--------|---------|---------|
| `URG`（marker purpose） | `chase_email.py`, `email_marker.py` | 两类 urgent 共用，不变 |
| `Urgent Delivery` | `outlook_send.py` | `urgent_now` 邮件主题，不变 |
| `Delivery Expedite` | `outlook_send.py` | **新增**，`urgent_keydate` 邮件主题 |

### API / Filter 参数

| 关键词 | 出现文件 | 变更说明 |
|--------|---------|---------|
| `material_state` query param | `materials.py` | 新增合法值 `overdue_now` / `overdue_keydate`；`overdue` 不再被 `_add_material_state_filter` 处理（前端已不传） |
| `overdue` bool query param | `materials.py` | 保持：快捷筛选，覆盖 OC < KEYDATE 的全部行（含 `overdue_now` + `overdue_keydate`） |
| `_add_material_state_filter()` | `materials.py` | 新增 `overdue_now`（`< today`）和 `overdue_keydate`（`today ≤ eta < key_date`）两分支 |

### 前端方法

| 关键词 | 出现文件 | 变更说明 |
|--------|---------|---------|
| `isOverdue(item)` | `app.js` | 现在返回 `true` 当 state 为 `overdue_now`、`overdue_keydate` 或旧的 `overdue` |
| `isOverdueNow(item)` | `app.js` | **新增**，精确匹配 `overdue_now` |
| `isOverdueKeydate(item)` | `app.js` | **新增**，精确匹配 `overdue_keydate` |
| `noEta(item)` | `app.js` | 不变 |
| `filters.material_state` | `app.js`, `index.html` | 下拉选项新增 `overdue_now` / `overdue_keydate`，移除旧的 `overdue` 选项 |

---

## 变更文件清单

```
app/services/material_view.py          # derive_material_state() 拆分逻辑
app/tools/chase_email.py               # _STATE_TO_CHASE_TYPE + _CHASE_TYPE_TO_PURPOSE
app/services/outlook_send.py           # build_chase_subject() + build_email_body() eta_field
app/api/materials.py                   # _add_material_state_filter() 新增两分支
config/chase_email_templates/
  urgent_now.txt                       # 新增：应交未交模板
  urgent_keydate.txt                   # 新增：晚于节点模板
  urgent.txt                           # 保留作历史备份（不再被自动调用）
web/static/style.css                   # 新增 badge-overdue-now / badge-overdue-keydate
web/index.html                         # 筛选下拉 + 行内 badge :class 映射
web/static/app.js                      # isOverdue() / isOverdueNow() / isOverdueKeydate()
tests/test_materials_view.py           # 拆分测试用例，新增 overdue_now / overdue_keydate 覆盖
```

---

## Dashboard 注意事项

`app/api/dashboard.py` 的 `overdue_count` 和 `overdue_by_supplier` 当前使用 `< date('now')`（今日口径），即仅统计 `overdue_now` 类型。如需同时展示"晚于节点"数量，可后续拆分为：

- `overdue_now_count`：`current_eta < date('now')`
- `overdue_keydate_count`：`date('now') ≤ current_eta < date(key_date)`

当前版本未修改 dashboard，保持向后兼容。

---

## 迁移说明

- 存量数据库中已存储的 `material_state = 'overdue'` 值在展示时会 fallback 到旧的 `badge-overdue` CSS（深红，兼容），不影响视觉。
- 重新加载物料列表时，`enrich_material_row()` 会实时重算 `material_state`，旧值不持久化到 DB（DB 无 `material_state` 字段，均为运行时推导）。
- `urgent.txt` 邮件模板保留，`load_template()` 逻辑会在找不到对应文件时 fallback 到 `default.txt`，新 `urgent_now` / `urgent_keydate` 均有专属文件。
