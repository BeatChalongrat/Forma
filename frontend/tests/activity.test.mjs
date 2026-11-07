import test from 'node:test'
import assert from 'node:assert/strict'
import { station, isLive, activityLabel } from '../src/lib/activity.ts'

const now = Date.parse('2026-11-07T10:00:00Z')
const job = { status: 'running', attempts: 2, lease_expires_at: '2026-11-07T10:01:00Z', latest: { role: 'mapper', attempt: 2 } }
test('only a current attempt with a valid lease appears as working', () => {
  assert.equal(station(job, now), 'mapper')
  assert.equal(station({ ...job, latest: { role: 'analyst', attempt: 1 } }, now), 'system')
  assert.equal(station({ ...job, lease_expires_at: '2026-11-07T09:00:00Z' }, now), 'system')
  assert.equal(isLive({ ...job, status: 'failed' }, now), false)
})
test('completed processing is not automatically a ready-to-download file', () => {
  assert.equal(station({ ...job, status: 'done', round_status: 'review' }, now), 'human')
  assert.equal(station({ ...job, status: 'done', round_status: 'published' }, now), 'ready')
  assert.equal(station({ ...job, status: 'failed', round_status: 'published' }, now), 'system')
  assert.equal(station({ ...job, status: 'queued', round_status: 'published' }, now), 'system')
  assert.equal(station({ ...job, lease_expires_at: '2026-11-07T09:00:00Z', round_status: 'published' }, now), 'system')
  assert.equal(station({ ...job, status: 'done' }, now), 'system')
  assert.equal(station({ ...job, status: 'done', result: { state: 'needs_customer' } }, now), 'human')
})
test('bubble uses observed tool progress and never invents reasoning', () => {
  assert.equal(activityLabel({ action: 'progress', detail: { message: 'Reading Sheet 2' } }), 'Reading Sheet 2')
  assert.equal(activityLabel(null), 'ยังไม่มีบันทึกกิจกรรม')
})
