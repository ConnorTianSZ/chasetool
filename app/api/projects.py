"""
API: 项目管理（创建/列出/删除）
每个项目独立一个 SQLite 数据库
"""
from __future__ import annotations
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.db.connection import list_projects, save_project, delete_project, init_db

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    id:          str           # 项目唯一标识，如 M.6001515
    name:        str           # 显示名称
    description: str = ""


@router.get("")
def get_projects():
    return list_projects()


@router.post("")
def create_project(body: ProjectCreate):
    if not body.id.strip():
        raise HTTPException(400, "项目 ID 不能为空")
    # 初始化数据库
    init_db(body.id)
    project = save_project(body.id, {
        "name":        body.name,
        "description": body.description,
        "created_at":  datetime.utcnow().isoformat(),
    })
    return project


@router.delete("/{project_id}")
def remove_project(project_id: str):
    ok = delete_project(project_id)
    if not ok:
        raise HTTPException(404, "项目不存在")
    return {"ok": True}
