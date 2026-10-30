# ADR-043 — Check input fidelity before evaluating real customer files

Status: implemented 2026-10-30. This is a bounded capability contract, not a claim to understand every
Excel feature or arbitrary JSON Schema. Extends ADR-003, ADR-017, ADR-026 and ADR-041.

## Findings reproduced before the fix

Independent adversarial fixtures exposed silent CSV character replacement, acceptance of an unclosed
quoted CSV field, formula checks stopping at row 400, uncached formulas and Excel error cells becoming
empty values, loss of numeric zero-padding and percentage meaning, omission of tables after row 5000,
and headers below a long preamble not being found. A further fixture exposed leading empty Excel
columns being trimmed, making source-cell coordinates wrong.

## Changes

- Stream through all OOXML worksheet rows. Record formula counts, missing caches, error-cell locations,
  hidden rows/columns, external-formula counts, and numeric-format transformations in sheet metadata.
  Bound inspection to 512 MiB of XML per workbook and cache at most eight inspections, keyed by resolved
  path, size and modification time. The cache assumes the application's source-file immutability.
- Profiling is descriptive: it retains incomplete/error-cell metadata and can profile other sheets.
  Transformation is strict: a selected sheet with missing formula caches or Excel errors fails before
  producing records, even if the bad cell is beyond the simulation row limit. Unselected sheets are not
  required for a transformation. Currently a bad cell anywhere in a selected sheet blocks that sheet;
  narrowing this to selected table regions is future work.
- Cached formula values are used as saved, never recalculated. A selected formula-bearing sheet forces
  human review because cache freshness cannot be inferred. Hidden data is included, with review required.
  Legacy .xls/.xlsb reads also require review because the OOXML inspection does not cover them.
- Restore pure zero masks (e.g. 000000) and pure percentage masks (0%, 0.00%) without display rounding.
  Complex percentage/zero-mask formats are reported for review, not interpreted heuristically. This is
  not an Excel display formatter. Use physical row and column positions, including leading empty cells.
- Detect blank-row table boundaries across the full sheet with a compact boolean vector, then sample
  only a bounded header window per block (60 candidate header rows). Column occupancy covers each full
  block. Multiple tables must still have detectable separators; arbitrary report layouts are not solved.
- CSV decoding uses BOMs and strict full-file decoding. Sniffing does not misclassify a UTF-8 character
  split at its sample boundary. Malformed quoting and invalid explicit encodings fail with input errors.
- Reject target-schema shapes the mapping catalogue cannot enumerate, before registration and during
  mapping validation for already-loaded schemas. Includes references, shape composition/conditionals,
  tuple schemas, nested object collections, boolean field schemas and unsupported pointer characters.
  These may be valid JSON Schema; they are not supported FORMA mapping contracts yet.
- Schema guidance now distinguishes semantic tags (conversion/business meaning) from source-column
  synonyms (header matching). A semantic tag alone does not supply every Thai/English header alias.
- All mapping-related AI requests now include the complete selected target JSON Schema, not just a
  field-name/type catalogue. The catalogue also carries descriptions and examples (standard JSON Schema
  examples take precedence over legacy x-forma examples). Prompts explicitly use the supplied contract
  and ask for missing business facts instead of imposing product/Thai-retail conventions.
- Unresolved mapper questions force review even when confidence is 100% or a prior mapping was approved.
  A pre-existing block remains a block. Human edits pass their remaining questions through the same gate.

## Evidence

`tests/test_ingestion_readiness.py` uses exact low-level OOXML fixtures independent of the synth generator,
including invalid caches and explicit style IDs. `tests/test_contract_readiness.py` checks an independently
written expected JSON document with renamed target fields and explicit source aliases, non-product
support-ticket contracts with flat/nested objects and no built-in semantics, AI contract delivery, ambiguous VAT
review, unsupported-schema rejection and formula failure gates. Existing table, Thai conversion, join,
custom-contract, schema-gate, lineage and synthetic-corpus tests remain regression coverage.

The automated suite runs offline. A separate, bounded live smoke probe on 2026-10-30 used the configured
DeepSeek model (`deepseek-flash`) with entirely synthetic data: a new non-product nested contract mapped
staff time from XLSX, preserved zero-padded IDs, and converted 90/120/100 minutes to 1.5/2/1.666... hours
without rounding. A second case with retail and wholesale prices produced an explicit clarification
question. Both probes passed their narrow checks; the latter still produced a provisional value, so
the unresolved-question policy gate is essential. These two cases are not an accuracy benchmark.

These checks do not establish general live-provider accuracy, throughput of a production deployment,
or accuracy on unseen customer files. The old
performance figures in ADR-003/PERFORMANCE.md predate full XML scanning and need remeasurement.

## Remaining limits and pilot prerequisites

1. There is no fixed downstream product format. Each import uses its selected, versioned contract;
   configure field meanings, required/null/default behavior, units and identity keys for that contract.
   Unclear meanings need operator clarification. Passing structural validation does not prove that
   a value represents the intended business concept. The bundled product schema is only one example.
2. Formulas are not calculated. Refresh external data/recalculate in Excel before upload. Macros are
   never executed; images/OCR, comments, color-based business rules, charts, pivot refresh, Power Query,
   external connections and encrypted workbooks are not interpreted as source business semantics.
3. Binary/legacy formats are read through calamine but need dedicated real-format fixtures before any
   stronger compatibility claim. Strict OOXML namespaces are rejected with conversion guidance.
4. Numeric IDs whose precision was already lost in Excel cannot be reconstructed. Prefer identifiers
   stored as text. Dates with ambiguous locale/business meaning still need an explicit rule.
5. Layouts with vertical forms, no discernible header, tables without separators, merged data cells or
   repeated headers need manual table/rule selection or additional targeted support. Full-file scanning
   is not equivalent to semantic coverage of every region.
6. Run the provider-enabled evaluator against held-out synthetic/adversarial cases, then real reviewed
   outputs. Preserve unknowns and review instead of treating model confidence as correctness.
7. Reprofile/repropose existing imports before validating them under the new reader; saved historical
   profiles/mappings are not rewritten by this change. Deploy/rebuild separately from code verification.

## Reproduce

From `backend/` with its existing development environment:

```
python -m pytest tests/test_ingestion_readiness.py tests/test_contract_readiness.py --no-cov -o addopts='' -q
python -m pytest
python -m ruff check src tests
python -m mypy -p forma
```

The focused command deliberately omits the repository-wide coverage gate; the full run retains it.
