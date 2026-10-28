import { useEffect, useState } from 'react'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import { Activity, BookOpen, Home, Inbox, LogOut, Moon, Search, Settings, ShieldCheck, Sun, Users, WifiOff, type LucideIcon } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { useTheme } from '../lib/theme'
import { cn } from '../lib/utils'
import { accessToken, logout } from '../lib/auth'
import { CommandPalette } from './CommandPalette'
import logo from '../assets/acommerce-logo.svg'

type NavItem = { to: string; label: string; th: string; icon: LucideIcon; end?: boolean; badge?: 'review' | 'failed' }

const GROUPS: { title: string; items: NavItem[]; admin?: boolean }[] = [
  {
    title: 'Work',
    items: [
      { to: '/', label: 'Home', th: 'งานที่รอคุณ', icon: Home, end: true },
      { to: '/imports', label: 'Imports', th: 'ทุกไฟล์ที่ลูกค้าส่งมา', icon: Inbox },
      { to: '/review', label: 'Review', th: 'mapping ที่รอตัดสินใจ', icon: ShieldCheck, badge: 'review' },
    ],
  },
  {
    title: 'Setup',
    items: [
      { to: '/customers', label: 'Customers', th: 'ลูกค้าและ format', icon: Users },
      { to: '/settings', label: 'Settings', th: 'AI · format · กฎอนุมัติ', icon: Settings },
    ],
  },
  { title: 'Admin', admin: true, items: [{ to: '/ops', label: 'System health', th: 'คิวงานและ job ที่ล้มเหลว', icon: Activity, badge: 'failed' }] },
  { title: 'Help', items: [{ to: '/guide', label: 'Feature guide', th: 'ฟีเจอร์อยู่ตรงไหน ใช้ทำอะไร', icon: BookOpen }] },
]

/** Quiet API-health banner: shown only when /ready fails or reports a degraded dependency. */
function HealthBanner() {
  const { data, isError } = useQuery({ queryKey: ['ready'], queryFn: api.ready, refetchInterval: 15000, retry: 0 })
  if (!isError && (!data || data.status === 'ok')) return null
  const failing = data ? Object.entries(data.checks).filter(([, v]) => !v.ok).map(([k, v]) => `${k}${v.detail ? ` (${v.detail})` : ''}`).join(', ') : ''
  return (
    <div className={cn('flex items-center gap-2 px-4 h-8 text-[12.5px] border-b', isError ? 'bg-bad-soft text-bad border-bad/25' : 'bg-warn-soft text-warn border-warn/25')} role="status">
      <WifiOff size={13} />
      {isError ? 'API unreachable — showing cached data; actions will fail until it is back.' : `API degraded: ${failing}.`}
    </div>
  )
}

export function Shell() {
  const [theme, toggle] = useTheme()
  const loc = useLocation()
  const [search, setSearch] = useState(false)
  const { data: dash } = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard, refetchInterval: 15000 })
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: api.me, staleTime: 60000 })
  const isAdmin = me?.effective_roles.includes('admin') ?? false
  const wide = loc.pathname.includes('/mapping/') || loc.pathname.startsWith('/datasets/')

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setSearch((s) => !s) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const badge = (b?: NavItem['badge']) =>
    b === 'review' && dash?.open_reviews ? <span className="tnum text-[11px] font-semibold px-1.5 h-5 rounded-full bg-warn-soft text-warn grid place-items-center">{dash.open_reviews}</span>
      : b === 'failed' && dash?.failed_jobs ? <span className="tnum text-[11px] font-semibold px-1.5 h-5 rounded-full bg-bad-soft text-bad grid place-items-center">{dash.failed_jobs}</span>
        : null

  return (
    <div className="h-full flex">
      <aside className="w-[236px] shrink-0 border-r border-line bg-surface flex flex-col">
        <Link to="/" className="px-5 pt-5 pb-4 block" aria-label="FORMA home">
          <span className="inline-block dark:bg-white dark:rounded-md dark:px-2 dark:py-1"><img src={logo} alt="aCommerce" className="h-[22px] w-auto" /></span>
          <div className="mt-2.5 font-bold text-navy tracking-[0.2em] text-[13px] leading-none">FORMA</div>
          <div className="text-[11px] text-muted mt-1">ERP files → clean JSON</div>
        </Link>
        <nav className="px-2 pt-3 flex flex-col overflow-auto">
          {GROUPS.filter((g) => !g.admin || isAdmin).map((g) => (
            <div key={g.title} className="mt-2">
              <div className="px-3 pb-1 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-ink-3">{g.title}</div>
              {g.items.map((n) => (
                <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => cn('group flex items-center gap-3 px-3 py-1.5 rounded-[var(--radius-md)] text-ink-2 hover:bg-surface-2 hover:text-navy border-l-[3px] border-transparent', isActive && 'bg-signal-soft text-navy border-brand-blue hover:bg-signal-soft')}>
                  <n.icon size={16} className="shrink-0" />
                  <span className="flex-1 min-w-0 leading-tight">
                    <span className="block font-semibold text-[13px]">{n.label}</span>
                    <span className="block text-[11px] text-muted truncate">{n.th}</span>
                  </span>
                  {badge(n.badge)}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="mt-auto px-4 py-3 border-t border-line text-[12px] text-muted">
          <div className="flex items-center gap-1.5">
            <span className={cn('w-2 h-2 rounded-full', dash?.llm.provider === 'mock' ? 'bg-ink-3' : 'bg-ok')} />
            {dash?.llm.provider === 'mock' ? (isAdmin ? <Link to="/settings?tab=ai" className="text-signal font-semibold hover:underline">AI offline — connect</Link> : 'AI offline') : <span className="truncate">AI · {dash?.llm.model ?? '…'}</span>}
          </div>
          <div className="tnum mt-0.5">{dash ? `${dash.customers} customers` : ''}</div>
        </div>
      </aside>
      <main className="flex-1 min-w-0 overflow-auto flex flex-col">
        <HealthBanner />
        {!wide && (
          <header className="sticky top-0 z-20 h-14 shrink-0 bg-surface/95 backdrop-blur flex items-center gap-3 px-6" style={{ boxShadow: 'var(--shadow-header)' }}>
            <button onClick={() => setSearch(true)} className="flex items-center gap-2.5 h-9 w-[420px] max-w-full px-3 rounded-[10px] border border-line-2 bg-surface-2 text-ink-3 hover:border-signal/50 text-left">
              <Search size={15} className="text-signal" />
              <span className="flex-1 truncate">Find a feature, customer or import…</span>
              <span className="kbd">Ctrl K</span>
            </button>
            <div className="ml-auto flex items-center gap-2">
              <Link to="/guide" className="btn btn-outline btn-sm"><BookOpen size={13} /> Guide</Link>
              <button className="btn btn-ghost btn-sm" onClick={toggle} aria-label="Toggle theme" title={theme === 'dark' ? 'Light mode' : 'Dark mode'}>{theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}</button>
              <div className="flex items-center gap-2 pl-2 ml-1 border-l border-line" title={me ? `${me.name} · ${me.effective_roles.join(', ')}` : ''}>
                <span className="w-8 h-8 rounded-full bg-signal-soft text-navy font-bold grid place-items-center text-[12px] uppercase">{me?.name?.slice(0, 2) ?? '··'}</span>
                <span className="hidden lg:block leading-tight text-[12px]"><span className="block font-semibold text-navy">{me?.name ?? '…'}</span><span className="block text-muted">{me?.roles[0] ?? ''}</span></span>
                {me?.mode === 'oidc' && accessToken() && <button className="btn btn-ghost btn-sm" onClick={logout} aria-label="Sign out"><LogOut size={14} /></button>}
              </div>
            </div>
          </header>
        )}
        {wide ? <Outlet /> : <div className="mx-auto px-6 py-7 max-w-[1240px] w-full"><Outlet /></div>}
      </main>
      <CommandPalette open={search} onClose={() => setSearch(false)} isAdmin={isAdmin} />
    </div>
  )
}
