"""Re-score frozen predictions, with unordered warehouse collections and explicit missing states."""
import importlib.util
import json
import sys
from pathlib import Path
from collections import Counter

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('relationship_runner',ROOT/'tools/run.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
read=runner.harness.read;save=runner.harness.save

def summarize(run_id='rel-20261103-a'):
    folder=ROOT/'runs'/run_id;out=[]
    manifest_name=read(folder/'metadata.json').get('manifest_file','manifest.json')
    for item in read(ROOT/manifest_name)['rounds']:
        dest=folder/item['id'];s=read(dest/'state.json') if (dest/'state.json').exists() else {}
        row={'case':item['case'],'title':item['title'],'round_id':s.get('round_id'),'state_complete':s.get('complete',False),
             'mode':read(ROOT/item['review'])['mode'],'failed_stage':s.get('failed_stage'),'elapsed_seconds':s.get('elapsed_active_seconds')}
        if (dest/'before.json').exists():
            b=read(dest/'before.json');m=b.get('mapping') or {};p=m.get('policy') or {};q=b.get('questions') or {};log=m.get('agent_log') or []
            score=runner.evaluate(b,item);save(dest/'score-reviewed.json',score)
            usage=[v.get('usage') or {} for v in log]
            row.update({'score':score,'mapping_id':m.get('id'),'decision':p.get('decision'),'verification':p.get('verification'),
              'self_check':p.get('self_check'),'agreement':p.get('agreement'),
              'disagree_count':len((p.get('agreement') or {}).get('disagree',{})),
              'questions':[{'id':v.get('id'),'kind':v.get('kind'),'action':v.get('action'),'title':v.get('title'),'question':v.get('question'),
                           'note':v.get('note'),'options':v.get('options'),'evidence':v.get('evidence'),'contested':v.get('contested')} for v in q.get('questions',[])],
              'open_questions':q.get('open_questions',[]),'reasons':q.get('reasons',[]),'preview_ok':b['preview'].get('ok'),
              'agent_events':[v.get('agent') for v in log],'cost_usd':sum(u.get('cost_usd',0) for u in usage),
              'calls':sum(u.get('calls',0) for u in usage),'input_tokens':sum(u.get('input_tokens',0) for u in usage),'output_tokens':sum(u.get('output_tokens',0) for u in usage)})
            flags=[]
            if score['rows_got']!=12:flags.append('rows != 12')
            if score['extra_rows']:flags.append('extra rows')
            if score['extra_values']:flags.append('extra facts or collection members')
            if len(row['questions'])>8:flags.append('questions > 8')
            if row['disagree_count']>=8:flags.append('disagree >= 8')
            if (p.get('verification') or {}).get('passed') is False:flags.append('verification false')
            failures=[{'agent':v.get('agent'),'error':v.get('error'),'notes':v.get('notes'),'fallback':v.get('fallback')} for v in log if v.get('agent')=='llm_failed' or v.get('fallback')=='heuristic' or any('llm_failed' in str(n) for n in v.get('notes',[]))]
            if failures:flags.append('llm_failed')
            row['llm_failures']=failures
            row['flags']=flags
        for stage in ['profile','propose']:
            jp=dest/f'{stage}-job.json'
            if jp.exists():
                job=read(jp)
                row[stage+'_job']={k:job.get(k) for k in ['status','error','duration_ms','started_at','finished_at']}
                if job.get('status')=='failed':row.setdefault('flags',[]).append(stage+' job failed')
        if row.get('propose_job',{}).get('finished_at') and s.get('started_at'):
            from datetime import datetime
            row['elapsed_seconds']=round((datetime.fromisoformat(row['propose_job']['finished_at'])-datetime.fromisoformat(s['started_at'])).total_seconds(),2)
            row['time_basis']='wall clock from case start to propose completion, including upload/profile/queue; independent of client resume'
        out.append(row)
    save(folder/'summary.json',out)
    for r in out:
        sc=r.get('score',{})
        print(r['case'],r.get('decision'),sc.get('field_accuracy'),sc.get('exact_record_accuracy'),'cards',len(r.get('questions',[])),'open',len(r.get('open_questions',[])),'failed',r.get('failed_stage'))
    return out

if __name__=='__main__':summarize(sys.argv[1] if len(sys.argv)>1 else 'rel-20261103-b')
