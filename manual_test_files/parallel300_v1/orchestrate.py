"""Run identical isolated 300-job workloads sequentially, never against localhost production."""
import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for label, slots, replicas in [('before', 4, 1), ('after', 8, 2)]:
    env = {**os.environ, 'TEST_CONCURRENCY': str(slots)}
    compose = ['docker', 'compose', '-p', f'forma-parallel300-{label}', '-f', str(ROOT/'compose.yml')]
    if (ROOT/label/'result.json').exists():
        result = json.loads((ROOT/label/'result.json').read_text(encoding='utf-8'))
        assert result['counts'] == {'done': 300}
        continue
    subprocess.run([*compose, 'up', '-d', '--no-build', '--scale', f'worker={replicas}'], env=env, check=True)
    for attempt in range(60):
        ready=subprocess.run([*compose,'exec','-T','api','python','-c',"import httpx; assert httpx.get('http://127.0.0.1:8000/ready').json()['status']=='ok'"],env=env,capture_output=True)
        if ready.returncode == 0: break
        time.sleep(1)
    assert ready.returncode == 0, ready.stderr
    cmd = [*compose, 'exec', '-T', '-e', f'STABILITY_RUN={label}', '-e', f'TEST_LIMIT={slots}', 'api', 'python', '/test/run.py']
    process = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8')
    checkpoint = -1
    with (ROOT/f'{label}.log').open('w', encoding='utf-8') as log:
        for line in process.stdout:
            log.write(line); log.flush()
            if "'counts':" in line:
                import ast
                observation = ast.literal_eval(line.strip())
                current = observation['counts'].get('done', 0) // 25
                if current != checkpoint:
                    print(label, line.strip(), flush=True); checkpoint = current
            else:
                print(label, line.strip(), flush=True)
    code = process.wait()
    subprocess.run([*compose, 'logs', '--no-color', 'worker'], env=env, stdout=(ROOT/f'{label}-workers.log').open('w', encoding='utf-8'), check=True)
    subprocess.run([*compose, 'stop'], env=env, check=True)
    if code:
        raise SystemExit(code)
print('Both 300-job comparisons complete', flush=True)
