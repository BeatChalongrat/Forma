import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import { LayoutGrid, Inbox, Users, ShieldCheck, Settings, Sun, Moon, Upload, Activity, LogOut, WifiOff } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { useTheme } from '../lib/theme'
import { cn } from '../lib/utils'
import { accessToken, logout } from '../lib/auth'

const nav = [
  { to: '/', label: 'Overview', icon: LayoutGrid, end: true },
  { to: '/imports', label: 'Imports', icon: Inbox },
  { to: '/review', label: 'Review', icon: ShieldCheck, badge: true },
  { to: '/customers', label: 'Customers', icon: Users },
  { to: '/settings', label: 'Settings', icon: Settings },
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
  const { data: dash } = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard, refetchInterval: 15000 })
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: api.me, staleTime: 60000 })
  const isAdmin = me?.effective_roles.includes('admin') ?? false
  const wide = loc.pathname.includes('/mapping/') || loc.pathname.startsWith('/datasets/')
  return (
    <div className="h-full flex">
      <aside className="w-[212px] shrink-0 border-r border-line bg-surface flex flex-col">
        <div className="h-14 flex items-center gap-2.5 px-4 border-b border-line">
          <span className="w-7 h-7 rounded-[8px] bg-signal text-white grid place-items-center font-bold text-[14px] tracking-tight">F</span>
          <div className="leading-tight">
            <div className="font-semibold tracking-[-0.01em]">FORMA</div>
            <div className="text-[11px] text-muted">Data operations</div>
          </div>
        </div>
        <nav className="p-2 flex flex-col gap-0.5">
          <NavLink to="/imports/new" className="btn btn-primary mb-2 justify-center"><Upload size={14} /> New import</NavLink>
          {[...nav, ...(isAdmin ? [{ to: '/ops', label: 'Ops', icon: Activity, badge: false, end: false }] : [])].map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => cn('flex items-center gap-2.5 h-8 px-2.5 rounded-[var(--radius-md)] text-ink-2 hover:bg-surface-2 hover:text-ink', isActive && 'bg-signal-soft text-signal hover:bg-signal-soft hover:text-signal font-medium')}>
              <n.icon size={15} />
              <span className="flex-1">{n.label}</span>
              {n.badge && !!dash?.open_reviews && <span className="tnum text-[11px] px-1.5 h-5 rounded-full bg-warn-soft text-warn grid place-items-center">{dash.open_reviews}</span>}
              {n.to === '/ops' && !!dash?.failed_jobs && <span className="tnum text-[11px] px-1.5 h-5 rounded-full bg-bad-soft text-bad grid place-items-center">{dash.failed_jobs}</span>}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto p-3 border-t border-line text-[12px] text-muted flex items-center justify-between gap-2">
          <div className="min-w-0">
            <div className="truncate" title={me ? `${me.name} · ${me.effective_roles.join(', ')}` : ''}>{me ? <>{me.name} <span className="text-ink-3">· {me.roles[0] ?? 'no role'}</span></> : '…'}</div>
            <div>{dash?.llm.provider === 'mock' ? (isAdmin ? <Link to="/settings" className="text-signal hover:underline">AI: offline — connect</Link> : 'AI: offline mode') : `AI: ${dash?.llm.model ?? '…'}`}</div>
            <div className="tnum">{dash ? `${dash.customers} customers` : ''}</div>
          </div>
          <div className="flex flex-col gap-1 shrink-0">
            <button className="btn btn-ghost btn-sm" onClick={toggle} aria-label="Toggle theme">{theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />}</button>
            {me?.mode === 'oidc' && accessToken() && <button className="btn btn-ghost btn-sm" onClick={logout} aria-label="Sign out"><LogOut size={14} /></button>}
          </div>
        </div>
      </aside>
      <main className="flex-1 min-w-0 overflow-auto flex flex-col">
        <HealthBanner />
        {wide ? <Outlet /> : <div className="mx-auto p-6 max-w-[1240px] w-full"><Outlet /></div>}
      </main>
    </div>
  )
}
