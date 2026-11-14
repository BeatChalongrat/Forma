"""Bounded source evidence, hypotheses and unresolved business scopes.

No filename/customer aliases and no decisions from a model's confidence. Candidate
discovery deliberately does not assign fields based on numeric shape alone.
"""

from __future__ import annotations

import re
from typing import Any

from forma.ai.fit import compatible
from forma.core.hashing import stable_hash
from forma.mapping_dsl.models import CallExpr, ColExpr, MappingDocument
from forma.profiling.profile import WorkbookProfile
from forma.schema_registry import FieldSpec, TargetSchema


def leaves(expr: Any) -> list[str]:
    if isinstance(expr, ColExpr):
        return [expr.name]
    if isinstance(expr, CallExpr):
        return list(dict.fromkeys(n for a in expr.args for n in leaves(a)))
    return []


def candidates(profile: WorkbookProfile, schema: TargetSchema, doc: MappingDocument) -> dict[str, Any]:
    from forma.ai.heuristics import score_header

    mapped = {f.target for f in [*doc.fields, *(f for c in doc.collections for f in c.fields)]}
    specs = sorted(schema.leaf_fields(), key=lambda f: (f.pointer in mapped, not f.required, f.pointer))
    result = []
    for spec in specs[:32]:
        choices: list[dict[str, Any]] = []
        words = set(re.findall(r"[\w]+", (spec.description or "").casefold()))
        for ti, table in enumerate(profile.tables):
            for column in table.columns:
                # Numeric-looking identifiers still belong in the shortlist for
                # string identity fields. Keep raw samples (including leading zeros).
                identity_candidate = (
                    "string" in spec.json_type
                    and (spec.pointer in schema.identity_keys or spec.semantic in ("sku", "barcode"))
                    and column.inferred_type != "empty"
                )
                if not identity_candidate and not compatible(spec, column.to_dict()):
                    continue
                header_score, _ = score_header(column.name, spec)
                overlap = words & set(re.findall(r"[\w]+", column.name.casefold()))
                choices.append(
                    {
                        "table_index": ti,
                        "file": table.file_name,
                        "sheet": table.sheet,
                        "header_row": table.table.header_row,
                        "column": column.name,
                        "header_similarity": round(header_score, 3),
                        "description_words": sorted(overlap),
                        "type": column.inferred_type,
                        "samples": [str(v)[:160] for v in column.samples[:5]],
                        "null_ratio": column.null_ratio,
                        "unique_ratio": column.unique_ratio,
                        "rank": header_score + min(0.3, len(overlap) * 0.1),
                    }
                )
        choices.sort(key=lambda c: (-c["rank"], str(c["file"]), c["sheet"], c["column"]))
        result.append(
            {
                "target": spec.pointer,
                "mapped": spec.pointer in mapped,
                "description": (spec.description or "")[:1000],
                "candidate_count": len(choices),
                "candidates_complete": len(choices) <= 3,
                "candidates": [{k: v for k, v in c.items() if k != "rank"} for c in choices[:3]],
            }
        )
    return {
        "fields": result,
        "total_fields": len(specs),
        "complete": len(specs) <= 32,
        "scope": "profile samples; candidate discovery only, not a semantic verdict",
        "next_step": "Inspect definitions, rows, relationships and competing candidates before choosing.",
    }


def is_currency(spec: FieldSpec) -> bool:
    names = " ".join([spec.name, *spec.synonyms_en, *spec.synonyms_th]).casefold().replace("_", " ")
    return "string" in spec.json_type and (
        spec.semantic == "currency"
        or bool(re.search(r"\bcurrency\b|สกุลเงิน", names))
        or bool(re.search(r"ISO[ -]?4217|currency code|สกุลเงิน", spec.description or "", re.I))
    )


def validate_currency_contract(schema: TargetSchema) -> None:
    from forma.core.errors import ValidationFailed

    config = (schema.document.get("x-forma") or {}).get("currency_sources", {})
    if not isinstance(config, dict):
        raise ValidationFailed("x-forma.currency_sources must be an object keyed by target path")
    for target, rule in config.items():
        if not schema.has_field(target) or not is_currency(schema.field(target)):
            raise ValidationFailed("currency_sources must reference a currency field")
        if not isinstance(rule, dict) or set(rule) - {"columns", "authoritative"}:
            raise ValidationFailed("currency_sources supports columns and authoritative only")
        cols = rule.get("columns")
        if not isinstance(cols, list) or not cols or not all(isinstance(c, str) and c.strip() for c in cols):
            raise ValidationFailed("currency_sources.columns must contain nonempty column names")
        if rule.get("authoritative") is not None and rule["authoritative"] not in cols:
            raise ValidationFailed("The authoritative currency column must be in columns")


def currency_questions(
    doc: MappingDocument,
    schema: TargetSchema,
    columns: dict[str, list[dict[str, Any]]],
    confirmations: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Observe conflicting candidate scopes; never equate different currencies with bad data.

    Columns explicitly mapped to another currency target belong to a different scope.
    An optional schema x-forma.currency_sources declares authoritative columns; this
    is contract configuration, never a model-supplied flag. Samples can prove a doubt,
    but cannot prove that the whole input is consistent.
    """
    fields = [(f, doc.record.root_source) for f in doc.fields]
    fields += [(f, c.source) for c in doc.collections for f in c.fields]
    currency = [
        (f, s) for f, s in fields if schema.has_field(f.target) and is_currency(schema.field(f.target))
    ]
    validate_currency_contract(schema)
    contracts = (schema.document.get("x-forma") or {}).get("currency_sources", {})
    questions = []
    for fm, source in currency:
        spec = schema.field(fm.target)
        source_spec = next((s for s in doc.sources if s.id == source), None)
        renames = source_spec.rename if source_spec is not None else {}
        selected = leaves(fm.expr)
        elsewhere = {
            n
            for other, src in currency
            if other.target != fm.target and src == source
            for n in leaves(other.expr)
        }
        contract = contracts.get(fm.target, {})
        eligible = []
        for column in columns.get(source, []):
            original_name = column["name"]
            name = renames.get(original_name, original_name)
            header = (name + " " + original_name).casefold().replace("_", " ")
            declared = name in contract.get("columns", []) or original_name in contract.get("columns", [])
            if (name in elsewhere and not declared and name not in selected) or not (
                declared or name in selected or re.search(r"\bcurrency\b|สกุลเงิน", header)
            ):
                continue
            values = sorted({str(v).strip().upper() for v in column.get("samples", []) if str(v).strip()})
            if values and all(re.fullmatch(r"[A-Z]{3}", v) for v in values):
                eligible.append(
                    {
                        "column": name,
                        "original_column": original_name,
                        "values": values,
                        "file": column.get("file"),
                        "file_sha256": column.get("file_sha256"),
                        "sheet": column.get("sheet"),
                        "header_row": column.get("header_row"),
                        "unique_count": column.get("unique_count"),
                        "scope": "profile samples",
                    }
                )
        authority = contract.get("authoritative")
        authority = renames.get(authority, authority)
        authority_present = bool(authority and any(c["column"] == authority for c in eligible))
        if authority_present and selected == [authority]:
            continue
        distinct = {tuple(c["values"]) for c in eligible}
        literal = getattr(fm.expr, "value", None)
        contract_default = spec.default is not None and literal == spec.default
        # Model-written constants and confidence are not evidence. Only a contract
        # default or a server-recorded human confirmation can justify a constant.
        missing = not selected and not contract_default
        # Identical sets with more than one value do not establish row-aligned agreement.
        ambiguous = len(eligible) > 1 and (len(distinct) > 1 or any(len(c["values"]) > 1 for c in eligible))
        if authority and selected != [authority]:
            ambiguous = True
        if not selected and eligible:
            ambiguous = ambiguous or any(c["values"] != [str(literal).upper()] for c in eligible)
        if not missing and not ambiguous:
            continue
        # Include full-file hashes where available, also when no column can be
        # identified. A changed tail outside the profile sample invalidates a receipt.
        file_hashes = sorted({c["file_sha256"] for c in columns.get(source, []) if c.get("file_sha256")})
        collection_scope = next(
            (
                c.model_dump(mode="json", exclude={"fields"})
                for c in doc.collections
                if any(f.target == fm.target for f in c.fields)
            ),
            None,
        )
        evidence_key = stable_hash(
            [
                schema.document,
                fm.target,
                source,
                eligible,
                file_hashes,
                source_spec.model_dump(mode="json") if source_spec else None,
                doc.record.model_dump(mode="json"),
                collection_scope,
            ],
            64,
        )
        expression = fm.expr.model_dump(mode="json")
        if any(
            c.get("evidence_key") == evidence_key and c.get("expression") == expression
            for c in confirmations or []
        ):
            continue
        choices = [c["column"] for c in eligible]
        if authority:
            choices = [authority] if authority_present else []
        details = "; ".join(f"{c['column']}: {', '.join(c['values'][:3])}" for c in eligible)
        q: dict[str, Any] = {
            "id": fm.target,
            "target": fm.target,
            "evidence_key": evidence_key,
            "question": (
                f"ข้อมูล {spec.name} ต้องใช้สกุลเงินจากคอลัมน์ไหน?"
                if ambiguous
                else f"ข้อมูล {spec.name} ใช้สกุลเงินอะไร?"
            ),
            "why": (
                f"พบ {details} ยังไม่มีข้อกำหนดว่าแหล่งใดใช้กับราคานี้ ต้องยืนยันขอบเขตก่อนใช้ข้อมูล"
                if ambiguous
                else "ไม่มีคอลัมน์หรือค่าในข้อกำหนดที่ยืนยันสกุลเงิน จึงยังไม่ใส่ค่าให้เอง"
            ),
            "evidence_sources": eligible,
            "kind": "choice",
            "options": choices,
            "scope": "source_column",
            "allow_custom": False,
        }
        if authority:
            q["question"] = f"ข้อมูล {spec.name} ต้องอ่านจากคอลัมน์ {authority} ตามข้อกำหนด ต้องการแก้การจับคู่ไหม?"
            q["why"] = (
                f"ข้อกำหนดระบุแหล่งข้อมูล {authority}; พบตัวอย่าง {details}"
                if authority_present
                else f"ยังไม่พบข้อมูลจากคอลัมน์ {authority} ตามข้อกำหนด กรุณาตรวจไฟล์หรือข้อกำหนด"
            )
        if len(selected) > 1 or not choices:
            if missing and not eligible and not authority:
                q.update(
                    kind="text",
                    action="constant",
                    options=[],
                    allow_custom=True,
                    scope="constant_for_this_file",
                )
            else:
                q.update(kind="confirm", action="request_source", options=[], scope="source_file")
        questions.append(q)
    return questions
