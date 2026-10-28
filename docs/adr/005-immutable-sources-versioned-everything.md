# ADR-005 — Immutable sources, append-only versions, change sets

**Decision** Uploaded files are stored content-addressed and never modified. Mappings and datasets are
versioned rows with `parent_id`; edits are `ChangeSet`s (operations + impact) that produce a new dataset
version; rollback produces a new version equal to the parent. Three edit kinds are distinct operations:
one-time correction (this round), persistent override (`Override` rules re-applied on future rounds),
mapping change (new `MappingVersion`, affects reprocessing).

**Consequences** Nothing is lost on reprocess; every number in an export can be traced to a file row, a
mapping expression and any human edit (`/datasets/{id}/lineage/{identity}`).
