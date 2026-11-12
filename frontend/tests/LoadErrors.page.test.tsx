// @vitest-environment jsdom
import { deferred, mountPage, stub } from './pageHarness'
import { expect, test, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { RoundPage } from '../src/pages/Round'
import { MappingStudio } from '../src/pages/MappingStudio'
import { SettingsPage } from '../src/pages/Settings'
import { api, ApiError } from '../src/lib/api'

test('round read failure keeps an explicit return to the original review and retries in place', async () => {
  const next = deferred<never>()
  stub('round', {}).mockRejectedValueOnce(new ApiError(404, 'missing', 'Not found')).mockImplementationOnce(() => next.promise)
  const { user, router } = mountPage(<RoundPage />, '/rounds/:roundId', '/rounds/r1?reviewReturn=' + encodeURIComponent('/review?mapping=m1'))
  await screen.findByRole('alert')
  expect(screen.getByRole('link').getAttribute('href')).toBe('/review?mapping=m1')
  await user.click(screen.getByRole('button', { name: 'Try again' }))
  await waitFor(() => expect(api.round).toHaveBeenCalledTimes(2))
  expect(router.state.location.pathname).toBe('/rounds/r1')
})

test('mapping read failure offers retry and returns to its own round', async () => {
  stub('round', {})
  stub('mapping', {}).mockRejectedValue(new ApiError(403, 'denied', 'No access'))
  const { user } = mountPage(<MappingStudio />, '/rounds/:roundId/mapping/:mappingId', '/rounds/r1/mapping/m1')
  expect((await screen.findByRole('alert')).textContent).toContain('You do not have access')
  expect(screen.getByRole('link').getAttribute('href')).toBe('/rounds/r1')
  await user.click(screen.getByRole('button', { name: 'Try again' }))
  await waitFor(() => expect(api.mapping).toHaveBeenCalledTimes(2))
})

test.each(['ai', 'policy'] as const)('settings %s failure allows retry without sending a settings mutation', async tab => {
  const method = tab === 'ai' ? 'aiSettings' : 'policySettings'
  stub(method, {}).mockRejectedValue(new Error('Offline'))
  const save = vi.spyOn(api, tab === 'ai' ? 'saveAiSettings' : 'savePolicy')
  const { user } = mountPage(<SettingsPage />, '/settings', '/settings?tab=' + tab)
  await screen.findByRole('alert')
  await user.click(screen.getByRole('button', { name: 'Try again' }))
  await waitFor(() => expect(api[method]).toHaveBeenCalledTimes(2))
  expect(save).not.toHaveBeenCalled()
})
