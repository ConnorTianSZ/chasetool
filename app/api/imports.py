"""API: Excel import / export with project ID"""
from __future__ import annotations
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException, Query, Path as FPath
import shutil, tempfile, os

from app.services.excel_io import import_excel, export_back, _is_excel
from app.db.connection import get_connection

router = APIRouter(prefix="/api/projects/{project_id}/imports", tags=["imports"])


@router.post("/upload")
async def upload_excel(
    project_id: str = FPath(...),
    file: UploadFile = File(...),
):
    if not _is_excel(file.filename or ""):
        raise HTTPException(400, "Only .xlsx / .xls files are supported")

    suffix = Path(file.filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        result = import_excel(tmp_path, project_id=project_id)
    finally:
        os.unlink(tmp_path)

    return result


@router.post("/import_path")
def import_from_path(
    project_id: str = FPath(...),
    path: str = Query(...),
):
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, f"File not found: {path}")
    return import_excel(p, project_id=project_id)


@router.get("/history")
def import_history(project_id: str = FPath(...), limit: int = 20):
    conn = get_connection(project_id)
    try:
        rows = conn.execute(
            "SELECT * FROM imports ORDER BY imported_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/export_back")
def export_back_api(
    project_id: str = FPath(...),
    source_path: str = Query(...),
    overwrite: bool = Query(False),
):
    p = Path(source_path)
    if not p.exists():
        raise HTTPException(404, f"File not found: {source_path}")
    dest = p if overwrite else None
    out_path = export_back(p, dest, project_id=project_id)
    return {"ok": True, "output_path": str(out_path)}
