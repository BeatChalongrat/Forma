"""300 real uploads/jobs against the isolated PostgreSQL+Redis+ARQ stack, fake HTTP model only."""
import concurrent.futures
import json
import os
import statistics
import subprocess
import time
from collections import Counter
from pathlib import Path

import httpx

SOURCE = Path(__file__).resolve().parent
HERE = SOURCE / os.environ.get('STABILITY_RUN', 'verified')
HERE.mkdir(exist_ok=True)
API = os.environ.get('STABILITY_API', 'http://127.0.0.1:8000')
FAKE = os.environ.get('STABILITY_FAKE', 'http://fake:8001')
COMPOSE = ['docker', 'compose', '-p', 'forma-stability-verified', '-f', str(SOURCE / 'compose.yml')]
http = httpx.Client(base_url=API, timeout=40)


def request(method, path, **kwargs):
    response = http.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()


def csv(i):
    return ('sku,product_name,currency,price,stock,warehouse,sell_unit,pack_size\n' + '\n'.join(
        f'{i:03d}-{n:04d},Product {n},THB,{n * 10},{n * 3},BKK,PCS,1' for n in range(1, 13))).encode()


def create(i):
    customer = request('POST', '/api/customers', json={'code': f'ST{i:03d}', 'name': f'Stability {i}'})
    rnd = request('POST', '/api/rounds', json={'customer_id': customer['id'], 'label': f'stability-{i}'})
    request('POST', f"/api/rounds/{rnd['id']}/files", files=[('files', (f'input-{i}.csv', csv(i), 'text/csv'))])
    return {'index': i, 'round_id': rnd['id']}


def command(*args):
    out = subprocess.run([*COMPOSE, *args], capture_output=True, text=True, timeout=60)
    with (HERE / 'fault-actions.log').open('a', encoding='utf-8') as f:
        f.write(f'{args}: {out.returncode}\n{out.stdout}\n{out.stderr}\n')
    assert out.returncode == 0, out.stderr


def main():
    assert request('GET', '/ready')['status'] == 'ok'
    ai = request('GET', '/api/settings/ai')
    assert ai['model'] == 'fake-stability' and 'fake:8001' in ai['base_url'], ai
    manifest_path = HERE / 'manifest.json'
    if manifest_path.exists():
        records = json.loads(manifest_path.read_text())
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            records = list(pool.map(create, range(300)))
        manifest_path.write_text(json.dumps(records, indent=2))
    httpx.post(FAKE + '/control', json={'mode': 'normal'}).raise_for_status()
    started = time.time()
    for item in records:
        if 'job_id' not in item:
            item['job_id'] = request('POST', f"/api/rounds/{item['round_id']}/run", json={'use_llm': True})['job_id']
            manifest_path.write_text(json.dumps(records, indent=2))
    observations, actions, retried = [], [], set()
    while time.time() - started < 3600:
        try:
            t0 = time.perf_counter()
            jobs = request('GET', '/api/jobs', params={'limit': 1000})
            latency = time.perf_counter() - t0
        except httpx.HTTPError:
            time.sleep(2)
            continue
        selected = [j for j in jobs if j['id'] in {r['job_id'] for r in records}]
        counts = Counter(j['status'] for j in selected)
        observations.append({'at_s': round(time.time() - started, 2), 'counts': dict(counts), 'api_s': latency})
        print(observations[-1], flush=True)
        done = counts.get('done', 0)
        for j in selected:
            if j['status'] == 'failed' and j['id'] not in retried:
                request('POST', f"/api/jobs/{j['id']}/retry")
                retried.add(j['id'])
        if len(selected) == 300 and counts.get('done', 0) == 300:
            break
        time.sleep(4)
    work_elapsed = time.time() - started
    mappings = []
    for j in selected:
        mid = (j.get('result') or {}).get('mapping_version_id')
        if mid:
            m = request('GET', f'/api/mappings/{mid}')
            mappings.append({'job_id': j['id'], 'mapping_id': mid, 'round_id': j['round_id'],
                             'policy': m.get('policy'), 'status': m.get('status')})
    result = {'jobs': selected, 'mappings': mappings, 'observations': observations,
              'fault_actions': actions, 'manual_retries': sorted(retried),
              'elapsed_s': round(work_elapsed, 2), 'counts': dict(counts),
              'api_latency_p95_s': sorted(o['api_s'] for o in observations)[int(len(observations)*.95)],
              'fake_provider': httpx.get(FAKE + '/stats').json(), 'actual_api_cost_usd': 0}
    (HERE / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    assert counts == {'done': 300}, counts
    assert len({m['mapping_id'] for m in mappings}) == 300
    assert all(m['policy']['simulation']['rows_out'] == 12 for m in mappings)
    assert not retried
    assert all(not m['policy']['checks'].get('llm_fallback') for m in mappings)
    assert all(m['policy'].get('verification', {}).get('passed') for m in mappings)
    assert result['fake_provider']['max_active'] <= int(os.environ.get('TEST_LIMIT', '4'))
    print('PASS: 300 terminal jobs, 300 distinct proposals, 12 output rows each', flush=True)


if __name__ == '__main__':
    main()
