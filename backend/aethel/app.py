from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api import ws
from .api.routes import conversations, health, keys, providers, settings
from .services import Services, build_services

ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
]


def create_app(services: Services | None = None) -> FastAPI:
    owns_services = services is None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = services or build_services()
        app.state.services = svc
        try:
            yield
        finally:
            try:
                await svc.chat.shutdown()
            finally:
                if owns_services:
                    try:
                        await svc.http_client.aclose()
                    finally:
                        svc.close()

    app = FastAPI(title="Aethel", version=__version__, lifespan=lifespan)
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
    app.include_router(ws.router)
    return app
