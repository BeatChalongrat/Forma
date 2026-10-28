"""Synthetic ERP corpus generator (stands in for real customer files until they arrive).

Every variant is produced from one internal *truth* (clean products/prices/stock/promos) that is then
rendered into a messy file through an archetype (ERP style) plus perturbations. Because the generator
owns the truth it can also write the expected target records — deterministic ground truth for evals.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.styles import Font, PatternFill

ARCHETYPES = [
    "sap_like",
    "express_th",
    "odoo_like",
    "dynamics_like",
    "handmade_excel",
    "crosstab_months",
    "promo_heavy",
    "csv_vendor",
]
PERTURBATIONS = [
    "title_rows",
    "header_offset",
    "merged_title",
    "hidden_sheet",
    "totals_row",
    "thai_digits",
    "currency_symbols",
    "leading_zeros",
    "duplicate_rows",
    "be_dates",
    "mixed_dates",
    "blank_rows",
    "empty_columns",
    "nbsp",
    "renamed_columns",
    "multi_table_sheet",
]
CATEGORIES = [
    ("เครื่องดื่ม", "Beverages"),
    ("ขนมขบเคี้ยว", "Snacks"),
    ("ของใช้ในบ้าน", "Household"),
    ("อาหารแห้ง", "Dry Food"),
    ("เครื่องเขียน", "Stationery"),
    ("อิเล็กทรอนิกส์", "Electronics"),
]
UNITS_TH = [
    ("ชิ้น", "PCS", None),
    ("แพ็ค 6", "PACK", 6),
    ("ลัง(12)", "CARTON", 12),
    ("ลัง(24)", "CARTON", 24),
    ("โหล", "DOZEN", None),
    ("กล่อง", "BOX", None),
    ("ขวด", "BOTTLE", None),
]
UNITS_EN = [
    ("PCS", "PCS", None),
    ("PACK 6", "PACK", 6),
    ("CTN 12", "CARTON", 12),
    ("CTN 24", "CARTON", 24),
    ("DZ", "DOZEN", None),
    ("BOX", "BOX", None),
    ("BTL", "BOTTLE", None),
]
BRANDS = ["Siam", "Chang", "Nara", "Krung", "Lanna", "Bua", "Thai Best", "Golden"]
NAMES_TH = [
    "น้ำดื่ม",
    "น้ำอัดลม",
    "ชาเขียว",
    "มันฝรั่งทอด",
    "สาหร่าย",
    "ผงซักฟอก",
    "น้ำยาล้างจาน",
    "บะหมี่กึ่งสำเร็จรูป",
    "ข้าวหอมมะลิ",
    "ปากกา",
    "สมุด",
    "หูฟัง",
    "สายชาร์จ",
    "ถ่าน AA",
]
NAMES_EN = [
    "Drinking Water",
    "Soda",
    "Green Tea",
    "Potato Chips",
    "Seaweed",
    "Detergent",
    "Dish Soap",
    "Instant Noodles",
    "Jasmine Rice",
    "Pen",
    "Notebook",
    "Earphones",
    "Charging Cable",
    "AA Battery",
]
WAREHOUSES = ["BKK", "CNX", "HKT", "KKC"]
TH_MONTH_ABBR = [
    "ม.ค.",
    "ก.พ.",
    "มี.ค.",
    "เม.ย.",
    "พ.ค.",
    "มิ.ย.",
    "ก.ค.",
    "ส.ค.",
    "ก.ย.",
    "ต.ค.",
    "พ.ย.",
    "ธ.ค.",
]


@dataclass
class Truth:
    products: list[dict[str, Any]]
    prices: list[dict[str, Any]]  # sku, price_list, amount(ex VAT), effective_from
    stock: list[dict[str, Any]]  # sku, warehouse, qty
    promos: list[dict[str, Any]]  # sku, promo_code, type, pct, amount, buy, get, start, end


@dataclass
class Variant:
    variant_id: str
    customer_code: str
    archetype: str
    perturbations: list[str]
    round_no: int
    sequence: str | None  # returning-customer sequence id
    expected_relation: str | None  # same_customer_unchanged | renamed | wrong_file | None
    files: list[str] = field(default_factory=list)
    truth: Truth | None = None
    notes: list[str] = field(default_factory=list)
    vat_inclusive: bool = True
    lang: str = "th"


def _rng(seed: int, salt: str) -> random.Random:
    return random.Random(f"{seed}:{salt}")


def make_truth(rng: random.Random, n: int, *, lang: str, n_warehouses: int = 2) -> Truth:
    products, prices, stock, promos = [], [], [], []
    for i in range(n):
        ci = rng.randrange(len(CATEGORIES))
        ni = rng.randrange(len(NAMES_TH))
        ui = rng.randrange(len(UNITS_TH))
        unit_th, unit_code, pack = UNITS_TH[ui]
        sku = f"{rng.choice(['A', 'B', 'P', 'SKU-'])}{1000 + i:04d}"
        name = f"{NAMES_TH[ni] if lang == 'th' else NAMES_EN[ni]} {rng.choice(['500ml', '1L', '100g', '250g', 'XL', 'M', '12pcs', 'Pro'])}"
        base = round(rng.uniform(8, 2500), 2)
        products.append(
            {
                "sku": sku,
                "name": name,
                "category_th": CATEGORIES[ci][0],
                "category_en": CATEGORIES[ci][1],
                "brand": rng.choice(BRANDS),
                "barcode": f"885{rng.randrange(10**9, 10**10 - 1)}",
                "unit_th": unit_th,
                "unit_en": UNITS_EN[ui][0],
                "unit_code": unit_code,
                "pack": pack,
                "status": rng.choices(["active", "inactive", "discontinued"], [0.85, 0.1, 0.05])[0],
            }
        )
        prices.append(
            {"sku": sku, "price_list": "standard", "amount_ex": base, "effective_from": dt.date(2026, 10, 1)}
        )
        if rng.random() < 0.3:
            prices.append(
                {
                    "sku": sku,
                    "price_list": "wholesale",
                    "amount_ex": round(base * 0.85, 2),
                    "effective_from": dt.date(2026, 10, 1),
                }
            )
        for w in WAREHOUSES[:n_warehouses]:
            if rng.random() < 0.9:
                stock.append({"sku": sku, "warehouse": w, "qty": rng.randrange(0, 500)})
        if rng.random() < 0.35:
            kind = rng.choices(
                ["percent_off", "amount_off", "buy_x_get_y", "fixed_price"], [0.5, 0.2, 0.2, 0.1]
            )[0]
            promos.append(
                {
                    "sku": sku,
                    "promo_code": f"PR{rng.randrange(100, 999)}-{i}",
                    "type": kind,
                    "pct": rng.choice([5, 10, 15, 20]) if kind == "percent_off" else None,
                    "amount": round(base * 0.1, 2) if kind == "amount_off" else None,
                    "promo_price": round(base * 0.8, 2) if kind == "fixed_price" else None,
                    "buy": 2 if kind == "buy_x_get_y" else None,
                    "get": 1 if kind == "buy_x_get_y" else None,
                    "start": dt.date(2026, 10, rng.randrange(1, 10)),
                    "end": dt.date(2026, 10, rng.randrange(20, 31)),
                }
            )
    return Truth(products, prices, stock, promos)


# --------------------------------------------------------------------------------------------
# Value formatting (the "mess")
# --------------------------------------------------------------------------------------------
def fmt_num(v: float, rng: random.Random, pert: set[str], *, decimals: int = 2) -> str | float:
    if "currency_symbols" in pert and rng.random() < 0.5:
        return f"฿ {v:,.{decimals}f}"
    if "thai_digits" in pert and rng.random() < 0.15:
        return f"{v:,.{decimals}f}".translate(str.maketrans("0123456789", "๐๑๒๓๔๕๖๗๘๙"))
    if rng.random() < 0.5:
        return f"{v:,.{decimals}f}"
    return round(v, decimals)


def fmt_date(d: dt.date, rng: random.Random, pert: set[str], lang: str) -> str:
    if "be_dates" in pert or (lang == "th" and "mixed_dates" not in pert):
        r = rng.random()
        if r < 0.5:
            return f"{d.day}/{d.month}/{d.year + 543}"
        if r < 0.8:
            return f"{d.day} {TH_MONTH_ABBR[d.month - 1]} {str(d.year + 543)[-2:]}"
        return f"{d.day:02d}/{d.month:02d}/{d.year + 543}"
    if "mixed_dates" in pert:
        return rng.choice(
            [
                d.isoformat(),
                f"{d.day}/{d.month}/{d.year + 543}",
                f"{d.day:02d}/{d.month:02d}/{d.year}",
                d.strftime("%d-%b-%Y"),
            ]
        )
    return d.isoformat()


def fmt_sku(sku: str, rng: random.Random, pert: set[str]) -> str:
    s = sku
    if "leading_zeros" in pert and s[0].isdigit():
        s = "00" + s
    if "nbsp" in pert and rng.random() < 0.1:
        s = s + " "
    return s


# --------------------------------------------------------------------------------------------
# Archetype renderers → list of (sheet_name, rows[list[list]], options)
# --------------------------------------------------------------------------------------------
def _headers(arche: str, lang: str, renamed: bool) -> dict[str, str]:
    base = {
        "sap_like": {
            "sku": "MATNR",
            "name": "MAKTX",
            "cat": "MATKL",
            "brand": "BRAND",
            "barcode": "EAN11",
            "unit": "MEINS",
            "price": "KBETR",
            "vat": "MWSKZ",
            "status": "LVORM",
            "wh": "WERKS",
            "qty": "LABST",
            "list": "KSCHL",
            "eff": "DATAB",
        },
        "express_th": {
            "sku": "รหัสสินค้า",
            "name": "ชื่อสินค้า",
            "cat": "หมวดสินค้า",
            "brand": "ยี่ห้อ",
            "barcode": "บาร์โค้ด",
            "unit": "หน่วยนับ",
            "price": "ราคาขาย (รวม VAT)",
            "status": "สถานะ",
            "wh": "คลัง",
            "qty": "ยอดคงเหลือ",
            "list": "ประเภทราคา",
            "eff": "วันที่มีผล",
        },
        "odoo_like": {
            "sku": "Internal Reference",
            "name": "Product Name",
            "cat": "Product Category",
            "brand": "Brand",
            "barcode": "Barcode",
            "unit": "Unit of Measure",
            "price": "Sales Price",
            "status": "Active",
            "wh": "Warehouse",
            "qty": "Quantity On Hand",
            "list": "Pricelist",
            "eff": "Start Date",
        },
        "dynamics_like": {
            "sku": "Item No.",
            "name": "Description",
            "cat": "Item Category Code",
            "brand": "Vendor",
            "barcode": "GTIN",
            "unit": "Base Unit of Measure",
            "price": "Unit Price",
            "status": "Blocked",
            "wh": "Location Code",
            "qty": "Inventory",
            "list": "Sales Code",
            "eff": "Starting Date",
        },
        "handmade_excel": {
            "sku": "รหัส",
            "name": "รายการสินค้า",
            "cat": "หมวด",
            "brand": "แบรนด์",
            "barcode": "Barcode",
            "unit": "หน่วย",
            "price": "ราคา",
            "status": "ขาย/เลิกขาย",
            "wh": "สาขา",
            "qty": "คงเหลือ",
            "list": "กลุ่มราคา",
            "eff": "เริ่มใช้",
        },
        "crosstab_months": {
            "sku": "Item Code",
            "name": "Item Name",
            "cat": "Group",
            "brand": "Brand",
            "barcode": "Barcode",
            "unit": "UOM",
            "price": "Price",
            "status": "Status",
            "wh": "WH",
            "qty": "Qty",
            "list": "Price List",
            "eff": "Effective",
        },
        "promo_heavy": {
            "sku": "รหัสสินค้า",
            "name": "ชื่อสินค้า",
            "cat": "กลุ่ม",
            "brand": "ยี่ห้อ",
            "barcode": "บาร์โค้ด",
            "unit": "หน่วย",
            "price": "ราคาปลีก",
            "status": "สถานะ",
            "wh": "คลังสินค้า",
            "qty": "สต็อก",
            "list": "ประเภทราคา",
            "eff": "มีผล",
        },
        "csv_vendor": {
            "sku": "sku",
            "name": "product_name",
            "cat": "category",
            "brand": "brand",
            "barcode": "ean",
            "unit": "uom",
            "price": "price_ex_vat",
            "status": "status",
            "wh": "warehouse",
            "qty": "stock_qty",
            "list": "price_list",
            "eff": "valid_from",
        },
    }[arche]
    if renamed:
        alt = {
            "name": {
                "ชื่อสินค้า": "ชื่อรายการ",
                "Product Name": "Name",
                "MAKTX": "Material Description",
                "Description": "Item Description",
                "product_name": "name",
            },
            "qty": {
                "ยอดคงเหลือ": "จำนวนคงเหลือ",
                "Quantity On Hand": "Qty Available",
                "LABST": "Unrestricted Stock",
                "stock_qty": "qty_on_hand",
                "Inventory": "Qty",
            },
            "price": {
                "ราคาขาย (รวม VAT)": "ราคาขายรวมภาษี",
                "Sales Price": "List Price",
                "KBETR": "Price Amount",
                "price_ex_vat": "unit_price_ex_vat",
                "Unit Price": "Price",
            },
        }
        for k, m in alt.items():
            if base[k] in m:
                base[k] = m[base[k]]
    return base


def render_variant(v: Variant, out_dir: Path, rng: random.Random) -> None:
    assert v.truth is not None
    t = v.truth
    pert = set(v.perturbations)
    h = _headers(v.archetype, v.lang, "renamed_columns" in pert)
    vat = v.vat_inclusive
    if not vat:
        h["price"] = {
            "ราคาขาย (รวม VAT)": "ราคาขาย (ไม่รวม VAT)",
            "ราคาขายรวมภาษี": "ราคาขายก่อนภาษี",
            "ราคา": "ราคา (ก่อน VAT)",
            "ราคาปลีก": "ราคาปลีก ex VAT",
            "Sales Price": "Sales Price (ex VAT)",
            "List Price": "List Price ex VAT",
            "Unit Price": "Unit Price (excl. VAT)",
            "Price": "Price ex-VAT",
            "KBETR": "KBETR",
            "price_ex_vat": "price_ex_vat",
        }.get(h["price"], h["price"] + " (ex VAT)")
    elif h["price"] in ("price_ex_vat", "unit_price_ex_vat"):
        h["price"] = "price_inc_vat"
    out_dir.mkdir(parents=True, exist_ok=True)

    price_index = {(x["sku"], x["price_list"]): x for x in t.prices}

    def price_for(sku: str, lst: str = "standard") -> float:
        p = price_index[(sku, lst)]
        return float(round(p["amount_ex"] * 1.07, 2) if vat else p["amount_ex"])

    # ---- products sheet -------------------------------------------------------------------
    prod_rows: list[list[Any]] = []
    cols = [h["sku"], h["name"], h["cat"], h["brand"], h["barcode"], h["unit"]]
    inline_price = v.archetype in ("express_th", "handmade_excel", "odoo_like", "csv_vendor", "promo_heavy")
    if inline_price:
        cols.append(h["price"])
    cols.append(h["status"])
    if "empty_columns" in pert:
        cols.insert(3, "")
    for p in t.products:
        status = {
            "active": {"th": "ขาย", "en": "Y"},
            "inactive": {"th": "ไม่ขาย", "en": "N"},
            "discontinued": {"th": "เลิกขาย", "en": "N"},
        }[p["status"]][v.lang if v.archetype != "sap_like" else "en"]
        if v.archetype == "sap_like":
            status = "X" if p["status"] == "discontinued" else ""
        if v.archetype == "dynamics_like":
            status = "Yes" if p["status"] != "active" else "No"
        if v.archetype == "odoo_like":
            status = "TRUE" if p["status"] == "active" else "FALSE"
        row = [
            fmt_sku(p["sku"], rng, pert),
            p["name"],
            p["category_th"] if v.lang == "th" else p["category_en"],
            p["brand"],
            p["barcode"],
            p["unit_th"] if v.lang == "th" else p["unit_en"],
        ]
        if inline_price:
            row.append(fmt_num(price_for(p["sku"]), rng, pert))
        row.append(status)
        if "empty_columns" in pert:
            row.insert(3, None)
        prod_rows.append(row)
    if "duplicate_rows" in pert and prod_rows:
        prod_rows.insert(len(prod_rows) // 2, list(prod_rows[0]))
        v.notes.append("duplicate_rows: first product row duplicated")

    sheets: list[tuple[str, list[list[Any]], dict[str, Any]]] = []
    title = {
        "th": f"รายงานสินค้า ประจำเดือน {TH_MONTH_ABBR[9]} {2569 + v.round_no // 26}",
        "en": f"Product Master Export — Round {v.round_no}",
    }[v.lang]
    pre: list[list[Any]] = []
    if "title_rows" in pert:
        pre += [[title], [f"Generated {dt.date(2026, 10, 1 + v.round_no).isoformat()}"]]
    if "header_offset" in pert:
        pre += [[None]] * rng.randrange(1, 4)
    body = [*pre, cols, *prod_rows]
    if "totals_row" in pert:
        body.append(
            ["รวม" if v.lang == "th" else "Total", None, None, None, None, None]
            + ([sum(price_for(p["sku"]) for p in t.products)] if inline_price else [])
            + [None]
        )
    sheets.append(
        (
            {"th": "สินค้า", "en": "Products"}[v.lang] if v.archetype != "sap_like" else "MARA",
            body,
            {"merged_title": "merged_title" in pert and "title_rows" in pert},
        )
    )

    # ---- pricing sheet (separate) ---------------------------------------------------------
    if not inline_price or v.archetype in ("sap_like", "dynamics_like", "crosstab_months"):
        if v.archetype == "crosstab_months":
            months = ["Oct-26", "Nov-26", "Dec-26"]
            rows: list[list[Any]] = [[h["sku"], *months]]
            for p in t.products:
                base = price_for(p["sku"])
                rows.append(
                    [
                        fmt_sku(p["sku"], rng, pert),
                        fmt_num(base, rng, pert),
                        fmt_num(round(base * 1.02, 2), rng, pert),
                        fmt_num(round(base * 1.03, 2), rng, pert),
                    ]
                )
            sheets.append(("Price by Month", rows, {}))
            v.notes.append("crosstab: price per month columns; expected uses first month")
        else:
            rows = [[h["sku"], h["list"], h["price"], h["eff"]]]
            for pr in t.prices:
                amt = round(pr["amount_ex"] * 1.07, 2) if vat else pr["amount_ex"]
                rows.append(
                    [
                        fmt_sku(pr["sku"], rng, pert),
                        pr["price_list"]
                        if v.lang == "en"
                        else {"standard": "ปลีก", "wholesale": "ส่ง"}[pr["price_list"]],
                        fmt_num(amt, rng, pert),
                        fmt_date(pr["effective_from"], rng, pert, v.lang),
                    ]
                )
            sheets.append(
                ({"th": "ราคา", "en": "Pricing"}[v.lang] if v.archetype != "sap_like" else "A304", rows, {})
            )

    # ---- stock sheet --------------------------------------------------------------------------
    rows = [[h["sku"], h["wh"], h["qty"]]]
    for s in t.stock:
        rows.append(
            [
                fmt_sku(s["sku"], rng, pert),
                s["warehouse"],
                fmt_num(s["qty"], rng, pert, decimals=0) if "thai_digits" in pert else s["qty"],
            ]
        )
    if "blank_rows" in pert:
        rows.insert(3, [None, None, None])
    sheets.append(({"th": "สต็อก", "en": "Stock"}[v.lang] if v.archetype != "sap_like" else "MARD", rows, {}))

    # ---- promotions sheet ---------------------------------------------------------------------
    if t.promos:
        if v.lang == "th":
            ph = [
                "รหัสสินค้า",
                "รหัสโปร",
                "ประเภทโปร",
                "ส่วนลด %",
                "ส่วนลด(บาท)",
                "ราคาโปร",
                "ซื้อ",
                "แถม",
                "เริ่ม",
                "สิ้นสุด",
            ]
            type_map = {
                "percent_off": "ลด%",
                "amount_off": "ลดบาท",
                "buy_x_get_y": "ซื้อแถม",
                "fixed_price": "ราคาพิเศษ",
            }
        else:
            ph = [
                "SKU",
                "Promo Code",
                "Promo Type",
                "Discount %",
                "Discount Amount",
                "Promo Price",
                "Buy",
                "Get",
                "Start Date",
                "End Date",
            ]
            type_map = {
                "percent_off": "Percent",
                "amount_off": "Amount",
                "buy_x_get_y": "Buy X Get Y",
                "fixed_price": "Fixed Price",
            }
        rows = []
        if "title_rows" in pert:
            rows += [[{"th": "โปรโมชั่น Q4", "en": "Promotions Q4"}[v.lang]], [None]]
        rows.append(ph)
        for pr in t.promos:
            rows.append(
                [
                    fmt_sku(pr["sku"], rng, pert),
                    pr["promo_code"],
                    type_map[pr["type"]],
                    f"{pr['pct']}%" if pr["pct"] is not None and rng.random() < 0.5 else pr["pct"],
                    pr["amount"],
                    pr["promo_price"],
                    pr["buy"],
                    pr["get"],
                    fmt_date(pr["start"], rng, pert, v.lang),
                    fmt_date(pr["end"], rng, pert, v.lang),
                ]
            )
        if "multi_table_sheet" in pert:
            # a second small table below: channel list (noise)
            rows += [[None]] * 3 + [
                ["ช่องทาง" if v.lang == "th" else "Channel", "คำอธิบาย" if v.lang == "th" else "Description"],
                ["ONLINE", "ขายออนไลน์"],
                ["STORE", "หน้าร้าน"],
            ]
        sheets.append(({"th": "โปรโมชั่น", "en": "Promotions"}[v.lang], rows, {}))

    # ---- write ----------------------------------------------------------------------------------
    if v.archetype == "csv_vendor":
        enc_cycle = ["utf-8-sig", "cp874", "utf-8"]
        delim = ";" if "renamed_columns" in pert else ","
        for i, (name, rows, _) in enumerate(sheets):
            fname = f"{re.sub(r'[^A-Za-z0-9ก-๙]+', '_', name).lower()}_{dt.date(2026, 10, 1 + v.round_no).strftime('%Y%m%d')}.csv"
            enc = enc_cycle[i % len(enc_cycle)]
            with (out_dir / fname).open("w", encoding=enc, newline="", errors="replace") as f:
                w = csv.writer(f, delimiter=delim)
                for r in rows:
                    w.writerow(["" if c is None else c for c in r])
            v.files.append(fname)
        v.notes.append(f"csv delimiter={delim!r} encodings={enc_cycle}")
    else:
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        for name, rows, opts in sheets:
            ws = wb.create_sheet(name[:31])
            for r in rows:
                ws.append(r)
            if opts.get("merged_title") and rows and rows[0] and rows[0][0]:
                ws.merge_cells(
                    start_row=1,
                    start_column=1,
                    end_row=1,
                    end_column=max(3, len(rows[-1]) if rows[-1] else 3),
                )
                ws["A1"].font = Font(bold=True, size=14)
            hdr_idx = next(
                (i for i, r in enumerate(rows) if r and sum(1 for c in r if c not in (None, "")) >= 3), 0
            )
            for c in range(1, len(rows[hdr_idx]) + 1):
                ws.cell(row=hdr_idx + 1, column=c).fill = PatternFill("solid", fgColor="DDEBF7")
                ws.cell(row=hdr_idx + 1, column=c).font = Font(bold=True)
        if "hidden_sheet" in pert:
            hs = wb.create_sheet("_calc")
            hs.append(["internal", "do not use"])
            hs.append([1, "=A2*2"])
            hs.sheet_state = "hidden"
        stem = {
            "th": f"สินค้า_{TH_MONTH_ABBR[9]}{69 + v.round_no // 26}_{v.customer_code}",
            "en": f"export_{v.customer_code}_{dt.date(2026, 10, 1 + v.round_no).strftime('%Y%m%d')}",
        }[v.lang]
        if v.archetype == "sap_like":
            stem = f"SAP_EXPORT_{v.customer_code}_{dt.date(2026, 10, 1 + v.round_no).strftime('%Y%m%d')}"
        fname = f"{stem}.xlsx"
        wb.save(out_dir / fname)
        v.files.append(fname)
    v.notes.append(f"vat_inclusive={vat}")


# --------------------------------------------------------------------------------------------
# Expected target records
# --------------------------------------------------------------------------------------------
def expected_records(v: Variant) -> list[dict[str, Any]]:
    assert v.truth is not None
    t = v.truth
    out = []
    prices_by: dict[str, list[dict[str, Any]]] = {}
    for pr in t.prices:
        prices_by.setdefault(pr["sku"], []).append(pr)
    stock_by: dict[str, list[dict[str, Any]]] = {}
    for st in t.stock:
        stock_by.setdefault(st["sku"], []).append(st)
    promos_by: dict[str, list[dict[str, Any]]] = {}
    for pm in t.promos:
        promos_by.setdefault(pm["sku"], []).append(pm)
    for p in t.products:
        rec: dict[str, Any] = {
            "sku": p["sku"],
            "product_name": p["name"],
            "brand": p["brand"],
            "barcode": p["barcode"],
            "category": [p["category_th"] if v.lang == "th" else p["category_en"]],
            "unit": {"sell_unit": p["unit_code"], **({"pack_size": p["pack"]} if p["pack"] else {})},
            "currency": "THB",
            "status": p["status"],
            "prices": [
                {
                    "price_list": pr["price_list"],
                    "amount": round(pr["amount_ex"] * 1.07, 2) if v.vat_inclusive else pr["amount_ex"],
                    "vat_included": v.vat_inclusive,
                    "effective_from": pr["effective_from"].isoformat(),
                }
                for pr in prices_by.get(p["sku"], [])
            ],
            "inventory": [
                {"warehouse_code": s["warehouse"], "qty": s["qty"]} for s in stock_by.get(p["sku"], [])
            ],
        }
        promos = promos_by.get(p["sku"], [])
        if promos:
            rec["promotions"] = [
                {
                    k: v_
                    for k, v_ in {
                        "promo_code": pr["promo_code"],
                        "type": pr["type"],
                        "discount_percent": pr["pct"],
                        "discount_amount": pr["amount"],
                        "promo_price": pr["promo_price"],
                        "buy_qty": pr["buy"],
                        "get_qty": pr["get"],
                        "start_date": pr["start"].isoformat(),
                        "end_date": pr["end"].isoformat(),
                    }.items()
                    if v_ is not None
                }
                for pr in promos
            ]
        out.append(rec)
    return out


# --------------------------------------------------------------------------------------------
# Corpus plan
# --------------------------------------------------------------------------------------------
def plan_corpus(seed: int, n_variants: int, *, sequences: bool = True) -> list[Variant]:
    """``n_variants`` total; the last 12 are the three returning-customer sequences unless ``sequences``
    is off (benchmarks want N independent single-round customers)."""
    rng = _rng(seed, "plan")
    variants: list[Variant] = []
    # 1) single-round variants across archetypes
    i = 0
    while len(variants) < max(0, n_variants - (12 if sequences else 0)):
        arche = ARCHETYPES[i % len(ARCHETYPES)]
        lang = (
            "th"
            if arche in ("express_th", "handmade_excel", "promo_heavy")
            else rng.choice(["th", "en"])
            if arche != "sap_like"
            else "en"
        )
        k = rng.randrange(2, 6)
        perts = rng.sample(PERTURBATIONS, k)
        if arche == "csv_vendor":
            perts = [p for p in perts if p not in ("merged_title", "hidden_sheet", "multi_table_sheet")]
        variants.append(
            Variant(
                f"v{len(variants):03d}_{arche}",
                f"C{100 + len(variants):03d}",
                arche,
                sorted(set(perts) - {"renamed_columns"}),
                0,
                None,
                None,
                lang=lang,
                vat_inclusive=rng.random() < 0.7,
            )
        )
        i += 1
    # 2) returning-customer sequences: r1 → r2 unchanged → r3 renamed → r4 wrong file
    for s in range(3 if sequences else 0):
        arche = ["express_th", "odoo_like", "csv_vendor"][s]
        code = f"C{900 + s:03d}"
        lang = "th" if arche == "express_th" else "en"
        base_perts = sorted(
            rng.sample([p for p in PERTURBATIONS if p not in ("renamed_columns", "duplicate_rows")], 3)
        )
        for rnd, rel, extra in [
            (0, None, []),
            (1, "same_customer_unchanged", []),
            (2, "renamed", ["renamed_columns"]),
            (3, "wrong_file", []),
        ]:
            v = Variant(
                f"seq{s}_r{rnd}_{arche}",
                code,
                arche if rel != "wrong_file" else ARCHETYPES[(ARCHETYPES.index(arche) + 3) % len(ARCHETYPES)],
                sorted(set(base_perts + extra)),
                rnd,
                f"seq{s}",
                rel,
                lang=lang if rel != "wrong_file" else "en",
                vat_inclusive=True,
            )
            if rel == "wrong_file":
                v.notes.append("WRONG FILE: this file belongs to another ERP format and must be flagged")
            variants.append(v)
    return variants


def generate_corpus(
    out: Path,
    *,
    seed: int = 42,
    n_variants: int = 30,
    scale: str = "small",
    rows: int | None = None,
    sequences: bool = True,
) -> dict[str, Any]:
    """``scale`` picks a row range; ``rows`` pins an exact row count (``forma bench --rows``)."""
    out.mkdir(parents=True, exist_ok=True)
    variants = plan_corpus(seed, n_variants, sequences=sequences)
    rows_range = {"small": (120, 400), "medium": (3000, 6000), "perf": (100_000, 100_000)}[scale]
    rows_range = (rows, rows) if rows else rows_range
    index: list[dict[str, Any]] = []
    for v in variants:
        rng = _rng(seed, v.variant_id)
        size_rng = (
            _rng(seed, f"{v.sequence}:size") if v.sequence and v.expected_relation != "wrong_file" else rng
        )
        n = (
            size_rng.randrange(rows_range[0], rows_range[1] + 1)
            if rows_range[0] != rows_range[1]
            else rows_range[0]
        )
        # same sequence shares one truth for r0..r2 (so change intelligence shows small deltas), wrong file gets a fresh truth
        truth_seed = (
            f"{v.sequence}:truth"
            if v.sequence and v.expected_relation != "wrong_file"
            else f"{v.variant_id}:truth"
        )
        v.truth = make_truth(
            _rng(seed, truth_seed), n, lang=v.lang, n_warehouses=2 if v.archetype != "sap_like" else 3
        )
        if v.sequence and v.round_no in (1, 2) and v.expected_relation != "wrong_file":
            # round-over-round business changes: a few prices move, one SKU added, one removed
            t = v.truth
            for pr in t.prices[: max(1, len(t.prices) // 20)]:
                pr["amount_ex"] = round(pr["amount_ex"] * (1.05 if v.round_no == 1 else 0.97), 2)
            if t.products:
                gone = t.products.pop()
                t.prices = [p for p in t.prices if p["sku"] != gone["sku"]]
                t.stock = [s for s in t.stock if s["sku"] != gone["sku"]]
                t.promos = [p for p in t.promos if p["sku"] != gone["sku"]]
        vdir = out / v.variant_id
        vdir.mkdir(parents=True, exist_ok=True)
        render_variant(v, vdir / "files", rng)
        exp = expected_records(v)
        (vdir / "expected_output.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in exp) + "\n", encoding="utf-8"
        )
        manifest = {
            "variant_id": v.variant_id,
            "customer_code": v.customer_code,
            "archetype": v.archetype,
            "perturbations": v.perturbations,
            "round_no": v.round_no,
            "sequence": v.sequence,
            "expected_relation": v.expected_relation,
            "files": v.files,
            "lang": v.lang,
            "vat_inclusive": v.vat_inclusive,
            "n_products": len(v.truth.products),
            "n_prices": len(v.truth.prices),
            "n_stock": len(v.truth.stock),
            "n_promos": len(v.truth.promos),
            "notes": v.notes,
            "expected_quality": {"duplicate_rows": 1 if "duplicate_rows" in v.perturbations else 0},
        }
        (vdir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        index.append(manifest)
    (out / "index.json").write_text(
        json.dumps({"seed": seed, "scale": scale, "variants": index}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    by_arch: dict[str, int] = {}
    for m in index:
        by_arch[m["archetype"]] = by_arch.get(m["archetype"], 0) + 1
    return {"variants": len(index), "by_archetype": by_arch, "sequences": 3, "out": str(out)}
