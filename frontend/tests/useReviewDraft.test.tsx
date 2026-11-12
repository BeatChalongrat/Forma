// @vitest-environment jsdom
import { afterEach, beforeEach, test, expect, vi } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { useReviewDraft } from '../src/lib/useReviewDraft'
import { emptyReviewDraft } from '../src/lib/reviewDraft'
import type { Me } from '../src/lib/api'

const { read, write } = vi.hoisted(() => ({ read: vi.fn(), write: vi.fn() }))
vi.mock('../src/lib/api', () => ({ api: { reviewDraft: read, saveReviewDraft: write } }))
const user = { mode: 'oidc', subject: 'alice' } as Me
const key = 'forma.review-outbox.v1:oidc:alice:m1'
beforeEach(() => {
  sessionStorage.clear()
  read.mockResolvedValue({ revision: 1, payload: {} })
  write.mockImplementation(async (_id, revision, payload) => ({ revision: revision + 1, payload }))
})
afterEach(() => { cleanup(); vi.resetAllMocks() })

test('invalid local JSON is ignored, then autosave clears pending navigation warning', async () => {
  sessionStorage.setItem(key, '{broken')
  const { result } = renderHook(() => useReviewDraft('m1', user, true))
  await waitFor(() => expect(result.current.loading).toBe(false))
  act(() => result.current.store.change({ ...emptyReviewDraft(), answers: { vat: false } }))
  const pending = new Event('beforeunload', { cancelable: true })
  window.dispatchEvent(pending)
  expect(pending.defaultPrevented).toBe(true)
  await waitFor(() => expect(result.current.pending).toBe(false))
  expect(write).toHaveBeenCalledExactlyOnceWith('m1', 1, expect.objectContaining({ answers: { vat: false } }))
  expect(sessionStorage.getItem(key)).toBeNull()
  const saved = new Event('beforeunload', { cancelable: true })
  window.dispatchEvent(saved)
  expect(saved.defaultPrevented).toBe(false)
})

test('offline autosave retains outbox and reload restores the answer without silent loss', async () => {
  write.mockRejectedValue(new Error('Offline'))
  const first = renderHook(() => useReviewDraft('m1', user, true))
  await waitFor(() => expect(first.result.current.loading).toBe(false))
  act(() => first.result.current.store.change({ ...emptyReviewDraft(), answers: { stock: 0 } }))
  await waitFor(() => expect(first.result.current.error).toBe('Offline'))
  expect(JSON.parse(sessionStorage.getItem(key)!).payload.answers.stock).toBe(0)
  first.unmount()
  const reopened = renderHook(() => useReviewDraft('m1', user, true))
  await waitFor(() => expect(reopened.result.current.loading).toBe(false))
  expect(reopened.result.current.payload.answers.stock).toBe(0)
  expect(reopened.result.current.pending).toBe(true)
})

test('outbox is scoped to owner and file; read-only view never autosaves on unmount', async () => {
  sessionStorage.setItem(key, JSON.stringify({ revision: 1, payload: { ...emptyReviewDraft(), answers: { private: 'alice' } } }))
  const { result, rerender, unmount } = renderHook(({ subject, id }) => useReviewDraft(id, { ...user, subject }, false), { initialProps: { subject: 'bob', id: 'm1' } })
  await waitFor(() => expect(result.current.loading).toBe(false))
  expect(result.current.payload.answers).toEqual({})
  rerender({ subject: 'alice', id: 'm2' })
  await waitFor(() => expect(result.current.loading).toBe(false))
  expect(result.current.payload.answers).toEqual({})
  rerender({ subject: 'alice', id: 'm1' })
  await waitFor(() => expect(result.current.loading).toBe(false))
  expect(result.current.payload.answers.private).toBe('alice')
  unmount()
  expect(write).not.toHaveBeenCalled()
  expect(sessionStorage.getItem(key)).not.toBeNull()
})
