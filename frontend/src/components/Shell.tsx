import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { LayoutGrid, Inbox, Users, ShieldCheck, Settings, Sun, Moon, Upload } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { useTheme } from '../lib/theme'
import { cn } from '../lib/utils'

const nav = [
  { to: '/', label: 'Overview', icon: LayoutGrid, end: true },
  { to: '/imports', label: 'Imports', icon: Inbox },
  { to: '/review', label: 'Review', icon: ShieldCheck, badge: true },
  { to: '/customers', label: 'Customers', icon: Users },
  { to: '/settings', label: 'Settings', icon: Settings },
]

export function Shell() {
  const [theme, toggle] = useTheme()
  const loc = useLocation()
  const { data: dash } = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard, refetchInterval: 15000 })
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
          {nav.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => cn('flex items-center gap-2.5 h-8 px-2.5 rounded-[var(--radius-md)] text-ink-2 hover:bg-surface-2 hover:text-ink', isActive && 'bg-signal-soft text-signal hover:bg-signal-soft hover:text-signal font-medium')}>
              <n.icon size={15} />
              <span className="flex-1">{n.label}</span>
              {n.badge && !!dash?.open_reviews && <span className="tnum text-[11px] px-1.5 h-5 rounded-full bg-warn-soft text-warn grid place-items-center">{dash.open_reviews}</span>}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto p-3 border-t border-line text-[12px] text-muted flex items-center justify-between">
          <div>
            <div>{dash?.llm.provider === 'mock' ? 'AI: offline mode' : `AI: ${dash?.llm.model ?? '…'}`}</div>
            <div className="tnum">{dash ? `${dash.customers} customers` : ''}</div>
          </div>
          <button className="btn btn-ghost btn-sm" onClick={toggle} aria-label="Toggle theme">{theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />}</button>
        </div>
      </aside>
      <main className="flex-1 min-w-0 overflow-auto">
        {wide ? <Outlet /> : <div className="mx-auto p-6 max-w-[1240px]"><Outlet /></div>}
      </main>
    </div>
  )
}
