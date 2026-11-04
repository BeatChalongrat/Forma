"""Read-only final audit for fixed runs; preserves the original baseline ledger."""
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    code = '''from forma.db.session import session_scope
from forma.db.models import LLMCall,ImportRound
from sqlalchemy import select
import json
with session_scope() as s:
 rows=s.execute(select(ImportRound.label,LLMCall).join(ImportRound,LLMCall.round_id==ImportRound.id).where(ImportRound.label.like('EVAL rel-20261104-fixed%'))).all()
 print(json.dumps([{'round':label,'agent':v.agent,'model':v.model,'input_tokens':v.input_tokens,'output_tokens':v.output_tokens,'cost_usd':v.cost_usd,'latency_ms':v.latency_ms,'attempts':v.attempts,'created_at':str(v.created_at)} for label,v in rows]))
'''
    ledger = json.loads(subprocess.check_output(
        ['docker', 'exec', '-i', 'forma-worker-1', 'python', '-'], input=code, text=True, cwd=REPO))
    save(ROOT / 'runs/ledger-fixed.json', ledger)
    manifest = read(ROOT / 'manifest-neutral.json')
    files = [f for case in manifest['rounds'] for f in case['files']]
    assert all(hashlib.sha256((ROOT / f['path']).read_bytes()).hexdigest() == f['sha256'] for f in files)
    assert all(hashlib.sha256((ROOT / c['schema']).read_bytes()).hexdigest() == c['schema_sha256'] for c in manifest['rounds'])
    folder = ROOT / 'runs/rel-20261104-fixed2'
    meta = read(folder / 'metadata.json')
    changed = [p for p, h in meta['backend_hashes'].items() if hashlib.sha256((REPO / p).read_bytes()).hexdigest() != h]
    worker_image = subprocess.check_output(['docker', 'inspect', '--format', '{{.Image}}', 'forma-worker-1'], cwd=REPO, text=True).strip()
    jobs = []
    with httpx.Client(base_url='http://127.0.0.1/api', timeout=30) as client:
        for state_path in sorted(folder.glob('*/state.json')):
            state = read(state_path)
            for stage in ('profile', 'propose'):
                if not state.get(stage + '_job'):
                    continue
                result = client.get('/jobs/' + state[stage + '_job'])
                result.raise_for_status()
                job = result.json()
                jobs.append({'case': state['id'], 'stage': stage, 'status': job['status'],
                             'error': job.get('error'), 'message': job.get('message')})
        settings = client.get('/settings/ai').json()
        ready = httpx.get('http://127.0.0.1/ready', timeout=30).json()
    result = {'captured_at': datetime.now(UTC).isoformat(), 'ready': ready, 'model': settings['model'],
              'verified_input_files': len(files), 'verified_schemas': len(manifest['rounds']),
              'changed_backend_files_during_final_run': changed,
              'post_prediction_note': 'verdict.py rules version was bumped after a browser check to invalidate old UI summaries; mapping/engine/provider code was unchanged during fixed2.',
              'same_worker_image': worker_image == meta['worker_image'], 'jobs': jobs,
              'recorded_cost_usd': sum(v['cost_usd'] or 0 for v in ledger), 'ledger_records': len(ledger)}
    save(ROOT / 'runs/final-audit-fixed.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
