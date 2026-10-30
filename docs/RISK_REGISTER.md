# Risk register and known limits

> **สรุปภาษาไทย** — รายการความเสี่ยงและข้อจำกัดที่รู้อยู่แล้ว พร้อมผลกระทบ สิ่งที่ลดความเสี่ยงไว้แล้ว และสิ่งที่ยังต้องทำ
> อัปเดตทุกครั้งที่ปิดหรือเปิดความเสี่ยงใหม่ เจ้าของ = บทบาทที่ควรรับผิดชอบ

Severity: H high · M medium · L low. Status: open · mitigated · accepted.

| # | Risk / limit | Impact | Mitigation in place | Still to do | Sev | Owner | Status |
|---|---|---|---|---|---|---|---|
| R1 | Accuracy is only measured on a synthetic corpus | Real files may expose layouts the mapper handles worse than 0.996 | eval harness, real-file folder and modes ready (ADR-041); first-time rounds always reviewed; verdict shows confidence | collect 20–30 reviewed real exports, write `data/real/eval_baseline.json`, gate on it | H | team lead | open |
| R2 | The downstream contract is a draft example | Rework when the real schema arrives | schema is configuration; semantics decouple code from names; schema diff and preview; unsupported shapes refused | register the real contract, re-run eval, confirm identity keys and required fields with the downstream team | H | approver | open |
| R3 | The AI path is unmeasured | A provider may degrade mappings silently | fallback is the deterministic draft; guard; policy review on fallback; `forma eval --llm` | run `--llm` on the corpus and real files; add a provider-enabled baseline when a key exists in CI | M | admin | open |
| R4 | Formulas are read as cached values, never recalculated | Stale caches in a customer's file produce stale values | missing caches and error cells detected per sheet; formula-bearing sheets force review; guidance to refresh in Excel | none beyond process guidance | M | importer | accepted |
| R5 | Excel semantics outside the data: macros, colours, comments, images, pivots, external links | Business rules carried that way are invisible | documented limit (ADR-043); hidden data included with review | targeted support only if real files need it | L | — | accepted |
| R6 | Layouts without separators, vertical forms, merged data cells, repeated headers | Table detection may miss or merge tables | full-sheet detection with bounded header windows; Studio allows manual table selection | real fixtures | M | — | open |
| R7 | Calibration treats published data as correct | A reviewer's mistake makes precision look higher | proposals need ≥ 3 matching outcomes and zero contradictions; applies are reversible and audited; the next report shows auto-then-edited | spot-check a sample of published rounds periodically | M | team lead | open |
| R8 | Learning can learn a wrong alias | Later rounds inherit a wrong column | evidence on every learned field says where it came from; forget retires it; suggestions need 3 customers; promotion is manual | — | L | reviewer | mitigated |
| R9 | OIDC not exercised against a real identity provider | Login issues at rollout | unit-tested with RSA keys; PKCE in the SPA; dev mode refused in prod | run against Keycloak/Entra in staging | M | admin | open |
| R10 | Multi-host workers not run in anger | Throughput assumptions may not hold | arq backend, stable job ids, startup reconciliation; benchmark on one node | shared S3 (not MinIO on the same box), pgbouncer sized to workers, load test | M | admin | open |
| R11 | Performance numbers predate the full OOXML scan | Profile stage may be slower than documented | bounded XML (512 MiB), cached inspections | re-run `forma bench` and update PERFORMANCE.md | L | — | open |
| R12 | Provider outage during the night | Mappings fall back to heuristic and go to review | breaker, backup keys, fallback never auto-approves, Ops visibility | — | L | — | mitigated |
| R13 | Queue loss (Redis down) at upload time | Files stuck | stuck detection + Resume; startup reconciliation | continuous monitor (not only at startup) | L | admin | open |
| R14 | Real customer files on an evaluation machine | Confidentiality | `data/real/` git-ignored; harness never uploads | anonymisation procedure before copying | M | team lead | open |
| R15 | Cost of AI calls | Unexpected spend | ledger with estimated cost on Home/Ops; rate limits; sample caps; narrative only on open | alert threshold on spend | L | admin | open |
| R16 | Prompt injection through cell contents | A file steers the model | rule 7 in every prompt, guard on every output, read-only tools, confirm-before-act | — | L | — | mitigated |
| R17 | Reviewer accepts a verdict without reading | Wrong approval with a clean audit trail | accept is only offered when the verdict's outcome is accept and no decision remains; evidence one click away; calibration shows auto/accepted-then-edited | training; spot checks | M | team lead | open |
| R18 | SQLite in dev differs from Postgres in prod | Dialect bugs | CI runs the suite on real Postgres; UTC datetimes; JSONB variants | — | L | — | mitigated |

## Explicit non-claims

FORMA does **not** claim: general accuracy on unseen real files; understanding of every Excel feature or
arbitrary JSON Schema; exactly-once execution of different job ids targeting the same unfinished round;
infallible uncertainty detection by a model; production throughput without a deployment-specific benchmark.
Every place a number appears says where it came from.
