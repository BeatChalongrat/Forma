"""Measure job-module cold imports only; does not execute jobs or call a model."""
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

samples = []
for _ in range(3):
    started = time.perf_counter()
    subprocess.run([sys.executable, '-c', 'import forma.jobs.child; import forma.jobs.handlers'], check=True)
    samples.append(round(time.perf_counter() - started, 3))
result = {'operation': 'Fresh Python imports of forma.jobs.child and forma.jobs.handlers; no job or model call',
          'samples_s': samples, 'median_s': statistics.median(samples)}
Path('/suite/run-after/startup-probe.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result))
