# ADR-053 — Housekeeping you can see, and a review queue with a clock

**Status** accepted · 2026-11 · builds on ADR-015, ADR-039 and ADR-052

## Context

Retention (ADR-015) was written and never switched on: `FORMA_RETENTION_INTERVAL_H` defaulted to 0 and the stack
did not set it. Nothing said so, because the System health page knew neither when retention last ran nor how
much storage was in use. It also missed three things: files in a work round that never became a round, resolved
review questions, and rounds that simply stopped. Backups were a command somebody had to remember.

The review queue had the same blind spot. It sorted by age but showed none, so a question nine days old looked
like one from this morning. A question waiting for the customer sat in the person's queue with no way of saying
whose turn it was, which trained the eye to skip the queue.

## Decision

1. **Every pass is recorded.** `retention.run` plans, optionally deletes, and writes a `retention.run` audit row with
   the counts. The CLI and the schedule both go through it. `forma backup` writes `backup.create`.
2. **System health shows the housekeeping.** Storage used (walked at most every five minutes, because listing a
   bucket is slow), the last retention pass and what it did, and the age of the last backup, amber past a day.
3. **Retention covers more, and still never closes work.** Files that never became a round go after
   `keep_days_unassigned`, keeping their bytes while any round or newer file uses them, and leave a `purged` row.
   Resolved questions go after `keep_days_resolved`, except those of a cancelled round, because reopening it
   brings them back. A file still waiting for a customer keeps its bytes even when a closed round shares them.
4. **An idle round is flagged, never closed.** A round with no activity for 60 days appears on the home page.
   Only a person closes a round (ADR-052).
5. **The schedule starts soon and runs once.** The first pass comes a minute after start rather than a whole
   interval later. The API and the worker both carry it, so a pass is skipped when one ran in the last half
   interval.
6. **Backups run themselves.** The `backup-nightly` service dumps the database every 24 hours and keeps 14 days.
7. **A question knows how old it is.** The queue and the home page show its age; past seven days it is called out,
   and foresight lists it.
8. **A question can wait for the customer.** It takes a note and a date, leaves the person's queue and every count,
   and comes back on that day by itself, or sooner by hand. Reading the queue or the dashboard wakes what is due, so
   there is no timer to fail. An approval, a rejection or a closed round settles a waiting question like an open
   one. When the customer's answer is due, foresight says so.

## Consequences

* A round reopened after retention removed its files is refused: there is nothing to run.
* Waiting is about whose turn it is. It decides nothing about the file, and the policy never sees it.
* Waking on read writes during a GET. It is idempotent and small, and the alternative was a scheduler for one
  field.
* Chat attachments stay out of retention: they belong to a person and leave with their chat.
* `backup-nightly` dumps the database only. Objects rely on the bucket, as the `backup` service already assumed.

## How it was checked

`test_review_queue.py` covers the age, waiting and bringing back, waking on the day, settling a waiting question by
approval and by cancelling, a customer's answer falling due, and an idle round being flagged and left alone.
`test_retention_backup.py` covers the recorded pass and the health page, unassigned uploads and old answers, and a
file waiting for a customer keeping its bytes.
