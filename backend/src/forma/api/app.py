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
from forma.jobs import runner


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    configure_logging(s.log_level, s.log_format)
    init_db()
    runner.start()
    yield
    runner.stop()


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
            headers=exc.headers or None,
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        """Liveness: the process is up. Never touches dependencies."""
        return {"status": "ok", "version": __version__, "env": s.env}

    @app.get("/ready")
    def ready() -> JSONResponse:
        """Readiness: database, storage and job queue reachable. 503 when any check fails."""
        from forma.api.health import readiness

        body = readiness()
        return JSONResponse(status_code=200 if body["status"] == "ok" else 503, content=body)

    app.include_router(api)
    return app


app = create_app()
