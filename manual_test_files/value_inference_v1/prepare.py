"""Prepare synthetic CSV evidence cases and separate oracles. Does NOT call FORMA or an LLM.

Run only when evaluation preparation is authorized. Refuses to overwrite a sealed suite.
Randomized values/layouts are not a genuinely unseen family: report this limitation explicitly.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import random
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    if (ROOT / "manifest.json").exists() or (ROOT / "inputs").exists():
        raise SystemExit("Suite already exists: preserve it for before/after comparisons")
    seed = args.seed if args.seed is not None else secrets.randbits(32)
    rng = random.Random(seed)
    manifest = {
        "suite": "value_inference_v1",
        "seed": seed,
        "synthetic": True,
        "real_customer_export": False,
        "rounds": [],
    }
    base = [
        {
            "sku": f"{i + 101:07d}",
            "name": f"Item {i + 1}",
            "price": round(rng.uniform(10, 900), 2),
            "stock": rng.randrange(1, 300),
        }
        for i in range(24)
    ]
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["sku", "name", "price", "stock"],
        "properties": {
            "sku": {
                "type": "string",
                "description": "Product identifier; preserve leading zeros.",
                "x-forma": {"semantic": "identity.sku"},
            },
            "name": {"type": "string", "description": "Product display name."},
            "price": {
                "type": ["number", "null"],
                "description": "Selling price per piece; null if no supported value can be established.",
            },
            "stock": {
                "type": ["integer", "null"],
                "description": "Stock in individual pieces; null if conversion is unavailable.",
            },
        },
        "x-forma": {"identity_keys": ["/sku"]},
    }

    def case(cid, tables, expected, task, questions=None, contract=None, relation=None):
        files = []
        for name, header, rows in tables:
            path = ROOT / "inputs" / cid / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(header)
                writer.writerows(rows)
            files.append(
                {
                    "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        task_path = ROOT / "inputs" / cid / "task.md"
        task_path.write_text(task, encoding="utf-8")
        contract = copy.deepcopy(contract or schema)
        save(ROOT / "schemas" / f"{cid}.json", contract)
        save(ROOT / "oracle" / cid / "expected.json", expected)
        save(ROOT / "oracle" / cid / "review.json", {"questions": questions or [], "relation": relation})
        manifest["rounds"].append(
            {
                "id": cid + "_r1",
                "case": cid,
                "round": "r1",
                "title": cid,
                "schema": f"schemas/{cid}.json",
                "task": f"inputs/{cid}/task.md",
                "expected": f"oracle/{cid}/expected.json",
                "review": f"oracle/{cid}/review.json",
                "files": files,
                "schema_sha256": hashlib.sha256((ROOT / "schemas" / f"{cid}.json").read_bytes()).hexdigest(),
            }
        )

    rows = [[r["sku"], r["name"], r["price"], r["stock"]] for r in base]
    task = "Convert the supplied export to the JSON contract. Read supporting files for column definitions; do not guess missing business facts."
    dictionary = (
        "definitions.csv",
        ["Column", "Definition"],
        [
            ["A", "Product code"],
            ["B", "Display name"],
            ["C", "Selling price per piece"],
            ["D", "Stock in pieces"],
        ],
    )
    case("V01", [("data.csv", ["A", "B", "C", "D"], rows), dictionary], base, task)
    shuffled = copy.deepcopy(rows)
    rng.shuffle(shuffled)
    case(
        "V02",
        [
            ("moved.csv", ["D", "B", "A", "C"], [[r[3], r[1], r[0], r[2]] for r in shuffled]),
            dictionary,
            ("unrelated.csv", ["Metric", "Value"], [["Visitors", 1000], ["Clicks", 123]]),
        ],
        base,
        task,
        relation={"same_output_as": "V01"},
    )
    # Same code in two companies: a one-column join creates false matches.
    multi, masters, balances = [], [], []
    for co in ("N", "S"):
        for r in base[:12]:
            qty = r["stock"] + (7 if co == "S" else 0)
            masters.append([co, r["sku"], r["name"], r["price"]])
            balances.append([r["sku"], qty, co])
            multi.append({**r, "sku": co + ":" + r["sku"], "stock": qty})
    rng.shuffle(balances)
    case(
        "V03",
        [
            ("master.csv", ["Company", "Item", "Name", "Selling price"], masters),
            ("stock.csv", ["Item", "Pieces", "Company"], balances),
        ],
        multi,
        task + " Output sku is Company:Item. Codes are unique within a company, not globally.",
    )
    factors = [[r["sku"], rng.choice([6, 12, 24])] for r in base]
    cartons = [[r["sku"], r["name"], r["price"], r["stock"], "CTN"] for r in base]
    case(
        "V04",
        [
            ("export.csv", ["Code", "Name", "Price per piece", "Qty", "UOM"], cartons),
            ("units.csv", ["Code", "Pieces per CTN"], factors),
        ],
        [{**r, "stock": r["stock"] * factors[i][1]} for i, r in enumerate(base)],
        task,
    )
    case(
        "V05",
        [("export.csv", ["Code", "Name", "Price per piece", "Qty", "UOM"], cartons)],
        [{**r, "stock": None} for r in base],
        task,
        [
            {
                "target": "/stock",
                "fact": "Pieces per carton missing; do not assume a factor",
                "needs_review": True,
            }
        ],
    )
    case(
        "V06",
        [
            (
                "export.csv",
                ["Code", "Name", "Retail", "Wholesale", "Stock pieces"],
                [[r["sku"], r["name"], r["price"], round(r["price"] * 0.8, 2), r["stock"]] for r in base],
            )
        ],
        base,
        task,
        [{"target": "/price", "fact": "Operator must choose retail versus wholesale", "needs_review": True}],
    )
    # Decimal stock, integer price: number shapes must not override source definitions.
    decimal_schema = copy.deepcopy(schema)
    decimal_schema["properties"]["stock"] = {
        "type": ["number", "null"],
        "description": "Available weight in kilograms.",
    }
    decimal_rows = [{**r, "price": int(r["price"]), "stock": r["stock"] / 10} for r in base]
    case(
        "V07",
        [
            (
                "export.csv",
                ["Code", "Name", "Price", "Weight kg"],
                [[r["sku"], r["name"], r["price"], r["stock"]] for r in decimal_rows],
            )
        ],
        decimal_rows,
        task,
        contract=decimal_schema,
    )
    date_schema = {
        "type": "object",
        "required": ["ref", "date"],
        "properties": {"ref": {"type": "string"}, "date": {"type": ["string", "null"], "format": "date"}},
        "x-forma": {"identity_keys": ["/ref"]},
    }
    case(
        "V08",
        [("dates.csv", ["Reference", "Date"], [[r["sku"], "03/04/2026"] for r in base])],
        [{"ref": r["sku"], "date": "2026-04-03"} for r in base],
        "Convert dates to ISO. The source has no locale information.",
        [{"target": "/date", "fact": "Day/month order cannot be established", "needs_review": True}],
        date_schema,
    )
    late_rows = [
        {"sku": f"{i + 1:07d}", "name": f"Late item {i}", "price": 10.5, "stock": 2} for i in range(451)
    ]
    case(
        "V09",
        [
            (
                "export.csv",
                ["Code", "Name", "Price per piece", "Qty", "UOM"],
                [
                    [r["sku"], r["name"], r["price"], 2, "CTN" if i == 450 else "PCS"]
                    for i, r in enumerate(late_rows)
                ],
            ),
            ("units.csv", ["Unit", "Pieces per unit"], [["PCS", 1], ["CTN", 12]]),
        ],
        [{**r, "stock": 24 if i == 450 else 2} for i, r in enumerate(late_rows)],
        task,
    )
    case(
        "V10",
        [("export.csv", ["Code", "Name", "Stock pieces"], [[r["sku"], r["name"], r["stock"]] for r in base])],
        [{**r, "price": None} for r in base],
        task,
        [
            {
                "target": "/price",
                "fact": "No price column exists; never map stock as price",
                "needs_review": False,
            }
        ],
    )
    invoice_schema = {
        "type": "object",
        "required": ["ref", "net", "tax", "total"],
        "properties": {
            "ref": {"type": "string"},
            "net": {"type": "number"},
            "tax": {"type": "number"},
            "total": {"type": "number"},
        },
        "x-forma": {"identity_keys": ["/ref"]},
    }
    invoices = [
        {"ref": r["sku"], "net": r["stock"] * 100, "tax": r["stock"] * 7, "total": r["stock"] * 107}
        for r in base
    ]
    case(
        "V11",
        [
            (
                "billing.csv",
                ["Document", "Tax", "Total", "Net"],
                [[r["ref"], r["tax"], r["total"], r["net"]] for r in invoices],
            )
        ],
        invoices,
        "Map invoice amounts. Net excludes tax; total includes tax. Preserve document identifiers.",
        contract=invoice_schema,
    )
    code_schema = copy.deepcopy(schema)
    code_schema["required"].append("barcode")
    code_schema["properties"]["barcode"] = {
        "type": "string",
        "description": "Barcode, distinct from internal product code.",
    }
    with_barcodes = [{**r, "barcode": str(8850000000000 + i)} for i, r in enumerate(base)]
    case(
        "V12",
        [
            (
                "codes.csv",
                ["Internal code", "EAN", "Name", "Price", "Stock pieces"],
                [[r["sku"], r["barcode"], r["name"], r["price"], r["stock"]] for r in with_barcodes],
            )
        ],
        with_barcodes,
        task,
        contract=code_schema,
    )
    save(ROOT / "manifest.json", manifest)


if __name__ == "__main__":
    main()
