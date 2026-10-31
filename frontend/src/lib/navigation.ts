export type Visit = { index: number; key: string; url: string; scroll: number }

// Filters and pagination belong to a page; opening a review or a chat is a new page.
export function pageIdentity(url: string): string {
  const { pathname, searchParams } = new URL(url, 'http://forma.local')
  const detail = pathname === '/review' ? searchParams.get('mapping')
    : pathname === '/assistant' ? searchParams.get('chat')
      : pathname === '/imports' ? searchParams.get('view') : null
  return pathname + (detail ? `?detail=${detail}` : '')
}

export function recordVisit(visits: Visit[], current: Visit, action: string): Visit[] {
  // A POP we never observed (e.g. a freshly opened link) has no trusted back history.
  if (action === 'POP' && !visits.some(v => v.index === current.index && v.key === current.key)) return [current]
  const kept = visits.filter(v => v.index !== current.index && (action !== 'PUSH' || v.index < current.index))
  return [...kept, current].sort((a, b) => a.index - b.index).slice(-100)
}
