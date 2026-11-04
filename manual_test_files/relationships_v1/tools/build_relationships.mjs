import fs from 'node:fs/promises';
import {Workbook,SpreadsheetFile} from '@oai/artifact-tool';
const root='C:/Users/Beat Beat/Desktop/Forma/manual_test_files/relationships_v1';
const specs=JSON.parse(await fs.readFile(root+'/specifications.json','utf8'));
await fs.mkdir(root+'/qa',{recursive:true});
for(const spec of specs){
 const wb=Workbook.create();
 for(const s of spec.sheets) wb.worksheets.add(s.name);
 for(const s of spec.sheets){
  const sh=wb.worksheets.getItem(s.name),width=Math.max(...s.rows.map(r=>r.length));
  const data=s.rows.map(r=>Array.from({length:width},(_,i)=>r[i]??null));
  sh.getRangeByIndexes(0,0,data.length,width).values=data;
  sh.getRangeByIndexes(0,0,data.length,width).format.font={name:'Arial',size:11};
  sh.getRangeByIndexes(0,0,data.length,width).format.columnWidth=23;
  sh.getRangeByIndexes(0,0,data.length,width).format.rowHeight=23;
  const hr=(s.headerRow??1)-1;
  sh.getRangeByIndexes(hr,0,1,width).format={fill:'#E8EEF5',font:{bold:true,color:'#243959'}};
  for(const m of s.merges??[])sh.mergeCells(m);
  for(const [cell,formula] of Object.entries(s.formulas??{})) sh.getRange(cell).formulas=[[formula]];
  if(s.name==='Report'){sh.getRange('A2:D2').format.wrapText=true;sh.getRange('A2:D2').format.rowHeight=40;}
 }
 wb.recalculate();
 const caseId=spec.path.split('/')[1],name=spec.path.split('/').at(-1);
 for(const s of spec.sheets){
  const inspect=await wb.inspect({kind:'table',range:`'${s.name}'!A1:F20`,include:'values,formulas',tableMaxRows:20,tableMaxCols:6,maxChars:5000});
  await fs.writeFile(`${root}/qa/${caseId}-${name}-${s.name}.jsonl`,inspect.ndjson);
  const im=await wb.render({sheetName:s.name,range:`A1:${String.fromCharCode(64+Math.max(...s.rows.map(r=>r.length)))}${Math.min(s.rows.length,18)}`,scale:1,format:'png'});
  await fs.writeFile(`${root}/qa/${caseId}-${name}-${s.name}.png`,new Uint8Array(await im.arrayBuffer()));
 }
 const out=await SpreadsheetFile.exportXlsx(wb);await out.save(root+'/'+spec.path);
 console.log('saved',spec.path);
}
