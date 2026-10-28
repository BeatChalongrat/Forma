# ADR-024 — Spreadsheet editing at scale, and overrides that collide with a new mapping

**Status** accepted · 2026-10

**Context** The Data Editor edited one cell at a time (plus copilot plans). Reviewers fix data the way they
do in Excel: select a block, paste from a spreadsheet, apply one value to many picked rows. And a
persistent override (re-applied on every import) can silently hide what a new mapping changes.

**Decision**

1. **Picked rows.** `set_field`, `scale_field` and `delete_rows` accept `identities: [...]` (combined with
   `where` when both are given). The editor's row checkboxes feed it: set a field, change the price by a
   percent, or delete the picked rows. A *persistent* `set_field` on picked rows becomes one override per
   record, so a later import finds them by identity.
2. **Ranges.** Shift-click / shift-arrows select a block; ⌘C copies it as tab-separated text, ⌘V pastes a
   block from Excel or Google Sheets at the selected cell (typed by each field's JSON type, invalid values
   skipped and reported), Delete clears non-required cells. A paste is one undo step.
3. Everything stays pending until the impact preview and schema validation pass and a person saves a new
   dataset version (unchanged: append-only versions, change sets, rollback).
4. **Override collisions.** When the sandbox (ADR-018) compares a new mapping with the approved one, the
   customer's active overrides are checked against both outputs: the record an override pins is gone,
   the mapping now produces a different value underneath it (the override would hide the change), a rule
   matches other records than before, or the field is no longer filled. The report lists them
   (`override_conflicts`), the Studio's Compare tab shows them, and the policy adds a review reason.

**Consequences** Bulk corrections take one action and still show their full impact before saving. A new
mapping cannot be approved without the reviewer seeing which pinned values it would mask.
