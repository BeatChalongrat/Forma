# ADR-027 — Bulk upload by format, one JSON per file, automatic publishing

**Status** accepted · 2026-10

**Context** An operator receives files from many customers and had to create a round per customer, pick the
customer, upload, then walk each round through mapping, data approval and export. Even when a returning
customer's format was identical and its data clean, a person had to approve the data and press export. The
goal is to drop all the files at once, let the system work out whose each file is, and get one JSON per file
without a person whenever nothing needs one.

**Decision**

1. **Upload many files at once** (`POST /api/batches`, the **Upload files** page). Each file is stored
   (content-addressed) the moment it arrives, as a `BatchItem` of a `Batch`, before its customer is known.
   One `process_batch_item` job per file does the rest, so files are handled in parallel by the workers.
2. **Whose file is it?** (`services/batches.match_customer`). The file is profiled and its structure compared
   with every customer's latest approved format:
   `exact` (same sheets and ordered headers) → `same_columns` (same headers, moved) → `similar` (the closest
   format, at least 75 % alike and 10 points ahead of the next customer — drift such as a renamed column then
   goes through self-healing review, sandbox and policy as usual). A customer code in the file name (as a
   word: `สินค้า_ต.ค.69_C101.xlsx`) confirms the match, settles a format several customers share, and names
   the customer of a format nobody has yet (`file_name`). **When name and format disagree, or nothing
   matches, the file waits for a person** (`needs_customer`) — two customers exporting the same ERP template
   must never be published as each other. The person picks an existing customer or creates one
   (`POST /api/batches/{id}/assign`); the file then continues on its own.
3. **One file → one round → one JSON.** A matched file becomes a one-file round of its customer with
   `summary.auto_publish`, and runs the usual pipeline. A delivery made of several files that belong together
   (products.csv + stock.csv) still uses the single-delivery import.
4. **Automatic publishing — the policy decides** (rule 6). After the data is created, `PolicyEngine.
   evaluate_publish` approves and exports it as JSON only when every check passes: the mapping is approved
   (by policy, or by a person), there are records, every record matches the target format (ADR-026), no engine
   errors (`publish.max_error_findings`, default 0) and no more high-severity anomalies than
   `publish.max_high_anomalies` (default 0). Otherwise the round waits at "check the data" with the reasons
   shown. Every decision, either way, is stored on the round (`summary.publish`) and in the audit log. The
   policy's `publish.enabled` turns it off for everyone; the upload's own option turns it off per upload.
5. **A person's decision continues the flow.** Approving the mapping of such a round re-runs the pipeline
   (create data → publish decision); approving its data writes the JSON at once.
6. **Where each file stands** (`GET /api/batches/{id}`): step 1 match → 2 mapping → 3 create data → 4 check →
   5 JSON, with a state (working, unknown format, check mapping, check data, JSON ready, failed) and the next
   action. `GET /api/batches/{id}/download` returns a zip with one JSON per finished file, named after it.
7. **Nothing is lost when the queue is not there.** Files are committed before their jobs are queued; a file
   waiting with no job queued or running for it (queue full, Redis down at upload time) is reported as
   `stuck`, and `POST /api/batches/{id}/resume` queues it again (the job is idempotent). Jobs are submitted
   off the request's event loop, which the arq backend needs.
8. A bulk upload matches files against every customer's formats, so customer-scoped users cannot run one.

**Consequences** Returning customers with an unchanged format and clean data go from upload to JSON with no
click. New formats, drifted formats with doubtful data, invalid records and unusual data still stop for a
person, now with the reason and a button to the right page. New tables `batches` and `batch_items`
(migration `6d0cdb26a7b6`).
