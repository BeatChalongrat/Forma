// @vitest-environment jsdom
import { afterEach, test, expect, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ConfirmAction } from '../src/components/ConfirmAction'

const { acceptVerdict, push } = vi.hoisted(() => ({ acceptVerdict: vi.fn(), push: vi.fn() }))
vi.mock('../src/lib/api', () => ({ api: { acceptVerdict } }))
vi.mock('../src/lib/preferences', () => ({ usePreferences: () => ({ prefs: { language: 'en' }, t: (en: string) => en }) }))
vi.mock('../src/lib/toast', () => ({ useToast: () => ({ push }) }))
afterEach(() => { cleanup(); vi.clearAllMocks() })

const action = { label: 'Approve file A', detail: 'Prepare the verified data', act: { round_id: 'r1', digest: 'server-digest', preview: [{ en: 'Approve 12 rows', th: 'อนุมัติ 12 รายการ' }] } }
function mount(value = action) {
  const query = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  const invalidate = vi.spyOn(query, 'invalidateQueries')
  render(<QueryClientProvider client={query}><ConfirmAction action={value as never} /></QueryClientProvider>)
  return { user: userEvent.setup(), invalidate }
}

test('preview and cancel never mutate; explicit confirm sends server digest once', async () => {
  let resolve!: (value: unknown) => void
  acceptVerdict.mockImplementation(() => new Promise(r => { resolve = r }))
  const { user, invalidate } = mount()
  await user.click(screen.getByRole('button', { name: 'Review & confirm' }))
  expect(screen.getByText('Approve 12 rows')).toBeTruthy()
  await user.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(acceptVerdict).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'Review & confirm' }))
  await user.click(screen.getByRole('button', { name: 'Confirm' }))
  const pending = screen.getByRole('button', { name: 'Working…' }) as HTMLButtonElement
  expect(pending.disabled).toBe(true)
  await user.click(pending)
  expect(acceptVerdict).toHaveBeenCalledExactlyOnceWith('r1', { digest: 'server-digest', decisions: {} })
  resolve({})
  await waitFor(() => expect(push).toHaveBeenCalledWith(expect.objectContaining({ kind: 'ok' })))
  expect(invalidate).toHaveBeenCalledTimes(1)
  expect(screen.queryByRole('button', { name: 'Confirm' })).toBeNull()
})

test.each([403, 409])('permission/conflict failure %s never displays success or auto-retries', async status => {
  acceptVerdict.mockRejectedValue(Object.assign(new Error(`Denied ${status}`), { status }))
  const { user, invalidate } = mount()
  await user.click(screen.getByRole('button', { name: 'Review & confirm' }))
  await user.click(screen.getByRole('button', { name: 'Confirm' }))
  await waitFor(() => expect(push).toHaveBeenCalledWith(expect.objectContaining({ kind: 'bad', detail: `Denied ${status}` })))
  expect(acceptVerdict).toHaveBeenCalledTimes(1)
  expect(invalidate).not.toHaveBeenCalled()
  expect((screen.getByRole('button', { name: 'Confirm' }) as HTMLButtonElement).disabled).toBe(false)
})

test('a navigation-only action cannot expose an approval button', () => {
  mount({ label: 'Open file' } as typeof action)
  expect(screen.queryByRole('button')).toBeNull()
})
