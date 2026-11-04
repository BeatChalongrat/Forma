"""Apply a withheld human fact only after the real model asked for it. No approval/export."""
import importlib.util
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('runner',ROOT/'tools/run.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
read=r.harness.read;save=r.harness.save;request=r.harness.request
dest=ROOT/'runs/rel-20261103-b/T09_r1'
before=read(dest/'before.json')
q=next(q for q in before['questions']['questions'] if q.get('action')=='value_map' and q.get('source_value')=='BT')
answer=read(ROOT/'oracle/R09_unit/review.json')['questions'][0]['answer']
payload={'answers':{q['id']:answer}}
with httpx.Client(base_url='http://127.0.0.1/api',timeout=180) as c:
    if not (dest/'human-applied.json').exists():
        preview=request(c,'POST',f"/mappings/{before['mapping']['id']}/answers/preview",json=payload)
        save(dest/'human-payload.json',payload);save(dest/'human-preview.json',preview)
        assert preview['preview']['ok'] and preview['comparison']['validation']['passed']
        after=request(c,'POST',f"/mappings/{before['mapping']['id']}/answers/apply",json={**payload,'digest':preview['digest']})
        save(dest/'human-applied.json',after)
    after=r.snapshot(c,before['round']['id'])
    save(dest/'after-human.json',after)
    item=next(i for i in read(ROOT/'manifest-neutral.json')['rounds'] if i['case']=='T09')
    scored=r.evaluate(after,item);scored['phase']='After operator confirmed BT = BOTTLE; no approval/export'
    save(dest/'score-after-human.json',scored)
    print('accuracy',scored['field_accuracy'],'exact',scored['exact_record_accuracy'],'rows',scored['rows_got'])
    print('remaining',after['questions']['open_questions'],'cards',len(after['questions']['questions']),'decision',after['mapping']['policy']['decision'])
