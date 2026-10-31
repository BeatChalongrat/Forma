# API overview

> **สรุปภาษาไทย** — แผนที่ REST API (~110 endpoint) จัดกลุ่มตามหน้าที่ พร้อมสิ่งที่แต่ละกลุ่มทำ
> รายละเอียด schema ของ request/response ดูที่ `/api/docs` (OpenAPI) ตอนรัน API; type ฝั่ง frontend อยู่ที่
> `frontend/src/lib/api.ts` ซึ่งสะท้อน `backend/src/forma/api/serialize.py`

Base path `/api`, except `/health`, `/ready` and `/metrics` at the root. JSON in and out. Application errors are
`{error, message, details}` with the HTTP status: 401 unauthorized, 403 forbidden or policy block, 404 not found,
409 conflict (stale digest, open decisions, unacknowledged anomalies, invalid records), 413 too large, 415 unsupported file,
422 validation, 429 backpressure or rate limit with Retry-After, 503 unavailable. Request-body validation errors
raised by FastAPI use its default `{"detail": […]}` shape. Interactive docs: `/api/docs`. Role per endpoint: [RBAC_AND_SECURITY.md](RBAC_AND_SECURITY.md).

Long operations run as jobs: `POST …/run`, `/profile`, `/propose` and `/transform` return a `job_id`; poll
`GET /jobs/{id}`. `POST /batches` returns the batch view; poll `GET /batches/{id}`. Tests run a job inline with
`sync`: in the JSON body on `/run` and `/propose`, as a query parameter on `/profile`, `/transform` and `/batches`.

## Rounds and files

| Endpoint | Purpose |
|---|---|
| `POST /rounds` · `GET /rounds` · `GET /rounds/{id}` | create / list / read a delivery (with files, mappings, datasets, review tasks, jobs) |
| `POST /rounds/{id}/files` | upload files (multipart); sha256 store; duplicates marked |
| `GET /rounds/{id}/files/{fid}` · `…/grid` | file facts; a bounded grid of raw cells |
| `POST /rounds/{id}/profile` · `/propose` · `/transform` · `/run` | the stages, or the whole pipeline |
| `POST /rounds/batch-run` | run many rounds; `GET /jobs/batch/{id}` for progress |
| `POST /rounds/{id}/mapping-preview` · `/source-preview` · `/relationships` | Studio previews: an unsaved document on a sample; raw source rows; measured relationships between tables |
| `GET /rounds/{id}/verdict` · `POST /rounds/{id}/verdict/accept` | the verdict of the current stage; act on it (digest, decisions) |

## Bulk uploads / work rounds

| Endpoint | Purpose |
|---|---|
| `POST /batches` (multipart, label, work_date, expected_files, auto_publish) · `GET /batches` · `GET /batches/{id}` | create with files, list, read with items, counts, stuck count |
| `PATCH /batches/{id}` · `POST /batches/{id}/files` | rename / date / target; append files (dedupe by name + sha256) |
| `POST /batches/{id}/assign` | give unknown-layout items a customer (existing or new) |
| `POST /batches/{id}/resume` | re-enqueue items whose jobs were lost |
| `GET /batches/{id}/download` | zip of every item's JSON |

## Mappings

| Endpoint | Purpose |
|---|---|
| `GET /mappings/{id}` | version with document, policy report (decision, checks, sandbox, field review, verdict, learning), agent log |
| `POST /mappings/{id}/approve` (`override_block`, comment) · `/reject` · `/edit` (document) | human decisions; edit = new proposed version |
| `GET /mappings/{id}/sandbox` · `POST /mappings/{id}/compare` | stored sandbox details; compare an unsaved document |
| `GET /mappings/{id}/field-review` | what changed vs the approved mapping, checked on the data |
| `GET /mappings/{id}/questions` · `…/questions/evidence?field=` · `POST …/answers/preview` · `/answers/apply` | guided review: questions, cell evidence, impact preview, save as a new version |
| `POST /mappings/{id}/diagnose` | root causes and a patched mapping (AI) |

## Datasets

| Endpoint | Purpose |
|---|---|
| `GET /datasets/{id}` · `/rows` | version with stats, findings, invalid count; paged rows |
| `POST /datasets/{id}/preview-changes` · `/changes` | impact preview; apply a change set (one-time or persistent) → new version |
| `POST /datasets/{id}/copilot` · `/ask` | instruction → operation plan (AI or rules); "why" questions from evidence |
| `GET /datasets/{id}/changes-report` | diff vs last published round + anomalies |
| `GET /datasets/{id}/lineage/{identity}` · `/trace/{identity}` | record lineage; cell-level trace with causes |
| `POST /datasets/{id}/approve` (`acknowledge_anomalies`, comment) · `/rollback` · `/export?fmt=json|jsonl|csv` | approve (contract gate), roll back, export (publishes) |
| `GET /exports/{id}/download` | the artifact (pre-signed on S3) |

## Review, learning, calibration, foresight

| Endpoint | Purpose |
|---|---|
| `GET /review?status=&round_id=` | queue rows with reasons, risk and the verdict brief |
| `GET /learning?customer_id=` · `POST /learning/promote` · `DELETE /learning/{id}` | what was learned, trend, suggestions; promote; forget |
| `GET /calibration` · `POST /calibration/apply` | precision report and proposals; apply one (digest); all-customer access |
| `GET /foresight` | expected deliveries and the watchlist |
| `GET /history` | paginated upload history with filters in the chosen time zone |

## Customers and schemas

| Endpoint | Purpose |
|---|---|
| `GET /customers` · `/{id}` · `POST /customers` · `PATCH /customers/{id}` | customers with rounds, formats, overrides, policy overrides, next expected delivery |
| `DELETE /customers/{id}/overrides/{oid}` | retire a persistent data override |
| `GET /schemas` · `/{ref}` · `/{a}/diff/{b}` · `POST /schemas/preview` · `POST /schemas` | target formats: list, read, diff, check, register |
| `GET /settings/semantics` · `GET /dsl` | the semantic vocabulary and the DSL reference |

## Settings (admin)

| Endpoint | Purpose |
|---|---|
| `GET/PUT /settings/ai` · `POST /settings/ai/connect` · `/test` · `/models` | primary AI connection; paste-a-key connect; probe; model discovery |
| `POST/PATCH/DELETE /settings/ai/backups[/{id}]` · `PUT /settings/ai/backups/order` | backup routes |
| `POST /settings/ai/vision-test` | show each connected model a test picture and keep the result (ADR-049) |
| `GET/PUT/DELETE /settings/policy` · `GET /policy` | approval policy (web edits on top of the file) |
| `PUT /settings/default-format` | default target format |

## Assistant and MCP

| Endpoint | Purpose |
|---|---|
| `GET /assistant/status` | provider, model, tools, whether images can be read, whether the code sandbox is up, the file types a chat accepts |
| `POST /assistant/ask` | one-shot answer with evidence (legacy) |
| `GET/POST /assistant/conversations` · `GET/PATCH/DELETE /assistant/conversations/{id}` · `POST …/import` | persistent chats (owner + access scope); rename, pin, archive, delete |
| `POST /assistant/conversations/{id}/messages` · `/messages/stream` (SSE) · `/stop` | send, with `attachment_ids`; stream deltas, phases, done; stop |
| `POST /assistant/conversations/{id}/attachments` · `GET /assistant/attachments/{file_id}` | add a file to a chat; read one, through a chat you can open (ADR-049) |
| `GET /assistant/memory` · `POST /assistant/memory` · `PATCH /assistant/memory/{id}` · `DELETE /assistant/memory/{id}` · `DELETE /assistant/memory` | read, add, change and delete memory; the last clears all of it (ADR-051) |
| `GET /assistant/artifacts` · `GET /assistant/artifacts/{id}` | every work in your chats; one work with all its versions (ADR-050) |
| `POST /assistant/artifacts/{id}/versions/{n}/restore` | bring an old version back as the next number (ADR-050) |
| `GET /mcp` (405) · `POST /mcp` | Streamable HTTP JSON-RPC: initialize, ping, tools/list, tools/call over the same read-only tools |

## Operations

| Endpoint | Purpose |
|---|---|
| `GET /jobs` · `/jobs/{id}` · `/jobs/stats` · `POST /jobs/{id}/retry` | job rows of the caller's customers and retry (admin); `/jobs/stats` needs all-customer access |
| `GET /ops` | queue, workers, failed jobs, breaker, spend, slowest rounds (all-customer access) |
| `GET /audit?entity_id=` | audit log (all-customer access) |
| `GET /dashboard` | Home figures for the caller's customers: rounds by status, open reviews, automation rate, AI spend, learning, recent rounds |
| `GET /health` · `/ready` · `/metrics` | liveness; dependencies; Prometheus |

## Conventions

* Decisions and edits record the authenticated actor (gaps are listed in
  [RBAC_AND_SECURITY.md](RBAC_AND_SECURITY.md)); the request's `request_id` follows a job into the worker logs.
* Identifiers are prefixed (`cus_`, `rnd_`, `fil_`, `map_`, `dsv_`, `rev_`, `bch_`, `bit_`, `chg_`, `ovr_`,
  `cor_`, `chat_`, `turn_`); job groups are `bat_`.
* Datetimes are UTC ISO-8601; the UI renders them in the user's time zone.
* Pagination is `offset`/`limit`. `/history` and `/learning` return a total; `/rounds`, `/review`, `/jobs`, `/audit`
  and `/batches` return plain arrays.
