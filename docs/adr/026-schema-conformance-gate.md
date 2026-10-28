# ADR-026 — Nothing that breaks the target contract is approved or exported

**Status** accepted · 2026-10

**Context** The engine validated every record against the target schema and listed the failures as
findings, but approving a dataset did not check them, so an export could carry records that break the
contract the downstream system expects. Edits had the opposite problem: a save was refused whenever the
whole dataset had any schema error. With three invalid records, fixing one was refused because two were
still invalid, so a reviewer had to fix every record in one save.

**Decision**

1. **Count.** Every dataset version has `stats.invalid_records`, the number of records that fail the
   schema in their exported shape (`export_view`). The engine counts them while it validates. After
   persistent overrides change records in a transform, the records are validated again. Versions made
   before this change count their validation findings, including the "… N more" line
   (`datasets.invalid_records`). The API serves the count on every dataset version.
2. **Gate.** Approving a dataset with `invalid_records > 0` returns 409, with the count and sample errors.
   Exporting one returns 409 too, which also covers versions approved before the gate existed. No
   acknowledgement overrides it, unlike the anomaly gate (ADR-022): the contract is not a judgement call.
   An invalid record is either fixed or deleted, and the deletion is an audited change set like any other.
3. **Edits are checked for what they add.** A save is refused only for errors it introduces: errors that
   exist after the edit and did not exist on the same record (by identity) before it
   (`datasets.new_schema_errors`). Fixing records one at a time works. Breaking a valid record, or
   replacing one wrong value with another wrong one, is still refused. Only the records that are invalid
   after the edit are validated again on the old side.
4. **Findings follow the records.** A new version's validation findings are recomputed from its records
   instead of being copied from its parent. The round summary's error count follows them.
5. **UI.** The Data Editor header shows "N records break the format". Approve is disabled until it is 0.
   The Findings tab explains the gate, and clicking a schema finding finds its row. The save preview shows
   "records not matching the target format: before → after" and lists only the errors the change would add.

**Consequences** Every exported record conforms to the target schema the dataset was produced for. The
shape was already guaranteed (ADR-017). Now the values are too. Reviewers can repair a bad import
gradually and save each step, but cannot publish it until it is clean. Validation on save covers the whole
dataset, as before; the schema's validator is now built once per schema instead of once per record.
