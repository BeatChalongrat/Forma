import { Localized } from '../lib/locale'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useVirtualizer } from '@tanstack/react-virtual'
import { Download, Search, Undo2, History, GitCompareArrows, CircleAlert, CircleCheck, Route, Trash2, Activity, X, PanelRight } from 'lucide-react'
import { api, ApiError, type Answer, type AnomalyReport, type FieldSpec, type Operation, type Impact, type RowsPage, type Trace } from '../lib/api'
import { AssistantIcon } from '../lib/icons'
import { cn, cellText, fmtInt, fmtNum, getPointer, timeAgo, describeExpr } from '../lib/utils'
import { Badge, Drawer, HelpStrip, Modal, Spinner, StatusBadge, Tabs, FindingRow } from '../components/ui'
import { WorkspacePanel } from '../components/WorkspacePanel'
import { useToast } from '../lib/toast'

const ROW_H = 32
const PAGE = 500

type Pending = Operation & { label: string; batch?: number }
type Cell = { r: number; c: number }

const isQuestion = (t: string) => /^(ทำไม|เพราะอะไร|อะไร|ทำไง|ยังไง|why|what|how|which)/i.test(t.trim()) || t.trim().endsWith('?')

// typed value from what a person typed or pasted, by the field's JSON type (null = empty)
function parseValue(f: FieldSpec, text: string): { ok: true; value: unknown } | { ok: false; error: string } {
  const raw = text.trim()
  if (raw === '') return { ok: true, value: null }
  if (f.json_type.includes('number') || f.json_type.includes('integer')) {
    const n = Number(raw.replace(/,/g, ''))
    if (Number.isNaN(n)) return { ok: false, error: `${f.name}: "${raw}" is not a number` }
    return { ok: true, value: f.json_type.includes('integer') ? Math.round(n) : n }
  }
  if (f.json_type.includes('boolean')) return { ok: true, value: ['true', '1', 'yes', 'y'].includes(raw.toLowerCase()) }
  let value: unknown = raw
  if (f.json_type.includes('array')) { try { value = raw.startsWith('[') ? JSON.parse(raw) : raw.split(/[|,]/).map((x) => x.trim()).filter(Boolean) } catch { return { ok: false, error: `${f.name}: invalid list` } } }
  if (f.enum && !f.enum.includes(value)) return { ok: false, error: `${f.name}: must be one of ${f.enum.join(', ')}` }
  return { ok: true, value }
}

const EDITOR_TABS = ['changes', 'findings', 'versions', 'diff', 'copilot'] as const
type EditorTab = (typeof EDITOR_TABS)[number]

export function DataEditor() {
  const { datasetId = '' } = useParams()
  const qc = useQueryClient()
  const nav = useNavigate()
  const toast = useToast()
  const { data: ds } = useQuery({ queryKey: ['dataset', datasetId], queryFn: () => api.dataset(datasetId) })
  const [q, setQ] = useState('')
  const [qDebounced, setQDebounced] = useState('')
  const [sort, setSort] = useState<{ ptr: string; desc: boolean } | null>(null)
  const [offset, setOffset] = useState(0)
  useEffect(() => { const t = setTimeout(() => { setQDebounced(q); setOffset(0) }, 300); return () => clearTimeout(t) }, [q])
  const { data: page, isLoading } = useQuery({ queryKey: ['rows', datasetId, offset, qDebounced, sort], queryFn: () => api.rows(datasetId, { offset, limit: PAGE, q: qDebounced || undefined, sort: sort?.ptr, desc: sort?.desc }), placeholderData: (prev) => prev })

  const [pending, setPending] = useState<Pending[]>([])
  const [sel, setSel] = useState<Cell | null>(null)
  const [anchor, setAnchor] = useState<Cell | null>(null) // other corner of a range (shift-click / shift-arrows)
  const [picked, setPicked] = useState<Set<string>>(new Set()) // rows ticked for a bulk edit
  const [editing, setEditing] = useState<{ r: number; c: number; value: string } | null>(null)
  const [drawer, setDrawer] = useState<{ kind: 'record' | 'lineage'; identity: string; pointer?: string } | null>(null)
  const [saveOpen, setSaveOpen] = useState(false)
  const [params] = useSearchParams() // ?tab= opens a panel, ?tool=trace explains tracing (links from the round page, the guide, Ctrl+K)
  const [rightTab, setActiveTab] = useState<EditorTab>(() => (EDITOR_TABS as readonly string[]).includes(params.get('tab') ?? '') ? (params.get('tab') as EditorTab) : 'changes')
  const [rightOpen, setRightOpen] = useState(() => !!params.get('tab'))
  const [assistantOpen, setAssistantOpen] = useState(false)
  const setRightTab = (tab: EditorTab) => { setActiveTab(tab); setRightOpen(true) }
  const [traceTip, setTraceTip] = useState(() => params.get('tool') === 'trace')
  const [copilot, setCopilot] = useState('')
  const [answer, setAnswer] = useState<Answer | null>(null)
  const [ack, setAck] = useState<{ high: number; headline: string } | null>(null)
  const batchSeq = useRef(0)

  const columns: FieldSpec[] = useMemo(() => (page?.columns ?? []).filter((f) => !f.system && !f.in_collection && !f.is_collection && !f.json_type.includes('object')).concat((page?.columns ?? []).filter((f) => f.is_collection)), [page])
  const identityOf = useCallback((row: Record<string, unknown>) => (page?.identity_keys ?? []).map((k) => String(getPointer(row, k))).join('|'), [page])

  // overlay pending edits onto rows for display
  const rows = useMemo(() => {
    const base = page?.rows ?? []
    if (!pending.length) return base
    const byId = new Map<string, Record<string, unknown>>()
    const deleted = new Set<string>()
    pending.forEach((p) => { if (p.kind === 'delete_row' && p.identity) deleted.add(p.identity); if (p.kind === 'delete_rows' && p.identities) p.identities.forEach((i) => deleted.add(i)) })
    const out = base.filter((r) => !deleted.has(identityOf(r))).map((r) => structuredClone(r))
    out.forEach((r) => byId.set(identityOf(r), r))
    pending.forEach((p) => {
      if (p.kind === 'set_cell' && p.identity && p.target) { const r = byId.get(p.identity); if (r) setPointerLocal(r, p.target, p.value) }
      if (p.kind === 'set_field' && p.identities && p.target && !p.target.includes('*')) p.identities.forEach((i) => { const r = byId.get(i); if (r) setPointerLocal(r, p.target!, p.value) })
    })
    return out
  }, [page, pending, identityOf])
  const editedCells = useMemo(() => new Set(pending.flatMap((p) => (p.kind === 'set_cell' ? [`${p.identity}|${p.target}`] : p.kind === 'set_field' && p.identities ? p.identities.map((i) => `${i}|${p.target}`) : []))), [pending])

  // virtualization
  const scrollRef = useRef<HTMLDivElement>(null)
  const rowVirt = useVirtualizer({ count: rows.length, getScrollElement: () => scrollRef.current, estimateSize: () => ROW_H, overscan: 12 })

  // keyboard navigation
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (editing || drawer || saveOpen || document.querySelector('[role="dialog"]')) return
      if ((e.target as HTMLElement)?.closest('input, textarea, select, button, a, [contenteditable=true]')) return
      if ((e.metaKey || e.ctrlKey) && e.key === 'z') { e.preventDefault(); undo(); return }
      if (!sel) return
      const move = (dr: number, dc: number) => {
        e.preventDefault()
        if (e.shiftKey) setAnchor((a) => a ?? sel)
        else setAnchor(null)
        setSel((s) => s && { r: Math.min(rows.length - 1, Math.max(0, s.r + dr)), c: Math.min(columns.length - 1, Math.max(0, s.c + dc)) })
      }
      if (e.key === 'ArrowDown') move(1, 0)
      if (e.key === 'ArrowUp') move(-1, 0)
      if (e.key === 'ArrowRight') move(0, 1)
      if (e.key === 'ArrowLeft') move(0, -1)
      if (e.key === 'Enter') { e.preventDefault(); startEdit(sel.r, sel.c) }
      if (e.key === 'Escape') setAnchor(null)
      if ((e.metaKey || e.ctrlKey) && e.key === 'c') { e.preventDefault(); copyRange() }
      if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); clearRange() }
      if (e.key === 't' || e.key === 'T') { e.preventDefault(); traceSelected() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel, anchor, editing, drawer, saveOpen, rows.length, columns.length, pending])
  useEffect(() => { if (sel) rowVirt.scrollToIndex(sel.r) }, [sel, rowVirt])
  // paste a block from Excel / Sheets (tab-separated) starting at the selected cell
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      if (editing || drawer || saveOpen || !sel || document.querySelector('[role="dialog"]')) return
      if ((e.target as HTMLElement)?.closest('input, textarea, select, button, a, [contenteditable=true]')) return
      const text = e.clipboardData?.getData('text/plain')
      if (!text) return
      e.preventDefault()
      pasteBlock(text.replace(/\r/g, '').replace(/\n$/, '').split('\n').map((line) => line.split('\t')))
    }
    window.addEventListener('paste', onPaste)
    return () => window.removeEventListener('paste', onPaste)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel, editing, drawer, saveOpen, rows, columns, pending])

  const range = sel ? { r0: Math.min(sel.r, (anchor ?? sel).r), r1: Math.max(sel.r, (anchor ?? sel).r), c0: Math.min(sel.c, (anchor ?? sel).c), c1: Math.max(sel.c, (anchor ?? sel).c) } : null
  const inRange = (r: number, c: number) => !!range && (range.r0 !== range.r1 || range.c0 !== range.c1) && r >= range.r0 && r <= range.r1 && c >= range.c0 && c <= range.c1
  const undo = () => setPending((p) => { const last = p[p.length - 1]; return last?.batch === undefined ? p.slice(0, -1) : p.filter((x) => x.batch !== last.batch) })
  // stage set_cell edits; a later edit of the same cell replaces the earlier one
  const stageCells = (cells: { identity: string; f: FieldSpec; value: unknown; before: unknown }[]) => {
    if (!cells.length) return
    const batch = ++batchSeq.current
    const keys = new Set(cells.map((x) => `${x.identity}|${x.f.pointer}`))
    setPending((p) => [...p.filter((x) => !(x.kind === 'set_cell' && keys.has(`${x.identity}|${x.target}`))), ...cells.map((x) => ({ kind: 'set_cell' as const, identity: x.identity, target: x.f.pointer, value: x.value, batch, label: `${x.identity} · ${x.f.name}: ${cellText(x.before) || '∅'} → ${cellText(x.value) || '∅'}` }))])
  }
  const original = (row: Record<string, unknown>) => page?.rows.find((r) => identityOf(r) === identityOf(row)) ?? row
  const pasteBlock = (grid: string[][]) => {
    if (!sel || ds?.status === 'published') return
    const cells: { identity: string; f: FieldSpec; value: unknown; before: unknown }[] = []
    const errs: string[] = []
    grid.forEach((line, i) => line.forEach((text, j) => {
      const row = rows[sel.r + i]
      const f = columns[sel.c + j]
      if (!row || !f || f.is_collection) return
      const v = parseValue(f, text)
      if (!v.ok) { errs.push(v.error); return }
      const before = getPointer(original(row), f.pointer)
      if (JSON.stringify(before) !== JSON.stringify(v.value)) cells.push({ identity: identityOf(row), f, value: v.value, before })
    }))
    stageCells(cells)
    toast.push({ kind: errs.length ? 'bad' : 'ok', title: `Pasted ${cells.length} cell${cells.length === 1 ? '' : 's'}`, detail: errs.length ? `${errs.length} skipped — ${errs[0]}` : 'Review and save when ready.' })
  }
  const copyRange = () => {
    if (!range) return
    const lines = []
    for (let r = range.r0; r <= range.r1; r++) lines.push(columns.slice(range.c0, range.c1 + 1).map((f) => cellText(getPointer(rows[r], f.pointer))).join('\t'))
    void navigator.clipboard?.writeText(lines.join('\n'))
    toast.push({ kind: 'info', title: `Copied ${(range.r1 - range.r0 + 1) * (range.c1 - range.c0 + 1)} cells` })
  }
  const clearRange = () => {
    if (!range || ds?.status === 'published') return
    const cells: { identity: string; f: FieldSpec; value: unknown; before: unknown }[] = []
    for (let r = range.r0; r <= range.r1; r++) for (let c = range.c0; c <= range.c1; c++) {
      const f = columns[c]
      if (!f || f.is_collection || f.required) continue
      const before = getPointer(original(rows[r]), f.pointer)
      if (before !== null && before !== undefined) cells.push({ identity: identityOf(rows[r]), f, value: null, before })
    }
    stageCells(cells)
  }
  const traceSelected = () => {
    if (!sel || !rows[sel.r]) return
    const f = columns[sel.c]
    setDrawer({ kind: 'lineage', identity: identityOf(rows[sel.r]), pointer: f && !f.is_collection ? f.pointer : undefined })
  }

  const startEdit = (r: number, c: number) => {
    const f = columns[c]
    if (!f || f.is_collection || ds?.status === 'published') return
    const v = getPointer(rows[r], f.pointer)
    setEditing({ r, c, value: v === null || v === undefined ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v) })
  }
  const commitEdit = () => {
    if (!editing) return
    const f = columns[editing.c]
    const row = rows[editing.r]
    const v = parseValue(f, editing.value)
    if (!v.ok) { toast.push({ kind: 'bad', title: v.error }); return }
    const before = getPointer(original(row), f.pointer)
    if (JSON.stringify(before) !== JSON.stringify(v.value)) stageCells([{ identity: identityOf(row), f, value: v.value, before }])
    setEditing(null)
  }

  const preview = useMutation({ mutationFn: () => api.previewChanges(datasetId, pending.map(strip)) })
  const apply = useMutation({
    mutationFn: (b: { kind: string; note: string }) => api.applyChanges(datasetId, { operations: pending.map(strip), kind: b.kind, actor: 'operator', note: b.note || undefined }),
    onSuccess: ({ dataset }) => { qc.invalidateQueries(); setPending([]); setSaveOpen(false); toast.push({ kind: 'ok', title: `Saved as dataset v${dataset.version}` }); nav(`/datasets/${dataset.id}`) },
    onError: (e: Error & { details?: { errors?: { pointer: string; message: string }[] } }) => toast.push({ kind: 'bad', title: 'Changes rejected by the schema', detail: e.details?.errors?.slice(0, 3).map((x) => `${x.pointer}: ${x.message}`).join('; ') ?? e.message }),
  })
  const rollback = useMutation({ mutationFn: () => api.rollback(datasetId), onSuccess: (d) => { qc.invalidateQueries(); toast.push({ kind: 'ok', title: `Rolled back → v${d.version}` }); nav(`/datasets/${d.id}`) } })
  const approve = useMutation({
    mutationFn: (b: { acknowledge_anomalies?: boolean; comment?: string }) => api.approveDataset(datasetId, b),
    onSuccess: () => { qc.invalidateQueries(); setAck(null); toast.push({ kind: 'ok', title: 'Dataset approved' }) },
    // the policy holds data with high-severity anomalies until a person acknowledges them (ADR-022)
    // records that break the target format are never approved — no acknowledgement overrides it (ADR-026)
    onError: (e: Error) => { const d = e instanceof ApiError ? (e.details as { high?: number; headline?: string; invalid_records?: number } | undefined) : undefined; if (d?.invalid_records) { setRightTab('findings'); toast.push({ kind: 'bad', title: 'Not approved', detail: e.message }) } else if (d?.high) { setAck({ high: d.high, headline: d.headline ?? '' }); setRightTab('diff') } else toast.push({ kind: 'bad', title: 'Approve failed', detail: e.message }) },
  })
  const doExport = useMutation({ mutationFn: (fmt: string) => api.exportDataset(datasetId, fmt), onSuccess: (a) => { qc.invalidateQueries(); window.open(api.downloadUrl(a.id), '_blank'); toast.push({ kind: 'ok', title: `Exported ${a.row_count} rows as ${a.fmt.toUpperCase()}` }) }, onError: (e: Error) => { setRightTab('findings'); toast.push({ kind: 'bad', title: 'Not exported', detail: e.message }) } })
  const question = useMutation({ mutationFn: () => api.ask(datasetId, copilot), onSuccess: (a) => { setAnswer(a); setRightTab('copilot'); setCopilot('') }, onError: (e: Error) => toast.push({ kind: 'bad', title: 'Could not answer', detail: e.message }) })
  const submitCopilot = () => { if (!copilot.trim()) return; if (isQuestion(copilot)) question.mutate(); else ask.mutate() }
  const ask = useMutation({
    mutationFn: () => api.copilot(datasetId, copilot),
    onSuccess: (r) => {
      if (r.needs_clarification) { toast.push({ kind: 'info', title: 'Copilot needs more detail', detail: r.needs_clarification }); return }
      if (!r.operations.length) { toast.push({ kind: 'info', title: 'No operations planned' }); return }
      const batch = ++batchSeq.current
      setPending((p) => [...p, ...r.operations.map((o) => ({ ...o, batch, label: `${o.explanation ?? o.kind} · ${r.impact ? `${r.impact.rows_affected} rows` : ''}` }))])
      setCopilot('')
      toast.push({ kind: 'ok', title: 'Plan added to pending changes', detail: `${r.impact?.rows_affected ?? '?'} rows affected — review and save when ready.` })
    },
  })
  const { data: report } = useQuery({ queryKey: ['change-report', datasetId], queryFn: () => api.changeReport(datasetId), enabled: rightTab === 'diff' })

  const pricePointer = (page?.columns ?? []).map((c) => c.pointer).find((p) => (page?.columns ?? []).find((c) => c.pointer === p)?.semantic === 'price.amount')
  const stageBulk = (op: Operation, label: string) => { const batch = ++batchSeq.current; setPending((p) => [...p, { ...op, identities: [...picked], batch, label }]) }
  const pageIds = rows.map(identityOf)
  const allPicked = pageIds.length > 0 && pageIds.every((i) => picked.has(i))

  if (!ds) return <div className="p-10 text-center"><Spinner /></div>
  const invalid = ds.invalid_records ?? 0
  const errors = ds.findings.filter((f) => f.severity === 'error' && f.kind !== 'validation').length
  const widths = (f: FieldSpec) => (f.is_collection ? 220 : f.semantic === 'product_name' || f.semantic === 'description' ? 240 : f.json_type.includes('number') ? 110 : 140)

  return (
    <div className="workspace">
      <div className="workspace-toolbar">
        <Link to={`/rounds/${ds.round_id}`} className="text-muted hover:text-ink text-[12.5px]">← Round</Link>
        <span className="heading">Data Editor</span>
        <span className="tnum text-muted">v{ds.version}</span>
        <StatusBadge status={ds.status} />
        <span className="tnum text-muted text-[12.5px]">{fmtInt(ds.row_count)} rows</span>
        {invalid > 0
          ? <button onClick={() => setRightTab('findings')} title="These records do not match the target format: approval and export wait until they are fixed or removed"><Badge tone="bad"><CircleAlert size={12} /> {fmtInt(invalid)} record{invalid > 1 ? 's' : ''} need correction</Badge></button>
          : <Badge tone="ok"><CircleCheck size={12} /> Format checked</Badge>}
        {errors > 0 && <Badge tone="bad"><CircleAlert size={12} /> {errors} other error{errors > 1 ? 's' : ''}</Badge>}
        <div className="relative min-w-0"><Search size={13} className="absolute left-2.5 top-2.5 text-muted" /><input className="input pl-7 w-52 max-w-full" placeholder="Search any field…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <div className="ml-auto flex items-center gap-2 flex-wrap">
          <button className="btn btn-ghost btn-sm" aria-expanded={assistantOpen} onClick={() => setAssistantOpen(!assistantOpen)}><AssistantIcon size={15} /> Assistant</button>
          <button className="btn btn-ghost btn-sm" aria-expanded={rightOpen} onClick={() => setRightOpen(!rightOpen)}><PanelRight size={15} /> Review{pending.length > 0 ? ` (${pending.length})` : ''}</button>

          {pending.length > 0 && <><button className="btn btn-sm" onClick={() => setPending([])}><Undo2 size={13} /> Discard</button><button className="btn btn-sm btn-primary" onClick={() => { preview.mutate(); setSaveOpen(true) }}>Review {pending.length} change{pending.length > 1 ? 's' : ''}</button></>}
          {!pending.length && ds.status === 'draft' && <button className="btn btn-sm" disabled={approve.isPending || invalid > 0} title={invalid ? `Fix or remove the ${invalid} record${invalid > 1 ? 's' : ''} that do not match the target format first` : undefined} onClick={() => approve.mutate({})}><CircleCheck size={13} /> Approve data</button>}
          {!pending.length && ds.parent_id && <button className="btn btn-sm" onClick={() => rollback.mutate()} title="New version with the parent's content"><History size={13} /> Roll back</button>}
          {!pending.length && (ds.status === 'approved' || ds.status === 'published') && <div className="flex items-center rounded-[var(--radius-md)] border border-line overflow-hidden">{['json', 'jsonl', 'csv'].map((f) => <button key={f} className="h-8 px-2.5 text-[12.5px] font-medium hover:bg-surface-2 border-r border-line last:border-0 flex items-center gap-1" onClick={() => doExport.mutate(f)}><Download size={12} /> {f.toUpperCase()}</button>)}</div>}
        </div>
      </div>

      {assistantOpen && <div className="px-5 py-3 border-b border-line bg-surface shrink-0">
        <div className="flex items-center gap-2"><AssistantIcon size={16} className="text-muted" />
          <input aria-label="Ask the assistant" className="input flex-1" placeholder="Ask about this data, or describe a change…" value={copilot} onChange={(e) => setCopilot(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && submitCopilot()} />
          <button className="btn btn-sm btn-primary" disabled={!copilot.trim() || ask.isPending || question.isPending} onClick={submitCopilot}>{ask.isPending || question.isPending ? <Spinner /> : isQuestion(copilot) ? 'Ask' : 'Preview'}</button>
        </div><p className="text-[12.5px] text-muted mt-2 ml-6">Try “ลดราคา 5% เฉพาะสินค้าที่มีสต็อก” — you review the changes before saving.</p>
      </div>}

      <div className="px-5 py-2 border-b border-line bg-surface shrink-0 flex flex-col gap-2">
        {traceTip && (
          <div className="flex items-center gap-3 rounded-[10px] border border-brand-blue/40 bg-signal-soft px-3.5 py-2 text-[12.5px]">
            <Route size={15} className="text-signal shrink-0" />
            <span className="flex-1"><b className="text-navy">Trace a value:</b> click any cell, then press <span className="kbd">T</span> or <b>Trace this value</b> (bottom bar) to see the file, sheet, row and column it came from — and why it changed. <span className="text-muted">— คลิกเซลล์ แล้วกด T เพื่อดูที่มา</span></span>
            <button className="btn btn-ghost btn-sm" onClick={() => setTraceTip(false)} aria-label="Hide"><X size={13} /></button>
          </div>
        )}
        <HelpStrip id="editor">
          Edit like Excel: double-click or <span className="kbd">↵</span> to edit, paste a block from Excel, tick rows to change many at once. Every change waits in <b>Pending</b> until you review its impact and save it as a new version. Use <b>Changes &amp; anomalies</b> before approving. <span className="text-muted">— แก้เหมือน Excel ทุกการแก้รออยู่ใน Pending จนกว่าจะกดบันทึก ดู Changes &amp; anomalies ก่อนกด Approve data</span>
        </HelpStrip>
      </div>

      <div className="workspace-body">
        {/* grid */}
        <div className="min-w-0 flex-1 flex flex-col">
          {picked.size > 0 && ds.status !== 'published' && (
            <BulkBar count={picked.size} fields={columns.filter((c) => !c.is_collection && !c.system)} pricePointer={pricePointer}
              onSet={(fld, text) => { const v = parseValue(fld, text); if (!v.ok) { toast.push({ kind: 'bad', title: v.error }); return } stageBulk({ kind: 'set_field', target: fld.pointer, value: v.value }, `set ${fld.name} = ${cellText(v.value) || '∅'} on ${picked.size} rows`) }}
              onScale={(pct) => pricePointer && stageBulk({ kind: 'scale_field', target: pricePointer, factor: Math.round((1 + pct / 100) * 10000) / 10000 }, `price ${pct > 0 ? '+' : ''}${pct}% on ${picked.size} rows`)}
              onDelete={() => { stageBulk({ kind: 'delete_rows' }, `delete ${picked.size} rows`); setPicked(new Set()) }}
              onClear={() => setPicked(new Set())} />
          )}
          <div ref={scrollRef} className="flex-1 min-h-0 overflow-auto relative select-none" tabIndex={0} aria-label="Data grid. Select a cell, then use arrow keys to navigate and Enter to edit." onMouseDown={() => editing && commitEdit()}>
            <div className="sticky top-0 z-10 flex bg-surface border-b border-line" style={{ width: 56 + 36 + columns.reduce((a, f) => a + widths(f), 0) }}>
              <label className="w-14 shrink-0 px-2 text-[11px] text-muted leading-8 tnum border-r border-line flex items-center gap-1.5" title="Select every row on this page"><input type="checkbox" className="accent-[var(--color-signal)]" checked={allPicked} onChange={() => setPicked((p) => { const n = new Set(p); pageIds.forEach((i) => (allPicked ? n.delete(i) : n.add(i))); return n })} />#</label>
              {columns.map((f) => (
                <button key={f.pointer} className="shrink-0 h-8 px-2.5 text-left text-[12px] font-medium text-ink-2 border-r border-line hover:bg-surface-2 flex items-center gap-1.5 truncate" style={{ width: widths(f) }} onClick={() => !f.is_collection && setSort((s) => (s?.ptr === f.pointer ? (s.desc ? null : { ptr: f.pointer, desc: true }) : { ptr: f.pointer, desc: false }))} title={f.pointer}>
                  <span className="truncate">{f.pointer.slice(1).replace('/', '.')}</span>
                  {sort?.ptr === f.pointer && <span className="text-signal">{sort.desc ? '↓' : '↑'}</span>}
                </button>
              ))}
              <div className="w-9 shrink-0" />
            </div>
            {isLoading && !page ? <div className="p-6 text-muted"><Spinner /></div> : null}
            <div style={{ height: rowVirt.getTotalSize(), position: 'relative' }}>
              {rowVirt.getVirtualItems().map((vi) => {
                const row = rows[vi.index]
                const id = identityOf(row)
                const rowErr = page?.findings.some((f) => f.identity === id && f.severity === 'error')
                return (
                  <div key={vi.key} className={cn('absolute left-0 flex border-b border-line/70 hover:bg-surface-2/50', rowErr && 'bg-bad-soft/40')} style={{ top: vi.start, height: ROW_H, width: 56 + 36 + columns.reduce((a, f) => a + widths(f), 0) }}>
                    <div className={cn('w-14 shrink-0 px-2 text-[11px] text-muted leading-8 tnum border-r border-line/70 flex items-center gap-1.5', picked.has(id) && 'bg-signal-soft')}>
                      <input type="checkbox" className="accent-[var(--color-signal)]" checked={picked.has(id)} onChange={() => setPicked((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n })} />
                      <button className="hover:text-signal" onClick={() => setDrawer({ kind: 'record', identity: id })} title="Open record" aria-label={`Open record ${id}`}>{offset + vi.index + 1}</button>
                    </div>
                    {columns.map((f, c) => {
                      const v = getPointer(row, f.pointer)
                      const isSel = sel?.r === vi.index && sel?.c === c
                      const isEd = editing?.r === vi.index && editing?.c === c
                      const edited = editedCells.has(`${id}|${f.pointer}`)
                      return (
                        <div key={f.pointer} className={cn('shrink-0 h-8 px-2.5 text-[12.5px] leading-8 border-r border-line/70 truncate relative', f.json_type.includes('number') && 'text-right tnum', isSel && 'outline outline-2 outline-signal -outline-offset-2 z-[1]', inRange(vi.index, c) && !isSel && 'bg-signal-soft/70', edited && 'bg-signal-soft text-signal')} style={{ width: widths(f) }}
                          onMouseDown={(e) => { e.stopPropagation(); if (editing && !isEd) commitEdit(); if (e.shiftKey && sel) setAnchor((a) => a ?? sel); else setAnchor(null); setSel({ r: vi.index, c }) }} onDoubleClick={() => (f.is_collection ? setDrawer({ kind: 'record', identity: id }) : startEdit(vi.index, c))}>
                          {isEd ? (
                            <input autoFocus className="absolute inset-0 w-full h-full px-2.5 bg-surface text-ink outline-none border-2 border-signal" value={editing.value} onChange={(e) => setEditing({ ...editing, value: e.target.value })} onKeyDown={(e) => { if (e.key === 'Enter') commitEdit(); if (e.key === 'Escape') setEditing(null) }} onMouseDown={(e) => e.stopPropagation()} />
                          ) : f.is_collection ? <CollectionCell value={v} fields={(page?.columns ?? []).filter((x) => x.in_collection === f.pointer)} /> : (v === null || v === undefined ? <span className="text-muted">∅</span> : cellText(v))}
                        </div>
                      )
                    })}
                    <button className="w-9 shrink-0 grid place-items-center text-muted hover:text-bad" title="Delete row" aria-label={`Delete record ${id}`} disabled={ds.status === 'published'} onClick={() => setPending((p) => [...p, { kind: 'delete_row', identity: id, label: `delete ${id}` }])}><Trash2 size={12} /></button>
                  </div>
                )
              })}
            </div>
          </div>
          <div className="editor-footer text-[12px] text-muted">
            <span className="tnum">{page ? `${fmtInt(offset + 1)}–${fmtInt(Math.min(offset + PAGE, page.total))} of ${fmtInt(page.total)}` : ''}</span>
            <button className="btn btn-ghost btn-sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>‹ prev</button>
            <button className="btn btn-ghost btn-sm" disabled={!page || offset + PAGE >= page.total} onClick={() => setOffset(offset + PAGE)}>next ›</button>
            <button className={cn('btn btn-sm', traceTip ? 'btn-outline' : 'btn-ghost')} disabled={!sel} onClick={traceSelected} title={sel ? 'ค่านี้มาจากเซลล์ไหนในไฟล์' : 'คลิกที่เซลล์ก่อน'}><Route size={12} /> Trace this value</button>
            <span className="editor-shortcuts ml-auto flex items-center gap-3"><span><span className="kbd">↵</span> edit</span><span><span className="kbd">⇧</span>+click range</span><span><span className="kbd">Ctrl C</span>/<span className="kbd">Ctrl V</span> copy · paste</span><span><span className="kbd">Del</span> clear</span><span><span className="kbd">T</span> trace</span><span><span className="kbd">Ctrl Z</span> undo</span></span>
          </div>
        </div>

        {/* right rail */}
        <WorkspacePanel open={rightOpen} onClose={() => setRightOpen(false)} title="Review data">
          <Tabs menu value={rightTab} onChange={setRightTab} items={[{ id: 'changes', label: 'Unsaved', count: pending.length, hint: 'การแก้ที่ยังไม่บันทึก' }, { id: 'diff', labelText: 'Changes & anomalies', hint: 'เทียบกับรอบก่อน และของที่ดูผิดปกติ', label: <span className="flex items-center gap-1">Changes &amp; anomalies{ack && <Activity size={12} className="text-bad" />}</span> }, { id: 'copilot', label: 'Assistant', hint: 'คำตอบจาก Copilot' }, { id: 'findings', label: 'Problems', count: ds.findings.length, hint: 'record ที่ไม่ตรง format และปัญหาอื่น' }, { id: 'versions', label: 'Versions', count: ds.history?.length, hint: 'ทุกเวอร์ชัน ย้อนกลับได้' }]} />
          <div className="flex-1 overflow-auto p-3 text-[12.5px]">
            {rightTab === 'changes' && (pending.length ? (
              <ul className="flex flex-col gap-1">{pending.map((p, i) => <li key={i} className="flex items-start gap-2 py-1 border-b border-line/70"><Badge tone={p.kind.startsWith('delete') ? 'bad' : 'signal'} className="min-h-6 px-1.5 text-[12px] shrink-0">{p.kind.replace('_', ' ')}</Badge><span className="flex-1 break-words">{p.label}</span><button className="text-muted hover:text-bad" onClick={() => setPending((xs) => xs.filter((_, j) => j !== i))}>×</button></li>)}</ul>
            ) : ds.status === 'published' ? <div className="text-muted leading-relaxed">This exported version is read-only. Use Versions to inspect its history, or open a record to trace where its values came from.</div> : <div className="text-muted leading-relaxed">Edit cells directly (double-click or <span className="kbd">↵</span>), delete rows, or ask the copilot. Changes stay here until you review their impact and save them as a new version — nothing is overwritten.</div>)}
            {rightTab === 'findings' && invalid > 0 && (
              <div className="card p-3 mb-2 border-bad/40 leading-relaxed">
                <div className="font-medium text-bad">{fmtInt(invalid)} record{invalid > 1 ? 's do' : ' does'} not match the target format</div>
                <div className="text-ink-2">Approval and export wait until every record fits. Fix them one at a time or delete them — click a schema finding to find its row.</div>
              </div>
            )}
            {rightTab === 'findings' && (ds.findings.length ? ds.findings.map((f, i) => (f.kind === 'validation' && f.identity
              ? <button key={i} className="block w-full text-left hover:bg-surface-2 rounded" title="Show this row" onClick={() => setQ(f.identity ?? '')}><FindingRow f={{ ...f, message: `${f.identity} · ${f.message}` }} /></button>
              : <FindingRow key={i} f={f} />)) : <div className="text-muted">No findings. Every record passed the schema and quality rules.</div>)}
            {rightTab === 'diff' && <DiffPanel report={report} ack={ack} acking={approve.isPending} onAcknowledge={(comment) => approve.mutate({ acknowledge_anomalies: true, comment })} onTrace={(identity, kind) => setDrawer({ kind: 'lineage', identity, pointer: /price|discount|promo/.test(kind) && pricePointer ? pricePointer.replace('/*/', '/0/') : undefined })} />}
            {rightTab === 'copilot' && <AnswerPanel answer={answer} pending={question.isPending} onTrace={(identity, pointer) => setDrawer({ kind: 'lineage', identity, pointer })} />}
            {rightTab === 'versions' && (
              <ul className="flex flex-col gap-1">
                {ds.history?.slice().reverse().map((v) => (
                  <li key={v.id} className={cn('card p-2.5 flex items-center gap-2', v.id === ds.id && 'border-signal/50')}>
                    <span className="tnum font-medium">v{v.version}</span><StatusBadge status={v.status} /><span className="text-muted tnum">{fmtInt(v.row_count)} rows</span><span className="text-muted ml-auto">{v.note ?? v.created_by} · {timeAgo(v.created_at)}</span>
                    {v.id !== ds.id && <Link className="btn btn-ghost btn-sm" to={`/datasets/${v.id}`}>open</Link>}
                  </li>
                ))}
                {ds.change_sets?.length ? <li className="text-muted mt-2">Change sets on this version: {ds.change_sets.map((c) => `${c.kind} by ${c.author} (${c.impact.cells_changed} cells)`).join('; ')}</li> : null}
              </ul>
            )}
          </div>
        </WorkspacePanel>
      </div>

      <RecordDrawer open={drawer?.kind === 'record'} identity={drawer?.identity ?? ''} rows={rows} identityOf={identityOf} columns={page?.columns ?? []} onClose={() => setDrawer(null)} onLineage={() => setDrawer({ kind: 'lineage', identity: drawer!.identity })} onEdit={(target, value, label) => setPending((p) => [...p, { kind: 'set_item', identity: drawer!.identity, target, value, label }])} />
      <TraceDrawer open={drawer?.kind === 'lineage'} datasetId={datasetId} identity={drawer?.identity ?? ''} pointer={drawer?.pointer} onPointer={(p) => setDrawer((d) => d && { ...d, pointer: p })} onClose={() => setDrawer(null)} />

      <Modal open={saveOpen} onClose={() => setSaveOpen(false)} title="Review changes before saving" width="max-w-3xl"
        footer={<SaveFooter pending={apply.isPending} valid={!!preview.data && preview.data.validation_errors.length === 0} onCancel={() => setSaveOpen(false)} onSave={(kind, note) => apply.mutate({ kind, note })} />}>
        {preview.isPending && <div className="text-muted flex items-center gap-2"><Spinner /> computing impact…</div>}
        {preview.data && <ImpactView impact={preview.data.impact} rowsAfter={preview.data.rows_after} errors={preview.data.validation_errors} invalid={[preview.data.invalid_before, preview.data.invalid_after]} />}
      </Modal>
    </div>
  )
}

function strip(p: Pending): Operation { const { label: _l, ...op } = p; return op }
function setPointerLocal(obj: Record<string, unknown>, pointer: string, value: unknown) {
  const parts = pointer.split('/').filter(Boolean)
  let cur: Record<string, unknown> | unknown[] = obj
  for (const p of parts.slice(0, -1)) {
    if (Array.isArray(cur)) cur = cur[Number(p)] as Record<string, unknown>
    else { if (cur[p] === undefined || cur[p] === null) cur[p] = {}; cur = cur[p] as Record<string, unknown> }
  }
  const last = parts[parts.length - 1]
  if (Array.isArray(cur)) cur[Number(last)] = value
  else cur[last] = value
}

// Field names come from the target schema, so a collection cell summarises each item by its fields'
// meaning (amount, quantity, VAT flag, end date …) instead of by hard-coded names.
const SUMMARY_SEMANTICS = ['price.list_name', 'stock.warehouse', 'promo.type', 'price.amount', 'stock.qty', 'discount.percent', 'discount.amount', 'promo.price', 'price.vat_included', 'promo.end_date']

function CollectionCell({ value, fields }: { value: unknown; fields: FieldSpec[] }) {
  if (!Array.isArray(value) || !value.length) return <span className="text-muted">∅</span>
  const items = value as Record<string, unknown>[]
  const name = (f: FieldSpec) => f.pointer.split('/').pop() ?? ''
  const shown = SUMMARY_SEMANTICS.map((s) => fields.find((f) => f.semantic === s)).filter((f): f is FieldSpec => !!f)
  const pick = shown.length ? shown : fields.slice(0, 3)
  const part = (f: FieldSpec, v: unknown) =>
    v === undefined || v === null || v === '' ? '' : f.semantic === 'price.vat_included' ? (v ? 'inc VAT' : 'ex VAT') : f.semantic === 'discount.percent' ? `${v}%` : f.semantic === 'promo.end_date' ? `→${String(v).slice(5)}` : typeof v === 'number' ? fmtNum(v) : String(v)
  return <span>{items.map((it) => pick.map((f) => part(f, it[name(f)])).filter(Boolean).join(' ')).join(' · ')}</span>
}

function ImpactView({ impact, rowsAfter, errors, invalid }: { impact: Impact; rowsAfter: number; errors: { row: number; identity?: string; pointer: string; message: string }[]; invalid: [number, number] }) {
  const [before, after] = invalid
  return (
    <div className="text-[13px]">
      <div className="grid grid-cols-4 gap-3 mb-4">
        {[['Rows affected', impact.rows_affected], ['Cells changed', impact.cells_changed], ['Rows deleted', impact.rows_deleted], ['Rows after', rowsAfter]].map(([k, v]) => <div key={String(k)} className="card p-3"><div className="text-muted text-[12px]">{k}</div><div className="text-[20px] font-semibold tnum">{fmtInt(v as number)}</div></div>)}
      </div>
      {impact.fields.length > 0 && <div className="text-muted mb-2">Fields: <span className="mono text-ink">{impact.fields.join(', ')}</span></div>}
      {errors.length > 0 && <div className="card p-3 border-bad/40 mb-3"><div className="font-medium text-bad">These changes would break records that fit the target format</div><ul className="mt-1 text-[12.5px]">{errors.slice(0, 8).map((e, i) => <li key={i} className="mono">{e.identity ?? `row ${e.row}`} {e.pointer}: {e.message}</li>)}</ul></div>}
      {(before > 0 || after > 0) && <div className={cn('card p-3 mb-3', after < before ? 'border-ok/40' : 'border-warn/40')}>Records not matching the target format: <span className="tnum font-medium">{fmtInt(before)} → {fmtInt(after)}</span>{after > 0 && <span className="text-muted"> — this version can be saved, but not approved or exported until it reaches 0.</span>}</div>}
      <table className="table text-[12.5px]"><thead><tr><th>Identity</th><th>Field</th><th>Before</th><th>After</th></tr></thead>
        <tbody>{impact.sample.map((s, i) => <tr key={i}><td className="mono">{s.identity}</td><td className="mono">{s.target ?? 'row'}</td><td className="text-muted">{cellText(s.before) || '∅'}</td><td className={s.after === null ? 'text-bad' : 'text-signal'}>{s.after === null && s.target === null ? 'deleted' : cellText(s.after) || '∅'}</td></tr>)}</tbody></table>
      {impact.sample.length < impact.cells_changed + impact.rows_deleted && <div className="text-muted text-[12px] mt-2">Showing the first {impact.sample.length} changes.</div>}
    </div>
  )
}

function SaveFooter({ pending, valid, onCancel, onSave }: { pending: boolean; valid: boolean; onCancel: () => void; onSave: (kind: string, note: string) => void }) {
  const [kind, setKind] = useState('one_time')
  const [note, setNote] = useState('')
  return (
    <div className="flex items-center gap-2 w-full">
      <select className="input w-auto" value={kind} onChange={(e) => setKind(e.target.value)}>
        <option value="one_time">One-time correction (this round only)</option>
        <option value="persistent">Persistent override (re-apply on every future import)</option>
      </select>
      <input className="input flex-1" placeholder="Note for the audit log (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
      <button className="btn" onClick={onCancel}><Localized>Cancel</Localized></button>
      <button className="btn btn-primary" disabled={pending || !valid} onClick={() => onSave(kind, note)}>{pending ? <Spinner className="border-t-white" /> : 'Save as new version'}</button>
    </div>
  )
}

function BulkBar({ count, fields, pricePointer, onSet, onScale, onDelete, onClear }: { count: number; fields: FieldSpec[]; pricePointer?: string; onSet: (f: FieldSpec, text: string) => void; onScale: (pct: number) => void; onDelete: () => void; onClear: () => void }) {
  const [ptr, setPtr] = useState(fields[0]?.pointer ?? '')
  const [val, setVal] = useState('')
  const [pct, setPct] = useState('')
  const fld = fields.find((x) => x.pointer === ptr)
  return (
    <div className="px-3 py-2 border-b border-line bg-signal-soft/50 flex items-center gap-2 text-[12.5px] flex-wrap shrink-0">
      <span className="font-medium tnum">{fmtInt(count)} row{count > 1 ? 's' : ''} selected</span>
      <span className="text-muted ml-2">set</span>
      <select className="input h-7 w-auto py-0" value={ptr} onChange={(e) => { setPtr(e.target.value); setVal('') }}>{fields.map((x) => <option key={x.pointer} value={x.pointer}>{x.pointer.slice(1)}</option>)}</select>
      <span className="text-muted">to</span>
      {fld?.enum ? <select className="input h-7 w-auto py-0" value={val} onChange={(e) => setVal(e.target.value)}><option value="">–</option>{fld.enum.map((v) => <option key={String(v)} value={String(v)}>{String(v)}</option>)}</select> : <input className="input h-7 w-36" value={val} onChange={(e) => setVal(e.target.value)} placeholder="value (empty = clear)" />}
      <button className="btn btn-sm" disabled={!fld} onClick={() => fld && onSet(fld, val)}>Apply</button>
      {pricePointer && <><span className="text-muted ml-3">price</span><input className="input h-7 w-16 tnum" value={pct} onChange={(e) => setPct(e.target.value)} placeholder="-5" /><span className="text-muted">%</span><button className="btn btn-sm" disabled={!pct || Number.isNaN(Number(pct))} onClick={() => onScale(Number(pct))}>Apply</button></>}
      <button className="btn btn-sm btn-ghost text-bad ml-2" onClick={onDelete}><Trash2 size={12} /> Delete rows</button>
      <button className="btn btn-sm btn-ghost ml-auto" onClick={onClear}><X size={12} /> Clear selection</button>
    </div>
  )
}

const SEVERITY_TONE = { high: 'bad', medium: 'warn', low: 'neutral' } as const

function AnomalyView({ an, onTrace }: { an: AnomalyReport; onTrace: (identity: string, kind: string) => void }) {
  return (
    <div className="flex flex-col gap-2.5">
      <div className="grid grid-cols-3 gap-2">
        <div className="card p-2"><div className="text-muted text-[11.5px]">Normal records</div><div className={cn('tnum text-[17px] font-semibold', an.normal_pct >= 95 ? 'text-ok' : 'text-warn')}>{an.normal_pct}%</div></div>
        <div className="card p-2"><div className="text-muted text-[11.5px]">Anomalies</div><div className="tnum text-[17px] font-semibold">{fmtInt(an.items.length + an.dataset.length)}</div></div>
        <div className="card p-2"><div className="text-muted text-[11.5px]">High severity</div><div className={cn('tnum text-[17px] font-semibold', an.high ? 'text-bad' : '')}>{fmtInt(an.high)}</div></div>
      </div>
      <div className={cn('rounded-[var(--radius-md)] px-3 py-2', an.high ? 'bg-bad-soft text-bad' : an.items.length ? 'bg-warn-soft text-warn' : 'bg-ok-soft text-ok')}>{an.headline}</div>
      {an.baseline && <div className="text-muted text-[12px] tnum">usual size: {fmtNum(an.baseline.avg_rows, 0)} records over {an.baseline.rounds} round(s) · now {fmtInt(an.baseline.current_rows)}</div>}
      {an.dataset.map((a, i) => <div key={i} className="flex gap-2"><Badge tone={SEVERITY_TONE[a.severity]}>{a.severity}</Badge><span>{a.message}</span></div>)}
      {Object.keys(an.by_kind).length > 0 && <div className="flex flex-wrap gap-1">{Object.entries(an.by_kind).map(([k, v]) => <Badge key={k} tone="neutral">{k.replace(/_/g, ' ')} · {v}</Badge>)}</div>}
      {an.items.length > 0 && (
        <div><div className="font-medium mb-1">Check first</div>
          <ul className="flex flex-col">{an.items.slice(0, 40).map((a, i) => (
            <li key={i} className="flex items-start gap-2 py-1 border-b border-line/60 cursor-pointer hover:bg-surface-2" onClick={() => a.identity && onTrace(a.identity, a.kind)} title="Trace this record">
              <Badge tone={SEVERITY_TONE[a.severity]} className="min-h-6 px-1.5 text-[12px] shrink-0">{a.severity}</Badge><span className="mono shrink-0">{a.identity}</span><span className="text-ink-2">{a.message}</span>
            </li>))}</ul>
          {an.items.length > 40 && <div className="text-muted text-[12px] mt-1">+{an.items.length - 40} more</div>}
        </div>
      )}
    </div>
  )
}

function DiffPanel({ report, ack, acking, onAcknowledge, onTrace }: { report?: import('../lib/api').ChangeReport; ack: { high: number; headline: string } | null; acking: boolean; onAcknowledge: (comment: string) => void; onTrace: (identity: string, kind: string) => void }) {
  const [comment, setComment] = useState('')
  if (!report) return <div className="text-muted flex items-center gap-2"><Spinner /> comparing with the previous published round…</div>
  return (
    <div className="flex flex-col gap-3">
      {ack && (
        <div className="card p-3 border-bad/40 flex flex-col gap-2">
          <div className="font-medium text-bad flex items-center gap-1.5"><Activity size={14} /> {ack.high} high-severity anomalies hold this approval</div>
          <div className="text-ink-2">Check the list below. Approving anyway is recorded in the audit log with your note.</div>
          <input className="input" placeholder="Why is this data correct? (e.g. price list update confirmed by the buyer)" value={comment} onChange={(e) => setComment(e.target.value)} />
          <button className="btn btn-sm btn-danger self-start" disabled={!comment.trim() || acking} onClick={() => onAcknowledge(comment)}><CircleCheck size={13} /> I reviewed them — approve</button>
        </div>
      )}
      {report.anomalies && <AnomalyView an={report.anomalies} onTrace={onTrace} />}
      {!report.has_previous ? <div className="text-muted"><GitCompareArrows size={14} className="inline mr-1" /> No previous published dataset for this customer yet. From the next round on, this panel also shows SKUs added/removed and every price or promotion change.</div> : (
        <>
          <div className="font-medium mt-1">vs last published round</div>
          <div className="grid grid-cols-3 gap-2">
            {[['Added SKUs', report.added_count], ['Removed SKUs', report.removed_count], ['Changed', report.changed_count], ['Price changes', report.price_change_count], ['Large moves ≥20%', report.large_price_moves?.length], ['Promo changes', report.promotion_changes]].map(([k, v]) => <div key={String(k)} className="card p-2"><div className="text-muted text-[11.5px]">{k}</div><div className="tnum text-[17px] font-semibold">{fmtInt(v as number)}</div></div>)}
          </div>
          <div className="text-muted">{fmtInt(report.previous_rows)} → {fmtInt(report.current_rows)} rows</div>
          {report.large_price_moves?.length ? <div><div className="font-medium mb-1">Large price moves</div><ul className="mono text-[12px]">{report.large_price_moves.slice(0, 15).map((p, i) => <li key={i} className={cn('cursor-pointer hover:underline', p.pct && p.pct > 0 ? 'text-warn' : 'text-ok')} onClick={() => onTrace(p.identity, 'price')}>{p.identity}: {fmtNum(p.before)} → {fmtNum(p.after)} ({p.pct?.toFixed(1)}%)</li>)}</ul></div> : null}
          {report.added?.length ? <div><div className="font-medium mb-1">Added</div><div className="mono text-[12px] text-ink-2 break-words">{report.added.slice(0, 40).join(', ')}{report.added.length > 40 ? ' …' : ''}</div></div> : null}
          {report.removed?.length ? <div><div className="font-medium mb-1">Removed</div><div className="mono text-[12px] text-ink-2 break-words">{report.removed.slice(0, 40).join(', ')}{report.removed.length > 40 ? ' …' : ''}</div></div> : null}
          {report.changed?.length ? <div><div className="font-medium mb-1">Changed records</div><ul className="text-[12px] flex flex-col gap-1">{report.changed.slice(0, 25).map((c) => <li key={c.identity}><span className="mono">{c.identity}</span> <span className="text-muted">{c.changes.map((x) => `${x.pointer}: ${cellText(x.before)} → ${cellText(x.after)}`).join('; ')}</span></li>)}</ul></div> : null}
        </>
      )}
    </div>
  )
}

function AnswerPanel({ answer, pending, onTrace }: { answer: Answer | null; pending: boolean; onTrace: (identity: string, pointer?: string) => void }) {
  if (pending) return <div className="text-muted flex items-center gap-2"><Spinner /> looking at the data…</div>
  if (!answer) return <div className="text-muted leading-relaxed">Ask a question in the copilot bar — "ทำไม SKU บางรายการ join ไม่สำเร็จ?", "ทำไมราคา A001 เป็นแบบนี้?", "มีอะไรผิดปกติที่ควรตรวจก่อน?". Answers are built from the data (findings, table links, lineage, anomalies) and show their evidence.</div>
  return (
    <div className="flex flex-col gap-3">
      <div className="card p-3"><Badge tone="neutral">{answer.intent}</Badge><div className="mt-1.5 leading-relaxed">{answer.answer}</div></div>
      {answer.suggestions.length > 0 && <div><div className="font-medium mb-1">What to do</div><ul className="flex flex-col gap-1">{answer.suggestions.map((x, i) => <li key={i}>• {x}</li>)}</ul></div>}
      {answer.evidence.length > 0 && <div><div className="font-medium mb-1">Evidence</div><ul className="flex flex-col gap-1 mono text-[11.5px] text-ink-2">{answer.evidence.map((e, i) => <li key={i} className="break-words">{typeof e === 'string' ? e : <span className="flex flex-col card p-2">{Object.entries(e as Record<string, unknown>).map(([k, v]) => <span key={k}><span className="text-muted">{k.replace(/_/g, ' ')}:</span> {Array.isArray(v) ? v.map((x) => (typeof x === 'object' ? JSON.stringify(x) : String(x))).join(', ') : typeof v === 'object' && v !== null ? Object.values(v).join(' · ') : String(v)}</span>)}</span>}</li>)}</ul></div>}
      {answer.trace && <button className="btn btn-sm self-start" onClick={() => onTrace(answer.trace!.identity, answer.trace!.pointer ?? undefined)}><Route size={13} /> Open lineage</button>}
    </div>
  )
}

function RecordDrawer({ open, identity, rows, identityOf, columns, onClose, onLineage, onEdit }: { open: boolean; identity: string; rows: Record<string, unknown>[]; identityOf: (r: Record<string, unknown>) => string; columns: FieldSpec[]; onClose: () => void; onLineage: () => void; onEdit: (target: string, value: unknown, label: string) => void }) {
  const rec = rows.find((r) => identityOf(r) === identity)
  const colls = columns.filter((f) => f.is_collection)
  if (!open || !rec) return null
  return (
    <Drawer open={open} onClose={onClose} title={<span className="flex items-center gap-2">Record <span className="mono text-muted">{identity}</span><button className="btn btn-ghost btn-sm ml-2" onClick={onLineage}><Route size={13} /> Lineage</button></span>} width="w-[640px]">
      <div className="flex flex-col gap-4 text-[13px]">
        {colls.map((c) => {
          const items = (getPointer(rec, c.pointer) as Record<string, unknown>[] | undefined) ?? []
          const itemFields = columns.filter((f) => f.in_collection === c.pointer && !f.is_collection)
          return (
            <div key={c.pointer}>
              <div className="font-medium mb-1.5">{c.pointer.slice(1)} <span className="text-muted">({items.length})</span></div>
              {items.length ? (
                <table className="table text-[12.5px]"><thead><tr>{itemFields.map((f) => <th key={f.pointer}>{f.name}</th>)}</tr></thead>
                  <tbody>{items.map((it, i) => <tr key={i}>{itemFields.map((f) => { const key = f.pointer.split('/*/')[1]; const v = it[key]; return <td key={f.pointer}><EditableCell value={v} field={f} onCommit={(nv) => onEdit(`${c.pointer}/${i}/${key}`, nv, `${identity} · ${c.pointer.slice(1)}[${i}].${key}: ${cellText(v) || '∅'} → ${cellText(nv) || '∅'}`)} /></td> })}</tr>)}</tbody></table>
              ) : <div className="text-muted">none</div>}
            </div>
          )
        })}
        <div><div className="font-medium mb-1.5">Full record</div><pre className="mono text-[11.5px] bg-surface-2 rounded-[var(--radius-md)] p-3 whitespace-pre-wrap">{JSON.stringify(rec, null, 2)}</pre></div>
      </div>
    </Drawer>
  )
}

function EditableCell({ value, field, onCommit }: { value: unknown; field: FieldSpec; onCommit: (v: unknown) => void }) {
  const [ed, setEd] = useState<string | null>(null)
  if (ed === null) return <span className="cursor-text hover:bg-signal-soft rounded px-1 -mx-1" onDoubleClick={() => setEd(value === null || value === undefined ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value))}>{cellText(value) || <span className="text-muted">∅</span>}</span>
  const commit = () => {
    let v: unknown = ed.trim()
    if (v === '') v = null
    else if (field.json_type.includes('number') || field.json_type.includes('integer')) v = Number(String(v).replace(/,/g, ''))
    else if (field.json_type.includes('boolean')) v = ['true', '1', 'yes'].includes(String(v).toLowerCase())
    else if (field.json_type.includes('array')) v = String(v).startsWith('[') ? JSON.parse(String(v)) : String(v).split(/[|,]/).map((s) => s.trim()).filter(Boolean)
    if (JSON.stringify(v) !== JSON.stringify(value)) onCommit(v)
    setEd(null)
  }
  return <input autoFocus className="input h-6 text-[12.5px] py-0" value={ed} onChange={(e) => setEd(e.target.value)} onBlur={commit} onKeyDown={(e) => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') setEd(null) }} />
}

const cellRef = (c: { sheet: string | null; source: string; column: string | null; row: number | null }) => `${c.sheet ?? c.source}!${c.column ?? '?'}${c.row ?? ''}`
const CAUSE_TONE: Record<string, string> = { source_changed: 'bg-info-soft text-info', mapping_changed: 'bg-warn-soft text-warn', manual: 'bg-signal-soft text-signal', previous_edit: 'bg-warn-soft text-warn', unchanged: 'bg-ok-soft text-ok', new_record: 'bg-surface-2 text-ink-2', no_baseline: 'bg-surface-2 text-ink-2', unknown: 'bg-surface-2 text-ink-2' }

function Step({ title, children }: { title: string; children: React.ReactNode }) {
  return <div className="flex gap-3 py-1.5 border-b border-line/60 last:border-0"><div className="w-32 shrink-0 text-muted text-[11px] uppercase tracking-wide pt-0.5">{title}</div><div className="flex-1 min-w-0 break-words">{children}</div></div>
}

function TraceDrawer({ open, datasetId, identity, pointer, onPointer, onClose }: { open: boolean; datasetId: string; identity: string; pointer?: string; onPointer: (p: string) => void; onClose: () => void }) {
  const { data, error } = useQuery({ queryKey: ['trace', datasetId, identity, pointer ?? ''], queryFn: () => api.trace(datasetId, identity, pointer), enabled: open && !!identity })
  const focus = data?.focus
  return (
    <Drawer open={open} onClose={onClose} title={<span>Lineage <span className="mono text-muted">{identity}</span></span>} width="w-[720px]">
      {error ? <div className="text-bad">{(error as Error).message}</div> : !data ? <Spinner /> : (
        <div className="flex flex-col gap-4 text-[12.5px]">
          <div className="text-muted">{data.source.file} · sheet {data.source.sheet ?? data.source.source} · row {data.source.row ?? '?'} · mapping v{data.mapping_version} · dataset v{data.version}</div>
          {data.problems.length > 0 && <div className="text-warn">{data.problems.join('; ')}</div>}
          {focus && <FocusView f={focus} mappingVersion={data.mapping_version} />}
          <div>
            <div className="font-medium mb-1">Every value of this record — click one to trace it</div>
            <table className="table text-[12px]"><thead><tr><th>Field</th><th>Value</th><th>From</th></tr></thead>
              <tbody>{data.fields.map((x) => {
                const edited = x.mapped !== null && x.mapped !== undefined && JSON.stringify(x.mapped) !== JSON.stringify(x.value)
                return (
                  <tr key={x.pointer} className={cn('cursor-pointer hover:bg-surface-2', x.pointer === pointer && 'bg-signal-soft/60')} onClick={() => onPointer(x.pointer)}>
                    <td className="mono">{x.pointer}</td>
                    <td>{cellText(x.value) || <span className="text-muted">∅</span>}{edited && <span className="text-signal"> · edited (mapped {cellText(x.mapped) || '∅'})</span>}</td>
                    <td className="mono text-muted">{x.cells.length ? x.cells.map(cellRef).join(', ') : x.expr ? describeExpr(x.expr) : ''}</td>
                  </tr>
                )
              })}</tbody></table>
          </div>
          {data.overrides.length > 0 && <div><div className="font-medium mb-1">Persistent overrides on this record</div><ul className="flex flex-col gap-1">{data.overrides.map((o) => <li key={o.id}><span className="mono">{o.target}</span> = {cellText(o.value)} <span className="text-muted">by {o.author}{o.note ? ` · “${o.note}”` : ''}</span></li>)}</ul></div>}
          <div>
            <div className="font-medium mb-1">History of this record</div>
            <ul className="flex flex-col gap-1.5">{data.history.map((h) => (
              <li key={h.dataset_id} className="card p-2">
                <div className="flex items-center gap-2"><span className="tnum font-medium">v{h.version}</span><Badge tone={h.kind === 'transform' ? 'neutral' : 'signal'}>{h.kind.replace('_', ' ')}</Badge><span className="text-muted">by {h.by} · {timeAgo(h.at)}</span>{h.note && <span className="text-muted">“{h.note}”</span>}</div>
                {h.changes.length > 0 && <ul className="mt-1 mono text-[11.5px]">{h.changes.map((c, i) => <li key={i}>{c.pointer}: {cellText(c.before) || '∅'} → <span className="text-signal">{cellText(c.after) || '∅'}</span></li>)}</ul>}
                {!h.present && <div className="text-bad mt-1">record deleted in this version</div>}
              </li>
            ))}</ul>
          </div>
        </div>
      )}
    </Drawer>
  )
}

function FocusView({ f, mappingVersion }: { f: NonNullable<Trace['focus']>; mappingVersion: number }) {
  const edited = f.mapped !== null && f.mapped !== undefined && JSON.stringify(f.mapped) !== JSON.stringify(f.value)
  return (
    <div className="card p-3">
      <div className="font-medium mono mb-1">{f.pointer}</div>
      <Step title="Source">{f.cells.length ? f.cells.map((c, i) => (
        <div key={i}><span className="mono">{c.file} · {c.sheet ?? c.source} · row {c.row ?? '?'} · column {c.column ?? '?'}</span> <span className="text-muted">({c.header})</span> = <span className="mono">"{cellText(c.raw)}"</span> {c.verified === true && <Badge tone="ok" className="min-h-6 text-[12px]">read back from the file</Badge>}{c.verified === false && <Badge tone="warn" className="min-h-6 text-[12px]">position approximate</Badge>}</div>
      )) : <span className="text-muted">no source cell — a constant or a default</span>}</Step>
      <Step title={`Mapping v${mappingVersion}`}><span className="mono">{f.expr ? describeExpr(f.expr) : '–'}</span> → <b>{cellText(f.mapped) || '∅'}</b></Step>
      {(f.edits.length > 0 || f.overrides.length > 0) && <Step title="Manual changes">
        {f.overrides.map((o) => <div key={o.id}>persistent override → {cellText(o.value)} <span className="text-muted">by {o.author}{o.note ? ` · “${o.note}”` : ''}</span></div>)}
        {f.edits.map((e, i) => <div key={i}>v{e.version} {e.kind.replace('_', ' ')} by {e.by} · {timeAgo(e.at)}: {cellText(e.before) || '∅'} → <span className="text-signal">{cellText(e.after) || '∅'}</span>{e.note ? <span className="text-muted"> “{e.note}”</span> : null}</div>)}
      </Step>}
      <Step title="Final JSON"><span className={cn('mono', edited && 'text-signal')}>"{f.pointer.split('/').pop()}": {JSON.stringify(f.value)}</span></Step>
      <div className={cn('rounded-[var(--radius-md)] px-3 py-2 mt-2', CAUSE_TONE[f.cause.kind] ?? 'bg-surface-2')}>
        <div className="font-medium">Why this value{f.previous ? ` — last round (${f.previous.round.label ?? 'previous'}): ${cellText(f.previous.value) || '∅'}` : ''}</div>
        <div>{f.cause.summary}</div>
        {f.cause.evidence?.map((e, i) => <div key={i} className="mono text-[11.5px] opacity-90">• {e}</div>)}
      </div>
    </div>
  )
}

export type { RowsPage }
