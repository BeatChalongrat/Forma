# ADR-017 — Schema-driven mapping and configuration edited in the web app

**Status** accepted · 2026-10

**Context** ADR-001 made the target contract configuration, but the deterministic mapper, the change
report, the copilot and the Data Editor still knew the draft's field names (`/sku`, `/currency`, `/prices`,
`/inventory`, `/promotions`). A real downstream contract with other names produced an invalid mapping and
was blocked; even a hand-fixed mapping exported `source_meta`/`attributes`, which a strict contract rejects.
Operators also had to edit environment files or the database to set an AI key, tune the policy, add a
format or correct a customer.

**Decision**

1. **Fields are found by meaning.** Every place that needs "the price" or "the identity" asks the schema:
   `TargetSchema.semantic_field`, `semantic_pointers`, `identity_field`, `collection_for(kind)`. The
   vocabulary (`schema_registry.SEMANTICS`: `sku`, `price.amount`, `stock.qty`, `promo.type` …) is what a
   new contract tags its fields with; collections are recognised by their own semantic or their items'
   (`price.*` → pricing, `stock.*` → inventory, `promo.*`/`discount.*` → promotions). A flat contract may
   carry `price.amount` on the record itself. Canonical words ('active', 'percent_off') are spelled the
   way the contract's enum spells them.
2. **Exports have exactly the contract's shape.** The engine still records provenance (`source_meta`) and
   unmapped columns (`attributes`) on stored records — lineage and "no data loss" stay — but
   `TargetSchema.export_view` drops them unless the schema declares them, and validation runs on that
   view, so "schema valid" means the export is valid.
3. **A sheet's own table is data.** A promotions sheet with one or two rows is mapped; only small blocks
   *beside* a sheet's main table (legends, footers) are noise, for mapping and fingerprints alike.
4. **Runtime configuration lives in the database, edited in Settings** (admin; formats by approver):
   * `app_settings` rows `ai` (provider, model, gateway, timeout, retries, API key **encrypted** with
     `FORMA_SECRET_KEY`, never returned — only a four-character hint), `policy` (a full policy validated by
     `validate_policy`, layered over `approval.yaml`, resettable), `defaults` (default target format),
     and `revision`;
   * `target_schemas` holds formats registered in the UI, append-only per version; files under
     `target_schema/` remain read-only built-ins. `POST /api/schemas/preview` validates, lists findings and
     diffs against the previous version before anything is stored;
   * `customers.default_target_schema` pins a customer to a format (`catalog` = latest, `catalog@2` =
     pinned); a round's format is: the request's, else the customer's, else the default.
   Every write is audited (policy before/after; never a secret) and bumps `revision`. `ServiceContext`
   rebuilds its registry, policy and LLM client when the revision changes, checked at most every
   `FORMA_CONFIG_REFRESH_S` seconds, so API and workers follow without a restart.

**Consequences** Replacing the draft contract is a UI action plus tagging fields with the vocabulary; a
contract without semantics still maps by synonyms and names, with findings saying so. In production
`FORMA_SECRET_KEY` must be set to save a key in the UI; changing it makes saved keys unreadable (the UI
says so) rather than guessable. Environment variables stay the baseline for everything the UI can edit.
