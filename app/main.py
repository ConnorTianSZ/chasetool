"""ChaseBase FastAPI entrypoint (multi-project)"""
from __future__ import annotations
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.types import Receive, Scope, Send
from dotenv import load_dotenv

from app.db.connection import init_db
from app.logger import setup_logging
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
    setup_logging()
    # 记录工具启动事件
    try:
        from app.db.activity import log_activity, EVT_STARTUP
        log_activity(EVT_STARTUP, "default", meta={"version": "0.2.0"})
    except Exception:
        pass
    yield


class NoCacheStaticFiles(StaticFiles):
    """静态文件响应头添加 no-cache，确保浏览器每次检查更新"""
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        original_send = send

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = message.get("headers", [])
                headers.extend([
                    (b"cache-control", b"no-cache, no-store, must-revalidate"),
                    (b"pragma", b"no-cache"),
                    (b"expires", b"0"),
                ])
                message["headers"] = headers
            await original_send(message)

        await super().__call__(scope, receive, send_with_headers)


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
    # 使用 no-cache StaticFiles：浏览器每次都会向服务器验证文件是否更新
    app.mount("/static", NoCacheStaticFiles(directory=str(_WEB_DIR / "static")), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(str(_WEB_DIR / "index.html"))


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.2.0"}
