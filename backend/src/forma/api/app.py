"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from forma import __version__
from forma.api.routes import api
from forma.core.errors import FormaError
from forma.core.logging import configure_logging
from forma.core.settings import get_settings
from forma.db.session import init_db
from forma.jobs import handlers as _handlers  # noqa: F401  (registers job handlers)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    init_db()
    yield


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="FORMA API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(FormaError)
    async def _forma_error(_: Request, exc: FormaError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": exc.code, "message": exc.message, "details": exc.details},
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__, "env": s.env}

    app.include_router(api)
    return app


app = create_app()
