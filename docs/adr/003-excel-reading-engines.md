# ADR-003 — fastexcel/Polars for data, streaming XML for structure

**Decision** Cell values are read with fastexcel (calamine) as strings into Polars — fast and explicit about
parsing (Thai-aware DSL ops decide what a value means). Structure facts (hidden sheets, formulas and whether
they carry cached values, merged ranges) are read directly from the xlsx zip with a streaming XML parser,
bounded to the first 400 rows per sheet; merged ranges (which sit after the data in the XML) are read only
for files ≤ 2 MB. openpyxl is used by the synthetic corpus generator only.

**Measured (100,000 rows × 8 columns, 4.2 MB, Thai text)** metadata scan 0.8 s · full profile 1.8 s ·
heuristic mapping < 0.1 s · transform to validated nested JSON 6.4 s. A full openpyxl load of the same file
took 19.8 s, which is why it is kept off the hot path.

**Consequences** .xls/.xlsb are readable through calamine; macros are never executed; per-row Python only
remains in date/unit parsing and can be vectorised further if profiling demands it.
