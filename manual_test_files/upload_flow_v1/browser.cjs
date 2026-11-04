// Isolated frontend against the fake stack. npm cache Playwright path is supplied by the caller.
const { chromium } = require(process.argv[2]);
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
(async () => {
  const browser = await chromium.launch({executablePath: process.argv[3], headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors=[]; page.on('pageerror', e => errors.push(e.message));
    const settings = await (await page.request.get('http://127.0.0.1:18084/api/settings/ai')).json();
    assert.equal(settings.model,'fake-stability'); assert.equal(settings.base_url,'http://fake:8001/v1');
    await page.goto('http://127.0.0.1:15173/imports/new');
    const a=Buffer.from('warehouse,bin,temperature\nW1,A1,4.5\n'), b=Buffer.from('warehouse,bin,temperature\nW1,A1,9.5\n');
    assert.equal(a.length,b.length);
    await page.locator('input[type=file]').setInputFiles([
      {name:'browser-same.csv',mimeType:'text/csv',buffer:a},
      {name:'browser-same.csv',mimeType:'text/csv',buffer:b},
    ]);
    assert.equal(await page.getByTitle('browser-same.csv',{exact:true}).count(),2);
    await page.screenshot({path:path.join(__dirname,'browser-selection.png'),fullPage:true});
    let lost, replay, keys=[];
    await page.route('**/api/batches', async route => {
      if(route.request().method()!=='POST') return route.continue();
      keys.push(route.request().postData().match(/name="upload_key"\r\n\r\n([^\r]+)/)?.[1]);
      const response=await route.fetch();
      assert.equal(response.status(),201);
      if(!lost){lost=await response.json(); await route.abort('connectionreset');}
      else {replay=await response.json();await route.fulfill({response});}
    });
    const button=page.getByRole('button',{name:/^(อัปโหลด|Upload) 2/});
    await button.click();
    await page.getByRole('alert').filter({hasText:/หยุดกลางทาง|interrupted/}).waitFor();
    assert.equal(lost.total,2);
    await page.screenshot({path:path.join(__dirname,'browser-retry.png'),fullPage:true});
    await button.click();
    await page.waitForURL('**/uploads/**');
    assert.equal(replay.id,lost.id);assert.equal(replay.replayed,true);assert.equal(replay.total,2);
    assert.equal(keys.length,2);assert.ok(keys[0]);assert.equal(keys[0],keys[1]);
    await page.screenshot({path:path.join(__dirname,'browser-result.png'),fullPage:true});
    assert.deepEqual(errors,[]);
    const result={same_name_same_size_visible:2,connection_lost_after_commit:true,retry_same_batch:true,
      duplicate_batch_created:false,upload_key_stable:true,batch_id:replay.id,page_errors:errors};
    fs.writeFileSync(path.join(__dirname,'browser-result.json'),JSON.stringify(result,null,2));
    console.log(JSON.stringify(result));
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1});
