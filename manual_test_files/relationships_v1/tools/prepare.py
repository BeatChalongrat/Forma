"""Deterministic fixture specification and sealed evaluator keys. No production imports."""
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
rng = random.Random(312026)
specs, manifest = [], []

def save(path, data):
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def shuffled(rows):
    out = list(rows); rng.shuffle(out); return out

def field(typ, description, semantic=None):
    v = {'type': typ, 'description': description}
    if semantic: v['x-forma'] = {'semantic': semantic}
    return v

base = {
    'sku': field('string', 'Unique SKU identifier; preserve leading zeros.', 'identity.sku'),
    'product_name': field('string', 'Product name from the master list.', 'product.name'),
}
defs = {
    'price': field('number', 'Requested selling price, not cost.', 'price.amount'),
    'currency': field('string', 'ISO 4217 code supported by the source.', 'currency'),
    'qty': field(['number','null'], 'Stock in individual pieces. Missing evidence is null, never zero.', 'stock.qty'),
    'sell_unit': field('string', 'Full selling unit name. Codes require source evidence or human confirmation.', 'unit.sell'),
    'inventory': {'type':'array','description':'One entry per warehouse. Aggregate lots within each warehouse; never sum warehouses together.',
        'items':{'type':'object','additionalProperties':False,'required':['warehouse_code','qty'], 'properties':{
            'warehouse_code':field('string','Warehouse identifier.','stock.warehouse'),
            'qty':field('number','Stock in individual pieces, summed across lots in this warehouse.','stock.qty')}}},
}

def sheet(name, headers, rows, **kw):
    return {'name':name,'rows':[headers]+rows,**kw}

def add(cid, title, fields, books, expected, task, questions=None, mode='map', fault=None):
    schema={'$schema':'https://json-schema.org/draft/2020-12/schema','type':'object','additionalProperties':False,
       'title':title,'required':['sku','product_name']+fields, 'properties':{**base,**{f:defs[f] for f in fields}},
       'x-forma':{'identity_keys':['/sku']}}
    if 'inventory' in fields:
        schema['x-forma']['collections']={'/inventory':{'key_fields':['/warehouse_code']}}
    save(f'schemas/{cid}.json',schema)
    save(f'oracle/{cid}/expected.json',expected)
    save(f'oracle/{cid}/review.json',{'mode':mode,'questions':questions or [],'fault':fault})
    d=ROOT/'inputs'/cid/'r1';d.mkdir(parents=True,exist_ok=True)
    task='Map the supplied ERP export(s) to the supplied JSON Schema. Preserve the master product population and identities. Do not invent business facts. Ask a specific question if required facts cannot be established.\n'+task
    (d/'task.md').write_text(task,encoding='utf-8')
    item={'id':cid+'_r1','case':cid,'round':'r1','title':title,'schema':f'schemas/{cid}.json','task':f'inputs/{cid}/r1/task.md','expected':f'oracle/{cid}/expected.json','review':f'oracle/{cid}/review.json','files':[]}
    for name,sheets in books.items():
        path=f'inputs/{cid}/r1/{name}.xlsx';item['files'].append({'path':path})
        specs.append({'path':path,'sheets':sheets,'fault':fault})
    manifest.append(item)

products=[{'sku':f'000{i:03d}','product_name':f'{["Tea","Coffee","Shampoo","Soap"][i%4]} variant {i:02d}'} for i in range(1,13)]
master=sheet('Material',['Material code','Description'],shuffled([[p['sku'],p['product_name']] for p in products]))
stocks=[[p['sku'],w,i*7+k] for i,p in enumerate(products,1) for k,w in enumerate(['BKK','CNX'])]
prices=[[p['sku'],100+i*13,'THB'] for i,p in enumerate(products,1)]
expect=[{**p,'price':100+i*13,'currency':'THB','inventory':[{'warehouse_code':w,'qty':i*7+k} for k,w in enumerate(['BKK','CNX'])]} for i,p in enumerate(products,1)]
add('R01_split','Three files, shuffled joins',['price','currency','inventory'],{
 'materials':[master], 'balances':[sheet('Stock',['Material code','Warehouse','Quantity pieces'],shuffled(stocks))],
 'selling_prices':[sheet('Price',['Material code','Selling price','Currency'],shuffled(prices))]},expect,
 'All three files belong to this delivery. Use the selling price as supplied and preserve both warehouses.')

compound=[];balances=[];expected=[]
for i in range(1,7):
 for org in ['TH','MY']:
    p={'sku':f'{org}-{i:04d}','product_name':f'{org} item {i}'};q=i*11+(100 if org=='MY' else 0)
    compound.append([p['sku'],org,f'{i:04d}',p['product_name']]);balances.append([org,f'{i:04d}',q]);expected.append({**p,'qty':q})
add('R02_composite','Company + item composite key',['qty'],{'delivery':[
 sheet('Master',['SKU','Company','Item code','Description'],shuffled(compound)),
 sheet('Stock',['Company','Item code','Quantity pieces'],shuffled(balances))]},expected,
 'Item codes are unique only within a company. The SKU in Master is the output identity. Never match items across companies.')

history=[]
for i,p in enumerate(products,1):
 for kind,cur,start,end,amount in [('RETAIL','THB','2026-01-01','2026-10-31',i+10),('RETAIL','THB','2026-11-01','2026-11-30',i*13+100),('WHOLESALE','THB','2026-11-01','2026-11-30',i+20),('RETAIL','USD','2026-11-01','2026-11-30',i+1)]:
    history.append([p['sku'],kind,cur,start,end,amount])
add('R03_validity','Date + currency + price list',['price','currency'],{'delivery':[master,sheet('Price history',['Material code','Price list','Currency','Valid from','Valid to','Selling price'],shuffled(history))]},
 [{**p,'price':100+i*13,'currency':'THB'} for i,p in enumerate(products,1)],
 'Return RETAIL THB prices valid on 2026-11-03 (inclusive validity bounds). Ignore other price lists, currencies and expired periods.')

report=[['Inventory and price list',None,None,None],['Currency: EUR; price per individual piece; stock below is in pieces.',None,None,None],
 [None,None,None,None],['Identity',None,'Commercial',None],['SKU','Product name','Selling price','Stock pieces']]
report += [[p['sku'],p['product_name'],i*17+50,i*3] for i,p in enumerate(products,1)]
report += [['TOTAL',None,sum(i*17+50 for i in range(1,13)),sum(i*3 for i in range(1,13))]]
add('R04_layout','Merged headings and currency outside table',['price','currency','qty'],{'report':[{'name':'Report','rows':report,'merges':['A1:D1','A2:D2','A4:B4','C4:D4'],'headerRow':5}]},
 [{**p,'price':i*17+50,'currency':'EUR','qty':i*3} for i,p in enumerate(products,1)],
 'The workbook contains the applicable currency and quantity basis. Exclude the TOTAL row; it is not a product.')

for cid,fault,title,mode in [('R05_formula',None,'Cross-sheet formula with correct cache','map'),('R06_stale','stale','Formula result cache contradicts inputs','detect'),('R07_uncached','uncached','Formula has no stored result','detect'),('R08_external','external','Missing external formula workbook','detect')]:
    rows=[[p['sku'],p['product_name'],None] for p in products]
    forms={f'C{i+1}':f"='Counts'!B{i+1}*'Pack factors'!B{i+1}" for i in range(1,13)}
    books={'delivery':[sheet('Export',['SKU','Product name','Stock pieces'],rows,formulas=forms),
      sheet('Counts',['SKU','Cartons'],[[p['sku'],i+2] for i,p in enumerate(products,1)]),
      sheet('Pack factors',['SKU','Pieces per carton'],[[p['sku'],[6,12,24][i%3]] for i,p in enumerate(products,1)])]}
    expected=[{**p,'qty':(i+2)*[6,12,24][i%3]} for i,p in enumerate(products,1)]
    if fault=='external': expected=[{**p,'qty':None} for p in products]
    questions=[] if not fault else [{'topic':fault,'acceptable':'Explain the specific formula/source issue and request recalculation or missing source, or safely recompute from complete internal operands with traceable evidence.','must_not':'Silently trust stale caches, substitute zero, or fabricate external quantities.'}]
    add(cid,title,['qty'],books,expected,'Use Export as the product list. Quantities must be supported by the workbook dependencies. If a referenced input is unavailable, do not infer its value.',questions,mode,fault)

add('R09_unit','Undefined unit in separate reference sheet',['sell_unit'],{'delivery':[master,
 sheet('Sales units',['Material code','Unit code'],shuffled([[p['sku'],'BT'] for p in products]))]},
 [{**p,'sell_unit':'BOTTLE'} for p in products],
 'Return full selling-unit names, not unexplained abbreviations. The meaning of BT has not been specified.',
 [{'topic':'unit_BT','answer':'BOTTLE','acceptable':'Ask what BT means before asserting a full unit name.'}], 'ask')

conflict=[[p['sku'],i*13+100,'THB'] for i,p in enumerate(products,1)]+[[products[3]['sku'],9999,'THB']]
add('R10_conflict','Duplicate price key without precedence',['price','currency'],{'delivery':[master,sheet('Prices',['Material code','Selling price','Currency'],shuffled(conflict))]},
 [{**p,'price':i*13+100,'currency':'THB'} for i,p in enumerate(products,1)],
 'These are current RETAIL prices for one delivery. No precedence rule exists for conflicting records.',
 [{'topic':'duplicate_price_000004','answer':152,'acceptable':'Identify the two conflicting prices 152 and 9999 for SKU 000004 and ask which is correct; no first/last-row guess.'}], 'ask')

add('R11_orphan','Unmatched stock keys and missing stock',['qty'],{'delivery':[master,sheet('Stock',['Material code','Stock pieces'],shuffled([[p['sku'],i*5] for i,p in enumerate(products,1) if i!=7]+[['999999',888]]))]},
 [{**p,'qty':None if i==7 else i*5} for i,p in enumerate(products,1)],
 'Master is the authoritative product population. A missing balance is unknown, not zero. Report unmatched source keys for review.',
 [{'topic':'unmatched_000007_999999','acceptable':'Flag missing stock for 000007 and stock for unknown 999999. Preserve all master products; do not create a new product or invent zero.'}], 'detect')

lots=[];packs=[];exp=[]
for i,p in enumerate(products,1):
    factor=[6,12,24][i%3];packs.append([p['sku'],factor]);inv=[]
    for j,w in enumerate(['BKK','CNX']):
        lots += [[p['sku'],w,'L1',i+j,'CASE'],[p['sku'],w,'L2',2,'CASE'],[p['sku'],w,'L3',3+j,'PCS']]
        inv.append({'warehouse_code':w,'qty':(i+j+2)*factor+3+j})
    exp.append({**p,'price':i*13+100,'currency':'THB','inventory':inv})
add('R12_combined','Lots + units + validity + multiple sheets',['price','currency','inventory'],{'delivery':[
 master,sheet('Batches',['Material code','Warehouse','Lot','Stock quantity','Unit'],shuffled(lots)),
 sheet('Pack sizes',['Material code','Pieces per CASE'],shuffled(packs)),
 sheet('Price history',['Material code','Price list','Currency','Valid from','Valid to','Selling price'],shuffled(history))]},exp,
 'Return RETAIL THB price valid on 2026-11-03. Inventory is in individual pieces, one entry per warehouse, summed across lots. CASE conversion differs by product in Pack sizes; PCS already means individual pieces. Do not multiply PCS balances.')

save('specifications.json',specs)
save('manifest.json',{'suite':'relationships_v1','seed':312026,'synthetic':True,'real_customer_export':False,'rounds':manifest})
for item in manifest:
    r=json.loads((ROOT/item['review']).read_text(encoding='utf-8'))
    (ROOT/'oracle'/item['case']/'ผลที่ควรได้.md').write_text('# '+item['title']+'\n\n'+json.dumps(r,ensure_ascii=False,indent=2)+'\n\nตรวจเทียบ expected.json ซึ่งไม่ส่งให้ mapper\n',encoding='utf-8')
print('Prepared',len(manifest),'cases;',len(specs),'workbooks')
