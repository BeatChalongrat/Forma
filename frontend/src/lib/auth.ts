/**
 * Auth client (ADR-013). In dev mode nothing happens: the API trusts the X-Forma-User header. With
 * FORMA_AUTH=oidc the API publishes issuer/client id at /api/auth/config and the SPA runs an
 * Authorization Code + PKCE flow; the access token is attached as a bearer header to every request.
 */

export type AuthConfig = { mode: 'dev' | 'oidc'; issuer?: string | null; client_id?: string | null; audience?: string | null; scopes?: string; dev_users?: string[] }

const TOKEN_KEY = 'forma.access_token'
const EXP_KEY = 'forma.token_exp'
const VERIFIER_KEY = 'forma.pkce_verifier'
const USER_KEY = 'forma.dev_user'

let config: AuthConfig | null = null
let discovery: { authorization_endpoint: string; token_endpoint: string; end_session_endpoint?: string } | null = null

function safeGet(k: string): string | null {
  try {
    return sessionStorage.getItem(k) ?? localStorage.getItem(k)
  } catch {
    return null
  }
}
function safeSet(k: string, v: string | null, persistent = false) {
  try {
    const store = persistent ? localStorage : sessionStorage
    if (v === null) {
      sessionStorage.removeItem(k)
      localStorage.removeItem(k)
    } else store.setItem(k, v)
  } catch {
    /* storage unavailable */
  }
}

export function devUser(): string | null {
  return safeGet(USER_KEY)
}
export function setDevUser(u: string | null) {
  safeSet(USER_KEY, u, true)
}

export function accessToken(): string | null {
  const t = safeGet(TOKEN_KEY)
  const exp = Number(safeGet(EXP_KEY) ?? 0)
  if (!t || (exp && Date.now() / 1000 > exp - 30)) return null
  return t
}

/** Headers to add to every API request. */
export function authHeaders(): Record<string, string> {
  const t = accessToken()
  if (t) return { Authorization: `Bearer ${t}` }
  const u = devUser()
  return u ? { 'X-Forma-User': u } : {}
}

export async function loadAuthConfig(): Promise<AuthConfig> {
  if (config) return config
  try {
    const res = await fetch('/api/auth/config')
    config = res.ok ? ((await res.json()) as AuthConfig) : { mode: 'dev' }
  } catch {
    config = { mode: 'dev' }
  }
  return config
}

async function loadDiscovery(issuer: string) {
  if (discovery) return discovery
  const res = await fetch(issuer.replace(/\/$/, '') + '/.well-known/openid-configuration')
  discovery = (await res.json()) as typeof discovery
  return discovery!
}

function b64url(bytes: ArrayBuffer | Uint8Array): string {
  const arr = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes)
  let s = ''
  arr.forEach((b) => (s += String.fromCharCode(b)))
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

/** Start the PKCE login: redirects the browser to the identity provider. */
export async function login(returnTo = window.location.pathname): Promise<void> {
  const cfg = await loadAuthConfig()
  if (cfg.mode !== 'oidc' || !cfg.issuer || !cfg.client_id) return
  const d = await loadDiscovery(cfg.issuer)
  const verifier = b64url(crypto.getRandomValues(new Uint8Array(32)))
  const challenge = b64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)))
  safeSet(VERIFIER_KEY, verifier)
  safeSet('forma.return_to', returnTo)
  const p = new URLSearchParams({
    response_type: 'code',
    client_id: cfg.client_id,
    redirect_uri: window.location.origin + '/auth/callback',
    scope: cfg.scopes ?? 'openid profile email',
    code_challenge: challenge,
    code_challenge_method: 'S256',
    state: b64url(crypto.getRandomValues(new Uint8Array(12))),
  })
  if (cfg.audience) p.set('audience', cfg.audience)
  window.location.assign(`${d.authorization_endpoint}?${p.toString()}`)
}

/** Finish the PKCE login on /auth/callback; returns the path to continue to. */
export async function handleCallback(): Promise<string> {
  const cfg = await loadAuthConfig()
  const url = new URL(window.location.href)
  const code = url.searchParams.get('code')
  const verifier = safeGet(VERIFIER_KEY)
  if (!code || !verifier || cfg.mode !== 'oidc' || !cfg.issuer || !cfg.client_id) return '/'
  const d = await loadDiscovery(cfg.issuer)
  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    code,
    redirect_uri: window.location.origin + '/auth/callback',
    client_id: cfg.client_id,
    code_verifier: verifier,
  })
  const res = await fetch(d.token_endpoint, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body })
  if (!res.ok) throw new Error(`token exchange failed: ${res.status}`)
  const tok = (await res.json()) as { access_token: string; expires_in?: number }
  safeSet(TOKEN_KEY, tok.access_token)
  safeSet(EXP_KEY, String(Math.floor(Date.now() / 1000) + (tok.expires_in ?? 3600)))
  safeSet(VERIFIER_KEY, null)
  const back = safeGet('forma.return_to') ?? '/'
  safeSet('forma.return_to', null)
  return back
}

export function logout() {
  safeSet(TOKEN_KEY, null)
  safeSet(EXP_KEY, null)
  const end = discovery?.end_session_endpoint
  if (end) window.location.assign(end)
  else window.location.assign('/')
}

/** Called once at startup: in OIDC mode without a valid token, go log in. */
export async function ensureAuthenticated(): Promise<void> {
  const cfg = await loadAuthConfig()
  if (cfg.mode !== 'oidc') return
  if (window.location.pathname === '/auth/callback') return
  if (!accessToken()) await login()
}
