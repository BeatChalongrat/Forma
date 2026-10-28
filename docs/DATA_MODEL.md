# Data model

```
Customer 1──* ImportRound 1──* SourceFile            (sha256, immutable path, profile, fingerprint, drift)
                 │
                 ├──* MappingVersion   (document, status, confidence, policy decision, agent log, parent)
                 ├──* ReviewTask       (kind, priority, reasons, resolution)
                 └──* DatasetVersion   (records_key → JSONL object in storage, row_count, findings, parent)
                          ├──* ChangeSet   (operations, impact, kind one_time|persistent|copilot)
                          └──* ExportArtifact (fmt, path, sha256)
Customer 1──* Override   (target, where, value — re-applied on every future import)
Job, AuditLog, LLMCall   (operational ledgers)
```

Versioning rules (ADR-005):

* `SourceFile` content never changes; a re-upload of identical bytes is recorded as a duplicate.
* `MappingVersion` is append-only. Approving a version supersedes the customer's other approved versions.
  A human edit in the Studio creates a new *proposed* version with `parent_id`.
* `DatasetVersion` is append-only. Every applied `ChangeSet` and every rollback produces a new version;
  the previous one is marked superseded (or stays approved/published if it was).
* Identity of a record is defined by the target schema (`x-forma.identity_keys`), not by the engine.
* `Override.where` is either `{identity: <sku>}` or a DSL boolean expression over target pointers.

Tables are created with SQLAlchemy `create_all` on startup (SQLite in dev). For Postgres in production,
introduce Alembic migrations before the first schema change that would break existing rows.
