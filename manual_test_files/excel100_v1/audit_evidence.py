"""Post-run source-cell evidence checks; isolated HTTP calls, no model calls."""
import json
from pathlib import Path

import httpx

ROOT = Path('/suite')
OUT = ROOT / 'run-after'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


with httpx.Client(base_url='http://127.0.0.1:8000', timeout=120) as client:
    checks = []
    for spec in read(ROOT / 'manifest.json')['files']:
        if spec['family'] != 3:
            continue
        case = spec['id']
        mapping = read(OUT / case / 'answered.json')['mapping']['id']
        response = client.get(f'/api/mappings/{mapping}/questions/evidence', params={'field': '/inventory/*/qty'})
        response.raise_for_status()
        evidence = response.json()
        (OUT / case / 'quantity-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
        for example in evidence['examples']:
            total = sum(float(c['raw']) for c in example['cells'])
            valid = example['source_complete'] and len(example['cells']) == 3 and abs(total - example['value']) < 1e-6
            checks.append({'id': case, 'identity': example['identity'], 'item': example['item'],
                           'value': example['value'], 'source_sum': total, 'source_cells': len(example['cells']),
                           'passed': valid})
    (OUT / 'evidence-audit.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'examples': len(checks), 'passed': sum(x['passed'] for x in checks)}))
    assert len(checks) == 50 and all(x['passed'] for x in checks)
