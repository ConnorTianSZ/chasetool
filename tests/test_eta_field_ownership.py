import sqlite3
import unittest
from pathlib import Path

from app.db import connection
from app.tools import parse_inbound
from app.tools import update_material


def _init_memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema = Path("app/db/schema.sql").read_text(encoding="utf-8")
    for statement in schema.split(";"):
        stmt = statement.strip()
        if stmt:
            conn.execute(stmt)
    for statement in connection._MIGRATION_STMTS:
        try:
            conn.execute(statement)
        except Exception:
            pass
    conn.commit()
    return conn


class NoCloseConnection:
    def __init__(self, conn):
        self.conn = conn

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def close(self):
        pass


class EtaFieldOwnershipTest(unittest.TestCase):
    def setUp(self):
        self.conn = _init_memory_db()
        self.conn.execute(
            """INSERT INTO materials
               (po_number, item_no, current_eta, supplier_eta, chase_count)
               VALUES ('PO1', '10', '2026-06-10', NULL, 2)"""
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_email_reply_updates_supplier_eta_without_changing_current_eta(self):
        result = parse_inbound._apply_single_item(
            self.conn,
            {
                "po_number": "PO1",
                "item_no": "10",
                "new_eta": "2026-05-20",
                "remarks": "供应商确认",
            },
            source_ref="mail-1",
            now_iso="2026-05-09T10:00:00",
        )

        row = self.conn.execute(
            "SELECT current_eta, supplier_eta, supplier_remarks FROM materials WHERE po_number='PO1'"
        ).fetchone()
        self.assertEqual(result["status"], "applied")
        self.assertEqual(row["current_eta"], "2026-06-10")
        self.assertEqual(row["supplier_eta"], "2026-05-20")
        self.assertEqual(row["supplier_remarks"], "供应商确认")

    def test_chat_tool_blocks_current_eta_and_allows_supplier_eta(self):
        old_get_connection = update_material.get_connection
        update_material.get_connection = lambda project_id: NoCloseConnection(self.conn)
        try:
            blocked = update_material.update_material_field(
                "PO1", "10", "current_eta", "2026-05-20", project_id="unit"
            )
            allowed = update_material.update_material_field(
                "PO1", "10", "supplier_eta", "2026-05-21", project_id="unit"
            )
        finally:
            update_material.get_connection = old_get_connection

        row = self.conn.execute(
            "SELECT current_eta, supplier_eta FROM materials WHERE po_number='PO1'"
        ).fetchone()
        self.assertFalse(blocked["ok"])
        self.assertIn("supplier_eta", blocked["reason"])
        self.assertTrue(allowed["ok"])
        self.assertEqual(row["current_eta"], "2026-06-10")
        self.assertEqual(row["supplier_eta"], "2026-05-21")

