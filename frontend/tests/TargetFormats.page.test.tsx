// @vitest-environment jsdom
import { deferred, mountPage, stub } from './pageHarness'
import { beforeEach, expect, test } from 'vitest'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { SettingsPage } from '../src/pages/Settings'
import type { CsvTemplateAnalysis } from '../src/lib/api'

const document = { type: 'object', properties: { SKU: { type: 'string' }, Price: { type: 'number' } }, 'x-forma': { schema_id: 'catalog', identity_keys: ['/SKU'] } }
const preview = { entity: 'catalog', status: 'active', title: 'Catalogue', description: '', ref: 'catalog@1', schema_id: 'catalog', version: 1, field_count: 2, identity_keys: ['/SKU'], fields: [], findings: [], previous: null, diff: null }
const analysis: CsvTemplateAnalysis = { delimiter: ',', row_count: 1, columns: [
  { name: 'SKU', suggested_type: 'string', examples: ['00123'], reason: 'เก็บเลขศูนย์นำหน้า', blank_count: 0 },
  { name: 'Price', suggested_type: 'number', examples: ['12.5'], reason: 'ตรวจหน่วยและ VAT', blank_count: 0 },
] }
beforeEach(() => { stub('schemas', []); stub('semantics', { sku: 'รหัสสินค้า', 'price.amount': 'ราคา' }); stub('schema', {}) })
async function start() {
  const page = mountPage(<SettingsPage />, '/settings', '/settings?tab=formats')
  await page.user.click(await screen.findByRole('button', { name: /New format/ }))
  return page
}
function csvFile(content = 'SKU,Price\n00123,12.5', name = 'catalog.csv') {
  const file = new File([content], name, { type: 'text/csv' })
  Object.defineProperty(file, 'arrayBuffer', { value: async () => new TextEncoder().encode(content).buffer })
  return file
}
async function openCsv() {
  const page = await start()
  await page.user.click(screen.getByRole('button', { name: 'CSV ตัวอย่าง' }))
  fireEvent.change(screen.getByLabelText('ไฟล์ CSV ตัวอย่าง'), { target: { files: [csvFile()] } })
  await waitFor(() => expect((screen.getByRole('button', { name: 'อ่านแม่แบบ CSV' }) as HTMLButtonElement).disabled).toBe(false))
  await page.user.click(screen.getByRole('button', { name: 'อ่านแม่แบบ CSV' }))
  await screen.findByRole('region', { name: 'คอลัมน์ SKU' })
  return page
}
async function decide(page: Awaited<ReturnType<typeof start>>) {
  fireEvent.change(screen.getByLabelText('ชื่อรูปแบบ'), { target: { value: 'Catalogue' } })
  fireEvent.change(screen.getByLabelText(/รหัสรูปแบบ/), { target: { value: 'catalog' } })
  const sku = within(screen.getByRole('region', { name: 'คอลัมน์ SKU' }))
  const price = within(screen.getByRole('region', { name: 'คอลัมน์ Price' }))
  await page.user.selectOptions(sku.getByLabelText('ความหมาย'), 'sku')
  await page.user.click(sku.getByLabelText(/ใช้ระบุรายการ/))
  await page.user.click(sku.getByLabelText('ตรวจและยืนยันการตั้งค่าคอลัมน์นี้แล้ว'))
  await page.user.selectOptions(price.getByLabelText('ความหมาย'), 'price.amount')
  await page.user.click(price.getByLabelText('ตรวจและยืนยันการตั้งค่าคอลัมน์นี้แล้ว'))
}

test('CSV cannot save until each column is reviewed; edits invalidate confirmation and preview', async () => {
  stub('analyzeCsvTemplate', analysis)
  const prepare = stub('prepareCsvTemplate', { document })
  stub('previewSchema', preview)
  const save = stub('registerSchema', preview)
  const page = await openCsv()
  expect((screen.getByRole('button', { name: /บันทึกรูปแบบ/ }) as HTMLButtonElement).disabled).toBe(true)
  expect((screen.getByRole('button', { name: 'ตรวจรูปแบบที่ยืนยันแล้ว' }) as HTMLButtonElement).disabled).toBe(true)
  await decide(page)
  await page.user.click(screen.getByRole('button', { name: 'ตรวจรูปแบบที่ยืนยันแล้ว' }))
  await screen.findByText('valid · catalog@1')
  expect(save).not.toHaveBeenCalled()
  expect(prepare).toHaveBeenCalledWith(expect.objectContaining({ schema_id: 'catalog', columns: [expect.objectContaining({ name: 'SKU', data_type: 'string', required: true, identity: true, confirmed: true }), expect.objectContaining({ name: 'Price', confirmed: true })] }))
  const price = within(screen.getByRole('region', { name: 'คอลัมน์ Price' }))
  await page.user.selectOptions(price.getByLabelText('ชนิดข้อมูล'), 'string')
  expect(screen.queryByText('valid · catalog@1')).toBeNull()
  expect((price.getByLabelText('ตรวจและยืนยันการตั้งค่าคอลัมน์นี้แล้ว') as HTMLInputElement).checked).toBe(false)
  expect((screen.getByRole('button', { name: /บันทึกรูปแบบ/ }) as HTMLButtonElement).disabled).toBe(true)
  await page.user.click(price.getByLabelText('ตรวจและยืนยันการตั้งค่าคอลัมน์นี้แล้ว'))
  await page.user.click(screen.getByRole('button', { name: 'ตรวจรูปแบบที่ยืนยันแล้ว' }))
  await screen.findByText('valid · catalog@1')
  await page.user.click(screen.getByRole('button', { name: /บันทึกรูปแบบ/ }))
  await waitFor(() => expect(save).toHaveBeenCalledWith(document))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
})

test('JSON Schema still previews and registers through the unchanged API', async () => {
  const check = stub('previewSchema', preview)
  const save = stub('registerSchema', preview)
  const page = await start()
  fireEvent.change(page.container.querySelector('textarea')!, { target: { value: JSON.stringify(document) } })
  await page.user.click(screen.getByRole('button', { name: 'ตรวจรูปแบบ' }))
  await screen.findByText('valid · catalog@1')
  expect(check).toHaveBeenCalledWith(document)
  await page.user.click(screen.getByRole('button', { name: /บันทึกรูปแบบ/ }))
  await waitFor(() => expect(save).toHaveBeenCalledWith(document))
})

test('fixed text needs explicit entry and reconfirmation, and changing type clears it', async () => {
  stub('analyzeCsvTemplate', analysis)
  const prepare = stub('prepareCsvTemplate', { document })
  stub('previewSchema', preview)
  const page = await openCsv()
  await decide(page)
  const sku = within(screen.getByRole('region', { name: 'คอลัมน์ SKU' }))
  await page.user.click(sku.getByLabelText('ใช้ค่านี้ทุกแถว'))
  expect((sku.getByLabelText('ค่าที่ต้องการใช้ทุกแถว') as HTMLInputElement).value).toBe('')
  await page.user.click(sku.getByLabelText('ตรวจและยืนยันการตั้งค่าคอลัมน์นี้แล้ว'))
  expect((screen.getByRole('button', { name: 'ตรวจรูปแบบที่ยืนยันแล้ว' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.change(sku.getByLabelText('ค่าที่ต้องการใช้ทุกแถว'), { target: { value: '00123' } })
  expect((sku.getByLabelText('ตรวจและยืนยันการตั้งค่าคอลัมน์นี้แล้ว') as HTMLInputElement).checked).toBe(false)
  await page.user.click(sku.getByLabelText('ตรวจและยืนยันการตั้งค่าคอลัมน์นี้แล้ว'))
  await page.user.click(screen.getByRole('button', { name: 'ตรวจรูปแบบที่ยืนยันแล้ว' }))
  await screen.findByText('valid · catalog@1')
  expect(prepare).toHaveBeenCalledWith(expect.objectContaining({ columns: expect.arrayContaining([expect.objectContaining({ name: 'SKU', fixed_value: '00123', confirmed: true })]) }))
  fireEvent.change(sku.getByLabelText('ค่าที่ต้องการใช้ทุกแถว'), { target: { value: '00124' } })
  expect(screen.queryByText('valid · catalog@1')).toBeNull()
  expect((screen.getByRole('button', { name: /บันทึกรูปแบบ/ }) as HTMLButtonElement).disabled).toBe(true)
  await page.user.selectOptions(sku.getByLabelText('ชนิดข้อมูล'), 'integer')
  expect(sku.queryByLabelText('ค่าที่ต้องการใช้ทุกแถว')).toBeNull()
  await page.user.selectOptions(sku.getByLabelText('ชนิดข้อมูล'), 'string')
  expect((sku.getByLabelText('ใช้ค่านี้ทุกแถว') as HTMLInputElement).checked).toBe(false)
})

test('pending JSON preview cannot enable saving edited text', async () => {
  const response = deferred<typeof preview>()
  stub('previewSchema', preview).mockReturnValueOnce(response.promise)
  const page = await start()
  const editor = page.container.querySelector('textarea')!
  fireEvent.change(editor, { target: { value: JSON.stringify(document) } })
  await page.user.click(screen.getByRole('button', { name: 'ตรวจรูปแบบ' }))
  fireEvent.change(editor, { target: { value: '{}' } })
  await act(async () => response.resolve(preview))
  expect((screen.getByRole('button', { name: /บันทึกรูปแบบ/ }) as HTMLButtonElement).disabled).toBe(true)
  expect(screen.queryByText('valid · catalog@1')).toBeNull()
})

test('malformed CSV errors are visible and never enable saving', async () => {
  stub('analyzeCsvTemplate', analysis).mockRejectedValue(new Error('ชื่อคอลัมน์ซ้ำกัน'))
  const save = stub('registerSchema', preview)
  const page = await start()
  await page.user.click(screen.getByRole('button', { name: 'CSV ตัวอย่าง' }))
  fireEvent.change(screen.getByLabelText('ไฟล์ CSV ตัวอย่าง'), { target: { files: [csvFile()] } })
  await waitFor(() => expect((screen.getByRole('button', { name: 'อ่านแม่แบบ CSV' }) as HTMLButtonElement).disabled).toBe(false))
  await page.user.click(screen.getByRole('button', { name: 'อ่านแม่แบบ CSV' }))
  expect((await screen.findByRole('alert')).textContent).toContain('ชื่อคอลัมน์ซ้ำกัน')
  expect(save).not.toHaveBeenCalled()
})

test('switching to JSON ignores an old CSV response, and closing resets the draft', async () => {
  stub('analyzeCsvTemplate', analysis)
  const response = deferred<{ document: Record<string, unknown> }>()
  stub('prepareCsvTemplate', { document }).mockReturnValueOnce(response.promise)
  const check = stub('previewSchema', preview)
  const page = await openCsv()
  await decide(page)
  await page.user.click(screen.getByRole('button', { name: 'ตรวจรูปแบบที่ยืนยันแล้ว' }))
  await page.user.click(screen.getByRole('button', { name: 'JSON Schema' }))
  await act(async () => response.resolve({ document }))
  expect(check).not.toHaveBeenCalled()
  expect((page.container.querySelector('textarea') as HTMLTextAreaElement).value).toBe('')
  await page.user.click(screen.getByRole('button', { name: 'Close' }))
  await page.user.click(screen.getByRole('button', { name: /New format/ }))
  expect((page.container.querySelector('textarea') as HTMLTextAreaElement).value).toBe('')
})

test('replacing or changing the delimiter discards old column decisions', async () => {
  stub('analyzeCsvTemplate', analysis)
  const page = await openCsv()
  await decide(page)
  await page.user.selectOptions(screen.getByLabelText('ตัวคั่นคอลัมน์'), ';')
  expect(screen.queryByRole('region', { name: 'คอลัมน์ SKU' })).toBeNull()
  expect((screen.getByRole('button', { name: /บันทึกรูปแบบ/ }) as HTMLButtonElement).disabled).toBe(true)
})

test('a delayed JSON file read cannot overwrite a CSV draft or a manually edited JSON document', async () => {
  const page = await start()
  const response = deferred<string>()
  const file = new File(['{}'], 'schema.json', { type: 'application/json' })
  Object.defineProperty(file, 'text', { value: () => response.promise })
  fireEvent.change(page.container.querySelector('input[type=file]')!, { target: { files: [file] } })
  fireEvent.change(page.container.querySelector('textarea')!, { target: { value: '{"title":"new"}' } })
  await act(async () => response.resolve(JSON.stringify(document)))
  expect((page.container.querySelector('textarea') as HTMLTextAreaElement).value).toBe('{"title":"new"}')
  const second = deferred<string>()
  const otherFile = new File(['{}'], 'another.json', { type: 'application/json' })
  Object.defineProperty(otherFile, 'text', { value: () => second.promise })
  fireEvent.change(page.container.querySelector('input[type=file]')!, { target: { files: [otherFile] } })
  await page.user.click(screen.getByRole('button', { name: 'CSV ตัวอย่าง' }))
  await act(async () => second.resolve(JSON.stringify(document)))
  expect((screen.getByRole('button', { name: /บันทึกรูปแบบ/ }) as HTMLButtonElement).disabled).toBe(true)
  expect(screen.queryByRole('button', { name: 'ตรวจรูปแบบ' })).toBeNull()
})
