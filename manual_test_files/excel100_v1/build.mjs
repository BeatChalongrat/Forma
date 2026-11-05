import fs from 'node:fs/promises';
import {Workbook,SpreadsheetFile} from '@oai/artifact-tool';
const root = 'C:/Users/Beat Beat/Desktop/Forma/manual_test_files/excel100_v1';
const specs=JSON.parse(await fs.readFile(root+'/specifications.json','utf8'));
await fs.mkdir(root+'/inputs',{recursive:true});
await fs.mkdir(root+'/qa',{recursive:true});
for(const spec of specs){
 const wb=Workbook.create();
 for(const s of spec.sheets)wb.worksheets.add(s.name);
 for(const s of spec.sheets){
  const sh=wb.worksheets.getItem(s.name),width=Math.max(...s.rows.map(r=>r.length));
  sh.getRangeByIndexes(0,0,s.rows.length,width).values=s.rows.map(r=>Array.from({length:width},(_,i)=>r[i]??null));
  const area=sh.getRangeByIndexes(0,0,s.rows.length,width);
  area.format.font={name:'Arial',size:11};area.format.columnWidth=23;area.format.rowHeight=24;
  sh.getRangeByIndexes((s.headerRow??1)-1,0,1,width).format={fill:'#E8EEF5',font:{bold:true,color:'#243959'}};
  for(const m of s.merges??[])sh.mergeCells(m);
  for(const [cell,formula]of Object.entries(s.formulas??{}))sh.getRange(cell).formulas=[[formula]];
 }
 wb.recalculate();
 if(spec.id.endsWith('01')){
  const s=spec.sheets.find(x=>x.formulas)??spec.sheets[0];
  const range=`A1:${String.fromCharCode(64+Math.max(...s.rows.map(r=>r.length)))}${Math.min(s.rows.length,12)}`;
  const check=await wb.inspect({kind:'table',range:`'${s.name}'!${range}`,include:'values,formulas',tableMaxRows:12,tableMaxCols:7,maxChars:5000});
  await fs.writeFile(`${root}/qa/${spec.id}.jsonl`,check.ndjson);
  const im=await wb.render({sheetName:s.name,range,scale:1,format:'png'});
  await fs.writeFile(`${root}/qa/${spec.id}.png`,new Uint8Array(await im.arrayBuffer()));
 }
 const out=await SpreadsheetFile.exportXlsx(wb);await out.save(root+'/'+spec.path);
 console.log('saved',spec.id);
}
