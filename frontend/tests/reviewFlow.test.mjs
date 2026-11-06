import test from 'node:test'
import assert from 'node:assert/strict'
import { reviewFlow, progressLabel } from '../src/lib/reviewFlow.ts'

const question = (id, prompt = id) => ({ id, question: prompt })
const review = extra => ({ status: 'proposed', blocked: false, questions: [question('vat'), question('stock')], open_questions: [], checks: {}, ...extra })

test('starts at the first question, without marking unknown answers complete', () => {
  const flow = reviewFlow(review())
  assert.equal(flow.defaultSection, 'vat')
  assert.equal(flow.remaining, 2)
  assert.equal(flow.canApprove, false)
  assert.equal(flow.hasDraft, false)
})
test('false and zero are drafts; whitespace, stale fields and invalid numbers are not', () => {
  const flow = reviewFlow(review(), { vat: false, stock: 0, stale: true })
  assert.equal(flow.drafts, 2)
  assert.equal(flow.remaining, 0)
  assert.equal(flow.stage, 2)
  assert.equal(flow.saved, 0)
  assert.equal(flow.canApprove, false)
  assert.equal(reviewFlow(review(), { vat: '  ', stock: NaN }).drafts, 0)
})
test('a partial preview never completes unanswered questions or enables approval', () => {
  const flow = reviewFlow(review(), { vat: false }, [], {})
  assert.equal(flow.stage, 2)
  assert.equal(flow.remaining, 1)
  assert.equal(flow.canApprove, false)
})
test('structured prompts and duplicate notes are counted once; acknowledging is still a draft', () => {
  const q = review({ open_questions: ['vat', 'Check sender', 'Check sender'] })
  const flow = reviewFlow(q, { vat: true }, ['Check sender', 'stale'])
  assert.equal(flow.pending, 3)
  assert.equal(flow.remaining, 1)
  assert.equal(flow.acknowledgedCount, 1)
  assert.equal(flow.canApprove, false)
})
test('a saved version with no questions opens final review, not an empty question step', () => {
  const flow = reviewFlow(review({ questions: [], progress: { saved_answers: 2 } }))
  assert.equal(flow.defaultSection, 'finish')
  assert.equal(flow.stage, 3)
  assert.equal(flow.saved, 2)
  assert.equal(flow.hasDraft, false)
  assert.equal(flow.canApprove, true)
})
test('unresolved notes and failed file checks prevent approval even with no questions', () => {
  assert.equal(reviewFlow(review({ questions: [], open_questions: ['Check sender'] })).canApprove, false)
  for (const checks of [{ verification: { complete: false, passed: true } }, { verification: { complete: true, passed: false } }]) {
    const flow = reviewFlow(review({ questions: [], checks }))
    assert.equal(flow.canApprove, false)
    assert.equal(flow.defaultSection, 'issues')
  }
  assert.equal(reviewFlow(review({ blocked: true, questions: [] })).canApprove, false)
})
test('reviewed versions remain read-only and progress does not call a proposal approved', () => {
  const th = (_en, th) => th
  assert.equal(reviewFlow(review({ status: 'approved', questions: [] })).canApprove, false)
  assert.equal(progressLabel({ state: 'final_review', remaining_questions: 0, remaining_notes: 0 }, th), 'รอตรวจผลและอนุมัติ')
  assert.equal(progressLabel({ state: 'approved' }, th), 'อนุมัติแล้ว')
})
