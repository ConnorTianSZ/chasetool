"""Excel import / export service
Supports: WBS parsing, PGR mapping, quantity=0 skip, case-insensitive extensions
"""
from __future__ import annotations
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any
import unicodedata

import openpyxl
import pandas as pd
import yaml

from app.db.connection import get_connection
from app.update_policy import bulk_update_fields, FACT_FIELDS
from app.services.material_view import clean_date_value, load_pgr_map

_MAPPING_PATH = Path(__file__).parent.parent.parent / "config" / "excel_column_mapping.yaml"
_DATE_FIELDS = {
    "order_date",
    "original_eta",
    "current_eta",
    "supplier_eta",
    "statical_delivery_date",
}


def _load_mapping() -> dict[str, list[str]]:
    with open(_MAPPING_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


_WBS_RE = re.compile(r"^([A-Za-z]\.\d+)\.(\d+)", re.IGNORECASE)


def parse_wbs(wbs: str) -> tuple[str | None, str | None]:
    if not wbs:
        return None, None
    m = _WBS_RE.match(wbs.strip())
    if m:
        return m.group(1), m.group(2)
    return None, None


def _normalize(text: str) -> str:
    text = str(text).strip()
    text = unicodedata.normalize("NFKC", text)
    return text.lower()


def _build_header_map(headers: list[str], mapping: dict[str, list[str]]) -> dict[str, str]:
    alias_index: dict[str, str] = {}
    for field, aliases in mapping.items():
        for alias in aliases:
            alias_index[_normalize(alias)] = field
    result: dict[str, str] = {}
    for h in headers:
        norm = _normalize(h)
        if norm in alias_index:
            result[h] = alias_index[norm]
    return result


def _file_hash(path: Path) -> str:
    h = hashlib.md5()
    h.update(path.read_bytes())
    return h.hexdigest()


def _is_excel(filename: str) -> bool:
    return filename.lower().endswith((".xlsx", ".xls"))


def import_excel(file_path: str | Path, project_id: str = "default") -> dict:
    path = Path(file_path)
    if not _is_excel(path.name):
        raise ValueError("Only .xlsx / .xls files are supported")

    mapping = _load_mapping()
    pgr_map = load_pgr_map()

    df = pd.read_excel(path, dtype=str, keep_default_na=False)
    df.columns = [str(c).strip() for c in df.columns]

    header_map = _build_header_map(list(df.columns), mapping)

    rows_added = 0
    rows_updated = 0
    rows_skipped = 0
    errors: list[dict] = []

    conn = get_connection(project_id)
    try:
        for idx, row in df.iterrows():
            raw = dict(row)

            po_col   = next((c for c, f in header_map.items() if f == "po_number"), None)
            item_col = next((c for c, f in header_map.items() if f == "item_no"),   None)

            po   = str(raw.get(po_col,   "")).strip() if po_col   else ""
            item = str(raw.get(item_col, "")).strip() if item_col else ""

            if not po or not item or po in ("nan", "") or item in ("nan", ""):
                errors.append({"row": int(idx) + 2, "reason": "missing PO or item number", "data": str(raw)})
                rows_skipped += 1
                continue

            qty_col = next((c for c, f in header_map.items() if f == "quantity"), None)
            if qty_col:
                qty_str = str(raw.get(qty_col, "")).strip()
                try:
                    if float(qty_str) == 0:
                        rows_skipped += 1
                        continue
                except (ValueError, TypeError):
                    pass

            data: dict[str, Any] = {}
            extra: dict[str, Any] = {}

            for col, val in raw.items():
                val_str = str(val).strip() if val != "" else None
                if val_str in ("nan", ""):
                    val_str = None

                if col in header_map:
                    field = header_map[col]
                    data[field] = val_str
                else:
                    if val_str:
                        extra[col] = val_str

            wbs = data.get("wbs_element")
            if wbs:
                project_no, station_no = parse_wbs(wbs)
                data["project_no"] = project_no
                data["station_no"] = station_no

            pg = (data.get("purchasing_group") or "").strip().upper()
            if pg and pg in pgr_map:
                pgr_info = pgr_map[pg]
                if not data.get("buyer_name"):
                    data["buyer_name"]  = pgr_info.get("name")
                if not data.get("buyer_email"):
                    data["buyer_email"] = pgr_info.get("email")

            for field in _DATE_FIELDS:
                if field in data:
                    data[field] = clean_date_value(data[field])

            if data.get("current_eta") and not data.get("original_eta"):
                data["original_eta"] = data["current_eta"]

            data["extra_json"] = json.dumps(extra, ensure_ascii=False) if extra else None

            cur = conn.execute(
                "SELECT id FROM materials WHERE po_number=? AND item_no=?", (po, item)
            )
            existing = cur.fetchone()

            if existing is None:
                cols = list(data.keys())
                placeholders = ", ".join(["?"] * len(cols))
                conn.execute(
                    "INSERT INTO materials (" + ", ".join(cols) + ") "
                    "VALUES (" + placeholders + ")",
                    [data[c] for c in cols],
                )
                rows_added += 1
            else:
                mat_id = existing[0]
                fact_updates = {
                    k: v for k, v in data.items()
                    if k in FACT_FIELDS and k not in ("po_number", "item_no")
                }
                semantic_updates = {
                    k: v for k, v in data.items()
                    if k not in FACT_FIELDS and k != "extra_json"
                }

                if fact_updates:
                    set_clause = ", ".join(k + "=?" for k in fact_updates)
                    conn.execute(
                        "UPDATE materials SET " + set_clause + " WHERE id=?",
                        [*fact_updates.values(), mat_id],
                    )

                if semantic_updates:
                    bulk_update_fields(conn, mat_id, semantic_updates, source="manual_import")

                if data.get("extra_json"):
                    conn.execute(
                        "UPDATE materials SET extra_json=? WHERE id=?",
                        (data["extra_json"], mat_id),
                    )

                rows_updated += 1

        fhash = _file_hash(path)
        from datetime import datetime
        conn.execute(
            "INSERT INTO imports "
            "(file_path, file_hash, rows_added, rows_updated, rows_skipped, errors_json, imported_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(path), fhash,
                rows_added, rows_updated, rows_skipped,
                json.dumps(errors, ensure_ascii=False),
                datetime.now().isoformat(sep=" ", timespec="seconds"),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "rows_added":   rows_added,
        "rows_updated": rows_updated,
        "rows_skipped": rows_skipped,
        "errors":       errors,
    }


def export_back(
    source_path: Path,
    dest_path: Path | None = None,
    project_id: str = "default",
) -> Path:
    """Write current_eta and status back into a copy of the source Excel."""
    wb = openpyxl.load_workbook(source_path)
    ws = wb.active

    headers = [str(ws.cell(1, c).value or "").strip() for c in range(1, ws.max_column + 1)]
    mapping  = _load_mapping()
    hmap     = _build_header_map(headers, mapping)

    po_col_idx   = next((i + 1 for i, h in enumerate(headers) if hmap.get(h) == "po_number"), None)
    item_col_idx = next((i + 1 for i, h in enumerate(headers) if hmap.get(h) == "item_no"),   None)
    eta_col_idx  = next((i + 1 for i, h in enumerate(headers) if hmap.get(h) == "current_eta"), None)
    stat_col_idx = next((i + 1 for i, h in enumerate(headers) if hmap.get(h) == "status"),    None)

    if not po_col_idx or not item_col_idx:
        raise ValueError("Cannot find PO / Item columns in source file")

    conn = get_connection(project_id)
    try:
        for row_idx in range(2, ws.max_row + 1):
            po   = str(ws.cell(row_idx, po_col_idx).value or "").strip()
            item = str(ws.cell(row_idx, item_col_idx).value or "").strip()
            if not po or not item:
                continue
            db_row = conn.execute(
                "SELECT current_eta, status FROM materials WHERE po_number=? AND item_no=?",
                (po, item),
            ).fetchone()
            if db_row:
                if eta_col_idx:
                    ws.cell(row_idx, eta_col_idx).value = db_row["current_eta"]
                if stat_col_idx:
                    ws.cell(row_idx, stat_col_idx).value = db_row["status"]
    finally:
        conn.close()

    out = dest_path or source_path.with_stem(source_path.stem + "_updated")
    wb.save(out)
    return out
