// @vitest-environment jsdom
import { expect, test, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { mountPage, stub } from './pageHarness'
import { PilotQuestionFeedback } from '../src/components/PilotQuestionFeedback'
import { api } from '../src/lib/api'

test('pilot feedback requires a source location, persists, and never answers the business question', async () => {
  stub('pilotObservations', { enabled: true, latest: {}, history: [] })
  const saved = { id: 'o1', question_id: 'vat', category: 'evidence_in_file', note: 'Header says VAT included', evidence_location: 'Products!G1', actor: 'reviewer', created_at: '2026-11-14T00:00:00Z' }
  stub('savePilotObservation', { enabled: true, latest: { vat: saved }, history: [saved] })
  const answer = vi.spyOn(api, 'confirmReview')
  const { user, unmount } = mountPage(<PilotQuestionFeedback mappingId="m1" questionId="vat" canEdit />, '/review', '/review')
  await user.selectOptions(await screen.findByLabelText('Reason'), 'evidence_in_file')
  await user.type(screen.getByLabelText('What did you check / what is missing?'), saved.note)
  expect((screen.getByRole('button', { name: 'Save observation' }) as HTMLButtonElement).disabled).toBe(true)
  await user.type(screen.getByLabelText('File, sheet and cells / column'), saved.evidence_location)
  await user.click(screen.getByRole('button', { name: 'Save observation' }))
  await waitFor(() => expect(api.savePilotObservation).toHaveBeenCalledWith('m1', { question_id: 'vat', category: saved.category, note: saved.note, evidence_location: saved.evidence_location }))
  await screen.findByText(/Saved by/)
  expect(answer).not.toHaveBeenCalled()
  unmount()
  vi.mocked(api.pilotObservations).mockResolvedValue({ enabled: true, latest: { vat: saved }, history: [saved] })
  mountPage(<PilotQuestionFeedback mappingId="m1" questionId="vat" canEdit={false} />, '/review', '/review')
  expect((await screen.findByLabelText('Reason') as HTMLSelectElement).value).toBe('evidence_in_file')
  expect((screen.getByRole('group') as HTMLFieldSetElement).disabled).toBe(true)
})

test('failed feedback save stays editable and does not claim success', async () => {
  stub('pilotObservations', { enabled: true, latest: {}, history: [] })
  stub('savePilotObservation', {}).mockRejectedValue(new Error('Connection lost'))
  const { user } = mountPage(<PilotQuestionFeedback mappingId="m1" questionId="vat" canEdit />, '/review', '/review')
  await user.click(await screen.findByRole('button', { name: 'Save observation' }))
  expect((await screen.findByRole('alert')).textContent).toContain('Connection lost')
  expect(screen.queryByText(/Saved by/)).toBeNull()
})
