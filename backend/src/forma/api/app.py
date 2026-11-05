"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from forma import __version__
from forma.api.batch_routes import router as batch_router
from forma.api.config_routes import router as config_router
from forma.api.knowledge_revision_routes import router as knowledge_revision_router
from forma.api.knowledge_routes import router as knowledge_router
from forma.api.routes import api
from forma.api.workspace_routes import router as workspace_router
from forma.auth import ensure_auth_is_safe
from forma.core import metrics, telemetry
from forma.core.errors import FormaError
from forma.core.ids import new_id
from forma.core.logging import configure_logging
from forma.core.settings import get_settings
from forma.db.session import commit_request_sessions, get_session, init_db, request_session
from forma.jobs import handlers as _handlers  # noqa: F401  (registers job handlers)
from forma.jobs import runner


@asynccontextmanager
async def lifespan(app_: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    configure_logging(s.log_level, s.log_format)
    if s.is_prod and s.auth == "dev":
        structlog.get_logger().warning("auth.dev_in_prod", detail="FORMA_AUTH_ALLOW_DEV_IN_PROD is set")
    init_db()
    if telemetry.configure(s.otel_endpoint, s.otel_service_name):
        telemetry.instrument_app(app_)
    # Redis submission uses a synchronous adapter; never nest asyncio.run in this event loop.
    import asyncio

    await asyncio.to_thread(runner.start)
    yield
    runner.stop()


def create_app() -> FastAPI:
    s = get_settings()
    ensure_auth_is_safe(s)  # refuse to serve before anything listens
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
        import time

        rid = request.headers.get("x-request-id") or new_id("req")
        request.state.request_id = rid
        structlog.contextvars.bind_contextvars(request_id=rid, path=request.url.path)
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
            # the handler's rows are committed here, before the client sees the response (ADR-057)
            try:
                await run_in_threadpool(commit_request_sessions, request, ok=response.status_code < 400)
            except Exception:
                structlog.get_logger().exception("request.commit_failed")
                response = JSONResponse(
                    status_code=503,
                    content={
                        "error": "commit_failed",
                        "message": "บันทึกข้อมูลไม่สำเร็จ กรุณาโหลดข้อมูลล่าสุดแล้วลองอีกครั้ง",
                        "details": {"request_id": rid},
                    },
                )
        finally:
            structlog.contextvars.unbind_contextvars("request_id", "path")
        response.headers["X-Request-Id"] = rid
        route = getattr(request.scope.get("route"), "path", None) or request.url.path
        if not route.startswith(("/metrics", "/health", "/ready")):
            metrics.HTTP_REQUESTS.labels(
                method=request.method, route=route, status=str(response.status_code)
            ).inc()
            metrics.HTTP_LATENCY.labels(route=route).observe(time.perf_counter() - t0)
        return response

    @app.get("/metrics")
    def prometheus_metrics() -> Response:
        """Prometheus scrape endpoint (ADR-014). Unauthenticated: expose it on the internal network only."""
        return Response(content=metrics.render(), media_type=metrics.CONTENT_TYPE_LATEST)

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
    app.include_router(config_router)
    app.include_router(knowledge_router)
    app.include_router(knowledge_revision_router)
    app.include_router(batch_router)
    app.include_router(workspace_router)
    app.dependency_overrides[get_session] = request_session  # committed before the response (ADR-057)
    return app


app = create_app()
