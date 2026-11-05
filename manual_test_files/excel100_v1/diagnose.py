"""Separate diagnosis of UI preview failures, WITHOUT fixing application code.

After full-source verification, call the existing API to determine whether the
failure is in bounded preview or actual answer application/export. These runs
MUST NOT be counted as normal UI flow passes.
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor
import httpx
import run as h

state=h.read(h.OUT/'state.json');bid=state['batch']
specs={s['id']:s for s in h.read(h.ROOT/'manifest.json')['files']}

def one(result):
    cid=result['id'];dest=h.OUT/'diagnostics'/f'{cid}.json'
    if dest.exists(): return h.read(dest)
    record={'id':cid,'normal_ui_flow_failed':True,'diagnostic_only':True,'reason':result.get('error')}
    try:
        item=h.wait_item(bid,result['item_id'])
        before=h.snapshot(item,cid,'diagnostic-before')
        oracle=h.read(h.ROOT/'oracle'/f'{cid}.json')
        proof=h.score(before['preview'].get('records',[]),oracle['rows'],ignore_unit=specs[cid]['family']==8)
        record['full_source_precheck']=proof
        assert proof['exact'], 'Full-source data is wrong; diagnostic approval withheld'
        m=before['mapping'];answers={}
        for q in before['questions']['questions']:
            names=h.column_names(q.get('current',{}))
            if specs[cid]['family']==8 and q['kind']=='text' and q.get('source_value')=='ZQ':answers[q['id']]='BOTTLE'
            elif q['kind']=='confirm':answers[q['id']]='confirm'
            elif q['kind']=='choice' and len(set(names))==1 and names[0] in q['options']:answers[q['id']]=names[0]
            else:raise RuntimeError('Unexpected unresolved question: '+str(q))
        p=h.req('POST',f"/api/mappings/{m['id']}/answers/preview",json={'answers':answers})
        record['sample_preview_ok']=p['preview']['ok']
        record['full_validation']=p['comparison']['validation']
        assert p['comparison']['validation']['passed'], 'Full validation failed; approval withheld'
        a=h.req('POST',f"/api/mappings/{m['id']}/answers/apply",json={'answers':answers,'digest':p['digest']})
        record['applied_mapping']=a['id']
        after=h.snapshot(item,cid,'diagnostic-after')
        record['full_source_score']=h.score(after['preview'].get('records',[]),oracle['rows'])
        assert record['full_source_score']['exact'], 'Changed data is wrong; approval withheld'
        h.req('POST',f"/api/mappings/{a['id']}/approve",json={})
        item=h.wait_item(bid,result['item_id'])
        if item['state']=='needs_data_check':
            r=h.req('GET',f"/api/rounds/{item['round_id']}")
            d=max(r['datasets'],key=lambda x:x.get('version',0))
            h.req('POST',f"/api/datasets/{d['id']}/approve",json={'acknowledge_anomalies':True})
            item=h.wait_item(bid,result['item_id'])
        record['final_state']=item['state']; record['backend_pass']=item['state']=='ready'
    except Exception as e:record['error']=str(e)
    h.save(f'diagnostics/{cid}.json',record)
    print(cid,record.get('backend_pass'),record.get('error',''),flush=True)
    return record

if __name__=='__main__':
    ai=h.req('GET','/api/settings/ai');assert ai['model']=='fake-excel100' and ai['base_url']=='http://fake:8001/v1'
    results=[h.read(p) for p in h.OUT.glob('X*/result.json')]
    targets=[r for r in results if r.get('error')=='Answer preview invalid']
    with ThreadPoolExecutor(max_workers=4) as pool:diagnostics=list(pool.map(one,targets))
    # Preserve the normal flow download separately from diagnostic downloads.
    for name in ['download.zip','download.json','download-manifest.json']:
        source=h.OUT/name;destination=h.OUT/('normal-'+name)
        if source.exists() and not destination.exists():destination.write_bytes(source.read_bytes())
    h.download(bid)
    h.save('final-batch.json',h.req('GET',f'/api/batches/{bid}'))
    jobs=[]
    for offset in range(0,1000,200):
        page=h.req('GET','/api/jobs',params={'limit':200,'offset':offset});jobs+=page
        if len(page)<200:break
    h.save('final-jobs.json',jobs)
    h.save('provider-stats-final.json',httpx.get('http://fake:8001/stats').json())
    h.save('diagnostics-summary.json',{'count':len(diagnostics),'passed':sum(bool(d.get('backend_pass')) for d in diagnostics),
                                    'errors':{d['id']:d['error'] for d in diagnostics if d.get('error')}})
