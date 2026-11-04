"""No LLM, no fixture edits: change one source pack factor in memory and execute saved mappings."""
import json
from pathlib import Path
from unittest.mock import patch

import polars as pl
from forma.engine import transform
from forma.engine.transform import BoundSource, TransformEngine
from forma.mapping_dsl.models import MappingDocument
from forma.schema_registry import TargetSchema

ROOT = Path(__file__).resolve().parent
SUITE = ROOT.parent / 'relationships_v1'
SOURCE = SUITE / 'inputs/R12_combined/r1/delivery.xlsx'
SCHEMA = TargetSchema('sensitivity', 1, json.loads((SUITE / 'schemas-neutral/T12.json').read_text(encoding='utf-8')))
original_read = transform.read_raw
sku, new_pack = '000001', 18


def modified_read(path, sheet=None, **kwargs):
    frame = original_read(path, sheet, **kwargs)
    if sheet == 'Pack sizes':
        rows = [list(row) for row in frame.rows()]
        matches = [row for row in rows if str(row[0]) == sku]
        assert len(matches) == 1 and float(matches[0][1]) == 12
        matches[0][1] = str(new_pack)
        return pl.DataFrame(rows, schema=frame.schema, orient='row')
    return frame


def expected():
    rows = original_read(SOURCE, 'Batches').rows()
    header = list(rows[0])
    totals = {}
    for values in rows[1:]:
        row = dict(zip(header, values, strict=True))
        if str(row['Material code']) != sku:
            continue
        quantity = float(row['Stock quantity']) * (new_pack if row['Unit'] == 'CASE' else 1)
        totals[row['Warehouse']] = totals.get(row['Warehouse'], 0) + quantity
    return totals


def evaluate(document):
    doc = MappingDocument.model_validate(document)
    bindings = {s.id: BoundSource(SOURCE, s.sheet.name or s.sheet.index) for s in doc.sources}
    with patch.object(transform, 'read_raw', modified_read):
        result = TransformEngine(SCHEMA).run(doc, bindings)
    assert not result.errors, [f.message for f in result.errors]
    record = next(r for r in result.records if r['sku'] == sku)
    return {r['warehouse_code']: r['qty'] for r in record['inventory']}


def main():
    documents = {'graph_initial': json.loads((ROOT / 't12-initial-mapping.json').read_text(encoding='utf-8'))}
    for arm in ['wf-before', 'wf-after']:
        documents[arm] = json.loads((ROOT / 'runs' / arm / 'T12_r1/before.json').read_text(encoding='utf-8'))['mapping']['document']
    target = expected()
    actual = {name: evaluate(doc) for name, doc in documents.items()}
    assert actual['graph_initial'] != target
    assert actual['wf-before'] == actual['wf-after'] == target
    result = {'mutation': f'Pack sizes: {sku}, 12 -> {new_pack}; original files untouched',
              'expected_from_lot_rows': target, 'actual': actual, 'passed': True, 'model_calls': 0}
    (ROOT / 'sensitivity-result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
