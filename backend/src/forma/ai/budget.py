"""Per-task deadlines and progress; context-local so concurrent imports cannot share a budget."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

deadline: ContextVar[float | None] = ContextVar("llm_deadline", default=None)
observer: ContextVar[Callable[[str], None] | None] = ContextVar("llm_observer", default=None)


def remaining() -> float | None:
    from forma.jobs.execution import check

    check()
    value = deadline.get()
    if value is None:
        return None
    left = value - time.monotonic()
    if left <= 0:
        from forma.ai.llm import LLMError

        raise LLMError("AI task deadline exceeded; retry this import without uploading again")
    return left


def progress(message: str) -> None:
    callback = observer.get()
    if callback is not None:
        callback(message)


@contextmanager
def task_budget(seconds: float, callback: Callable[[str], None] | None = None) -> Iterator[None]:
    outer = deadline.get()
    token = deadline.set(min(outer, time.monotonic() + seconds) if outer else time.monotonic() + seconds)
    watch = observer.set(callback or observer.get())
    try:
        yield
    finally:
        deadline.reset(token)
        observer.reset(watch)


def bounded_task[**P, T](fn: Callable[P, T]) -> Callable[P, T]:
    @wraps(fn)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        from forma.core.settings import get_settings

        with task_budget(get_settings().llm_task_budget_s):
            return fn(*args, **kwargs)

    return wrapped
