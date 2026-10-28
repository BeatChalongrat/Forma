"""`forma bench` — end-to-end throughput benchmark (ADR-010, docs/PERFORMANCE.md).

Generates (or reuses) a perf-scale corpus, loads ``--files`` rounds into a throw-away database, runs
profile → propose → approve → transform for every round through the configured job backend and
reports wall-clock, per-stage p50/p95, rows/s and peak RSS. The report is written as JSON + Markdown.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import statistics
import threading
import time
from pathlib import Path
from typing import Any

import psutil


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    k = max(0, min(len(xs) - 1, round(p / 100 * (len(xs) - 1))))
    return round(xs[k], 1)


def _stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"n": 0, "p50": 0.0, "p95": 0.0, "mean": 0.0, "max": 0.0, "total": 0.0}
    return {
        "n": len(values),
        "p50": _pct(values, 50),
        "p95": _pct(values, 95),
        "mean": round(statistics.fmean(values), 1),
        "max": round(max(values), 1),
        "total": round(sum(values), 1),
    }


class _RssMonitor:
    """Peak resident set size of this process *and* its children (process backend), sampled in a thread."""

    def __init__(self) -> None:
        self.peak = 0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)

    def _sample(self) -> int:
        me = psutil.Process()
        total = int(me.memory_info().rss)
        for ch in me.children(recursive=True):
            try:
                total += int(ch.memory_info().rss)
            except psutil.Error:
                continue
        return total

    def _loop(self) -> None:
        while not self._stop.is_set():
            with contextlib.suppress(psutil.Error):
                self.peak = max(self.peak, self._sample())
            self._stop.wait(0.5)

    def __enter__(self) -> _RssMonitor:
        self._t.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self._stop.set()
        self._t.join(timeout=2)
        self.peak = max(self.peak, self._sample())


def ensure_corpus(corpus_dir: Path, *, rows: int, distinct: int, seed: int = 7) -> dict[str, Any]:
    """Generate the perf corpus once; later runs reuse it (content-addressed, so the DB load is cheap)."""
    index = corpus_dir / "index.json"
    if index.exists():
        data = json.loads(index.read_text(encoding="utf-8"))
        if data.get("rows") == rows and len(data.get("variants", [])) >= distinct:
            return data  # type: ignore[no-any-return]
    from forma.synth.generator import generate_corpus

    generate_corpus(corpus_dir, seed=seed, n_variants=distinct, rows=rows, sequences=False)
    data = json.loads(index.read_text(encoding="utf-8"))
    data["rows"] = rows
    index.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data  # type: ignore[no-any-return]


def run_bench(
    *,
    files: int = 300,
    rows: int = 100_000,
    distinct: int = 8,
    backend: str | None = None,
    workers: int | None = None,
    out_dir: Path,
    corpus_dir: Path | None = None,
    poll_s: float = 0.5,
) -> dict[str, Any]:
    """Must be called with FORMA_* env already pointing at the bench DB/data dir (see ``cli.bench``)."""
    from sqlalchemy import select

    from forma.core.settings import get_settings
    from forma.db.models import Job, MappingVersion
    from forma.db.session import init_db, session_scope
    from forma.jobs import handlers as _h  # noqa: F401
    from forma.jobs import runner
    from forma.services import mapping as ms
    from forma.services import rounds as rs
    from forma.services.context import get_context

    s = get_settings()
    out_dir.mkdir(parents=True, exist_ok=True)
    corpus_dir = corpus_dir or (out_dir / f"corpus_{rows}")
    t_corpus = time.perf_counter()
    index = ensure_corpus(corpus_dir, rows=rows, distinct=distinct)
    variants = index["variants"][:distinct]
    corpus_s = time.perf_counter() - t_corpus

    init_db()
    ctx = get_context()
    runner.reset_backend()
    # ---- load rounds ---------------------------------------------------------------------
    t_load = time.perf_counter()
    round_ids: list[str] = []
    file_bytes = 0
    for i in range(files):
        v = variants[i % len(variants)]
        with session_scope() as sess:
            c = rs.create_customer(sess, f"BENCH{i:04d}", f"Bench customer {i}", erp_type=v["archetype"])
            r = rs.create_round(sess, c.id, label=f"bench {i}")
            for fname in v["files"]:
                p = corpus_dir / v["variant_id"] / "files" / fname
                data = p.read_bytes()
                file_bytes += len(data)
                rs.add_file(ctx, sess, r, fname, data)
            round_ids.append(r.id)
    load_s = time.perf_counter() - t_load

    # ---- phase 1: profile + propose (run_pipeline) ----------------------------------------
    def _run_batch(
        kind: str, payloads: list[dict[str, Any]], rids: list[str]
    ) -> tuple[dict[str, Any], float]:
        t0 = time.perf_counter()
        batch_id, _ = runner.enqueue_batch(kind, payloads, round_ids=list(rids))
        while True:
            st = runner.batch_status(batch_id)
            if st["done"]:
                break
            time.sleep(poll_s)
        return st, time.perf_counter() - t0

    with _RssMonitor() as rss:
        p1, wall1 = _run_batch(
            "run_pipeline", [{"round_id": r, "use_llm": False} for r in round_ids], round_ids
        )
        # approve every proposed mapping (an operator would do this once per customer); policy blocks stay
        t_appr = time.perf_counter()
        with session_scope() as sess:
            for mv in sess.scalars(select(MappingVersion).where(MappingVersion.status == "proposed")):
                if (mv.policy or {}).get("decision") != "block":
                    ms.approve_mapping(sess, mv.id, "bench", "approved by bench")
        approve_s = time.perf_counter() - t_appr
        p2, wall2 = _run_batch(
            "transform_round", [{"round_id": r, "force": True} for r in round_ids], round_ids
        )

    # ---- collect per-stage timings from the job ledgers ------------------------------------
    stage_ms: dict[str, list[float]] = {"profile": [], "propose": [], "transform": []}
    rows_out = 0
    failures: list[dict[str, Any]] = []
    with session_scope() as sess:
        for j in sess.scalars(select(Job).where(Job.kind.in_(["run_pipeline", "transform_round"]))):
            if j.status == "failed":
                failures.append({"job": j.id, "round": j.round_id, "error": (j.error or "")[:300]})
                continue
            stages = (j.result or {}).get("stages", {})
            for name, info in stages.items():
                if name in stage_ms and isinstance(info, dict) and info.get("duration_ms") is not None:
                    stage_ms[name].append(float(info["duration_ms"]))
            if j.kind == "transform_round":
                rows_out += int((j.result or {}).get("rows") or 0)
    wall = wall1 + approve_s + wall2
    report = {
        "at": dt.datetime.now(dt.UTC).isoformat(),
        "machine": {
            "cpu_count": os.cpu_count(),
            "ram_gb": round(psutil.virtual_memory().total / 1024**3, 1),
            "platform": os.name,
        },
        "config": {
            "files": files,
            "rows_per_file": rows,
            "distinct_variants": len(variants),
            "backend": s.job_backend,
            "workers": s.workers,
            "file_workers": s.file_workers,
        },
        "corpus_generate_s": round(corpus_s, 1),
        "load_s": round(load_s, 1),
        "input_bytes": file_bytes,
        "phase1_profile_propose_s": round(wall1, 1),
        "approve_s": round(approve_s, 1),
        "phase2_transform_s": round(wall2, 1),
        "wall_s": round(wall, 1),
        "rows_out": rows_out,
        "rows_per_s": round(rows_out / wall2, 1) if wall2 else None,
        "rows_per_s_end_to_end": round(rows_out / wall, 1) if wall else None,
        "files_per_min": round(files / wall * 60, 1) if wall else None,
        "stages_ms": {k: _stats(v) for k, v in stage_ms.items()},
        "peak_rss_mb": round(rss.peak / 1024**2, 1),
        "failed_jobs": failures,
        "batch1": {k: v for k, v in p1.items() if k != "jobs"},
        "batch2": {k: v for k, v in p2.items() if k != "jobs"},
    }
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(markdown(report), encoding="utf-8")
    return report


def markdown(r: dict[str, Any]) -> str:
    c = r["config"]
    st = r["stages_ms"]
    lines = [
        f"# forma bench — {c['files']} files × {c['rows_per_file']:,} rows "
        f"({c['backend']} backend, {c['workers']} workers)",
        "",
        f"Run at {r['at']} on {r['machine']['cpu_count']} CPUs / {r['machine']['ram_gb']} GB RAM.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Wall-clock (profile→propose→approve→transform) | {r['wall_s']} s |",
        f"| Phase 1 profile+propose | {r['phase1_profile_propose_s']} s |",
        f"| Phase 2 transform | {r['phase2_transform_s']} s |",
        f"| Rows transformed | {r['rows_out']:,} |",
        f"| Rows/s (transform phase) | {r['rows_per_s']} |",
        f"| Rows/s (end to end) | {r['rows_per_s_end_to_end']} |",
        f"| Files/min | {r['files_per_min']} |",
        f"| Peak RSS (api + workers) | {r['peak_rss_mb']} MB |",
        f"| Failed jobs | {len(r['failed_jobs'])} |",
        f"| Corpus generation (one-off, cached) | {r['corpus_generate_s']} s |",
        "",
        "| Stage | n | p50 ms | p95 ms | mean ms | max ms |",
        "|---|---|---|---|---|---|",
    ]
    for name in ("profile", "propose", "transform"):
        x = st.get(name, {})
        cells = [name, x.get("n", 0), x.get("p50"), x.get("p95"), x.get("mean"), x.get("max")]
        lines.append("| " + " | ".join(str(c_) for c_ in cells) + " |")
    if r["failed_jobs"]:
        lines += ["", "## Failed jobs", ""] + [f"- {f['round']}: {f['error']}" for f in r["failed_jobs"][:20]]
    return "\n".join(lines) + "\n"
