"""Reproducible synthetic ERP contracts. Author XLSX only via build.mjs.

The fake provider sees DSL contracts only, never oracle rows or this generator.
This suite tests execution/guardrails, NOT autonomous LLM intelligence.
"""
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RNG = random.Random(5112026)
specs, manifest = [], []
FAMILIES = {
    1: 'Shuffled master / prices / inventory / unit joins',
    2: 'Composite company + item keys with repeated item codes',
    3: 'Three lots per warehouse, sum without multiplying root rows',
    4: 'Wide stock matrix unpivoted into warehouse collections',
    5: 'Price history, current retail filter, mixed currencies',
    6: 'Merged report headings, repeated header, footer totals',
    7: 'Cross-sheet price formulas with cached results',
    8: 'Undefined selling unit: ask, answer, preview, apply',
    9: 'Competing price rows: must not silently choose a price',
    10: 'Mapped formula without cache: must not publish invented values',
}

def save(path, obj):
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')

def shuffled(rows):
    rows = list(rows); RNG.shuffle(rows); return rows

def col(src, name): return {'op': 'col', 'source': src, 'name': name}
def const(v): return {'op': 'const', 'value': v}
def call(op, *args, **params): return {'op': op, 'args': list(args), 'params': params}
def fm(target, expr, confidence=1):
    return {'target': target, 'expr': expr, 'confidence': confidence,
            'evidence': ['Scripted local model contract; verify against source workbook.']}
def tab(name, headers, rows, **kw): return {'name': name, 'rows': [headers] + rows, **kw}
def src(id, sheet, role='other', **table):
    return {'id': id, 'role': role, 'sheet': {'name': sheet}, 'table': {'header_row': 0, **table}}
def prop(typ, description, semantic=None):
    p = {'type': typ, 'description': description}
    if semantic: p['x-forma'] = {'semantic': semantic}
    return p

for family in range(1, 11):
    for v in range(1, 11):
        cid = f'X{family:02}{v:02}'
        n = [12, 18, 24, 36, 48][(v-1) % 5]
        region, currency, lang = [('TH', 'THB', 'ชา'), ('MY', 'MYR', 'Kopi'),
                                  ('ID', 'IDR', 'Sabun'), ('VN', 'VND', 'Trà'),
                                  ('PH', 'PHP', 'Shampoo')][(v-1) % 5]
        # Vary names/order; leading zero identifiers are always strings.
        names = {k: f'{label} {v}' for k, label in [('m', 'Item Master'), ('p', 'Price List'),
                 ('s', 'Warehouse Balances'), ('u', 'Unit Dictionary'), ('n', 'Delivery Notes')]}
        headers = {'sku': ['SKU', 'Material Code', 'รหัสสินค้า'][v % 3],
                   'desc': ['Description', 'Product Name', 'ชื่อสินค้า'][v % 3]}
        mrows, prows, srows, expected = [], [], [], []
        for i in range(1, n+1):
            company = region if family != 2 else (region if i <= n//2 else 'SG')
            item = f'{(i if family != 2 else (i-1) % (n//2)+1):06}'
            sku = item if family != 2 else company + '-' + item
            unit = 'ZQ' if family == 8 else ('EA' if i % 2 else 'BT')
            unit_full = 'BOTTLE' if family == 8 or unit == 'BT' else 'PIECE'
            title = f'{lang} {company} {i:03} / {v}'
            amount = round(31 + family * 3 + v * 2 + i * 1.25, 2)
            mrows.append([sku, company, item, title, unit])
            prows.append([company, item, amount, currency, 'RETAIL', '2026-11-01', '2026-11-30'])
            if family == 5:
                prows += [[company, item, amount+999, currency, 'RETAIL', '2026-01-01', '2026-10-31'],
                          [company, item, amount-10, currency, 'WHOLESALE', '2026-11-01', '2026-11-30'],
                          [company, item, amount+500, 'USD', 'RETAIL', '2026-11-01', '2026-11-30']]
            inv = []
            wide = [company, item]
            for w, warehouse in enumerate([region+'-01', region+'-02', region+'-03']):
                qty = i*7 + v*3 + w
                if family == 3:
                    for lot, value in [('A', qty), ('B', 2), ('C', 3+w)]:
                        srows.append([company, item, warehouse, lot, value])
                    qty += 5+w
                else:
                    srows.append([company, item, warehouse, 'A', qty])
                wide.append(qty)
                inv.append({'warehouse_code': warehouse, 'qty': qty})
            if family == 4:
                if i == 1: srows = []
                # overwritten below using expected rows to avoid mixing tall/wide rows
            expected.append({'sku': sku, 'product_name': title, 'price': amount, 'currency': currency,
                             'sell_unit': unit_full, 'inventory': inv})

        price_headers = ['Company', 'Item', 'Selling price', 'Currency', 'Price list', 'Valid from', 'Valid to']
        mrows = shuffled(mrows); prows = shuffled(prows); srows = shuffled(srows)
        if family == 9:
            row = list(prows[0]); row[2] += 4321; prows.insert(len(prows)//2, row)
        sheets = [tab(names['m'], [headers['sku'], 'Company', 'Item', headers['desc'], 'Unit'], mrows),
                  tab(names['p'], price_headers, prows),
                  tab(names['s'], ['Company', 'Item', 'Warehouse', 'Lot', 'Quantity'], srows),
                  tab(names['u'], ['Unit', 'Full unit'], [['EA','PIECE'], ['BT','BOTTLE']]),
                  tab(names['n'], ['Property', 'Value'], [['Country', region], ['Target currency', currency],
                      ['Effective date', '2026-11-05'], ['Price list', 'RETAIL'],
                      ['Stock basis', 'Individual pieces; sum lots within each warehouse'],
                      ['Source', 'Synthetic test data, not an actual customer export']])]
        if family == 8: sheets[3]['rows'] = [['Unit', 'Full unit'], ['ZQ', None]]
        sources = [src('master', names['m'], 'products'), src('prices', names['p'], 'pricing'),
                   src('stock', names['s'], 'inventory'), src('units', names['u'])]
        if family == 4:
            sheets[2]['rows'] = [['Company', 'Item', region+'-01',region+'-02',region+'-03']]
            rows = []
            for i, e in enumerate(expected, 1):
                rows.append([region, f'{i:06}'] + [x['qty'] for x in e['inventory']])
            sheets[2]['rows'] += shuffled(rows)
            sources[2]['table']['unpivot'] = {'id_columns':['Company','Item'],
                'value_columns':[region+'-01',region+'-02',region+'-03'],
                'variable_name':'Warehouse','value_name':'Quantity'}
        if family == 5:
            sources[1]['table']['filter'] = call('and',
                call('eq', col('prices','Price list'), const('RETAIL')),
                call('eq', col('prices','Currency'), const(currency)),
                call('lte', call('parse_date_th', col('prices','Valid from')), call('parse_date_th', const('2026-11-05'))),
                call('gte', call('parse_date_th', col('prices','Valid to')), call('parse_date_th', const('2026-11-05'))))
        if family == 6:
            header = sheets[0]['rows'][0]
            sheets[0]['rows'] = [[f'{region} Product export',None,None,None,None],
                ['Stock is in individual pieces',None,None,None,None], [None]*5, header] + mrows[:n//2] + [header] + mrows[n//2:] + [['TOTAL',None,None,None,None]]
            sheets[0].update(merges=['A1:E1','A2:E2'], headerRow=4)
            sources[0]['table']['header_row'] = 3
        if family in (7, 10):
            rates = 'Calculation Inputs '+str(v)
            forms = {}
            inputs = [['Company','Item','Base price','Multiplier']]
            for rownum, row in enumerate(prows, 2):
                inputs.append([row[0],row[1],row[2]/2,2])
                forms[f'C{rownum}'] = f"='{rates}'!C{rownum}*'{rates}'!D{rownum}"
                row[2] = None
            sheets[1]['formulas'] = forms
            sheets.append({'name': rates, 'rows': inputs})
        joins = [{'left':'master','right':'prices','on':[{'left':'Company','right':'Company'}, {'left':'Item','right':'Item'}],
                  'cardinality':'1:1','on_unmatched':'review'},
                 {'left':'master','right':'units','on':[{'left':'Unit','right':'Unit'}], 'cardinality':'N:1','on_unmatched':'review'}]
        fields = [fm('/sku',col('master',headers['sku'])), fm('/product_name',col('master',headers['desc'])),
                  fm('/price',call('parse_number_th',col('prices','Selling price'))), fm('/currency',col('prices','Currency')),
                  fm('/sell_unit',col('master','Unit') if family == 8 else col('units','Full unit'), .5 if family == 8 else 1)]
        collection = {'target':'/inventory','source':'stock',
            'link':[{'left':'Company','right':'Company'}, {'left':'Item','right':'Item'}],
            'fields':[fm('/inventory/*/warehouse_code',col('stock','Warehouse')), fm('/inventory/*/qty',call('parse_number_th',col('stock','Quantity')))]}
        if family == 3: collection['aggregate'] = {'group_by':['/warehouse_code'],'sum':['/qty']}
        ref = f'excel100_{cid.lower()}@1'
        doc = {'meta': {'target_schema':ref,'created_by':'ai','model':'fake-excel100','confidence':1},
               'sources':sources,'joins':joins,'record':{'root_source':'master','dedupe':{'keys':['/sku'],'keep':'error'}},
               'fields':fields,'collections':[collection], 'attributes_passthrough':False,'provenance':True}
        clarifications = []
        if family == 8:
            clarifications = [{'target':'/sell_unit','kind':'text','action':'value_map','source_value':'ZQ',
                'question':'What full selling unit does ZQ mean in this file?',
                'why':'The unit dictionary has no definition for ZQ. A human must confirm; do not guess.'}]
        schema = {'$schema':'https://json-schema.org/draft/2020-12/schema', '$id':'https://forma.local/eval/'+ref.split('@')[0],
            'type':'object','title':FAMILIES[family], 'additionalProperties':False,
            'required':['sku','product_name','price','currency','sell_unit','inventory'],
            'properties': {'sku':prop('string','Preserve identity including leading zeroes.','identity.sku'),
               'product_name':prop('string','Use the master product description.','product.name'),
               'price':prop('number','Selling price for RETAIL in target currency valid 2026-11-05.','price.amount'),
               'currency':prop('string','ISO currency from the applicable price row.','currency'),
               'sell_unit':prop('string','Full unit name; ask if the code has no definition.','unit.sell'),
               'inventory': {'type':'array','items':{'type':'object','additionalProperties':False,
                  'required':['warehouse_code','qty'],'properties':{
                    'warehouse_code':prop('string','Warehouse code.','stock.warehouse'),
                    'qty':prop('number','Individual pieces; sum lots within a warehouse.','stock.qty')}}}},
            'x-forma': {'schema_id':ref.split('@')[0], 'version':1, 'identity_keys':['/sku'],
                       'collections':{'/inventory':{'identity_keys':['/warehouse_code'],'key_fields':['/warehouse_code']}}}}
        save(f'schemas/{cid}.json',schema)
        save(f'provider/contracts/{ref}.json', {'mapping':doc,'clarifications':clarifications,'open_questions':[]})
        save(f'oracle/{cid}.json', {'rows':expected, 'expected_action':'ask' if family==8 else 'block' if family>=9 else 'map',
            'human_answers': {'/sell_unit':'BOTTLE'} if family==8 else {},
            'reason':FAMILIES[family]})
        path = f'inputs/{cid}.xlsx'
        # Deterministic different sheet ordering while formulas use named references.
        if v % 2 == 0: sheets = [sheets[4],sheets[2],sheets[0],sheets[3],sheets[1]] + sheets[5:]
        specs.append({'id':cid,'family':family,'path':path,'sheets':sheets,'remove_formula_cache':family==10})
        manifest.append({'id':cid,'family':family,'title':FAMILIES[family], 'path':path,'schema':f'schemas/{cid}.json',
                         'schema_ref':ref,'expected_rows':n,'sheets':len(sheets),'expected_action':'ask' if family==8 else 'block' if family>=9 else 'map'})
save('specifications.json',specs)
save('manifest.json',{'seed':5112026,'synthetic':True,'paid_api':False,'files':manifest,'families':FAMILIES})
print('Prepared',len(specs),'workbook specifications;',sum(x['sheets'] for x in manifest),'sheets')
