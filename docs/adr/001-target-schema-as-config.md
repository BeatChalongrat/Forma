# ADR-001 — Target schema is versioned JSON Schema configuration

**Status** accepted · 2026-10

**Context** The downstream JSON contract does not exist yet and will change. Hard-coding target fields in
Python types or UI components would make every change a deployment.

**Decision** Target schemas live in `target_schema/<id>.v<n>.json` as JSON Schema 2020-12 with an
`x-forma` extension carrying semantics, synonyms (TH/EN), risk, identity keys and output variants. The
registry (`schema_registry`) is the only way to learn field names; mapping validation, AI prompts, the UI
field catalogue, quality rules and CSV flattening read from it. `SchemaRegistry.diff` reports impact of a new version.

**Consequences** Replacing the draft with the real contract is a file drop + POST. Mappings reference
fields by JSON pointer and are re-validated against the new version. The schema id is also the file name,
so it must be 1–64 characters of `a-z 0-9 _ -` (a schema without one gets a slug of its title).
