import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useVirtualizer } from '@tanstack/react-virtual'
import { Download, Search, Undo2, Sparkles, History, GitCompare, AlertCircle, CheckCircle2, Route, Trash2 } from 'lucide-react'
import { api, type FieldSpec, type Operation, type Impact, type RowsPage } from '../lib/api'
import { cn, cellText, fmtInt, fmtNum, getPointer, timeAgo, describeExpr } from '../lib/utils'
import { Badge, Drawer, Modal, Spinner, StatusBadge, Tabs, FindingRow, Confidence } from '../components/ui'
import { useToast } from '../lib/toast'

const ROW_H = 32
const PAGE = 500

type Pending = Operation & { label: string }

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
  const [sel, setSel] = useState<{ r: number; c: number } | null>(null)
  const [editing, setEditing] = useState<{ r: number; c: number; value: string } | null>(null)
  const [drawer, setDrawer] = useState<{ kind: 'record' | 'lineage'; identity: string } | null>(null)
  const [saveOpen, setSaveOpen] = useState(false)
  const [rightTab, setRightTab] = useState<'changes' | 'findings' | 'versions' | 'diff'>('changes')
  const [copilot, setCopilot] = useState('')

  const columns: FieldSpec[] = useMemo(() => (page?.columns ?? []).filter((f) => !f.system && !f.in_collection && !f.is_collection && !f.json_type.includes('object')).concat((page?.columns ?? []).filter((f) => f.is_collection)), [page])
  const identityOf = useCallback((row: Record<string, unknown>) => (page?.identity_keys ?? ['/sku']).map((k) => String(getPointer(row, k))).join('|'), [page])

  // overlay pending edits onto rows for display
  const rows = useMemo(() => {
    const base = page?.rows ?? []
    if (!pending.length) return base
    const byId = new Map<string, Record<string, unknown>>()
    const deleted = new Set<string>()
    pending.forEach((p) => { if (p.kind === 'delete_row' && p.identity) deleted.add(p.identity) })
    const out = base.filter((r) => !deleted.has(identityOf(r))).map((r) => structuredClone(r))
    out.forEach((r) => byId.set(identityOf(r), r))
    pending.forEach((p) => { if (p.kind === 'set_cell' && p.identity && p.target) { const r = byId.get(p.identity); if (r) setPointerLocal(r, p.target, p.value) } })
    return out
  }, [page, pending, identityOf])
  const editedCells = useMemo(() => new Set(pending.filter((p) => p.kind === 'set_cell').map((p) => `${p.identity}|${p.target}`)), [pending])

  // virtualization
  const scrollRef = useRef<HTMLDivElement>(null)
  const rowVirt = useVirtualizer({ count: rows.length, getScrollElement: () => scrollRef.current, estimateSize: () => ROW_H, overscan: 12 })

  // keyboard navigation
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (editing || drawer || saveOpen) return
      if ((e.target as HTMLElement)?.tagName === 'INPUT' || (e.target as HTMLElement)?.tagName === 'TEXTAREA') return
      if (!sel) return
      if (e.key === 'ArrowDown') { e.preventDefault(); setSel((s) => s && { ...s, r: Math.min(rows.length - 1, s.r + 1) }) }
      if (e.key === 'ArrowUp') { e.preventDefault(); setSel((s) => s && { ...s, r: Math.max(0, s.r - 1) }) }
      if (e.key === 'ArrowRight') { e.preventDefault(); setSel((s) => s && { ...s, c: Math.min(columns.length - 1, s.c + 1) }) }
      if (e.key === 'ArrowLeft') { e.preventDefault(); setSel((s) => s && { ...s, c: Math.max(0, s.c - 1) }) }
      if (e.key === 'Enter') { e.preventDefault(); startEdit(sel.r, sel.c) }
      if ((e.metaKey || e.ctrlKey) && e.key === 'z') { e.preventDefault(); setPending((p) => p.slice(0, -1)) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel, editing, drawer, saveOpen, rows.length, columns.length])
  useEffect(() => { if (sel) rowVirt.scrollToIndex(sel.r) }, [sel, rowVirt])

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
    const raw = editing.value.trim()
    let value: unknown = raw
    if (raw === '') value = null
    else if (f.json_type.includes('number') || f.json_type.includes('integer')) { const n = Number(raw.replace(/,/g, '')); if (Number.isNaN(n)) { toast.push({ kind: 'bad', title: 'Not a number' }); return } value = f.json_type.includes('integer') ? Math.round(n) : n }
    else if (f.json_type.includes('boolean')) value = ['true', '1', 'yes', 'y'].includes(raw.toLowerCase())
    else if (f.json_type.includes('array')) value = raw.startsWith('[') ? JSON.parse(raw) : raw.split(/[|,]/).map((s) => s.trim()).filter(Boolean)
    if (f.enum && !f.enum.includes(value)) { toast.push({ kind: 'bad', title: `Must be one of ${f.enum.join(', ')}` }); return }
    const before = getPointer(page?.rows.find((r) => identityOf(r) === identityOf(row)) ?? row, f.pointer)
    if (JSON.stringify(before) !== JSON.stringify(value)) setPending((p) => [...p.filter((x) => !(x.kind === 'set_cell' && x.identity === identityOf(row) && x.target === f.pointer)), { kind: 'set_cell', identity: identityOf(row), target: f.pointer, value, label: `${identityOf(row)} · ${f.name}: ${cellText(before) || '∅'} → ${cellText(value) || '∅'}` }])
    setEditing(null)
  }

  const preview = useMutation({ mutationFn: () => api.previewChanges(datasetId, pending.map(strip)) })
  const apply = useMutation({
    mutationFn: (b: { kind: string; note: string }) => api.applyChanges(datasetId, { operations: pending.map(strip), kind: b.kind, actor: 'operator', note: b.note || undefined }),
    onSuccess: ({ dataset }) => { qc.invalidateQueries(); setPending([]); setSaveOpen(false); toast.push({ kind: 'ok', title: `Saved as dataset v${dataset.version}` }); nav(`/datasets/${dataset.id}`) },
    onError: (e: Error & { details?: { errors?: { pointer: string; message: string }[] } }) => toast.push({ kind: 'bad', title: 'Changes rejected by the schema', detail: e.details?.errors?.slice(0, 3).map((x) => `${x.pointer}: ${x.message}`).join('; ') ?? e.message }),
  })
  const rollback = useMutation({ mutationFn: () => api.rollback(datasetId), onSuccess: (d) => { qc.invalidateQueries(); toast.push({ kind: 'ok', title: `Rolled back → v${d.version}` }); nav(`/datasets/${d.id}`) } })
  const approve = useMutation({ mutationFn: () => api.approveDataset(datasetId), onSuccess: () => { qc.invalidateQueries(); toast.push({ kind: 'ok', title: 'Dataset approved' }) } })
  const doExport = useMutation({ mutationFn: (fmt: string) => api.exportDataset(datasetId, fmt), onSuccess: (a) => { qc.invalidateQueries(); window.open(api.downloadUrl(a.id), '_blank'); toast.push({ kind: 'ok', title: `Exported ${a.row_count} rows as ${a.fmt.toUpperCase()}` }) } })
  const ask = useMutation({
    mutationFn: () => api.copilot(datasetId, copilot),
    onSuccess: (r) => {
      if (r.needs_clarification) { toast.push({ kind: 'info', title: 'Copilot needs more detail', detail: r.needs_clarification }); return }
      if (!r.operations.length) { toast.push({ kind: 'info', title: 'No operations planned' }); return }
      setPending((p) => [...p, ...r.operations.map((o) => ({ ...o, label: `${o.explanation ?? o.kind} · ${r.impact ? `${r.impact.rows_affected} rows` : ''}` }))])
      setCopilot('')
      toast.push({ kind: 'ok', title: 'Plan added to pending changes', detail: `${r.impact?.rows_affected ?? '?'} rows affected — review and save when ready.` })
    },
  })
  const { data: report } = useQuery({ queryKey: ['change-report', datasetId], queryFn: () => api.changeReport(datasetId), enabled: rightTab === 'diff' })

  if (!ds) return <div className="p-10 text-center"><Spinner /></div>
  const errors = ds.findings.filter((f) => f.severity === 'error').length
  const widths = (f: FieldSpec) => (f.is_collection ? 220 : f.semantic === 'product_name' || f.semantic === 'description' ? 240 : f.json_type.includes('number') ? 110 : 140)

  return (
    <div className="h-screen flex flex-col">
      <div className="h-14 px-5 border-b border-line bg-surface flex items-center gap-3 shrink-0">
        <Link to={`/rounds/${ds.round_id}`} className="text-muted hover:text-ink text-[12.5px]">← Round</Link>
        <span className="font-semibold">Data Editor</span>
        <span className="tnum text-muted">v{ds.version}</span>
        <StatusBadge status={ds.status} />
        <span className="tnum text-muted text-[12.5px]">{fmtInt(ds.row_count)} rows</span>
        {errors ? <Badge tone="bad"><AlertCircle size={12} /> {errors} errors</Badge> : <Badge tone="ok"><CheckCircle2 size={12} /> schema valid</Badge>}
        <div className="relative ml-4"><Search size={13} className="absolute left-2.5 top-2.5 text-muted" /><input className="input pl-7 w-72" placeholder="Search any field…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <div className="ml-auto flex items-center gap-2">
          {pending.length > 0 && <><button className="btn btn-sm" onClick={() => setPending([])}><Undo2 size={13} /> Discard</button><button className="btn btn-sm btn-primary" onClick={() => { preview.mutate(); setSaveOpen(true) }}>Review {pending.length} change{pending.length > 1 ? 's' : ''}</button></>}
          {!pending.length && ds.status === 'draft' && <button className="btn btn-sm" onClick={() => approve.mutate()}><CheckCircle2 size={13} /> Approve data</button>}
          {!pending.length && ds.parent_id && <button className="btn btn-sm" onClick={() => rollback.mutate()} title="New version with the parent's content"><History size={13} /> Roll back</button>}
          {!pending.length && <div className="flex items-center rounded-[var(--radius-md)] border border-line overflow-hidden">{['json', 'jsonl', 'csv'].map((f) => <button key={f} className="h-8 px-2.5 text-[12.5px] font-medium hover:bg-surface-2 border-r border-line last:border-0 flex items-center gap-1" onClick={() => doExport.mutate(f)}><Download size={12} /> {f.toUpperCase()}</button>)}</div>}
        </div>
      </div>

      {/* copilot bar */}
      <div className="px-5 py-2 border-b border-line bg-surface-2/60 flex items-center gap-2 shrink-0">
        <Sparkles size={14} className="text-signal" />
        <input className="input flex-1 bg-surface" placeholder='Tell the copilot what to change — e.g. "ลดราคาหมวด Electronics 5%" or "ตั้งสถานะ inactive ให้หมวด Snacks". Nothing is applied until you review and save.' value={copilot} onChange={(e) => setCopilot(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && copilot && ask.mutate()} />
        <button className="btn btn-sm btn-primary" disabled={!copilot || ask.isPending} onClick={() => ask.mutate()}>{ask.isPending ? <Spinner className="border-t-white" /> : 'Plan'}</button>
      </div>

      <div className="flex-1 min-h-0 grid grid-cols-[1fr_360px]">
        {/* grid */}
        <div className="min-w-0 flex flex-col">
          <div ref={scrollRef} className="flex-1 overflow-auto relative select-none" onMouseDown={() => editing && commitEdit()}>
            <div className="sticky top-0 z-10 flex bg-surface border-b border-line" style={{ width: 56 + 36 + columns.reduce((a, f) => a + widths(f), 0) }}>
              <div className="w-14 shrink-0 px-2 text-[11px] text-muted leading-8 tnum border-r border-line">#</div>
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
                    <div className="w-14 shrink-0 px-2 text-[11px] text-muted leading-8 tnum border-r border-line/70 cursor-pointer hover:text-signal" onClick={() => setDrawer({ kind: 'record', identity: id })} title="Open record">{offset + vi.index + 1}</div>
                    {columns.map((f, c) => {
                      const v = getPointer(row, f.pointer)
                      const isSel = sel?.r === vi.index && sel?.c === c
                      const isEd = editing?.r === vi.index && editing?.c === c
                      const edited = editedCells.has(`${id}|${f.pointer}`)
                      return (
                        <div key={f.pointer} className={cn('shrink-0 h-8 px-2.5 text-[12.5px] leading-8 border-r border-line/70 truncate relative', f.json_type.includes('number') && 'text-right tnum', isSel && 'outline outline-2 outline-signal -outline-offset-2 z-[1]', edited && 'bg-signal-soft text-signal')} style={{ width: widths(f) }}
                          onMouseDown={(e) => { e.stopPropagation(); if (editing && !isEd) commitEdit(); setSel({ r: vi.index, c }) }} onDoubleClick={() => (f.is_collection ? setDrawer({ kind: 'record', identity: id }) : startEdit(vi.index, c))}>
                          {isEd ? (
                            <input autoFocus className="absolute inset-0 w-full h-full px-2.5 bg-surface text-ink outline-none border-2 border-signal" value={editing.value} onChange={(e) => setEditing({ ...editing, value: e.target.value })} onKeyDown={(e) => { if (e.key === 'Enter') commitEdit(); if (e.key === 'Escape') setEditing(null) }} onMouseDown={(e) => e.stopPropagation()} />
                          ) : f.is_collection ? <CollectionCell value={v} pointer={f.pointer} /> : (v === null || v === undefined ? <span className="text-muted/60">∅</span> : cellText(v))}
                        </div>
                      )
                    })}
                    <button className="w-9 shrink-0 grid place-items-center text-muted hover:text-bad" title="Delete row" onClick={() => setPending((p) => [...p, { kind: 'delete_row', identity: id, label: `delete ${id}` }])}><Trash2 size={12} /></button>
                  </div>
                )
              })}
            </div>
          </div>
          <div className="h-9 border-t border-line bg-surface flex items-center gap-3 px-4 text-[12px] text-muted shrink-0">
            <span className="tnum">{page ? `${fmtInt(offset + 1)}–${fmtInt(Math.min(offset + PAGE, page.total))} of ${fmtInt(page.total)}` : ''}</span>
            <button className="btn btn-ghost btn-sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>‹ prev</button>
            <button className="btn btn-ghost btn-sm" disabled={!page || offset + PAGE >= page.total} onClick={() => setOffset(offset + PAGE)}>next ›</button>
            <span className="ml-auto flex items-center gap-3"><span><span className="kbd">↵</span> edit</span><span><span className="kbd">esc</span> cancel</span><span><span className="kbd">⌘Z</span> undo</span><span>double-click a nested cell to edit items</span></span>
          </div>
        </div>

        {/* right rail */}
        <aside className="border-l border-line bg-surface flex flex-col min-h-0">
          <Tabs value={rightTab} onChange={setRightTab} items={[{ id: 'changes', label: 'Pending', count: pending.length }, { id: 'findings', label: 'Findings', count: ds.findings.length }, { id: 'diff', label: 'vs last round' }, { id: 'versions', label: 'Versions', count: ds.history?.length }]} />
          <div className="flex-1 overflow-auto p-3 text-[12.5px]">
            {rightTab === 'changes' && (pending.length ? (
              <ul className="flex flex-col gap-1">{pending.map((p, i) => <li key={i} className="flex items-start gap-2 py-1 border-b border-line/70"><Badge tone={p.kind.startsWith('delete') ? 'bad' : 'signal'} className="h-5 px-1.5 text-[11px] shrink-0">{p.kind.replace('_', ' ')}</Badge><span className="flex-1 break-words">{p.label}</span><button className="text-muted hover:text-bad" onClick={() => setPending((xs) => xs.filter((_, j) => j !== i))}>×</button></li>)}</ul>
            ) : <div className="text-muted leading-relaxed">Edit cells directly (double-click or <span className="kbd">↵</span>), delete rows, or ask the copilot. Changes stay here until you review their impact and save them as a new version — nothing is overwritten.</div>)}
            {rightTab === 'findings' && (ds.findings.length ? ds.findings.map((f, i) => <FindingRow key={i} f={f} />) : <div className="text-muted">No findings. Every record passed the schema and quality rules.</div>)}
            {rightTab === 'diff' && <DiffPanel report={report} />}
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
        </aside>
      </div>

      <RecordDrawer open={drawer?.kind === 'record'} identity={drawer?.identity ?? ''} rows={rows} identityOf={identityOf} columns={page?.columns ?? []} onClose={() => setDrawer(null)} onLineage={() => setDrawer({ kind: 'lineage', identity: drawer!.identity })} onEdit={(target, value, label) => setPending((p) => [...p, { kind: 'set_item', identity: drawer!.identity, target, value, label }])} />
      <LineageDrawer open={drawer?.kind === 'lineage'} datasetId={datasetId} identity={drawer?.identity ?? ''} onClose={() => setDrawer(null)} />

      <Modal open={saveOpen} onClose={() => setSaveOpen(false)} title="Review changes before saving" width="max-w-3xl"
        footer={<SaveFooter pending={apply.isPending} valid={!!preview.data && preview.data.validation_errors.length === 0} onCancel={() => setSaveOpen(false)} onSave={(kind, note) => apply.mutate({ kind, note })} />}>
        {preview.isPending && <div className="text-muted flex items-center gap-2"><Spinner /> computing impact…</div>}
        {preview.data && <ImpactView impact={preview.data.impact} rowsAfter={preview.data.rows_after} errors={preview.data.validation_errors} />}
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

function CollectionCell({ value, pointer }: { value: unknown; pointer: string }) {
  if (!Array.isArray(value) || !value.length) return <span className="text-muted/60">∅</span>
  const items = value as Record<string, unknown>[]
  if (pointer === '/prices') return <span>{items.map((p) => `${fmtNum(p.amount as number)} ${p.vat_included ? 'inc' : 'ex'}${p.price_list && p.price_list !== 'standard' ? ` (${p.price_list})` : ''}`).join(' · ')}</span>
  if (pointer === '/inventory') return <span>{items.map((p) => `${p.warehouse_code}: ${fmtNum(p.qty as number, 0)}`).join(' · ')}</span>
  if (pointer === '/promotions') return <span>{items.map((p) => `${p.type}${p.discount_percent ? ` ${p.discount_percent}%` : ''}${p.end_date ? ` →${String(p.end_date).slice(5)}` : ''}`).join(' · ')}</span>
  return <span className="text-muted">{items.length} items</span>
}

function ImpactView({ impact, rowsAfter, errors }: { impact: Impact; rowsAfter: number; errors: { row: number; pointer: string; message: string }[] }) {
  return (
    <div className="text-[13px]">
      <div className="grid grid-cols-4 gap-3 mb-4">
        {[['Rows affected', impact.rows_affected], ['Cells changed', impact.cells_changed], ['Rows deleted', impact.rows_deleted], ['Rows after', rowsAfter]].map(([k, v]) => <div key={String(k)} className="card p-3"><div className="text-muted text-[12px]">{k}</div><div className="text-[20px] font-semibold tnum">{fmtInt(v as number)}</div></div>)}
      </div>
      {impact.fields.length > 0 && <div className="text-muted mb-2">Fields: <span className="mono text-ink">{impact.fields.join(', ')}</span></div>}
      {errors.length > 0 && <div className="card p-3 border-bad/40 mb-3"><div className="font-medium text-bad">These changes would violate the target schema</div><ul className="mt-1 text-[12.5px]">{errors.slice(0, 8).map((e, i) => <li key={i} className="mono">row {e.row} {e.pointer}: {e.message}</li>)}</ul></div>}
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
      <button className="btn" onClick={onCancel}>Cancel</button>
      <button className="btn btn-primary" disabled={pending || !valid} onClick={() => onSave(kind, note)}>{pending ? <Spinner className="border-t-white" /> : 'Save as new version'}</button>
    </div>
  )
}

function DiffPanel({ report }: { report?: import('../lib/api').ChangeReport }) {
  if (!report) return <div className="text-muted flex items-center gap-2"><Spinner /> comparing with the previous published round…</div>
  if (!report.has_previous) return <div className="text-muted"><GitCompare size={14} className="inline mr-1" /> No previous published dataset for this customer yet. From the next round on, this panel shows SKUs added/removed and every price or promotion change.</div>
  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-3 gap-2">
        {[['Added SKUs', report.added_count, 'ok'], ['Removed SKUs', report.removed_count, 'bad'], ['Changed', report.changed_count, 'info'], ['Price changes', report.price_change_count, 'warn'], ['Large moves ≥20%', report.large_price_moves?.length, 'bad'], ['Promo changes', report.promotion_changes, 'info']].map(([k, v]) => <div key={String(k)} className="card p-2"><div className="text-muted text-[11.5px]">{k}</div><div className="tnum text-[17px] font-semibold">{fmtInt(v as number)}</div></div>)}
      </div>
      <div className="text-muted">{fmtInt(report.previous_rows)} → {fmtInt(report.current_rows)} rows</div>
      {report.large_price_moves?.length ? <div><div className="font-medium mb-1">Large price moves</div><ul className="mono text-[12px]">{report.large_price_moves.slice(0, 15).map((p, i) => <li key={i} className={cn(p.pct && p.pct > 0 ? 'text-warn' : 'text-ok')}>{p.identity}: {fmtNum(p.before)} → {fmtNum(p.after)} ({p.pct?.toFixed(1)}%)</li>)}</ul></div> : null}
      {report.added?.length ? <div><div className="font-medium mb-1">Added</div><div className="mono text-[12px] text-ink-2 break-words">{report.added.slice(0, 40).join(', ')}{report.added.length > 40 ? ' …' : ''}</div></div> : null}
      {report.removed?.length ? <div><div className="font-medium mb-1">Removed</div><div className="mono text-[12px] text-ink-2 break-words">{report.removed.slice(0, 40).join(', ')}{report.removed.length > 40 ? ' …' : ''}</div></div> : null}
      {report.changed?.length ? <div><div className="font-medium mb-1">Changed records</div><ul className="text-[12px] flex flex-col gap-1">{report.changed.slice(0, 25).map((c) => <li key={c.identity}><span className="mono">{c.identity}</span> <span className="text-muted">{c.changes.map((x) => `${x.pointer}: ${cellText(x.before)} → ${cellText(x.after)}`).join('; ')}</span></li>)}</ul></div> : null}
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
  if (ed === null) return <span className="cursor-text hover:bg-signal-soft rounded px-1 -mx-1" onDoubleClick={() => setEd(value === null || value === undefined ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value))}>{cellText(value) || <span className="text-muted/60">∅</span>}</span>
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

function LineageDrawer({ open, datasetId, identity, onClose }: { open: boolean; datasetId: string; identity: string; onClose: () => void }) {
  const { data } = useQuery({ queryKey: ['lineage', datasetId, identity], queryFn: () => api.lineage(datasetId, identity), enabled: open && !!identity })
  return (
    <Drawer open={open} onClose={onClose} title={<span>Lineage <span className="mono text-muted">{identity}</span></span>} width="w-[640px]">
      {!data ? <Spinner /> : (
        <div className="flex flex-col gap-4 text-[12.5px]">
          <div className="card p-3"><div className="text-muted">Source</div><div className="mono">{String((data.source_meta as { file?: string })?.file ?? '')} · root row {String((data.source_meta as { lineage?: { root_row?: number } })?.lineage?.root_row ?? '')}</div><div className="text-muted mt-1">mapping version <span className="mono text-ink">{data.mapping_version}</span></div></div>
          <div><div className="font-medium mb-1">How each field was derived</div>
            <table className="table text-[12px]"><thead><tr><th>Target</th><th>Expression</th><th>Confidence</th></tr></thead>
              <tbody>{data.fields.map((f) => <tr key={f.target}><td className="mono">{f.target}</td><td className="mono">{describeExpr(f.expr)}</td><td><Confidence value={f.confidence} /></td></tr>)}
                {data.collections.flatMap((c) => c.fields.map((f) => <tr key={f.target}><td className="mono">{f.target}</td><td className="mono">{describeExpr(f.expr)} <span className="text-muted">from {c.source}</span></td><td><Confidence value={f.confidence} /></td></tr>))}</tbody></table></div>
          <div><div className="font-medium mb-1">Manual edits</div>{data.edits.length ? <ul className="flex flex-col gap-1">{data.edits.map((e) => <li key={e.change_set}><span className="text-muted">{e.kind} by {e.author} · {timeAgo(e.at)}</span> {e.changes.map((c) => `${c.target}: ${cellText(c.before)} → ${cellText(c.after)}`).join('; ')}</li>)}</ul> : <div className="text-muted">None — this record is exactly what the mapping produced.</div>}</div>
        </div>
      )}
    </Drawer>
  )
}

export type { RowsPage }
