# ADR-018 — Mapping simulation sandbox: a new mapping runs next to the approved one

**Status** accepted · 2026-10

**Context** The most expensive failure in FORMA is a wrong mapping: one bad expression makes every record
of a customer wrong. A proposal was simulated on its own (findings, sample records), but nobody saw what
it would change compared with the mapping already trusted for that customer, and nothing stopped a
mapping that silently dropped records or prices.

**Decision**

1. Whenever a new mapping is proposed for a customer that has an approved one — a re-proposal after drift,
   an AI adaptation, or a human edit in the Studio — FORMA runs **both** on the round's real files
   (`FORMA_SANDBOX_MAX_ROWS` rows per source, 0 = all) and compares: output rows, errors, records
   without a valid price (`price.amount` by semantic), duplicates, rows without identity, records lost and
   gained, every changed record and every changed price with its percentage. A verbatim memory reuse is
   not compared (nothing changed).
2. The **policy decides** (`policy.sandbox`, editable in Settings): records lost, extra errors, extra invalid
   prices or extra duplicates beyond their thresholds → `block` with the reasons (approval then needs an
   approver and a written reason, ADR-008); more changed prices than `max_changed_prices_auto` → review,
   never automatic.
3. The report summary lives in the mapping version's `policy.sandbox`; the drill-down lists (up to 5,000
   rows each) are written to object storage under `sandbox/<customer>/<mapping>.json` **before** the
   mapping row that points at them. `GET /api/mappings/{id}/sandbox` pages through them;
   `POST /api/mappings/{id}/compare` compares unsaved Studio edits on demand without storing anything.
4. The Studio's **Compare** tab shows the side-by-side table and the lists (changed prices, changed
   records, missing, new); the Round page shows the headline.

**Consequences** A mapping that would make the data worse cannot reach publication without a named human
accepting it, and reviewers see exactly which SKUs and prices change before approving. Each comparison
costs two engine runs of the round (bounded by `FORMA_SANDBOX_MAX_ROWS`); returning customers with an
unchanged format pay nothing (memory reuse is not compared).
