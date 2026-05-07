"""ChaseBase FastAPI entrypoint (multi-project)"""
from __future__ import annotations
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv

from app.db.connection import init_db
from app.api import (
    projects,
    materials,
    imports,
    chase,
    inbox,
    chat,
    dashboard,
    settings_api,
)

load_dotenv(override=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db("default")
    yield


app = FastAPI(
    title="ChaseBase",
    description="Procurement chase management system (multi-project)",
    version="0.2.0",
    lifespan=lifespan,
)

app.include_router(projects.router)
app.include_router(materials.router)
app.include_router(imports.router)
app.include_router(chase.router)
app.include_router(inbox.router)
app.include_router(chat.router)
app.include_router(dashboard.router)
app.include_router(settings_api.router)

_WEB_DIR = Path(__file__).parent.parent / "web"
if (_WEB_DIR / "static").exists():
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(str(_WEB_DIR / "index.html"))


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.2.0"}
