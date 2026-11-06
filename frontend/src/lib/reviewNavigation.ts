/** Only a local review route may be a return target. Preserve queue filters, never an external URL. */
export function reviewReturn(value: string | null, fallback = '/review'): string {
  if (!value || !value.startsWith('/review') || value.includes('\\')) return fallback
  try { const u = new URL(value, 'http://forma.local'); return u.origin === 'http://forma.local' && u.pathname === '/review' ? u.pathname + u.search : fallback } catch { return fallback }
}
export function reviewToolUrl(path: string, returnTo: string): string {
  const u = new URL(path, 'http://forma.local'); u.searchParams.set('reviewReturn', reviewReturn(returnTo)); return u.pathname + u.search
}
export function latestReview(returnTo: string, mappingId: string): string {
  const u = new URL(reviewReturn(returnTo), 'http://forma.local'); u.searchParams.set('mapping', mappingId); u.searchParams.set('section', 'finish'); u.searchParams.delete('panel'); return u.pathname + u.search
}
