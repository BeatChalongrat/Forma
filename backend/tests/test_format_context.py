from forma.db.models import SourceFile
from forma.services.format_context import differences, observe


def context(currency="THB", price="12.50", date="day_first", country="TH", locale="th-TH", unit="PCS"):
    columns = [
        {"name": name, "samples": [value]}
        for name, value in [
            ("Currency", currency),
            ("Price", price),
            ("Country", country),
            ("Locale", locale),
            ("Unit", unit),
        ]
    ]
    columns.append({"name": "Date", "hints": [date]})
    return observe(
        [SourceFile(profile={"tables": [{"sheet": "Products", "sheet_index": 0, "columns": columns}]})]
    )


def test_same_layout_new_meaning_requires_recheck():
    changed = differences(context(), context("USD", "12,50", "month_first", "US", "en-US"))
    assert {d["kind"] for d in changed} == {"currency", "number", "date", "country", "locale"}


def test_price_changes_and_new_unit_tokens_do_not_reject_whole_format():
    assert differences(context(), context(price="999.99", unit="XYZ")) == []


def test_missing_context_does_not_count_as_matching_evidence():
    assert differences(context(), {"columns": {}})


def test_unknown_names_are_not_guessed_as_country_or_currency():
    c = observe(
        [
            SourceFile(
                original_name="US report.xlsx",
                profile={"tables": [{"columns": [{"name": "Value", "samples": ["$12", "THB", "TH"]}]}]},
            )
        ]
    )
    assert c["columns"] == {}


def test_thai_context_headers_preserve_combining_marks():
    source = SourceFile(
        profile={
            "tables": [
                {"columns": [{"name": "สกุลเงิน", "samples": ["THB"]}, {"name": "หน่วยขาย", "samples": ["BT"]}]}
            ]
        }
    )
    observed = observe([source])["columns"]
    assert any(c.get("currency") == ["THB"] for c in observed.values())
    assert any(c.get("unit") == ["BT"] for c in observed.values())


def test_renamed_csv_and_different_file_order_retain_same_context():
    a = SourceFile(
        profile={"tables": [{"sheet": "first.csv", "columns": [{"name": "Currency", "samples": ["THB"]}]}]}
    )
    b = SourceFile(
        profile={"tables": [{"sheet": "renamed.csv", "columns": [{"name": "Currency", "samples": ["USD"]}]}]}
    )
    assert differences(observe([a, b]), observe([b, a])) == []
