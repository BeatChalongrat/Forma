"""Retry with exponential backoff (ADR-015). Small, explicit, test-friendly (``sleep`` is injectable)."""

from __future__ import annotations

import random
import time
from collections.abc import Callable


def retry[T](
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_s: float = 1.0,
    max_s: float = 30.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> T:
    """Call ``fn`` up to ``attempts`` times; wait ``base_s * 2**i`` (± 25 % jitter, capped) between tries."""
    last: BaseException | None = None
    for i in range(max(1, attempts)):
        try:
            return fn()
        except retry_on as e:
            last = e
            if i == attempts - 1:
                break
            if on_retry:
                on_retry(i + 1, e)
            delay = min(max_s, base_s * (2**i))
            sleep(delay * (0.75 + random.random() * 0.5))
    assert last is not None
    raise last
