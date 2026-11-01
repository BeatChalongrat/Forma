"""The small catalogue every test file is built from, and the JSON each should produce.

expected.json comes from here, never from FORMA's output, so a pass means FORMA agrees with an answer it did
not write.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class Item:
    sku: str
    name: str
    name_en: str
    category: str
    category_en: str
    brand: str
    barcode: str
    unit: str  # as the ERP writes it
    unit_en: str
    sell_unit: str  # what the contract wants
    pack: int | None
    price: float  # THB including VAT
    status: str
    bkk: int
    cnx: int


ITEMS = [
    Item("100101", "น้ำดื่มตราบัว 600 มล.", "Bua Water 600ml", "เครื่องดื่ม", "Beverages", "บัว", "8850001000011", "ขวด", "BTL", "BOTTLE", None, 7.0, "active", 480, 120),
    Item("100102", "น้ำดื่มตราบัว 1.5 ลิตร", "Bua Water 1.5L", "เครื่องดื่ม", "Beverages", "บัว", "8850001000028", "ขวด", "BTL", "BOTTLE", None, 14.0, "active", 360, 96),
    Item("100103", "น้ำดื่มตราบัว แพ็ค 6", "Bua Water Pack 6", "เครื่องดื่ม", "Beverages", "บัว", "8850001000035", "แพ็ค 6", "PACK 6", "PACK", 6, 39.0, "active", 150, 40),
    Item("100104", "ชาเขียวใบชา 350 มล.", "Baicha Green Tea 350ml", "เครื่องดื่ม", "Beverages", "ใบชา", "8850002000010", "ขวด", "BTL", "BOTTLE", None, 20.0, "active", 240, 72),
    Item("100105", "กาแฟกระป๋องดอยฟ้า 180 มล.", "Doifa Canned Coffee 180ml", "เครื่องดื่ม", "Beverages", "ดอยฟ้า", "8850003000019", "ลัง(24)", "CTN 24", "CARTON", 24, 336.0, "active", 35, 12),
    Item("200101", "ข้าวหอมมะลิทุ่งทอง 5 กก.", "Thungthong Jasmine Rice 5kg", "อาหารแห้ง", "Dry Food", "ทุ่งทอง", "8851001000018", "ชิ้น", "PCS", "PCS", None, 189.0, "active", 80, 25),
    Item("200102", "บะหมี่กึ่งสำเร็จรูปรสต้มยำ", "Tom Yum Instant Noodles", "อาหารแห้ง", "Dry Food", "ทุ่งทอง", "8851001000025", "กล่อง", "BOX", "BOX", None, 165.0, "active", 60, 20),
    Item("200103", "น้ำมันพืชทุ่งทอง 1 ลิตร", "Thungthong Vegetable Oil 1L", "อาหารแห้ง", "Dry Food", "ทุ่งทอง", "8851001000032", "ขวด", "BTL", "BOTTLE", None, 59.5, "active", 110, 30),
    Item("300101", "สบู่เหลวใบบัว 500 มล.", "Baibua Liquid Soap 500ml", "ของใช้ในบ้าน", "Household", "ใบบัว", "8852001000017", "ขวด", "BTL", "BOTTLE", None, 89.0, "active", 70, 18),
    Item("300102", "กระดาษทิชชูนุ่มนวล แพ็ค 6", "Numnuan Tissue Pack 6", "ของใช้ในบ้าน", "Household", "นุ่มนวล", "8852002000016", "แพ็ค 6", "PACK 6", "PACK", 6, 99.0, "active", 90, 28),
    Item("300103", "ผงซักฟอกสะอาดดี 3 กก.", "Sa-at Dee Detergent 3kg", "ของใช้ในบ้าน", "Household", "สะอาดดี", "8852003000015", "ชิ้น", "PCS", "PCS", None, 215.0, "inactive", 15, 4),
    Item("300104", "ถ่าน AA พลังดี", "Palangdee AA Battery", "ของใช้ในบ้าน", "Household", "พลังดี", "8852004000014", "โหล", "DZ", "DOZEN", None, 120.0, "discontinued", 0, 6),
]

D1 = dt.date(2026, 10, 1)
D31 = dt.date(2026, 10, 31)
PROMOS: list[dict[str, Any]] = [
    {"sku": "100104", "promo_code": "PR-2601", "type": "percent_off", "pct": 10, "amount": None, "promo_price": None, "buy": None, "get": None, "start": D1, "end": D31},
    {"sku": "200103", "promo_code": "PR-2602", "type": "amount_off", "pct": None, "amount": 5.0, "promo_price": None, "buy": None, "get": None, "start": D1, "end": D31},
    {"sku": "300101", "promo_code": "PR-2603", "type": "buy_x_get_y", "pct": None, "amount": None, "promo_price": None, "buy": 2, "get": 1, "start": D1, "end": D31},
    {"sku": "300102", "promo_code": "PR-2604", "type": "fixed_price", "pct": None, "amount": None, "promo_price": 89.0, "buy": None, "get": None, "start": D1, "end": D31},
]

STATUS_TH = {"active": "ขาย", "inactive": "ไม่ขาย", "discontinued": "เลิกขาย"}
PROMO_TH = {"percent_off": "ลด%", "amount_off": "ลดบาท", "buy_x_get_y": "ซื้อแถม", "fixed_price": "ราคาพิเศษ"}


def with_prices(items: list[Item], change: dict[str, float]) -> list[Item]:
    return [replace(i, price=change.get(i.sku, i.price)) for i in items]


def promo_json(p: dict[str, Any]) -> dict[str, Any]:
    out = {
        "promo_code": p["promo_code"],
        "type": p["type"],
        "discount_percent": p["pct"],
        "discount_amount": p["amount"],
        "promo_price": p["promo_price"],
        "buy_qty": p["buy"],
        "get_qty": p["get"],
        "start_date": p["start"].isoformat(),
        "end_date": p["end"].isoformat(),
    }
    return {k: v for k, v in out.items() if v is not None}


def record(
    item: Item,
    *,
    vat: bool = True,
    price: float | None = None,
    price_list: str = "standard",
    effective_from: str | None = None,
    promos: list[dict[str, Any]] | None = None,
    stock: bool = True,
    lang: str = "th",
    currency: str = "THB",
) -> dict[str, Any]:
    amount: dict[str, Any] = {
        "price_list": price_list,
        "amount": item.price if price is None else price,
        "vat_included": vat,
        "vat_rate": 0.07,
    }
    if effective_from:
        amount["effective_from"] = effective_from
    out: dict[str, Any] = {
        "sku": item.sku,
        "product_name": item.name if lang == "th" else item.name_en,
        "brand": item.brand,
        "barcode": item.barcode,
        "category": [item.category if lang == "th" else item.category_en],
        "unit": {"sell_unit": item.sell_unit, **({"pack_size": item.pack} if item.pack else {})},
        "currency": currency,
        "status": item.status,
        "prices": [amount],
    }
    if stock:
        out["inventory"] = [
            {"warehouse_code": "BKK", "qty": item.bkk},
            {"warehouse_code": "CNX", "qty": item.cnx},
        ]
    mine = [promo_json(p) for p in (PROMOS if promos is None else promos) if p["sku"] == item.sku]
    if mine:
        out["promotions"] = mine
    return out


def records(items: list[Item] | None = None, **kw: Any) -> list[dict[str, Any]]:
    return [record(i, **kw) for i in (ITEMS if items is None else items)]


def canonical(value: Any) -> Any:
    """Compare meaning, not spelling: nulls and empty lists say the same as a missing key, and order of keys
    or of records does not matter."""
    if isinstance(value, dict):
        kept = {k: canonical(v) for k, v in value.items()}
        return {k: v for k, v in sorted(kept.items()) if v not in (None, [], {})}
    if isinstance(value, list):
        return [canonical(v) for v in value]
    if isinstance(value, float) and value == int(value):
        return int(value)
    return value


def by_identity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """source_meta is FORMA's own note of where a record came from, different in every run."""
    return {str(r.get("sku")): canonical({k: v for k, v in r.items() if k != "source_meta"}) for r in rows}
