"""OpenTelemetry integration (ADR-014). Everything here is a no-op until ``FORMA_OTEL_ENDPOINT`` is set.

``span(name, **attrs)`` is the one helper the rest of the code base uses, so pipeline stages, agent
calls and engine runs are traced without importing OpenTelemetry anywhere else.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

_TRACER: Any = None
_ENABLED = False


def configure(endpoint: str | None, service_name: str = "forma-api") -> bool:
    """Install the OTLP exporter + FastAPI/SQLAlchemy/httpx instrumentation. Returns True when active."""
    global _TRACER, _ENABLED
    if not endpoint:
        _ENABLED = False
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:  # pragma: no cover - optional dependency
        _ENABLED = False
        return False
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    _TRACER = trace.get_tracer("forma")
    _ENABLED = True
    with contextlib.suppress(Exception):  # instrumentation packages are optional
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        from forma.db.session import get_engine

        SQLAlchemyInstrumentor().instrument(engine=get_engine())
        HTTPXClientInstrumentor().instrument()
    return True


def instrument_app(app: Any) -> None:
    if not _ENABLED:
        return
    with contextlib.suppress(Exception):
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)


def enabled() -> bool:
    return _ENABLED


@contextlib.contextmanager
def span(name: str, **attrs: Any) -> Iterator[Any]:
    """Start a span with the given attributes; a no-op context when tracing is off."""
    if not _ENABLED or _TRACER is None:
        yield None
        return
    with _TRACER.start_as_current_span(name) as sp:
        for k, v in attrs.items():
            if v is not None:
                sp.set_attribute(k, v if isinstance(v, str | int | float | bool) else str(v))
        yield sp


def set_attrs(sp: Any, **attrs: Any) -> None:
    if sp is None:
        return
    for k, v in attrs.items():
        if v is not None:
            sp.set_attribute(k, v if isinstance(v, str | int | float | bool) else str(v))
