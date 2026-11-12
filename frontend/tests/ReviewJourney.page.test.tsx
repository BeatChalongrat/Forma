// @vitest-environment jsdom
import { fields, me, mountPage, round, rows, stub } from './pageHarness'
import { beforeEach, expect, test, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { ReviewJourney } from '../src/components/ReviewJourney'
import { emptyReviewDraft } from '../src/lib/reviewDraft'
import { api, type ReviewDraftPayload } from '../src/lib/api'

const questions = {
  mapping_id: 'm1', round_id: 'r1', status: 'proposed', blocked: false,
  questions: [
    { id: 'vat', kind: 'boolean', question: 'Is VAT included?', scope: 'constant_for_this_file', field: { ...fields[1], pointer: '/vat', semantic: 'price.vat_included' }, current: { op: 'const', value: false }, evidence: [] },
    { id: 'pack', kind: 'number', field: { ...fields[1], pointer: '/pack', semantic: 'unit.pack_size', json_type: ['integer'] }, current: { op: 'const', value: 1 }, evidence: [] },
  ],
  context: { customer: { code: 'C1', name: 'Customer One' }, files: [{ id: 'f1', name: 'products.xlsx' }], round_label: 'November', mapping_version: 1 },
  open_questions: [], reasons: [], changes: [], checks: {}, fields,
}
const preview = { digest: 'digest-1', remaining_questions: [], preview: { ok: true, records: rows.rows, findings: [] }, comparison: { reliable: true, validation: { passed: true }, before_records: 2, after_records: 2, counts: { changed_records: 1, lost: 0, gained: 0 }, sample_limit: 50, rows: [] } }
let saved: { revision: number; payload: ReviewDraftPayload }
beforeEach(() => {
  saved = { revision: 0, payload: emptyReviewDraft() }
  stub('me', me); stub('reviewQuestions', questions); stub('round', round)
  stub('reviewEvidence', { examples: [], unavailable: true })
  stub('reviewDraft', saved).mockImplementation(async () => structuredClone(saved) as never)
  stub('saveReviewDraft', {}).mockImplementation(async (_id, revision, payload) => { saved = { revision: revision + 1, payload: structuredClone(payload) }; return saved as never })
  stub('reviewPreview', preview)
  stub('confirmReview', { mapping_id: 'm2', state: 'processing', job_id: 'job-2' })
})
function mount(section = '') {
  const onSaved = vi.fn(), onDone = vi.fn()
  return { ...mountPage(<ReviewJourney id="m1" onSaved={onSaved} onDone={onDone} />, '/review', '/review?mapping=m1&round=r1' + (section ? `&section=${section}` : '')), onSaved, onDone }
}
async function answerBoth(user: ReturnType<typeof mount>['user']) {
  await user.click(await screen.findByRole('button', { name: 'VAT included' }))
  await user.click(screen.getByRole('button', { name: 'Next' }))
  await user.clear(screen.getByRole('spinbutton')); await user.type(screen.getByRole('spinbutton'), '12')
  await user.click(screen.getByRole('button', { name: 'Check the result' }))
  await screen.findByText('SKU-001')
}

test('answers survive Previous, evidence panel, preview, and leave/resume without confirming', async () => {
  const first = mount()
  await answerBoth(first.user)
  expect(api.confirmReview).not.toHaveBeenCalled()
  await first.user.click(screen.getByRole('button', { name: 'Previous step' }))
  expect((screen.getByRole('spinbutton') as HTMLInputElement).value).toBe('12')
  await first.user.click(screen.getByRole('button', { name: 'File checks' }))
  await first.user.click(screen.getByRole('button', { name: 'Back to this file’s current step' }))
  expect((screen.getByRole('spinbutton') as HTMLInputElement).value).toBe('12')
  await first.user.click(screen.getByRole('button', { name: 'Save draft & leave' }))
  await waitFor(() => expect(first.onDone).toHaveBeenCalledTimes(1))
  expect(saved.payload.answers).toEqual({ vat: true, pack: 12 })
  first.unmount()
  mount()
  await waitFor(() => expect((screen.getByRole('spinbutton') as HTMLInputElement).value).toBe('12'))
  expect(api.confirmReview).not.toHaveBeenCalled()
})

test('explicit confirmation flushes answers and submits the preview digest exactly once', async () => {
  const { user, onSaved } = mount()
  await answerBoth(user)
  const button = screen.getByRole('button', { name: 'Confirm & prepare file' }) as HTMLButtonElement
  expect(button.disabled).toBe(true)
  await user.click(screen.getByLabelText('I checked the source, answers, and result. They are correct.'))
  await user.click(button)
  await waitFor(() => expect(onSaved).toHaveBeenCalledWith('m2', 'ready', 'job-2'))
  expect(api.confirmReview).toHaveBeenCalledExactlyOnceWith('m1', expect.objectContaining({ answers: { vat: true, pack: 12 }, digest: 'digest-1' }))
  expect(saved.payload.answers).toEqual({ vat: true, pack: 12 })
})

test('Not sure removes an answer and learning consent; remaining questions cannot masquerade as approval', async () => {
  const { user } = mount()
  await user.click(await screen.findByRole('button', { name: 'VAT included' }))
  await user.click(screen.getByRole('checkbox'))
  await user.click(screen.getByRole('button', { name: 'Not sure yet' }))
  await screen.findByText('This stays unanswered. Other answers are kept, and you can come back later.')
  await user.click(screen.getByRole('button', { name: 'Next' }))
  await user.type(screen.getByRole('spinbutton'), '12')
  await user.click(screen.getByRole('button', { name: 'Check the result' }))
  await screen.findByText('SKU-001')
  expect(screen.queryByRole('button', { name: 'Confirm & prepare file' })).toBeNull()
  await user.click(screen.getByRole('button', { name: 'Save confirmed answers' }))
  await waitFor(() => expect(api.confirmReview).toHaveBeenCalledWith('m1', expect.objectContaining({ answers: { pack: 12 }, remember: [] })))
})

test.each(['unreliable', 'invalid', 'network'])('preview %s blocks confirmation and retains the answers', async problem => {
  if (problem === 'network') vi.mocked(api.reviewPreview).mockRejectedValue(new Error('Preview unavailable'))
  else vi.mocked(api.reviewPreview).mockResolvedValue({ ...preview, comparison: { ...preview.comparison, reliable: problem !== 'unreliable', validation: { passed: problem !== 'invalid' } } } as never)
  const { user } = mount()
  await user.click(await screen.findByRole('button', { name: 'VAT included' }))
  await user.click(screen.getByRole('button', { name: 'Next' }))
  await user.type(screen.getByRole('spinbutton'), '12')
  await user.click(screen.getByRole('button', { name: 'Check the result' }))
  if (problem === 'invalid') await screen.findByText('The check did not pass. Inspect the problems before confirming.')
  else await screen.findByRole('alert')
  const check = screen.getByLabelText('I checked the source, answers, and result. They are correct.')
  await user.click(check)
  const button = screen.getByRole('button', { name: 'Confirm & prepare file' }) as HTMLButtonElement
  expect(button.disabled).toBe(true)
  expect(api.confirmReview).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'Previous step' }))
  expect((screen.getByRole('spinbutton') as HTMLInputElement).value).toBe('12')
})

test('conflicting draft displays both versions and blocks writes until the user chooses', async () => {
  const remote = { revision: 8, payload: { ...emptyReviewDraft(), answers: { vat: false }, section: 'vat' } }
  vi.mocked(api.saveReviewDraft).mockRejectedValue(Object.assign(new Error('Changed in another tab'), { status: 409 }))
  const { user } = mount()
  await user.click(await screen.findByRole('button', { name: 'VAT included' }))
  vi.mocked(api.reviewDraft).mockResolvedValue(remote as never)
  await screen.findByText('Choose which draft to continue with')
  expect(api.confirmReview).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'Use the saved draft' }))
  expect(screen.getByRole('button', { name: 'VAT excluded' }).getAttribute('aria-pressed')).toBe('true')
  expect(screen.queryByText('Choose which draft to continue with')).toBeNull()
})

test('read-only user and superseded version cannot submit review decisions', async () => {
  vi.mocked(api.me).mockResolvedValue({ ...me, effective_roles: ['reader'] } as never)
  const first = mount()
  await screen.findByRole('button', { name: 'VAT included' })
  await first.user.click(screen.getByRole('button', { name: 'VAT included' }))
  expect(api.confirmReview).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: 'VAT included' }).getAttribute('aria-pressed')).toBe('false')
  first.unmount()
  vi.mocked(api.me).mockResolvedValue(me as never)
  vi.mocked(api.round).mockResolvedValue({ ...round, summary: { mapping_version_id: 'm2' } } as never)
  const { user, onSaved } = mount()
  await user.click(await screen.findByRole('button', { name: 'Open latest version' }))
  expect(onSaved).toHaveBeenCalledWith('m2', 'finish')
  expect(api.confirmReview).not.toHaveBeenCalled()
})

test('changing an answer invalidates the previous confirmation and uses a fresh preview digest', async () => {
  const { user } = mount()
  await answerBoth(user)
  await user.click(screen.getByLabelText('I checked the source, answers, and result. They are correct.'))
  await user.click(screen.getAllByRole('button', { name: 'Change answer' })[0])
  await user.click(screen.getByRole('button', { name: 'VAT excluded' }))
  vi.mocked(api.reviewPreview).mockResolvedValue({ ...preview, digest: 'digest-2' } as never)
  await user.click(screen.getByRole('button', { name: 'Check & confirm' }))
  await waitFor(() => expect(api.reviewPreview).toHaveBeenLastCalledWith('m1', expect.objectContaining({ answers: { vat: false, pack: 12 } })))
  await screen.findByText('SKU-001')
  expect((screen.getByLabelText('I checked the source, answers, and result. They are correct.') as HTMLInputElement).checked).toBe(false)
  expect((screen.getByRole('button', { name: 'Confirm & prepare file' }) as HTMLButtonElement).disabled).toBe(true)
  await user.click(screen.getByLabelText('I checked the source, answers, and result. They are correct.'))
  await user.click(screen.getByRole('button', { name: 'Confirm & prepare file' }))
  await waitFor(() => expect(api.confirmReview).toHaveBeenCalledWith('m1', expect.objectContaining({ digest: 'digest-2', answers: { vat: false, pack: 12 } })))
})
