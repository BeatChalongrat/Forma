import { test } from 'node:test'
import assert from 'node:assert/strict'
import { parseValue } from '../src/lib/valueInput.ts'
const field = (type, extra = {}) => ({ name: 'value', json_type: [type], nullable: false, enum: null, format: null, ...extra })
test('decimal and grouping follow the selected grammar, never silently lose commas', () => {
  assert.equal(parseValue(field('number'), '1,25').ok, false)
  assert.equal(parseValue(field('number'), '1,25', 'comma').value, 1.25)
  assert.equal(parseValue(field('number'), '1.234,56', 'comma').value, 1234.56)
  assert.equal(parseValue(field('number'), '1,234.56', 'dot').value, 1234.56)
  assert.equal(parseValue(field('number'), '1,25', 'dot').ok, false)
  assert.equal(parseValue(field('number'), '1e999').ok, false)
  assert.equal(parseValue(field('integer'), '1.2').ok, false)
  assert.equal(parseValue(field('integer'), '9007199254740993').ok, false)
  assert.equal(parseValue(field('number'), '9007199254740993').ok, false)
})
test('boolean, enum and dates must be explicit and valid', () => {
  assert.equal(parseValue(field('boolean'), 'perhaps').ok, false)
  assert.equal(parseValue(field('boolean'), 'no').value, false)
  assert.equal(parseValue(field('number', { enum: [2, 4] }), '3').ok, false)
  assert.equal(parseValue(field('string', { format: 'date' }), '2026-02-30').ok, false)
  assert.equal(parseValue(field('string', { format: 'date' }), '02/03/2026').ok, false)
  assert.equal(parseValue(field('string', { format: 'date' }), '2024-02-29').ok, true)
})
test('preserves strings and nested JSON, does not guess comma-separated lists', () => {
  assert.equal(parseValue(field('string'), '0012').value, '0012')
  assert.equal(parseValue(field('array'), 'a,b').ok, false)
  assert.deepEqual(parseValue(field('array'), '[{"price":1.25}]').value, [{ price: 1.25 }])
  assert.equal(parseValue(field('object'), '[]').ok, false)
  assert.equal(parseValue(field('number', { nullable: true }), '').value, null)
})
