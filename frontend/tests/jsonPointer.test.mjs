import { test } from 'vitest'
import assert from 'node:assert/strict'
import { getPointer, setPointerLocal } from '../src/lib/jsonPointer.ts'

test('nested edits retain array positions and decode JSON pointer keys exactly once', () => {
  const row = { items: [{ 'a/b': { '~key': 10 } }], '': 12 }
  setPointerLocal(row, '/items/0/a~1b/~0key', 42)
  assert.equal(getPointer(row, '/items/0/a~1b/~0key'), 42)
  assert.equal(getPointer(row, '/'), 12)
  setPointerLocal(row, '/literal~01', 'value')
  assert.equal(row['literal~1'], 'value')
  assert.equal(getPointer(row, ''), row)
})

test('JSON keys cannot change inherited prototypes', () => {
  const row = {}
  setPointerLocal(row, '/__proto__/formaPolluted', true)
  assert.equal({}.formaPolluted, undefined)
  assert.equal(getPointer(row, '/__proto__/formaPolluted'), true)
  assert.equal(getPointer({}, '/constructor'), undefined)
})
