import { useEffect, useState } from 'react'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import { Server, BookOpen, House, Inbox, NotebookPen, Sparkles, Search, Settings, MessageCircleQuestion, Users, WifiOff, PanelLeft, type LucideIcon } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { usePreferences } from '../lib/preferences'
import { Profile } from './Profile'
import { ChatHistory } from './ChatHistory'
import { cn } from '../lib/utils'
import { CommandPalette } from './CommandPalette'
import { Drawer, Empty } from './ui'
import { useMediaQuery } from '../lib/useMediaQuery'
import { SidebarHold } from '../lib/sidebar'
import logo from '../assets/acommerce-logo.svg'

type NavItem = { to: string; label: string; th: string; icon: LucideIcon; end?: boolean; badge?: 'review' | 'failed' }

const GROUPS: { title: string; items: NavItem[]; admin?: boolean }[] = [
  {
    title: 'Work',
    items: [
      { to: '/', label: 'Home', th: 'งานที่รอคุณ', icon: House, end: true },
      { to: '/imports', label: 'Imports', th: 'ทุกไฟล์ที่ลูกค้าส่งมา', icon: Inbox },
      { to: '/review', label: 'Review', th: 'ตอบคำถามและตรวจปัญหา', icon: MessageCircleQuestion, badge: 'review' },
    ],
  },
  {
    title: 'Setup',
    items: [
      { to: '/customers', label: 'Customers', th: 'ลูกค้าและ format', icon: Users },
      { to: '/settings', label: 'Settings', th: 'AI · format · กฎอนุมัติ', icon: Settings },
    ],
  },
  { title: 'Admin', admin: true, items: [{ to: '/ops', label: 'System health', th: 'คิวงานและ job ที่ล้มเหลว', icon: Server, badge: 'failed' }] },
  { title: 'Help', items: [{ to: '/guide', label: 'Feature guide', th: 'ฟีเจอร์อยู่ตรงไหน ใช้ทำอะไร', icon: BookOpen }] },
  { title: 'Conversations', items: [{ to: '/memory', label: 'Memory', th: 'สิ่งที่ผู้ช่วยจำเกี่ยวกับคุณ', icon: NotebookPen }, { to: '/assistant', label: 'Assistant', th: 'ผู้ช่วย FORMA', icon: Sparkles }] },
]

/** Quiet API-health banner: shown only when /ready fails or reports a degraded dependency. */
function HealthBanner() {
  const { data, isError } = useQuery({ queryKey: ['ready'], queryFn: api.ready, refetchInterval: 15000, retry: 0 })
  if (!isError && (!data || data.status === 'ok')) return null
  const failing = data ? Object.entries(data.checks).filter(([, v]) => !v.ok).map(([k, v]) => `${k}${v.detail ? ` (${v.detail})` : ''}`).join(', ') : ''
  return (
    <div className={cn('flex items-center gap-2 px-4 py-2 text-[12.5px] border-b', isError ? 'bg-bad-soft text-bad border-bad/25' : 'bg-warn-soft text-warn border-warn/25')} role="status">
      <WifiOff size={13} />
      {isError ? 'API unreachable — showing cached data; actions will fail until it is back.' : `API degraded: ${failing}.`}
    </div>
  )
}

export function Shell() {
  const { prefs, t } = usePreferences()
  const labels: Record<string, string> = { Memory: 'ความจำ', Home: 'หน้าหลัก', Imports: 'ประวัติไฟล์', Review: 'คำถามและปัญหา', Customers: 'ลูกค้า', Settings: 'ตั้งค่าระบบ', 'System health': 'สถานะระบบ', 'Feature guide': 'คู่มือฟีเจอร์', Work: 'งานของคุณ', Setup: 'การตั้งค่า', Admin: 'ผู้ดูแล', Help: 'ช่วยเหลือ', Conversations: 'บทสนทนา' }
  const label = (s: string) => prefs.language === 'th' ? (s === 'Assistant' ? 'ผู้ช่วย' : labels[s] ?? s) : s === 'Review' ? 'Questions & issues' : s
  const loc = useLocation()
  const [search, setSearch] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)
  const desktop = useMediaQuery('(min-width: 900px)')
  const [collapsed, setCollapsed] = useState(() => { try { return localStorage.getItem('forma.sidebar') === 'collapsed' } catch { return false } })
  const { data: dash } = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard, refetchInterval: 15000 })
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: api.me, staleTime: 60000 })
  const isAdmin = me?.effective_roles.includes('admin') ?? false
  const wide = loc.pathname.includes('/mapping/') || loc.pathname.startsWith('/datasets/') || loc.pathname === '/assistant'
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k' && !document.querySelector('[role="dialog"]')) { e.preventDefault(); setSearch(true) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  const toggleSidebar = () => {
    if (!desktop) { setMobileOpen(true); return }
    setCollapsed((value) => { try { localStorage.setItem('forma.sidebar', !value ? 'collapsed' : 'expanded') } catch { /* private mode */ } return !value })
  }
  const [held, setHeld] = useState(false)
  const compact = desktop && (collapsed || held)
  const navigation = <>
    <Link to="/" onClick={() => setMobileOpen(false)} className="px-4 pt-6 pb-5 block" aria-label="FORMA home">
      {compact ? <span className="font-bold text-navy text-xl block text-center">F<span className="text-brand-green">.</span></span> : <><span className="inline-block dark:bg-white dark:rounded-md dark:px-2 dark:py-1"><img src={logo} alt="aCommerce" className="h-[22px] w-auto" /></span><div className="mt-4 flex items-center gap-2"><span className="font-semibold text-navy tracking-[0.12em] text-[13px]">FORMA</span><span className="h-1.5 w-1.5 rounded-full bg-brand-lime" /></div></>}
    </Link>
    <nav aria-label="Main navigation" className="px-2 flex-1 min-h-0 overflow-auto">
      {GROUPS.filter((g) => !g.admin || isAdmin).map((g) => <div key={g.title} className="mb-4">
        {!compact && <div className="px-3 py-1 text-[11px] text-muted">{label(g.title)}</div>}
        {g.items.map((n) => {
          if (n.to === '/assistant') return <ChatHistory key={n.to} compact={compact} onNavigate={() => setMobileOpen(false)} />
          const count = n.badge === 'review' ? dash?.open_reviews : n.badge === 'failed' ? dash?.failed_jobs : 0
          return <NavLink key={n.to} to={n.to} end={n.end} onClick={() => setMobileOpen(false)} title={`${n.label} — ${n.th}`} aria-label={label(n.label)} className={({ isActive }) => cn('flex items-center gap-3 px-3 py-2.5 rounded-lg text-ink-2 hover:bg-surface-2 mb-0.5', compact && 'justify-center', isActive && 'bg-signal-soft text-navy font-semibold')}>
            <n.icon size={18} />{!compact && <><span className="flex-1 text-[13px]">{label(n.label)}</span>{!!count && <span className="text-[12px] tnum text-muted">{count}</span>}</>}
          </NavLink>
        })}
      </div>)}
    </nav>
    {!compact && <div className="mx-4 py-4 border-t border-line text-[12px] text-muted">{dash ? `${dash.customers} customers` : 'Data workspace'}<div className="brand-rule mt-3" /></div>}
  </>
  return <SidebarHold.Provider value={setHeld}><div className="h-full flex min-w-0">
    <a href="#main-content" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 btn">Skip to content</a>
    {desktop && <aside className={cn('app-sidebar shrink-0 border-r border-line flex flex-col', compact ? 'w-[72px]' : 'w-[224px]')}>{navigation}</aside>}
    {!desktop && <Drawer open={mobileOpen} onClose={() => setMobileOpen(false)} title="Navigation" width="w-[300px]">{navigation}</Drawer>}
    <main id="main-content" tabIndex={-1} className="flex-1 min-w-0 min-h-0 flex flex-col overflow-auto">
      <HealthBanner />
      <header className="sticky top-0 z-20 min-h-14 shrink-0 bg-surface border-b border-line flex items-center gap-2 px-3 sm:px-5 py-2">
        <button className="btn btn-ghost" onClick={toggleSidebar} aria-label={desktop ? compact ? 'Expand sidebar' : 'Collapse sidebar' : 'Open navigation'} aria-expanded={desktop ? !compact : mobileOpen}><PanelLeft size={18} /></button>
        <button onClick={() => setSearch(true)} className="flex items-center gap-2 h-9 min-w-0 flex-1 max-w-[420px] px-2 rounded-lg text-muted hover:bg-surface-2 text-left" aria-label="Search features, customers and imports"><Search size={16} /><span className="truncate hidden sm:block flex-1 text-[13px]">{t('Search files, customers, tools…', 'ค้นหาไฟล์ ลูกค้า เครื่องมือ…')}</span><span className="kbd hidden md:inline-flex">Ctrl K</span></button>
        <div className="ml-auto flex items-center gap-1 shrink-0">
          <Profile />
        </div>
      </header>
      {me?.roles.length === 0
        ? <div className="mx-auto px-4 sm:px-6 lg:px-8 py-6 lg:py-8 max-w-[1440px] w-full"><Empty title={t('This account has no FORMA role', 'บัญชีนี้ยังไม่มีบทบาทใน FORMA')} body={t('Ask your administrator to give you access.', 'ขอให้ผู้ดูแลระบบเพิ่มสิทธิ์ให้')} /></div>
        : wide ? <div className="flex-1 min-h-0 overflow-hidden"><Outlet /></div> : <div className="mx-auto px-4 sm:px-6 lg:px-8 py-6 lg:py-8 max-w-[1440px] w-full"><Outlet /></div>}
    </main>
    <CommandPalette open={search} onClose={() => setSearch(false)} isAdmin={isAdmin} />
  </div></SidebarHold.Provider>
}
