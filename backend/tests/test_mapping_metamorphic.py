"""Format invariance and rule sensitivity, without a model or oracle in runtime inputs.

These prove the execution/contract boundary, NOT a real LLM's generalisation.
Mappings stand in for mock model proposals; expected values are computed independently.
"""

import random
from datetime import date, timedelta

import openpyxl
import pytest

from forma.engine.transform import BoundSource, TransformEngine
from forma.mapping_dsl.models import MappingDocument, call, col, const
from forma.mapping_dsl.validate import validate_mapping
from forma.schema_registry import TargetSchema


def proposal(schema, sheet, names, targets, selected, as_of):
    code, price = targets
    return MappingDocument.model_validate(
        {
            "meta": {"target_schema": schema.ref},
            "sources": [{"id": "input", "sheet": {"name": sheet}, "table": {"header_row": 0}}],
            "record": {
                "root_source": "input",
                "filters": [
                    call("eq", col(names[2]), const(selected)).model_dump(),
                    call(
                        "lte", call("parse_date_th", col(names[3])), call("parse_date_th", const(as_of))
                    ).model_dump(),
                    call(
                        "gte", call("parse_date_th", col(names[4])), call("parse_date_th", const(as_of))
                    ).model_dump(),
                ],
                "dedupe": {"keep": "review"},
            },
            "fields": [
                {"target": "/" + code, "expr": col(names[0]).model_dump()},
                {"target": "/" + price, "expr": call("parse_number_th", col(names[1])).model_dump()},
            ],
            "attributes_passthrough": False,
        }
    )


@pytest.mark.parametrize("seed", range(8))
def test_renaming_reordering_schema_and_selection_are_not_fixture_constants(tmp_path, seed):
    rng = random.Random(seed)
    names = [f"ข้อมูล_{rng.randrange(100000)}_{i}" for i in range(5)]
    targets = (f"ref_{seed}", f"value_{seed}")
    schema = TargetSchema(
        f"variant_{seed}",
        1,
        {
            "type": "object",
            "additionalProperties": False,
            "required": list(targets),
            "properties": {targets[0]: {"type": "string"}, targets[1]: {"type": "number"}},
            "x-forma": {"identity_keys": ["/" + targets[0]]},
        },
    )
    day = date(2028, 1, 1) + timedelta(days=seed * 9)
    sheet = f"current_{rng.randrange(10000)}"
    chosen, other = f"channel_{seed}", f"alternate_{seed}"
    rows, expected, alternate = [], {}, {}
    for i in range(7):
        key = f"00{seed}{i:03}"
        amount = rng.randrange(10, 1000) + 0.25
        expected[key] = amount
        alternate[key] = amount + 41
        for label, start, end, value in [
            (chosen, day, day, amount),  # inclusive boundaries
            (other, day, day, amount + 41),
            (chosen, day - timedelta(days=8), day - timedelta(days=1), 9999),
            (chosen, day + timedelta(days=1), day + timedelta(days=8), 8888),
        ]:
            rows.append([key, value, label, start.isoformat(), end.isoformat()])

    def workbook(path, order, data, distract_first):
        wb = openpyxl.Workbook()
        wb.remove(wb.active)

        def stale():
            ws = wb.create_sheet("archive")
            ws.append(names)
            ws.append(["WRONG", 999999, chosen, day.isoformat(), day.isoformat()])

        if distract_first:
            stale()
        ws = wb.create_sheet(sheet)
        ws.append([names[j] for j in order])
        for row in data:
            ws.append([row[j] for j in order])
        if not distract_first:
            stale()
        wb.save(path)

    baseline = tmp_path / "baseline.xlsx"
    variant = tmp_path / "renamed-export.xlsx"
    workbook(baseline, list(range(5)), rows, False)
    shuffled = rows.copy()
    rng.shuffle(shuffled)
    order = list(range(5))
    rng.shuffle(order)
    workbook(variant, order, shuffled, True)

    def execute(path, label):
        doc = proposal(schema, sheet, names, targets, label, day.isoformat())
        assert validate_mapping(doc, schema).ok
        result = TransformEngine(schema).run(doc, {"input": BoundSource(path)})
        assert not [f for f in result.findings if f.severity == "error"]
        assert len(result.records) == 7
        return {r[targets[0]]: r[targets[1]] for r in result.records}

    assert execute(baseline, chosen) == expected
    assert execute(variant, chosen) == expected
    # Identical file/layout but different business instruction must change the result.
    assert execute(variant, other) == alternate
    assert alternate != expected


def test_old_target_names_are_rejected_after_schema_change():
    old = TargetSchema(
        "old", 1, {"type": "object", "properties": {"sku": {"type": "string"}, "price": {"type": "number"}}}
    )
    doc = proposal(old, "sheet", ["a", "b", "c", "d", "e"], ("sku", "price"), "X", "2028-01-01")
    new = TargetSchema(
        "new",
        1,
        {"type": "object", "properties": {"reference": {"type": "string"}, "total": {"type": "number"}}},
    )
    doc.meta.target_schema = new.ref
    assert not validate_mapping(doc, new).ok
