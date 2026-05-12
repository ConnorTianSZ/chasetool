"""API: Excel import / export with project ID"""
from __future__ import annotations
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException, Query, Path as FPath
from fastapi.responses import Response
import shutil, tempfile, os
from datetime import datetime

from app.services.excel_io import (
    import_excel,
    import_chase_updates,
    export_back,
    export_full_db,
    export_chase_append,
    _is_excel,
)
from app.db.connection import get_connection
from app.db.activity import log_activity, EVT_IMPORT_EXCEL, EVT_ETA_UPLOAD

router = APIRouter(prefix="/api/projects/{project_id}/imports", tags=["imports"])


@router.post("/upload")
async def upload_excel(
    project_id: str = FPath(...),
    file: UploadFile = File(...),
):
    if not _is_excel(file.filename or ""):
        raise HTTPException(400, "Only .xlsx / .xls files are supported")

    filename = file.filename or ""
    suffix = Path(filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        result = import_excel(tmp_path, project_id=project_id)
    finally:
        os.unlink(tmp_path)

    # 记录 SAP Excel 上传日志
    log_activity(
        EVT_IMPORT_EXCEL,
        project_id,
        meta={
            "file_name":    filename,
            "rows_added":   result.get("added",   0),
            "rows_updated": result.get("updated", 0),
            "rows_skipped": result.get("skipped", 0),
        },
    )
    return result


@router.post("/upload_chase_updates")
async def upload_chase_updates(
    project_id: str = FPath(...),
    file: UploadFile = File(...),
):
    if not _is_excel(file.filename or ""):
        raise HTTPException(400, "Only .xlsx / .xls files are supported")

    filename = file.filename or ""
    suffix = Path(filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        result = import_chase_updates(tmp_path, project_id=project_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        os.unlink(tmp_path)

    # 记录催货回复交期上传日志（手工填写的 ETA 回填表）
    log_activity(
        EVT_ETA_UPLOAD,
        project_id,
        meta={
            "file_name":    filename,
            "rows_updated": result.get("updated", 0),
            "rows_skipped": result.get("skipped", 0),
        },
    )
    return result


@router.post("/import_path")
def import_from_path(
    project_id: str = FPath(...),
    path: str = Query(...),
):
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, f"File not found: {path}")
    result = import_excel(p, project_id=project_id)
    log_activity(
        EVT_IMPORT_EXCEL,
        project_id,
        meta={
            "file_name":    p.name,
            "rows_added":   result.get("added",   0),
            "rows_updated": result.get("updated", 0),
            "rows_skipped": result.get("skipped", 0),
        },
    )
    return result


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


@router.get("/export_db")
def export_db_api(project_id: str = FPath(...)):
    """将当前项目完整物料数据库导出为 Excel 文件（浏览器直接下载）。"""
    try:
        xlsx_bytes = export_full_db(project_id=project_id)
    except Exception as e:
        raise HTTPException(500, f"导出失败：{e}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"materials_{project_id}_{timestamp}.xlsx"
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/export_chase")
def export_chase_api(
    project_id: str = FPath(...),
    source_path: str = Query(...),
):
    """在原始 Excel 末尾追加催货信息列，输出为同目录 <原名>-chase.xlsx。"""
    p = Path(source_path)
    if not p.exists():
        raise HTTPException(404, f"源文件不存在：{source_path}")
    try:
        out_path = export_chase_append(p, project_id=project_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"导出失败：{e}")
    return {"ok": True, "output_path": str(out_path)}
