# Release and change process

> **สรุปภาษาไทย** — วิธีทำงานกับ repo: branch ต่อ feature → `make check` + `make eval-gate` → commit แบบ
> conventional พร้อมเอกสาร/ADR ในชุดเดียวกัน → fast-forward merge เข้า `main` → build image → migrate → deploy
> ทุก model change มี migration, ทุก setting ใหม่มี description, ทุกการตัดสินใจเชิงสถาปัตยกรรมเป็น ADR

## 1. Branching and commits

* One branch per change (`feat/…`, `fix/…`, `docs/…`, `chore/…`); merge into `main` with `--ff-only` once
  the gates are green. `main` is always deployable.
* Conventional commit messages: `feat:`, `fix:`, `docs:`, `test:`, `chore:`; the body says what changed and
  why, names the ADR, and lists the migrations.
* Documentation and ADRs change in the same commit as the code they describe. An architectural decision is
  an ADR in `docs/adr/` (numbered, status, context, decision, consequences, verification); an ADR is binding
  until a newer one supersedes it.

## 2. Gates (see [TESTING_STRATEGY.md](TESTING_STRATEGY.md))

```
make check          # ruff + ruff format --check + mypy strict + pytest (coverage ≥ 80 %)
make eval-gate      # accuracy on the synthetic corpus vs data/eval_baseline.json
cd frontend && npx tsc -b && npx oxlint src && npm run build
cd backend && .venv/bin/forma config example --check ../.env.production.example   # when settings changed
```

CI runs the same plus real Postgres/MinIO, the migration drift guard, a short benchmark, both images and the
compose config.

## 3. Database changes

1. Change the model in `db/models.py`.
2. `forma db revision -m "…"`, then rewrite the generated file by hand so it uses portable types
   (`sa.DateTime(timezone=True)`, JSON with a JSONB variant) and reads well; test upgrade and downgrade.
3. `tests/test_migrations.py::test_models_and_migrations_agree` must pass.
4. Production runs migrations through the `migrate` service before `api` and `worker` start
   (`FORMA_ENV=prod` makes pending migrations a startup error). Dev SQLite is upgraded with `forma db upgrade`.

## 4. Settings changes

Every new `Settings` field has a `description`; regenerate `.env.production.example` with
`forma config example --out ../.env.production.example` and commit it (CI fails on drift). Settings edited in
the web app go through `services/config.py` (validated, audited without secrets, revision bumped).

## 5. Policy and rule changes

* `policies/approval.yaml` is the baseline; web edits sit on top. A new section or key needs
  `DEFAULT_POLICY`, `validate_policy`, the Settings form, tests and a doc row.
* Verdict rules: bump `RULES_VERSION` in `services/verdict.py` when a rule or its wording changes, so stored
  verdicts recompute on read; record what the change fixed in ADR-036's implementation notes.
* Heuristic or engine changes: run `make eval-gate`; if a metric legitimately improves, update
  `data/eval_baseline.json` in the same commit and say why.

## 6. Deploying

```
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build --no-deps migrate api worker web
curl http://localhost/ready
```

The stack is stateless apart from Postgres and the bucket. Rolling back a release = redeploy the previous
image; data written by the newer release stays (migrations are additive by convention; a downgrade exists
for each but is a deliberate operation).

Before a release: back up (`forma backup`), read [RUNBOOK.md](RUNBOOK.md) for the rollout of a new target
schema or a provider change, and announce the downstream-visible changes (new export fields, renamed
formats).

## 7. Versioning what the downstream sees

* The target format is versioned (`product@1`, `product@2`); a round records which it used; exports carry
  exactly that version's fields. Rolling out a new version is a registration plus assignment per customer
  or as default, never a rewrite of old rounds.
* Export artifacts carry a sha256; tell the consumer which artifact to take after a re-export.

## 8. Checklist

- [ ] branch, tests first for bugs
- [ ] `make check` · `make eval-gate` · frontend gates
- [ ] migration (if model changed) · `.env.production.example` (if settings changed)
- [ ] docs / ADR in the same commit · roadmap row if it is a feature
- [ ] conventional commit · ff-merge into `main`
- [ ] backup · deploy · `/ready` · smoke check (TESTING_STRATEGY §4)
