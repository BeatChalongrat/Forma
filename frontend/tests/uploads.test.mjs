import { test } from 'node:test'
import assert from 'node:assert/strict'
import { isReadable, refusedNames, splitReadable, UPLOAD_ACCEPT } from '../src/lib/uploads.ts'

const file = (name) => ({ name })

test('every kind of file the backend reads can be picked, in any case', () => {
  for (const name of ['a.xlsx', 'a.xlsm', 'a.xls', 'a.xlsb', 'a.csv', 'a.txt', 'a.tsv', 'A.XLS', 'สินค้า.Xlsb']) assert.equal(isReadable(name), true, name)
  assert.equal(UPLOAD_ACCEPT, '.xlsx,.xlsm,.xls,.xlsb,.csv,.txt,.tsv')
})

test('a file FORMA cannot read is refused, not lost', () => {
  const { readable, refused } = splitReadable([file('a.xls'), file('report.pdf'), file('b.csv'), file('note')])
  assert.deepEqual(readable.map((f) => f.name), ['a.xls', 'b.csv'])
  assert.deepEqual(refused.map((f) => f.name), ['report.pdf', 'note'])
})

test('a long list of refused files names the first few and counts the rest', () => {
  assert.deepEqual(refusedNames([file('a.pdf')]), { names: 'a.pdf', more: 0 })
  assert.deepEqual(refusedNames(['a', 'b', 'c', 'd', 'e'].map((n) => file(`${n}.pdf`))), { names: 'a.pdf, b.pdf, c.pdf', more: 2 })
})
