"""
回邮拉取 (查找回邮) 故障诊断脚本
在公司电脑上完整运行，将全部输出截图/复制发给我。
"""
import sys, json, traceback
from datetime import datetime, timedelta

SEP = "=" * 60

def log(label: str):
    print(f"\n{SEP}\n【{label}】\n{SEP}")

# ── 1. 基础环境 ──────────────────────────────────────────────────
log("1. 基础环境")
print(f"Python: {sys.version}")
print(f"Platform: {sys.platform}")

# ── 2. 连接 Outlook ──────────────────────────────────────────────
log("2. 连接 Outlook")
try:
    import win32com.client
    print("win32com 导入成功")
    outlook = win32com.client.Dispatch("Outlook.Application")
    print("Outlook.Application Dispatch 成功")

    namespace = outlook.GetNamespace("MAPI")
    print("GetNamespace('MAPI') 成功")

    inbox = namespace.GetDefaultFolder(6)  # olFolderInbox = 6
    print(f"GetDefaultFolder(6) 成功, 收件箱: {inbox.Name}")

    messages = inbox.Items
    messages.Sort("[ReceivedTime]", True)
    print(f"Items.Sort 成功, 总邮件数: {messages.Count}")

except Exception as e:
    print(f"❌ Outlook 连接失败: {type(e).__name__}: {e}")
    traceback.print_exc()
    print("无法继续，后续诊断无法执行")
    sys.exit(1)

# ── 3. 遍历前 5 封邮件（逐字段测试） ──────────────────────────
log("3. 遍历前 5 封邮件 - 逐字段诊断")

# 检查最后一封邮件的 ReceivedTime 时区问题
print("\n>>> 先看最后一封邮件的 ReceivedTime:")
try:
    last = messages.GetLast()
    if last:
        rt = last.ReceivedTime
        print(f"  ReceivedTime 值: {rt!r}")
        print(f"  类型: {type(rt).__name__}")
        print(f"  isinstance(datetime): {isinstance(rt, datetime)}")
        if isinstance(rt, datetime):
            print(f"  tzinfo: {rt.tzinfo!r}")
        # 测试比较
        since = datetime.now() - timedelta(days=14)
        print(f"  since (naive): {since!r}")
        try:
            result = rt < since
            print(f"  比较 rt < since: {result} ✅ (无异常)")
        except TypeError as te:
            print(f"  比较 rt < since: ❌ TypeError - {te}")
        except Exception as ce:
            print(f"  比较 rt < since: ❌ {type(ce).__name__} - {ce}")
    else:
        print("  收件箱为空")
except Exception as e:
    print(f"  获取最后邮件失败: {type(e).__name__}: {e}")

print("\n>>> 逐封测试字段读取:")
tested = 0
for msg in messages:
    tested += 1
    if tested > 5:
        break
    print(f"\n--- 邮件 #{tested} ---")
    try:
        entry_id = msg.EntryID
        print(f"  EntryID: {'✓ (取到)' if entry_id else '空'}")
    except Exception as e:
        print(f"  EntryID: ❌ {type(e).__name__}: {e}")
        continue  # EntryID 保底都不行就跳过

    try:
        subject = str(msg.Subject or "")
        print(f"  Subject: {subject[:80]}")
    except Exception as e:
        print(f"  Subject: ❌ {type(e).__name__}: {e}")
        subject = ""

    try:
        body_preview = str((msg.Body or "")[:100])
        print(f"  Body (前100字符): {body_preview[:80]}")
    except Exception as e:
        print(f"  Body: ❌ {type(e).__name__}: {e}")

    try:
        sender = str(msg.SenderEmailAddress or "")
        print(f"  Sender: {sender[:60]}")
    except Exception as e:
        print(f"  Sender: ❌ {type(e).__name__}: {e}")

    try:
        rt = msg.ReceivedTime
        print(f"  ReceivedTime: {rt!r}")
        if isinstance(rt, datetime):
            print(f"    tzinfo={rt.tzinfo!r}")
    except Exception as e:
        print(f"  ReceivedTime: ❌ {type(e).__name__}: {e}")
        continue

    # 模拟原有代码的日期比较
    try:
        recv_dt = rt if isinstance(rt, datetime) else datetime.fromtimestamp(float(rt))
        since2 = datetime.now() - timedelta(days=14)
        if recv_dt < since2:
            print(f"  日期判断: 早于14天前 → 会 break 跳出")
        else:
            print(f"  日期判断: 14天内 ✅")
    except TypeError as te:
        print(f"  日期判断: ❌ TypeError (aware vs naive) - {te}")
    except Exception as e:
        print(f"  日期判断: ❌ {type(e).__name__} - {e}")

# ── 4. 查找含 Marker 的邮件 ──────────────────────────────────
log("4. Marker 匹配测试")

from app.services.email_marker import parse_marker, ChaseMarker, LegacyChaseMarker

total_checked = 0
v2_found = 0
v1_found = 0
marker_errors = 0

for msg in messages:
    total_checked += 1
    if total_checked > 50:  # 扫前 50 封
        break
    try:
        subject = str(msg.Subject or "")
        marker = parse_marker(subject)
        if marker:
            print(f"\n  找到 Marker: subject={subject[:80]}")
            print(f"    marker={marker!r}")
            print(f"    type={type(marker).__name__}")
            try:
                tag = marker.to_subject_tag()
                print(f"    to_subject_tag()={tag}")
            except Exception as e:
                print(f"    to_subject_tag() ❌ {type(e).__name__}: {e}")

            if isinstance(marker, ChaseMarker):
                v2_found += 1
            elif isinstance(marker, LegacyChaseMarker):
                v1_found += 1
    except Exception as e:
        marker_errors += 1
        print(f"  Marker解析异常: {type(e).__name__}: {e}")

print(f"\n总扫描: {total_checked} 封")
print(f"v2 marker: {v2_found}")
print(f"v1 marker: {v1_found}")
if marker_errors:
    print(f"解析异常: {marker_errors}")

# ── 5. 完整模拟 pull_inbox ──────────────────────────────────
log("5. 完整模拟 pull_inbox() 流程")

from app.db.connection import get_connection

try:
    conn = get_connection("TEST")
    print("TEST 项目数据库连接成功")

    # 检查 chase_log 表结构
    cols = conn.execute("PRAGMA table_info(chase_log)").fetchall()
    print(f"chase_log 表列: {[c[1] for c in cols]}")

    has_marker_tag = any(c[1] == "marker_tag" for c in cols)
    print(f"  含 marker_tag 列: {has_marker_tag}")

    # 检查 chase_log 记录
    log_count = conn.execute("SELECT COUNT(*) FROM chase_log").fetchone()[0]
    print(f"chase_log 记录数: {log_count}")

    if log_count > 0:
        for row in conn.execute(
            "SELECT id, marker_tag, material_ids_json FROM chase_log ORDER BY sent_at DESC LIMIT 5"
        ).fetchall():
            print(f"  log id={row['id']}, marker_tag={row['marker_tag']!r}, material_ids_json={row['material_ids_json']!r}")

    # 检查 inbound_emails 表结构
    ie_cols = conn.execute("PRAGMA table_info(inbound_emails)").fetchall()
    print(f"\ninbound_emails 表列: {[c[1] for c in ie_cols]}")
    ie_count = conn.execute("SELECT COUNT(*) FROM inbound_emails").fetchone()[0]
    print(f"inbound_emails 记录数: {ie_count}")

    conn.close()
except Exception as e:
    print(f"❌ 数据库错误: {type(e).__name__}: {e}")
    traceback.print_exc()

# ── 6. 模拟 v2 marker 查询 chase_log ──────────────────────────
log("6. 模拟 v2 ChaseMarker → chase_log 匹配查询")

for msg in messages:
    try:
        subject = str(msg.Subject or "")
        marker = parse_marker(subject)
        if isinstance(marker, ChaseMarker):
            tag = marker.to_subject_tag()
            conn2 = get_connection("TEST")
            try:
                row = conn2.execute(
                    "SELECT material_ids_json FROM chase_log "
                    "WHERE marker_tag=? ORDER BY sent_at DESC LIMIT 1",
                    (tag,),
                ).fetchone()
                if row:
                    ids = json.loads(row[0])
                    print(f"  ✓ 匹配到 chase_log: marker_tag={tag}, material_ids={ids}")
                else:
                    print(f"  ✗ 未匹配: marker_tag={tag} (chase_log 中无此 tag)")
            finally:
                conn2.close()
            break  # 只测第一个 v2 marker
    except Exception as e:
        print(f"  ❌ 查询异常: {type(e).__name__}: {e}")

# ── 7. 概要 ──────────────────────────────────────────────────
log("7. 诊断概要")
print("如果以上步骤没有报错，问题可能在服务器运行时环境差异。")
print("请把全部输出截图或复制发给我。")
