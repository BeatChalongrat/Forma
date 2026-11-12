// @vitest-environment jsdom
import { mountPage, stub, toast } from './pageHarness'
import { beforeEach, expect, test } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { UploadPage } from '../src/pages/Upload'

const item = { id: 'f1', file_name: 'products.csv', size: 12, step: 3, state: 'needs_review', round_id: 'r1', mapping_version_id: 'm1', customer: { code: 'C1', id: 'c1' }, row_count: 2, message: 'Confirm VAT' }
const batch = { id: 'b1', label: 'November', work_date: '2026-11-12', created_at: '2026-11-12T00:00:00Z', total: 3, expected_files: 300, stuck: 0, done: false, complete: false, counts: { processing: 1, needs_review: 1, ready: 1 }, items: [item, { ...item, id: 'f2', file_name: 'stock.csv', state: 'processing', step: 1 }, { ...item, id: 'f3', file_name: 'prices.csv', state: 'ready', export_id: 'e1', json_name: 'prices.json' }] }
beforeEach(() => { stub('upload', batch); stub('customers', [{ id: 'c1', code: 'C1', name: 'Customer One' }]) })
function mount() { return mountPage(<UploadPage />, '/uploads/:batchId', '/uploads/b1') }

test('round separates received, processing and ready files and routes each file to its next action', async () => {
  const { user, router } = mount()
  const reviewRow = (await screen.findByText('products.csv')).closest('tr')!
  expect(within(reviewRow).getByRole('link', { name: 'Answer questions' }).getAttribute('href')).toBe('/review?round=r1')
  const readyRow = screen.getByText('prices.csv').closest('tr')!
  expect(within(readyRow).getByRole('link', { name: 'JSON' }).getAttribute('href')).toContain('e1')
  expect(within(screen.getByText('stock.csv').closest('tr')!).queryByRole('link', { name: 'JSON' })).toBeNull()
  expect(screen.getByRole('progressbar', { name: 'Receipt progress' }).getAttribute('aria-valuenow')).toBe('3')
  expect(screen.getByRole('progressbar', { name: 'Receipt progress' }).getAttribute('aria-valuemax')).toBe('300')
  await user.click(screen.getByRole('button', { name: 'Needs you' }))
  expect(screen.queryByText('stock.csv')).toBeNull()
  expect(router.state.location.search).toBe('?state=needs_you')
  await user.click(screen.getByRole('link', { name: 'Add files to this round' }))
  expect(router.state.location.state.uploadReturnTo).toBe('/uploads/b1?state=needs_you')
})

test('no ready files disables the ZIP link and failed receipt fetch can retry', async () => {
  const upload = stub('upload', { ...batch, counts: { processing: 3 }, items: [] }).mockRejectedValueOnce(new Error('Offline'))
  const { user } = mount()
  await user.click(await screen.findByRole('button', { name: 'Could not load round. Retry' }))
  const link = await screen.findByRole('link', { name: 'Download ready files (0)' })
  expect(link.getAttribute('aria-disabled')).toBe('true')
  expect(link.getAttribute('tabindex')).toBe('-1')
  expect(upload).toHaveBeenCalledTimes(2)
})

test('stuck work resumes the existing batch; failed resume remains recoverable', async () => {
  stub('upload', { ...batch, stuck: 1 })
  const resume = stub('resumeUpload', { ...batch, stuck: 0 }).mockRejectedValueOnce(new Error('Worker unavailable'))
  const { user } = mount()
  await user.click(await screen.findByRole('button', { name: 'Resume' }))
  await waitFor(() => expect(toast.push).toHaveBeenCalledWith(expect.objectContaining({ kind: 'bad', title: 'Not resumed' })))
  await user.click(screen.getByRole('button', { name: 'Resume' }))
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Resume' })).toBeNull())
  expect(resume.mock.calls).toEqual([['b1'], ['b1']])
})

test('unknown file requires explicit file and customer selection; denied assignment does not claim success', async () => {
  stub('upload', { ...batch, counts: { needs_customer: 1 }, items: [{ ...item, state: 'needs_customer', customer: null }] })
  const assign = stub('assignUpload', {}).mockRejectedValue(new Error('403: customer not allowed'))
  const { user } = mount()
  const button = await screen.findByRole('button', { name: /^Assign/ }) as HTMLButtonElement
  expect(button.disabled).toBe(true)
  await user.click(screen.getByRole('checkbox'))
  expect(button.disabled).toBe(true)
  await user.selectOptions(screen.getByRole('combobox'), 'c1')
  await user.click(screen.getByRole('button', { name: 'Assign 1 file' }))
  await waitFor(() => expect(toast.push).toHaveBeenCalledWith(expect.objectContaining({ kind: 'bad', title: 'Not assigned' })))
  expect(assign).toHaveBeenCalledExactlyOnceWith('b1', { item_ids: ['f1'], customer_id: 'c1' })
  expect(screen.getByRole('button', { name: 'Assign 1 file' })).toBeTruthy()
})
