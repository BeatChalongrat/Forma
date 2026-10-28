# ADR-016 — Deployment topology, generated configuration, CI gates

**Status** accepted (Phase 7).

## Decision

1. **One backend image, two commands.** `backend/Dockerfile` builds a non-root image that runs
   `forma serve` (API) or `forma worker` (job worker). The web app is a separate nginx image
   (`frontend/Dockerfile`) serving the Vite build and proxying `/api`, `/health`, `/ready` to the API.
   `/metrics` is not proxied.
2. **`docker-compose.prod.yml`** is the reference topology: postgres, redis (arq queue), minio (+ a
   one-shot bucket init with versioning), a one-shot `migrate` service (`forma db upgrade`) that the API
   and workers wait for, `api`, `worker` (scaled with `--scale worker=N` / `FORMA_WORKER_REPLICAS`), `web`,
   an optional `otel-collector` profile and an `ops` profile with a `backup` one-shot. Dev stays
   `make api && make web` with SQLite and local disk — nothing in the dev loop depends on compose.
3. **Configuration is generated, never hand-written.** `Settings` carries a description per field;
   `forma config example` renders `.env.production.example` (every variable, default, explanation,
   secrets blank and marked) and `forma config example --check` fails CI when the committed file
   drifts. `forma config show` prints the effective configuration with secrets masked.
4. **CI gates** (`.github/workflows/ci.yml`): ruff + format + mypy; pytest with coverage ≥ 85 % against
   SQLite **and** real Postgres + MinIO service containers; the migration drift guard; the config example
   drift guard; `forma eval --baseline data/eval_baseline.json` (fails when any summary metric drops);
   `forma bench` in short mode; the frontend lint + build; both images build and the production compose
   file validates.

## Why

A production that differs from CI is a production nobody has tested. The compose file, the generated env
example and the CI gates are three views of the same list of settings and services, kept consistent by
tooling rather than discipline.

## Consequences

* Adding a setting means adding a `Field(description=…)` — CI fails until `.env.production.example`
  is regenerated.
* Raising the eval baseline is a deliberate commit of `data/eval_baseline.json`.
* The compose file assumes MinIO/Postgres/Redis in-cluster; managed equivalents only need the URL/credential
  variables changed.
