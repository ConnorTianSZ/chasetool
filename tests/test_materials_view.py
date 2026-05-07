import os
import sqlite3
import unittest
from pathlib import Path

from app.api import materials as materials_api
from app.db import connection
from app.services.material_view import (
    clean_date_value,
    derive_chase_status,
    derive_material_state,
    enrich_material_row,
    format_display_date,
)


class MaterialViewHelpersTest(unittest.TestCase):
    def test_clean_date_value_strips_midnight_time(self):
        self.assertEqual(clean_date_value("2026-05-07 00:00:00"), "2026-05-07")
        self.assertEqual(clean_date_value("2026/05/07 00:00:00"), "2026-05-07")
        self.assertIsNone(clean_date_value(""))

    def test_format_display_date_uses_slashes_without_time(self):
        self.assertEqual(format_display_date("2026-05-07 00:00:00"), "2026/05/07")
        self.assertEqual(format_display_date("2026-05-07"), "2026/05/07")
        self.assertEqual(format_display_date(None), "")

    def test_material_state_uses_key_date_and_open_quantity(self):
        self.assertEqual(
            derive_material_state({"open_quantity_gr": 0, "current_eta": "2026-01-01"}, "2026-05-07")["code"],
            "delivered",
        )
        self.assertEqual(
            derive_material_state({"open_quantity_gr": 5, "current_eta": "2026-05-08"}, "2026-05-07")["code"],
            "normal",
        )
        self.assertEqual(
            derive_material_state({"open_quantity_gr": 5, "current_eta": "2026-05-06"}, "2026-05-07")["code"],
            "overdue",
        )
        self.assertEqual(
            derive_material_state({"open_quantity_gr": 5, "current_eta": None}, "2026-05-07")["code"],
            "no_oc",
        )

    def test_chase_status_labels_count_and_feedback_dates(self):
        self.assertEqual(
            derive_chase_status({"chase_count": 0})["label"],
            "未催",
        )
        self.assertEqual(
            derive_chase_status({"chase_count": 2, "last_chased_at": "2026-05-07T01:02:03"})["label"],
            "已第 2 次催于 05/07 未反馈",
        )
        self.assertEqual(
            derive_chase_status({
                "chase_count": 3,
                "last_feedback_chase_count": 2,
                "supplier_feedback_time": "2026-05-08 00:00:00",
            })["label"],
            "已于 05/08 第 2 次反馈",
        )

    def test_enrich_material_row_adds_fallback_buyer_display_and_state(self):
        row = {
            "id": 1,
            "purchasing_group": "MFW",
            "buyer_name": "",
            "buyer_email": "",
            "open_quantity_gr": "1",
            "order_date": "2026-05-01 00:00:00",
            "current_eta": "2026-05-08 00:00:00",
            "chase_count": 0,
        }
        enriched = enrich_material_row(
            row,
            key_date="2026-05-07",
            pgr_map={"MFW": {"name": "Tian Connor", "email": "Connor.TIAN@cn.bosch.com"}},
        )
        self.assertEqual(enriched["buyer_name"], "Tian Connor")
        self.assertEqual(enriched["buyer_email"], "Connor.TIAN@cn.bosch.com")
        self.assertEqual(enriched["material_state"], "normal")
        self.assertEqual(enriched["display_order_date"], "2026/05/01")
        self.assertEqual(enriched["display_current_eta"], "2026/05/08")


class MaterialsApiTest(unittest.TestCase):
    def setUp(self):
        self.project_id = "unit"
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        schema = Path("app/db/schema.sql").read_text(encoding="utf-8")
        for statement in schema.split(";"):
            stmt = statement.strip()
            if stmt:
                self.conn.execute(stmt)
        for statement in connection._MIGRATION_STMTS:
            try:
                self.conn.execute(statement)
            except Exception:
                pass
        self.conn.commit()

        class NoCloseConnection:
            def __init__(self, conn):
                self.conn = conn

            def __getattr__(self, name):
                return getattr(self.conn, name)

            def close(self):
                pass

        self._old_get_connection = materials_api.get_connection
        materials_api.get_connection = lambda project_id: NoCloseConnection(self.conn)

    def tearDown(self):
        materials_api.get_connection = self._old_get_connection
        self.conn.close()

    def _insert_material(self, **fields):
        defaults = {
            "po_number": "PO1",
            "item_no": "10",
            "purchasing_group": "MFW",
            "buyer_name": "Tian Connor",
            "buyer_email": "Connor.TIAN@cn.bosch.com",
            "current_eta": "2026-05-08",
            "open_quantity_gr": 1,
            "status": "open",
        }
        defaults.update(fields)
        cols = list(defaults.keys())
        self.conn.execute(
            f"INSERT INTO materials ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})",
            [defaults[c] for c in cols],
        )
        self.conn.commit()

    def test_list_materials_sorts_delivered_last_and_returns_derived_state(self):
        self._insert_material(po_number="PO-DONE", item_no="10", current_eta="2026-04-01", open_quantity_gr=0)
        self._insert_material(po_number="PO-LATE", item_no="10", current_eta="2026-05-06", open_quantity_gr=5)
        self._insert_material(po_number="PO-NORMAL", item_no="10", current_eta="2026-05-08", open_quantity_gr=5)

        result = materials_api.list_materials(
            project_id=self.project_id,
            po_number=None,
            buyer_email=None,
            buyer_key=None,
            supplier=None,
            status=None,
            material_state=None,
            station_no=None,
            purchasing_group=None,
            is_focus=None,
            overdue=False,
            no_eta=False,
            search=None,
            key_date="2026-05-07",
            page=1,
            page_size=50,
        )

        self.assertEqual([r["po_number"] for r in result["items"]], ["PO-LATE", "PO-NORMAL", "PO-DONE"])
        self.assertEqual([r["material_state"] for r in result["items"]], ["overdue", "normal", "delivered"])

    def test_filter_options_include_buyer_choices(self):
        self._insert_material(buyer_name="Tian Connor", buyer_email="Connor.TIAN@cn.bosch.com")
        options = materials_api.filter_options(project_id=self.project_id)
        self.assertIn(
            {"key": "email:connor.tian@cn.bosch.com", "name": "Tian Connor", "email": "Connor.TIAN@cn.bosch.com"},
            options["buyers"],
        )
