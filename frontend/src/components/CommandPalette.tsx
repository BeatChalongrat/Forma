import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowRight, BookOpen, FileSpreadsheet, Search, Users } from 'lucide-react'
import { api } from '../lib/api'
import { FEATURES, matches, STEPS, useOpenFeature } from '../lib/features'
import { useToast } from '../lib/toast'
import { cn } from '../lib/utils'
import { useDialog } from '../lib/useDialog'

type Item = { key: string; group: string; title: string; sub: string; icon: typeof Search; run: () => void }

/** Ctrl+K: find any feature, page, customer or recent import by name — in English or Thai. */
export function CommandPalette({ open, onClose, isAdmin }: { open: boolean; onClose: () => void; isAdmin: boolean }) {
  const dialogRef = useDialog(open, onClose)
  const [q, setQ] = useState('')
  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const nav = useNavigate()
  const toast = useToast()
  const openFeature = useOpenFeature((m) => toast.push({ kind: 'info', title: 'Nothing to open yet', detail: m }))
  const { data: customers } = useQuery({ queryKey: ['customers'], queryFn: api.customers, enabled: open })
  const { data: dash } = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard, enabled: open })

  useEffect(() => {
    if (!open) return
    const t = window.setTimeout(() => inputRef.current?.focus(), 0)
    return () => window.clearTimeout(t)
  }, [open])

  useEffect(() => {
    if (open) dialogRef.current?.querySelector('[data-command-active="true"]')?.scrollIntoView({ block: 'nearest' })
  }, [active, open, dialogRef])

  const items = useMemo<Item[]>(() => {
    const s = q.trim().toLowerCase()
    const close = (fn: () => void) => () => { onClose(); setQ(''); fn() }
    const feats = FEATURES.filter((f) => (!f.adminOnly || isAdmin) && matches(f, q)).map((f) => ({
      key: `f:${f.id}`, group: s ? 'Features' : STEPS.find((x) => x.id === f.step)?.title ?? 'Features', title: f.title, sub: `${f.where} — ${f.th}`, icon: f.icon,
      run: close(() => void openFeature(f)),
    }))
    const custs = s ? (customers ?? []).filter((c) => `${c.code} ${c.name}`.toLowerCase().includes(s)).slice(0, 6).map((c) => ({
      key: `c:${c.id}`, group: 'Customers', title: `${c.code} — ${c.name}`, sub: c.has_approved_mapping ? 'has an approved mapping' : 'no approved mapping yet', icon: Users,
      run: close(() => nav(`/customers/${c.id}`)),
    })) : []
    const rounds = s ? (dash?.recent_rounds ?? []).filter((r) => `${r.customer?.code} ${r.customer?.name} ${r.label}`.toLowerCase().includes(s)).slice(0, 5).map((r) => ({
      key: `r:${r.id}`, group: 'Recent imports', title: `${r.customer?.code} · ${r.label ?? 'round'}`, sub: r.status.replace('_', ' '), icon: FileSpreadsheet,
      run: close(() => nav(`/rounds/${r.id}`)),
    })) : []
    const guide = { key: 'guide', group: 'Help', title: 'Feature guide — where everything is', sub: 'รวมทุกฟีเจอร์ว่าอยู่ตรงไหนและใช้ทำอะไร', icon: BookOpen, run: close(() => nav('/guide')) }
    const guideMatch = !s || [guide.title, guide.sub, 'help', 'คู่มือ', 'ช่วย'].some((x) => x.toLowerCase().includes(s))
    return [...feats, ...custs, ...rounds, ...(guideMatch ? [guide] : [])]
  }, [q, customers, dash, isAdmin, nav, onClose, openFeature])

  if (!open) return null

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.max(0, Math.min(items.length - 1, a + 1))) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(0, a - 1)) }
    else if (e.key === 'Enter') { e.preventDefault(); items[active]?.run() }
    else if (e.key === 'Escape') { e.preventDefault(); onClose() }
  }
  let lastGroup = ''
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[12vh] px-4 bg-navy/25 dark:bg-black/60" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-label="Search features" className="card w-full max-w-[640px] overflow-hidden shadow-xl">
        <div className="flex items-center gap-2.5 px-4 h-14 border-b border-line">
          <Search size={17} className="text-signal" />
          <input ref={inputRef} aria-label="Search features" className="flex-1 min-w-0 bg-transparent outline-none text-[15px] placeholder:text-ink-3" placeholder="Search files, customers or tools…" value={q} onChange={(e) => { setQ(e.target.value); setActive(0) }} onKeyDown={onKey} />
          <span className="kbd">Esc</span>
        </div>
        <div className="max-h-[60vh] overflow-auto py-1.5">
          {items.length === 0 && <div className="px-4 py-8 text-center text-muted">Nothing matches “{q}”. ลองคำอื่น หรือเปิด Feature guide</div>}
          {items.map((it, i) => {
            const head = it.group !== lastGroup ? it.group : null
            lastGroup = it.group
            return (
              <div key={it.key}>
                {head && <div className="px-4 pt-2.5 pb-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-3">{head}</div>}
                <button data-command-active={i === active} onMouseEnter={() => setActive(i)} onClick={it.run} className={cn('w-full flex items-start gap-3 px-4 py-2 text-left', i === active && 'bg-signal-soft')}>
                  <it.icon size={16} className="mt-0.5 shrink-0 text-signal" />
                  <span className="min-w-0 flex-1">
                    <span className="block font-semibold text-navy">{it.title}</span>
                    <span className="block text-[12px] text-muted truncate">{it.sub}</span>
                  </span>
                  {i === active && <ArrowRight size={15} className="mt-0.5 text-signal shrink-0" />}
                </button>
              </div>
            )
          })}
        </div>
        <div className="px-4 py-2 border-t border-line text-[11.5px] text-muted flex items-center gap-3"><span className="kbd">↑</span><span className="kbd">↓</span> move <span className="kbd">Enter</span> open <span className="ml-auto">Ctrl K anywhere</span></div>
      </div>
    </div>
  )
}
