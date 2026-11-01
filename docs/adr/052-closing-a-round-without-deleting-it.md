# ADR-052 — Closing a round without deleting it: cancel, move, replace

**Status** accepted · 2026-11 · builds on ADR-005 and ADR-008

## Context

Every path through FORMA went forward. A test file uploaded by mistake stayed in the history and in the
work round's counts for ever. The verdict could say "this file is probably C901's, not C902's" (B5), but
offered no way to act on it: the person uploaded the file again under the right customer and the wrong
round stayed open. A corrected delivery became a second round with nothing tying it to the first, so the
work round's zip held both JSONs. A round whose question the customer never answered sat in the review
queue indefinitely. There was no route that deleted, cancelled or moved anything, by design (ADR-005), and
retention never touches an open round, so none of these ever went away.

## Decision

1. **Three closed states, no deletion.** A round can become `cancelled`, `moved` or `superseded`. Its files,
   versions, tasks and audit rows stay. It leaves the review queue (its open tasks are set aside as
   `dismissed`), the history list, foresight's cadence, and the work round's zip. Retention treats a closed
   round like a published one.
2. **Every close has a reason and an author.** `closed_from`, `closed_by`, `closed_at` and `closed_reason`
   are columns on the round, and `round.cancel`, `round.move` and `round.replace` are audit actions.
3. **Cancel can be undone; move and replace cannot.** `reopen` restores a cancelled round to the status it
   had and brings its tasks back. A moved or replaced round has a successor that took its place, so it stays
   closed; the successor's page links back.
4. **Move reuses the stored file.** The new round of the other customer gets `SourceFile` rows that point at
   the same storage key, marked as duplicates of the originals, and the pipeline runs on it at once. Nothing
   is copied.
5. **Replace is an upload against an old round.** The corrected files make a new round of the same customer
   with `predecessor_id` set; the old one becomes `superseded` with `successor_id`. In a work round the
   file's row follows the file to the new round, so the zip and the counts show what is current.
6. **The UI asks before it closes.** Each action is a dialog that states the reason field and what will
   happen, reached from a menu on the round page and on a work round's file row. The verdict's "move to the
   right customer" opens the move dialog with the matched customer selected. Closed rounds are hidden from
   the history by default and shown under one filter.

## Consequences

* An approved mapping of a round that is later cancelled stays approved: approval is a fact about the
  layout, not about the delivery. If the approval itself was wrong, reject the mapping.
* A moved round's successor runs the ordinary pipeline for its new customer, so a first-time customer still
  gets a review, and a known layout passes on its own.
* Replacing a published round withdraws its JSON from the zip immediately, before the new JSON exists. The
  work round's counts say so.
* The combine-files page asks whether a file replaces a round from the last seven days. Bulk upload does not,
  because one upload spans many customers; the replace action on the old round covers that case.

## How it was checked

`test_round_lifecycle.py` covers cancel and reopen (queue, history, audit, double cancel), that only a
cancelled round reopens, move (same bytes, duplicate marks, pipeline run, old round closed), replace in a
work round (zip holds only the new JSON, counts follow the file) and a cancelled work-round file counting as
closed.
