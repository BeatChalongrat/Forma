# ADR-011 — PostgreSQL in production, Alembic migrations, SQLite stays for dev

**Status** accepted (Phase 7).

## Decision

1. **Alembic owns the schema.** `backend/alembic/` holds the migration chain; `forma db upgrade |
   downgrade | revision -m … | current | stamp` wrap it. The API refuses to start on a pending revision
   (`MigrationPending`) instead of silently running against an old schema.
2. **Dev stays zero-ceremony.** With `FORMA_ENV=dev` (default) and no `alembic_version` table,
   `init_db()` runs `create_all`, adds columns a legacy dev database is missing, and *stamps head* so the
   next change goes through migrations like everywhere else. `FORMA_ENV=prod` never calls `create_all`.
3. **One model declaration, two dialects.** `JSONType = JSON().with_variant(JSONB(), "postgresql")`:
   JSON on SQLite, JSONB on Postgres. `UTCDateTime` is a `TypeDecorator` that returns timezone-aware UTC
   on every dialect (SQLite drops tz info; naive values are declared UTC on read and write), so no code
   compares aware with naive datetimes.
4. **Indexes for the hot queries** live in the models (and therefore in the migrations): review queue by
   `(status, priority, created_at)`, mappings by `(customer_id, status)` and `(customer_id, fingerprint)`,
   datasets by `(round_id, version)`, jobs by `(status, created_at)` and `batch_id`, llm_calls by
   `round_id`, audit by `(entity_id, created_at)`.
5. **Pagination everywhere lists are exposed**: `limit` + `offset` on rounds, review, jobs and audit.
6. **Verified on a real Postgres.** `tests/test_postgres.py` runs the full API flow with migrations only,
   against `docker compose up postgres`, when `FORMA_TEST_PG_URL` is set; CI starts the service container.
   `tests/test_migrations.py::test_models_and_migrations_agree` fails when a model change has no migration.

## Why

SQLite with WAL is right for one operator team on one node and keeps `make install && make api` free of
services. Several worker nodes, concurrent reviewers and JSONB queries need Postgres, and a schema that
evolves needs migrations before the first breaking change — not after.

## Consequences

* Every model change ships with `forma db revision -m "…"` output in the same commit.
* Date/time columns must use `UTCDateTime` (the `dt.datetime` annotation maps to it); raw `DateTime` is a
  review finding.
* Postgres-only features (partial indexes, JSONB operators) are allowed only behind `with_variant` or a
  dialect check, so SQLite dev keeps working.
