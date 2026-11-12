// @vitest-environment jsdom
import { deferred, mountPage, stub, toast } from './pageHarness'
import { beforeEach, expect, test } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { BulkUpload } from '../src/pages/Upload'

beforeEach(() => { stub('uploads', []); stub('upload', { id: 'batch-1', label: 'Existing round', auto_publish: false }) })
function mount(url = '/imports/new') { return mountPage(<BulkUpload />, '/imports/new', url) }
function add(container: HTMLElement, count = 1) {
  const files = Array.from({ length: count }, (_, i) => new File(['sku,price\nA,10'], `products-${i}.csv`, { type: 'text/csv' }))
  fireEvent.change(container.querySelector('input[type=file]')!, { target: { files } })
  return files
}

test('upload requires a readable file and date; rejected files never reach the API', async () => {
  const create = stub('createUpload', {})
  const { container, user } = mount()
  expect((screen.getByRole('button', { name: /^Upload .*files$/ }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.change(container.querySelector('input[type=file]')!, { target: { files: [new File(['x'], 'virus.exe')] } })
  expect(toast.push).toHaveBeenCalledWith(expect.objectContaining({ kind: 'bad' }))
  expect(screen.queryByText('virus.exe')).toBeNull()
  add(container)
  const date = screen.getByLabelText('Round date')
  fireEvent.change(date, { target: { value: '' } })
  await user.click(screen.getByRole('button', { name: 'Upload 1 files' }))
  expect(create).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'Remove' }))
  expect(screen.queryByText('products-0.csv')).toBeNull()
})

test('21 files are sent in bounded chunks, pending blocks duplicate submit, then opens the receipt', async () => {
  const pending = deferred<{ id: string; skipped: number }>()
  const create = stub('createUpload', {}).mockImplementation(() => pending.promise as never)
  const append = stub('appendUpload', { skipped: 1 })
  const { container, user, router } = mount()
  const files = add(container, 21)
  await user.type(screen.getByLabelText('Round name (optional)'), 'November delivery')
  await user.click(screen.getByRole('button', { name: 'Upload 21 files' }))
  const busy = screen.getByRole('button', { name: 'Uploading 21 files…' }) as HTMLButtonElement
  expect(busy.disabled).toBe(true)
  await user.click(busy)
  expect(create).toHaveBeenCalledTimes(1)
  expect(create.mock.calls[0][0]).toEqual(files.slice(0, 10))
  expect(create.mock.calls[0][1]).toMatchObject({ label: 'November delivery', auto_publish: true, upload_key: expect.any(String) })
  pending.resolve({ id: 'batch-1', skipped: 0 })
  await waitFor(() => expect(router.state.location.pathname).toBe('/uploads/batch-1'))
  expect(append.mock.calls.map(call => call[1].length)).toEqual([10, 1])
  expect(append.mock.calls.every(call => call[0] === 'batch-1')).toBe(true)
})

test('lost first response reuses the same idempotency key and retains selected files', async () => {
  const create = stub('createUpload', { id: 'batch-1', skipped: 0 }).mockRejectedValueOnce(new Error('Network offline'))
  const { container, user, router } = mount()
  add(container)
  await user.click(screen.getByRole('button', { name: 'Upload 1 files' }))
  await screen.findByRole('alert')
  expect(screen.getByText('products-0.csv')).toBeTruthy()
  await user.click(screen.getByRole('button', { name: 'Upload 1 files' }))
  await waitFor(() => expect(router.state.location.pathname).toBe('/uploads/batch-1'))
  expect(create.mock.calls[1][1].upload_key).toBe(create.mock.calls[0][1].upload_key)
})

test('interrupted append continues in the same round without creating a second round', async () => {
  const create = stub('createUpload', { id: 'batch-1', skipped: 0 })
  const append = stub('appendUpload', { skipped: 0 }).mockRejectedValueOnce(new Error('Connection reset'))
  const { container, user, router } = mount()
  add(container, 11)
  await user.click(screen.getByRole('button', { name: 'Upload 11 files' }))
  await screen.findByRole('alert')
  await waitFor(() => expect((screen.getByRole('button', { name: 'Upload 11 files' }) as HTMLButtonElement).disabled).toBe(false))
  await user.click(screen.getByRole('button', { name: 'Upload 11 files' }))
  await waitFor(() => expect(router.state.location.pathname).toBe('/uploads/batch-1'))
  expect(create).toHaveBeenCalledTimes(1)
  expect(append.mock.calls.every(call => call[0] === 'batch-1')).toBe(true)
})

test('adding to an existing round respects its approval setting', async () => {
  const create = stub('createUpload', {})
  const append = stub('appendUpload', { skipped: 0 })
  const { container, user, router } = mount('/imports/new?batch=batch-1')
  add(container)
  await waitFor(() => expect((screen.getByRole('button', { name: 'Upload 1 files' }) as HTMLButtonElement).disabled).toBe(false))
  const auto = screen.getByRole('checkbox') as HTMLInputElement
  expect(auto.disabled).toBe(true); expect(auto.checked).toBe(false)
  await user.click(screen.getByRole('button', { name: 'Upload 1 files' }))
  await waitFor(() => expect(router.state.location.pathname).toBe('/uploads/batch-1'))
  expect(create).not.toHaveBeenCalled(); expect(append).toHaveBeenCalledTimes(1)
})

test.each(['/imports/new', '/imports/new?batch=batch-1'])('Back from %s returns to the expected parent without uploading', async url => {
  const create = stub('createUpload', {})
  const { user, router } = mount(url)
  await user.click(screen.getByRole('link', { name: url.includes('batch=') ? 'Back to this round' : 'Back to upload history' }))
  expect(router.state.location.pathname).toBe(url.includes('batch=') ? '/uploads/batch-1' : '/imports')
  expect(create).not.toHaveBeenCalled()
})
