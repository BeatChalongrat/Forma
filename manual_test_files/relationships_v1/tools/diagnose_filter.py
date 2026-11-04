"""Read-only replay of the R03 predicates against the frozen source; no mapping edits."""
import json
from pathlib import Path
from forma.mapping_dsl.models import SourceSpec
from forma.engine.transform import BoundSource,load_source_frame,_prefixed
from forma.engine.ops import CompileContext,compile_expr

root=Path(__file__).resolve().parents[1]
dest=root/'runs/rel-20261103-a/R03_validity_r1'
b=json.loads((dest/'before.json').read_text(encoding='utf-8'))
spec=SourceSpec.model_validate(b['mapping']['document']['sources'][1])
expr=spec.table.filter
plain=spec.model_copy(deep=True);plain.table.filter=None
frame=load_source_frame(plain,BoundSource(root/'inputs/R03_validity/r1/delivery.xlsx',sheet='Price history'))
pf=_prefixed(frame,spec.id);ctx=CompileContext(default_source=spec.id,columns=set(pf.columns))
out={'rows_unfiltered':frame.height,'columns':frame.columns,'clauses':[]}
for index,clause in enumerate([expr.args[0].args[0],expr.args[0].args[1],expr.args[1].args[0],expr.args[1].args[1]]):
    values=pf.select(compile_expr(clause,ctx).alias('ok')).to_series()
    out['clauses'].append({'index':index,'expression':clause.model_dump(mode='json'),'true':values.sum(),'null':values.null_count()})
out['missing_columns']=ctx.missing
out['diagnosis']='Date comparison uses numeric conversion in GTE/LTE; both date predicates are NULL for all 48 rows. No production change made.'
(dest/'filter-diagnostic.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in out.items() if k!='clauses'},ensure_ascii=False))
