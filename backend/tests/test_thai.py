"""Thai-context normalisation functions (engine/thai.py)."""

from __future__ import annotations

import datetime as dt

import pytest

from forma.engine import thai


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1,500.50 บาท", 1500.5),
        ("฿ 1,200", 1200.0),
        ("฿1,000.00 baht", 1000.0),
        ("THB 99", 99.0),
        ("๑๒๓", 123.0),
        ("๕๓.๕๐", 53.5),
        ("15%", 15.0),
        ("12.5%", 12.5),
        ("(200)", -200.0),
        ("(1,000.25)", -1000.25),
        ("-45.5", -45.5),
        ("+7", 7.0),
        ("1 234", 1234.0),
        ("", None),
        ("   ", None),
        ("-", None),
        ("n/a", None),
        ("null", None),
        (None, None),
        ("abc", None),
        ("1.2.3", None),
        (True, 1.0),
        (False, 0.0),
        (3, 3.0),
        (2.5, 2.5),
    ],
)
def test_parse_number_th(value: object, expected: float | None) -> None:
    assert thai.parse_number_th_py(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("15/10/2569", dt.date(2026, 10, 15)),  # dd/mm/BE
        ("15/10/69", dt.date(2026, 10, 15)),  # 2-digit BE
        ("15 ต.ค. 69", dt.date(2026, 10, 15)),  # Thai month abbreviation + 2-digit BE
        ("15 ต.ค. 2569", dt.date(2026, 10, 15)),
        ("1 มกราคม 2569", dt.date(2026, 1, 1)),  # full Thai month name
        ("5 ตุลาคม 2569", dt.date(2026, 10, 5)),
        ("2026-10-15", dt.date(2026, 10, 15)),  # ISO
        ("2026-10-15T10:00:00", dt.date(2026, 10, 15)),  # ISO datetime
        ("2569-10-15", dt.date(2026, 10, 15)),  # ISO-looking BE year
        ("21-Oct-2026", dt.date(2026, 10, 21)),
        ("15 Oct 26", dt.date(2026, 10, 15)),
        ("15/10/2026", dt.date(2026, 10, 15)),  # dd/mm/yyyy CE
        ("15.10.2569", dt.date(2026, 10, 15)),  # dotted separators
        ("15/10/69 10:30", dt.date(2026, 10, 15)),  # trailing time
        ("10/15/2026", dt.date(2026, 10, 15)),  # month>12 in day slot → swapped
        ("1/1/2000", dt.date(2000, 1, 1)),
        ("46000", dt.date(2025, 12, 9)),  # Excel serial
        ("46000.5", dt.date(2025, 12, 9)),
        ("99999", None),  # out of plausible serial range
        ("31.12.9999", None),  # SAP "valid forever": a sentinel, not Buddhist year 9456 (ADR-057)
        ("9999-12-31", None),
        ("31/12/9999", None),
        ("31/02/2569", None),  # invalid calendar date
        ("13/14/2026", None),
        ("foo", None),
        ("", None),
        (None, None),
        ("๑๕/๑๐/๒๕๖๙", dt.date(2026, 10, 15)),  # Thai digits
        (dt.date(2026, 1, 2), dt.date(2026, 1, 2)),
        (dt.datetime(2026, 1, 2, 3, 4), dt.date(2026, 1, 2)),
    ],
)
def test_parse_date_th(value: object, expected: dt.date | None) -> None:
    assert thai.parse_date_th_py(value) == expected


def test_parse_date_th_options() -> None:
    assert thai.parse_date_th_py("15/10/26", assume_year="ce") == dt.date(2026, 10, 15)
    assert thai.parse_date_th_py("15/10/26") == dt.date(2026, 10, 15)  # auto: 2-digit ≥ 40 is BE
    assert thai.parse_date_th_py("15/10/39", assume_year="ce") == dt.date(2039, 10, 15)
    assert thai.parse_date_th_py("15/10/85", assume_year="ce") == dt.date(1985, 10, 15)
    assert thai.parse_date_th_py("15/10/10", assume_year="be") == dt.date(1967, 10, 15)
    assert thai.parse_date_th_py("10/15/2026", dayfirst=False) == dt.date(2026, 10, 15)
    assert thai.parse_date_th_py("2569-10-15", assume_year="be") == dt.date(2026, 10, 15)
    assert thai.parse_date_th_py("15-10-2569", formats=["%d-%m-%Y"]) == dt.date(2026, 10, 15)
    assert thai.parse_date_th_py("15-10-2026", formats=["%d-%m-%Y"]) == dt.date(2026, 10, 15)
    # an explicit format that does not match falls through to the generic parsers
    assert thai.parse_date_th_py("2026-10-15", formats=["%d.%m.%Y"]) == dt.date(2026, 10, 15)


def test_looks_like_thai_date() -> None:
    assert thai.looks_like_thai_date("15/10/2569")
    assert not thai.looks_like_thai_date("46000")  # bare numbers are numbers, not dates
    assert not thai.looks_like_thai_date("abc")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("ชิ้น", "PCS"),
        ("ลัง(12)", "CARTON"),
        ("PACK 6", "PACK"),
        ("CTN", "CARTON"),
        ("pcs", "PCS"),
        ("Pcs", "PCS"),
        ("กล่อง", "BOX"),
        ("โหล", "DOZEN"),
        ("ขวด", "BOTTLE"),
        ("kg", "KG"),
        ("KG", "KG"),
        ("แพ็ค", "PACK"),
        ("12 ชิ้น/ลัง", "PCS"),
        ("XYZ", "XYZ"),  # unknown → passthrough upper-cased
        ("1x12", "CARTON"),
        ("12's", "PACK"),
        ("FL", "BOTTLE"),  # SAP Flasche
        ("KAR", "CARTON"),  # SAP Karton
        ("ST", "PCS"),  # SAP Stück
        ("Dozens", "DOZEN"),  # Odoo UoM name
        (None, None),
        ("", None),
    ],
)
def test_unit_normalize(value: object, expected: str | None) -> None:
    assert thai.unit_normalize_py(value) == expected


def test_unit_normalize_default_and_extra() -> None:
    assert thai.unit_normalize_py(None, default="PCS") == "PCS"
    assert thai.unit_normalize_py("foo", default="PCS") == "PCS"
    assert thai.unit_normalize_py("stk", extra={"STK": "PCS"}) == "PCS"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1x12", 12),
        ("12x1", 12),
        ("ลัง(24)", 24),
        ("CTN 12", 12),
        ("12's", 12),
        ("24 ชิ้น/ลัง", 24),
        ("แพ็ค 6", 6),
        ("12", 12),
        ("๑๒", 12),
        ("PCS", None),
        ("abc", None),
        (None, None),
        (12, 12),
        (2.0, 2),
        (0, None),
        (-1, None),
    ],
)
def test_pack_size(value: object, expected: int | None) -> None:
    assert thai.pack_size_py(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Y", True),
        ("no", False),
        ("ใช่", True),
        ("ไม่", False),
        ("1", True),
        ("0", False),
        ("TRUE", True),
        ("False", False),
        ("ขาย", True),
        ("เลิกขาย", False),
        ("x", True),
        ("", False),
        ("maybe", None),
        (None, None),
        (True, True),
        (False, False),
    ],
)
def test_parse_bool(value: object, expected: bool | None) -> None:
    assert thai.parse_bool_py(value) == expected


def test_vat_adjust() -> None:
    assert thai.vat_adjust_py(107, "remove") == 100.0
    assert thai.vat_adjust_py(100, "add") == 107.0
    assert thai.vat_adjust_py(100, "add", rate=0.1) == 110.0
    assert thai.vat_adjust_py(None, "add") is None
    with pytest.raises(ValueError, match="unknown vat mode"):
        thai.vat_adjust_py(1, "x")
