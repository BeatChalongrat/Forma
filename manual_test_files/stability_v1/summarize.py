"""Reproduce the measured summary without running a model or changing application data."""
import json
from collections import Counter
from pathlib import Path

root = Path(__file__).parent
data = json.loads((root / 'verified/result.json').read_text(encoding='utf-8'))
fault = json.loads((root / 'verified/fault-recheck.json').read_text(encoding='utf-8'))
jobs = {j['id']: j for j in data['jobs']}
policies = [m['policy'] for m in data['mappings']]
summary = {
    'jobs': data['counts'],
    'attempts': dict(Counter(j['attempts'] for j in jobs.values())),
    'manual_retries': len(data['manual_retries']),
    'mapping_statuses': dict(Counter(m['status'] for m in data['mappings'])),
    'rows_out': dict(Counter(p['simulation']['rows_out'] for p in policies)),
    'verification_passed': dict(Counter(str(p.get('verification', {}).get('passed')) for p in policies)),
    'llm_fallbacks': sum(bool(p['checks'].get('llm_fallback')) for p in policies),
    'workflow_role_outcomes': dict(Counter(f"{r['role']}:{r['outcome']}" for p in policies
                                         for r in p.get('workflow', {}).get('roles', []))),
    'elapsed_s': data['elapsed_s'],
    'api_p95_s': data['api_latency_p95_s'],
    'provider_faults': data['fake_provider']['faults'],
    'provider_requests': data['fake_provider']['requests'],
    'actual_api_cost_usd': data['actual_api_cost_usd'],
    'captured_killed_jobs': {jid: {'status': jobs[jid]['status'], 'attempts': jobs[jid]['attempts']}
                             for jid in fault['jobs']},
}
assert all(j['status'] == 'done' and j['attempts'] >= 2 for j in summary['captured_killed_jobs'].values())
(root / 'verified/summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False, indent=2))
