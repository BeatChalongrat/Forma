"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from forma import __version__
from forma.api.routes import api
from forma.core.errors import FormaError
from forma.core.ids import new_id
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
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Forma-User",
            "X-Forma-Roles",
            "X-Forma-Customers",
            "X-Request-Id",
        ],
        expose_headers=["X-Request-Id", "Retry-After"],
        max_age=600,
    )

    @app.middleware("http")
    async def _request_id(request: Request, call_next: Any) -> Any:
        """Request id in, request id out, bound to the structlog context and propagated to jobs."""
        import structlog

        rid = request.headers.get("x-request-id") or new_id("req")
        request.state.request_id = rid
        structlog.contextvars.bind_contextvars(request_id=rid, path=request.url.path)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id", "path")
        response.headers["X-Request-Id"] = rid
        return response

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
