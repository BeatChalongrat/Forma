# Risk register and known limits

> **สรุปภาษาไทย** — รายการความเสี่ยงและข้อจำกัดที่รู้อยู่แล้ว พร้อมผลกระทบ สิ่งที่ลดความเสี่ยงไว้แล้ว และสิ่งที่ยังต้องทำ
> อัปเดตทุกครั้งที่ปิดหรือเปิดความเสี่ยงใหม่ เจ้าของ = บทบาทที่ควรรับผิดชอบ

Severity: H high · M medium · L low. Status: open · mitigated · accepted · closed. Severities and owners are
proposals by role; the team lead confirms them and assigns named people.

**Operating context.** One person operates FORMA today, so that person is importer, reviewer, approver and admin,
and no second person checks an approval. As a proposal, not a decision: R9, R10, R20, R21, R22, R27 and R30 wait
until more people or customers share a deployment, and R11, R13 and R18 wait until they cause a problem.

| # | Risk / limit | Impact | Mitigation in place | Still to do | Sev | Proposed owner | Status |
|---|---|---|---|---|---|---|---|
| R1 | Accuracy is only measured on a synthetic corpus | Real files may expose layouts the mapper handles worse than 0.996 | eval harness, real-file folder and modes ready (ADR-041); first-time rounds always reviewed; verdict shows confidence | collect 20–30 reviewed real exports, write `data/real/eval_baseline.json`, gate on it | H | team lead | open |
| R2 | The downstream contract is a draft example | Rework when the real schema arrives | schema is configuration; semantics decouple code from names; schema diff and preview; unsupported shapes refused | register the real contract, re-run eval, confirm identity keys and required fields with the downstream team | H | approver | open |
| R3 | The AI path is unmeasured | A provider may degrade mappings silently | fallback is the deterministic draft; guard; policy review on fallback; `forma eval --llm` | run `--llm` on the corpus and real files; add a provider-enabled baseline when a key exists in CI | M | admin | open |
| R4 | Formulas are read as cached values, never recalculated | Stale caches in a customer's file produce stale values | missing caches and error cells detected per sheet; formula-bearing sheets force review; guidance to refresh in Excel | none beyond process guidance | M | importer | accepted |
| R5 | Excel semantics outside the data: macros, colours, comments, images, pivots, external links | Business rules carried that way are invisible | documented limit (ADR-043); hidden data included with review | targeted support only if real files need it | L | — | accepted |
| R6 | Layouts without separators, vertical forms, merged data cells, repeated headers | Table detection may miss or merge tables | full-sheet detection with bounded header windows; Studio allows manual table selection | real fixtures | M | — | open |
| R7 | Calibration treats published data as correct | A reviewer's mistake makes precision look higher | proposals need repeated outcomes (trust data checks: 3 with none corrected or rejected; confidence bar: 5 with corrections under a quarter; row change: 2 with none corrected; price move: 2 acknowledged rounds); applies are reversible and audited; the next report shows auto-then-edited | spot-check a sample of published rounds periodically | M | team lead | open |
| R8 | Learning can learn a wrong alias | Later rounds inherit a wrong column | evidence on every learned field says where it came from; forget retires it; suggestions need 3 customers; promotion is manual | — | L | reviewer | mitigated |
| R9 | OIDC not exercised against a real identity provider | Login issues at rollout | unit-tested with RSA keys; PKCE in the SPA; dev mode refused in prod | run against Keycloak/Entra in staging | M | admin | open |
| R10 | Multi-host workers not run in anger | Throughput assumptions may not hold | arq backend, stable job ids, startup reconciliation; benchmark on one node | shared S3 (not MinIO on the same box), pgbouncer sized to workers, load test | M | admin | open |
| R11 | Performance numbers predate the full OOXML scan | Profile stage may be slower than documented | bounded XML (512 MiB), cached inspections | re-run `forma bench` and update PERFORMANCE.md | L | — | open |
| R12 | Provider outage during the night | Mappings fall back to heuristic and go to review | breaker, backup keys, fallback never auto-approves, Ops visibility | — | L | — | mitigated |
| R13 | Queue loss (Redis down) at upload time | Files stuck | stuck detection + Resume; startup reconciliation | continuous monitor (not only at startup) | L | admin | open |
| R14 | Real customer files on an evaluation machine | Confidentiality | `data/real/` git-ignored; harness never uploads | anonymisation procedure before copying | M | team lead | open |
| R15 | Cost of AI calls | Unexpected spend | ledger with estimated cost on Home/Ops; rate limits; sample caps | alert threshold on spend | L | admin | open |
| R16 | Prompt injection through cell contents | A file steers the model | rule 7 in every prompt, guard on every output, read-only tools, confirm-before-act | — | L | — | mitigated |
| R17 | Reviewer accepts a verdict without reading | Wrong approval with a clean audit trail | evidence one click away; calibration counts corrections and rejections, but a later data edit of an accepted mapping stays `accepted_unchanged` | training; spot checks | M | team lead | open |
| R18 | SQLite in dev differs from Postgres in prod | Dialect bugs | CI runs the API flow and migrations on real Postgres (2 tests) and one MinIO test; the rest of the suite uses SQLite; UTC datetimes; JSONB variants | run the full suite on Postgres | M | admin | open |
| R19 | Tenant scope was incomplete: jobs, dashboard, learning statistics, calibration, audit and ops were not filtered by customer, and `DELETE /learning/{id}` had no customer check | A customer-scoped user could see other customers' job rows and global figures | jobs, figures and learning follow the caller's customers; all-customer views need all-customer access (ADR-047) | none | H | admin | closed |
| R20 | Provider response text reaches API responses | Text from a model provider appears in `agent_log` and AI settings results | the assistant path is sanitised | redact `LLMError` text before storing or returning it | M | admin | open |
| R21 | Audit gaps: four early pipeline events logged as `system`; override deletion and job retry not audited | Who did it cannot be answered for those actions | approvals, edits, exports and settings are audited with the actor | pass the actor down; add the two audit rows | M | admin | open |
| R22 | The AI rate limit is skipped when `use_llm` is unset, and is per API process | Default pipeline calls are unlimited; N replicas allow N times the limit | sample caps, queue bound, breaker | apply the limit whenever the model will be used; share the limiter across replicas | M | admin | open |
| R23 | Calibration's `auto_then_rolled_back` outcome never fires: the rollback audit row has no `round_id` | Automatic approvals later rolled back are not counted as wrong | `auto_then_edited` still counts edits | add `round_id` to the `dataset.rollback` audit row | M | admin | open |
| R24 | `block.on_missing_identity` adds a review reason, it does not block | Records with no identity can reach review despite the setting's name | the review reason is high priority; the contract gate still applies | decide: real block, or rename the setting | M | team lead | open |
| R25 | A new target schema version has no baseline: no sandbox or field review for its first round | The first round after a rollout is treated as a first-time format | approved mappings are kept per version; first-time rounds always go to review | decide whether the previous version should act as the baseline | M | team lead | open |
| R26 | The VAT decision on the verdict card could not be saved: it was attached to the price field and returned 422 | The one-click VAT answer failed | the decision is attached to the VAT field and saves from the card (ADR-047) | none | H | admin | closed |
| R27 | Breakers live in each process's memory and the gauge tracks the primary route only | Worker failures in the production stack never reach Ops or `/metrics` | fallback to review still works in the worker | publish breaker state from workers | M | admin | open |
| R28 | A valid OIDC token with no recognised role could call every read that uses `current_principal` | Anyone the IdP authenticated could read in-scope data | an identity with no FORMA role gets 403 everywhere except `GET /me` (ADR-047) | none | H | admin | closed |
| R29 | On an `accept` outcome, the verdict button and endpoint ignored the decisions listed on the card | A `rename_only` verdict with open questions could be approved unanswered | open decisions turn `accept` into `answer`; the endpoint refuses with 409 and the button is disabled (ADR-047) | none | H | admin | closed |
| R30 | A customer-scoped admin can change settings shared by every customer: AI connection, approval policy, default format, customers, target formats | One tenant's admin affects all tenants | all-customer views are closed to them (ADR-047); settings changes are audited | decide whether these endpoints need all-customer access; until then give admin only to all-customer identities | M | admin | open |
| R31 | The compose stack publishes the web port and MinIO on every network interface, and `FORMA_AUTH=dev` has no login | On a machine others can reach, anyone on that network acts with the default role, admin by default, and can read or change customer data | the prod stack needs an explicit setting to run in dev mode; MinIO has its own credentials | publish the ports on the local machine only, or switch on OIDC; confirm from another device | H | admin | open |

## Explicit non-claims

FORMA does **not** claim: general accuracy on unseen real files; understanding of every Excel feature or
arbitrary JSON Schema; exactly-once execution of different job ids targeting the same unfinished round;
infallible uncertainty detection by a model; production throughput without a deployment-specific benchmark.
Every place a number appears says where it came from.
