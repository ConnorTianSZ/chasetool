import sqlite3
import unittest
import os
import shutil
import uuid
from pathlib import Path

from app.db import connection


class JournalModeFallbackTest(unittest.TestCase):
    def test_uses_memory_journal_for_local_single_user_database(self):
        class FakeConnection:
            def __init__(self):
                self.calls = []

            def execute(self, sql):
                self.calls.append(sql)

        fake = FakeConnection()
        connection._configure_journal_mode(fake)

        self.assertEqual(
            fake.calls,
            ["PRAGMA journal_mode=MEMORY"],
        )


class ProjectSchemaInitializationTest(unittest.TestCase):
    def setUp(self):
        self._old_data_dir = os.environ.get("DATA_DIR")
        self.tempdir = str(Path.cwd() / ".test-db" / uuid.uuid4().hex)
        os.makedirs(self.tempdir, exist_ok=True)
        os.environ["DATA_DIR"] = self.tempdir
        connection._ROOT_DATA_DIR = None

    def tearDown(self):
        if self._old_data_dir is None:
            os.environ.pop("DATA_DIR", None)
        else:
            os.environ["DATA_DIR"] = self._old_data_dir
        connection._ROOT_DATA_DIR = None
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_get_connection_initializes_schema_for_new_project_database(self):
        conn = connection.get_connection("new-project")
        try:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        finally:
            conn.close()

        self.assertIn("materials", tables)
        self.assertIn("project_settings", tables)
