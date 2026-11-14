// Real browser, restricted to the owned local mock pilot and its freshly prepared fixture.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

(async () => {
  const name = process.env.FORMA_PILOT_NAME || 'pilot-nov14';
  assert.match(name, /^[a-z][a-z0-9-]{0,23}$/);
  const dir = path.resolve(__dirname, '../../deploy/repro/generated', name);
  const marker = JSON.parse(await fs.readFile(path.join(dir, 'owner.json'), 'utf8'));
  assert.equal(marker.pilot, true);
  const f = JSON.parse(await fs.readFile(path.join(dir, 'review-ui-fixture.json'), 'utf8'));
  const base = `http://127.0.0.1:${marker.ports[0]}`;
  assert.equal(f.url, base);
  assert.equal(f.mock_only, true);
  const out = path.resolve(__dirname, '../../test-results/pilot-browser');
  await fs.mkdir(out, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await context.tracing.start({ screenshots: true, snapshots: true });
  async function waitFor(fn) {
    for (let i = 0; i < 240; i++) { if (await fn()) return; await page.waitForTimeout(250); }
    throw Error('Condition timed out: ' + page.url());
  }
  const api = async url => {
    const r = await page.request.get(base + url);
    assert.equal(r.ok(), true, await r.text());
    return r.json();
  };
  try {
    assert.equal((await api('/api/settings/ai')).mock_only, true);
    assert.equal((await api('/api/me')).pilot_mode, true);
    await page.goto(base + '/imports/new');
    await page.getByText(/Pilot: a person must|โหมดทดลอง: คนต้องตรวจ/).waitFor();
    assert.equal(await page.getByRole('checkbox').isDisabled(), true);
    const url = base + `/review?mapping=${f.mapping_id}&round=${f.round_id}`;
    const draft = `/api/mappings/${f.mapping_id}/review/draft`;
    const qs = await api(`/api/mappings/${f.mapping_id}/questions`);
    await page.goto(url);
    const vat = page.getByRole('button', { name: /^VAT included$|^รวม VAT แล้ว$/ });
    await vat.click();
    await waitFor(async () => Object.values((await api(draft)).payload?.answers || {}).includes(true));
    const feedback = page.getByRole('region', { name: /Pilot question feedback|บันทึกคุณภาพคำถาม/ });
    await feedback.getByRole('combobox').selectOption('evidence_in_file');
    await feedback.getByRole('textbox').first().fill('Synthetic check: VAT included appears in the source header.');
    await feedback.getByRole('textbox').nth(1).fill('review-journey.xlsx / สินค้า / G1');
    await feedback.getByRole('button', { name: /Save observation|บันทึกสาเหตุ/ }).click();
    await feedback.getByRole('status').waitFor();
    await page.screenshot({ path: path.join(out, 'pilot-question.png'), fullPage: true });
    await page.reload(); await vat.waitFor();
    assert.equal(await vat.getAttribute('aria-pressed'), 'true');
    assert.equal(await feedback.getByRole('combobox').inputValue(), 'evidence_in_file');
    await page.getByRole('button', { name: /^Next$|^ข้อถัดไป$/ }).click();
    await page.getByRole('spinbutton').fill('12');
    await waitFor(async () => Object.values((await api(draft)).payload?.answers || {}).includes(12));
    await feedback.getByRole('combobox').selectOption('missing_information');
    await feedback.getByRole('textbox').fill('Synthetic file has no pack-size value; supplied by test operator.');
    await feedback.getByRole('button', { name: /Save observation|บันทึกสาเหตุ/ }).click();
    await feedback.getByRole('status').waitFor();
    await page.getByRole('button', { name: /^Check & confirm$|^ตรวจผลและยืนยัน$/ }).click();
    await page.getByRole('heading', { name: /Check the result before confirming|ตรวจผลก่อนยืนยัน/ }).waitFor();
    await page.goBack();
    await page.getByRole('spinbutton').waitFor();
    assert.equal(await page.getByRole('spinbutton').inputValue(), '12');
    const notes = page.getByRole('button', { name: /Other checks|เรื่องที่ต้องตรวจเพิ่ม/ });
    if (await notes.count()) {
      await notes.click();
      for (const checkbox of await page.locator('section').getByRole('checkbox').all()) await checkbox.check();
      await waitFor(async () => ((await api(draft)).payload?.acknowledged || []).length > 0);
    }
    await page.getByRole('button', { name: /^Check & confirm$|^ตรวจผลและยืนยัน$/ }).click();
    await page.getByRole('heading', { name: /Check the result before confirming|ตรวจผลก่อนยืนยัน/ }).waitFor();
    const confirm = page.getByRole('button', { name: /^Confirm & prepare file$|^ยืนยันและเตรียมไฟล์$/ });
    await page.locator('section').getByRole('checkbox').last().waitFor();
    for (const checkbox of await page.locator('section').getByRole('checkbox').all()) await checkbox.check();
    await waitFor(() => confirm.isEnabled());
    await confirm.click();
    const inspected = page.getByRole('checkbox', { name: /I checked the generated data|ฉันตรวจข้อมูลที่สร้างแล้ว/ });
    await inspected.waitFor({ timeout: 60000 });
    const r = await api(`/api/rounds/${f.round_id}`);
    const denied = await page.request.post(base + `/api/datasets/${r.summary.dataset_version_id}/export?fmt=json`);
    assert.equal(denied.status(), 409);
    await inspected.check();
    await page.getByRole('button', { name: /Confirm data & prepare download|ยืนยันข้อมูลและเตรียมดาวน์โหลด/ }).click();
    const link = page.getByRole('link', { name: /Download JSON|ดาวน์โหลด JSON/ });
    await link.waitFor({ timeout: 60000 });
    const [download] = await Promise.all([page.waitForEvent('download'), link.click()]);
    const downloaded = path.join(out, 'download.json');
    await download.saveAs(downloaded);
    const rows = JSON.parse(await fs.readFile(downloaded, 'utf8'));
    assert.equal(rows.length, 12);
    assert.ok(rows.every(row => row.prices.every(price => price.vat_included === true)));
    assert.deepEqual(errors, []);
    const result = { passed: true, rows: 12, mock_only: true, cost_usd: 0, question_count: qs.questions.length,
      checks: ['upload auto-export disabled', 'question feedback persisted', 'reload preserves answer', 'browser Back preserves pack size', 'preview', 'export denied before approval', 'human approval', 'download correct JSON'] };
    await fs.writeFile(path.join(out, 'result.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result));
  } finally {
    await page.screenshot({ path: path.join(out, 'last.png'), fullPage: true }).catch(() => {});
    await context.tracing.stop({ path: path.join(out, 'trace.zip') });
    await browser.close();
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
