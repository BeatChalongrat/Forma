import { afterEach, beforeEach, expect, vi } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import type { ReactNode } from 'react'
import { api } from '../src/lib/api'

// Mount production pages/hooks/router. Only the API boundary is replaced.
// Unexpected requests must never reach localhost, dev storage, or a model provider.
vi.mock('../src/lib/preferences', async importOriginal => ({
  ...await importOriginal<typeof import('../src/lib/preferences')>(),
  usePreferences: () => ({ prefs: { language: 'en', timezone: 'Asia/Bangkok', display_name: 'Tester' }, t: (en: string) => en, date: (value?: string) => value ?? '' }),
}))
const toastMock = vi.hoisted(() => ({ push: vi.fn() }))
vi.mock('../src/lib/toast', () => ({ useToast: () => toastMock }))
export const toast = toastMock

const clients: QueryClient[] = []
let network: ReturnType<typeof vi.fn>
// Flow diagrams probe canvas text measurement at import time. jsdom has no canvas;
// exercise the production fallback rather than pretending to verify rendered pixels.
vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
beforeEach(() => {
  localStorage.clear(); sessionStorage.clear()
  network = vi.fn(() => { throw new Error('Unexpected network request: page tests are offline') })
  vi.stubGlobal('fetch', network)
  vi.spyOn(Element.prototype, 'scrollIntoView').mockImplementation(() => {})
})
// jsdom does not implement scrolling/layout. Keep the real components and effects.
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}
afterEach(() => {
  cleanup(); clients.splice(0).forEach(client => client.clear())
  try { expect(network).not.toHaveBeenCalled() }
  finally { vi.restoreAllMocks(); vi.unstubAllGlobals(); toast.push.mockReset() }
})

export function stub<K extends keyof typeof api>(name: K, value: unknown) {
  return vi.spyOn(api, name).mockResolvedValue(value as never)
}
export function mountPage(element: ReactNode, path: string, url: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  clients.push(client)
  const router = createMemoryRouter([{ path, element }, { path: '*', element: <p>Destination page</p> }], { initialEntries: [url] })
  const view = render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>)
  return { ...view, client, router, user: userEvent.setup() }
}
export function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

export const me = { mode: 'dev', subject: 'test-operator', name: 'Tester', effective_roles: ['reviewer', 'approver'], roles: ['reviewer', 'approver'], customers: null }
export const fields = [
  { pointer: '/sku', name: 'sku', json_type: ['string'], semantic: 'sku', required: true, system: false, in_collection: null, is_collection: false },
  { pointer: '/price', name: 'price', json_type: ['number'], semantic: 'price.amount', required: true, system: false, in_collection: null, is_collection: false },
]
export const rows = { rows: [{ sku: 'SKU-001', price: 100 }, { sku: 'SKU-002', price: 250 }], total: 2, columns: fields, identity_keys: ['/sku'], findings: [] }
export const dataset = { id: 'd1', round_id: 'r1', version: 1, status: 'draft', row_count: 2, invalid_records: 0, findings: [], exports: [], history: [], change_sets: [], parent_id: null }
export const round = { id: 'r1', summary: { mapping_version_id: 'm1', dataset_version_id: 'd1' }, datasets: [{ id: 'd1', mapping_version_id: 'm1' }], jobs: [] }
