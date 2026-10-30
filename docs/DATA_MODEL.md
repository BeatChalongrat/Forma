# Data model

> **สรุปภาษาไทย** — ตารางทั้งหมดของระบบ, ความสัมพันธ์, และกฎการเก็บเวอร์ชัน นิยามจริงอยู่ที่
> `backend/src/forma/db/models.py` ทุกการเปลี่ยน model ต้องมี Alembic migration

```
Customer 1──* ImportRound 1──* SourceFile      (sha256, storage key, profile, fingerprint, drift)
                 │
                 ├──* MappingVersion           (document, status, confidence, policy report, agent log, parent)
                 ├──* ReviewTask               (kind, priority, reasons, details, resolution)
                 └──* DatasetVersion           (records_key → JSONL object, row_count, stats, findings, parent)
                          ├──* ChangeSet       (operations, impact, kind one_time|persistent|mapping|copilot)
                          └──* ExportArtifact  (fmt, storage key, sha256)
Customer 1──* Override     (target, where, value; re-applied on every future import)
Customer 1──* Correction   (alias, constant, accepted; promoted rows have no customer)

Batch 1──* BatchItem ──0..1 ImportRound        (one file of a bulk upload; becomes a round once matched)

AssistantConversation 1──* AssistantTurn       (owner + access scope, leases, reply JSON)

Job · AuditLog · LLMCall · AppSetting · StoredSchema   (operational ledgers and runtime configuration)
```

## Tables

| Table | Model | Purpose |
|---|---|---|
| `customers` | `Customer` | code, name, ERP type, default target schema, `policy_overrides` (ADR-037) |
| `import_rounds` | `ImportRound` | one delivery: status, label, target schema, `summary` (drift, decision, verdict, publish, anomaly summary) |
| `source_files` | `SourceFile` | immutable uploaded file: sha256, storage key, profile, fingerprint, drift |
| `batches` | `Batch` | a bulk upload or work round: label, `work_date`, `expected_files`, `auto_publish` |
| `batch_items` | `BatchItem` | one file of a batch: match, stored status, customer, round |
| `mapping_versions` | `MappingVersion` | document, status, confidence, policy report, agent log, open questions, parent |
| `review_tasks` | `ReviewTask` | queue row: kind, priority, reasons, details (risk, verdict brief), resolution |
| `dataset_versions` | `DatasetVersion` | JSONL object key, row count, stats (`invalid_records`), findings, parent |
| `change_sets` | `ChangeSet` | a reviewed batch of edits that produced a new dataset version |
| `overrides` | `Override` | persistent edit re-applied on every future import of the customer (ADR-024) |
| `exports` | `ExportArtifact` | exported file: format, storage key, row count, sha256 |
| `corrections` | `Correction` | what an approval taught: alias, constant, accepted, promoted (ADR-038) |
| `jobs` | `Job` | durable job rows with stages, attempts, request id (ADR-010, ADR-044) |
| `audit_log` | `AuditLog` | actor, action, entity, details for every write |
| `llm_calls` | `LLMCall` | agent, model, tokens, estimated cost, latency, attempts (ADR-032) |
| `app_settings` | `AppSetting` | runtime configuration edited in the web app, secrets encrypted (ADR-017) |
| `target_schemas` | `StoredSchema` | target formats registered in the web app |
| `assistant_conversations` | `AssistantConversation` | chats with owner key, access key, optional round and mapping context, pin, archive, lease (ADR-030, ADR-042) |
| `assistant_turns` | `AssistantTurn` | ordered question and reply with status (ADR-030) |

## Versioning rules (ADR-005)

* `SourceFile` content never changes; a re-upload of identical bytes is recorded as a duplicate.
* `MappingVersion` is append-only. Approving a version supersedes the customer's other approved versions. A
  human edit or saved answers create a new *proposed* version with `parent_id`. A superseded version that was
  once approved stays in the approved-format library (ADR-046).
* `DatasetVersion` is append-only. Every applied `ChangeSet` produces a new version and marks a draft parent
  superseded; an approved or published parent keeps its status. A rollback produces a new draft and leaves the
  version it came from unchanged. Approving a version supersedes the round's other approved or published versions.
* Identity of a record is defined by the target schema (`x-forma.identity_keys`), not by the engine.
* `Override.where` is either `{identity: <sku>}` or a DSL boolean expression over target pointers.

State machines for rounds, mappings, datasets, batch items and jobs: [SYSTEM_FLOWS.md](SYSTEM_FLOWS.md#8-state-machines).

## Schema management

SQLite in development (`create_all` plus a stamp), PostgreSQL in production with mandatory Alembic migrations
(`FORMA_ENV=prod` refuses to start on a pending migration). JSON columns are JSONB on Postgres; every datetime is
UTC-aware. CI checks that the models and the migrations agree (ADR-011).
