import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import openpyxl

from app.db import connection
from app.services import excel_io


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


class ChaseUpdateImportTest(unittest.TestCase):
    def setUp(self):
        self.conn = _init_memory_db()
        self.conn.execute(
            """INSERT INTO materials
               (po_number, item_no, current_eta, supplier_eta, supplier_remarks,
                status, chase_count)
               VALUES ('PO1', '10', '2026-06-10', NULL, NULL, 'open', 1)"""
        )
        self.conn.commit()
        self._old_get_connection = excel_io.get_connection
        excel_io.get_connection = lambda project_id: NoCloseConnection(self.conn)

    def tearDown(self):
        excel_io.get_connection = self._old_get_connection
        self.conn.close()

    def _xlsx_path(self):
        fd, path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        return path

    def test_import_chase_updates_only_updates_existing_feedback_fields(self):
        path = self._xlsx_path()
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["PO Number", "Item No", "Supplier ETA", "Remarks", "Status"])
        ws.append(["PO1", "10", "2026-05-22", "confirmed after chase", "open"])
        wb.save(path)

        result = excel_io.import_chase_updates(path, project_id="unit")

        row = self.conn.execute(
            """SELECT current_eta, supplier_eta, supplier_remarks, status
               FROM materials WHERE po_number='PO1' AND item_no='10'"""
        ).fetchone()
        self.assertEqual(result["rows_updated"], 1)
        self.assertEqual(row["current_eta"], "2026-06-10")
        self.assertEqual(row["supplier_eta"], "2026-05-22")
        self.assertEqual(row["supplier_remarks"], "confirmed after chase")
        self.assertEqual(row["status"], "open")
