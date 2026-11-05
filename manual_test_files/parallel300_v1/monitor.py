import json,subprocess,time
from pathlib import Path
p=Path('manual_test_files/parallel300_v1')
with (p/'resources.jsonl').open('a',encoding='utf-8') as f:
 while not (p/'summary.json').exists():
  label='after' if (p/'after/manifest.json').exists() else 'before'
  names=[f'forma-parallel300-{label}-api-1',f'forma-parallel300-{label}-worker-1']
  if label=='after': names.append('forma-parallel300-after-worker-2')
  r=subprocess.run(['docker','stats','--no-stream','--format','{{json .}}',*names],capture_output=True,text=True,encoding='utf-8')
  for line in r.stdout.splitlines():
   try: row=json.loads(line)
   except ValueError: continue
   row.update(phase=label,at=time.time());f.write(json.dumps(row)+'\n')
  f.flush();time.sleep(20)
