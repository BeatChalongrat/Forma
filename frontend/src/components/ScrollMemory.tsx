import { useLayoutEffect, useRef, type RefObject } from 'react'
import { useLocation, useNavigationType } from 'react-router-dom'
import { pageIdentity, recordVisit, type Visit } from '../lib/navigation'

const STORAGE = 'forma.page-history.v1'

function readVisits(current: Visit): Visit[] {
  try {
    const data: unknown = JSON.parse(sessionStorage.getItem(STORAGE) || '[]')
    if (!Array.isArray(data)) return []
    const visits: Visit[] = data.filter(v => v && Number.isInteger(v.index) && typeof v.key === 'string'
      && typeof v.url === 'string' && v.url.startsWith('/') && !v.url.startsWith('//') && !v.url.includes('\\') && Number.isFinite(v.scroll))
    return visits.some(v => v.index === current.index && v.key === current.key) ? visits : []
  } catch { return [] }
}

/** A new page opens at the top and the browser's Back returns to where you were. Pages scroll inside `main`, so the browser cannot do this itself. */
export function ScrollMemory({ scrollRef }: { scrollRef: RefObject<HTMLElement | null> }) {
  const location = useLocation(), action = useNavigationType()
  const url = location.pathname + location.search + location.hash
  const index = Number.isInteger(window.history.state?.idx) ? window.history.state.idx as number : 0
  const visits = useRef<Visit[] | null>(null)
  const lastPage = useRef<string | null>(null)

  useLayoutEffect(() => {
    const current: Visit = { index, key: location.key, url, scroll: 0 }
    if (visits.current === null) visits.current = readVisits(current)
    const saved = visits.current.find(v => v.index === index && v.key === location.key)
    current.scroll = saved?.scroll ?? 0
    visits.current = recordVisit(visits.current, current, action)
    const page = pageIdentity(url)
    const requestedScroll: unknown = location.state?.restoreScroll
    if (lastPage.current !== page) scrollRef.current?.scrollTo({ top: typeof requestedScroll === 'number' && Number.isFinite(requestedScroll) ? requestedScroll : action === 'POP' ? current.scroll : 0 })
    lastPage.current = page
    const persist = () => {
      current.scroll = scrollRef.current?.scrollTop ?? 0
      try { sessionStorage.setItem(STORAGE, JSON.stringify(visits.current)) } catch { /* private mode */ }
    }
    persist()
    window.addEventListener('pagehide', persist)
    return () => { persist(); window.removeEventListener('pagehide', persist) }
  }, [action, index, location.key, location.state, scrollRef, url])

  return null
}
