"""Replay all frozen model documents on the final runtime. No LLM, approvals or exports."""
import importlib.util
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('relationship_runner', ROOT / 'tools/run.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
read, save = runner.harness.read, runner.harness.save


def main():
    folder = ROOT / 'runs/rel-20261104-fixed2'
    results = []
    with httpx.Client(base_url='http://127.0.0.1/api', timeout=180) as client:
        for item in read(ROOT / 'manifest-neutral.json')['rounds']:
            dest = folder / item['id']
            snapshots = ['before.json'] + (['after-human.json'] if (dest / 'after-human.json').exists() else [])
            for name in snapshots:
                original = read(dest / name)
                response = client.post(f"/rounds/{original['round']['id']}/mapping-preview",
                                       json={'document': original['mapping']['document'], 'limit': 500})
                response.raise_for_status()
                preview = response.json()
                replayed = {**original, 'preview': preview}
                score = runner.evaluate(replayed, item)
                prior = runner.evaluate(original, item)
                entry = {'case': item['case'], 'phase': name, 'field_accuracy': score['field_accuracy'],
                         'rows': score['rows_got'], 'same_accuracy': score['field_accuracy'] == prior['field_accuracy'],
                         'same_records': preview.get('records') == original['preview'].get('records'),
                         'preview_ok': preview.get('ok'), 'score': score}
                save(dest / ('final-replay-' + name), {'preview': preview, 'result': entry})
                results.append(entry)
                print(item['case'], name, entry['field_accuracy'], 'same', entry['same_records'], flush=True)
    save(folder / 'final-replay.json', results)
    assert all(r['same_accuracy'] and r['same_records'] for r in results)


if __name__ == '__main__':
    main()
