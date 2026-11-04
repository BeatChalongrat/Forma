"""Simulated operator response, only after a frozen model prediction; no approval/export."""
import importlib.util
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('relationship_runner', ROOT / 'tools/run.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
read, save, request = r.harness.read, r.harness.save, r.harness.request


def main():
    run_id, case = sys.argv[1:3]
    dest = ROOT / 'runs' / run_id / (case + '_r1')
    before = read(dest / 'before.json')
    questions = before['questions']['questions']
    item = next(i for i in read(ROOT / 'manifest-neutral.json')['rounds'] if i['case'] == case)
    expected = read(ROOT / item['review'])['questions'][0]['answer']
    if case == 'T09':
        q = next(q for q in questions if q.get('action') == 'value_map' and q.get('source_value') == 'BT')
        value = expected
    elif case == 'T10':
        options = [(q, key) for q in questions if q.get('action') == 'select_rows'
                   for key, label in q.get('option_labels', {}).items() if f'price: {expected}' in label]
        if len(options) != 1:
            raise ValueError('No unambiguous record-scoped price choice; do not apply a global price constant')
        q, value = options[0]
    else:
        raise ValueError('Only withheld unit and price decisions are supported')
    payload = {'answers': {q['id']: value}}
    with httpx.Client(base_url='http://127.0.0.1/api', timeout=180) as c:
        if not (dest / 'human-applied.json').exists():
            preview = request(c, 'POST', f"/mappings/{before['mapping']['id']}/answers/preview", json=payload)
            save(dest / 'human-payload.json', payload)
            save(dest / 'human-preview.json', preview)
            assert preview['preview']['ok'] and preview['comparison']['validation']['passed'], preview
            result = request(c, 'POST', f"/mappings/{before['mapping']['id']}/answers/apply",
                             json={**payload, 'digest': preview['digest']})
            save(dest / 'human-applied.json', result)
        after = r.snapshot(c, before['round']['id'])
        save(dest / 'after-human.json', after)
        score = r.evaluate(after, item)
        score['phase'] = 'After simulated human answer; no approval/export'
        save(dest / 'score-after-human.json', score)
        print(case, 'accuracy', score['field_accuracy'], 'rows', score['rows_got'],
              'cards', len(after['questions']['questions']), 'open', len(after['questions']['open_questions']))


if __name__ == '__main__':
    main()
