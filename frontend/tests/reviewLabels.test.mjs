import { test } from 'vitest'
import assert from 'node:assert/strict'
import { answerLabel, answerScope } from '../src/lib/reviewLabels.ts'
const th = (_en, th) => th
const q = (semantic, extra = {}) => ({ kind: 'boolean', field: { semantic }, ...extra })
test('boolean labels refer to VAT only for VAT fields', () => {
  assert.equal(answerLabel(q('price.vat_included'), false, th), 'ยังไม่รวม VAT')
  assert.equal(answerLabel(q('is_active'), false, th), 'ไม่ใช่')
  assert.equal(answerLabel(q('is_active'), true, th), 'ใช่')
})
test('units and enum choices retain the actual value alongside readable labels', () => {
  assert.equal(answerLabel(q('unit.sell'), 'BOTTLE', th), 'ขวด (BOTTLE)')
  assert.equal(answerLabel(q('custom'), 'BOTTLE', th), 'BOTTLE')
  assert.equal(answerLabel(q('date', { option_labels: { DMY: 'วัน / เดือน / ปี' } }), 'DMY', th), 'วัน / เดือน / ปี')
})
test('unanswered is never converted into false or confirmed', () => {
  assert.equal(answerLabel(q('is_active'), undefined, th), 'ยังไม่ตอบ')
  assert.match(answerScope(q('unit.sell', { scope: 'source_value', source_value: 'BT' }), true, th), /เฉพาะรหัส “BT”.*จำหลังอนุมัติ/)
})
