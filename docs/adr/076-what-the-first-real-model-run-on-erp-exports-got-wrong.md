# ADR-076: What the first real-model run on ERP exports got wrong

Status: implemented, 2026-11-21; tested with a scripted model, and sent to the real model twice more that
day: all eleven files, then the six SAP files after the two fixes the second run called for (see "The second
run" and "The third run").

## Context

On 2026-11-20 the eleven export files of `backend/tests/test_erp_export_formats.py` (SAP, NetSuite, Oracle
OTBI; six products each) were run through the configured model (`deepseek-flash`, graph workflow), each as
a new customer's first file, into a throw-away SQLite database.

| Measure | Result |
|---|---|
| Field values right | 408 of 420 (97.1%); 78.1% without a model |
| Decisions a person must make that were asked | 16 of 16 |
| Lines in `open_questions` | 39, of which 14 repeat another and 6 ask what the file shows |
| Files that pass the contract before any answer / after one VAT answer | 2 of 11 / 11 of 11 |
| Reviewer answers the guard accepted | 8 of 11 |
| Provider attempts per file | 6 to 14, about 28,000 input tokens each |
| Time per file | 158 to 533 seconds |

Nothing was approved automatically. Nine things were wrong:

1. One mapping fixed the table's last row (`data_end=10`). On a file of twelve products it reads six and says
   nothing.
2. When the reviewer's answer was rejected, the card said "AI mapping unavailable; heuristic draft used" and
   `source: heuristic`. The mapping on the card was the model's.
3. The matcher's sentence about a field stayed beside the model's question about the same field. The VAT
   sentence said "assumed VAT-inclusive" beside a flag the model had left empty.
4. On SQLite the finished mapping was thrown away: "Worker lease lost".
5. SAP's `31.12.9999` left as `9999-12-31` in two files, and the matcher asked whether dates are day-first.
6. Six SAP files hold the same rows. The model left the VAT flag empty in four and wrote `true` in two.
7. SAP's `per` (the number of units the amount is for) was mapped to the pack size in two files.
8. The reviewer had no accepted way to say "this is already asked". It ordered a repair, or failed.
9. The mapper was sent round twice in every file to fix the errors its own empty VAT flag causes.

## Decision

### Row limits a model writes down

- After a model returns a mapping, each source is read with and without its `data_end` and `data_start`. A
  limit that leaves every cell the same is removed (`services.mapping.release_row_bounds`). The matcher
  already writes none: row counts change every round.
- A limit that does cut rows stays. The engine now reports data-like rows past it as a `source_review`
  finding, `rows_after_data_end`, by the rule used for rows after a blank gap (ADR-072). A reused mapping
  cannot clear it.
- A table the engine found in the file itself has no fixed limit; `extract_table(detected=True)` says so.
- Column limits are not released: a note typed beside the table next month must stay outside it. But SAP's
  "Text with Tabs" starts every line with a tab, and the model starts the table at column 1 where the matcher
  says 0 and drops the empty column. A column range that reads the same cells as the matcher's for the same
  sheet takes the matcher's spelling, so the two readings are compared by what they read.

### Who wrote the mapping

A workflow failure before the mapper finishes leaves FORMA's draft in use: `llm_failed`, as before. A failure
after it leaves the model's mapping without its review: `review_failed`. The card then says `source: ai`,
`checks.ai_review_failed`, and "AI wrote this mapping but its own review step failed". It is never approved
automatically, and the matcher's reading is still compared with it.

### One prompt for one decision

- A matcher sentence is merged into a structured question that reaches the card for the same field: the VAT
  sentence when every mapped VAT flag has one, the warehouse sentence when the model asks to confirm the same
  column (`services.mapping.one_question_per_decision`). The log entry `question_reconciliation` lists what
  was merged.
- A VAT sentence that stays beside an empty flag is reworded: "nothing is assumed".
- An answer that makes a record valid may expose the contract's default in a price row (the VAT rate). The
  guard on answers allowed this for root fields only; it now allows it in collections under the same rule:
  absent before, exactly the default after.

### SQLite and the lease

SQLite has one write lock for the file. The mapping transaction holds it while the model thinks, so the
heartbeat cannot renew the lease, and no other worker can take the job either, because taking it is a
write. At commit, an expired lease whose row still carries this worker's token is renewed in the same
transaction (`jobs.execution._renew_as_only_writer`). A database with row locks keeps the strict rule:
there the heartbeat is never blocked, so an expired lease means a dead one.

### An ERP's "no end date"

- A date field's value with a year from 9000 on leaves as null whatever expression produced it
  (`engine.transform._date_text`). `parse_date_th` already read it so (ADR-057); a constant or a condition a
  model wrote around it brought the sentinel back.
- The profiler takes `31.12.9999` for a date. Its 31 shows the file writes the day first, so the question
  about date order is not asked.
- The mapper is told that the sentinel already reads as null and needs no condition and no question.

### A VAT flag nothing states

A VAT flag a model wrote as `true` or `false` is reopened (`services.mapping.leave_unproven_vat_open`) when
all of these hold: it is the customer's first mapping; the matcher found no VAT marker in the headers and no
taught rule; the operator's requirements do not mention VAT; no remembered decision covers the field; no cell
of the sources says VAT, tax, ภาษี or มูลค่าเพิ่ม. The records then fail the contract until a person answers.

### A field that waits for an answer

A field left empty for a person fails the contract on every record. That is the state the model is told to
produce, not a problem to fix.

- The mapper's own check and the workflow's execution run the mapping a second time with each pending
  answer filled by some value of its type (`ai.clarifications.answered_for_trial`). What is still wrong
  there is what is wrong with the mapping. The trial is never stored.
- The mapper is not sent round again when the trial is clean, and a mapping that asks no longer scores
  worse than one that guesses.
- The reviewer sees `execution.pending_answers`. It may name a question the mapper already asked by its
  target in `covered_questions`. A repair whose every concern is a pending answer is rejected.
- A workflow whose only errors are pending answers is `awaiting_human`, not `failed`.

### SAP's pricing unit, and the price table

- The mapper is told that an ERP price list puts the amount, its currency (`Unit`), the pricing unit (`per`)
  and the unit of measure (`UoM`) side by side, and that the pricing unit is never a pack size.
- `deepseek-flash` and `deepseek-v4-pro` are in the price table at DeepSeek's peak, cache-miss rate. They are
  not offered in the model picker.

## The second run

On 2026-11-21 the eleven files were sent to the same model with the rules above, on SQLite with the default
90-second lease.

| Measure | 2026-11-20 | 2026-11-21 |
|---|---|---|
| Files that finished | 11 (lease raised to an hour) | 11 (default lease) |
| Field values right | 408 of 420 | 420 of 420 |
| Lines in `open_questions` | 39 | 16, none repeated, each a decision a person must make |
| Controls on the cards | 42 | 28 |
| VAT flag left empty for a person | 9 of 11 | 11 of 11 |
| Files that pass the contract after one VAT answer | 11 of 11 | 11 of 11 |
| Reviewer answers the guard accepted | 8 of 11 | 11 of 11 |
| Cards that name the matcher as the source of the model's mapping | 3 | 0 |
| Workflow `awaiting_human` / `failed` | 1 / 10 | 6 / 5 |
| Mapper fix rounds, all files | 22 | 4 |
| Repairs the reviewer ordered | 4 | 4 |
| Provider attempts | 100 | 77 |
| Input / output tokens | 2.92M / 0.57M | 2.04M / 0.39M |
| Time, all files / per file | 3,309 s / 301 s | 2,086 s / 190 s |
| Cost at DeepSeek's peak rate | at most $1.56 | at most $1.08 |

The five NetSuite and Oracle files took 4 to 6 attempts and 78 to 173 seconds each. The six SAP files took 7
to 11 attempts, and all five `failed` workflows are among them. Both causes were FORMA's own:

1. **The reviewer called the empty end date a mapping error** in four SAP files and ordered a repair. It was
   shown `effective_to` filled on 0 of 6 records beside source cells that all hold `31.12.9999`, and nothing
   that says FORMA reads that as null. The execution report now names such fields
   (`execution.dates_without_an_end`), the reviewer is told the rule, and a repair whose every concern is such
   a field is rejected.
2. **The matcher took `per` for the pack size**: the header is a word of the synonym "qty per pack". When the
   model rightly left it out, the check against the matcher's reading reported the pack size as lost and sent
   the mapper round twice in two files. A header that is only a connecting word (`per`, `of`, `ต่อ` …) now
   matches a field only when a synonym is that very word.

## The third run

The six SAP files, with both fixes, on 2026-11-21:

| SAP files only | First run | Second run | Third run |
|---|---|---|---|
| Field values right | 240 of 252 | 252 of 252 | 252 of 252 |
| Workflow `awaiting_human` / `failed` | 1 / 5 | 1 / 5 | 6 / 0 |
| Repairs the reviewer ordered | 4 | 4 | 0 |
| Mapper fix rounds | 12 | 4 | 1 |
| Provider attempts | 58 | 52 | 31 (4 to 6 per file) |
| Input / output tokens | 1.73M / 0.39M | 1.49M / 0.27M | 0.75M / 0.14M |
| Time, six files | 2,183 s | 1,443 s | 850 s (99 to 212 s per file) |

After one VAT answer each file passes the contract with six records. The model wrote the VAT flag as `true`
in two of the six again; both were reopened, and in both the reviewer asked about it. One mapping fixed its
last row again (`sap_unconverted.txt`); the limit was released. No mapping read `per` as the pack size.

Each SAP card still offered a choice of column for the currency, the selling unit and the price list: 16 of
the 23 controls. The model was right each time; the matcher was not.

## The matcher reads an ERP price list

- **Currency by value.** The profiler gives a column whose every value is an ISO 4217 code the hint
  `currency_code` (`profiling.profile.CURRENCY_CODES`; CUP and ALL are left out, they are also words). The
  matcher reads the currency from such a column whatever its header (SAP's `Unit`), at 0.9: the file's codes
  overrule the contract's default. Such a column is never the selling unit, so SAP's `UoM` is.
- **Short headings.** `CnTy`, SAP's heading for the condition type, reads as "condition type", which the
  contract already names as the price list (`heuristics.SHORT_LABELS`, with `Crcy` for currency). A price list
  column on the product rows is read through FORMA's map (ZPR0 → standard); before, the contract default was
  written whatever the file said. Only a header that is a name of the price list counts: NetSuite's item
  `Type` contains a word of "condition type" and holds item kinds.
- **Readings compared by what they write** (`services.mapping.compare_readings`). Comparing the cells two
  readers read misses both ways. Where a field is read from one column through a value map or a constant,
  each distinct value of the column is run through both readings, when the profile holds them all:
  - a disagreement whose outputs are equal on every value is resolved by evidence. A model that writes the
    constant "standard" where the matcher maps CnTy's one value ZPR0 to "standard" is no longer a choice of
    column;
  - an agreement whose outputs differ on a value becomes one question about that value, with both outputs as
    the choices: "ZPR0 in CnTy: the matcher writes 'standard', the model 'ZPR0'". The mapping is not changed.

Replaying the eleven mappings the model wrote (SAP from the third run, the others from the second) through
FORMA as it is now, with a scripted provider that gives each saved mapping back:

| Cards | Before | After |
|---|---|---|
| Controls | 29 | 17 |
| Choices of column | 16 | 0 |
| SAP card | VAT, currency, selling unit, price list | VAT, and what ZPR0 means (five files); VAT alone (one) |
| NetSuite, Oracle cards | unchanged | unchanged |

## Consequences

- Re-running the eleven mappings the model wrote through the engine as it is now gives 420 of 420 field
  values. The mapping with `data_end=10` reads twelve of twelve products once the limit is released, and as
  saved it reads six and reports the other six.
- On the two SAP "Text with Tabs" files the fields counted as read differently by matcher and model fall from
  7 to 5 and from 7 to 4. What is left differs in what it reads: the currency, the price list, the VAT flag,
  the selling unit, and on one file how the amount is cleaned. The card no longer offers a choice of column
  for the code and the name.
- With a model that leaves VAT empty, a first file passes the contract only after the VAT answer. Two of the
  eleven files passed before any answer because the model had guessed.
- A SQLite deployment needs no longer lease for a model run. The run on 2026-11-20 set
  `FORMA_JOB_LEASE_S=3600` to finish at all.
- The cost FORMA shows for the eleven files falls from $5.21 to at most $1.56: the same tokens at the model's
  own rate.
- Without a model, a SAP price list now gives the selling unit from `UoM` (PCS, before THB), the currency
  from `Unit` and the price list from `CnTy`. A mapping approved before keeps its reading until the layout
  changes: the matcher's new reading reaches only a new or changed format.
- A column that holds only open-ended dates was profiled as text and is now a date. A customer whose approved
  format has such a column gets one "types changed" review on the next file.
- `PROMPT_VERSION` and `WORKFLOW_VERSION` changed, so no checkpoint of an earlier run is reused.

## What this does not establish

- Each run is one run of a model that does not answer the same way twice. The NetSuite and Oracle files were
  not sent again after the last two fixes; neither fix touches what they hold.
- On SQLite the log still shows "heartbeat unavailable" and a failed "job recovery" with `database is
  locked` while a model thinks (7 to 26 lines a run). They are the attempts the commit guard now makes
  harmless: recovery cannot take a job whose transaction holds the lock. The noise is not removed.
- The matcher's reading of SAP price lists ("The matcher reads an ERP price list") was checked by replaying
  the model's saved mappings with a scripted provider, not by a fourth run. The currency codes are a fixed
  list; a column of codes it lacks is not recognised. `SHORT_LABELS` holds two headings.
- The guidance on SAP's pricing unit is text in a prompt. For the date sentinel only the null on output and
  the rejected repair are enforced.
- The VAT rule looks for four words. A file that states VAT another way keeps what the model wrote, and the
  matcher's question is still asked. A model that writes `true` on a customer's later file is not reopened.
- Only the VAT and warehouse sentences are merged. Any other matcher sentence stays beside a model's
  question on the same field.
- The trial value is one value. A mapping that fails only for the other answer shows when the person
  answers: the answer is run on the whole file before it is saved.
- The lease rule for SQLite is tested at the commit guard. The heartbeat still computes its time before it
  waits for the lock, so a lease shorter than SQLite's 30-second wait can be written already expired.
- The files are imitations with six products. No file from a customer and no large file has been run.
