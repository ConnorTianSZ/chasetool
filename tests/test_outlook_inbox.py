import sqlite3
import unittest
from pathlib import Path

from app.db import connection
from app.services import outlook_inbox


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


class DisconnectingMessages:
    Count = 1

    def Sort(self, *_args):
        pass

    def __iter__(self):
        raise RuntimeError("object is not connected to the server none,none")

    def Item(self, _index):
        raise RuntimeError("object is not connected to the server none,none")


class FakeInbox:
    @property
    def Items(self):
        return DisconnectingMessages()


class FakeNamespace:
    def GetDefaultFolder(self, _folder_id):
        return FakeInbox()


class FakeOutlook:
    def GetNamespace(self, _name):
        return FakeNamespace()


class EmptyMessages:
    Count = 0

    def Sort(self, *_args):
        pass


class EmptyInbox:
    @property
    def Items(self):
        return EmptyMessages()


class EmptyNamespace:
    def GetDefaultFolder(self, _folder_id):
        return EmptyInbox()


class GoodOutlook:
    def GetNamespace(self, _name):
        return EmptyNamespace()


class StaleOutlook:
    def GetNamespace(self, _name):
        raise RuntimeError("object is not connected to the server none,none")


class OutlookInboxTest(unittest.TestCase):
    def setUp(self):
        self.conn = _init_memory_db()
        self._old_get_connection = outlook_inbox.get_connection
        self._old_get_outlook = outlook_inbox._get_outlook
        self._old_logger_disabled = outlook_inbox.logger.disabled
        outlook_inbox.get_connection = lambda project_id: NoCloseConnection(self.conn)
        outlook_inbox.logger.disabled = True

    def tearDown(self):
        outlook_inbox.get_connection = self._old_get_connection
        outlook_inbox._get_outlook = self._old_get_outlook
        outlook_inbox.logger.disabled = self._old_logger_disabled
        self.conn.close()

    def test_disconnect_during_message_enumeration_is_counted_not_raised(self):
        outlook_inbox._get_outlook = lambda: FakeOutlook()

        result = outlook_inbox.pull_inbox(days=14, project_id="unit")

        self.assertEqual(result["pulled"], 0)
        self.assertEqual(result["skipped_error"], 1)

    def test_stale_cached_outlook_object_is_retried_once(self):
        outlooks = [StaleOutlook(), GoodOutlook()]
        outlook_inbox._get_outlook = lambda: outlooks.pop(0)

        result = outlook_inbox.pull_inbox(days=14, project_id="unit")

        self.assertEqual(result["pulled"], 0)
        self.assertEqual(result["skipped_error"], 0)
        self.assertEqual(len(outlooks), 0)
