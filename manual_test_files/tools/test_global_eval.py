from copy import deepcopy

from global_score import score

SCHEMA = {
    "required": ["sku"],
    "properties": {"status": {"default": "active"}},
    "x-forma": {
        "identity_keys": ["/sku"],
        "collections": {
            "/prices": {"identity_keys": ["/price_list", "/effective_from"]}
        },
    },
}
EXPECTED = [
    {
        "sku": "01",
        "prices": [
            {"price_list": "standard", "effective_from": "2026-11-01", "amount": 10}
        ],
    }
]


def test_perfect_and_schema_defaults_without_null_inflation():
    got = deepcopy(EXPECTED)
    got[0].update(status="active", source_meta={"file": "a"}, unused=None)
    result = score(got, EXPECTED, SCHEMA)
    assert result["expected_fields"] == 4
    assert result["field_accuracy"] == result["exact_record_accuracy"] == 1


def test_duplicate_price_does_not_overwrite_or_hide_extra():
    got = deepcopy(EXPECTED)
    got[0]["prices"].append(dict(got[0]["prices"][0]))
    result = score(got, EXPECTED, SCHEMA)
    assert len(result["extra_values"]) == 1
    assert result["exact_record_accuracy"] == 0
    assert result["penalized_accuracy"] < 1


def test_price_history_key_includes_date():
    got = deepcopy(EXPECTED)
    got[0]["prices"][0]["effective_from"] = "2026-10-01"
    result = score(got, EXPECTED, SCHEMA)
    assert result["wrong"] and result["extra_values"]
    assert result["exact_record_accuracy"] == 0


def test_duplicate_identity_is_not_collapsed():
    got = deepcopy(EXPECTED) * 2
    result = score(got, EXPECTED, SCHEMA)
    assert result["rows_got"] == 2 and result["duplicate_records"] == 1
    assert result["identity_precision"] == 0.5
    assert result["exact_record_accuracy"] == 0


def test_missing_identity_and_zero_are_not_null():
    result = score(
        [{"sku": "01", "qty": None}],
        [{"sku": "01", "qty": 0}, {"sku": "02", "qty": 2}],
        SCHEMA,
    )
    assert result["identity_recall"] == 0.5 and result["field_accuracy"] == 0.25
    assert result["missing_rows"]


def test_schema_can_change_and_extra_facts_count():
    schema = {"required": ["code"], "properties": {}}
    result = score(
        [{"code": "x", "money": {"amount": 20}, "invented": "USD"}],
        [{"code": "x", "money": {"amount": 20}}],
        schema,
    )
    assert result["field_accuracy"] == 1 and result["penalized_accuracy"] < 1
    assert result["exact_record_accuracy"] == 0


def test_existing_schema_reference_survives_name_shortening(monkeypatch):
    import global_eval as runner

    item = {"case": "G03_my_warehouses", "schema": "unused.json"}
    ref = "eval_intl-20261102-a_g03_my_warehouses@1"
    monkeypatch.setattr(runner, "read", lambda _: {"required": ["sku"]})
    calls = []

    def request(_client, method, path, **kwargs):
        calls.append((method, path))
        return [{"ref": ref}]

    monkeypatch.setattr(runner, "request", request)
    assert runner.prepare_schema(None, item, "intl-20261102-a") == ref
    assert calls == [("GET", "/schemas")]


def test_new_long_schema_reference_fits_audit_storage(monkeypatch):
    import global_eval as runner

    monkeypatch.setattr(runner, "read", lambda _: {"required": ["item_code"]})
    registered = []

    def request(_client, method, path, **kwargs):
        if method == "GET":
            return []
        doc = kwargs["json"]["document"]
        registered.append(doc)
        return {"ref": doc["x-forma"]["schema_id"] + "@1"}

    monkeypatch.setattr(runner, "request", request)
    ref = runner.prepare_schema(None, {"case": "long_case" * 10, "schema": "x"}, "run")
    assert len(ref) <= 40
    assert registered[0]["x-forma"]["identity_keys"] == ["/item_code"]
