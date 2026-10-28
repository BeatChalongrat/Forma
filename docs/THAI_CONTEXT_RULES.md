# Thai context rules (ADR-007)

All rules are explicit DSL operations with unit tests (`engine/thai.py`). The heuristic mapper selects
them from column hints; the AI is told to use them.

## Dates — `parse_date_th`
| Input | Result | Rule |
|---|---|---|
| `1/10/2569`, `01/10/2569` | 2026-10-01 | year ≥ 2400 → Buddhist era, minus 543 |
| `15 ต.ค. 69`, `15 ตุลาคม 2569` | 2026-10-15 | Thai month names/abbreviations; 2-digit year ≥ 40 → 25xx BE |
| `2026-10-01`, `2026-10-01 00:00:00` | 2026-10-01 | ISO, time ignored |
| `21-Oct-2026`, `21 Oct 2026` | 2026-10-21 | English month names |
| `02/10/2026` | 2026-10-02 | day-first by default (`dayfirst: false` for US files) |
| `46000` | 2025-12-09 | Excel serial (bounded 20000–80000) |
| `assume_year: be` | | force BE for files known to use 2-digit BE years < 40 |

## Numbers — `parse_number_th`
`1,500.00` · `฿ 25.50` · `1,284 บาท` · `๑๒๐` (Thai digits) · `15%` → 15 · `(200)` → -200 · blanks/`-`/`n/a` → null.

## VAT — `vat_adjust`
Thai retail price lists are usually VAT-inclusive (7%). Headers decide: `รวม VAT` / `incl` → inclusive;
`ไม่รวม` / `ก่อน VAT` / `ex VAT` / `excl` → exclusive. When no marker exists the mapper assumes inclusive and
records the assumption as an open question so a reviewer confirms it. `vat_adjust(mode=add|remove, rate=0.07)`
converts explicitly; the target carries `vat_included` so downstream never guesses. The rate is the named
constant `engine.thai.VAT_RATE`; the self-healing data check (ADR-021) uses it to recognise a price column
that now includes or excludes VAT (a steady ×1.07 or ÷1.07 against last round).

## Units — `unit_normalize`, `pack_size`
| Source | sell_unit | pack_size |
|---|---|---|
| `ชิ้น`, `อัน`, `PCS`, `EA` | PCS | – |
| `แพ็ค 6`, `PACK 6`, `pk6` | PACK | 6 |
| `ลัง(12)`, `CTN 12`, `1x12`, `12 ชิ้น/ลัง` | CARTON | 12 |
| `โหล`, `DZ` | DOZEN | – |
| `กล่อง`, `BOX` | BOX | – |
| `ขวด`, `BTL` | BOTTLE | – |
Unknown units pass through upper-cased (never silently dropped); add synonyms via `params.synonyms`.

## Status / booleans — `parse_bool`, `map_values`
`ขาย`/`ใช้งาน`/`Y`/`TRUE` → active; `ไม่ขาย`/`N`/`FALSE` → inactive; `เลิกขาย`/`ยกเลิก`/`X` (SAP deletion flag) → discontinued.
Headers that ask the inverse question (`Blocked`, `LVORM`, `ระงับ`) invert the mapping automatically.

## Text & files
Zero-width characters, NBSP and BOMs are stripped on read; blank strings become nulls. CSV encodings are
detected in the order utf-8-sig → utf-8 → cp874/tis-620 (Thai) → cp1252; delimiters are sniffed (`,` `;` tab `|`).
Sheet and file names lose digits before fingerprinting so `stock_20261001.csv` and `stock_20261015.csv` match.
