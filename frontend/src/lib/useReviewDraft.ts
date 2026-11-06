import { useEffect, useMemo, useSyncExternalStore } from 'react'
import { api, type Me } from './api'
import { ReviewDraftStore } from './reviewDraft'

export function useReviewDraft(id: string, me: Me, editable: boolean) {
  const store = useMemo(() => {
    const key = `forma.review-outbox.v1:${encodeURIComponent(me.mode)}:${encodeURIComponent(me.subject)}:${id}`
    // The outbox is tab-local; the shared, durable draft is kept in the database.
    let saved = null
    try { const p = JSON.parse(sessionStorage.getItem(key) || 'null'); if (p?.payload && Number.isInteger(p.revision)) saved = p } catch { /* server remains authoritative */ }
    return new ReviewDraftStore({ read: () => api.reviewDraft(id), write: (rev, payload) => api.saveReviewDraft(id, rev, payload) }, value => {
      try { if (value) sessionStorage.setItem(key, JSON.stringify(value)); else sessionStorage.removeItem(key) } catch { /* pending changes still prevent explicit navigation */ }
    }, saved)
  }, [id, me.mode, me.subject])
  const state = useSyncExternalStore(store.subscribe, store.snapshot)
  useEffect(() => { void store.load() }, [store])
  useEffect(() => {
    if (!editable || !state.pending || state.conflict || state.error) return
    const timer = window.setTimeout(() => { void store.flush() }, 250)
    return () => clearTimeout(timer)
  }, [store, state.payload, state.pending, state.conflict, state.error, editable])
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (store.snapshot().pending) { e.preventDefault(); e.returnValue = '' } }
    window.addEventListener('beforeunload', warn)
    return () => { window.removeEventListener('beforeunload', warn); if (editable) void store.flush() }
  }, [store, editable])
  return { ...state, store }
}
