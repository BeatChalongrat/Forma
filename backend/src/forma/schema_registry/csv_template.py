"""CSV target templates become ordinary, explicitly confirmed JSON Schema contracts.

No model calls and no inferred business defaults. Samples suggest types, never requiredness,
identity, VAT or currency. Arbitrary CSV headers are mapped to safe internal field names.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from forma.core.errors import ValidationFailed

MAX_BYTES = 1_048_576
MAX_ROWS = 2000
MAX_COLUMNS = 100
DELIMITERS = (",", ";", "\t", "|")


class CsvTemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=MAX_BYTES)
    delimiter: Literal["auto", ",", ";", "\t", "|"] = "auto"


class CsvColumnIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    data_type: Literal["string", "integer", "number", "boolean", "date"]
    required: bool
    identity: bool = False
    semantic: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=1000)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    confirmed: bool = False
    fixed_value: str | None = Field(default=None, max_length=200)


class CsvContractIn(CsvTemplateIn):
    schema_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    title: str = Field(min_length=1, max_length=200)
    columns: list[CsvColumnIn] = Field(min_length=1, max_length=MAX_COLUMNS)


def _read(body: CsvTemplateIn) -> tuple[str, list[str], list[list[str]]]:
    if len(body.content.encode("utf-8")) > MAX_BYTES:
        raise ValidationFailed("แม่แบบ CSV ต้องไม่เกิน 1 MB ใช้เฉพาะหัวคอลัมน์และแถวตัวอย่าง")
    text = body.content.removeprefix("\ufeff")
    if "\x00" in text or "\ufffd" in text:
        raise ValidationFailed("อ่านตัวอักษรไม่ครบ กรุณาเลือก encoding ให้ตรงหรือบันทึกเป็น CSV UTF-8")
    delimiter: str = body.delimiter
    if delimiter == "auto":
        try:
            delimiter = csv.Sniffer().sniff(text[:65536], delimiters="".join(DELIMITERS)).delimiter
        except csv.Error:
            # A single-column template is valid; ambiguous multi-column files require a choice.
            if any(d in text for d in DELIMITERS):
                raise ValidationFailed("ระบุตัวคั่นคอลัมน์ก่อน ไม่สามารถตรวจตัวคั่นได้แน่ชัด") from None
            delimiter = ","
    try:
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
        headers = next(reader, [])
        if not headers or len(headers) > MAX_COLUMNS:
            raise ValidationFailed("แม่แบบต้องมีหัวคอลัมน์ 1–100 คอลัมน์ในแถวแรก")
        if any(not h.strip() or len(h) > 200 or any(ord(c) < 32 for c in h) for h in headers):
            raise ValidationFailed("หัวคอลัมน์ต้องไม่ว่าง ยาวไม่เกิน 200 ตัวอักษร และไม่มีการขึ้นบรรทัดใหม่")
        if len({h.strip().casefold() for h in headers}) != len(headers):
            raise ValidationFailed("ชื่อคอลัมน์ซ้ำกัน กรุณาตั้งชื่อแต่ละคอลัมน์ให้ต่างกัน")
        rows = []
        for row in reader:
            if not row:  # empty physical lines, not rows with empty cells
                continue
            if len(row) != len(headers):
                raise ValidationFailed(f"แถว {reader.line_num} มีจำนวนช่องไม่ตรงกับหัวคอลัมน์")
            rows.append(row)
            if len(rows) > MAX_ROWS:
                raise ValidationFailed("แม่แบบมีได้ไม่เกิน 2,000 แถว ใช้ตัวอย่างแทนไฟล์ข้อมูลเต็ม")
    except csv.Error as exc:
        raise ValidationFailed("รูปแบบ CSV ไม่ถูกต้อง ตรวจเครื่องหมายคำพูดและตัวคั่น") from exc
    return delimiter, headers, rows


def _suggest(values: list[str]) -> tuple[str, str]:
    if not values:
        return "string", "ไม่มีตัวอย่างค่า กรุณาระบุชนิดข้อมูล"
    if any(re.fullmatch(r"[+-]?0\d+", v) or re.fullmatch(r"\d{16,}", v) for v in values):
        return "string", "มีเลขศูนย์นำหน้าหรือรหัสยาว แนะนำเก็บเป็นข้อความเพื่อรักษาค่าเดิม"
    if all(re.fullmatch(r"-?(?:0|[1-9]\d*)", v) for v in values):
        return "integer", "ตัวอย่างเป็นจำนวนเต็ม แต่ถ้าเป็นรหัสให้เลือกข้อความ"
    if all(re.fullmatch(r"-?(?:0|[1-9]\d*)(?:\.\d+)?", v) for v in values):
        return "number", "ตัวอย่างเป็นตัวเลขแบบจุดทศนิยม กรุณายืนยันความหมายและหน่วย"
    if all(v in ("true", "false") for v in values):
        return "boolean", "ตัวอย่างใช้ true/false กรุณายืนยันว่าเป็นค่าใช่/ไม่ใช่"
    if all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) for v in values):
        try:
            for value in values:
                date.fromisoformat(value)
            return "date", "ตัวอย่างเป็นวันที่ YYYY-MM-DD กรุณายืนยัน"
        except ValueError:
            pass
    return "string", "เก็บเป็นข้อความก่อน วันที่หรือเลขที่มีตัวคั่นอาจตีความได้หลายแบบ"


def analyze_csv(body: CsvTemplateIn) -> dict[str, Any]:
    delimiter, headers, rows = _read(body)
    columns = []
    for index, name in enumerate(headers):
        values = [r[index] for r in rows if r[index].strip()]
        suggested, reason = _suggest(values)
        columns.append(
            {
                "name": name,
                "suggested_type": suggested,
                "reason": reason,
                "examples": list(dict.fromkeys(values))[:3],
                "blank_count": len(rows) - len(values),
            }
        )
    return {"delimiter": delimiter, "row_count": len(rows), "columns": columns}


def _key(name: str, index: int, used: set[str], headers: list[str]) -> str:
    # Engine paths currently use /, *, ~ and dots structurally. CSV headers remain literal on export.
    if not any(c in name for c in "/~.*") and name not in {
        "source_meta",
        "attributes",
        "__proto__",
        "constructor",
        "prototype",
    }:
        return name
    key = f"csv_column_{index + 1}"
    while key in used or key in headers:
        key += "_"
    return key


def build_csv_contract(body: CsvContractIn) -> dict[str, Any]:
    from forma.schema_registry.registry import SEMANTICS

    delimiter, headers, rows = _read(body)
    if [c.name for c in body.columns] != headers:
        raise ValidationFailed("คอลัมน์เปลี่ยนไป กรุณาอ่านแม่แบบและตรวจยืนยันใหม่")
    if not all(c.confirmed for c in body.columns):
        raise ValidationFailed("กรุณายืนยันชนิดข้อมูล ความหมาย และช่องบังคับของทุกคอลัมน์")
    identities = [i for i, c in enumerate(body.columns) if c.identity]
    if not identities:
        raise ValidationFailed("เลือกอย่างน้อยหนึ่งคอลัมน์ที่ใช้ระบุแต่ละรายการ เช่น รหัสสินค้า")
    seen: set[tuple[str, ...]] = set()
    properties: dict[str, Any] = {}
    required, identity_keys, output_columns = [], [], []
    for index, column in enumerate(body.columns):
        if column.fixed_value is not None and (
            column.data_type != "string" or not column.fixed_value.strip()
        ):
            raise ValidationFailed(f"{column.name}: ค่าที่ใช้ทุกแถวต้องเป็นข้อความที่ไม่ว่าง")
        if any(not a.strip() or len(a) > 200 for a in column.aliases):
            raise ValidationFailed(f"{column.name}: ชื่อเรียกอื่นต้องไม่ว่างและยาวไม่เกิน 200 ตัวอักษร")
        if column.identity and (not column.required or column.data_type not in ("string", "integer")):
            raise ValidationFailed(f"{column.name}: รหัสรายการต้องห้ามว่างและเป็นข้อความหรือจำนวนเต็ม")
        if column.semantic and column.semantic not in SEMANTICS:
            raise ValidationFailed(f"{column.name}: เลือกความหมายที่ระบบรองรับ หรือระบุเป็นข้อมูลอื่นพร้อมคำอธิบาย")
        if column.semantic in (
            "prices",
            "inventory",
            "promotions",
            "attributes",
            "category_path",
            "promo.bundle_skus",
            "promo.channels",
        ):
            raise ValidationFailed(f"{column.name}: แม่แบบ CSV นี้ใช้ค่าทีละช่อง ข้อมูลซ้อนให้กำหนดด้วย JSON Schema")
        if not column.semantic and not column.description.strip():
            raise ValidationFailed(f"{column.name}: กรุณาอธิบายความหมาย เพื่อให้ระบบจับคู่จากหลักฐาน")
        key = _key(column.name, index, set(properties), headers)
        examples: list[Any] = []
        for row in rows:
            value = row[index]
            if not value.strip():
                if column.required:
                    raise ValidationFailed(f"{column.name}: ตัวอย่างมีค่าว่าง แต่ตั้งเป็นช่องห้ามว่าง")
                continue
            if column.fixed_value is not None and value != column.fixed_value:
                raise ValidationFailed(f"{column.name}: ตัวอย่างขัดกับค่าที่เลือกใช้ทุกแถว กรุณาตรวจให้ตรงกัน")
            converted: Any = value
            if column.data_type in ("integer", "number"):
                pattern = (
                    r"-?(?:0|[1-9]\d*)" if column.data_type == "integer" else r"-?(?:0|[1-9]\d*)(?:\.\d+)?"
                )
                if not re.fullmatch(pattern, value) or len(value) > 100:
                    raise ValidationFailed(
                        f"{column.name}: ตัวอย่างไม่ใช่ตัวเลขที่ชัดเจน ใช้ข้อความหรือแก้ตัวอย่างให้เป็นตัวเลขมาตรฐาน"
                    )
                converted = int(value) if column.data_type == "integer" else float(value)
            elif column.data_type == "boolean":
                if value not in ("true", "false"):
                    raise ValidationFailed(f"{column.name}: ค่าใช่/ไม่ใช่ต้องใช้ true หรือ false")
                converted = value == "true"
            elif column.data_type == "date":
                try:
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                        raise ValueError()
                    date.fromisoformat(value)
                except ValueError as exc:
                    raise ValidationFailed(
                        f"{column.name}: วันที่ปลายทางต้องเป็น YYYY-MM-DD หรือเลือกข้อความพร้อมอธิบายรูปแบบ"
                    ) from exc
            if converted not in examples and len(examples) < 3:
                examples.append(converted)
        json_type = "string" if column.data_type == "date" else column.data_type
        prop: dict[str, Any] = {
            "type": json_type if column.required else [json_type, "null"],
            "title": column.name,
            "description": column.description.strip(),
            "examples": examples,
            "x-forma": {
                "synonyms_en": list(dict.fromkeys([column.name, *column.aliases])),
                "synonyms_th": list(dict.fromkeys([column.name, *column.aliases])),
            },
        }
        if column.semantic:
            prop["x-forma"]["semantic"] = column.semantic
        if column.fixed_value is not None:
            prop["const"] = column.fixed_value
            prop["default"] = column.fixed_value
        if column.data_type == "date":
            prop["format"] = "date"
        if column.required:
            required.append(key)
            if json_type == "string":
                prop["minLength"] = 1
                prop["pattern"] = r"\S"
        if column.identity:
            identity_keys.append(f"/{key}")
        properties[key] = prop
        output_columns.append({"field": key, "header": column.name})
    for row in rows:
        # Numeric identity 1 and 01 cannot slip through: numeric parsing above rejects leading zeroes.
        identity = tuple(row[i] for i in identities)
        if identity in seen:
            raise ValidationFailed("รหัสรายการซ้ำในตัวอย่าง เลือกหลายคอลัมน์ร่วมกันหากหนึ่งสินค้าแยกตามคลังหรือราคา")
        seen.add(identity)
    doc: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "title": body.title,
        "additionalProperties": False,
        "properties": properties,
        "required": required,
        "x-forma": {
            "schema_id": body.schema_id,
            "identity_keys": identity_keys,
            "output_variants": {
                "json": {"shape": "flat"},
                "csv": {"columns": output_columns, "delimiter": delimiter},
            },
            "csv_template": {"confirmed": True, "sample_rows_checked": len(rows)},
        },
    }
    doc["x-forma"]["csv_template"]["confirmation_digest"] = _digest(doc)
    return doc


def _digest(document: dict[str, Any]) -> str:
    # Version is allocated by the shared registry; the confirmed contract itself cannot change.
    def normalize(value: Any) -> Any:
        # JSON.parse/stringify in the browser represents 20.0 as 20. They are the same
        # JSON number; preserve type/meaning changes elsewhere, including boolean vs integer.
        if isinstance(value, dict):
            return {k: normalize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [normalize(v) for v in value]
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value

    value = {
        **document,
        "x-forma": {k: v for k, v in document["x-forma"].items() if k not in ("csv_template", "version")},
    }
    return hashlib.sha256(
        json.dumps(normalize(value), sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def validate_csv_contract(document: dict[str, Any]) -> None:
    xf = document.get("x-forma", {})
    if not isinstance(xf, dict):
        raise ValidationFailed("x-forma ต้องเป็น object")
    variants = xf.get("output_variants", {})
    if not isinstance(variants, dict) or not isinstance(variants.get("csv", {}), dict):
        raise ValidationFailed("output_variants และรูปแบบ CSV ต้องเป็น object")
    variant = variants.get("csv", {})
    columns = variant.get("columns")
    if columns is not None:
        props = document.get("properties", {})
        if (
            not isinstance(columns, list)
            or not columns
            or any(
                not isinstance(c, dict)
                or not isinstance(c.get("header"), str)
                or not c["header"].strip()
                or not isinstance(c.get("field"), str)
                or c["field"] not in props
                for c in columns
            )
        ):
            raise ValidationFailed("CSV columns ต้องอ้างอิงฟิลด์ปลายทางที่มีอยู่และมีชื่อหัวคอลัมน์")
        if len({c["header"].strip().casefold() for c in columns}) != len(columns) or len(
            {c["field"] for c in columns}
        ) != len(columns):
            raise ValidationFailed("CSV columns ต้องไม่มีชื่อหรือฟิลด์ซ้ำกัน")
        if set(c["field"] for c in columns) != set(props) or variant.get("explode"):
            raise ValidationFailed("แม่แบบ CSV ต้องครอบคลุมทุกฟิลด์และเป็นตารางแบน")
        if any(
            not isinstance(p, dict) or "object" in str(p.get("type")) or "array" in str(p.get("type"))
            for p in props.values()
        ):
            raise ValidationFailed("CSV columns รองรับเฉพาะค่าทีละช่อง ไม่รองรับข้อมูลซ้อน")
        if variant.get("delimiter", ",") not in DELIMITERS:
            raise ValidationFailed("ตัวคั่น CSV ไม่ถูกต้อง")
    confirmation = xf.get("csv_template")
    if confirmation is not None and (
        not isinstance(confirmation, dict)
        or confirmation.get("confirmed") is not True
        or confirmation.get("confirmation_digest") != _digest(document)
    ):
        raise ValidationFailed("แม่แบบ CSV ยังไม่ยืนยันหรือถูกแก้ไขหลังยืนยัน กรุณาตรวจแม่แบบใหม่")
