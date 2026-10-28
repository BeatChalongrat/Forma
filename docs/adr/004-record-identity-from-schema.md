# ADR-004 — Record identity and composite keys are schema configuration

**Decision** `x-forma.identity_keys` on the root (default `/sku`) and per collection (e.g. prices by
price_list + effective_from, inventory by warehouse_code) define identity. The engine dedupes, links
collections, diffs rounds and applies overrides using these keys; the mapping may override dedupe keys per
customer when a SKU is only unique together with a warehouse or price list.

**Consequences** Switching identity later is a schema change, not an engine change; change intelligence
and overrides stay correct because they key on the same definition.
