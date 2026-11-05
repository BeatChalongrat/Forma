"""Relationships must stay complete when only a few output records are displayed."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from forma.mapping_dsl.models import MappingDocument, call, col, const
from forma.schema_registry import TargetSchema
from forma.services import mapping, review_evidence, review_questions


@pytest.fixture
def delivery(tmp_path, monkeypatch):
    data = {
        "master.csv": "SKU,Name\n" + "".join(f"{i:03},Item {i}\n" for i in range(1, 71)),
        "prices.csv": "SKU,Price\n" + "".join(f"{i:03},{i * 3}\n" for i in range(70, 0, -1)),
        "stock.csv": "SKU,Warehouse,Lot,Qty\n"
        + "".join(
            f"{i:03},{w},{lot},{i * 10 + lot}\n"
            for i in range(70, 0, -1)
            for w in ("A", "B")
            for lot in (1, 2, 3)
        ),
    }
    files = []
    for name, content in data.items():
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        files.append(
            SimpleNamespace(
                id=name,
                storage_key=str(path),
                original_name=name,
                status="profiled",
                profile=None,
                fingerprint=None,
                kind="csv",
            )
        )
    schema = TargetSchema(
        "preview",
        1,
        {
            "type": "object",
            "required": ["sku", "price", "inventory"],
            "properties": {
                "sku": {"type": "string"},
                "price": {"type": "number"},
                "inventory": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["warehouse", "qty"],
                        "properties": {"warehouse": {"type": "string"}, "qty": {"type": "number"}},
                    },
                },
            },
            "x-forma": {"identity_keys": ["/sku"]},
        },
    )
    doc = MappingDocument.model_validate(
        {
            "meta": {"target_schema": schema.ref},
            "sources": [
                {"id": name, "file": {"name_pattern": f"^{name}\\.csv$"}, "table": {"header_row": 0}}
                for name in ("master", "prices", "stock")
            ],
            "record": {"root_source": "master"},
            "joins": [{"left": "master", "right": "prices", "on": [{"left": "SKU", "right": "SKU"}]}],
            "fields": [
                {"target": "/sku", "expr": {"op": "col", "source": "master", "name": "SKU"}},
                {
                    "target": "/price",
                    "expr": {
                        "op": "parse_number_th",
                        "args": [{"op": "col", "source": "prices", "name": "Price"}],
                    },
                },
            ],
            "collections": [
                {
                    "target": "/inventory",
                    "source": "stock",
                    "link": [{"left": "SKU", "right": "SKU"}],
                    "fields": [
                        {"target": "/inventory/*/warehouse", "expr": {"op": "col", "name": "Warehouse"}},
                        {
                            "target": "/inventory/*/qty",
                            "expr": {"op": "parse_number_th", "args": [{"op": "col", "name": "Qty"}]},
                        },
                    ],
                    "aggregate": {"group_by": ["/warehouse"], "sum": ["/qty"]},
                }
            ],
            "attributes_passthrough": False,
        }
    )
    ctx = SimpleNamespace(
        registry=SimpleNamespace(get_ref=lambda _: schema),
        storage=SimpleNamespace(local_path=lambda key: tmp_path / key),
    )
    r = SimpleNamespace(files=files, target_schema=schema.ref)
    session = Mock()
    session.get.return_value = r
    monkeypatch.setattr(mapping, "get_round", lambda *_: r)
    return ctx, session, doc, schema


def test_preview_joins_and_aggregates_before_limiting_display(delivery):
    ctx, session, doc, _ = delivery
    preview = mapping.preview_mapping(ctx, session, "round", doc.model_dump(mode="json"), limit=1)
    assert preview["ok"]
    assert preview["records"][0]["sku"] == "001"
    assert preview["records"][0]["price"] == 3
    assert sorted(preview["records"][0]["inventory"], key=lambda r: r["warehouse"]) == [
        {"warehouse": "A", "qty": 36},
        {"warehouse": "B", "qty": 36},
    ]
    assert preview["scope"] == {
        "execution": "complete",
        "records_total": 70,
        "records_shown": 1,
        "display_limit": 1,
    }
    assert preview["stats"]["sources"]["stock"] == 420


def test_preview_does_not_hide_invalid_records_outside_display(delivery):
    ctx, session, doc, _ = delivery
    doc.sources[1].table.filter = call("neq", col("SKU"), const("070"))
    preview = mapping.preview_mapping(ctx, session, "round", doc.model_dump(mode="json"), limit=1)
    assert len(preview["records"]) == 1 and preview["records"][0]["price"] == 3
    assert not preview["ok"]
    assert preview["stats"]["invalid_records"] == 1


def test_answer_preview_reuses_two_full_executions_and_counts_all_changes(delivery, monkeypatch):
    ctx, session, doc, schema = delivery
    edited = doc.model_copy(deep=True)
    edited.fields[1].expr = call("add", edited.fields[1].expr, const(1))
    m = SimpleNamespace(
        id="m", round_id="round", target_schema=schema.ref, document=doc.model_dump(mode="json")
    )
    monkeypatch.setattr(
        review_questions,
        "questions",
        lambda *_: {"questions": [{"id": "/price", "field": {"pointer": "/price"}}]},
    )
    original = mapping.simulate
    limits = []

    def counted(*args, **kw):
        limits.append(kw["limit"])
        return original(*args, **kw)

    monkeypatch.setattr(mapping, "simulate", counted)
    result = review_evidence.answer_impact(ctx, session, m, edited.model_dump(mode="json"), {"/price": 1})
    assert limits == [None, None]  # No separate partial simulation or repeated full validation reads.
    assert result["preview"]["ok"] and result["comparison"]["reliable"]
    assert result["comparison"]["validation"]["records_checked"] == 70
    assert result["comparison"]["counts"]["changed_records"] == 70
    assert result["comparison"]["before_records"] == result["comparison"]["after_records"] == 70
    assert len(result["preview"]["records"]) == len(result["comparison"]["rows"]) == 50


def test_question_evidence_keeps_all_lots_and_real_source_cells(delivery):
    ctx, session, doc, schema = delivery
    m = SimpleNamespace(round_id="round", target_schema=schema.ref, document=doc.model_dump(mode="json"))
    evidence = review_evidence.evidence(ctx, session, m, "/inventory/*/qty")
    assert evidence["execution_scope"] == "complete_delivery"
    first = evidence["examples"][0]
    assert first["identity"] == "001" and first["value"] == 36
    assert first["source_complete"] and len(first["cells"]) == 3
    assert sum(float(c["raw"]) for c in first["cells"]) == 36


def test_full_width_and_default_column_start_are_not_a_disagreement():
    from tests.test_agents import _doc

    original = _doc()
    changed = original.model_copy(deep=True)
    original.sources[0].table.first_column = 0
    original.sources[0].table.last_column = 8
    files = [
        SimpleNamespace(
            id="file",
            status="profiled",
            original_name="data.xlsx",
            fingerprint=None,
            profile={"meta": {"sheets": [{"name": original.sources[0].sheet.name, "index": 0, "n_cols": 8}]}},
        )
    ]
    # Use the same bound physical file without fixture-specific filename selectors.
    for d in (original, changed):
        for source in d.sources:
            source.file.name_pattern = None
            source.file.sheet_names_any = []
    target = original.fields[0].target
    assert target not in mapping._agreement(original, changed, files)["disagree"]
    changed.sources[0].table.last_column = 7
    assert target in mapping._agreement(original, changed, files)["disagree"]
    changed.sources[0].table = original.sources[0].table.model_copy(deep=True)
    changed.sources[0].table.data_end = 10
    assert target in mapping._agreement(original, changed, files)["disagree"]


@pytest.mark.parametrize(
    "values,header,resolved",
    [
        (["THB", "THB"], "currency", True),
        (["THB", "USD"], "currency", False),
        (["THB", None], "currency", False),
        ([], "currency", False),
        (["THB", "THB"], "Company", False),
    ],
)
def test_currency_resolution_requires_complete_explicit_source_evidence(values, header, resolved):
    from tests.test_agents import _doc

    old = _doc()
    proposed = old.model_copy(deep=True)
    schema = TargetSchema(
        "c",
        1,
        {
            "type": "object",
            "properties": {"currency": {"type": "string", "x-forma": {"semantic": "currency"}}},
        },
    )
    old.fields = [old.fields[0].model_copy(update={"target": "/currency", "expr": const("THB")})]
    proposed.fields = [old.fields[0].model_copy(update={"expr": col(header)})]
    agreement = {"disagree": {"/currency": {"matcher": ["THB"], "model": [header]}}}
    mapping._resolve_explicit_currency(agreement, old, proposed, schema, [{"currency": v} for v in values])
    assert ("/currency" not in agreement["disagree"]) is resolved
    if resolved:
        assert agreement["resolved_by_evidence"]["/currency"]["records_checked"] == len(values)


@pytest.mark.parametrize("semantic,value", [("unit.sell", "ZQ"), ("price.amount", 100)])
def test_equal_values_do_not_settle_business_meaning(semantic, value):
    from tests.test_agents import _doc

    old = _doc()
    proposed = old.model_copy(deep=True)
    schema = TargetSchema(
        "meaning",
        1,
        {"type": "object", "properties": {"value": {"x-forma": {"semantic": semantic}}}},
    )
    old.fields = [old.fields[0].model_copy(update={"target": "/value", "expr": const(value)})]
    proposed.fields = [old.fields[0].model_copy(update={"expr": col("value")})]
    agreement = {"disagree": {"/value": {"matcher": [str(value)], "model": ["value"]}}}
    mapping._resolve_explicit_currency(agreement, old, proposed, schema, [{"value": value}] * 3)
    assert "/value" in agreement["disagree"]
