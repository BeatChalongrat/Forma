# Roadmap status (blueprint phases → what exists)

| Phase | Blueprint goal | Status | Where |
|---|---|---|---|
| 0 Foundation | target schema, DSL, ground truth, architecture | **done** | `target_schema/`, `mapping_dsl/`, `synth/`, `docs/` |
| 1 Core engine | multi-sheet ingest, profiler, joins, transform, export | **done** | `ingestion/`, `profiling/`, `engine/`, `export/` |
| 2 AI mapping | agents, confidence/evidence, validation, risk policy | **done** (LLM optional; heuristic floor ≥99% on corpus) | `ai/` |
| 3 Web app & review | dashboard, import workspace, Mapping Studio, review queue, bulk approval | **done** | `frontend/` |
| 4 Editor & versioning | spreadsheet editor, one-time/persistent/mapping edits, impact preview, versions, rollback | **done** | `services/datasets.py`, `DataEditor.tsx` |
| 5 Self-healing | drift detection, mapping memory, adaptation agent, feedback | **done** (adaptation needs an LLM key) | `fingerprint/`, `services/mapping.py` |
| 6 Advanced | NL data editing (copilot), root-cause diagnosis, change intelligence, lineage | **done** | `agents.py`, `routes.py` |
| 7 Production | scale, reliability, security, observability, deployment | **done** — see below | ADR-010 … ADR-016 |
| 7+ Safety | mapping simulation sandbox: new vs approved mapping on real files, drill-down, policy block | **done** | ADR-018 |
| 7+ Trust & visual mapping | risk level (low/medium/high + factors + records affected) on every decision; relationship discovery between all tables, diagram in the Studio, drag-to-map | **done** | ADR-019 |
| 7+ Signature features | spreadsheet editing (paste, ranges, bulk on picked rows) + override collisions; cell-level lineage with cause of change; self-healing per-field review checked on data; anomaly detection + approval gate; copilot conditions across collections + evidence-based answers | **done** | ADR-020 … ADR-024 |
| 7+ Operability | any target format without code (semantic mapping, exact export shape), settings in the web app (AI key, policy, formats, customers) | **done** | ADR-017 |
| 7+ AI resilience | backup AI keys tried in order (own provider, model, breaker), model picker, test every key | **done** | ADR-025 |
| 7+ Bulk upload | many files at once, customer found by format (and file-name code), one JSON per file, policy-decided automatic publishing, zip of all JSON | **done** | ADR-027 |
| 7+ Contract gate | records that break the target schema block approval and export; edits refused only for the errors they add | **done** | ADR-026 |
| 7+ Focused workspace | responsive rail/drawer navigation, on-demand inspection panels, focus-trapped dialogs, aCommerce light + neutral dark theme, quiet header, honest estimated-cost labels | **done** | ADR-028, ADR-031, ADR-032 |
| 7+ Guided review & assistant | deterministic review questions (answers = new mapping versions), evidence-first file review traced to source cells, personal preferences (language, theme, time zone), workspace assistant with 19 read-only tools + `POST /api/mcp`, provider endpoint + model discovery | **done** | ADR-029, ADR-035 |
| 7+ Persistent conversations | chats stored in the database (owner + access scope), rename/pin/archive/delete, idempotent retries, cancellable SSE streaming of replies | **done** | ADR-030, ADR-031, ADR-034 |
| 8 Verdict layer | one conclusion per file (mapping and data stage) from the stored reports: outcome, confidence, what only a person decides, evidence, what was folded away; one-click accept through the policy; every acceptance audited with the rules that fired | **phase 1 done** (narrative, assign-customer click, eval metric pending) | ADR-036 |
| 8 Learning from corrections | every approval is diffed against FORMA's proposal: column aliases and confirmed constants per customer, accepted-as-proposed records; the mapper and the AI agents read them first with evidence; aliases three customers taught are suggested for everyone (admin promotes); corrections per approval shown by month | **phase 1 done** | ADR-038 |
| 8 Policy calibration | every decision gets an outcome (accepted unchanged / corrected / rejected / auto then edited); precision per verdict rule and per decision kind; proposals with evidence (trust the data check, lower a confidence bar, a customer's row-count tolerance) applied by an admin through the settings service; per-customer policy overrides | **phase 1 done** | ADR-037 |
| 8 Proactive intelligence | expected deliveries from each customer's cadence (due / overdue with the reason), a watchlist of customers likely to need a check, per-customer price-move baselines proposed from acknowledged rounds | **phase 1 done** | ADR-039 |
| 8 Case page & assistant actions | the round page carries the full verdict card with its one-click accept for both stages; the assistant proposes a server-built `verdict.accept` action (round + digest) that a person confirms in the UI through the ordinary endpoint | **phase 1 done** | ADR-040 |
| 8 Evaluation modes | `forma eval --llm` (AI-refined mapping scored, draft as fallback), `--replay` (round-0 corrections become learned aliases; later rounds scored without / with), `--index` + `data/real/` for real customer files (git-ignored) | **harness done**; real files and their baseline pending | ADR-041 |
| 8 Review explanations & clarification | policy reasons explained in Thai and English; the assistant asks clarification questions before assuming, with the review context carried into the chat | **done** | ADR-042 |
| 8 Input contract readiness | full OOXML scan, strict CSV decoding, formula and error-cell gates, table detection across the whole sheet, unsupported schema shapes refused, the full target schema in AI prompts | **done** | ADR-043 |
| 8 Durable queue reconciliation | on startup, queued jobs are checked against durable results before recovery and resubmission | **done** | ADR-044 |
| 12 Housekeeping and the review clock | retention on a schedule and visible, nightly backups, question age, waiting for the customer, idle rounds | **done** | ADR-053 |
| 13 Backups that restore | every file in the nightly backup, restore proven on this stack, health page says what a backup holds | **done** | ADR-054 |
| 11 Closing a round | cancel with reopen, move to the right customer, replace with a corrected file; nothing deleted | **done** | ADR-052 |
| 10 Memory across chats | topics kept while talking, search of earlier chats, pause, reset, memory-off and incognito chats | **done** | ADR-051 |
| 9 Work beside the chat | flows, documents, slides, sheets and web pages open in a panel beside the chat, with versions, restore and previews | **done** | ADR-050 |
| 8 Assistant sandbox and images | the assistant runs Python in an isolated container and reads images when the connected model can; files attach to a chat | **done** | ADR-048, ADR-049 |
| 8 Grounded chat reports | server-computed tables and charts in assistant conversations; the model selects a view, never values | **done** | ADR-045 |
| 8 Approved format library | a customer's approved layouts stay reusable across history, keyed by target schema and fingerprint | **done** | ADR-046 |
| 8 Decisions and scope fixes | the VAT decision saves from the card; nothing is accepted past an open decision; an account needs a FORMA role; jobs, figures and learning follow the caller's customers | **done** | ADR-047 |
| 7+ Work rounds | a bulk upload is a named, dated work round that accepts more files later; receipt dashboard (received / expected / ready) on Home and the round | **done** | ADR-033, ADR-034 |

## Phase 7 — production hardening (done)

| Item | Status | Where / evidence |
|---|---|---|
| Parallel execution: thread / process / arq (Redis) job backends behind `enqueue()` | done | `jobs/runner.py`, `forma worker`, ADR-010 |
| Durable, idempotent, resumable jobs; stale-job recovery; retry from UI | done | `jobs/handlers.py` stages, `recover_stale`, `POST /api/jobs/{id}/retry`, `tests/test_jobs.py` |
| Per-file parallel profiling; `POST /api/rounds/batch-run` + batch progress | done | `services/rounds.py`, `GET /api/jobs/batch/{id}` |
| Backpressure (429 + Retry-After), upload size cap (413) | done | `FORMA_QUEUE_MAX`, `FORMA_MAX_UPLOAD_MB` |
| 300-file × 100K-row benchmark, numbers for thread and process backends | done | `forma bench`, `docs/PERFORMANCE.md`, `docs/bench/` |
| Engine hot path: vectorised record builder + Polars unit rules | done (dates kept in Python, measured) | `engine/transform.py`, `engine/thai_vec.py` |
| Streaming exports (JSON/JSONL/CSV) | done | `export/exporter.py` |
| Postgres + Alembic; JSONB; indexes; UTC datetimes; pagination | done (verified on real Postgres) | `backend/alembic/`, `forma db`, ADR-011, `tests/test_postgres.py` |
| Object storage protocol: local / S3 / MinIO; keys in rows; pre-signed downloads | done (verified on MinIO) | `forma/storage/`, ADR-012, `tests/test_minio.py` |
| Auth: dev header mode + OIDC (PKCE in the SPA); RBAC importer/reviewer/approver/admin; tenant scoping | done | `forma/auth/`, ADR-013, `tests/test_auth.py` |
| Security pass: upload sniffing, safe names, CORS, LLM rate limits, sample caps, **LLM output guard** | done | `ingestion/sniff.py`, `ai/guard.py`, `tests/test_guard_sniff.py` |
| OpenTelemetry traces, Prometheus `/metrics`, JSON logs, request ids, `/health` + `/ready`, Ops page | done | `core/telemetry.py`, `core/metrics.py`, `pages/Ops.tsx`, ADR-014 |
| Retries + circuit breaker (fallback never auto-approves), atomic dataset versions | done | `ai/breaker.py`, `services/datasets.py::_new_version`, ADR-015 |
| Retention policy (dry-run default, audited, scheduled) | done | `policies/retention.yaml`, `forma retention run` |
| Backup / restore + runbook (RPO/RTO, procedures) | done | `forma backup|restore`, `docs/RUNBOOK.md` |
| Failure-injection tests (dead worker, storage, DB, LLM timeout) | done | `tests/test_failure_injection.py` |
| Production compose, Dockerfiles, generated `.env.production.example`, CI with eval/bench gates | done (stack smoke-tested) | `docker-compose.prod.yml`, `.github/workflows/ci.yml`, ADR-016 |

## What remains (next phases)

FORMA runs for one person on one machine, so what remains comes in two parts.

**For real use**

* **Eval on real customer files** — the harness is ready (`data/real/`, `forma eval --index|--llm|--replay`,
  ADR-041); what is missing are the first 20–30 real ERP exports with reviewed expected output and a
  baseline for them (`docs/EVALUATION.md`).
* **Real target contract** — `target_schema/product.v1.json` is still the draft; swap in the downstream
  schema and run the schema diff + a full re-eval.
* **Copies off this machine** — the repository has no remote, and the backups sit on the same disk (R36).
* **Retention that frees disk** — the bucket is versioned, so a delete keeps the bytes until a lifecycle rule
  expires them (R37). Settle this before switching retention on.
* **Engine next levers** — schema validation per column, nested structs from Polars (see PERFORMANCE.md).

**Only when moving to a server**

* **OIDC against a real IdP** — the token path is unit-tested with RSA keys and the SPA has the PKCE flow;
  it has not been exercised against Keycloak/Entra yet (`docs/PHASE7_REVIEW.md`).
* **Multi-node arq in anger** — the compose stack runs two workers on one host; a multi-host run needs a
  shared S3 (not MinIO-on-the-same-box) and Postgres connection pooling (pgbouncer) sized to workers.
