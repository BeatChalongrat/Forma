"""Target Schema Registry.

The target JSON contract is *configuration*, not code. Schemas are JSON Schema (Draft 2020-12)
documents enriched with an ``x-forma`` vendor extension that carries the semantics FORMA needs
(semantic tag, risk level, Thai/English synonyms, identity keys, output variants).

Everything downstream — mapping validation, AI prompts, UI field pickers, quality rules, exports —
goes through this module. Nothing else in the codebase may hard-code target field names.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator, FormatChecker

from forma.core.errors import NotFound, ValidationFailed

Risk = Literal["critical", "high", "medium", "low"]
RISK_ORDER: dict[str, int] = {"critical": 3, "high": 2, "medium": 1, "low": 0}


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

    # ---- validation -----------------------------------------------------------------------
    def validator(self) -> Draft202012Validator:
        return Draft202012Validator(self.document, format_checker=FormatChecker())

    def validate_record(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        errs = []
        for e in self.validator().iter_errors(record):
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
            risk=xf.get("risk", "medium"),
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

    def register(self, document: dict[str, Any], *, persist: bool = True) -> TargetSchema:
        """Validate and add a new schema (new version if the id already exists)."""
        Draft202012Validator.check_schema(document)
        xf = document.setdefault("x-forma", {})
        sid = str(xf.get("schema_id") or document.get("title", "schema")).lower()
        existing = [v for (s, v) in self._cache if s == sid]
        version = int(xf.get("version") or (max(existing) + 1 if existing else 1))
        if (sid, version) in self._cache:
            version = max(existing) + 1
        xf["schema_id"], xf["version"] = sid, version
        path = None
        if persist:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / f"{sid}.v{version}.json"
            path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        schema = TargetSchema(sid, version, document, path)
        self._cache[(sid, version)] = schema
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
