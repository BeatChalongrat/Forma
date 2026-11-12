// @vitest-environment jsdom
import { dataset, mountPage, rows, stub, toast } from './pageHarness'
import { beforeEach, expect, test, vi } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { DataEditor } from '../src/pages/DataEditor'
import { api, ApiError } from '../src/lib/api'

const impact = { rows_affected: 1, cells_changed: 1, rows_deleted: 0, fields: ['/price'], sample: [{ identity: 'SKU-001', target: '/price', before: 100, after: 125 }] }
const preview = { impact, rows_after: 2, validation_errors: [], invalid_before: 0, invalid_after: 0 }
beforeEach(() => {
  // Give the real virtualizer a viewport; jsdom has no layout engine.
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(640)
  vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(1000)
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(640)
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1000)
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({ width: 1000, height: 640, top: 0, left: 0, right: 1000, bottom: 640, x: 0, y: 0, toJSON: () => ({}) })
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  stub('dataset', dataset); stub('rows', rows)
  stub('previewChanges', preview)
  stub('applyChanges', { dataset: { ...dataset, id: 'd2', version: 2 } })
})
function mount() { return mountPage(<DataEditor />, '/datasets/:datasetId', '/datasets/d1?reviewReturn=' + encodeURIComponent('/review?mapping=m1&section=finish')) }
async function editPrice(user: ReturnType<typeof mount>['user'], value = '125') {
  await user.dblClick(await screen.findByText('100'))
  const input = document.activeElement as HTMLInputElement
  expect(input.tagName).toBe('INPUT')
  await user.clear(input); await user.type(input, value); await user.keyboard('{Enter}')
}

test('cell edit stays local until preview and explicit save; creates a version with audit note and review return', async () => {
  const { user, router } = mount()
  await editPrice(user)
  expect(api.applyChanges).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'Review 1 change' }))
  const dialog = await screen.findByRole('dialog')
  await within(dialog).findByText('125')
  expect(api.previewChanges).toHaveBeenCalledWith('d1', [expect.objectContaining({ kind: 'set_cell', identity: 'SKU-001', target: '/price', value: 125 })])
  await user.type(within(dialog).getByPlaceholderText('Note for the audit log (optional)'), 'Buyer confirmed')
  await user.click(within(dialog).getByRole('button', { name: 'Save as new version' }))
  await waitFor(() => expect(router.state.location.pathname).toBe('/datasets/d2'))
  expect(new URLSearchParams(router.state.location.search).get('reviewReturn')).toBe('/review?mapping=m1&section=finish')
  expect(api.applyChanges).toHaveBeenCalledExactlyOnceWith('d1', expect.objectContaining({ kind: 'one_time', note: 'Buyer confirmed', operations: [expect.objectContaining({ value: 125 })] }))
})

test('invalid numeric input is rejected; Escape and Discard never submit mutations', async () => {
  const { user } = mount()
  await editPrice(user, 'not-a-number')
  expect(toast.push).toHaveBeenCalledWith(expect.objectContaining({ kind: 'bad' }))
  expect(screen.queryByRole('button', { name: 'Review 1 change' })).toBeNull()
  await user.keyboard('{Escape}')
  await editPrice(user)
  await user.click(screen.getByRole('button', { name: 'Discard' }))
  expect(screen.getByText('100')).toBeTruthy()
  expect(api.applyChanges).not.toHaveBeenCalled()
})

test('preview validation errors prevent saving; no partial write occurs', async () => {
  vi.mocked(api.previewChanges).mockResolvedValue({ ...preview, validation_errors: [{ row: 0, identity: 'SKU-001', pointer: '/price', message: 'Price must be nonnegative' }] } as never)
  const { user } = mount()
  await editPrice(user, '-2')
  await user.click(screen.getByRole('button', { name: 'Review 1 change' }))
  await screen.findByText(/Price must be nonnegative/)
  expect((screen.getByRole('button', { name: 'Save as new version' }) as HTMLButtonElement).disabled).toBe(true)
  expect(api.applyChanges).not.toHaveBeenCalled()
})

test('version conflict keeps staged edits and opens latest version separately without losing review origin', async () => {
  vi.mocked(api.applyChanges).mockRejectedValue(Object.assign(new Error('Newer dataset exists'), { details: { latest_dataset_id: 'd3' } }))
  const { user, router } = mount()
  await editPrice(user)
  await user.click(screen.getByRole('button', { name: 'Review 1 change' }))
  await waitFor(() => expect((screen.getByRole('button', { name: 'Save as new version' }) as HTMLButtonElement).disabled).toBe(false))
  await user.click(screen.getByRole('button', { name: 'Save as new version' }))
  const link = await screen.findByRole('link', { name: 'Open latest version' })
  expect(link.getAttribute('target')).toBe('_blank')
  expect(new URL(link.getAttribute('href')!, 'http://test').searchParams.get('reviewReturn')).toBe('/review?mapping=m1&section=finish')
  expect(router.state.location.pathname).toBe('/datasets/d1')
  expect(screen.getByText('125')).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Review 1 change' })).toBeTruthy()
})

test('Back asks before discarding staged edits; cancelling stays on the editor', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
  const { user, router } = mount()
  await editPrice(user)
  await user.click(screen.getByRole('link', { name: /Back to this file’s review/ }))
  expect(confirm).toHaveBeenCalledTimes(1)
  expect(router.state.location.pathname).toBe('/datasets/d1')
  confirm.mockReturnValue(true)
  await user.click(screen.getByRole('link', { name: /Back to this file’s review/ }))
  expect(router.state.location.pathname + router.state.location.search).toBe('/review?mapping=m1&section=finish')
  expect(api.applyChanges).not.toHaveBeenCalled()
})

test('published data cannot be edited or deleted; denied export never opens a download', async () => {
  stub('dataset', { ...dataset, status: 'published' })
  stub('exportDataset', {}).mockRejectedValue(new Error('403: export denied'))
  const open = vi.spyOn(window, 'open').mockImplementation(() => null)
  const { user } = mount()
  await user.dblClick(await screen.findByText('100'))
  expect(screen.queryByRole('button', { name: 'Review 1 change' })).toBeNull()
  expect((screen.getByRole('button', { name: 'Delete record SKU-001' }) as HTMLButtonElement).disabled).toBe(true)
  await user.click(screen.getByRole('button', { name: 'JSON' }))
  await waitFor(() => expect(toast.push).toHaveBeenCalledWith(expect.objectContaining({ kind: 'bad', title: 'Not exported' })))
  expect(open).not.toHaveBeenCalled()
})

test('schema-invalid dataset disables approval; corrected approved dataset exports the requested format', async () => {
  stub('dataset', { ...dataset, invalid_records: 1 })
  const approve = stub('approveDataset', {})
  const first = mount()
  expect((await screen.findByRole('button', { name: 'Approve data' }) as HTMLButtonElement).disabled).toBe(true)
  expect(approve).not.toHaveBeenCalled()
  first.unmount()
  stub('dataset', { ...dataset, status: 'approved' })
  stub('exportDataset', { id: 'export-1', fmt: 'json', row_count: 2 })
  const open = vi.spyOn(window, 'open').mockImplementation(() => null)
  const { user } = mount()
  await user.click(await screen.findByRole('button', { name: 'JSON' }))
  await waitFor(() => expect(open).toHaveBeenCalledWith(expect.stringContaining('export-1'), '_blank'))
  expect(api.exportDataset).toHaveBeenCalledExactlyOnceWith('d1', 'json')
})

test('pasting a locale-formatted value stages the parsed number, not a rounded string', async () => {
  const { user } = mount()
  await screen.findByText('100')
  await user.selectOptions(screen.getByLabelText('Number input format'), 'comma')
  fireEvent.mouseDown(screen.getByText('100'))
  fireEvent.paste(screen.getByLabelText('Data grid. Select a cell, then use arrow keys to navigate and Enter to edit.'), { clipboardData: { getData: () => '1.234,56' } })
  await user.click(screen.getByRole('button', { name: 'Review 1 change' }))
  await waitFor(() => expect(api.previewChanges).toHaveBeenCalledWith('d1', [expect.objectContaining({ value: 1234.56 })]))
})

test('failed impact preview shows Retry, keeps edits, and cannot save until the retry passes', async () => {
  vi.mocked(api.previewChanges).mockRejectedValueOnce(new Error('Impact service offline'))
  const { user } = mount()
  await editPrice(user)
  await user.click(screen.getByRole('button', { name: 'Review 1 change' }))
  expect((await screen.findByRole('alert')).textContent).toContain('Could not check these changes')
  expect((screen.getByRole('button', { name: 'Save as new version' }) as HTMLButtonElement).disabled).toBe(true)
  expect(api.applyChanges).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'Retry check' }))
  await waitFor(() => expect((screen.getByRole('button', { name: 'Save as new version' }) as HTMLButtonElement).disabled).toBe(false))
  expect(api.previewChanges).toHaveBeenCalledTimes(2)
  expect(vi.mocked(api.previewChanges).mock.calls[1]).toEqual(vi.mocked(api.previewChanges).mock.calls[0])
})

test('permission-denied save retains the staged edit and never navigates to a false new version', async () => {
  vi.mocked(api.applyChanges).mockRejectedValue(new Error('403: reviewer permission required'))
  const { user, router } = mount()
  await editPrice(user)
  await user.click(screen.getByRole('button', { name: 'Review 1 change' }))
  await waitFor(() => expect((screen.getByRole('button', { name: 'Save as new version' }) as HTMLButtonElement).disabled).toBe(false))
  await user.click(screen.getByRole('button', { name: 'Save as new version' }))
  await waitFor(() => expect(toast.push).toHaveBeenCalledWith(expect.objectContaining({ kind: 'bad', detail: '403: reviewer permission required' })))
  expect(router.state.location.pathname).toBe('/datasets/d1')
  expect(screen.getByRole('dialog')).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Review 1 change' })).toBeTruthy()
  expect(api.applyChanges).toHaveBeenCalledTimes(1)
})

test('deleting a row requires preview and cannot be saved as a reusable override', async () => {
  const { user } = mount()
  await user.click(await screen.findByRole('button', { name: 'Delete record SKU-001' }))
  expect(screen.queryByText('SKU-001')).toBeNull()
  expect(api.applyChanges).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'Review 1 change' }))
  const dialog = screen.getByRole('dialog')
  expect((within(dialog).getByRole('option', { name: /Apply these values in future rounds/ }) as HTMLOptionElement).disabled).toBe(true)
  await waitFor(() => expect((within(dialog).getByRole('button', { name: 'Save as new version' }) as HTMLButtonElement).disabled).toBe(false))
  await user.click(within(dialog).getByRole('button', { name: 'Save as new version' }))
  await waitFor(() => expect(api.applyChanges).toHaveBeenCalledWith('d1', expect.objectContaining({ kind: 'one_time', operations: [{ kind: 'delete_row', identity: 'SKU-001' }] })))
})


test.each([403, 404, 503])('dataset load %s shows a recoverable error and a safe return, not an endless spinner', async status => {
  vi.mocked(api.dataset).mockRejectedValueOnce(new ApiError(status, 'load_failed', 'Internal details'))
  const { user } = mount()
  const alert = await screen.findByRole('alert')
  expect(alert.textContent).not.toContain('Internal details')
  expect(screen.getByRole('link', { name: /Back to files or review/ }).getAttribute('href')).toBe('/review?mapping=m1&section=finish')
  expect(api.rows).not.toHaveBeenCalled()
  await user.click(within(alert).getByRole('button', { name: 'Try again' }))
  await screen.findByText('100')
  expect(screen.queryByRole('alert')).toBeNull()
})

test('initial rows failure is an error, not an empty file; retry recovers without leaving', async () => {
  vi.mocked(api.rows).mockRejectedValueOnce(new Error('Offline'))
  const { user, router } = mount()
  await screen.findByRole('alert')
  expect((screen.getByRole('button', { name: 'Approve data' }) as HTMLButtonElement).disabled).toBe(true)
  await user.click(screen.getByRole('button', { name: 'Try again' }))
  await screen.findByText('100')
  expect(router.state.location.pathname).toBe('/datasets/d1')
})

test('background rows failure preserves edits, blocks save, and retries the same query', async () => {
  const { user, client } = mount()
  await editPrice(user)
  vi.mocked(api.rows).mockRejectedValueOnce(new Error('Offline'))
  await client.refetchQueries({ queryKey: ['rows'] })
  const alert = await screen.findByRole('alert')
  expect(alert.textContent).toContain('Your unsaved edits remain')
  expect(screen.getByText('125')).toBeTruthy()
  expect((screen.getByRole('button', { name: 'Review 1 change' }) as HTMLButtonElement).disabled).toBe(true)
  await user.click(within(alert).getByRole('button', { name: 'Try again' }))
  await waitFor(() => expect((screen.getByRole('button', { name: 'Review 1 change' }) as HTMLButtonElement).disabled).toBe(false))
  expect(screen.getByText('125')).toBeTruthy()
  expect(api.applyChanges).not.toHaveBeenCalled()
  expect(vi.mocked(api.rows).mock.calls.at(-1)).toEqual(vi.mocked(api.rows).mock.calls[0])
})
