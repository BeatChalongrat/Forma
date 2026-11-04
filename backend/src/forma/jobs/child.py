"""Isolated execution entrypoint used by ARQ; one process per active job."""

from __future__ import annotations

import sys

from forma.core.logging import configure_logging
from forma.core.settings import get_settings
from forma.jobs.runner import run_job


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    generation = int(sys.argv[2])
    run_job(sys.argv[1], generation if generation >= 0 else None, sys.argv[3])


if __name__ == "__main__":
    main()
