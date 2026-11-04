"""Inject deliberate OOXML fault fixtures, independently validate, then freeze hashes."""
import hashlib
import json
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
import openpyxl

ROOT=Path(__file__).resolve().parents[1]
NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
ET.register_namespace('',NS)

def rewrite(path, mode):
    with zipfile.ZipFile(path) as z: entries={n:z.read(n) for n in z.namelist()}
    name='xl/worksheets/sheet1.xml';tree=ET.fromstring(entries[name])
    for cell in tree.findall('.//{%s}c'%NS):
        formula=cell.find('{%s}f'%NS)
        if formula is None:continue
        value=cell.find('{%s}v'%NS)
        if mode=='stale':value.text=str(float(value.text)+777)
        if mode=='uncached' and value is not None:cell.remove(value)
        if mode=='external':
            row=cell.attrib['r'][1:]
            formula.text=f"'Counts'!B{row}*'[missing-pack.xlsx]Pack factors'!B{row}"
            value.text='999'
    entries[name]=ET.tostring(tree,encoding='utf-8',xml_declaration=True)
    if mode=='external':
        name='xl/worksheets/sheet3.xml';tree=ET.fromstring(entries[name])
        for cell in tree.findall('.//{%s}c'%NS):
            if cell.attrib['r'].startswith('B') and cell.attrib['r']!='B1':
                for child in list(cell):cell.remove(child)
        entries[name]=ET.tostring(tree,encoding='utf-8',xml_declaration=True)
    tmp=path.with_suffix('.tmp')
    with zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED) as z:
        for name,data in entries.items():z.writestr(name,data)
    tmp.replace(path)

manifest=json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))
qa=[]
for item in manifest['rounds']:
    review=json.loads((ROOT/item['review']).read_text(encoding='utf-8'))
    for f in item['files']:
        p=ROOT/f['path']
        if review['fault']:rewrite(p,review['fault'])
        f['sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
        w=openpyxl.load_workbook(p,data_only=False,read_only=False)
        v=openpyxl.load_workbook(p,data_only=True,read_only=True)
        formulas=[(s.title,c.coordinate,c.value,v[s.title][c.coordinate].value) for s in w for row in s for c in row if c.data_type=='f']
        if item['case']=='R05_formula':
            expected=json.loads((ROOT/item['expected']).read_text(encoding='utf-8'))
            assert [x[3] for x in formulas]==[x['qty'] for x in expected]
        if review['fault']=='stale':assert all(x[3]>=777 for x in formulas)
        if review['fault']=='uncached':assert len(formulas)==12 and all(x[3] is None for x in formulas)
        if review['fault']=='external':assert len(formulas)==12 and all('[missing-pack.xlsx]' in x[2] for x in formulas)
        qa.append({'path':f['path'],'sheets':[{'name':s.title,'rows':s.max_row,'columns':s.max_column,'merged':[str(m) for m in s.merged_cells.ranges]} for s in w],'formulas':formulas})
        w.close();v.close()
(ROOT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
(ROOT/'qa/structure.json').write_text(json.dumps(qa,ensure_ascii=False,indent=2),encoding='utf-8')
print('Frozen',len(qa),'files. Formula fault injection verified. Do not open/save fault cases in Excel before upload.')
