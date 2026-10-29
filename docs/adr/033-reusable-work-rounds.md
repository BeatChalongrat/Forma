# ADR-033: Reusable work rounds and stable history refresh

Status: accepted; extends ADR-027 and ADR-029.

A work round is an existing Batch with an editable name and optional work_date.
The operator chooses this date; it is a label, not an upload timestamp. Migration
c73d901e820a adds a nullable indexed date without guessing dates for existing batches.
Source and per-customer processing rounds remain immutable and independent.

The Imports landing page lists work rounds with server-side name/date filters and
pagination. The All files view retains actual upload-date filters and existing deep
links. A work round shows all its items, status filters, and 50 rows per page.
Existing rounds can be renamed/dated and can accept more files later; their approval
policy is retained. Processing settled (done) and all files ready (complete) are distinct.

Appending requires importer role and all-customer access, matching bulk-upload scope.
The append transaction locks the batch before checking normalized filename plus SHA256
against existing items. Identical files are skipped; different contents create new items.
Every append and label edit records the actor in the audit log. Only newly accepted valid
items are dispatched. This is retry deduplication within a known round, not global exactly-once
delivery. Existing resume handles stored files that could not be queued.

The browser sends sequential chunks of at most 10 files and normally 20 MiB per request;
a single larger file still uses the server's existing per-file limit. On a failed later
chunk the selected round and file list remain available for retry. Losing the first create
response can still leave a newly created round to locate in history. Hard refresh does not
retain local File objects. No automatic destructive replacement of earlier files is added.

History refresh preserves rows and reserves space for its refresh indicator, eliminating
the loading-banner layout jump. Search is debounced. Work-round detail polls periodically
even after processing settles so later appended files and reviewed results appear.

Capacity: Redis/arq, PostgreSQL, S3-compatible storage and workers already exist. This change
does not add infrastructure or claim a 300-file AI throughput benchmark. Next operational
work should address durable dispatch reconciliation, per-worker heartbeat and stale jobs,
provider concurrency/rate limits, and realistic load testing before adding API replicas.
Cache only versioned derivations; approvals and changed source/schema/policy require revalidation.

Validation covers append/retry/date filters/label edits, scoped access, migration behavior,
and 300 stored files dispatched once across three batches with processing mocked. Existing
batch pipeline tests cover matching/review/export. Frontend build/lint and live browser checks
cover work-round selection, filters, themes, responsive layout, and stable polling geometry.

Observed on localhost: 42 history rows remained visible through two ten-second refreshes;
table top stayed at 472.0875 px. Previously the loading banner shifted the table by 46.4 px.
At a 390 px viewport the work-round list and upload form had no document-level horizontal
overflow. The targeted batch/migration/workspace suite passed 29 tests. Production data was
backed up before the nullable-date migration; browser checks did not modify existing round labels.
