import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api import ws
from .api.routes import conversations, health, keys, providers, settings, tasks, tools
from .auth import ALLOWED_ORIGINS
from .services import Services, build_services

__all__ = ["ALLOWED_ORIGINS", "create_app"]


def create_app(services: Services | None = None) -> FastAPI:
    owns_services = services is None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = services or build_services()
        app.state.services = svc
        svc.mcp.start(svc.mcp_servers)
        if os.environ.get("AETHEL_SYSTEM1") != "0":
            svc.system1.start()  # downloads (~1.7 GB, first run only) and loads in the background
        try:
            yield
        finally:
            try:
                await svc.engine.shutdown()
                await svc.chat.shutdown()
                await svc.mcp.stop()
            finally:
                if owns_services:
                    try:
                        await svc.http_client.aclose()
                    finally:
                        svc.close()

    # Interactive API docs are unauthenticated, so they only exist in dev mode.
    dev = os.environ.get("AETHEL_DEV") == "1"
    app = FastAPI(
        title="Aethel",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if dev else None,
        redoc_url="/redoc" if dev else None,
        openapi_url="/openapi.json" if dev else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(keys.router)
    app.include_router(conversations.router)
    app.include_router(settings.router)
    app.include_router(providers.router)
    app.include_router(tasks.router)
    app.include_router(tools.router)
    app.include_router(ws.router)
    return app
