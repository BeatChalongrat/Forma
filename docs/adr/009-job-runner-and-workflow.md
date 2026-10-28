# ADR-009 — Explicit pipeline stages with a swappable job runner

**Decision** The pipeline is a small set of explicit, idempotent service functions (profile → propose →
transform → export) executed by a thread-pool job runner with durable `Job` rows. The agent sequence is a
plain Python state machine with per-agent logs rather than a graph framework.

**Why** One engineer must be able to read the whole flow; durable state lives in the DB anyway.

**Consequences** Celery/arq/Temporal can replace the runner behind `enqueue()` when parallel files per
round or multi-node execution are needed; LangGraph can wrap the agents if branching grows.
