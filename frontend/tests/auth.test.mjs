import { beforeEach, afterEach, test, expect, vi } from 'vitest'

let auth, fetcher, assign
const store = () => {
  const values = new Map()
  return { getItem: k => values.get(k) ?? null, setItem: (k, v) => values.set(k, String(v)), removeItem: k => values.delete(k) }
}
const location = href => {
  const url = new URL(href)
  vi.stubGlobal('window', { location: { href, origin: url.origin, pathname: url.pathname, assign } })
}
beforeEach(async () => {
  vi.resetModules()
  vi.stubGlobal('sessionStorage', store())
  vi.stubGlobal('localStorage', store())
  assign = vi.fn()
  location('https://forma.example/review')
  fetcher = vi.fn(async url => {
    if (url === '/api/auth/config') return { ok: true, json: async () => ({ mode: 'oidc', issuer: 'https://id.example/', client_id: 'forma', audience: 'forma-api' }) }
    if (String(url).endsWith('openid-configuration')) return { ok: true, json: async () => ({ authorization_endpoint: 'https://id.example/auth', token_endpoint: 'https://id.example/token' }) }
    return { ok: true, json: async () => ({ access_token: 'valid-token', expires_in: 3600 }) }
  })
  vi.stubGlobal('fetch', fetcher)
  auth = await import('../src/lib/auth.ts')
})
afterEach(() => vi.unstubAllGlobals())

test('OIDC callback must match the state sent with PKCE, and cannot be replayed', async () => {
  await auth.login('/review?round=one')
  const redirect = new URL(assign.mock.calls[0][0])
  expect(redirect.searchParams.get('code_challenge_method')).toBe('S256')
  expect(redirect.searchParams.get('audience')).toBe('forma-api')
  const state = redirect.searchParams.get('state')
  location(`https://forma.example/auth/callback?code=one&state=${state}`)
  expect(await auth.handleCallback()).toBe('/review?round=one')
  expect(auth.authHeaders()).toEqual({ Authorization: 'Bearer valid-token' })
  await auth.handleCallback()
  expect(fetcher.mock.calls.filter(([url]) => url === 'https://id.example/token')).toHaveLength(1)
})

test('concurrent callback effects await the same token exchange without early redirect', async () => {
  await auth.login('/review?round=one')
  const state = new URL(assign.mock.calls[0][0]).searchParams.get('state')
  location(`https://forma.example/auth/callback?code=one&state=${state}`)
  let resolve
  const response = new Promise(r => { resolve = r })
  fetcher.mockImplementationOnce(() => response)
  const first = auth.handleCallback(), second = auth.handleCallback()
  expect(first).toBe(second)
  resolve({ ok: true, json: async () => ({ access_token: 'valid-token', expires_in: 3600 }) })
  expect(await first).toBe('/review?round=one')
  expect(await second).toBe('/review?round=one')
  expect(fetcher.mock.calls.filter(([url]) => url === 'https://id.example/token')).toHaveLength(1)
})

test.each(['', 'different'])('unmatched callback state %s never exchanges a code', async state => {
  await auth.login()
  location(`https://forma.example/auth/callback?code=foreign&state=${state}`)
  await expect(auth.handleCallback()).rejects.toThrow(/state/i)
  expect(auth.accessToken()).toBeNull()
  expect(fetcher.mock.calls.some(([url]) => url === 'https://id.example/token')).toBe(false)
})

test.each(['https://evil.example', '//evil.example', '/\\evil.example', '/\n/evil.example'])('callback refuses external return path %j', async path => {
  await auth.login(path)
  const state = new URL(assign.mock.calls[0][0]).searchParams.get('state')
  location(`https://forma.example/auth/callback?code=one&state=${state}`)
  expect(await auth.handleCallback()).toBe('/')
})

test.each([null, 'NaN', '0', String(Math.floor(Date.now() / 1000) + 10)])('invalid or near-expired token %s is not sent', expiry => {
  sessionStorage.setItem('forma.access_token', 'secret')
  if (expiry !== null) sessionStorage.setItem('forma.token_exp', expiry)
  expect(auth.accessToken()).toBeNull()
})

test('failed token exchange does not save credentials or allow replay', async () => {
  await auth.login()
  const state = new URL(assign.mock.calls[0][0]).searchParams.get('state')
  location(`https://forma.example/auth/callback?code=one&state=${state}`)
  fetcher.mockImplementationOnce(async () => ({ ok: false, status: 401 }))
  await expect(auth.handleCallback()).rejects.toThrow('401')
  expect(auth.accessToken()).toBeNull()
  expect(await auth.handleCallback()).toBe('/')
})

test.each([{}, { access_token: '' }, { access_token: 'token', expires_in: -1 }, { access_token: 'token', expires_in: '3600' }])('malformed token response %j never stores credentials', async token => {
  await auth.login()
  const state = new URL(assign.mock.calls[0][0]).searchParams.get('state')
  location(`https://forma.example/auth/callback?code=one&state=${state}`)
  fetcher.mockImplementationOnce(async () => ({ ok: true, json: async () => token }))
  await expect(auth.handleCallback()).rejects.toThrow('invalid token')
  expect(auth.accessToken()).toBeNull()
})

test('dev identity can be cleared and logout removes both token stores', () => {
  auth.setDevUser('reviewer')
  expect(auth.authHeaders()).toEqual({ 'X-Forma-User': 'reviewer' })
  auth.setDevUser(null)
  expect(auth.authHeaders()).toEqual({})
  localStorage.setItem('forma.access_token', 'old')
  sessionStorage.setItem('forma.access_token', 'new')
  auth.logout()
  expect(auth.accessToken()).toBeNull()
  expect(localStorage.getItem('forma.access_token')).toBeNull()
  expect(assign).toHaveBeenCalledWith('/')
})

test('startup skips callback, accepts valid token, otherwise starts login', async () => {
  location('https://forma.example/auth/callback')
  await auth.ensureAuthenticated()
  expect(assign).not.toHaveBeenCalled()
  location('https://forma.example/review')
  sessionStorage.setItem('forma.access_token', 'valid')
  sessionStorage.setItem('forma.token_exp', String(Date.now() / 1000 + 3600))
  await auth.ensureAuthenticated()
  expect(assign).not.toHaveBeenCalled()
  sessionStorage.removeItem('forma.access_token')
  await auth.ensureAuthenticated()
  expect(assign).toHaveBeenCalledTimes(1)
})
