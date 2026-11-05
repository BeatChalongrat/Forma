"""Resumable real HTTP flow. Run INSIDE isolated API container; no external network.
Oracle is evaluator-only. Fake provider mounts only provider/.
"""
import hashlib
import io
import json
import os
import sys
import time
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import httpx

ROOT = Path('/suite')
OUT = ROOT/os.environ.get('EXCEL100_RUN', 'run')
OUT.mkdir(exist_ok=True)
c = httpx.Client(base_url='http://127.0.0.1:8000', timeout=120)

def save(path, obj):
    p = OUT/path; p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
def read(path): return json.loads(path.read_text(encoding='utf-8'))
def req(method, path, **kw):
    r = c.request(method, path, **kw)
    if not r.is_success: raise RuntimeError(f'{method} {path} {r.status_code}: {r.text[:2500]}')
    return r.json()
def canonical(value):
    if isinstance(value, dict): return {k:canonical(v) for k,v in sorted(value.items()) if k not in ('source_meta','attributes')}
    if isinstance(value, list): return sorted([canonical(v) for v in value], key=lambda v:json.dumps(v,sort_keys=True))
    return value
def column_names(expr):
    if not isinstance(expr,dict): return []
    if expr.get('op')=='col': return [expr['name']]
    return [name for arg in expr.get('args',[]) for name in column_names(arg)]
def score(records, expected, ignore_unit=False):
    got = {str(x.get('sku')):x for x in records}
    wrong = []
    total = correct = 0
    def walk(a,b,path):
        nonlocal total,correct
        if isinstance(b,dict):
            for k,v in b.items():
                if ignore_unit and k=='sell_unit': continue
                walk(a.get(k) if isinstance(a,dict) else None,v,path+'/'+k)
        elif isinstance(b,list):
            aa = sorted(a,key=lambda v:str(v.get('warehouse_code'))) if isinstance(a,list) else []
            bb = sorted(b,key=lambda v:str(v.get('warehouse_code')))
            if len(aa)!=len(bb): wrong.append({'path':path,'expected_items':len(bb),'got_items':len(aa)})
            for i,v in enumerate(bb): walk(aa[i] if i<len(aa) else None,v,path+f'/{i}')
        else:
            total += 1
            match = abs(a-b)<.000001 if isinstance(a,(float,int)) and isinstance(b,(float,int)) else a==b
            if match: correct += 1
            else: wrong.append({'path':path,'expected':b,'got':a})
    for e in expected: walk(got.get(e['sku']), e, e['sku'])
    extra = sorted(set(got)-{x['sku'] for x in expected})
    return {'rows_got':len(records),'rows_expected':len(expected),'field_accuracy':correct/total if total else 0,
            'correct_fields':correct,'total_fields':total,'extra_rows':extra,
            'duplicate_rows':len(records)-len(got),'wrong':wrong,
            'exact':not wrong and not extra and len(records)==len(expected)}

def snapshot(item, cid, stage):
    r = req('GET',f"/api/rounds/{item['round_id']}")
    ms = r.get('mappings',[])
    if not ms:
        result = {'round':r,'mapping':None,'questions':{},'preview':{'records':[]}}
    else:
        mid = max(ms,key=lambda m:m.get('version',0))['id']
        m = req('GET',f'/api/mappings/{mid}')
        q = req('GET',f'/api/mappings/{mid}/questions')
        response = c.post(f"/api/rounds/{item['round_id']}/mapping-preview",json={'document':m['document'],'limit':500})
        preview = response.json() if response.is_success else {'records':[],'http_status':response.status_code,'error':response.text[:5000]}
        result = {'round':r,'mapping':m,'questions':q,'preview':preview}
    save(f'{cid}/{stage}.json',result)
    return result

def wait_item(bid, iid, timeout=1800):
    deadline = time.monotonic()+timeout
    while time.monotonic()<deadline:
        b = req('GET',f'/api/batches/{bid}')
        item = next(i for i in b['items'] if i['id']==iid)
        if item['state']!='processing': return item
        time.sleep(3)
    raise TimeoutError(f'{iid}: {item}')

def one(spec,bid,iid):
    cid=spec['id']; dest=OUT/cid/'result.json'
    if dest.exists() and read(dest).get('complete') and '--retry-errors' not in sys.argv: return read(dest)
    if dest.exists() and '--retry-errors' in sys.argv and not read(dest).get('error'): return read(dest)
    started=time.monotonic()
    result={'id':cid,'family':spec['family'],'expected_action':spec['expected_action'],'item_id':iid}
    try:
        item=wait_item(bid,iid)
        if item['state']=='needs_customer':
            result['customer_confirmation_required']=True
            result['customer_match_reason']=item.get('match')
            customer=read(OUT/'state.json')['customers'][cid]
            req('POST',f'/api/batches/{bid}/assign',json={'item_ids':[iid],'customer_id':customer,'use_llm':True})
            item=wait_item(bid,iid)
        result['before_state']=item['state']; result['round_id']=item.get('round_id')
        if not item.get('round_id'): raise RuntimeError('No assigned round: '+str(item))
        before=snapshot(item,cid,'before')
        oracle=read(ROOT/'oracle'/f'{cid}.json')
        result['before_score']=score(before['preview'].get('records',[]),oracle['rows'])
        q=before['questions'].get('questions',[])
        result['questions']=[{'id':x['id'],'kind':x['kind'],'question':x.get('question',x.get('label'))} for x in q]
        result['question_count']=len(q)
        result['verification']=before['mapping'].get('policy',{}).get('verification') if before.get('mapping') else None
        mid=before['mapping']['id'] if before['mapping'] else None
        if spec['expected_action']=='block':
            # Negative authorization test in isolated data: unresolved issues must be rejected.
            if mid:
                probe=c.post(f'/api/mappings/{mid}/approve',json={})
                result['approval_probe']={'status':probe.status_code,'body':probe.text[:5000]}
            result['guard_pass']=item['state']!='ready' and (not mid or result['approval_probe']['status']>=400)
        else:
            precheck=score(before['preview'].get('records',[]),oracle['rows'],ignore_unit=spec['family']==8)
            if not precheck['exact']: raise RuntimeError('Source-to-JSON mismatch before approval; withheld approval')
            answers={}
            for question in q:
                qid=question['id']; pointer=question.get('field',{}).get('pointer',qid)
                if spec['family']==8 and question['kind'] in ('text','enum') and (pointer=='/sell_unit' or '/sell_unit' in qid):
                    answers[qid]='BOTTLE'
                elif question['kind']=='confirm':
                    # Confirm only after independently comparing every mapped data value.
                    answers[qid]='confirm'
                elif question['kind']=='choice' and len(set(column_names(question.get('current',{}))))==1 and column_names(question['current'])[0] in question.get('options',[]):
                    answers[qid]=column_names(question['current'])[0]
                else: raise RuntimeError('Unexpected question needs examination: '+str(question))
            if spec['family']==8:
                result['ask_pass']=any(v=='BOTTLE' for v in answers.values()) and item['state']!='ready'
                if not result['ask_pass']: raise RuntimeError('Missing specific unit question before publication')
            if answers:
                body={'answers':answers}
                preview=req('POST',f'/api/mappings/{mid}/answers/preview',json=body)
                save(f'{cid}/answer-preview.json',preview)
                if not preview['preview']['ok']: raise RuntimeError('Answer preview invalid')
                shown = preview['preview'].get('records', [])
                shown_ids = {x['sku'] for x in shown}
                shown_score = score(shown, [x for x in oracle['rows'] if x['sku'] in shown_ids])
                if not shown_score['exact']: raise RuntimeError('Displayed preview values differ from full source truth')
                result['preview_exact'] = True
                stale=c.post(f'/api/mappings/{mid}/answers/apply',json={**body,'digest':'invalid-preview'})
                if stale.status_code!=409: raise RuntimeError('Stale answer preview accepted')
                applied=req('POST',f'/api/mappings/{mid}/answers/apply',json={**body,'digest':preview['digest']})
                result['answers']=answers; result['new_mapping']=applied['id'];result['stale_preview_rejected']=True
                if applied['parent_id']!=mid: raise RuntimeError('Lost mapping lineage')
                mid=applied['id']
            after=snapshot(item,cid,'answered')
            result['after_score']=score(after['preview'].get('records',[]),oracle['rows'])
            if not result['after_score']['exact']: raise RuntimeError('Answer changed unrelated values; withheld approval')
            display=req('POST',f"/api/rounds/{item['round_id']}/mapping-preview",json={'document':after['mapping']['document'],'limit':40})
            save(f'{cid}/display-preview.json',display)
            shown=display.get('records',[])
            shown_ids={x['sku'] for x in shown}
            display_score=score(shown,[x for x in oracle['rows'] if x['sku'] in shown_ids])
            result['display_preview_exact']=bool(display['ok'] and display_score['exact'] and len(shown)==min(40,len(oracle['rows'])))
            if not result['display_preview_exact']: raise RuntimeError('Mapping Studio display preview differs from full source truth')
            if item['state']!='ready' or answers:
                req('POST',f'/api/mappings/{mid}/approve',json={})
                item=wait_item(bid,iid)
            if item['state']=='needs_data_check':
                r=req('GET',f"/api/rounds/{item['round_id']}")
                dataset=max(r['datasets'],key=lambda x:x.get('version',0))
                req('POST',f"/api/datasets/{dataset['id']}/approve",json={'acknowledge_anomalies':True})
                item=wait_item(bid,iid)
            result['after_state']=item['state']
            if item['state']!='ready': raise RuntimeError('Not ready after approval: '+str(item))
            result['flow_pass']=True
        result['complete']=True
    except Exception as exc:
        result['error']=str(exc); result['complete']=True
    result['elapsed_s']=round(time.monotonic()-started,2)
    save(f'{cid}/result.json',result)
    brief={k:result.get(k) for k in ('id','before_state','after_state','flow_pass','guard_pass','ask_pass','elapsed_s')}
    if result.get('error'): brief['error']=result['error'][:160]
    print(json.dumps(brief,ensure_ascii=False),flush=True)
    return result

def download(bid):
    response=c.get(f'/api/batches/{bid}/download')
    if response.status_code!=200:
        save('download.json',{'status':response.status_code,'error':response.text});return
    (OUT/'download.zip').write_bytes(response.content)
    findings=[]
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        manifest=json.loads(z.read('_forma/manifest.json')); save('download-manifest.json',manifest)
        states={read(p)['item_id']:read(p) for p in OUT.glob('X*/result.json')}
        for entry in manifest['files']:
            state=states.get(entry['item_id'])
            if not state: continue
            check={'id':state['id'],'included':entry['included'],'reason':entry.get('reason')}
            if entry['included']:
                data=z.read(entry['archive_name'])
                check['hash_ok']=hashlib.sha256(data).hexdigest()==entry['sha256']
                check['versions_present']=bool(entry.get('mapping_version_id') and entry.get('dataset_version_id'))
                check['score']=score(json.loads(data),read(ROOT/'oracle'/f"{state['id']}.json")['rows'])
            findings.append(check)
    save('download.json',findings)

def main():
    assert req('GET','/ready')['status']=='ok'
    ai=req('GET','/api/settings/ai')
    assert ai['model']=='fake-excel100' and ai['base_url']=='http://fake:8001/v1', ai
    specs=read(ROOT/'manifest.json')['files']
    if '--pilot' in sys.argv: specs=[s for s in specs if s['id'] in ('X0101','X0301','X0401','X0501','X0701','X0801','X0901','X1001')]
    statefile=OUT/'state.json'
    state=read(statefile) if statefile.exists() else {'started_at':datetime.now(timezone.utc).isoformat(),'customers':{},'uploaded':[]}
    if not (OUT/'metadata.json').exists():
        save('metadata.json',{'started_at':state['started_at'],'model':ai['model'],'endpoint':ai['base_url'],
            'no_external_network':True,'mode':'Scripted DSL fake, not a model intelligence benchmark',
            'fixture_hashes':read(ROOT/'qa/fixtures.json'),'cost_usd':0})
    for spec in specs:
        cid=spec['id']
        if cid in state['customers']: continue
        schemas=req('GET','/api/schemas')
        if not any(s['ref']==spec['schema_ref'] for s in schemas):
            req('POST','/api/schemas',json={'document':read(ROOT/spec['schema'])})
        customer=req('POST','/api/customers',json={'code':cid,'name':f"Excel100 {cid}",'notes':'Synthetic test only'})
        req('PATCH',f"/api/customers/{customer['id']}",json={'default_target_schema':spec['schema_ref']})
        state['customers'][cid]=customer['id'];save('state.json',state)
    pending=[s for s in specs if s['id'] not in state['uploaded']]
    for offset in range(0,len(pending),10):
        chunk=pending[offset:offset+10]
        files=[('files',(s['id']+'.xlsx',(ROOT/s['path']).read_bytes(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')) for s in chunk]
        if 'batch' not in state:
            b=req('POST','/api/batches',files=files,data={'label':'Excel100 complex suite','expected_files':'100','use_llm':'true','auto_publish':'true','upload_key':'excel100-v1'})
            state['batch']=b['id']
        else: b=req('POST',f"/api/batches/{state['batch']}/files",files=files,data={'use_llm':'true'})
        state['uploaded'] += [s['id'] for s in chunk];save('state.json',state)
        print('Uploaded',len(state['uploaded']),flush=True)
    bid=state['batch']
    b=req('GET',f'/api/batches/{bid}')
    for s in specs:
        item=next(i for i in b['items'] if i['file_name']==s['id']+'.xlsx')
        if item['state']=='needs_customer':
            state.setdefault('customer_confirmations',{})[s['id']]=item.get('match')
            save('state.json',state)
            req('POST',f'/api/batches/{bid}/assign',json={'item_ids':[item['id']],'customer_id':state['customers'][s['id']],'use_llm':True})
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures=[pool.submit(one,s,bid,next(i['id'] for i in b['items'] if i['file_name']==s['id']+'.xlsx')) for s in specs]
        for f in as_completed(futures): f.result()
    b=req('GET',f'/api/batches/{bid}');save('batch.json',b)
    download(bid)
    save('provider-stats.json',httpx.get('http://fake:8001/stats').json())
    jobs=[]
    for offset in range(0,1000,200):
        page=req('GET','/api/jobs',params={'limit':200,'offset':offset});jobs+=page
        if len(page)<200: break
    save('jobs.json',jobs)
    results=[read(p) for p in sorted(OUT.glob('X*/result.json'))]
    summary={'files':len(results),'flow_pass':sum(bool(r.get('flow_pass')) for r in results),
             'guard_pass':sum(bool(r.get('guard_pass')) for r in results),'ask_pass':sum(bool(r.get('ask_pass')) for r in results),
             'errors':{r['id']:r['error'] for r in results if r.get('error')},'states':dict(Counter(i['state'] for i in b['items'])),
             'jobs':dict(Counter(j['status'] for j in jobs)),'completed_at':datetime.now(timezone.utc).isoformat()}
    save('summary.json',summary);print(json.dumps({**summary,'errors':len(summary['errors'])},ensure_ascii=False),flush=True)

if __name__=='__main__': main()
