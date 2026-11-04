"""Real stack evaluation using existing resumable API harness; never approve or export."""
import sys
import json
import hashlib
import subprocess
from datetime import datetime,UTC
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import httpx

ROOT=Path(__file__).resolve().parents[1]
REPO=ROOT.parents[1]
sys.path.insert(0,str(REPO/'manual_test_files/tools'))
import global_eval as harness
harness.ROOT=ROOT
from global_score import score

def evaluate(before,item):
    expected=harness.read(ROOT/item['expected'])
    schema=harness.read(ROOT/item['schema'])
    # Inventory order is not a contract requirement. Match by warehouse and preserve multiplicity.
    if 'inventory' in schema['properties']:
        schema.setdefault('x-forma',{}).setdefault('collections',{}).setdefault('/inventory',{})['identity_keys']=['/warehouse_code']
    result=score(before['preview'].get('records',[]),expected,schema)
    result['expected_questions']=harness.read(ROOT/item['review'])['questions']
    result['phase']='Before human assistance; unresolved oracle-only facts scored separately in report.'
    return result
harness.evaluate_snapshot=evaluate

def snapshot(c,rid):
    r=harness.request(c,'GET',f'/rounds/{rid}')
    ms=r.get('mappings') or []
    if not ms:return {'round':r,'mapping':None,'questions':{},'preview':{'records':[]}}
    mid=max(ms,key=lambda m:m.get('version',0))['id']
    m=harness.request(c,'GET',f'/mappings/{mid}')
    qs=harness.request(c,'GET',f'/mappings/{mid}/questions')
    response=c.post(f'/rounds/{rid}/mapping-preview',json={'document':m['document'],'limit':500})
    preview=response.json() if response.is_success else {'records':[],'http_status':response.status_code,'error':response.text[:4000]}
    return {'round':r,'mapping':m,'questions':qs,'preview':preview}
harness.snapshot=snapshot

def main():
    run_id=sys.argv[1] if len(sys.argv)>1 else 'rel-20261103-a'
    folder=ROOT/'runs'/run_id;folder.mkdir(parents=True,exist_ok=True)
    manifest_name='manifest.json' if run_id.endswith('-a') else 'manifest-neutral.json'
    manifest=harness.read(ROOT/manifest_name)
    with httpx.Client(base_url='http://127.0.0.1/api',timeout=180) as c:
        ready=httpx.get('http://127.0.0.1/ready',timeout=30).json();assert ready['status']=='ok'
        settings=harness.request(c,'GET','/settings/ai');assert settings['model']=='deepseek-flash'
        if not (folder/'metadata.json').exists():
            harness.save(folder/'metadata.json',{'started_at':datetime.now(UTC).isoformat(),'model':settings['model'],'provider':settings['provider'],
                'ready':ready,'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
                'worker_image':subprocess.check_output(['docker','inspect','--format','{{.Image}}','forma-worker-1'],cwd=REPO,text=True).strip(),
                'manifest_file':manifest_name,'manifest_sha256':hashlib.sha256((ROOT/manifest_name).read_bytes()).hexdigest(),
                'backend_hashes':{str(p.relative_to(REPO)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((REPO/'backend/src/forma').rglob('*.py'))},
                'note':'Cold isolated customers; no approve/export. Only task, schema and source files sent. Oracle read only after frozen prediction.'})
        def one(item):
            if item.get('schema_sha256') and hashlib.sha256((ROOT/item['schema']).read_bytes()).hexdigest()!=item['schema_sha256']:
                raise RuntimeError('Schema changed during run')
            print('START',item['id'],flush=True)
            try:
                result=harness.run_case(c,item,folder,run_id)
                print('DONE',item['id'],json.dumps({k:result.get(k) for k in ['field_accuracy','exact_record_accuracy','questions','decision','failed_stage','elapsed_active_seconds']},ensure_ascii=False),flush=True)
            except Exception as exc:
                harness.save(folder/item['id']/'error.json',{'type':type(exc).__name__,'error':str(exc)})
                print('ERROR',item['id'],type(exc).__name__,str(exc)[:300],flush=True)
        workers=int(sys.argv[2]) if len(sys.argv)>2 else 1
        harness.save(folder/'execution.json',{'client_concurrency':workers,'note':'Each case has its own customer/schema/round. Existing jobs are polled on resume.'})
        selected=[i for i in manifest['rounds'] if not sys.argv[3:] or i['case'] in sys.argv[3:]]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(one,selected))

if __name__=='__main__':main()
