import test from 'node:test'
import assert from 'node:assert/strict'
import { ReviewDraftStore, emptyReviewDraft } from '../src/lib/reviewDraft.ts'
import { reviewReturn, reviewToolUrl, latestReview } from '../src/lib/reviewNavigation.ts'

const defer = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
const remote = (revision, payload = {}) => ({ mapping_id: 'm1', revision, payload, updated_at: null })

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
