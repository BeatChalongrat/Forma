# ADR-014 — Observability: OpenTelemetry traces, Prometheus metrics, JSON logs, readiness

**Status** accepted (Phase 7).

## Decision

1. **Tracing is opt-in and zero-cost when off.** `forma.core.telemetry.span(name, **attrs)` is the only
   tracing API the code base uses. With `FORMA_OTEL_ENDPOINT` unset it is a no-op context manager;
   when set, the OTLP/HTTP exporter is installed and FastAPI, SQLAlchemy and httpx are auto-instrumented.
   Spans: `job.run` (per job), `stage.profile|propose|transform` (per pipeline stage), `llm.structured`
   (model, provider, attempts, tokens, cost, outcome) and `engine.run` (rows in/out, duration, errors).
2. **Metrics describe the system, not the replica.** `/metrics` (Prometheus text format) mixes
   in-process counters/histograms (job durations by kind, HTTP latency by route, LLM calls/cost/tokens
   by agent, engine rows/s, breaker state) with gauges computed from the database at scrape time (rounds
   by status, review queue size and oldest age, queue depth and oldest queued age, automation rate, LLM
   cost per round) so every API replica reports the same truth. A scrape never fails on a DB error.
3. **Logs are structured.** `FORMA_LOG_FORMAT=json` switches structlog to one JSON object per line.
   Every request gets an `X-Request-Id` (incoming header or generated), bound to the log context, echoed in
   the response, stored on the `Job` row and re-bound while the job runs on a worker, so a failure in a
   worker process can be traced back to the HTTP call that caused it.
4. **Health has two meanings.** `/health` is liveness (process up, touches nothing). `/ready` checks the
   database (including pending migrations), the storage backend and the job queue and answers 503 when any
   is down. The web app polls `/ready` and shows a quiet banner when the API is unreachable or degraded.
5. **Ops page** (`/ops`, admin only; `GET /api/ops`): queue depth and age, workers and in-flight jobs,
   failed jobs with a retry button, LLM spend per day, slowest rounds with per-stage times, breaker state,
   storage/database/auth/tracing/schema-revision facts.

## Why

An operator at 02:00 needs to answer "is it stuck, is it failing, is it slow, is it expensive" without a
shell. Traces answer *where* time goes per round; metrics answer *how much* across rounds; the Ops page
is the subset of both that is safe to act on (retry) from the UI.

## Consequences

* `/metrics` is unauthenticated by design (Prometheus scrapes without tokens); expose it only on the
  internal network (the compose file does not publish it through nginx).
* Metric label cardinality is bounded: routes are templates (`/api/rounds/{round_id}`), agents and job
  kinds are enums, `forma_llm_cost_usd_per_round` keeps the last 100 rounds only.
* New pipeline stages must wrap themselves in `span("stage.<name>")` and `JobProgress.mark` to appear in
  traces and on the Ops page.
