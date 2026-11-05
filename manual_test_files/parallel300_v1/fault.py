"""Sixteen extra jobs for crash/retry recovery, excluded from the speed comparison."""
import json
import time
import httpx
from run import create, request, HERE

HERE.mkdir(exist_ok=True)
httpx.post('http://fake:8001/control', json={'mode':'hang'}).raise_for_status()
items = [create(i) for i in range(300,316)]
for item in items:
    item['job_id'] = request('POST', f"/api/rounds/{item['round_id']}/run", json={'use_llm':True})['job_id']
until = time.monotonic()+240
while time.monotonic()<until:
    jobs = [request('GET', f"/api/jobs/{i['job_id']}") for i in items]
    running = [j for j in jobs if j['status']=='running' and j['progress']>=35]
    active=httpx.get('http://fake:8001/stats').json()['active']
    if len(running)>=6 and active>=6:
        break
    time.sleep(1)
assert len(running)>=6 and active>=6, jobs
(HERE/'ready.json').write_text(json.dumps({j['id']:j['attempts'] for j in running}))
until=time.monotonic()+120
while not (HERE/'continue.json').exists() and time.monotonic()<until:
    time.sleep(.5)
assert (HERE/'continue.json').exists()
httpx.post('http://fake:8001/control', json={'mode':'faults'}).raise_for_status()
start=time.monotonic()
while time.monotonic()-start<600:
    jobs=[request('GET', f"/api/jobs/{i['job_id']}") for i in items]
    if all(j['status']=='done' for j in jobs): break
    time.sleep(3)
rounds=[request('GET', f"/api/rounds/{i['round_id']}") for i in items]
result={'jobs':jobs,'recovery_s':time.monotonic()-start,'round_mapping_counts':[len(r['mappings']) for r in rounds],
        'killed_jobs':json.loads((HERE/'ready.json').read_text()),'provider':httpx.get('http://fake:8001/stats').json()}
(HERE/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
assert all(j['status']=='done' for j in jobs)
assert result['round_mapping_counts']==[1]*16
assert all(j['attempts']>result['killed_jobs'][j['id']] for j in jobs if j['id'] in result['killed_jobs'])
print('PASS: 16 jobs recovered; no manual job retry or duplicate mapping',flush=True)
