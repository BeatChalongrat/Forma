from pathlib import Path
from decimal import Decimal
from datetime import datetime
import csv, json, hashlib, sys
import openpyxl
import re
sys.stdout.reconfigure(encoding='utf-8')
BASE=Path(r'C:\Users\Beat Beat\Documents\Codex\2026-10-29\new-chat\forma-blind-evaluation')
INDEX=json.loads((BASE/'index.json').read_text(encoding='utf-8-sig'))

def table(path,sheet=None):
 if path.suffix=='.xlsx':
  wb=openpyxl.load_workbook(path,data_only=False)
  ws=wb[sheet] if sheet else wb.worksheets[0]
  rows=list(ws.values); hdr=rows[0]
  return [(n,dict(zip(hdr,r))) for n,r in enumerate(rows[1:],2) if any(v is not None for v in r)]
 raw=path.read_bytes(); enc='utf-16' if raw[:2] in [b'\xff\xfe',b'\xfe\xff'] else 'utf-8-sig'
 txt=raw.decode(enc); sep='\t' if path.suffix=='.tsv' else (';' if ';' in txt.splitlines()[0] else ',')
 # Blank lines are not records; preserve physical source line numbers as evidence.
 lines=txt.splitlines(); hdr=next(csv.reader([lines[0]],delimiter=sep))
 return [(n,dict(zip(hdr,next(csv.reader([line],delimiter=sep))))) for n,line in enumerate(lines[1:],2) if line.strip()]

def number(v,style='dot'):
 s=str(v).replace('€','').replace('$','')
 s=''.join(s.split())
 if style=='comma': s=s.replace('.','').replace(',','.')
 elif style=='us': s=s.replace(',','')
 return float(Decimal(s))

def fraction(v): return float(Decimal(str(v).strip().removesuffix('%'))/100)

def flag(v):
 mapping={'No':False,'Yes':True,'Tidak':False,'Non':False,'Nein':False,'いいえ':False,'아니오':False}
 return mapping[v]

def date(v,fmt): return datetime.strptime(v,fmt).date().isoformat()

# Explicit field interpretations made by this evaluator from headers and task evidence.
FIELDS={
 'en':['SKU','Product name','Currency','Sales unit','Price list','Net price','Tax included','Tax rate'],
 'B02':['Kode barang','Nama produk','Mata uang','Satuan','Daftar harga','Harga netto','Termasuk pajak','Tarif pajak'],
 'B06':['Référence','Désignation','Devise','Unité','Liste de prix','Prix HT (EUR)','Taxe incluse','Taux taxe'],
 'B07':['Artikelnummer','Bezeichnung','Währung','Einheit','Preisliste','Nettopreis (de-DE)','MwSt enthalten','Steuersatz'],
 'B09':['商品コード','商品名','通貨','単位','価格リスト','税抜価格','税込','税率'],
 'B10':['상품코드','상품명','통화','단위','가격표','세전 가격','세금 포함','세율']}
TARGETS=['sku','product_name','currency','unit.sell_unit','prices[].price_list','prices[].amount','prices[].vat_included','prices[].vat_rate']
summary=[]
for task in INDEX:
 tid=task['id']; group=task['source_group']; p=BASE/tid
 schema=json.loads((p/task['schema']).read_text(encoding='utf-8-sig'))
 instructions=(p/task['task']).read_text(encoding='utf-8-sig')
 records=[]; questions=[]; evidence=[]; limitations=[]
 def ev(source,meaning,**kw): evidence.append(dict(source=source,interpretation=meaning,**kw))
 def question(qid,question,field,scope): questions.append(dict(id=qid,question=question,field=field,scope=scope))
 for filename in task['files']:
  ev(filename,'Input file read directly; no reference answer accessed.',sha256=hashlib.sha256((p/filename).read_bytes()).hexdigest())
 if group=='B01':
  materials=table(p/'material.xlsx','Material'); prices=table(p/'prices.xlsx','Prices'); inventory=table(p/'inventory.xlsx','Stock')
  byprice={r['MATNR']:(n,r) for n,r in prices}
  for n,r in materials:
   pn,pr=byprice[r['MATNR']]
   stock=[(sn,s) for sn,s in inventory if s['MATNR']==r['MATNR']]
   records.append({'sku':r['MATNR'],'product_name':r['MAKTX'],'barcode':r['EAN11'],'unit':{'sell_unit':'CARTON','pack_size':int(r['Units per carton'])},'currency':pr['WAERS'],'prices':[{'price_list':pr['Price list'],'amount':number(pr['Net amount']),'vat_included':flag(pr['VAT included']),'vat_rate':fraction(pr['Tax rate'])}],'inventory':[{'warehouse_code':s['Warehouse'],'qty':number(s['Available pieces'])} for sn,s in stock]})
   ev('material.xlsx/Material', 'Joined product, price, and stock using exact text MATNR; retained text barcode.',sku=r['MATNR'],row=n,price_row=pn,inventory_rows=[sn for sn,s in stock])
  ev('task.md and material.xlsx/Legend','CTN means CARTON. Units per carton maps pack_size. Available pieces is already pieces; no stock multiplication.')
 elif group=='B05_alt':
  for n,r in table(p/task['files'][0]):
   records.append({'item_code':r['SKU'],'description':r['Product name'],'money':{'iso_currency':r['Currency'],'net_amount':number(r['Net price']),'tax_fraction':fraction(r['Tax rate'])},'valid_from':date(r['Valid from (DD/MM/YYYY)'],'%d/%m/%Y')})
   ev(task['files'][0],'Mapped supplied row directly into commercial offer contract; no tax adjustment or currency conversion.',row=n,item_code=r['SKU'])
  ev('task.md and schema.json','Commercial offer contract takes precedence over generic product instructions. Price list and sales unit have no destination in this contract.')
 else:
  rows=table(p/task['files'][0]); cols=FIELDS.get(group,FIELDS['en']).copy()
  if group=='B12': cols[5]='Amount as supplied'
  ev(task['files'][0],'Header interpretation.',mapping=dict(zip(cols,TARGETS)))
  bysku={}; row_by_sku={}
  for n,r in rows:
   if group=='B11' and r['Record state']!='active':
    ev(task['files'][0],'Excluded expired price per task instruction.',row=n,sku=r['SKU'],record_state=r['Record state']); continue
   sku=str(r[cols[0]]); name=r[cols[1]]
   style='comma' if group in ['B02','B06','B07'] else 'dot'
   if tid=='B02_r3': style='us'
   price={'price_list':r[cols[4]],'amount':number(r[cols[5]],style),'vat_included':flag(r[cols[6]]),'vat_rate':fraction(r[cols[7]])}
   if group=='B05': price['effective_from']=date(r['Valid from (DD/MM/YYYY)'],'%d/%m/%Y')
   if group=='B07': price['effective_from']=date(r['Gültig ab (TT.MM.JJJJ)'],'%d.%m.%Y')
   if group=='B08': price['effective_from']=date(r['Effective from (MM/DD/YYYY)'],'%m/%d/%Y')
   if group=='B11': price['effective_from']=date(r['Valid from'],'%Y-%m-%d')
   rec={'sku':sku,'product_name':name,'prices':[price]}
   if cols[2] in r: rec['currency']=r[cols[2]]
   if group!='B15': rec['unit']={'sell_unit':r[cols[3]]}
   if group=='B04': rec['attributes']={'color':str(r['Color']),'size':str(r['EU size']),'parent_style':str(r['Parent style'])}
   if group=='B10': rec['barcode']=str(r['바코드'])
   if group=='B15': rec['attributes']={'source_sales_unit':r['Sales unit']}
   if group=='B13': rec['attributes']={'source_effective_date':r['Effective date']}
   if group=='B03':
    multiplier=12 if tid=='B03_r3' else 1
    rec['inventory']=[{'warehouse_code':r['Location'],'qty':number(r['Available'])*multiplier}]
   if sku not in bysku:
    bysku[sku]=rec; row_by_sku[sku]=[n]
   else:
    prior=bysku[sku]; row_by_sku[sku].append(n)
    if group=='B03':
     assert prior['product_name']==rec['product_name'] and prior['prices']==rec['prices'] and prior['currency']==rec['currency'] and prior['unit']==rec['unit']
     prior['inventory']+=rec['inventory']
    elif group=='B16':
     ev(task['files'][0],'Conflicting prices share SKU and price-list identity, with no effective date or precedence evidence. Omitted this SKU prices until clarified.',sku=sku,source_rows=row_by_sku[sku],candidate_prices=prior['prices']+rec['prices'])
     prior.pop('prices')
    else: raise ValueError('Unexpected duplicate '+tid+' '+sku)
   ev(task['files'][0],'Source record mapped using explicit fields; numeric amount retained without tax or FX adjustment.',row=n,sku=sku)
  records=list(bysku.values())
  if group=='B02': ev('task.md and produk.csv','Harga netto uses '+('comma grouping and dot decimal in this round (explicit change in task).' if tid=='B02_r3' else 'dot grouping and comma decimal, supported by values such as '+str(rows[0][1]['Harga netto'])+'.')+' Tidak means no tax included. Tax percent divided by 100.')
  if group=='B03': ev('task.md and inventory_export.csv','Merged per SKU; each Location becomes its own inventory entry; repeated identical prices deduplicated. '+('Available cartons multiplied by 12 to obtain pieces; sales unit remains explicit PCS.' if tid=='B03_r3' else 'Available quantity preserved as supplied; no conversion instruction for this round.'))
  if group=='B04': ev('task.md','Variant SKU is identity. Color and EU size preserved as strings; parent style retained in attributes.parent_style.')
  if group=='B06': ev('task.md and tarif.xlsx/Tarif','French thousands spaces including U+202F removed; decimal comma normalized; optional euro symbol removed. Prix HT and Non mean tax excluded.')
  if group=='B07': ev('task.md and artikel.csv','de-DE periods are thousands separators, so 1.234 becomes 1234. Nein means false. TT.MM.JJJJ parsed as day.month.year.')
  if group=='B08': ev('prices.csv header','MM/DD/YYYY is explicit; 03/04/2026 maps to 2026-03-04.')
  if group=='B09': ev('商品.tsv','UTF-16 BOM used to decode tab-delimited text. Japanese 商品コード/name/currency/unit/price-list/net-price/tax-included/tax-rate headers translated. いいえ means false.')
  if group=='B10': ev('products.xlsx/상품','Korean headers translated to product fields. 아니오 means false. Barcode and SKU preserved as source text.')
  if group=='B11': ev('task.md and history.csv','Price Record state determines inclusion only; no product status inferred. ISO Valid from retained.')
  if group=='B12': ev('task.md and tax_flags.csv','Each amount kept exactly as supplied with its row-specific Yes/No inclusion flag and percentage rate, including 0%.')
  if group=='B13':
   question('B13-date-order','For Effective date = 03/04/2026, is the format DD/MM/YYYY (3 April 2026) or MM/DD/YYYY (March 4, 2026)? Does that format apply to all rows?','prices[].effective_from',{'file':'export.csv','rows':'all 12 products'})
   limitations.append('Effective date omitted from prices until day/month order is confirmed; raw text retained in attributes.source_effective_date.')
  if group=='B14':
   question('B14-dollar-currency','Which three-letter ISO currency does the $ symbol in Net price mean, and does it apply to every row (for example USD, SGD, AUD, or another currency)?','currency',{'file':'export.csv','rows':'all 12 products'})
   limitations.append('currency omitted because $ is ambiguous. Numeric amounts are preserved; schema default THB is not evidence.')
  if group=='B15':
   question('B15-unit-BT','What does Sales unit code BT mean in this export, and what target sell_unit should represent it?','unit.sell_unit',{'file':'export.csv','rows':'all 12 products','source_value':'BT'})
   limitations.append('unit.sell_unit omitted pending BT meaning; raw code retained in attributes.source_sales_unit. No pack size inferred.')
  if group=='B16':
   question('B16-price-conflict','For SKU 00470001 and price list standard, source rows give 1234.56 and 9999 with no dates or priority. Which price is authoritative, or do these represent distinct price periods or tiers requiring additional identifying information?','prices[].amount',{'file':'export.csv','sku':'00470001','price_list':'standard','source_rows':row_by_sku['00470001']})
   limitations.append('prices omitted for SKU 00470001 because amount is required by each price entry and cannot be chosen without evidence. Candidate prices retained in evidence.')
 def validate(value, spec, path=''):
  errs=[]; typ=spec.get('type'); types=typ if isinstance(typ,list) else [typ]
  def istype(t):
   return {'object':isinstance(value,dict),'array':isinstance(value,list),'string':isinstance(value,str),'number':isinstance(value,(int,float)) and not isinstance(value,bool),'integer':isinstance(value,int) and not isinstance(value,bool),'boolean':isinstance(value,bool),'null':value is None}.get(t,True)
  if typ and not any(istype(t) for t in types): return [path+': invalid type']
  if isinstance(value,dict):
   for key in spec.get('required',[]):
    if key not in value: errs.append(path+': missing '+key)
   for key,val in value.items():
    subspec=spec.get('properties',{}).get(key,spec.get('additionalProperties',True))
    if subspec is False: errs.append(path+': unexpected '+key)
    elif isinstance(subspec,dict): errs+=validate(val,subspec,path+'/'+key)
  if isinstance(value,list):
   for j,val in enumerate(value): errs+=validate(val,spec.get('items',{}),path+'/'+str(j))
  if isinstance(value,str):
   if len(value)<spec.get('minLength',0): errs.append(path+': too short')
   if 'pattern' in spec and not re.search(spec['pattern'],value): errs.append(path+': pattern mismatch')
   if spec.get('format')=='date':
    try: datetime.strptime(value,'%Y-%m-%d')
    except ValueError: errs.append(path+': invalid date')
  if isinstance(value,(int,float)) and not isinstance(value,bool):
   if 'minimum' in spec and value<spec['minimum']: errs.append(path+': below minimum')
   if 'maximum' in spec and value>spec['maximum']: errs.append(path+': above maximum')
  if 'enum' in spec and value not in spec['enum']: errs.append(path+': enum mismatch')
  return errs
 errors=[]
 for i,record in enumerate(records): errors += validate(record,schema,'/records/'+str(i))
 assert not errors,(tid,errors)
 out={'records':records,'questions':questions,'evidence':evidence,'limitations':limitations}
 (p/'prediction-before.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 summary.append({'id':tid,'status':'awaiting_clarification' if questions else 'mapped','record_count':len(records),'questions':questions,'prediction_file':tid+'/prediction-before.json','schema_validation_errors':errors})
 print(tid, 'records',len(records),'questions',len(questions),'schema valid')
report={'evaluator':'Independent coding-agent evaluator; not a claim about literal ChatGPT or Claude product performance.','phase':'before controlled human answers','input_access':'Only index.json, task.md, schema.json, and index-listed source data under this blind directory; no expected data, oracle, project source, or reports read.','tools':['PowerShell via exec_command','Bundled Python runtime','openpyxl for read-only Excel parsing','Python csv, json, decimal, datetime, hashlib','Custom recursive checks of the supplied schemas: types, required fields, additionalProperties, enum, min/max, patterns and date format (jsonschema library unavailable)'],'assumptions':['Optional missing facts are omitted; schema defaults are not applied as business evidence.','Repeated source groups are parsed from each round input, with round-specific rules taken from task.md.','Tax percentages are fixture values, normalized to fractions without adjusting amounts.','No internet, API, connector publishing, or external business facts used.'],'tasks':summary}
(BASE/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('TOTAL',len(summary),'tasks',sum(x['record_count'] for x in summary),'records',sum(len(x['questions']) for x in summary),'questions')

