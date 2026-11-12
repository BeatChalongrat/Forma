import { test } from 'vitest'
import assert from 'node:assert/strict'
import { ReviewDraftStore, emptyReviewDraft } from '../src/lib/reviewDraft.ts'
import { reviewReturn, reviewToolUrl, latestReview } from '../src/lib/reviewNavigation.ts'

const defer = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
const remote = (revision, payload = {}) => ({ mapping_id: 'm1', revision, payload, updated_at: null })

test('failed initial read blocks edits and can be retried without losing the backup', async () => {
  let reads = 0, backup
  const payload = { ...emptyReviewDraft(), answers: { vat: false } }
  const store = new ReviewDraftStore({ read: async () => { if (++reads === 1) throw new Error('Offline'); return remote(2) }, write: async () => { throw new Error('Unexpected write') } }, value => backup = value, { revision: 1, payload })
  let notifications = 0
  const unsubscribe = store.subscribe(() => notifications++)
  await store.load()
  store.change(emptyReviewDraft())
  assert.equal(await store.flush(), false)
  assert.equal(store.snapshot().loading, true)
  await store.load()
  assert.deepEqual(store.snapshot().payload, payload)
  assert.equal(store.snapshot().conflict.revision, 2)
  assert.equal(await store.flush(), false)
  store.resolve(false)
  assert.deepEqual(store.snapshot().payload, emptyReviewDraft())
  assert.equal(store.snapshot().revision, 2)
  assert.equal(backup, null)
  assert.equal(await store.flush(), true)
  unsubscribe()
  const before = notifications
  store.resolve(false)
  store.clearBackup()
  assert.equal(notifications, before)
})

test('concurrent flush is single-flight and conflict read failure keeps unsent data', async () => {
  const pending = defer()
  let writes = 0, reads = 0, backup
  const store = new ReviewDraftStore({ read: async () => { if (++reads > 1) throw new Error('Offline'); return remote(0) }, write: async () => { writes++; await pending.promise; throw Object.assign(new Error('Conflict'), { status: 409 }) } }, value => backup = value)
  await store.load()
  store.change({ ...emptyReviewDraft(), answers: { vat: false } })
  const first = store.flush(), second = store.flush()
  assert.equal(first, second)
  pending.resolve()
  assert.equal(await first, false)
  assert.equal(writes, 1)
  assert.equal(store.snapshot().error, 'Conflict')
  assert.equal(backup.payload.answers.vat, false)
  assert.equal(store.snapshot().pending, true)
})

test('identical saved backup is discarded and partial remote payload receives defaults', async () => {
  const payload = emptyReviewDraft()
  let backup = 'not cleared'
  const store = new ReviewDraftStore({ read: async () => remote(2, {}), write: async () => { throw new Error('Unexpected write') } }, value => backup = value, { revision: 1, payload })
  await store.load()
  assert.equal(backup, null)
  assert.deepEqual(store.snapshot().payload, payload)
  assert.equal(store.snapshot().pending, false)
})

test('draft restores false, zero, uncertainty, remember, checks and the current step after reload', async () => {
  let saved = remote(0), backup
  const transport = { read: async () => saved, write: async (revision, payload) => saved = remote(revision + 1, payload) }
  const first = new ReviewDraftStore(transport, value => backup = value)
  await first.load()
  const payload = { ...emptyReviewDraft(), answers: { vat: false, stock: 0 }, remember: ['vat'], acknowledged: ['Check owner'], unsure: { unit: true }, section: 'finish', comment: 'Please check unit' }
  first.change(payload)
  assert.deepEqual(backup.payload, payload)
  await first.flush()
  assert.equal(backup, null)
  const reopened = new ReviewDraftStore(transport, () => {})
  await reopened.load()
  assert.deepEqual(reopened.snapshot().payload, payload)
})

test('a slow save never overwrites edits made while the request is in flight', async () => {
  const pending = defer(), writes = []
  const store = new ReviewDraftStore({ read: async () => remote(0), write: async (revision, payload) => {
    writes.push({ revision, payload }); if (revision === 0) await pending.promise
    return remote(revision + 1, payload)
  } }, () => {})
  await store.load()
  store.change({ ...emptyReviewDraft(), answers: { vat: true } })
  const flushing = store.flush()
  store.change({ ...emptyReviewDraft(), answers: { vat: false } })
  pending.resolve()
  assert.equal(await flushing, true)
  assert.deepEqual(writes.map(w => w.revision), [0, 1])
  assert.equal(store.snapshot().payload.answers.vat, false)
  assert.equal(store.snapshot().pending, false)
})

test('conflicting tabs cannot silently overwrite each other; keep-mine is explicit', async () => {
  let saved = remote(0)
  const transport = { read: async () => saved, write: async (revision, payload) => {
    if (revision !== saved.revision) throw Object.assign(new Error('Another tab saved'), { status: 409 })
    return saved = remote(revision + 1, payload)
  } }
  const first = new ReviewDraftStore(transport, () => {}), second = new ReviewDraftStore(transport, () => {})
  await Promise.all([first.load(), second.load()])
  first.change({ ...emptyReviewDraft(), answers: { vat: true } }); await first.flush()
  second.change({ ...emptyReviewDraft(), answers: { vat: false } })
  assert.equal(await second.flush(), false)
  assert.equal(saved.payload.answers.vat, true)
  assert.equal(second.snapshot().conflict.revision, 1)
  second.resolve(true)
  assert.equal(await second.flush(), true)
  assert.equal(saved.payload.answers.vat, false)
  assert.equal(saved.revision, 2)
})

test('a tab-local unsent answer survives refresh and failed requests', async () => {
  const payload = { ...emptyReviewDraft(), answers: { vat: false } }
  let backup
  const store = new ReviewDraftStore({ read: async () => remote(2), write: async () => { throw new Error('Offline') } }, value => backup = value, { revision: 2, payload })
  await store.load()
  assert.equal(store.snapshot().pending, true)
  assert.equal(await store.flush(), false)
  assert.deepEqual(backup.payload, payload)
  assert.equal(store.snapshot().payload.answers.vat, false)
})

test('opening the result/editor and returning preserves file, question, queue filter and page', () => {
  const review = '/review?mapping=m1&round=r1&section=%2Fprice%2Fvat&returnTo=%2Freview%3Fstatus%3Dopen%26offset%3D50'
  const url = new URL(reviewToolUrl('/rounds/r1/mapping/m1?tab=json', review), 'http://forma.local')
  assert.equal(reviewReturn(url.searchParams.get('reviewReturn')), review)
  const updated = new URL(latestReview(review, 'm2'), 'http://forma.local')
  assert.equal(updated.searchParams.get('mapping'), 'm2')
  assert.equal(updated.searchParams.get('returnTo'), '/review?status=open&offset=50')
  assert.equal(updated.searchParams.get('section'), 'finish')
  for (const invalid of ['https://example.com/review', '//example.com/review', '/review-other', '/review\\evil', null]) assert.equal(reviewReturn(invalid), '/review')
})
