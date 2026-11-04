"""Execution-only candidate evaluator. It cannot select business meaning or approve output."""

from __future__ import annotations

from typing import Any

from forma.ai.budget import remaining
from forma.ai.reconciliation import regressions
from forma.ai.workbench import Workbench
from forma.mapping_dsl.models import MappingDocument


def compare_candidates(
    workbench: Workbench, candidates: list[dict[str, Any]], baseline: dict[str, Any] | None = None
) -> dict[str, Any]:
    results = []
    for candidate in candidates[:3]:
        remaining()
        doc = MappingDocument.model_validate(candidate["mapping"])
        # Explicitly sampled to bound speculative work. The final selection still receives full verify.
        report = workbench.run(doc)
        if baseline is not None and report.complete:
            report.problems.extend(regressions(baseline, report.coverage))
        results.append(
            {
                "label": candidate["label"],
                "mapping": doc.model_dump(mode="json"),
                "execution": report.to_prompt(),
            }
        )
    return {
        "candidates": results,
        "selected": None,
        "scope": (
            "Candidate execution only; a clean sample does not establish semantic correctness. "
            "Final mapping must pass full verification."
        ),
    }
