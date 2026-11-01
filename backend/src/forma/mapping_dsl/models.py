"""Mapping DSL — the single declarative contract between AI proposals, the UI and the engine.

Design rules
------------
* Fully declarative. Nothing in a mapping is executable code; every transformation is a node in a
  whitelisted operation tree (`Expr`). The engine compiles the tree to Polars expressions.
* Serialisable to YAML/JSON and emitted by LLMs as structured output. Field names are stable.
* Target fields are addressed by JSON pointer into the Target Schema (``/prices/*/amount``);
  the validator (``validate.py``) checks every pointer against the registry.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --------------------------------------------------------------------------------------------
# Expressions
# --------------------------------------------------------------------------------------------
class Op(StrEnum):
    """Whitelisted operations. Adding an op = adding a compiler branch in ``engine/ops.py`` + tests."""

    COL = "col"
    CONST = "const"
    COALESCE = "coalesce"
    CONCAT = "concat"
    TRIM = "trim"
    UPPER = "upper"
    LOWER = "lower"
    CAST = "cast"
    REGEX_EXTRACT = "regex_extract"
    REGEX_REPLACE = "regex_replace"
    SPLIT = "split"
    MAP_VALUES = "map_values"
    LOOKUP = "lookup"
    PARSE_NUMBER_TH = "parse_number_th"
    PARSE_DATE_TH = "parse_date_th"
    PARSE_BOOL = "parse_bool"
    VAT_ADJUST = "vat_adjust"
    UNIT_NORMALIZE = "unit_normalize"
    PACK_SIZE = "pack_size"
    ADD = "add"
    SUBTRACT = "subtract"
    MULTIPLY = "multiply"
    DIVIDE = "divide"
    ROUND = "round"
    CONDITIONAL = "conditional"
    DEFAULT = "default"
    NULL_IF = "null_if"
    STRIP_LEADING_ZEROS = "strip_leading_zeros"
    NORMALIZE_WHITESPACE = "normalize_whitespace"
    TO_LIST = "to_list"
    EQ = "eq"
    NEQ = "neq"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IS_NULL = "is_null"
    NOT_NULL = "not_null"
    AND = "and"
    OR = "or"
    NOT = "not"
    CONTAINS = "contains"
    IN = "in"
    STARTS_WITH = "starts_with"


class ColExpr(Strict):
    op: Literal["col"] = "col"
    name: str = Field(description="Source column name after table extraction/normalisation")
    source: str | None = Field(default=None, description="Source id; defaults to the frame being mapped")


class ConstExpr(Strict):
    op: Literal["const"] = "const"
    # A null constant ("else null" in a conditional) is dropped by ``model_dump(exclude_none=True)``, so the
    # stored form is ``{"op": "const"}`` and must read back as the same null (ADR-056 follow-up).
    value: Any = None


class CallExpr(Strict):
    """Generic operation node. ``args`` are child expressions, ``params`` are literal options."""

    op: Op
    args: list[Expr] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _alias_params(cls, data: Any) -> Any:
        """A model writes ``day_first`` for ``dayfirst`` and the date silently reads the wrong way round
        (ADR-057). Known spellings are repaired here, deterministically; anything else is an error in
        ``validate_mapping`` so the author is told instead of ignored."""
        if isinstance(data, dict) and isinstance(data.get("params"), dict):
            params = data["params"]
            fixed = {PARAM_ALIASES.get(str(k), str(k)): v for k, v in params.items()}
            if fixed != params:
                data = {**data, "params": fixed}
        return data

    @model_validator(mode="after")
    def _check_arity(self) -> CallExpr:
        if self.op in (Op.COL, Op.CONST):
            raise ValueError("use ColExpr/ConstExpr for col/const")
        arity = OP_ARITY.get(self.op)
        if arity is not None:
            lo, hi = arity
            n = len(self.args)
            if n < lo or (hi is not None and n > hi):
                raise ValueError(f"op {self.op} expects {lo}..{hi if hi is not None else '∞'} args, got {n}")
        return self


Expr = Annotated[ColExpr | ConstExpr | CallExpr, Field(union_mode="smart")]
CallExpr.model_rebuild()

# (min_args, max_args or None)
OP_ARITY: dict[Op, tuple[int, int | None]] = {
    Op.COALESCE: (1, None),
    Op.CONCAT: (1, None),
    Op.TRIM: (1, 1),
    Op.UPPER: (1, 1),
    Op.LOWER: (1, 1),
    Op.CAST: (1, 1),
    Op.REGEX_EXTRACT: (1, 1),
    Op.REGEX_REPLACE: (1, 1),
    Op.SPLIT: (1, 1),
    Op.MAP_VALUES: (1, 1),
    Op.LOOKUP: (1, 1),
    Op.PARSE_NUMBER_TH: (1, 1),
    Op.PARSE_DATE_TH: (1, 1),
    Op.PARSE_BOOL: (1, 1),
    Op.VAT_ADJUST: (1, 1),
    Op.UNIT_NORMALIZE: (1, 1),
    Op.PACK_SIZE: (1, 1),
    Op.ADD: (2, None),
    Op.SUBTRACT: (2, 2),
    Op.MULTIPLY: (2, None),
    Op.DIVIDE: (2, 2),
    Op.ROUND: (1, 1),
    Op.CONDITIONAL: (2, 3),
    Op.DEFAULT: (1, 1),
    Op.NULL_IF: (1, 1),
    Op.STRIP_LEADING_ZEROS: (1, 1),
    Op.NORMALIZE_WHITESPACE: (1, 1),
    Op.TO_LIST: (1, None),
    Op.EQ: (2, 2),
    Op.NEQ: (2, 2),
    Op.GT: (2, 2),
    Op.GTE: (2, 2),
    Op.LT: (2, 2),
    Op.LTE: (2, 2),
    Op.IS_NULL: (1, 1),
    Op.NOT_NULL: (1, 1),
    Op.AND: (2, None),
    Op.OR: (2, None),
    Op.NOT: (1, 1),
    Op.CONTAINS: (1, 1),
    Op.IN: (1, 1),
    Op.STARTS_WITH: (1, 1),
}

# Every literal option an op reads (engine/ops.py), with the one-line meaning the prompt shows. An op
# missing here takes no params; a param missing here is an error (ADR-057).
OP_PARAMS: dict[Op, dict[str, str]] = {
    Op.CONCAT: {"separator": "string between the parts, default ''"},
    Op.CAST: {"to": "string | int | float | bool | date"},
    Op.REGEX_EXTRACT: {"pattern": "regex", "group": "capture group, default 1"},
    Op.REGEX_REPLACE: {"pattern": "regex", "replacement": "string, default ''"},
    Op.SPLIT: {"separator": "default ','", "index": "0-based element; omit for the whole list"},
    Op.MAP_VALUES: {
        "mapping": "dict source value → target value (case-insensitive)",
        "default": "value when unmapped (omit to keep null)",
        "keep_unmapped": "true keeps the source value when unmapped",
        "fuzzy": "true matches when the source contains the key",
    },
    Op.LOOKUP: {"table": "dict", "source": "joined source id", "default": "value when missing"},
    Op.PARSE_NUMBER_TH: {"percent_to_fraction": "true turns 15% into 0.15"},
    Op.PARSE_DATE_TH: {
        "formats": "list of strptime formats tried first",
        "assume_year": "auto | be | ce",
        "dayfirst": "false for month/day/year (US) dates; default true",
    },
    Op.VAT_ADJUST: {"mode": "add | remove", "rate": "default 0.07"},
    Op.UNIT_NORMALIZE: {"default": "unit when unknown", "synonyms": "dict extra source word → unit"},
    Op.ROUND: {"decimals": "default 2"},
    Op.DEFAULT: {"value": "fills nulls"},
    Op.NULL_IF: {"values": "list of placeholders that mean null"},
    Op.CONTAINS: {"pattern": "regex, or text with literal=true", "literal": "true for plain text"},
    Op.STARTS_WITH: {"pattern": "prefix"},
    Op.IN: {"values": "list"},
}

# Spellings models and people reach for; repaired on the way in (ADR-057).
PARAM_ALIASES: dict[str, str] = {
    "day_first": "dayfirst",
    "dayFirst": "dayfirst",
    "assume": "assume_year",
    "year": "assume_year",
    "sep": "separator",
    "delimiter": "separator",
    "format": "formats",
    "map": "mapping",
    "to_type": "to",
    "type": "to",
    "precision": "decimals",
    "digits": "decimals",
    "vat_rate": "rate",
    "regex": "pattern",
    "prefix": "pattern",
}


# --------------------------------------------------------------------------------------------
# Sources, tables, joins
# --------------------------------------------------------------------------------------------
class SourceRole(StrEnum):
    PRODUCTS = "products"
    PRICING = "pricing"
    INVENTORY = "inventory"
    PROMOTIONS = "promotions"
    CATEGORIES = "categories"
    OTHER = "other"


class FileMatch(Strict):
    """How to bind this source to one of the uploaded files of an import round."""

    name_pattern: str | None = Field(
        default=None, description="Regex on the original file name (case-insensitive)"
    )
    fingerprint: str | None = Field(default=None, description="Expected structural fingerprint of the file")
    sheet_names_any: list[str] = Field(
        default_factory=list, description="File must contain at least one of these sheets"
    )


class SheetSelect(Strict):
    name: str | None = None
    index: int | None = None
    match: str | None = Field(default=None, description="Regex on sheet name")

    @model_validator(mode="after")
    def _one(self) -> SheetSelect:
        if sum(x is not None for x in (self.name, self.index, self.match)) != 1:
            raise ValueError("sheet selector needs exactly one of name/index/match")
        return self


class Unpivot(Strict):
    """Cross-tab → long. E.g. months as columns → (sku, month, value)."""

    id_columns: list[str]
    value_columns: list[str] | None = None
    value_columns_regex: str | None = None
    variable_name: str = "variable"
    value_name: str = "value"


class TableSpec(Strict):
    header_row: int | Literal["auto"] = Field(
        default="auto", description="0-based row index of the header row"
    )
    header_rows: int = Field(
        default=1, ge=1, le=3, description="Number of header rows to merge (multi-row headers)"
    )
    data_start: int | None = Field(
        default=None, description="0-based first data row; defaults to header_row+header_rows"
    )
    data_end: int | None = Field(
        default=None, description="0-based exclusive end row; defaults to last non-blank row"
    )
    first_column: int | None = Field(default=None, description="0-based first column of the table")
    last_column: int | None = Field(default=None, description="0-based exclusive end column")
    stop_at_blank_rows: int = Field(
        default=2, ge=0, description="Stop table after N consecutive blank rows (0=never)"
    )
    drop_total_rows: bool = Field(
        default=True, description="Drop trailing rows whose first text cell is a total marker"
    )
    drop_repeated_headers: bool = Field(
        default=True, description="Drop a header printed again on each page, and the title lines above it"
    )
    fill_down: list[str] = Field(
        default_factory=list, description="Columns whose blanks inherit the value above (merged cells)"
    )
    drop_empty_columns: bool = True
    unpivot: Unpivot | None = None
    filter: Expr | None = Field(default=None, description="Row filter applied after extraction")


class CsvOptions(Strict):
    encoding: str = Field(default="auto", description="auto | utf-8 | utf-8-sig | cp874 | tis-620 | ...")
    delimiter: str = Field(default="auto", description="auto | , | ; | \\t | |")
    quote_char: str = '"'


class SourceSpec(Strict):
    id: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    role: SourceRole = SourceRole.OTHER
    file: FileMatch = Field(default_factory=FileMatch)
    sheet: SheetSelect | None = Field(default=None, description="Required for Excel; ignored for CSV")
    table: TableSpec = Field(default_factory=TableSpec)
    csv: CsvOptions = Field(default_factory=CsvOptions)
    rename: dict[str, str] = Field(default_factory=dict, description="Raw header → canonical source column")
    description: str | None = None


NormalizeKeyOp = Literal["trim", "upper", "strip_leading_zeros", "normalize_whitespace"]


def _default_normalize_keys() -> list[NormalizeKeyOp]:
    return ["trim", "upper"]


class JoinKey(Strict):
    left: str
    right: str


class JoinSpec(Strict):
    left: str
    right: str
    on: list[JoinKey] = Field(min_length=1)
    how: Literal["left", "inner"] = "left"
    cardinality: Literal["1:1", "1:N", "N:1"] = "1:1"
    on_unmatched: Literal["keep", "drop", "error", "review"] = "keep"
    normalize_keys: list[NormalizeKeyOp] = Field(default_factory=_default_normalize_keys)


# --------------------------------------------------------------------------------------------
# Field & collection mappings
# --------------------------------------------------------------------------------------------
class FieldMapping(Strict):
    target: str = Field(description="JSON pointer into the target schema, e.g. /sku or /prices/*/amount")
    expr: Expr
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence: list[str] = Field(
        default_factory=list, description="Why this mapping (headers, samples, rules)"
    )
    note: str | None = None


class CollectionMapping(Strict):
    """Builds a nested array (``/prices``, ``/promotions``, ``/inventory``) from a source frame."""

    target: str = Field(description="Pointer of the array field, e.g. /prices")
    source: str = Field(description="Source id whose rows become array items")
    link: list[JoinKey] = Field(
        min_length=1,
        description="How items attach to the root record: left=root source column, right=item source column",
    )
    fields: list[FieldMapping]
    filter: Expr | None = None
    dedupe_keys: list[str] = Field(
        default_factory=list, description="Target item pointers (relative, e.g. /price_list)"
    )
    normalize_keys: list[NormalizeKeyOp] = Field(default_factory=_default_normalize_keys)


class DedupeSpec(Strict):
    keys: list[str] = Field(
        default_factory=list, description="Target pointers; defaults to schema identity keys"
    )
    keep: Literal["first", "last", "error", "review"] = "first"


class RecordSpec(Strict):
    root_source: str = Field(description="Source id that yields one row per target record")
    filters: list[Expr] = Field(default_factory=list)
    dedupe: DedupeSpec = Field(default_factory=DedupeSpec)


class QualityRule(Strict):
    target: str
    rule: Literal["not_null", "unique", "range", "enum", "regex", "max_length", "positive", "date_order"]
    params: dict[str, Any] = Field(default_factory=dict)
    severity: Literal["error", "warning"] = "error"
    message: str | None = None


class MappingMeta(Strict):
    mapping_id: str | None = None
    version: int = 1
    customer_id: str | None = None
    target_schema: str = Field(description="Schema ref, e.g. product@1")
    created_by: Literal["human", "ai", "system"] = "human"
    model: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)
    fingerprints: dict[str, str] = Field(
        default_factory=dict, description="source id → structural fingerprint"
    )
    notes: str | None = None


class MappingDocument(Strict):
    """Top-level mapping. One per (customer, target schema, format)."""

    meta: MappingMeta
    sources: list[SourceSpec] = Field(min_length=1)
    joins: list[JoinSpec] = Field(default_factory=list)
    record: RecordSpec
    fields: list[FieldMapping] = Field(default_factory=list)
    collections: list[CollectionMapping] = Field(default_factory=list)
    quality: list[QualityRule] = Field(default_factory=list)
    provenance: bool = True
    attributes_passthrough: bool = Field(
        default=True, description="Unmapped root-source columns are kept under /attributes so no data is lost"
    )

    # ---- helpers --------------------------------------------------------------------------
    def source(self, source_id: str) -> SourceSpec:
        for s in self.sources:
            if s.id == source_id:
                return s
        raise KeyError(source_id)

    def source_ids(self) -> list[str]:
        return [s.id for s in self.sources]

    def all_field_targets(self) -> list[str]:
        out = [f.target for f in self.fields]
        for c in self.collections:
            out.extend(f.target for f in c.fields)
        return out

    def to_yaml(self) -> str:
        import yaml

        return yaml.safe_dump(
            self.model_dump(mode="json", exclude_none=True), sort_keys=False, allow_unicode=True, width=100
        )

    @classmethod
    def from_yaml(cls, text: str) -> MappingDocument:
        import yaml

        return cls.model_validate(yaml.safe_load(text))

    @classmethod
    def json_schema_document(cls) -> dict[str, Any]:
        """JSON Schema of the DSL itself — used for LLM structured output and UI editors."""
        return cls.model_json_schema()


def col(name: str, source: str | None = None) -> ColExpr:
    return ColExpr(name=name, source=source)


def const(value: Any) -> ConstExpr:
    return ConstExpr(value=value)


def call(op: Op | str, *args: ColExpr | ConstExpr | CallExpr, **params: Any) -> CallExpr:
    return CallExpr(op=Op(op), args=list(args), params=params)
