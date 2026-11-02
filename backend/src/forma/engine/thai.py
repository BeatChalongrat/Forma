"""Thai-context normalisation (ADR-007): explicit, testable functions — never implicit magic.

Covers Buddhist-era dates, Thai month names, Thai digits, currency/comma formatting, percent,
VAT 7% inclusive/exclusive maths and Thai trade units (ชิ้น/แพ็ค/ลัง …) with pack sizes.
"""

from __future__ import annotations

import datetime as dt
import re

THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
VAT_RATE = 0.07  # Thai VAT; ops take it as a parameter, checks that suspect VAT use this default

THAI_MONTHS: dict[str, int] = {
    "มกราคม": 1,
    "ม.ค.": 1,
    "มค": 1,
    "ม.ค": 1,
    "กุมภาพันธ์": 2,
    "ก.พ.": 2,
    "กพ": 2,
    "ก.พ": 2,
    "มีนาคม": 3,
    "มี.ค.": 3,
    "มีค": 3,
    "มี.ค": 3,
    "เมษายน": 4,
    "เม.ย.": 4,
    "เมย": 4,
    "เม.ย": 4,
    "พฤษภาคม": 5,
    "พ.ค.": 5,
    "พค": 5,
    "พ.ค": 5,
    "มิถุนายน": 6,
    "มิ.ย.": 6,
    "มิย": 6,
    "มิ.ย": 6,
    "กรกฎาคม": 7,
    "ก.ค.": 7,
    "กค": 7,
    "ก.ค": 7,
    "สิงหาคม": 8,
    "ส.ค.": 8,
    "สค": 8,
    "ส.ค": 8,
    "กันยายน": 9,
    "ก.ย.": 9,
    "กย": 9,
    "ก.ย": 9,
    "ตุลาคม": 10,
    "ต.ค.": 10,
    "ตค": 10,
    "ต.ค": 10,
    "พฤศจิกายน": 11,
    "พ.ย.": 11,
    "พย": 11,
    "พ.ย": 11,
    "ธันวาคม": 12,
    "ธ.ค.": 12,
    "ธค": 12,
    "ธ.ค": 12,
}
EN_MONTHS: dict[str, int] = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for m in names
}

_NUM_CLEAN_RE = re.compile(r"[,\s฿$€£]|บาท|thb|baht", re.IGNORECASE)
_NUM_RE = re.compile(r"^[-+]?\d+(\.\d+)?$")
_WHOLE_BAHT_RE = re.compile(r"\.-$")  # 189.- : Thai for "189 baht exactly"
_TRAILING_MINUS_RE = re.compile(r"^(.*\d)-$")  # 200.00- : SAP writes the sign last
# 1.500,00 is unambiguous (dot groups of three, then a comma); 1.500 alone is not and stays 1.5
_EUROPEAN_RE = re.compile(r"^[-+]?\d{1,3}(?:(?:\.\d{3})+,\d+|(?:\.\d{3}){2,})$")
_DECIMAL_COMMA_RE = re.compile(r"^[-+]?\d+,\d{1,2}$")  # 1500,50 : a comma with one or two digits is a decimal


def number_text(s: str) -> tuple[str, bool]:
    """The digits of a written amount and whether it was negative, after the forms a regex cannot tell apart
    from each other once the separators are gone: whole baht ``189.-``, SAP's trailing minus ``200.00-``,
    European ``1.500,00`` and ``1500,50`` (ADR-056)."""
    neg = False
    s = _WHOLE_BAHT_RE.sub("", s)
    m = _TRAILING_MINUS_RE.match(s)
    if m:
        neg, s = True, m.group(1)
    body = _NUM_CLEAN_RE.sub("", s) if not _EUROPEAN_RE.match(s) and not _DECIMAL_COMMA_RE.match(s) else s
    if _EUROPEAN_RE.match(s):
        body = s.replace(".", "").replace(",", ".")
    elif _DECIMAL_COMMA_RE.match(s):
        body = s.replace(",", ".")
    return body, neg


_PAREN_NEG_RE = re.compile(r"^\((.*)\)$")
_DATE_SEP_RE = re.compile(r"^(\d{1,4})[-/.](\d{1,2})[-/.](\d{1,4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?.*)?$")
_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_THAI_TEXT_DATE_RE = re.compile(r"^(\d{1,2})\s*([ก-๙.]+)\s*(\d{2,4})$")
_EN_TEXT_DATE_RE = re.compile(r"^(\d{1,2})[ ,-]+([A-Za-z]{3,9})[ ,-]+(\d{2,4})$")
_EXCEL_SERIAL_RE = re.compile(r"^\d{5}(\.\d+)?$")


# --------------------------------------------------------------------------------------------
# Numbers
# --------------------------------------------------------------------------------------------
def parse_number_th_py(value: object) -> float | None:
    """'1,500.50 บาท' → 1500.5 ; '๑๒๓' → 123 ; '(200)' → -200 ; '15%' → 15 ; '' → None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    s = str(value).strip().translate(THAI_DIGITS)
    if not s or s in {"-", "—", "n/a", "na", "null", "none"}:
        return None
    neg = False
    m = _PAREN_NEG_RE.match(s)
    if m:
        neg, s = True, m.group(1)
    s = s.rstrip("%")
    s, trailing = number_text(s)
    neg = neg != trailing
    if s.startswith("-"):
        neg, s = (not neg), s[1:]
    if not _NUM_RE.match(s):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def vat_adjust_py(amount: float | None, mode: str, rate: float = VAT_RATE) -> float | None:
    """mode='add' → amount*(1+rate) ; mode='remove' → amount/(1+rate). Rounded to 4 dp."""
    if amount is None:
        return None
    if mode == "add":
        return round(amount * (1 + rate), 4)
    if mode == "remove":
        return round(amount / (1 + rate), 4)
    raise ValueError(f"unknown vat mode {mode!r}")


# --------------------------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------------------------
OPEN_ENDED_YEAR = 9000  # years from here on mean "no end date" and read as null


def _normalize_year(y: int, assume: str) -> int:
    """Convert 2-digit / BE years to a CE 4-digit year.

    assume: 'auto' | 'be' | 'ce'. Heuristic for 'auto': >2400 → BE; 2-digit ≥ 40 → BE 25xx
    (e.g. 69 → 2569 → 2026) because Thai forms rarely use 2-digit CE for business dates.
    """
    if y >= OPEN_ENDED_YEAR:
        return y  # SAP's 31.12.9999: a sentinel, never a Buddhist year (ADR-057)
    if y >= 2400:
        return y - 543
    if y >= 1000:
        return y if assume != "be" else y - 543
    # two-digit year
    if assume == "ce":
        return 2000 + y if y < 70 else 1900 + y
    if assume == "be" or y >= 40:
        return 2500 + y - 543
    return 2000 + y


def parse_date_th_py(
    value: object, *, formats: list[str] | None = None, assume_year: str = "auto", dayfirst: bool = True
) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    s = str(value).strip().translate(THAI_DIGITS)
    if not s:
        return None
    # explicit formats first
    for f in formats or []:
        try:
            parsed = dt.datetime.strptime(s, f).date()
        except ValueError:
            continue
        if parsed.year >= OPEN_ENDED_YEAR:
            return None
        if parsed.year >= 2400:
            return parsed.replace(year=_normalize_year(parsed.year, assume_year))
        return parsed
    m = _ISO_RE.match(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return _safe_date(_normalize_year(y, assume_year), mo, d)
    m = _DATE_SEP_RE.match(s)
    if m:
        a, b, c = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a > 31:  # yyyy-mm-dd or yyyy/dd/mm (rare)
            return _safe_date(_normalize_year(a, assume_year), b, c)
        day, mon = (a, b) if dayfirst else (b, a)
        if mon > 12 and day <= 12:
            day, mon = mon, day
        return _safe_date(_normalize_year(c, assume_year), mon, day)
    m = _THAI_TEXT_DATE_RE.match(s)
    if m:
        th_mon = THAI_MONTHS.get(m.group(2)) or THAI_MONTHS.get(m.group(2) + ".")
        if th_mon:
            return _safe_date(_normalize_year(int(m.group(3)), assume_year), th_mon, int(m.group(1)))
    m = _EN_TEXT_DATE_RE.match(s)
    if m:
        en_mon = EN_MONTHS.get(m.group(2).lower())
        if en_mon:
            return _safe_date(_normalize_year(int(m.group(3)), assume_year), en_mon, int(m.group(1)))
    if _EXCEL_SERIAL_RE.match(s):
        serial = float(s)
        if 20000 <= serial <= 80000:
            return (dt.datetime(1899, 12, 30) + dt.timedelta(days=serial)).date()
    return None


def _safe_date(y: int, m: int, d: int) -> dt.date | None:
    if y >= OPEN_ENDED_YEAR:
        return None
    try:
        return dt.date(y, m, d)
    except ValueError:
        return None


def looks_like_thai_date(s: str) -> bool:
    return parse_date_th_py(s) is not None and not _NUM_RE.match(s.strip())


# --------------------------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------------------------
UNIT_SYNONYMS: dict[str, str] = {
    # canonical: PCS, PACK, BOX, CARTON, DOZEN, SET, KG, G, L, ML, BAG, BOTTLE, CAN, ROLL, PAIR
    "pcs": "PCS",
    "pc": "PCS",
    "piece": "PCS",
    "pieces": "PCS",
    "ea": "PCS",
    "each": "PCS",
    "unit": "PCS",
    "units": "PCS",
    "ชิ้น": "PCS",
    "อัน": "PCS",
    "ตัว": "PCS",
    "หน่วย": "PCS",
    "เม็ด": "PCS",
    "ใบ": "PCS",
    "แผ่น": "PCS",
    "เล่ม": "PCS",
    "pack": "PACK",
    "pk": "PACK",
    "pkt": "PACK",
    "แพ็ค": "PACK",
    "แพค": "PACK",
    "แพ็ก": "PACK",
    "ห่อ": "PACK",
    "ซอง": "PACK",
    "box": "BOX",
    "bx": "BOX",
    "กล่อง": "BOX",
    "carton": "CARTON",
    "ctn": "CARTON",
    "case": "CARTON",
    "cs": "CARTON",
    "ลัง": "CARTON",
    "หีบ": "CARTON",
    "dozen": "DOZEN",
    "dozens": "DOZEN",
    "dz": "DOZEN",
    "doz": "DOZEN",
    "โหล": "DOZEN",
    "set": "SET",
    "ชุด": "SET",
    "เซ็ต": "SET",
    "kg": "KG",
    "kgs": "KG",
    "กก.": "KG",
    "กก": "KG",
    "กิโลกรัม": "KG",
    "กิโล": "KG",
    "g": "G",
    "gm": "G",
    "gram": "G",
    "กรัม": "G",
    "ก.": "G",
    "l": "L",
    "ltr": "L",
    "liter": "L",
    "litre": "L",
    "ลิตร": "L",
    "ml": "ML",
    "มล.": "ML",
    "มล": "ML",
    "มิลลิลิตร": "ML",
    "bag": "BAG",
    "ถุง": "BAG",
    "กระสอบ": "BAG",
    "bottle": "BOTTLE",
    "btl": "BOTTLE",
    "ขวด": "BOTTLE",
    "can": "CAN",
    "กระป๋อง": "CAN",
    "roll": "ROLL",
    "ม้วน": "ROLL",
    "pair": "PAIR",
    "คู่": "PAIR",
    # SAP unit codes (German abbreviations) as they appear in MEINS / KMEIN exports (ADR-058)
    "st": "PCS",  # Stück
    "pak": "PACK",  # Paket
    "kar": "CARTON",  # Karton
    "ki": "BOX",  # Kiste
    "fl": "BOTTLE",  # Flasche
    "do": "CAN",  # Dose
    "bt": "BAG",  # Beutel
}
_PACK_RE_LIST = [
    re.compile(r"(\d+)\s*[xX×*]\s*(\d+)"),  # 1x12, 12x1
    re.compile(r"\((\d+)\)"),  # ลัง(24)
    re.compile(r"(\d+)\s*(?:ชิ้น|pcs|pc|ea|units?)\s*(?:/|ต่อ|per)\s*\S+", re.I),  # 24 ชิ้น/ลัง
    re.compile(r"(\d+)\s*'s"),  # 12's
    re.compile(
        r"(?:pack|pk|แพ็ค|แพค|บรรจุ|ctn|carton|case|box|ลัง|กล่อง|หีบ|dz|dozen|โหล|set|ชุด)\s*[:=]?\s*(\d+)", re.I
    ),
    re.compile(r"^(\d+)$"),
]


def unit_normalize_py(
    value: object, *, default: str | None = None, extra: dict[str, str] | None = None
) -> str | None:
    if value is None:
        return default
    s = str(value).strip().lower()
    if not s:
        return default
    table = {**UNIT_SYNONYMS, **{k.lower(): v for k, v in (extra or {}).items()}}
    if s in table:
        return table[s]
    s2 = re.sub(r"[\d\s()xX×*./'-]+", " ", s).strip()
    if s2 in table:
        return table[s2]
    for tok in re.split(r"[\s/()]+", s):
        if tok in table:
            return table[tok]
    up = s.upper()
    if up in set(UNIT_SYNONYMS.values()):
        return up
    # a bare packing figure: 1x12 is a case of twelve, 12's a pack of twelve (THAI_CONTEXT_RULES.md)
    if re.fullmatch(r"\d+\s*[x×*]\s*\d+", s):
        return "CARTON"
    if re.fullmatch(r"\d+\s*'s", s):
        return "PACK"
    return default if default is not None else str(value).strip().upper()


def pack_size_py(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return int(value) if value > 0 else None
    s = str(value).strip().translate(THAI_DIGITS)
    for rx in _PACK_RE_LIST:
        m = rx.search(s)
        if m:
            nums = [int(g) for g in m.groups() if g]
            if len(nums) == 2:
                return max(nums)  # 1x12 or 12x1 → 12
            return nums[0] if nums and nums[0] > 0 else None
    return None


# --------------------------------------------------------------------------------------------
# Booleans
# --------------------------------------------------------------------------------------------
_TRUE = {"y", "yes", "true", "1", "t", "ใช่", "มี", "active", "เปิด", "ขาย", "on", "x", "✓", "✔"}
_FALSE = {"n", "no", "false", "0", "f", "ไม่", "ไม่มี", "inactive", "ปิด", "เลิกขาย", "off", ""}


def parse_bool_py(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    return None
