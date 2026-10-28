import { type ReactNode, useEffect, useRef } from 'react'
import { X } from 'lucide-react'
import { cn, STATUS_LABEL, STATUS_TONE } from '../lib/utils'

export function PageHeader({ title, subtitle, actions, crumbs }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; crumbs?: ReactNode }) {
  return (
    <div className="flex items-end justify-between gap-4 mb-5">
      <div className="min-w-0">
        {crumbs && <div className="text-muted text-[12.5px] mb-1 flex items-center gap-1.5">{crumbs}</div>}
        <h1 className="text-[22px] font-semibold tracking-[-0.01em] leading-tight truncate">{title}</h1>
        {subtitle && <div className="text-muted mt-1">{subtitle}</div>}
      </div>
      {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
    </div>
  )
}

const toneCls = {
  ok: 'bg-ok-soft text-ok border-ok/20',
  warn: 'bg-warn-soft text-warn border-warn/25',
  bad: 'bg-bad-soft text-bad border-bad/25',
  info: 'bg-info-soft text-info border-info/25',
  neutral: 'bg-surface-2 text-ink-2 border-line',
  signal: 'bg-signal-soft text-signal border-signal/20',
}
export type Tone = keyof typeof toneCls

export function Badge({ tone = 'neutral', children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return <span className={cn('chip', toneCls[tone], className)}>{children}</span>
}

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={STATUS_TONE[status] ?? 'neutral'}>{STATUS_LABEL[status] ?? status}</Badge>
}

export function RiskDot({ risk }: { risk: string }) {
  const c = { critical: 'bg-bad', high: 'bg-warn', medium: 'bg-info', low: 'bg-line-2' }[risk] ?? 'bg-line-2'
  return <span className={cn('inline-block w-1.5 h-1.5 rounded-full', c)} title={`${risk} risk`} />
}

/** Trust level of a mapping decision (ADR-019): low = safe to automate, high = a human must decide. */
export function RiskLevel({ level, factors }: { level?: string | null; factors?: string[] }) {
  if (!level) return null
  const tone: Tone = level === 'high' ? 'bad' : level === 'low' ? 'ok' : 'warn'
  return <span className='whitespace-nowrap' title={factors?.length ? factors.join(' · ') : undefined}><Badge tone={tone} className='whitespace-nowrap'>{level} risk</Badge></span>
}

/** Confidence as a thin rail — evidence, not decoration. */
export function Confidence({ value, className }: { value: number | null | undefined; className?: string }) {
  if (value === null || value === undefined) return <span className="text-muted">–</span>
  const pct = Math.round(value * 100)
  const tone = value >= 0.9 ? 'bg-ok' : value >= 0.75 ? 'bg-warn' : 'bg-bad'
  return (
    <span className={cn('inline-flex items-center gap-2', className)} title={`confidence ${pct}%`}>
      <span className="relative h-1.5 w-10 rounded-full bg-line overflow-hidden"><span className={cn('absolute inset-y-0 left-0 rounded-full', tone)} style={{ width: `${pct}%` }} /></span>
      <span className="tnum text-[12px] text-ink-2 w-8">{pct}%</span>
    </span>
  )
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: Tone }) {
  return (
    <div className="card p-4">
      <div className="text-muted text-[12.5px]">{label}</div>
      <div className={cn('text-[26px] font-semibold tracking-[-0.02em] tnum mt-1 leading-none', tone === 'bad' && 'text-bad', tone === 'warn' && 'text-warn', tone === 'ok' && 'text-ok')}>{value}</div>
      {hint && <div className="text-muted text-[12px] mt-2">{hint}</div>}
    </div>
  )
}

export function Empty({ title, body, action }: { title: string; body?: ReactNode; action?: ReactNode }) {
  return (
    <div className="card p-10 text-center">
      <div className="font-medium">{title}</div>
      {body && <div className="text-muted mt-1 max-w-md mx-auto">{body}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

export function Spinner({ className }: { className?: string }) {
  return <span className={cn('inline-block w-3.5 h-3.5 border-2 border-line-2 border-t-signal rounded-full animate-spin', className)} />
}

export function Modal({ open, onClose, title, children, footer, width = 'max-w-2xl' }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; footer?: ReactNode; width?: string }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center p-4 bg-ink/30 dark:bg-black/60" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} role="dialog" aria-modal className={cn('card w-full max-h-[88vh] flex flex-col', width)}>
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-line">
          <div className="font-semibold text-[15px]">{title}</div>
          <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close"><X size={15} /></button>
        </div>
        <div className="px-5 py-4 overflow-auto">{children}</div>
        {footer && <div className="px-5 py-3 border-t border-line flex items-center justify-end gap-2">{footer}</div>}
      </div>
    </div>
  )
}

export function Drawer({ open, onClose, title, children, width = 'w-[520px]' }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; width?: string }) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-ink/20 dark:bg-black/50" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={cn('h-full bg-surface border-l border-line shadow-xl flex flex-col max-w-full', width)}>
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-line">
          <div className="font-semibold text-[15px] truncate">{title}</div>
          <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close"><X size={15} /></button>
        </div>
        <div className="p-5 overflow-auto flex-1">{children}</div>
      </div>
    </div>
  )
}

export function Section({ title, children, actions, className }: { title: ReactNode; children: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <section className={cn('card', className)}>
      <div className="flex items-center justify-between px-4 py-3 border-b border-line">
        <h2 className="font-semibold text-[14px]">{title}</h2>
        {actions}
      </div>
      <div>{children}</div>
    </section>
  )
}

export function KV({ k, v }: { k: ReactNode; v: ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-1.5 border-b border-line last:border-0 text-[13px]">
      <div className="text-muted shrink-0">{k}</div>
      <div className="text-right min-w-0 break-words">{v}</div>
    </div>
  )
}

export function Json({ value, className }: { value: unknown; className?: string }) {
  return <pre className={cn('mono text-[12px] leading-relaxed whitespace-pre-wrap break-words bg-surface-2 rounded-[var(--radius-md)] p-3 overflow-auto', className)}>{JSON.stringify(value, null, 2)}</pre>
}

export function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { id: T; label: ReactNode; count?: number }[] }) {
  return (
    <div className="flex items-center gap-1 border-b border-line">
      {items.map((it) => (
        <button key={it.id} onClick={() => onChange(it.id)} className={cn('relative px-3 py-2 text-[13px] font-medium text-muted hover:text-ink -mb-px border-b-2 border-transparent', value === it.id && 'text-ink border-signal')}>
          {it.label}
          {it.count !== undefined && <span className="ml-1.5 text-[11.5px] tnum text-muted">{it.count}</span>}
        </button>
      ))}
    </div>
  )
}

export function FindingRow({ f }: { f: { severity: string; kind: string; message: string; target?: string | null } }) {
  const tone = f.severity === 'error' ? 'bad' : f.severity === 'warning' ? 'warn' : 'info'
  return (
    <div className="flex items-start gap-2 py-1.5 text-[12.5px]">
      <Badge tone={tone} className="h-5 px-1.5 text-[11px] shrink-0">{f.kind}</Badge>
      <div className="min-w-0"><span>{f.message}</span>{f.target && <span className="text-muted mono ml-1.5">{f.target}</span>}</div>
    </div>
  )
}
