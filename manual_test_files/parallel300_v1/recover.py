import os
import subprocess
import time
from pathlib import Path

root=Path(__file__).resolve().parent
env={**os.environ,'TEST_CONCURRENCY':'8'}
compose=['docker','compose','-p','forma-parallel300-after','-f',str(root/'compose.yml')]
subprocess.run([*compose,'up','-d','--no-build','--scale','worker=2'],env=env,check=True)
for attempt in range(60):
    ready=subprocess.run([*compose,'exec','-T','api','python','-c',"import httpx; assert httpx.get('http://127.0.0.1:8000/ready').json()['status']=='ok'"],env=env,capture_output=True)
    if ready.returncode == 0: break
    time.sleep(1)
assert ready.returncode == 0, ready.stderr
process=subprocess.Popen([*compose,'exec','-T','-e','STABILITY_RUN=fault','api','python','/test/fault.py'],env=env)
until=time.monotonic()+300
while not (root/'fault/ready.json').exists() and time.monotonic()<until:
    if process.poll() is not None: raise SystemExit(process.returncode or 1)
    time.sleep(1)
assert (root/'fault/ready.json').exists()
subprocess.run([*compose,'kill','-s','SIGKILL','worker'],env=env,check=True)
(root/'fault/continue.json').write_text('{}')
subprocess.run([*compose,'start','worker'],env=env,check=True)
code=process.wait(timeout=700)
subprocess.run([*compose,'stop'],env=env,check=True)
raise SystemExit(code)
