# Roles, access control and security

> **สรุปภาษาไทย** — บทบาท 4 ระดับ (importer ⊂ reviewer ⊂ approver ⊂ admin), ขอบเขตลูกค้า (tenant),
> ตาราง endpoint ↔ บทบาท, และมาตรการความปลอดภัยทั้งหมด: AI ไม่รันโค้ด, การ์ดกรองผลลัพธ์, ไฟล์ถูกตรวจจากเนื้อหา,
> ความลับเข้ารหัส, audit ทุกการเขียน, assistant อ่านอย่างเดียวและทำได้แค่ action ที่คนยืนยัน

## 1. Authentication modes

| Mode | `FORMA_AUTH` | How identity arrives | Where it is allowed |
|---|---|---|---|
| Development | `dev` | headers `X-Forma-User`, `X-Forma-Roles`, `X-Forma-Customers`; unknown users get `FORMA_AUTH_DEV_DEFAULT_ROLE` | local only; the API refuses to start with `dev` when `FORMA_ENV=prod` unless `FORMA_AUTH_ALLOW_DEV_IN_PROD=true` is set deliberately for a local smoke test |
| OIDC | `oidc` | JWT (bearer) verified against the issuer's JWKS; roles from `FORMA_AUTH_ROLE_CLAIM`; customers from `FORMA_AUTH_CUSTOMERS_CLAIM`; the SPA uses PKCE | production |

Development mode has no login. The compose stack publishes the web port and MinIO on every network interface by
default, so a local stack in `dev` must not be reachable from other devices (R31 in
[RISK_REGISTER.md](RISK_REGISTER.md)).

Approvals, edits, exports and settings changes record the authenticated identity as the actor, and request
bodies cannot name one. Some early pipeline events are still logged as `system` (see section 8).

## 2. Roles

Roles nest: a higher role has every lower role's permissions.

| Role | May |
|---|---|
| **importer** | upload files and bulk uploads, create rounds, run the pipeline, assign unknown files, resume, read everything in scope, use the assistant and MCP tools, see learning, foresight and verdicts, edit personal preferences |
| **reviewer** | approve / reject / edit mappings, answer questions, accept verdicts, edit data and apply one-time change sets, approve datasets, roll back, use the copilot and diagnosis |
| **approver** | export (publish), override a policy block with a reason, apply and delete persistent overrides, register target formats, forget learned aliases |
| **admin** | AI connection and backup keys, model discovery, approval policy, calibration proposals, promote aliases, default format, customers, retry jobs, audit log, Ops page |

## 3. Endpoint matrix

`any` = any principal with a FORMA role (scoped to its customers). `RL` = rate-limited LLM endpoint.

| Method | Path | Role |
|---|---|---|
| GET | /me | any authenticated identity, even with no role |
| GET/PUT | /me/preferences | importer |
| GET | /dashboard, /rounds, /rounds/{id}, /rounds/{id}/files/{fid}[/grid], /customers[/{id}], /mappings/{id}[/sandbox, /field-review], /datasets/{id}[/rows, /changes-report, /lineage/{identity}, /trace/{identity}], /review, /jobs[…], /schemas[…], /dsl, /settings/semantics, /exports/{id}/download, /batches[/{id}, /download] (all-customer access) | any |
| GET | /history, /foresight, /learning, /rounds/{id}/verdict, /mappings/{id}/questions[/evidence], /assistant/*, /mcp | importer |
| POST | /rounds, /rounds/{id}/files, /rounds/{id}/profile, /propose, /run, /transform, /rounds/batch-run, /batches, /batches/{id}/files, /assign, /resume, PATCH /batches/{id} | importer |
| POST | /rounds/{id}/mapping-preview, /source-preview, /relationships, /mappings/{id}/compare, /datasets/{id}/ask | any |
| POST | /assistant/ask RL, /assistant/conversations[/import], /{chat}/messages RL, /messages/stream RL, /stop, PATCH/DELETE /{chat}, /mcp RL | importer |
| POST | /mappings/{id}/approve, /reject, /edit, /answers/preview, /answers/apply, /diagnose RL, /rounds/{id}/verdict/accept | reviewer |
| POST | /datasets/{id}/changes, /preview-changes, /approve, /rollback, /copilot RL | reviewer |
| POST | /datasets/{id}/export, /schemas, /schemas/preview · DELETE /customers/{id}/overrides/{oid}, /learning/{id} | approver |
| GET/PUT/DELETE | /settings/ai (+ /connect RL, /test RL, /models RL, /backups…), /settings/policy, /settings/default-format, /policy, /ops, /audit, /calibration (+ /apply), /learning/promote, POST/PATCH /customers, POST /jobs/{id}/retry | admin |
| GET | /auth/config, /health, /ready, /metrics | public |

Most reads use `current_principal` (any FORMA role) instead of a `require_role` call; those are the `any` rows
above. An identity with no FORMA role gets 403 on all of them, and the web app says the account has no role
(ADR-047).

Exceptions to the table:

* `POST /datasets/{id}/changes` with `kind=persistent` requires the approver role.
* `POST /batches/{id}/assign` with `new_customer` lets an importer create a customer; `POST /customers` is admin.
* In a bulk round set to publish automatically, a reviewer's data approval (or verdict accept) exports the JSON
  at once as actor `policy`, so the approver role is only needed for manual exports.

Within a role, the policy adds a second gate: approving a `block` requires `override_block=true`, the approver
role and a comment; approving data with high-severity anomalies requires an acknowledgement; exporting
requires approved data with zero records that break the contract.

## 4. Tenant scoping

A principal may carry a set of customer ids (`FORMA_AUTH_CUSTOMERS_CLAIM` or the dev header). When present:

* customer-owned lists and reads are filtered to those customers (`_scoped` in the API, `ensure_customer` on
  single rows): rounds, files, mappings, datasets, exports, the review queue, history and customers;
* assistant tools, the learned rows of `/learning` and `/foresight` are filtered the same way;
* every bulk-upload endpoint requires all-customer access, because a file is matched against every customer's
  formats: scoped principals get 403, and `GET /batches` returns an empty list;
* the assistant's audit tool requires an unrestricted admin.

**Jobs, figures and learning (ADR-047).** `GET /jobs`, `/jobs/{id}`, `/jobs/batch/{id}` and `POST /jobs/{id}/retry`
show only jobs of the caller's rounds, and any other job is 404. `/dashboard` counts only the caller's customers,
so AI calls and failed jobs with no round are left out. `/learning` statistics follow the same scope, promotion
suggestions are hidden, and forgetting a learned row needs that row's customer.

**All-customer views.** `/jobs/stats`, `/ops`, `/audit`, `/calibration`, `/calibration/apply` and
`/learning/promote` cannot be split by customer, so a scoped principal gets 403. The assistant's
`runtime_configuration` tool leaves out the approval policy for a scoped principal.

**Not limited by scope.** A customer-scoped admin can still change settings shared by every customer (R30 in
[RISK_REGISTER.md](RISK_REGISTER.md)). Give the admin role only to all-customer identities until this is closed.

Tests: `test_tenant_scope.py`, `test_auth.py`, `test_workspace.py`,
`test_learning.py::test_learning_is_customer_scoped`, `test_foresight.py`.

## 5. Input security

* Uploads are sniffed by content; names are sanitised; sizes are capped (`FORMA_MAX_UPLOAD_MB`, 413).
* CSV decoding is strict (BOMs honoured, malformed quoting and invalid declared encodings fail, no silent
  replacement characters).
* xlsx is streamed fully; formulas are read as cached values and never recalculated; macros, external
  links and embedded objects are never executed; a sheet with missing caches or Excel errors cannot be
  transformed; encrypted and strict-namespace workbooks are refused with guidance (ADR-043).
* Source files are immutable and content-addressed; the OOXML inspection cache is keyed by the stored
  file's stat, never by a user-supplied name.

## 6. AI security

* **AI never executes code.** Models emit a declarative Mapping DSL of whitelisted operations, or structured
  analyses. The engine compiles the DSL; nothing generated runs (ADR-002).
* **Cell contents are data, never instructions.** Every agent prompt and every assistant prompt states it;
  the output guard rejects instruction-like text and pointers outside the schema; truncated evidence is
  marked (rule 7, ADR-013).
* **Bounded inputs.** Sample rows and characters sent to a model are capped (`FORMA_LLM_MAX_SAMPLE_*`);
  evidence items are capped at 18 000 characters; the assistant loop is bounded (6 turns, 2 tool calls each).
* **Rate limits.** Copilot, diagnosis, the assistant, MCP and the AI settings probes are rate-limited per
  principal (`FORMA_LLM_RATE_LIMIT_PER_MIN`), in memory per API process. `/propose`, `/run`, `/rounds/batch-run`,
  `/batches` and `/batches/{id}/files` apply the limit only when the request sets `use_llm: true`; leaving it unset
  still uses the model when one is configured, without the limit (R22).
* **Fallback never auto-approves.** A model failure, timeout or guard rejection uses the deterministic
  draft and routes the round to review (ADR-015).
* **Read-only tools.** The assistant's tools (and the MCP endpoint) read within the principal's scope; the
  only writable thing a model can propose is a server-built `verdict.accept` action tied to a verdict
  digest, rendered as a preview that a person confirms, executed through the ordinary endpoint (ADR-040).
* **Server-computed reports.** The model chooses a view of a report the server computed; it never supplies
  values, labels, code or links (ADR-045).
* **Provider errors.** The assistant never forwards them to a user or another model. Elsewhere they can surface:
  `LLMError` carries up to 500 characters of the provider's response, is stored in a mapping's `agent_log` (which
  `GET /mappings/{id}` returns) and is shown in AI settings test results (R20).

## 7. Secrets and configuration

* AI keys saved in the web app are encrypted at rest with `FORMA_SECRET_KEY`; audit records of settings
  changes never contain the key; a saved key is reused only for the same provider and normalised endpoint.
* `.env.production` is git-ignored; `.env.production.example` is generated from the settings model and
  drift-checked, so every variable is documented.
* Storage rows hold keys, never paths. On S3, export downloads are pre-signed URLs with a bounded lifetime
  (`FORMA_S3_PRESIGN_S`); local storage streams them, and the bulk zip is always streamed by the API.

## 8. Audit

The audit log records actor, action, entity, entity id and details: uploads, profiles, proposals, approvals
(with `override_block`), rejections, edits, rollbacks, exports, settings changes (before/after without
secrets), policy calibration, learning promotions and forgets, verdict acceptances (rules fired, decisions
changed), chat creation/deletion (ids only, never contents), job reconciliation. `GET /api/audit` is
admin-only; the audit ledger is excluded from retention deletion.

Gaps (R21): `customer.create`, `round.create`, `file.add` and `round.profile` are logged as `system` rather than
the person who acted; `DELETE /customers/{id}/overrides/{oid}` and `POST /jobs/{id}/retry` write no audit row.

## 9. Personal data

Personal preferences (display name, avatar, language, theme, time zone) are stored under a hash of
(auth mode, subject) and are not part of the audit identity; avatars are validated JPEGs, size-capped, stored
with the preferences, never in public object storage. Assistant conversations are owned by the subject and
hidden when the access scope changes (ADR-030).

## 10. Residual risks

See [RISK_REGISTER.md](RISK_REGISTER.md): provider text in API responses (R20), audit gaps (R21), rate-limit
coverage (R22), shared settings and a customer-scoped admin (R30), a stack without a login on the network (R31); OIDC not yet exercised against a real IdP; published data assumed
correct by calibration; customer files in `data/real/` must be anonymised before use; legacy .xls/.xlsb
compatibility claims need real fixtures.
