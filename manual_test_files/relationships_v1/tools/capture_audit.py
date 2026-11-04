"""Read-only evidence capture for the isolated relationship experiment."""
import hashlib
import json
import subprocess
from datetime import datetime,UTC
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[1]
REPO=ROOT.parents[1]
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
code='''from forma.db.session import session_scope
from forma.db.models import LLMCall,ImportRound
from sqlalchemy import select
import json
with session_scope() as s:
 rows=s.execute(select(ImportRound.label,LLMCall).join(ImportRound,LLMCall.round_id==ImportRound.id).where(ImportRound.label.like('EVAL rel-20261103-%'))).all()
 print(json.dumps([{'round':label,'agent':v.agent,'model':v.model,'input_tokens':v.input_tokens,'output_tokens':v.output_tokens,'cost_usd':v.cost_usd,'latency_ms':v.latency_ms,'attempts':v.attempts,'created_at':str(v.created_at)} for label,v in rows]))
'''
ledger=json.loads(subprocess.check_output(['docker','exec','-i','forma-worker-1','python','-'],input=code,text=True,cwd=REPO))
save(ROOT/'runs/ledger.json',ledger)
manifest=read(ROOT/'manifest-neutral.json')
files=[f for c in manifest['rounds'] for f in c['files']]
assert all(hashlib.sha256((ROOT/f['path']).read_bytes()).hexdigest()==f['sha256'] for f in files)
assert all(hashlib.sha256((ROOT/c['schema']).read_bytes()).hexdigest()==c['schema_sha256'] for c in manifest['rounds'])
meta=read(ROOT/'runs/rel-20261103-b/metadata.json')
changed=[p for p,h in meta['backend_hashes'].items() if hashlib.sha256((REPO/p).read_bytes()).hexdigest()!=h]
jobs=[]
with httpx.Client(base_url='http://127.0.0.1/api',timeout=30) as c:
 for sp in sorted((ROOT/'runs').glob('rel-*/**/state.json')):
  s=read(sp)
  if not s.get('propose_job'):continue
  j=c.get('/jobs/'+s['propose_job']);j.raise_for_status();j=j.json()
  jobs.append({'run':sp.parent.parent.name,'case':s['id'],'job':j})
 ready=httpx.get('http://127.0.0.1/ready',timeout=30).json()
 settings=c.get('/settings/ai').json()
result={'captured_at':datetime.now(UTC).isoformat(),'ready':ready,'model':settings['model'],
 'verified_input_files':len(files),'verified_schemas':len(manifest['rounds']),'changed_backend_files':changed,
 'ledger_records':len(ledger),'recorded_cost_usd':sum(v['cost_usd'] for v in ledger),'jobs':jobs}
save(ROOT/'runs/final-audit.json',result)
print('verified inputs',len(files),'backend changes',changed,'logged cost',result['recorded_cost_usd'])
print('active jobs',[(j['run'],j['case'],j['job']['status']) for j in jobs if j['job']['status'] not in ['done','failed','cancelled']])
