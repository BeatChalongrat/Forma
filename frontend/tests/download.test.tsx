// @vitest-environment jsdom
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { cleanup, render, screen, fireEvent, waitFor } from '@testing-library/react'
import { downloadFile } from '../src/lib/download'
import { DownloadLink } from '../src/components/DownloadLink'

vi.mock('../src/lib/auth', () => ({ authHeaders: () => ({ Authorization: 'Bearer test-user-token' }) }))
vi.mock('../src/lib/preferences', () => ({ usePreferences: () => ({ t: (en: string) => en }) }))
let fetcher: ReturnType<typeof vi.fn>
beforeEach(() => {
  fetcher = vi.fn(async () => new Response(new Blob(['{}']), { headers: { 'Content-Disposition': "attachment; filename*=UTF-8''%E0%B9%84%E0%B8%97%E0%B8%A2.json" } }))
  vi.stubGlobal('fetch', fetcher)
  URL.createObjectURL = vi.fn(() => 'blob:download')
  URL.revokeObjectURL = vi.fn()
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('download sends user credentials to same-origin proxy and preserves Unicode name', async () => {
  let name = ''
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { name = this.download })
  await downloadFile('/api/exports/one/download')
  expect(fetcher).toHaveBeenCalledWith('/api/exports/one/download?proxy=true', { headers: { Authorization: 'Bearer test-user-token' } })
  expect(name).toBe('ไทย.json')
  expect(document.querySelector('a')).toBeNull()
})

test('no credentials leave the app via an external download path', async () => {
  await expect(downloadFile('https://foreign.example/file')).rejects.toThrow('Invalid download path')
  expect(fetcher).not.toHaveBeenCalled()
})

test('download errors stay on the same page and can retry without creating another export', async () => {
  fetcher.mockResolvedValueOnce(new Response(JSON.stringify({ message: 'Please sign in again' }), { status: 401 }))
  render(<DownloadLink href="/api/exports/one/download">Download JSON</DownloadLink>)
  fireEvent.click(screen.getByRole('link'))
  expect((await screen.findByRole('alert')).textContent).toContain('Please sign in again')
  fireEvent.click(screen.getByRole('link'))
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2))
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
})

test('duplicate clicks and disabled downloads never start extra requests', async () => {
  let finish!: (value: Response) => void
  fetcher.mockReturnValue(new Promise<Response>(resolve => { finish = resolve }))
  const view = render(<DownloadLink href="/api/batches/one/download">ZIP</DownloadLink>)
  fireEvent.click(screen.getByRole('link')); fireEvent.click(screen.getByRole('link'))
  expect(fetcher).toHaveBeenCalledTimes(1)
  finish(new Response('{}'))
  await waitFor(() => expect(screen.getByRole('link').getAttribute('aria-busy')).toBe('false'))
  view.rerender(<DownloadLink disabled href="/api/batches/one/download">ZIP</DownloadLink>)
  fireEvent.click(screen.getByRole('link'))
  expect(fetcher).toHaveBeenCalledTimes(1)
})
