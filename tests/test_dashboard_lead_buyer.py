import sqlite3
import unittest
from datetime import date, timedelta
from pathlib import Path

from app.api import dashboard as dashboard_api
from app.db import connection
from app.services import outlook_send


class DashboardLeadBuyerTest(unittest.TestCase):
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

        self._old_get_connection = dashboard_api.get_connection
        self._old_load_pgr_map = dashboard_api.load_pgr_map
        dashboard_api.get_connection = lambda project_id: NoCloseConnection(self.conn)
        dashboard_api.load_pgr_map = lambda: {}

    def tearDown(self):
        dashboard_api.get_connection = self._old_get_connection
        dashboard_api.load_pgr_map = self._old_load_pgr_map
        self.conn.close()

    def _insert_material(self, **fields):
        defaults = {
            "po_number": f"PO-{fields.get('item_no', '10')}",
            "item_no": fields.get("item_no", "10"),
            "buyer_name": "Buyer A",
            "buyer_email": "buyer.a@example.com",
            "supplier": "Supplier A",
            "manufacturer": "Maker A",
            "current_eta": None,
            "open_quantity_gr": 1,
            "status": "open",
            "chase_count": 0,
            "is_focus": 0,
        }
        defaults.update(fields)
        cols = list(defaults.keys())
        self.conn.execute(
            f"INSERT INTO materials ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})",
            [defaults[c] for c in cols],
        )
        self.conn.commit()

    def test_lead_buyer_dashboard_counts_and_top_evidence(self):
        today = date.today()
        key_date = (today + timedelta(days=2)).isoformat()
        self._insert_material(item_no="10", current_eta=None, supplier="Supplier A", manufacturer="Maker A")
        self._insert_material(
            item_no="20",
            current_eta=(today - timedelta(days=1)).isoformat(),
            supplier="Supplier B",
            manufacturer="Maker B",
        )
        self._insert_material(
            item_no="30",
            current_eta=(today + timedelta(days=5)).isoformat(),
            supplier="Supplier B",
            manufacturer="Maker C",
        )
        self._insert_material(
            item_no="40",
            current_eta=(today + timedelta(days=1)).isoformat(),
            supplier="Supplier C",
            manufacturer="Maker C",
            chase_count=2,
            last_chased_at=today.isoformat(),
        )
        self._insert_material(
            item_no="50",
            current_eta=(today - timedelta(days=1)).isoformat(),
            open_quantity_gr=0,
            supplier="Supplier Z",
            manufacturer="Maker Z",
        )

        result = dashboard_api.lead_buyer(
            project_id=self.project_id,
            key_date=key_date,
            evidence_by="supplier",
        )

        cards = {c["id"]: c["value"] for c in result["summary_cards"]}
        self.assertEqual(cards["no_oc"], 1)
        self.assertEqual(cards["overdue_now"], 1)
        self.assertEqual(cards["overdue_keydate"], 1)
        self.assertEqual(cards["chased_no_feedback"], 1)

        buyer = result["buyer_rows"][0]
        self.assertEqual(buyer["buyer_name"], "Buyer A")
        self.assertEqual(buyer["open_count"], 4)
        self.assertEqual(buyer["no_oc_count"], 1)
        self.assertEqual(buyer["overdue_now_count"], 1)
        self.assertEqual(buyer["overdue_keydate_count"], 1)
        self.assertEqual(buyer["chased_no_feedback_count"], 1)
        self.assertEqual(buyer["top_suppliers"][0], {"name": "Supplier B", "count": 2})
        self.assertEqual(buyer["top_manufacturers"][0], {"name": "Maker C", "count": 2})

    def test_late_evidence_can_group_by_supplier_or_manufacturer(self):
        today = date.today()
        key_date = (today + timedelta(days=2)).isoformat()
        self._insert_material(
            item_no="10",
            current_eta=(today + timedelta(days=5)).isoformat(),
            supplier="Supplier A",
            manufacturer="Maker X",
        )
        self._insert_material(
            item_no="20",
            current_eta=(today + timedelta(days=6)).isoformat(),
            supplier="Supplier B",
            manufacturer="Maker X",
        )

        by_supplier = dashboard_api.lead_buyer(
            project_id=self.project_id,
            key_date=key_date,
            evidence_by="supplier",
        )
        by_manufacturer = dashboard_api.lead_buyer(
            project_id=self.project_id,
            key_date=key_date,
            evidence_by="manufacturer",
        )

        self.assertEqual(
            [r["name"] for r in by_supplier["late_evidence"]],
            ["Supplier A", "Supplier B"],
        )
        self.assertEqual(by_manufacturer["late_evidence"], [{"name": "Maker X", "count": 2}])


class OutlookDisplayDraftTest(unittest.TestCase):
    def test_create_display_draft_saves_and_displays_mail(self):
        calls = []

        class FakeMail:
            EntryID = "entry-1"

            def Save(self):
                calls.append("Save")

            def Display(self):
                calls.append("Display")

        class FakeOutlook:
            def CreateItem(self, item_type):
                self.item_type = item_type
                self.mail = FakeMail()
                return self.mail

        fake_outlook = FakeOutlook()
        old_get_outlook = outlook_send._get_outlook
        outlook_send._get_outlook = lambda: fake_outlook
        try:
            result = outlook_send.create_display_draft(
                to_address="lead@example.com",
                cc="cc@example.com",
                subject="Dashboard follow up",
                html_body="<p>Body</p>",
            )
        finally:
            outlook_send._get_outlook = old_get_outlook

        self.assertEqual(calls, ["Save", "Display"])
        self.assertEqual(result["entry_id"], "entry-1")
        self.assertEqual(fake_outlook.mail.To, "lead@example.com")
        self.assertEqual(fake_outlook.mail.CC, "cc@example.com")
        self.assertEqual(fake_outlook.mail.Subject, "Dashboard follow up")
        self.assertEqual(fake_outlook.mail.HTMLBody, "<p>Body</p>")
