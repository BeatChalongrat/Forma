"""Answer two withheld business decisions only after BOTH arms are frozen; no approval/export."""
import sys
import httpx
import run as runner

h = runner.h
ROOT = runner.ROOT
h.ROOT = runner.REPO / 'manual_test_files/relationships_v1'


def answer(run_id, case):
    for arm in ('wf-before', 'wf-after'):
        assert (ROOT / 'runs' / arm / (case + '_r1') / 'before.json').exists(), 'Freeze both proposals first'
    dest = ROOT / 'runs' / run_id / (case + '_r1')
    before = h.read(dest / 'before.json')
    assert before['round']['label'].startswith('EVAL ' + run_id + ' ')
    item = next(i for i in h.read(h.ROOT / 'manifest-neutral.json')['rounds'] if i['case'] == case)
    expected = h.read(h.ROOT / item['review'])['questions'][0]['answer']
    questions = before['questions']['questions']
    if case == 'T09':
        q = next(q for q in questions if q.get('action') == 'value_map' and q.get('source_value') == 'BT')
        value = expected
    elif case == 'T10':
        candidates = [(q, key) for q in questions if q.get('action') == 'select_rows'
                      for key, label in q.get('option_labels', {}).items() if f'price: {expected}' in label]
        assert len(candidates) == 1, 'No unique record-scoped choice; do not substitute a global price'
        q, value = candidates[0]
    else:
        raise ValueError('Unsupported decision')
    payload = {'answers': {q['id']: value}}
    with httpx.Client(base_url='http://127.0.0.1/api', timeout=180) as c:
        if not (dest / 'human-applied.json').exists():
            preview = h.request(c, 'POST', f"/mappings/{before['mapping']['id']}/answers/preview", json=payload)
            h.save(dest / 'human-payload.json', payload)
            h.save(dest / 'human-preview.json', preview)
            assert preview['preview']['ok'] and preview['comparison']['validation']['passed'], preview
            applied = h.request(c, 'POST', f"/mappings/{before['mapping']['id']}/answers/apply", json={**payload, 'digest': preview['digest']})
            h.save(dest / 'human-applied.json', applied)
        after = h.snapshot(c, before['round']['id'])
        h.save(dest / 'after-human.json', after)
        score = runner.evaluate(after, item)
        score['phase'] = 'after simulated operator decision; no approval/export'
        h.save(dest / 'score-after-human.json', score)
        print(run_id, case, score['field_accuracy'], score['rows_got'], flush=True)


if __name__ == '__main__':
    answer(*sys.argv[1:3])
