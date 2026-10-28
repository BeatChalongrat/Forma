# Mapping DSL

A `MappingDocument` fully describes how one customer's files become target records. It is YAML/JSON,
validated by Pydantic (`forma.mapping_dsl.models`) and statically checked against the target schema
(`forma.mapping_dsl.validate`). LLM agents emit exactly this document; the UI edits it; the engine runs it.

```yaml
meta:
  target_schema: product@1
  customer_id: cus_…
  created_by: ai | human | system
  confidence: 0.94
  evidence: ["root table: sheet 'สินค้า' …"]
sources:                      # one per table that is read
  - id: products
    role: products            # products | pricing | inventory | promotions | categories | other
    file: { name_pattern: "(?i)^สินค้า_", sheet_names_any: ["สินค้า"] }   # how to find the file in a round
    sheet: { name: "สินค้า" }  # or {index: 0} or {match: "regex"}; omit for CSV
    table:
      header_row: 2           # 0-based; or "auto"
      header_rows: 1          # multi-row headers are merged
      stop_at_blank_rows: 2
      drop_total_rows: true   # label "รวม"/"Total:"/"ยอดรวม…" with only numbers beside it ("SUM-01", "รวมมิตร" stay)
      fill_down: ["หมวด"]     # merged-cell groups
      unpivot: { id_columns: ["Item Code"], value_columns_regex: "^[A-Z][a-z]{2}-\\d{2}$", variable_name: period, value_name: value }
    csv: { encoding: auto, delimiter: auto }
    rename: { "Item No.": "sku" }
joins: []                     # optional root-level joins (left/right/on/how/cardinality/on_unmatched)
record:
  root_source: products       # one row here = one target record
  filters: []                 # DSL boolean expressions
  dedupe: { keys: [], keep: first }   # keys default to the schema identity keys
fields:                       # root-level target fields
  - target: /sku
    expr: { op: trim, args: [{ op: col, name: "รหัสสินค้า" }] }
    confidence: 1.0
    evidence: ["header 'รหัสสินค้า' == synonym 'รหัสสินค้า'"]
collections:                  # nested arrays
  - target: /prices
    source: pricing
    link: [{ left: "รหัสสินค้า", right: "Item" }]   # root column ↔ item column
    fields:
      - { target: /prices/*/amount, expr: { op: parse_number_th, args: [{ op: col, name: "ราคา" }] } }
      - { target: /prices/*/vat_included, expr: { op: const, value: true } }
quality:
  - { target: /sku, rule: unique, severity: warning }
provenance: true              # attach source file / row lineage
attributes_passthrough: true  # unmapped root columns land under /attributes (no data loss)
```

## Expressions

Three node kinds: `col` (a source column), `const`, and a `call` with `op`, `args` (child expressions) and
`params` (literal options). Column references default to the frame being mapped (root source for `fields`,
the collection's source for its `fields`); `source: other_id` reaches a joined frame.

| op | args | params | result |
|---|---|---|---|
| `coalesce` | 1+ | | first non-null |
| `concat` | 1+ | `separator` | string |
| `trim` · `upper` · `lower` · `normalize_whitespace` · `strip_leading_zeros` | 1 | | string |
| `cast` | 1 | `to: string|int|float|bool|date` | typed |
| `regex_extract` | 1 | `pattern`, `group` | string |
| `regex_replace` | 1 | `pattern`, `replacement` | string |
| `split` | 1 | `separator`, `index?` | list or element |
| `map_values` | 1 | `mapping`, `default?`, `keep_unmapped?`, `fuzzy?` | mapped value (fuzzy = containment match) |
| `lookup` | 1 | `table` (dict) or `source` | mapped value |
| `parse_number_th` | 1 | `percent_to_fraction?` | float — commas, ฿, บาท, Thai digits, %, (negatives) |
| `parse_date_th` | 1 | `formats?`, `assume_year: auto|be|ce`, `dayfirst` | date — BE/CE, 2-digit years, Thai/English months, Excel serials |
| `parse_bool` | 1 | | bool — y/n, ใช่/ไม่, active/inactive, … |
| `vat_adjust` | 1 | `mode: add|remove`, `rate=0.07` | float |
| `unit_normalize` | 1 | `default?`, `synonyms?` | PCS · PACK · BOX · CARTON · DOZEN · KG · … |
| `pack_size` | 1 | | int from "1x12", "ลัง(24)", "CTN 12", "12's" |
| `add` · `multiply` | 2+ | | number |
| `subtract` · `divide` | 2 | | number (divide by 0 → null) |
| `round` | 1 | `decimals` | number |
| `conditional` | 2–3 | | `when(args[0]) then args[1] else args[2]` |
| `default` | 1 | `value` | fills nulls |
| `null_if` | 1 | `values` | nulls listed placeholders ("-", "n/a", …) |
| `to_list` | 1+ | | list of strings |
| `eq` `neq` `gt` `gte` `lt` `lte` `is_null` `not_null` `and` `or` `not` `contains` `starts_with` `in` | | `pattern`, `values` | boolean |

Adding an op means: add it to `Op`, give it an arity in `OP_ARITY`, one compiler branch in
`engine/ops.py`, and a test. The LLM prompt lists the ops automatically.

## Static validation (`validate_mapping`)

Errors: unknown target pointer, collection field mapped at root, duplicate target, unknown source,
required root field or identity key not mapped, required collection item field not mapped, bad op params.
Warnings: critical field unmapped, critical field mapped to a constant.
