import test from 'node:test'
import assert from 'node:assert/strict'
import { inspectionHref, inspectionParams, inspectionStatuses, inspectionFilters } from '../src/lib/inspection.ts'

test('selecting receipts and closing details preserves scope and filters without mutating history', () => {
  const before = new URLSearchParams({ search: 'VAT 7%', batch: 'Nov', scope: 'round', id: 'r1', cursor: 'older' })
  const selected = inspectionParams(before, { event: 'mapping:m1' })
  assert.equal(before.has('event'), false)
  assert.equal(selected.get('cursor'), 'older')
  const closed = inspectionParams(selected, { event: '' })
  assert.equal(closed.toString(), before.toString())
  assert.equal(selected.get('event'), 'mapping:m1')
})
test('report deep links select personal reports and encode identifiers safely', () => {
  const url = new URL(inspectionHref('chat', 'chat & id', 'job:report'), 'http://localhost')
  assert.equal(url.pathname, '/inspection')
  assert.equal(url.searchParams.get('kind'), 'assistant')
  assert.equal(url.searchParams.get('id'), 'chat & id')
  assert.equal(url.searchParams.get('event'), 'job:report')
  assert.notEqual(inspectionStatuses.done, inspectionStatuses.approved)
  assert.notEqual(inspectionStatuses.completed, inspectionStatuses.published)
})
test('empty date inputs are omitted so the initial list is not rejected as an invalid date', () => {
  const query = inspectionFilters({ start: '', end: '', search: '', offset: '0', kind: 'imports' })
  assert.equal(query.has('start'), false)
  assert.equal(query.has('end'), false)
  assert.equal(query.get('offset'), '0')
})
