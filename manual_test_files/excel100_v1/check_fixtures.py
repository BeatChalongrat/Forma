"""Read-only workbook QA, plus deliberate removal of cached results for family 10.
Uses OOXML to represent a valid Excel file saved without calculated values.
Never uses openpyxl for authoring.
"""
import hashlib
import json
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
import openpyxl

ROOT = Path(__file__).resolve().parent
specs = json.loads((ROOT/'specifications.json').read_text(encoding='utf-8'))
NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
results = []
for s in specs:
    p = ROOT/s['path']
    if s['remove_formula_cache']:
        with zipfile.ZipFile(p) as z: entries = [(info, z.read(info.filename)) for info in z.infolist()]
        with zipfile.ZipFile(p, 'w', zipfile.ZIP_DEFLATED) as z:
            for info, data in entries:
                if info.filename.startswith('xl/worksheets/') and info.filename.endswith('.xml'):
                    tree = ET.fromstring(data)
                    for cell in tree.iter(NS+'c'):
                        if cell.find(NS+'f') is not None:
                            value = cell.find(NS+'v')
                            if value is not None: cell.remove(value)
                    data = ET.tostring(tree, encoding='utf-8', xml_declaration=True)
                z.writestr(info, data)
    values = openpyxl.load_workbook(p, data_only=True)
    formulas = openpyxl.load_workbook(p, data_only=False)
    assert len(values.sheetnames) == len(s['sheets']) >= 5
    count = missing = 0
    for ws in formulas:
        for row in ws:
            for cell in row:
                if cell.data_type == 'f':
                    count += 1
                    val = values[ws.title][cell.coordinate].value
                    missing += val is None
                    if not s['remove_formula_cache']:
                        assert isinstance(val, (float,int)), (p, ws.title, cell.coordinate, val)
                elif cell.data_type == 'e': raise AssertionError((p,cell.coordinate,cell.value))
    assert missing == (count if s['remove_formula_cache'] else 0)
    result = {'id':s['id'],'sha256':hashlib.sha256(p.read_bytes()).hexdigest(), 'bytes':p.stat().st_size,
              'sheets':len(values.sheetnames),'physical_rows':sum(ws.max_row for ws in values),
              'formula_cells':count,'uncached_formulas':missing}
    results.append(result); values.close(); formulas.close()
(ROOT/'qa/fixtures.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
print(json.dumps({'files':len(results),'sheets':sum(x['sheets'] for x in results),
 'bytes':sum(x['bytes'] for x in results),'physical_rows':sum(x['physical_rows'] for x in results),
 'formula_cells':sum(x['formula_cells'] for x in results),'intentional_uncached':sum(x['uncached_formulas'] for x in results)}))
