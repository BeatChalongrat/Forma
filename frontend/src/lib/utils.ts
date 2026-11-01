import { clsx, type ClassValue } from 'clsx'

export const cn = (...i: ClassValue[]) => clsx(i)

export function fmtInt(n: number | null | undefined): string {
  if (n === null || n === undefined) return '–'
  return new Intl.NumberFormat('en-US').format(n)
}
export function fmtNum(n: number | null | undefined, d = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '–'
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: d, minimumFractionDigits: 0 }).format(n)
}
export function fmtPct(x: number | null | undefined, d = 0): string {
  if (x === null || x === undefined) return '–'
  return `${(x * 100).toFixed(d)}%`
}
export function fmtBytes(b: number): string {
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`
  return `${(b / 1024 / 1024).toFixed(1)} MB`
}
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return '–'
  const d = new Date(iso)
  const s = Math.max(0, (Date.now() - d.getTime()) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  if (s < 86400 * 7) return `${Math.floor(s / 86400)} d ago`
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '–'
  return new Date(iso).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}
export function shortId(id: string | null | undefined): string {
  if (!id) return '–'
  const parts = id.split('_')
  return parts.length > 1 ? `${parts[0]}…${id.slice(-5)}` : id.slice(-6)
}
export function getPointer(obj: unknown, pointer: string): unknown {
  let cur: unknown = obj
  for (const p of pointer.split('/').filter(Boolean)) {
    if (Array.isArray(cur)) cur = cur[Number(p)]
    else if (cur && typeof cur === 'object') cur = (cur as Record<string, unknown>)[p]
    else return undefined
  }
  return cur
}
export function cellText(v: unknown): string {
  if (v === null || v === undefined) return ''
  if (typeof v === 'number') return fmtNum(v, 4)
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  if (Array.isArray(v)) return v.map((x) => (typeof x === 'object' ? JSON.stringify(x) : String(x))).join(' · ')
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}
export function describeExpr(e: { op: string; name?: string; value?: unknown; args?: unknown[]; params?: Record<string, unknown> } | undefined | null): string {
  if (!e) return ''
  if (e.op === 'col') return e.name ?? ''
  if (e.op === 'const') return `= ${JSON.stringify(e.value)}`
  const args = (e.args ?? []).map((a) => describeExpr(a as never)).join(', ')
  const p = e.params && Object.keys(e.params).length ? ` ${Object.entries(e.params).map(([k, v]) => `${k}=${typeof v === 'object' ? '{…}' : String(v)}`).join(' ')}` : ''
  return `${e.op}(${args})${p}`
}
export const STATUS_LABEL: Record<string, string> = {
  uploaded: 'Uploaded', profiled: 'File read', review: 'Needs review', auto_approved: 'Auto-approved', approved: 'Fields approved', transformed: 'Data ready for review',
  cancelled: 'Cancelled', moved: 'Moved',
  approved_data: 'Data approved', published: 'Exported', blocked: 'Blocked', rejected: 'Rejected', failed: 'Failed', proposed: 'Proposed', superseded: 'Superseded', draft: 'Draft', open: 'Open',
}
export const STATUS_TONE: Record<string, 'ok' | 'warn' | 'bad' | 'info' | 'neutral'> = {
  uploaded: 'neutral', profiled: 'neutral', review: 'warn', auto_approved: 'ok', approved: 'ok', transformed: 'info', approved_data: 'ok', published: 'ok', blocked: 'bad', rejected: 'bad', failed: 'bad',
  proposed: 'warn', superseded: 'neutral', cancelled: 'neutral', moved: 'neutral', draft: 'neutral', open: 'warn', done: 'ok', running: 'info', queued: 'neutral',
}

/** Python-style regex (may start with (?i)) → JS RegExp; null when it cannot be compiled. */
export function pyRegex(pattern: string | null | undefined): RegExp | null {
  if (!pattern) return null
  let flags = ''
  let src = pattern
  const m = /^\(\?([a-z]+)\)/.exec(src)
  if (m) { src = src.slice(m[0].length); if (m[1].includes('i')) flags += 'i' }
  try { return new RegExp(src, flags) } catch { return null }
}

export const FORMAT_LABEL: Record<string, string> = {
  identical: 'Same format', header_moved: 'Header moved', columns_added: 'Columns added',
  columns_renamed: 'Columns renamed', columns_removed: 'Missing columns', columns_moved: 'Columns moved to another sheet',
  types_changed: 'Data types changed',
  sheets_added: 'Sheets added', sheets_removed: 'Missing sheets', unrelated_file: 'Check customer',
}
export const formatLabel = (value: string) => FORMAT_LABEL[value] ?? value.replace(/_/g, ' ')
