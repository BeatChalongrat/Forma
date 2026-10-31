# ADR-002 — AI emits a declarative DSL; no generated code is executed

**Scope** Holds for everything in the data path. ADR-048 lets the assistant run code in an isolated sandbox whose
results reach a person, never a record.

**Decision** Every transformation is a tree of whitelisted operations (`mapping_dsl.models.Op`) compiled to
Polars expressions in `engine/ops.py`. LLMs return a `MappingDocument` as structured JSON that is Pydantic-
validated and statically checked against the target schema, with retry-on-error feedback. Unknown ops fail
validation before data is touched.

**Why** Correctness, reproducibility and auditability outrank flexibility. A declarative document is also
what the Mapping Studio edits and what mapping memory stores.

**Consequences** New capabilities = new op + compiler branch + test. Arbitrary Python is never an escape hatch.
