"""Target Schema Registry.

The target JSON contract is *configuration*, not code. Schemas are JSON Schema (Draft 2020-12)
documents enriched with an ``x-forma`` vendor extension that carries the semantics FORMA needs
(semantic tag, risk level, Thai/English synonyms, identity keys, output variants).

Everything downstream — mapping validation, AI prompts, UI field pickers, quality rules, exports —
goes through this module. Nothing else in the codebase may hard-code target field names.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator, FormatChecker

from forma.core.errors import NotFound, ValidationFailed

Risk = Literal["critical", "high", "medium", "low"]
RISK_ORDER: dict[str, int] = {"critical": 3, "high": 2, "medium": 1, "low": 0}
_SCHEMA_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")  # also the file name: <schema_id>.v<n>.json
# Collections FORMA knows business rules for: kind → (collection semantic, item semantic prefixes).
COLLECTION_KINDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "pricing": ("prices", ("price.",)),
    "inventory": ("inventory", ("stock.",)),
    "promotions": ("promotions", ("promo.", "discount.")),
}
# The x-forma.semantic vocabulary FORMA has rules for (Thai parsing, VAT, units, drift, change reports).
# A schema tags its fields with these; any other semantic is still matched by synonyms and field name.
SEMANTICS: dict[str, str] = {
    "sku": "record identity / item code",
    "product_name": "product name",
    "description": "long description",
    "barcode": "EAN / barcode",
    "brand": "brand",
    "category_path": "category (list or path)",
    "unit.sell": "selling unit, normalised (PCS, PACK, CARTON …)",
    "unit.pack_size": "pieces per pack, from 'ลัง(12)', '1x12' …",
    "currency": "currency code (defaults to the schema default, else THB)",
    "status": "active / inactive / discontinued (spelled as the schema enum spells them)",
    "attributes": "unmapped columns, kept as key → value",
    "price.amount": "price; at the root for one price, or inside a prices collection",
    "price.list_name": "price list (standard, wholesale, member …)",
    "price.vat_included": "true when the price includes VAT (guessed from the header)",
    "price.vat_rate": "VAT rate",
    "price.effective_from": "price valid from (date)",
    "price.effective_to": "price valid to (date)",
    "stock.warehouse": "warehouse / branch code",
    "stock.qty": "quantity on hand",
    "stock.reserved": "reserved quantity",
    "promo.code": "promotion code",
    "promo.name": "promotion name",
    "promo.type": "percent_off, amount_off, fixed_price, buy_x_get_y, bundle, tiered",
    "discount.percent": "discount in percent",
    "discount.amount": "discount in baht",
    "promo.price": "special price",
    "promo.min_qty": "minimum quantity",
    "promo.buy_qty": "buy X",
    "promo.get_qty": "get Y",
    "promo.bundle_skus": "items of a bundle",
    "promo.channels": "sales channels",
    "promo.start_date": "promotion start (date)",
    "promo.end_date": "promotion end (date)",
    "prices": "collection of prices",
    "inventory": "collection of stock rows",
    "promotions": "collection of promotions",
}

# Risk a field gets from its meaning when the schema does not say: money, identity and promotion terms are
# critical, so the policy's confidence floors protect them in any contract without extra configuration.
SEMANTIC_RISK: dict[str, Risk] = {
    "sku": "critical",
    "currency": "critical",
    "price.amount": "critical",
    "price.vat_included": "critical",
    "promo.type": "critical",
    "discount.percent": "critical",
    "discount.amount": "critical",
    "promo.price": "critical",
    "promo.start_date": "critical",
    "promo.end_date": "critical",
    "product_name": "high",
    "unit.sell": "high",
    "unit.pack_size": "high",
    "price.list_name": "high",
    "price.vat_rate": "high",
    "price.effective_from": "high",
    "price.effective_to": "high",
    "stock.warehouse": "high",
    "stock.qty": "high",
    "promo.code": "high",
    "promo.min_qty": "high",
    "promo.buy_qty": "high",
    "promo.get_qty": "high",
    "promo.bundle_skus": "high",
}


def schema_findings(schema: TargetSchema) -> list[dict[str, str]]:
    """What the web app shows before a schema is registered: how well FORMA can map onto it."""
    out: list[dict[str, str]] = []
    for f in schema.leaf_fields():
        if f.semantic is None and not (f.synonyms_en or f.synonyms_th):
            level = "warning" if f.required else "info"
            out.append(
                {
                    "level": level,
                    "pointer": f.pointer,
                    "message": "no semantic or synonyms: matched by field name only",
                }
            )
        elif f.semantic and f.semantic not in SEMANTICS:
            out.append(
                {
                    "level": "info",
                    "pointer": f.pointer,
                    "message": f"custom semantic '{f.semantic}': matched by synonyms and name",
                }
            )
    for c in schema.collections():
        if schema.collection_kind(c.pointer) is None:
            out.append(
                {
                    "level": "info",
                    "pointer": c.pointer,
                    "message": "no pricing/stock/promotion semantics: items matched by synonyms only",
                }
            )
    if schema.semantic_field("product_name") is None:
        out.append(
            {
                "level": "info",
                "pointer": "/",
                "message": "no field tagged product_name: the main table is found by the identity key alone",
            }
        )
    return out


# Keys the engine adds to every record: provenance (lineage) and unmapped root columns. They reach an
# export only when the target schema declares them as top-level properties.
PROVENANCE_KEY = "source_meta"
PASSTHROUGH_KEY = "attributes"
INTERNAL_KEYS = (PROVENANCE_KEY, PASSTHROUGH_KEY)


@dataclass(frozen=True)
class FieldSpec:
    """A flattened view of one leaf (or array/object) target field."""

    pointer: str  # JSON pointer, e.g. "/prices/*/amount"
    name: str  # last segment
    json_type: tuple[str, ...]  # e.g. ("number",) or ("string","null")
    semantic: str | None
    risk: Risk
    required: bool
    nullable: bool
    enum: tuple[Any, ...] | None
    fmt: str | None
    synonyms_en: tuple[str, ...]
    synonyms_th: tuple[str, ...]
    description: str | None
    description_th: str | None
    in_collection: str | None  # pointer of the enclosing array, if any ("/prices")
    is_collection: bool  # this field is itself an array of objects
    system: bool = False
    default: Any = None
    examples: tuple[Any, ...] = field(default_factory=tuple)

    @property
    def is_leaf(self) -> bool:
        return not self.is_collection and "object" not in self.json_type

    def to_dict(self) -> dict[str, Any]:
        return {
            "pointer": self.pointer,
            "name": self.name,
            "json_type": list(self.json_type),
            "semantic": self.semantic,
            "risk": self.risk,
            "required": self.required,
            "nullable": self.nullable,
            "enum": list(self.enum) if self.enum else None,
            "format": self.fmt,
            "synonyms_en": list(self.synonyms_en),
            "synonyms_th": list(self.synonyms_th),
            "description": self.description,
            "description_th": self.description_th,
            "in_collection": self.in_collection,
            "is_collection": self.is_collection,
            "system": self.system,
            "default": self.default,
            "examples": list(self.examples),
        }


@dataclass
class TargetSchema:
    schema_id: str
    version: int
    document: dict[str, Any]
    path: Path | None = None

    # ---- metadata -------------------------------------------------------------------------
    @property
    def xf(self) -> dict[str, Any]:
        return dict(self.document.get("x-forma", {}))

    @property
    def entity(self) -> str:
        return str(self.xf.get("entity", self.schema_id))

    @property
    def status(self) -> str:
        return str(self.xf.get("status", "active"))

    @property
    def identity_keys(self) -> list[str]:
        return list(self.xf.get("identity_keys", []))

    def collection_identity_keys(self, collection_pointer: str) -> list[str]:
        cols = self.xf.get("collections", {})
        return list(cols.get(collection_pointer, {}).get("identity_keys", []))

    @property
    def output_variants(self) -> dict[str, Any]:
        return dict(self.xf.get("output_variants", {"json": {"shape": "nested"}}))

    @property
    def ref(self) -> str:
        return f"{self.schema_id}@{self.version}"

    # ---- field catalogue ------------------------------------------------------------------
    def fields(self, *, include_system: bool = False) -> list[FieldSpec]:
        out = list(_walk(self.document, "", None, set(self.document.get("required", []))))
        if not include_system:
            out = [f for f in out if not f.system]
        return out

    def field(self, pointer: str) -> FieldSpec:
        for f in self.fields(include_system=True):
            if f.pointer == pointer:
                return f
        raise NotFound(f"Target field {pointer!r} not in schema {self.ref}")

    def has_field(self, pointer: str) -> bool:
        return any(f.pointer == pointer for f in self.fields(include_system=True))

    def leaf_fields(self) -> list[FieldSpec]:
        return [f for f in self.fields() if f.is_leaf]

    def collections(self) -> list[FieldSpec]:
        return [f for f in self.fields() if f.is_collection]

    def required_leaf_pointers(self) -> list[str]:
        return [f.pointer for f in self.leaf_fields() if f.required and f.in_collection is None]

    def risk_of(self, pointer: str) -> Risk:
        try:
            return self.field(pointer).risk
        except NotFound:
            return "medium"

    # ---- semantics: how code finds a field by meaning, never by name (ADR-001) ---------------
    def semantic_field(self, semantic: str, *, in_collection: str | None = None) -> FieldSpec | None:
        """First leaf with this ``x-forma.semantic`` at the root (default) or inside ``in_collection``."""
        return next(
            (f for f in self.leaf_fields() if f.semantic == semantic and f.in_collection == in_collection),
            None,
        )

    def semantic_pointers(self, semantic: str) -> set[str]:
        """Every pointer carrying this semantic, at the root or in any collection."""
        return {f.pointer for f in self.fields() if f.semantic == semantic}

    def identity_field(self) -> FieldSpec | None:
        keys = self.identity_keys
        if keys and self.has_field(keys[0]):
            return self.field(keys[0])
        return self.semantic_field("sku")

    def collection_kind(self, pointer: str) -> str | None:
        """``pricing`` / ``inventory`` / ``promotions`` from the collection's semantic or, failing that,
        from the semantics of its item fields; ``None`` for a collection FORMA has no special rules for."""
        coll = next((f for f in self.collections() if f.pointer == pointer), None)
        if coll is None:
            return None
        for kind, (sem, _) in COLLECTION_KINDS.items():
            if coll.semantic == sem:
                return kind
        votes: dict[str, int] = {}
        for f in self.leaf_fields():
            if f.in_collection == pointer and f.semantic:
                for kind, (_, prefixes) in COLLECTION_KINDS.items():
                    if f.semantic.startswith(prefixes):
                        votes[kind] = votes.get(kind, 0) + 1
        return max(votes, key=lambda k: votes[k]) if votes else None

    def collection_for(self, kind: str) -> str | None:
        return next((c.pointer for c in self.collections() if self.collection_kind(c.pointer) == kind), None)

    def declares(self, top_level_key: str) -> bool:
        props = self.document.get("properties", {})
        return isinstance(props, dict) and top_level_key in props

    def export_view(self, record: dict[str, Any]) -> dict[str, Any]:
        """The record as the contract sees it: engine-internal keys (provenance, unmapped columns) that
        this schema does not declare are dropped, so a strict downstream schema gets exactly its shape."""
        drop = [k for k in INTERNAL_KEYS if k in record and not self.declares(k)]
        return {k: v for k, v in record.items() if k not in drop} if drop else record

    @staticmethod
    def wildcard(pointer: str) -> str:
        """``/prices/0/amount`` → ``/prices/*/amount`` (data pointer → catalogue pointer)."""
        return re.sub(r"/\d+(?=/|$)", "/*", pointer)

    # ---- validation -----------------------------------------------------------------------
    def validator(self) -> Draft202012Validator:
        return Draft202012Validator(self.document, format_checker=FormatChecker())

    def validate_record(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        """Errors of the record *as exported* (``export_view``), so 'valid' means the export is valid."""
        errs = []
        for e in self.validator().iter_errors(self.export_view(record)):
            errs.append(
                {
                    "pointer": "/" + "/".join(str(p) for p in e.absolute_path),
                    "message": e.message,
                    "validator": e.validator,
                }
            )
        return errs

    def validate_records(
        self, records: Iterable[dict[str, Any]], *, limit: int = 1000
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i, rec in enumerate(records):
            for err in self.validate_record(rec):
                out.append({"row": i, **err})
                if len(out) >= limit:
                    return out
        return out

    def to_summary(self) -> dict[str, Any]:
        return {
            "schema_id": self.schema_id,
            "version": self.version,
            "ref": self.ref,
            "entity": self.entity,
            "status": self.status,
            "title": self.document.get("title"),
            "description": self.document.get("description"),
            "identity_keys": self.identity_keys,
            "output_variants": self.output_variants,
            "field_count": len(self.leaf_fields()),
        }


def _types(node: dict[str, Any]) -> tuple[str, ...]:
    t = node.get("type")
    if t is None:
        if "enum" in node:
            return ("string",)
        if "properties" in node:
            return ("object",)
        if "items" in node:
            return ("array",)
        return ("any",)
    return (t,) if isinstance(t, str) else tuple(t)


def _walk(
    node: dict[str, Any],
    prefix: str,
    in_collection: str | None,
    required: set[str],
    system: bool = False,
) -> Iterator[FieldSpec]:
    props: dict[str, Any] = node.get("properties", {})
    for name, sub in props.items():
        pointer = f"{prefix}/{name}"
        xf = sub.get("x-forma", {})
        types = _types(sub)
        is_arr_of_obj = (
            "array" in types and isinstance(sub.get("items"), dict) and "properties" in sub["items"]
        )
        spec = FieldSpec(
            pointer=pointer,
            name=name,
            json_type=types,
            semantic=xf.get("semantic"),
            risk=xf.get("risk") or SEMANTIC_RISK.get(xf.get("semantic") or "", "medium"),
            required=name in required,
            nullable="null" in types,
            enum=tuple(sub["enum"]) if "enum" in sub else None,
            fmt=sub.get("format"),
            synonyms_en=tuple(xf.get("synonyms_en", [])),
            synonyms_th=tuple(xf.get("synonyms_th", [])),
            description=sub.get("description"),
            description_th=xf.get("description_th"),
            in_collection=in_collection,
            is_collection=is_arr_of_obj,
            system=system or bool(xf.get("system", False)),
            default=sub.get("default"),
            examples=tuple(xf.get("examples", [])),
        )
        yield spec
        if is_arr_of_obj:
            items = sub["items"]
            yield from _walk(
                items,
                f"{pointer}/*",
                pointer,
                set(items.get("required", [])),
                system or bool(xf.get("system", False)),
            )
        elif "object" in types and "properties" in sub:
            yield from _walk(
                sub,
                pointer,
                in_collection,
                set(sub.get("required", [])),
                system or bool(xf.get("system", False)),
            )


class SchemaRegistry:
    """Loads target schemas from a directory (``<schema_id>.v<version>.json``) and keeps them in memory.

    The registry is deliberately file-backed: schemas are reviewed and versioned like code, but
    swapping or adding one never requires a deployment of FORMA itself.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._cache: dict[tuple[str, int], TargetSchema] = {}
        self.reload()

    def reload(self) -> None:
        self._cache.clear()
        if not self.directory.exists():
            return
        for p in sorted(self.directory.glob("*.json")):
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:  # pragma: no cover - config error
                raise ValidationFailed(f"Invalid JSON in {p.name}: {e}") from e
            xf = doc.get("x-forma", {})
            sid = str(xf.get("schema_id") or p.stem.split(".v")[0])
            ver = int(xf.get("version") or (p.stem.split(".v")[1] if ".v" in p.stem else 1))
            self._cache[(sid, ver)] = TargetSchema(sid, ver, doc, p)

    def list(self) -> list[TargetSchema]:
        return [self._cache[k] for k in sorted(self._cache)]

    def get(self, schema_id: str, version: int | None = None) -> TargetSchema:
        if version is None:
            versions = [v for (s, v) in self._cache if s == schema_id]
            if not versions:
                raise NotFound(f"Unknown target schema {schema_id!r}")
            version = max(versions)
        try:
            return self._cache[(schema_id, version)]
        except KeyError as e:
            raise NotFound(f"Unknown target schema {schema_id}@{version}") from e

    def get_ref(self, ref: str) -> TargetSchema:
        """``product@1`` or ``product`` (latest)."""
        if "@" in ref:
            sid, v = ref.split("@", 1)
            return self.get(sid, int(v))
        return self.get(ref)

    def prepare(self, document: dict[str, Any]) -> TargetSchema:
        """Validate a new schema and give it its id and next version, without storing it anywhere
        (the web app previews it first). The caller's document is never modified."""
        document = copy.deepcopy(document)
        Draft202012Validator.check_schema(document)
        xf = document.setdefault("x-forma", {})
        # the id becomes a file name: an explicit one must already be safe, a title is slugified
        if xf.get("schema_id"):
            sid = str(xf["schema_id"]).strip().lower()
        else:
            sid = re.sub(r"[^a-z0-9_-]+", "_", str(document.get("title") or "schema").lower()).strip("_-")
        if not _SCHEMA_ID_RE.fullmatch(sid):
            raise ValidationFailed(
                "x-forma.schema_id must be 1-64 characters of a-z, 0-9, '_' or '-'",
                details={"schema_id": sid},
            )
        existing = [v for (s, v) in self._cache if s == sid]
        version = int(xf.get("version") or (max(existing) + 1 if existing else 1))
        if version < 1:
            raise ValidationFailed("x-forma.version must be a positive integer", details={"version": version})
        if (sid, version) in self._cache:
            version = max(existing) + 1
        xf["schema_id"], xf["version"] = sid, version
        schema = TargetSchema(sid, version, document, None)
        identity = schema.identity_keys
        if not identity or not all(schema.has_field(k) for k in identity):
            raise ValidationFailed(
                'x-forma.identity_keys must name the field(s) that identify a record, e.g. ["/sku"]',
                details={"identity_keys": identity},
            )
        return schema

    def register(self, document: dict[str, Any], *, persist: bool = True) -> TargetSchema:
        """Validate and add a new schema (new version if the id already exists)."""
        schema = self.prepare(document)
        if persist:
            self.directory.mkdir(parents=True, exist_ok=True)
            schema.path = self.directory / f"{schema.schema_id}.v{schema.version}.json"
            schema.path.write_text(
                json.dumps(schema.document, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        self._cache[(schema.schema_id, schema.version)] = schema
        return schema

    def add(self, document: dict[str, Any]) -> TargetSchema:
        """Load a schema stored outside the directory (registered in the web app) as id@version."""
        xf = document.get("x-forma", {})
        schema = TargetSchema(str(xf["schema_id"]), int(xf["version"]), document, None)
        self._cache[(schema.schema_id, schema.version)] = schema
        return schema

    @staticmethod
    def diff(a: TargetSchema, b: TargetSchema) -> dict[str, Any]:
        fa = {f.pointer: f for f in a.fields()}
        fb = {f.pointer: f for f in b.fields()}
        added = sorted(set(fb) - set(fa))
        removed = sorted(set(fa) - set(fb))
        changed = []
        for p in sorted(set(fa) & set(fb)):
            x, y = fa[p], fb[p]
            deltas = {}
            for attr in ("json_type", "required", "nullable", "enum", "risk", "semantic", "fmt"):
                if getattr(x, attr) != getattr(y, attr):
                    deltas[attr] = {"from": getattr(x, attr), "to": getattr(y, attr)}
            if deltas:
                changed.append({"pointer": p, **{k: v for k, v in deltas.items()}})
        return {
            "from": a.ref,
            "to": b.ref,
            "added": added,
            "removed": removed,
            "changed": changed,
            "identity_keys_changed": a.identity_keys != b.identity_keys,
        }
