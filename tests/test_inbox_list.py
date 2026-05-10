import sqlite3
import unittest
from pathlib import Path

from app.api import inbox as inbox_api
from app.db import connection


class NoCloseConnection:
    def __init__(self, conn):
        self.conn = conn

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def close(self):
        pass


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


class InboxListTest(unittest.TestCase):
    def setUp(self):
        self.conn = _init_memory_db()
        self.conn.execute(
            """INSERT INTO materials
               (id, po_number, item_no, buyer_name, buyer_email, status)
               VALUES (1, 'PO1', '10', 'Buyer A', 'buyer.a@example.com', 'open')"""
        )
        self.conn.execute(
            """INSERT INTO inbound_emails
               (outlook_entry_id, from_address, subject, body, received_at,
                matched_material_id, status)
               VALUES ('entry-1', 'supplier@example.com', 'Re: PO1', 'ok',
                       '2026-05-10T10:00:00', 1, 'new')"""
        )
        self.conn.commit()
        self._old_get_connection = inbox_api.get_connection
        inbox_api.get_connection = lambda project_id: NoCloseConnection(self.conn)

    def tearDown(self):
        inbox_api.get_connection = self._old_get_connection
        self.conn.close()

    def test_list_emails_derives_buyer_display_from_material_columns(self):
        result = inbox_api.list_emails(project_id="unit", status=None, limit=50, offset=0)

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["buyer_display"], "Buyer A")
        self.assertEqual(result["items"][0]["mat_buyer_email"], "buyer.a@example.com")
