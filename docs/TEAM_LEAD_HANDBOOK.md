# FORMA — Team lead handbook

> **สรุปภาษาไทย** — จุดเริ่มต้นสำหรับหัวหน้าทีม: ระบบคืออะไรในหนึ่งหน้า, เอกสารแต่ละฉบับอยู่ไหนและใช้ตอนไหน,
> ตัวชี้วัดที่ต้องดู, ความเสี่ยงที่เปิดอยู่, สิ่งที่ต้องตัดสินใจ, และสารบัญ ADR ทั้ง 58 ฉบับแบบบรรทัดเดียว

## 1. One page

FORMA converts ~300 customers' fortnightly ERP exports (any layout, Thai/English, multi-sheet) into one
validated JSON contract. It reads and profiles every file, decides what it means (memory → approved-format
library → deterministic mapper → optional AI, all emitting a declarative DSL), checks the new mapping against
the approved one on real data, lets a configurable policy decide `auto_approve | review | block`, states a
**verdict** per file with evidence and the decisions only a person can make, transforms deterministically,
gates on the contract, learns from every correction, calibrates its own policy from outcomes, expects the
next delivery per customer, and keeps everything immutable, versioned and audited.

Built: phases 0–8 (see [ROADMAP_STATUS.md](ROADMAP_STATUS.md)). It runs for one person on one machine, with no
server. Not yet established: accuracy on real files and the real downstream contract
([RISK_REGISTER.md](RISK_REGISTER.md)). OIDC and multi-host workers matter only if FORMA moves to a server.

## 2. The documents and when to use them

| When you need to… | Read |
|---|---|
| explain the product in one sitting | [README.md](../README.md) |
| explain a word the team uses | [GLOSSARY.md](GLOSSARY.md) |
| understand or review the architecture | [SYSTEM_ARCHITECTURE.md](SYSTEM_ARCHITECTURE.md) (long), [ARCHITECTURE.md](ARCHITECTURE.md) (short) |
| walk a stakeholder through a flow, or write a test plan | [SYSTEM_FLOWS.md](SYSTEM_FLOWS.md), [USER_STORIES.md](USER_STORIES.md) |
| know who may do what, and how the system is secured | [RBAC_AND_SECURITY.md](RBAC_AND_SECURITY.md) |
| find an endpoint | [API_OVERVIEW.md](API_OVERVIEW.md), `/api/docs` |
| understand the mapping language or the data model | [MAPPING_DSL.md](MAPPING_DSL.md), [DATA_MODEL.md](DATA_MODEL.md), [THAI_CONTEXT_RULES.md](THAI_CONTEXT_RULES.md) |
| judge quality and release readiness | [TESTING_STRATEGY.md](TESTING_STRATEGY.md), [EVALUATION.md](EVALUATION.md), [PERFORMANCE.md](PERFORMANCE.md) |
| ship a change | [RELEASE_PROCESS.md](RELEASE_PROCESS.md), [CONTRIBUTING.md](CONTRIBUTING.md) |
| run it, handle an incident | [RUNBOOK.md](RUNBOOK.md) |
| measure the product | [KPIS_AND_METRICS.md](KPIS_AND_METRICS.md) |
| see what could go wrong | [RISK_REGISTER.md](RISK_REGISTER.md) |
| bring someone in | [ONBOARDING.md](ONBOARDING.md) |
| know why something is the way it is | [adr/](adr/) — the decision log below |

## 3. Decisions waiting for the team lead

1. **The real downstream contract** — who owns the JSON Schema, when it arrives, identity keys and required
   fields. Everything downstream-facing depends on it (R2).
2. **Real files for evaluation** — 20–30 reviewed exports (anonymised) and who signs off the expected output;
   alternatively, build the from-database evaluation that uses published rounds as the answer key (R1).
3. **Automation appetite** — which calibration proposals to accept first (`trust_data_checks`, per-customer
   thresholds), and the review cadence for the precision report.
4. **AI provider and budget** — provider, model, backup keys and monthly cap.
5. **Retention** — how long source files, datasets and exports are kept; when to switch retention from dry-run
   to apply, which needs R37 settled first.
6. **Copies off this machine** — the repository has no remote and the backups sit on the same disk, so losing the
   machine loses all of it (R36). It takes a private remote, and a copy of the newest backup and `.env.production`
   somewhere else.

Before any production use, the open items in the risk register need a decision, first R36 (losing this machine loses the code, the data and the backups), then R30 (a customer-scoped admin can change shared settings) and R20–R22.

## 4. Operating rhythm

| Cadence | What |
|---|---|
| Daily (5 min) | Ops page: queue, failed jobs, breaker, spend; `/ready`; oldest review; backup age |
| Weekly | KPIs: automation rate, reviews per 100 files, precision per rule, corrections per approval; expected deliveries overdue |
| Per release | gates green, eval and bench deltas, risk register, runbook changes, downstream-visible changes announced |
| Monthly | calibration proposals and their effect, cost per round, real-file baseline progress, ADR review |

## 5. Decision log (ADR index)

| ADR | Decision |
|---|---|
| 001 | The target schema is configuration (JSON Schema + `x-forma`), never code |
| 002 | AI emits a declarative Mapping DSL; the engine executes; no generated code runs |
| 003 | Excel reading engines: fastexcel for speed, CSV sniffing; structure facts later moved to the OOXML scan (ADR-043) |
| 004 | Record identity comes from the schema's identity keys |
| 005 | Immutable sources; append-only versions; edits are change sets; rollback is a new version |
| 006 | Fingerprints, drift classification, mapping memory, wrong-file detection |
| 007 | Thai context rules are explicit, tested operations |
| 008 | Policy-gated automation: auto_approve / review / block with reasons; blocks are explicit overrides |
| 009 | Job runner and workflow stages |
| 010 | Job backends (thread, process, arq); durable, idempotent, resumable jobs |
| 011 | Postgres + Alembic migrations; JSONB; UTC |
| 012 | Object storage behind one protocol; keys on rows; pre-signed downloads |
| 013 | Auth (dev/OIDC), RBAC, tenancy, upload sniffing, LLM output guard, rate limits |
| 014 | Observability: traces, Prometheus, JSON logs, health, Ops page |
| 015 | Reliability: retries, circuit breaker, atomic versions, retention, backup/restore |
| 016 | Deployment: images, compose, generated config example, CI gates |
| 017 | Schema-driven mapping by semantics; runtime configuration edited in the web app |
| 018 | Mapping simulation sandbox before a mapping replaces the approved one |
| 019 | Trust levels on every decision; measured relationships and visual multi-sheet mapping |
| 020 | Explainable lineage to the Excel cell with the cause of a change |
| 021 | Self-healing field review checked on the data (same / VAT / scaled / different) |
| 022 | Anomaly detection with an approval gate on high severity |
| 023 | Data repair copilot: conditions across collections, answers from evidence |
| 024 | Spreadsheet editing and override collisions |
| 025 | Backup AI keys with per-route breakers and model choice |
| 026 | Schema conformance gate: invalid records block approval and export |
| 027 | Bulk upload, customer by layout, one JSON per file, automatic publishing |
| 028 | Focused, responsive operations workspace |
| 029 | Guided review (questions as new versions), preferences, workspace assistant with read-only tools, MCP |
| 030 | Persistent assistant conversations with leases and access scope |
| 031 | Chat actions and a neutral dark theme |
| 032 | Quiet header; honest estimated-cost labels |
| 033 | Reusable work rounds with dates and appended files |
| 034 | Receipt dashboard; cancellable streamed replies |
| 035 | Evidence-first file review traced to source cells |
| 036 | The verdict layer: one conclusion per file, one-click accept through the policy |
| 037 | Policy calibration from outcomes; proposals an admin applies; per-customer overrides |
| 038 | Learning from corrections: aliases, constants, promotion across customers |
| 039 | Proactive intelligence: expected deliveries, watchlist, per-customer price baselines |
| 040 | The round page is the case; the assistant proposes a confirmable action |
| 041 | Evaluation on real files, with the AI on, and with corrections replayed |
| 042 | Bilingual explanations of review evidence; the assistant asks before assuming |
| 043 | Input contract readiness: full OOXML scan, strict CSV, unsupported schema shapes refused, full schema in prompts |
| 044 | Durable queue reconciliation before recovery |
| 045 | Server-computed reports (tables, charts) in assistant conversations |
| 046 | Approved mapping library across a customer's ERP layouts |
| 047 | Verdict decisions, accounts with no role, and tenant scope beyond owned rows |
| 048 | The assistant runs code in an isolated sandbox |
| 049 | Images and files in an assistant chat |
| 050 | What the assistant makes opens beside the chat, with its versions kept |
| 051 | The assistant remembers a person across chats |
| 052 | Closing a round without deleting it: cancel, move, replace |
| 053 | Housekeeping you can see, and a review queue with a clock |
| 054 | A backup holds everything, and has been restored |
| 055 | Four files that went wrong quietly: printed reports, a second price sheet, currency, date order |
| 056 | Six values that changed quietly: reused mappings checked, conflicting duplicates, enum defaults, number forms, the exported version |
| 057 | What the first real AI run taught: op params in the prompt and validator, drift per table, year 9999, commit before the response |
| 058 | The agent checks its own work: inspect the file, run the mapping on the first rows and fix it, plausibility checks, section rows, choice questions |

## 6. Numbers to quote (and where they come from)

| Claim | Number | Source |
|---|---|---|
| Field accuracy on the synthetic corpus, no AI | 0.996 (30/30 variants ≥ 0.99); identity 1.00; critical 1.00; drift 9/9 | `make eval-gate`, `data/eval_baseline.json` |
| A 300-file night | 17 min, 8 worker processes, 12-core node, 30M rows | PERFORMANCE.md (pre-ADR-043; re-measure) |
| Tests | 731 passed, 5 skipped locally, coverage 91 % (gate 85 % in CI) | `pytest`, CI |
| Endpoints / tables / ADRs | ~126 / 23 / 58 | API_OVERVIEW.md, DATA_MODEL.md, adr/ |

Anything else — accuracy on real files, provider accuracy, production throughput — is not yet measured and
should be said so.
