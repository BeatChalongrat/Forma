// @vitest-environment jsdom
import { dataset, deferred, me, mountPage, round, rows, stub } from './pageHarness'
import { beforeEach, expect, test, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { ReviewDelivery } from '../src/components/ReviewDelivery'
import type { Me, ReviewQuestions } from '../src/lib/api'

const returnTo = '/review?mapping=m1&round=r1&section=ready'
beforeEach(() => { stub('round', round); stub('dataset', dataset); stub('rows', rows) })
function mount(roles = me.effective_roles, outdated = false) {
  const done = vi.fn()
  return { ...mountPage(<ReviewDelivery q={{ round_id: 'r1', mapping_id: 'm1', status: 'approved' } as ReviewQuestions} me={{ ...me, effective_roles: roles } as Me} outdated={outdated} returnTo={returnTo} onDone={done} />, '/review', returnTo), done }
}

test('approval waits for inspection, approves before export, and exposes only the returned JSON artifact', async () => {
  const pending = deferred<never>()
  const approve = stub('approveDataset', {}).mockImplementation(() => pending.promise)
  const exportFile = stub('exportDataset', { id: 'artifact-1', dataset_version_id: 'd1', fmt: 'json' })
  const { user, done } = mount()
  const button = await screen.findByRole('button', { name: 'Confirm data & prepare download' }) as HTMLButtonElement
  expect(button.disabled).toBe(true)
  expect(screen.queryByRole('link', { name: 'Download JSON' })).toBeNull()
  await user.click(screen.getByRole('checkbox'))
  await user.click(button)
  expect(approve).toHaveBeenCalledExactlyOnceWith('d1')
  expect(exportFile).not.toHaveBeenCalled()
  expect(button.disabled).toBe(true)
  pending.resolve({} as never)
  const link = await screen.findByRole('link', { name: 'Download JSON' })
  expect(exportFile).toHaveBeenCalledExactlyOnceWith('d1', 'json')
  expect(link.getAttribute('href')).toContain('artifact-1')
  await user.click(screen.getByRole('button', { name: 'Back to the review list' }))
  expect(done).toHaveBeenCalledTimes(1)
})

test('invalid records block approval and download; correction link keeps the exact return step', async () => {
  stub('dataset', { ...dataset, invalid_records: 1 })
  const approve = stub('approveDataset', {}), exportFile = stub('exportDataset', {})
  mount()
  const link = await screen.findByRole('link', { name: 'Open the data to correct it' })
  expect(new URL(link.getAttribute('href')!, 'http://test').searchParams.get('reviewReturn')).toBe(returnTo)
  expect(screen.queryByRole('checkbox')).toBeNull()
  expect(screen.queryByRole('link', { name: 'Download JSON' })).toBeNull()
  expect(approve).not.toHaveBeenCalled(); expect(exportFile).not.toHaveBeenCalled()
})

test('reader cannot approve; reviewer can confirm data but cannot export', async () => {
  const approve = stub('approveDataset', {}), exportFile = stub('exportDataset', {})
  const reader = mount(['reader'])
  await screen.findByText('Waiting for a person with approval/export permission.')
  expect(screen.queryByRole('button', { name: 'Confirm data' })).toBeNull()
  reader.unmount()
  const { user } = mount(['reviewer'])
  await screen.findByRole('button', { name: 'Confirm data' })
  await user.click(screen.getByRole('checkbox'))
  await user.click(screen.getByRole('button', { name: 'Confirm data' }))
  await waitFor(() => expect(approve).toHaveBeenCalledTimes(1))
  expect(exportFile).not.toHaveBeenCalled()
})

test.each([403, 409])('approval rejection %s cannot fall through to export or show success', async status => {
  stub('approveDataset', {}).mockRejectedValue(new Error(`Approval rejected ${status}`))
  const exportFile = stub('exportDataset', {})
  const { user } = mount()
  await screen.findByRole('checkbox')
  await user.click(screen.getByRole('checkbox'))
  await user.click(screen.getByRole('button', { name: 'Confirm data & prepare download' }))
  expect((await screen.findByRole('alert')).textContent).toContain(`Approval rejected ${status}`)
  expect(exportFile).not.toHaveBeenCalled()
  expect(screen.queryByText('Your file is ready')).toBeNull()
})

test('failed preparation retries its job, not a fresh mapping or transform', async () => {
  stub('round', { ...round, datasets: [], jobs: [{ id: 'job-1', kind: 'finish_review', status: 'failed', can_retry: true }] })
  const retry = stub('retryJob', {}), transform = stub('transform', {})
  const { user } = mount()
  await user.click(await screen.findByRole('button', { name: 'Try preparing the file again' }))
  await waitFor(() => expect(retry).toHaveBeenCalledExactlyOnceWith('job-1'))
  expect(transform).not.toHaveBeenCalled()
})

test('a replaced dataset loses the old confirmation; previous versions remain read-only', async () => {
  const { user, client, unmount } = mount()
  await user.click(await screen.findByRole('checkbox'))
  expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(true)
  stub('dataset', { ...dataset, id: 'd2', version: 2 })
  client.setQueryData(['review-round', 'r1'], { ...round, summary: { ...round.summary, dataset_version_id: 'd2' }, datasets: [{ id: 'd2', mapping_version_id: 'm1' }] })
  await screen.findByText('2 records · data version 2')
  expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(false)
  expect((screen.getByRole('button', { name: 'Confirm data & prepare download' }) as HTMLButtonElement).disabled).toBe(true)
  unmount(); mount(me.effective_roles, true)
  await screen.findByText('This version is read-only')
  expect(screen.queryByRole('checkbox')).toBeNull()
})

test('a failed sample load cannot be confirmed until Retry retrieves the real sample', async () => {
  stub('rows', rows).mockRejectedValueOnce(new Error('Unavailable sample'))
  const { user } = mount()
  await screen.findByRole('alert')
  await user.click(screen.getByRole('checkbox'))
  expect((screen.getByRole('button', { name: 'Confirm data & prepare download' }) as HTMLButtonElement).disabled).toBe(true)
  await user.click(screen.getByRole('button', { name: 'Retry' }))
  await screen.findByText('SKU-001')
  expect((screen.getByRole('button', { name: 'Confirm data & prepare download' }) as HTMLButtonElement).disabled).toBe(false)
})
