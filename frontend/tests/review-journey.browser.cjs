// Real browser against the disposable mock fixture, never localhost dev.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs/promises');

(async () => {
  const browser = await chromium.launch({headless:true});
  const context = await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});
  const page = await context.newPage();
  const out = process.env.FORMA_QA_OUTPUT || require('node:os').tmpdir();
  const errors=[];
  page.on('pageerror', e=>errors.push(e.message));
  await context.tracing.start({screenshots:true,snapshots:true});
  const base='http://127.0.0.1:15174';
  async function waitFor(fn){for(let i=0;i<240;i++){if(await fn())return;await page.waitForTimeout(250)}throw Error('Condition timed out: '+page.url())}
  try {
    const f=await (await page.request.get(base+'/api/test-fixture')).json();
    assert.equal(f.mock_only,true);
    const url=base+`/review?mapping=${f.mapping_id}&round=${f.round_id}`;
    const draft=base+`/api/mappings/${f.mapping_id}/review/draft`;
    const settled=()=>waitFor(()=>page.evaluate(id=>!Object.keys(sessionStorage).some(k=>k.startsWith('forma.review-outbox.v1:')&&k.endsWith(':'+id)),f.mapping_id));
    await page.goto(url);
    const answer=()=>page.getByRole('button',{name:/^Not included$|^VAT not included$|^VAT excluded$|^ยังไม่รวม VAT$/});
    await answer().click();
    await waitFor(async()=>Object.values((await (await page.request.get(draft)).json()).payload?.answers || {}).includes(false));
    await settled();
    await page.reload(); await answer().waitFor();
    assert.equal(await answer().getAttribute('aria-pressed'),'true');
    await page.getByRole('button',{name:/^Check & confirm$|^ตรวจผลและยืนยัน$/}).click();
    await page.getByRole('heading',{name:/Check the result before confirming|ตรวจผลก่อนยืนยัน/}).waitFor();
    await page.goBack(); await answer().waitFor();
    assert.equal(await answer().getAttribute('aria-pressed'),'true');
    assert.ok(page.url().includes(f.mapping_id));
    // Return from the file list to the exact same draft.
    await page.getByRole('button',{name:/Back to the review list|กลับรายการที่รอตรวจ/}).first().click();
    await page.waitForURL(u=>u.pathname==='/review'&&!u.searchParams.has('mapping'));
    await page.goto(url); await answer().waitFor(); assert.equal(await answer().getAttribute('aria-pressed'),'true');
    const notes=page.getByRole('button',{name:/Other checks|เรื่องที่ต้องตรวจเพิ่ม/});
    if(await notes.count()){
      await notes.click();
      await page.getByRole('heading',{name:/^Other points to check$|^เรื่องที่ต้องตรวจเพิ่ม$/}).waitFor();
      assert.equal(await page.locator('section').getByRole('checkbox').count(),2);
      for(const checkbox of await page.locator('section').getByRole('checkbox').all()) await checkbox.check();
      await waitFor(async()=>((await (await page.request.get(draft)).json()).payload?.acknowledged || []).length===2);
    }
    await page.getByRole('button',{name:/^Check & confirm$|^ตรวจผลและยืนยัน$/}).click();
    await page.getByRole('heading',{name:/Check the result before confirming|ตรวจผลก่อนยืนยัน/}).waitFor();
    await settled();
    for(const checkbox of await page.locator('section').getByRole('checkbox').all()) await checkbox.check();
    const confirm=page.getByRole('button',{name:/^Confirm & prepare file$|^ยืนยันและเตรียมไฟล์$/});
    await confirm.waitFor(); await waitFor(()=>confirm.isEnabled()); await confirm.click();
    const inspected=page.getByRole('checkbox',{name:/I checked the generated data|ฉันตรวจข้อมูลที่สร้างแล้ว/});
    await inspected.waitFor({timeout:60000}); await inspected.check();
    await page.getByRole('button',{name:/Confirm data & prepare download|ยืนยันข้อมูลและเตรียมดาวน์โหลด/}).click();
    const link=page.getByRole('link',{name:/Download JSON|ดาวน์โหลด JSON/});
    await link.waitFor({timeout:60000});
    const [download]=await Promise.all([page.waitForEvent('download'),link.click()]);
    const file=path.join(out,'review-download.json');await download.saveAs(file);
    const rows=JSON.parse(await fs.readFile(file,'utf8'));
    assert.equal(rows.length,12); assert.equal(new Set(rows.map(r=>r.sku)).size,12);
    assert.ok(rows.every(r=>r.prices.every(p=>p.vat_included===false)));
    assert.deepEqual(errors,[]);
    await page.screenshot({path:path.join(out,'review-complete.png'),fullPage:true});
    const receipt = {passed:['answer false persists','reload','browser Back stays on same file',
      'list return preserves draft','preview','confirm','download values'],rows:rows.length};
    require('node:fs').writeFileSync(require('node:path').join(out, 'browser-review.json'), JSON.stringify(receipt, null, 2));
    console.log(JSON.stringify(receipt));
  } finally {
    await page.screenshot({path:path.join(out,'review-last.png'),fullPage:true}).catch(()=>{});
    await context.tracing.stop({path:path.join(out,'review-trace.zip')});
    await browser.close();
  }
})().catch(e=>{console.error(e);process.exitCode=1});
