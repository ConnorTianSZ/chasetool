"""Database connection - multi-project (each project has its own SQLite file)"""
import sqlite3
import json
import os
import re
from pathlib import Path

_ROOT_DATA_DIR: Path | None = None
_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_root_data_dir() -> Path:
    global _ROOT_DATA_DIR
    if _ROOT_DATA_DIR is None:
        d = Path(os.getenv("DATA_DIR", "./data"))
        d.mkdir(parents=True, exist_ok=True)
        _ROOT_DATA_DIR = d
    return _ROOT_DATA_DIR


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w\-.]", "_", name)


def get_db_path(project_id: str) -> Path:
    project_dir = get_root_data_dir() / _safe_name(project_id)
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir / "chasebase.db"


def _migrate_conn(conn: sqlite3.Connection) -> None:
    """Run schema migrations on an existing database connection."""
    _execute_schema(conn)
    for stmt in _MIGRATION_STMTS:
        try:
            conn.execute(stmt)
        except Exception:
            pass
    try:
        conn.execute(_PROJECT_SETTINGS_SQL)
    except Exception:
        pass
    try:
        conn.execute(_TIME_NODES_SQL)
    except Exception:
        pass


def _configure_journal_mode(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("PRAGMA journal_mode=MEMORY").fetchone()
    except Exception:
        pass


def _execute_schema(conn: sqlite3.Connection) -> None:
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    for statement in sql.split(";"):
        stmt = statement.strip()
        if stmt:
            try:
                conn.execute(stmt)
            except Exception:
                pass


def get_connection(project_id: str = "default") -> sqlite3.Connection:
    conn = sqlite3.connect(str(get_db_path(project_id)), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _configure_journal_mode(conn)
    conn.execute("PRAGMA foreign_keys=ON")
    _migrate_conn(conn)
    return conn


_MIGRATION_STMTS = [
    "ALTER TABLE materials ADD COLUMN plant TEXT",
    "ALTER TABLE materials ADD COLUMN supplier_code TEXT",
    "ALTER TABLE materials ADD COLUMN statical_delivery_date DATE",
    "ALTER TABLE materials ADD COLUMN manufacturer TEXT",
    "ALTER TABLE materials ADD COLUMN manufacturer_part_no TEXT",
    "ALTER TABLE materials ADD COLUMN open_quantity_gr REAL",
    "ALTER TABLE materials ADD COLUMN net_order_price REAL",
    "ALTER TABLE materials ADD COLUMN currency TEXT",
    "ALTER TABLE materials ADD COLUMN net_order_value REAL",
    "ALTER TABLE materials ADD COLUMN position_text1 TEXT",
    "ALTER TABLE materials ADD COLUMN position_text2 TEXT",
    "ALTER TABLE materials ADD COLUMN last_feedback_chase_count INTEGER",
    # Phase 1b: marker_tag 用于新格式 marker → chase_log 归属查询
    "ALTER TABLE chase_log ADD COLUMN marker_tag TEXT",
    # Phase 3: inbound_emails 关联 chase_log
    "ALTER TABLE inbound_emails ADD COLUMN chase_log_id INTEGER",
    # Phase 4: 采购员加急后手工记录的最新交期（不被 Excel 导入覆盖）
    "ALTER TABLE materials ADD COLUMN urgent_feedback_eta DATE",
    "ALTER TABLE materials ADD COLUMN urgent_feedback_note TEXT",
]

_PROJECT_SETTINGS_SQL = """
CREATE TABLE IF NOT EXISTS project_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

_TIME_NODES_SQL = """
CREATE TABLE IF NOT EXISTS time_nodes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    label       TEXT NOT NULL,
    node_date   DATE NOT NULL,
    color       TEXT DEFAULT '#2563eb',
    sort_order  INTEGER DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


def init_db(project_id: str = "default") -> None:
    conn = sqlite3.connect(str(get_db_path(project_id)), check_same_thread=False)
    try:
        conn.row_factory = sqlite3.Row
        _configure_journal_mode(conn)
        conn.execute("PRAGMA foreign_keys=ON")
        _execute_schema(conn)
        # Migration: add columns for existing databases (safe to run repeatedly)
        for stmt in _MIGRATION_STMTS:
            try:
                conn.execute(stmt)
            except Exception:
                pass
        # Migration: ensure project settings table exists
        try:
            conn.execute(_PROJECT_SETTINGS_SQL)
        except Exception:
            pass
        # Migration: ensure time_nodes table exists
        try:
            conn.execute(_TIME_NODES_SQL)
        except Exception:
            pass
        conn.commit()
    finally:
        conn.close()


def _projects_path() -> Path:
    return get_root_data_dir() / "projects.json"


def list_projects() -> list[dict]:
    p = _projects_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_project(project_id: str, meta: dict) -> dict:
    projects = list_projects()
    existing = next((p for p in projects if p["id"] == project_id), None)
    if existing:
        existing.update(meta)
    else:
        projects.append({"id": project_id, **meta})
    _projects_path().write_text(
        json.dumps(projects, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return next(p for p in projects if p["id"] == project_id)


def delete_project(project_id: str) -> bool:
    projects = list_projects()
    before = len(projects)
    projects = [p for p in projects if p["id"] != project_id]
    if len(projects) == before:
        return False
    _projects_path().write_text(
        json.dumps(projects, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return True
