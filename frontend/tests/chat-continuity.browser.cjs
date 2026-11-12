// Usage: node tests/chat-continuity.browser.cjs <playwright module> <chromium executable>
// Requires disposable tests/assistant_browser_server.py on 18171 and Vite proxy on 15174.
const {chromium} = require(process.argv[2] || 'playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({executablePath: process.argv[3], headless: true});
  const context = await browser.newContext({viewport:{width:1440,height:1000}});
  const page = await context.newPage(), errors=[];
  const out=process.env.FORMA_QA_OUTPUT || require('node:os').tmpdir();
  await context.tracing.start({screenshots:true,snapshots:true});
  try {
    page.on('pageerror', e => errors.push(e.message));
    const base='http://127.0.0.1:15174';
    const cid=(await (await page.request.post(base+'/api/assistant/conversations',{data:{}})).json()).id;
    const url=base+'/assistant?chat='+cid, api=base+'/api/assistant/conversations/'+cid;
    await page.goto(url);
    const input=()=>page.getByRole('textbox',{name:/Your question|คำถามของคุณ/});
    const send=()=>page.getByRole('button',{name:/^Send question$|^ส่งคำถาม$/});
    async function waitFor(fn){for(let i=0;i<160;i++){if(await fn())return;await page.waitForTimeout(150)}console.log(JSON.stringify({url:page.url(),alerts:await page.getByRole('alert').allTextContents(),state:await (await page.request.get(api+'/state')).json(),local:await page.evaluate(()=>Object.fromEntries(Object.entries(localStorage).filter(([k])=>k.startsWith('forma.draft:'))))}));throw Error('condition timed out')}
    await input().fill('slow report');
    await waitFor(async()=> (await (await page.request.get(api+'/state')).json()).draft.input==='slow report');
    await page.reload(); await waitFor(async()=>await input().inputValue()==='slow report');
    await send().click();
    await waitFor(async()=> (await (await page.request.get(api)).json()).turns.length===1);
    await page.goto(base+'/imports');
    await waitFor(async()=> (await (await page.request.get(api)).json()).turns[0]?.status==='complete');
    await page.goto(url);
    await page.getByRole('table').first().waitFor();
    assert.equal(await page.getByRole('cell',{name:'12',exact:true}).count(),1);
    assert.ok(await page.getByRole('button',{name:/Copy message|คัดลอกข้อความ/}).count());
    await page.getByRole('button',{name:/Helpful|คำตอบมีประโยชน์/}).click();
    await waitFor(async()=> (await (await page.request.get(api)).json()).turns[0]?.feedback?.rating==='helpful');
    await page.reload(); await page.getByText(/Feedback saved|บันทึกความเห็นแล้ว/).waitFor();
    await input().fill('clarify'); await send().click();
    await page.getByRole('radio',{name:'November',exact:true}).waitFor();
    await page.getByRole('radio',{name:'November',exact:true}).check();
    await waitFor(async()=> Object.values((await (await page.request.get(api+'/state')).json()).draft.clarifications??{}).some(v=>v.answers?.[0]==='November'));
    await page.reload();
    await page.getByRole('radio',{name:'November',exact:true}).waitFor();
    await waitFor(()=>page.getByRole('radio',{name:'November',exact:true}).isChecked());
    await page.getByRole('button',{name:/^Send answers$|^ส่งคำตอบ$/}).click();
    await page.getByText(/Answers sent|ส่งคำตอบแล้ว/).waitFor();
    // Editing an earlier question forks its prefix and preserves the complete original.
    await page.getByRole('button',{name:/Edit and ask again|แก้ไขและถามใหม่/}).first().click();
    await page.getByRole('textbox',{name:/Edit question|แก้ไขคำถาม/}).fill('Corrected report');
    await page.getByRole('button',{name:/Send updated question|เปิดทางเลือกที่แก้ไข/}).click();
    await waitFor(()=>Promise.resolve(page.url()!==url));
    await waitFor(async()=>await input().inputValue()==='Corrected report');
    assert.equal((await (await page.request.get(api)).json()).turns.length,3);
    assert.equal(await page.getByRole('table').count(),0);
    await page.getByRole('link',{name:/Open original chat|กลับแชตเดิม/}).click();
    await page.getByRole('table').first().waitFor();
    // A dropped browser connection follows the same saved request, without another POST.
    await input().fill('slow reconnect'); await send().click();
    await waitFor(async()=> (await (await page.request.get(api)).json()).turns.length===4);
    await context.setOffline(true); await page.waitForTimeout(1500); await context.setOffline(false);
    await waitFor(async()=> (await (await page.request.get(api)).json()).turns[3]?.status==='complete');
    await waitFor(async()=>await page.getByRole('table').count()===4);
    assert.equal((await (await page.request.get(api)).json()).turns.length,4);
    await input().fill('slow stop'); await send().click();
    await waitFor(async()=> (await (await page.request.get(api)).json()).turns.length===5);
    await page.getByRole('button',{name:/^Stop response$|^หยุดคำตอบ$/}).click();
    await waitFor(async()=> (await (await page.request.get(api)).json()).turns[4]?.status==='stopped');
    await page.reload();
    await page.getByText(/Stopped. This reply|หยุดแล้ว คำตอบนี้/).waitFor();
    // One file uploaded, the second fails, then reload: retry must keep the original chat and file.
    let uploads=0, uploadChat='';
    await page.route('**/api/assistant/conversations/*/attachments',async route=>{
      if(route.request().method()!=='POST') return route.continue();
      uploads++; uploadChat=new URL(route.request().url()).pathname.split('/')[4];
      if(uploads===2) return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:{code:'unavailable',message:'Mock upload interrupted'}})});
      return route.continue();
    });
    await page.goto(base+'/assistant?new=1');
    await input().fill('Report with files');
    await waitFor(async()=>await page.getByRole('button',{name:/^Attach a file$|^แนบไฟล์$/}).isEnabled());
    const file=name=>({name,mimeType:'text/csv',buffer:Buffer.from('sku,qty\nA,12')});
    await page.locator('input[type=file]').setInputFiles([file('first.csv'),file('second.csv')]);
    await send().click();
    await waitFor(async()=>uploads===2 && await send().isEnabled());
    const originalUploadChat=uploadChat;
    await page.reload();
    await waitFor(async()=>await input().inputValue()==='Report with files');
    await page.getByText(/Select these files again|เลือกไฟล์ที่ยังไม่ได้อัปโหลด/).waitFor();
    await page.locator('input[type=file]').setInputFiles(file('second.csv'));
    await send().click();
    await waitFor(()=>Promise.resolve(page.url().includes(originalUploadChat)));
    const attachmentChatApi=base+'/api/assistant/conversations/'+originalUploadChat;
    await waitFor(async()=> (await (await page.request.get(attachmentChatApi)).json()).turns[0]?.status==='complete');
    const attached=(await (await page.request.get(attachmentChatApi)).json()).turns[0].attachments;
    assert.equal(uploads,3); assert.equal(uploadChat,originalUploadChat);
    assert.deepEqual(attached.map(f=>f.name).sort(),['first.csv','second.csv']);
    await page.unroute('**/api/assistant/conversations/*/attachments');
    await page.screenshot({path:require('node:path').join(process.env.FORMA_QA_OUTPUT || require('node:os').tmpdir(),'forma-chat-continuity.png'),fullPage:true});
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:['draft reload','background completion after navigation','Markdown table','message actions','feedback reload','clarification draft reload','send clarification','edit branch preserves original','back to original','network reconnect without duplicate request','stop persists after reload','partial attachment upload survives reload without duplicates'],consoleErrors:errors}));
  } finally {
    await page.screenshot({path:require('node:path').join(out,'chat-last.png'),fullPage:true}).catch(()=>{});
    await context.tracing.stop({path:require('node:path').join(out,'chat-trace.zip')});
    await browser.close();
  }
})().catch(e=>{console.error(e);process.exitCode=1});
