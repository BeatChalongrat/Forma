# Onboarding — your first week on FORMA

> **สรุปภาษาไทย** — เส้นทางสำหรับคนใหม่: วันแรกรันระบบและเดินผ่าน demo, วันสองอ่านกฎ 12 ข้อและ ADR หลัก,
> วันสามไล่โค้ดตามเส้นทางของไฟล์หนึ่งไฟล์, วันสี่เขียน test แรก, วันห้า deploy stack แบบ production ในเครื่อง

## Day 1 — run it and watch a file go through

1. [README.md](../README.md) Quick start: install, synth, seed, api, web.
2. Open Home. Find C902 round 4 (the wrong-file case): read the verdict and the evidence under it, and see what
   the verdict set aside. Find a renamed-layout round (C900–C902 round 3): accept it with one click; look at the audit.
3. Upload files: drop two files from `data/synthetic/*/files`; watch the navy card follow you across pages and the
   batch rows; download the zip.
4. Review a first-time customer: answer the VAT question, preview, save; open the Studio from the evidence.
5. Data Editor: edit a cell, see the impact, roll back; trace a value to its Excel cell.
6. Settings: look at the policy, the calibration report, what FORMA learned, the DSL reference.
7. Round menu: cancel a round and reopen it; park a question for the customer and bring it back.
8. Assistant: attach a file, ask for a chart, open what it makes beside the chat; then read the Memory page.
9. System health: storage used, the last retention pass, the last backup.

## Day 2 — the rules and the decisions

1. The twelve non-negotiable rules: [SYSTEM_ARCHITECTURE.md §10](SYSTEM_ARCHITECTURE.md#10-design-principles-in-one-place-the-non-negotiable-rules).
2. The vocabulary: [GLOSSARY.md](GLOSSARY.md).
3. The ADRs that shape everything: 001 (schema as config), 002 (DSL, AI never executes code in the data path), 005 (immutability),
   006 (fingerprints), 007 (Thai rules), 008 (policy decides), 013 (security), 017 (semantics), 018 (sandbox),
   026 (contract gate), 027 (bulk), 036 (verdict), 038 (learning), 037 (calibration), 043 (input fidelity),
   048–051 (the assistant's sandbox, files, works and memory), 052 (closing a round), 053 (housekeeping and the
   review clock).
   Index: [TEAM_LEAD_HANDBOOK.md](TEAM_LEAD_HANDBOOK.md#5-decision-log-adr-index).
4. The flows as diagrams: [SYSTEM_FLOWS.md](SYSTEM_FLOWS.md).

## Day 3 — follow one file through the code

Read in this order, each with its test next to it:

1. `api/routes.py::upload_files` → `services/rounds.py::add_file` → `ingestion/store.py`
2. `jobs/handlers.py::run_pipeline` → `services/rounds.py::profile_round` → `profiling/`, `fingerprint/`
3. `services/mapping.py::propose_for_round` → `ai/heuristics.py::propose_mapping` → `ai/agents.py::mapping_agent`
   → `services/sandbox.py` → `services/healing.py` → `ai/policy.py::evaluate` → `services/verdict.py::for_mapping`,
   then `services/mapping.py::approve_mapping` → `services/learning.py::record_from_approval`
4. `services/datasets.py::transform_round` → `engine/transform.py` → `services/anomalies.py` → `services/verdict.py::for_dataset`
5. `services/datasets.py::approve_dataset / export_dataset`, then `services/calibration.py::outcomes`

Then the web: `pages/Round.tsx` → `components/RoundSteps.tsx` → `components/VerdictCard.tsx` → `lib/api.ts`.

## Day 4 — make a change the right way

1. Pick a small issue (a Thai date format, a new anomaly kind, a wording in a verdict).
2. Write the failing test first (`backend/tests/`); for Thai rules add a row to the parametrised table.
3. Implement; run `make check` and `make eval-gate`; for a verdict wording bump `RULES_VERSION`.
4. Update the doc the change belongs to (DSL table, Thai rules, ADR implementation notes).
5. Commit with a conventional message on a branch; ff-merge into `main` ([RELEASE_PROCESS.md](RELEASE_PROCESS.md)).

## Day 5 — run it like production

1. `cp .env.production.example .env.production`, fill the secrets, `docker compose … up -d --build`.
2. `curl /ready`; open Ops; kill the worker, watch stale recovery; stop Redis during an upload, use Resume.
3. Read [RUNBOOK.md](RUNBOOK.md) procedures end to end; try backup and restore on the local stack.
4. FORMA runs like this for real: one machine, ports on 127.0.0.1, no login. Do not set `FORMA_BIND_HOST` while
   `FORMA_AUTH=dev` (R31).

## Working agreements

* Never hard-code a target field name. Never let a model execute anything in the data path (the assistant's sandbox
  is the one exception, and its results stop at the chat). Never mutate a source, a mapping
  document or a dataset version in place.
* Every write goes through a service with a `Principal` and declares a role.
* Tests before fixes; docs in the same commit; `make check` before every commit.
* Windows: `scripts\dev.ps1` has no `check` target, so run its `lint` and `test` targets plus
  `ruff format --check .` from `backend\`; backend tools are in `backend\.venv\Scripts\`; set
  `PYTHONIOENCODING=utf-8` when a command prints Thai.

## Who to ask

FORMA has one user today, who holds every role. When more people join, these are the owners.

| Topic | Owner (role) |
|---|---|
| Target contract, export fields | approver / downstream team |
| Policy thresholds, calibration | admin / team lead |
| AI provider, keys, cost | admin |
| Customer onboarding, formats, overrides | reviewer |
| Infrastructure, deploy, backups | admin / platform |
