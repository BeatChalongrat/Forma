import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Check, X, Save, Stethoscope, ChevronDown, ChevronRight, Braces, Plus, Trash2, Wand2, ShieldAlert, Network } from 'lucide-react'
import { api, type SandboxItem, type SandboxReport, type Expr, type FieldChange, type FieldMapping, type FieldSpec, type MappingDocument, type TableProfile } from '../lib/api'
import { cn, describeExpr, fmtInt, pyRegex } from '../lib/utils'
import { Badge, Confidence, HelpStrip, RiskDot, RiskLevel, Spinner, StatusBadge, Tabs, FindingRow, Modal } from '../components/ui'
import { useToast } from '../lib/toast'
import { RelationshipMap } from './RelationshipMap'

type Group = { title: string; pointer: string | null; source: string; fields: FieldSpec[] }
const DRAG_TYPE = 'application/x-forma-column'

const OP_PRESETS: { op: string; label: string; params?: Record<string, unknown> }[] = [
  { op: 'col', label: 'as is (column)' },
  { op: 'trim', label: 'trim' },
  { op: 'normalize_whitespace', label: 'normalize whitespace' },
  { op: 'upper', label: 'UPPER' },
  { op: 'parse_number_th', label: 'number (Thai-aware)' },
  { op: 'parse_date_th', label: 'date (BE/CE, Thai months)' },
  { op: 'parse_bool', label: 'boolean' },
  { op: 'unit_normalize', label: 'unit → canonical' },
  { op: 'pack_size', label: 'pack size from text' },
  { op: 'vat_adjust', label: 'VAT add 7%', params: { mode: 'add', rate: 0.07 } },
  { op: 'vat_adjust_remove', label: 'VAT remove 7%', params: { mode: 'remove', rate: 0.07 } },
  { op: 'strip_leading_zeros', label: 'strip leading zeros' },
  { op: 'to_list', label: 'wrap in list' },
  { op: 'split', label: 'split by separator', params: { separator: ',' } },
  { op: 'map_values', label: 'map values', params: { mapping: { '': '' }, default: null, fuzzy: true } },
  { op: 'regex_extract', label: 'regex extract', params: { pattern: '(\\d+)', group: 1 } },
  { op: 'const', label: 'constant' },
]

function exprLeaf(e: Expr | undefined): { col?: string; source?: string | null; op: string; params: Record<string, unknown>; constValue?: unknown } {
  if (!e) return { op: 'col', params: {} }
  if (e.op === 'col') return { col: e.name, source: e.source, op: 'col', params: {} }
  if (e.op === 'const') return { op: 'const', params: {}, constValue: e.value }
  const inner = e.args?.[0]
  if (inner?.op === 'col') return { col: inner.name, source: inner.source, op: e.op, params: e.params ?? {} }
  return { op: e.op, params: e.params ?? {} }
}
function buildExpr(leaf: { col?: string; source?: string | null; op: string; params: Record<string, unknown>; constValue?: unknown }): Expr {
  if (leaf.op === 'const') return { op: 'const', value: leaf.constValue ?? null }
  const col: Expr = { op: 'col', name: leaf.col ?? '', source: leaf.source ?? null }
  if (leaf.op === 'col') return col
  const op = leaf.op === 'vat_adjust_remove' ? 'vat_adjust' : leaf.op
  return { op, args: [col], params: leaf.params }
}

const STUDIO_TABS = ['json', 'source', 'findings', 'diagnosis', 'compare', 'changes'] as const
type StudioTab = (typeof STUDIO_TABS)[number]

export function MappingStudio() {
  const { roundId = '', mappingId = '' } = useParams()
  const qc = useQueryClient()
  const toast = useToast()
  const nav = useNavigate()
  const { data: mv } = useQuery({ queryKey: ['mapping', mappingId], queryFn: () => api.mapping(mappingId) })
  const { data: round } = useQuery({ queryKey: ['round', roundId], queryFn: () => api.round(roundId) })
  const { data: schema } = useQuery({ queryKey: ['schema', mv?.target_schema], queryFn: () => api.schema(mv!.target_schema), enabled: !!mv })
  const [doc, setDoc] = useState<MappingDocument | null>(null)
  const [dirty, setDirty] = useState(false)
  const [selected, setSelected] = useState<string | null>(null) // target pointer being edited
  const [selectedSource, setSelectedSource] = useState<string | null>(null)
  const [params] = useSearchParams() // ?tab= and ?view= open a tool directly (links from the round page, the guide, Ctrl+K)
  const [rightTab, setRightTab] = useState<StudioTab>(() => (STUDIO_TABS as readonly string[]).includes(params.get('tab') ?? '') ? (params.get('tab') as StudioTab) : 'json')
  const [showRaw, setShowRaw] = useState(false)
  const [rawText, setRawText] = useState('')
  const [comment, setComment] = useState('')
  const [reject, setReject] = useState(false)
  const [overrideOpen, setOverrideOpen] = useState(false)
  const [view, setView] = useState<'fields' | 'relationships'>(() => (params.get('view') === 'relationships' ? 'relationships' : 'fields'))
  const [dropOn, setDropOn] = useState<string | null>(null) // target field a column is dragged over

  useEffect(() => {
    if (mv?.document && !doc) {
      setDoc(mv.document)
      setSelectedSource(mv.document.record.root_source)
      if (mv.policy.field_review?.needs_decision?.length && !params.get('tab')) setRightTab('changes') // a drifted format: show what needs a decision first
    }
  }, [mv, doc, params])

  // ---- profiles of this round (columns per source) --------------------------------------------
  const tables: TableProfile[] = useMemo(() => {
    const out: TableProfile[] = []
    round?.files?.forEach((f) => f.profile?.tables.forEach((t) => out.push({ ...t, file_id: f.id, file_name: f.name })))
    return out
  }, [round])
  const fileProfiles = useQuery({
    queryKey: ['round-files-full', roundId, round?.files?.map((f) => f.id).join(',')],
    queryFn: async () => Promise.all((round?.files ?? []).map((f) => api.file(roundId, f.id))),
    enabled: !!round?.files?.length,
  })
  const allTables: TableProfile[] = useMemo(() => {
    const out: TableProfile[] = []
    fileProfiles.data?.forEach((f) => f.profile?.tables.forEach((t) => out.push({ ...t, file_id: f.id, file_name: f.name })))
    return out.length ? out : tables
  }, [fileProfiles.data, tables])

  const columnsFor = useCallback((sourceId: string): string[] => {
    const s = doc?.sources.find((x) => x.id === sourceId)
    if (!s) return []
    const sheetName = s.sheet?.name
    const t = allTables.find((t) => (sheetName ? t.sheet === sheetName : true) && (s.file.name_pattern ? (pyRegex(s.file.name_pattern)?.test(t.file_name ?? '') ?? true) : true)) ?? allTables.find((t) => (sheetName ? t.sheet === sheetName : true))
    const names = t?.columns.map((c) => c.name) ?? []
    return Object.keys(s.rename).length ? names.map((n) => s.rename[n] ?? n) : names
  }, [doc, allTables])

  // ---- live preview --------------------------------------------------------------------------
  const preview = useMutation({ mutationFn: (d: MappingDocument) => api.mappingPreview(roundId, d, 40) })
  const timer = useRef<number | null>(null)
  useEffect(() => {
    if (!doc) return
    if (timer.current) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => preview.mutate(doc), 500)
    return () => { if (timer.current) window.clearTimeout(timer.current) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc])
  const srcPreview = useQuery({
    queryKey: ['source-preview', roundId, selectedSource, JSON.stringify(doc?.sources.find((s) => s.id === selectedSource) ?? null)],
    queryFn: () => api.sourcePreview(roundId, doc!.sources.find((s) => s.id === selectedSource)!, 40),
    enabled: !!doc && !!selectedSource && rightTab === 'source',
  })
  const diagnose = useMutation({ mutationFn: () => api.diagnose(mappingId) })

  // ---- actions ---------------------------------------------------------------------------------
  const approve = useMutation({ mutationFn: (overrideBlock: boolean) => api.approveMapping(mappingId, { actor: 'operator', comment, override_block: overrideBlock }), onSuccess: () => { qc.invalidateQueries(); setOverrideOpen(false); toast.push({ kind: 'ok', title: 'Mapping approved', detail: 'Transform the round to produce the dataset.' }) }, onError: (e: Error) => toast.push({ kind: 'bad', title: 'Approve failed', detail: e.message }) })
  const rejectM = useMutation({ mutationFn: () => api.rejectMapping(mappingId, { actor: 'operator', comment }), onSuccess: () => { qc.invalidateQueries(); setReject(false); toast.push({ kind: 'ok', title: 'Mapping rejected' }) } })
  const save = useMutation({
    mutationFn: () => api.editMapping(mappingId, doc!, 'operator'),
    onSuccess: (m) => { qc.invalidateQueries(); toast.push({ kind: 'ok', title: `Saved as v${m.version}`, detail: 'New proposed version created; the previous one is kept in history.' }); nav(`/rounds/${roundId}/mapping/${m.id}`); setDoc(null); setDirty(false) },
    onError: (e: Error & { details?: { issues?: { message: string }[] } }) => toast.push({ kind: 'bad', title: 'Mapping is not valid', detail: e.details?.issues?.map((i) => i.message).join('; ') ?? e.message }),
  })

  const update = (fn: (d: MappingDocument) => MappingDocument) => { setDoc((d) => (d ? fn(structuredClone(d)) : d)); setDirty(true) }

  // ---- groups ----------------------------------------------------------------------------------
  const groups: Group[] = useMemo(() => {
    if (!schema?.fields || !doc) return []
    const fields = schema.fields.filter((f) => !f.system && !f.is_collection && !f.json_type.includes('object'))
    const root: Group = { title: 'Record', pointer: null, source: doc.record.root_source, fields: fields.filter((f) => !f.in_collection) }
    const colls = schema.fields.filter((f) => f.is_collection).map((c) => ({ title: c.pointer.slice(1), pointer: c.pointer, source: doc.collections.find((x) => x.target === c.pointer)?.source ?? '', fields: fields.filter((f) => f.in_collection === c.pointer) }))
    return [root, ...colls]
  }, [schema, doc])

  const getMapping = (ptr: string): { fm: FieldMapping | undefined; coll: string | null } => {
    if (!doc) return { fm: undefined, coll: null }
    const root = doc.fields.find((f) => f.target === ptr)
    if (root) return { fm: root, coll: null }
    for (const c of doc.collections) { const f = c.fields.find((x) => x.target === ptr); if (f) return { fm: f, coll: c.target } }
    return { fm: undefined, coll: null }
  }
  const setFieldExpr = (ptr: string, coll: string | null, expr: Expr) => update((d) => {
    if (coll) {
      const c = d.collections.find((x) => x.target === coll)
      if (c) { const f = c.fields.find((x) => x.target === ptr); if (f) { f.expr = expr; f.confidence = 1; f.evidence = ['edited by operator'] } else c.fields.push({ target: ptr, expr, confidence: 1, evidence: ['added by operator'] }) }
    } else {
      const f = d.fields.find((x) => x.target === ptr)
      if (f) { f.expr = expr; f.confidence = 1; f.evidence = ['edited by operator'] } else d.fields.push({ target: ptr, expr, confidence: 1, evidence: ['added by operator'] })
    }
    return d
  })
  const removeField = (ptr: string, coll: string | null) => update((d) => {
    if (coll) { const c = d.collections.find((x) => x.target === coll); if (c) c.fields = c.fields.filter((f) => f.target !== ptr) } else d.fields = d.fields.filter((f) => f.target !== ptr)
    return d
  })
  // point a target field at a source column — shared by drag-and-drop and the "use" button; a field of a
  // collection goes into that collection even before it is mapped (the schema says which one)
  const pointAt = (ptr: string, sourceId: string, column: string) => {
    if (!doc || !schema) return
    const { fm, coll: mapped } = getMapping(ptr)
    const coll = mapped ?? schema.fields?.find((f) => f.pointer === ptr)?.in_collection ?? null
    const base = coll ? doc.collections.find((x) => x.target === coll)?.source : doc.record.root_source
    if (coll && !base) { toast.push({ kind: 'info', title: `Link a table to ${coll} first`, detail: 'Use Relationships or “add collection”.' }); return }
    const leaf = exprLeaf(fm?.expr)
    setFieldExpr(ptr, coll, buildExpr({ ...leaf, op: leaf.op === 'const' ? 'col' : leaf.op, col: column, source: sourceId === base ? null : sourceId }))
  }
  // "use the approved one" for a field the proposal changed (self-healing review, ADR-021)
  const revertField = (c: FieldChange) => {
    const { coll: mapped } = getMapping(c.pointer)
    const coll = mapped ?? schema?.fields?.find((x) => x.pointer === c.pointer)?.in_collection ?? null
    if (c.status === 'added') removeField(c.pointer, mapped)
    else if (c.before) setFieldExpr(c.pointer, coll, c.before.expr)
  }
  const addCollection = (ptr: string) => update((d) => { if (!d.collections.find((c) => c.target === ptr)) d.collections.push({ target: ptr, source: d.record.root_source, link: [{ left: '', right: '' }], fields: [] }); return d })

  if (!mv || !doc || !schema) return <div className="p-10 text-center"><Spinner /></div>
  const pv = preview.data
  const locked = mv.status !== 'proposed'

  return (
    <div className="h-screen flex flex-col">
      {/* top bar */}
      <div className="h-14 px-5 border-b border-line bg-surface flex items-center gap-4 shrink-0">
        <div className="min-w-0 flex items-center gap-3">
          <Link to={`/rounds/${roundId}`} className="text-muted hover:text-ink text-[12.5px]">← {round?.customer?.code} · {round?.label}</Link>
          <span className="heading">Mapping Studio</span>
          <span className="text-muted tnum">v{mv.version}</span>
          <StatusBadge status={mv.status} />
          <Confidence value={mv.confidence} />
          {dirty && <Badge tone="warn">unsaved edits</Badge>}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <button className={cn('btn btn-sm', view === 'relationships' && 'bg-signal-soft text-signal border-signal/30')} title="แผนภาพว่าชีต/ตารางเชื่อมกันยังไง" onClick={() => setView(view === 'relationships' ? 'fields' : 'relationships')}><Network size={13} /> {view === 'relationships' ? 'Back to fields' : 'How sheets connect'}</button>
          <button className={cn('btn btn-sm', showRaw && 'bg-signal-soft text-signal border-signal/30')} onClick={() => { setRawText(JSON.stringify(doc, null, 2)); setShowRaw(true) }}><Braces size={13} /> JSON</button>
          <button className="btn btn-sm" title="ให้ระบบหาสาเหตุของปัญหาที่พบ" onClick={() => { setRightTab('diagnosis'); diagnose.mutate() }}><Stethoscope size={13} /> Diagnose</button>
          {dirty && <button className="btn btn-sm btn-primary" disabled={save.isPending} onClick={() => save.mutate()}><Save size={13} /> Save as new version</button>}
          {!dirty && !locked && (
            <>
              <button className="btn btn-sm btn-danger" onClick={() => setReject(true)}><X size={13} /> Reject</button>
              {mv.policy.decision === 'block'
                ? <button className="btn btn-sm" onClick={() => setOverrideOpen(true)} title="Blocked by policy — approving needs the approver role and a reason"><ShieldAlert size={13} /> Override block…</button>
                : <button className="btn btn-sm btn-primary" disabled={approve.isPending} onClick={() => approve.mutate(false)}><Check size={13} /> Approve</button>}
            </>
          )}
          {!dirty && mv.status === 'approved' && <Link to={`/rounds/${roundId}`} className="btn btn-sm">Back to round</Link>}
        </div>
      </div>

      {/* policy strip */}
      <div className="px-5 py-2 border-b border-line bg-surface-2/60 text-[12.5px] flex items-center gap-4 shrink-0 overflow-x-auto">
        <RiskLevel level={mv.policy.risk_level} factors={mv.policy.risk_factors} />
        <span className={cn('font-medium', mv.policy.decision === 'auto_approve' ? 'text-ok' : mv.policy.decision === 'block' ? 'text-bad' : 'text-warn')}>{mv.policy.decision?.replace('_', ' ')}</span>
        {mv.policy.risk_level === 'high' && mv.policy.risk_factors?.map((r) => <span key={r} className="text-bad whitespace-nowrap">• {r}</span>)}
        {mv.policy.impact?.affected_records !== undefined && !mv.policy.impact.high && <span className="text-ink-2 whitespace-nowrap tnum">• {fmtInt(mv.policy.impact.affected_records)} of {fmtInt(mv.policy.impact.of_records)} records change vs the approved mapping</span>}
        {mv.policy.reasons.map((r, i) => <span key={i} className="text-ink-2 whitespace-nowrap">• {r}</span>)}
        {mv.open_questions.length > 0 && <span className="text-warn whitespace-nowrap">• {mv.open_questions.length} open question{mv.open_questions.length > 1 ? 's' : ''}</span>}
      </div>

      <div className="px-5 py-2 border-b border-line bg-surface shrink-0">
        <HelpStrip id="studio">
          {view === 'relationships'
            ? <>Each box is a table found in the files; lines are links measured on the real rows. Click a line for its numbers, drag between two columns to connect tables. <span className="text-muted">— กล่องคือตารางในไฟล์ เส้นคือการเชื่อมที่วัดจากข้อมูลจริง ลากระหว่างคอลัมน์เพื่อเชื่อมเอง</span></>
            : <><b>Left</b>: columns found in the files · <b>Middle</b>: fields of the target format — drag a column onto a field to map it · <b>Right</b>: the JSON you will get, problems, and what changed. Approve when it looks right. <span className="text-muted">— ซ้าย: คอลัมน์ในไฟล์ · กลาง: field ปลายทาง ลากคอลัมน์มาวางเพื่อจับคู่ · ขวา: ผลลัพธ์และสิ่งที่เปลี่ยน แล้วกด Approve</span></>}
        </HelpStrip>
      </div>

      {view === 'relationships' ? <RelationshipMap roundId={roundId} doc={doc} schemaFields={schema.fields ?? []} locked={locked} onChange={update} /> : (
      <div className="flex-1 min-h-0 grid grid-cols-[240px_minmax(0,1fr)_380px]">
        {/* LEFT: sources */}
        <aside className="border-r border-line overflow-auto bg-surface">
          <div className="px-4 pt-4 pb-2 text-[12px] text-muted">Sources — {doc.sources.length} table{doc.sources.length > 1 ? 's' : ''} from {round?.files?.length} file{(round?.files?.length ?? 0) > 1 ? 's' : ''}</div>
          {doc.sources.map((s) => {
            const cols = columnsFor(s.id)
            const open = selectedSource === s.id
            const used = new Set<string>()
            doc.fields.forEach((f) => collectCols(f.expr, doc.record.root_source).forEach((c) => c.startsWith(s.id + '.') && used.add(c.slice(s.id.length + 1))))
            doc.collections.forEach((c) => { c.fields.forEach((f) => collectCols(f.expr, c.source).forEach((x) => x.startsWith(s.id + '.') && used.add(x.slice(s.id.length + 1)))); if (c.source === s.id) c.link.forEach((l) => used.add(l.right)); if (doc.record.root_source === s.id) c.link.forEach((l) => used.add(l.left)) })
            return (
              <div key={s.id} className="border-b border-line">
                <button className="w-full flex items-center gap-2 px-4 py-2.5 hover:bg-surface-2 text-left" onClick={() => { setSelectedSource(open ? null : s.id); if (!open) setRightTab('source') }}>
                  {open ? <ChevronDown size={14} className="text-muted" /> : <ChevronRight size={14} className="text-muted" />}
                  <span className="font-medium truncate">{s.id}</span>
                  <Badge tone={s.id === doc.record.root_source ? 'signal' : 'neutral'} className="ml-auto h-5 text-[11px]">{s.id === doc.record.root_source ? 'root' : s.role}</Badge>
                </button>
                <div className="px-4 pb-2 -mt-1 text-[11.5px] text-muted truncate">{s.sheet?.name ? `sheet “${s.sheet.name}”` : 'CSV'}{' · '}header row {String((s.table as { header_row?: number | string }).header_row ?? 'auto')}</div>
                {open && (
                  <ul className="pb-2">
                    {cols.map((c) => (
                      <li key={c} draggable={!locked} onDragStart={(e) => { e.dataTransfer.setData(DRAG_TYPE, JSON.stringify({ source: s.id, column: c })); e.dataTransfer.effectAllowed = 'copy' }} onDragEnd={() => setDropOn(null)}
                        className={cn('flex items-center gap-2 px-4 py-1 text-[12.5px]', !locked && 'cursor-grab active:cursor-grabbing hover:bg-surface-2', used.has(c) ? 'text-ink' : 'text-muted')}>
                        <span className={cn('w-1.5 h-1.5 rounded-full shrink-0', used.has(c) ? 'bg-signal' : 'bg-line-2')} />
                        <span className="truncate">{c}</span>
                        {selected && !locked && <button className="ml-auto btn btn-ghost btn-sm text-signal" onClick={() => pointAt(selected, s.id, c)}>use</button>}
                      </li>
                    ))}
                    {!cols.length && <li className="px-4 py-1 text-[12px] text-muted">columns unavailable (profile missing)</li>}
                  </ul>
                )}
              </div>
            )
          })}
          <div className="p-4 text-[11.5px] text-muted leading-relaxed">Drag a column onto a target field — or select a field, then click <span className="text-signal">use</span> on a column. <span className="text-signal">Relationships</span> shows how the tables connect.</div>
        </aside>

        {/* CENTER: target fields */}
        <section className="overflow-auto min-w-0">
          {groups.map((g) => {
            const coll = g.pointer ? doc.collections.find((c) => c.target === g.pointer) : null
            return (
              <div key={g.title} className="border-b border-line">
                <div className="sticky top-0 z-10 bg-paper/95 backdrop-blur px-5 py-2.5 flex items-center gap-3 border-b border-line">
                  <span className="font-semibold">{g.title}</span>
                  {g.pointer ? (
                    coll ? (
                      <span className="text-[12px] text-muted flex items-center gap-2 flex-wrap">
                        from <SourceSelect value={coll.source} sources={doc.sources.map((s) => s.id)} disabled={locked} onChange={(v) => update((d) => { const c = d.collections.find((x) => x.target === g.pointer); if (c) c.source = v; return d })} />
                        linked by <Inline value={coll.link[0]?.left ?? ''} options={columnsFor(doc.record.root_source)} disabled={locked} onChange={(v) => update((d) => { const c = d.collections.find((x) => x.target === g.pointer); if (c) c.link[0] = { ...c.link[0], left: v }; return d })} />
                        ↔ <Inline value={coll.link[0]?.right ?? ''} options={columnsFor(coll.source)} disabled={locked} onChange={(v) => update((d) => { const c = d.collections.find((x) => x.target === g.pointer); if (c) c.link[0] = { ...c.link[0], right: v }; return d })} />
                        {!locked && <button className="btn btn-ghost btn-sm text-bad" title="Remove collection" onClick={() => update((d) => { d.collections = d.collections.filter((c) => c.target !== g.pointer); return d })}><Trash2 size={12} /></button>}
                      </span>
                    ) : (
                      <span className="text-[12px] text-muted flex items-center gap-2">not mapped {!locked && <button className="btn btn-ghost btn-sm text-signal" onClick={() => addCollection(g.pointer!)}><Plus size={12} /> add collection</button>}</span>
                    )
                  ) : <span className="text-[12px] text-muted">root source: {doc.record.root_source} · identity: {schema.identity_keys.join(', ')}</span>}
                </div>
                {(g.pointer === null || coll) && (
                  <ul>
                    {g.fields.map((f) => {
                      const { fm, coll: c } = getMapping(f.pointer)
                      const isSel = selected === f.pointer
                      return (
                        <li key={f.pointer} className={cn('px-5 border-b border-line/70 last:border-0', dropOn === f.pointer ? 'bg-signal-soft ring-2 ring-inset ring-signal' : isSel ? 'bg-signal-soft/50' : 'hover:bg-surface-2/50')}
                          onDragOver={(e) => { if (locked || !e.dataTransfer.types.includes(DRAG_TYPE)) return; e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; if (dropOn !== f.pointer) setDropOn(f.pointer) }}
                          onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDropOn((p) => (p === f.pointer ? null : p)) }}
                          onDrop={(e) => { e.preventDefault(); setDropOn(null); const raw = e.dataTransfer.getData(DRAG_TYPE); if (!raw) return; const v = JSON.parse(raw) as { source: string; column: string }; pointAt(f.pointer, v.source, v.column); setSelected(f.pointer) }}>
                          <button className="w-full grid grid-cols-[minmax(140px,180px)_minmax(0,1fr)_104px_56px] items-center gap-3 py-2 text-left" onClick={() => setSelected(isSel ? null : f.pointer)}>
                            <span className="flex items-center gap-2 min-w-0"><RiskDot risk={f.risk} /><span className={cn('mono truncate', !fm && 'text-muted')}>{f.pointer.replace(/^\/[^/]+\/\*\//, '').replace(/^\//, '')}</span>{f.required && <span className="text-[10px] text-muted">req</span>}</span>
                            <span className={cn('truncate text-[12.5px]', fm ? 'text-ink' : 'text-muted italic')}>{fm ? describeExpr(fm.expr) : 'not mapped'}</span>
                            <span>{fm && <Confidence value={fm.confidence} />}</span>
                            <span className="text-[11px] text-muted text-right truncate">{f.format ?? f.json_type.filter((t) => t !== 'null')[0]}</span>
                          </button>
                          {isSel && <FieldEditor field={f} fm={fm} locked={locked} columns={columnsFor(c ? doc.collections.find((x) => x.target === c)!.source : doc.record.root_source)} onChange={(e) => setFieldExpr(f.pointer, c ?? (g.pointer || null), e)} onRemove={fm ? () => removeField(f.pointer, c) : undefined} />}
                        </li>
                      )
                    })}
                  </ul>
                )}
              </div>
            )
          })}
          <div className="p-5 text-[12px] text-muted">
            <div className="font-medium text-ink-2 mb-1">Why these mappings</div>
            <ul className="flex flex-col gap-0.5">{doc.meta.evidence.map((e, i) => <li key={i}>• {e}</li>)}</ul>
            {mv.open_questions.length > 0 && <><div className="font-medium text-warn mt-3 mb-1">Open questions</div><ul className="flex flex-col gap-0.5">{mv.open_questions.map((q, i) => <li key={i}>? {q}</li>)}</ul></>}
          </div>
        </section>

        {/* RIGHT: preview */}
        <aside className="border-l border-line bg-surface flex flex-col min-h-0">
          <Tabs value={rightTab} onChange={setRightTab} items={[{ id: 'json', label: 'Output preview', count: pv?.records.length, hint: 'ตัวอย่าง JSON ที่จะได้จากไฟล์นี้' }, { id: 'source', label: 'Source rows', hint: 'แถวต้นทางจากไฟล์' }, { id: 'findings', label: 'Problems', count: pv?.findings.length, hint: 'ปัญหาที่พบตอนลองรัน mapping' }, { id: 'changes', hint: 'field ไหนเปลี่ยนจาก mapping ที่ approve แล้ว ตรวจกับข้อมูลจริง', label: <span className="flex items-center gap-1">What changed{(mv.policy.field_review?.needs_decision?.length ?? 0) > 0 && <span className="w-1.5 h-1.5 rounded-full bg-warn" />}</span> }, { id: 'diagnosis', label: 'Diagnosis', hint: 'สาเหตุของปัญหาและวิธีแก้' }, { id: 'compare', hint: 'รันคู่กับ mapping เดิม ดูว่า record/ราคาไหนเปลี่ยน', label: <span className="flex items-center gap-1">Compare outputs{mv.policy.sandbox?.verdict === 'fail' && <span className="w-1.5 h-1.5 rounded-full bg-bad" />}</span> }]} />
          <div className="flex-1 overflow-auto p-3">
            {rightTab === 'json' && (
              <>
                {preview.isPending && <div className="text-muted text-[12px] mb-2 flex items-center gap-2"><Spinner /> re-running mapping on sample rows…</div>}
                {pv && !pv.ok && <div className="text-bad text-[12.5px] mb-2">{pv.errors?.join('; ')}{pv.validation?.issues.filter((i) => i.level === 'error').map((i) => i.message).join('; ')}</div>}
                {pv?.ok && <div className="text-[12px] text-muted mb-2 tnum">{fmtInt(pv.stats?.rows_out as number)} records from {fmtInt(pv.stats?.rows_in as number)} sample rows · {pv.stats?.duration_ms as number} ms</div>}
                {pv?.records.slice(0, 20).map((r, i) => <pre key={i} className="mono text-[11.5px] leading-relaxed bg-surface-2 rounded-[var(--radius-md)] p-2.5 mb-2 overflow-auto whitespace-pre-wrap">{JSON.stringify(stripMeta(r), null, 1)}</pre>)}
              </>
            )}
            {rightTab === 'source' && (
              srcPreview.data ? (
                <div className="overflow-auto -m-3">
                  <div className="px-3 py-2 text-[12px] text-muted">{srcPreview.data.file} · extracted as <span className="font-medium text-ink">{selectedSource}</span></div>
                  <table className="text-[12px] border-collapse min-w-full">
                    <thead><tr>{srcPreview.data.columns.map((c) => <th key={c} className="sticky top-0 bg-surface-2 text-left px-2 py-1.5 border border-line font-medium whitespace-nowrap">{c}</th>)}</tr></thead>
                    <tbody>{srcPreview.data.rows.map((row, i) => <tr key={i}>{row.map((v, j) => <td key={j} className="px-2 py-1 border border-line whitespace-nowrap max-w-[220px] truncate">{v === null ? '' : String(v)}</td>)}</tr>)}</tbody>
                  </table>
                </div>
              ) : <div className="text-muted text-[12.5px]">{selectedSource ? (srcPreview.isLoading ? 'Loading…' : srcPreview.error ? String((srcPreview.error as Error).message) : '') : 'Select a source on the left.'}</div>
            )}
            {rightTab === 'findings' && (pv?.findings.length ? pv.findings.map((f, i) => <FindingRow key={i} f={f} />) : <div className="text-muted text-[12.5px]">No findings on the sample.</div>)}
            {rightTab === 'changes' && <ChangesPanel mappingId={mappingId} onRevert={locked ? null : revertField} />}
            {rightTab === 'compare' && <ComparePanel mappingId={mappingId} stored={mv.policy.sandbox ?? null} draft={dirty ? doc : null} />}
            {rightTab === 'diagnosis' && (
              diagnose.isPending ? <div className="text-muted text-[12.5px] flex items-center gap-2"><Spinner /> analysing…</div> : diagnose.data ? (
                <div className="flex flex-col gap-3 text-[12.5px]">
                  {diagnose.data.summary_th && <div className="card p-3">{diagnose.data.summary_th}</div>}
                  {!diagnose.data.ai && <Badge tone="neutral">rule-based — configure an LLM for AI root-cause analysis</Badge>}
                  {diagnose.data.root_causes.map((rc, i) => (
                    <div key={i} className="card p-3">
                      <div className="font-medium">{rc.finding}</div>
                      <div className="text-ink-2 mt-1">{rc.cause}</div>
                      <div className="mt-1 text-signal">Fix: {rc.fix}</div>
                    </div>
                  ))}
                  {diagnose.data.root_causes.length === 0 && <div className="text-muted">Nothing to diagnose — the mapping runs clean on the sample.</div>}
                  {diagnose.data.patched_mapping && !locked && <button className="btn btn-primary btn-sm self-start" onClick={() => { setDoc(diagnose.data!.patched_mapping!); setDirty(true); toast.push({ kind: 'info', title: 'Patched mapping loaded', detail: 'Review the changes, then save as a new version.' }) }}><Wand2 size={13} /> Apply suggested patch</button>}
                </div>
              ) : <div className="text-muted text-[12.5px]">Click Diagnose to explain findings and get suggested fixes.</div>
            )}
          </div>
        </aside>
      </div>
      )}

      <Modal open={showRaw} onClose={() => setShowRaw(false)} title="Mapping document (JSON)" width="max-w-4xl" footer={<><button className="btn" onClick={() => setShowRaw(false)}>Close</button>{!locked && <button className="btn btn-primary" onClick={() => { try { const d = JSON.parse(rawText) as MappingDocument; setDoc(d); setDirty(true); setShowRaw(false) } catch (e) { toast.push({ kind: 'bad', title: 'Invalid JSON', detail: (e as Error).message }) } }}>Apply</button>}</>}>
        <textarea className="input mono h-[60vh] py-2 resize-none" value={rawText} onChange={(e) => setRawText(e.target.value)} spellCheck={false} />
      </Modal>
      <Modal open={reject} onClose={() => setReject(false)} title="Reject this mapping" width="max-w-md" footer={<><button className="btn" onClick={() => setReject(false)}>Cancel</button><button className="btn btn-danger" onClick={() => rejectM.mutate()}>Reject</button></>}>
        <textarea className="input h-24 py-2" placeholder="Why? (kept in the audit log and shown to the AI on the next proposal)" value={comment} onChange={(e) => setComment(e.target.value)} />
      </Modal>
      <Modal open={overrideOpen} onClose={() => setOverrideOpen(false)} title="Override the policy block" width="max-w-md" footer={<><button className="btn" onClick={() => setOverrideOpen(false)}>Cancel</button><button className="btn btn-danger" disabled={!comment.trim() || approve.isPending} onClick={() => approve.mutate(true)}><ShieldAlert size={13} /> Approve anyway</button></>}>
        <div className="space-y-3 text-[13px]">
          <ul className="text-ink-2 space-y-1">{mv.policy.reasons.map((r, i) => <li key={i}>• {r}</li>)}</ul>
          <textarea className="input h-24 py-2" placeholder="Why is this safe to approve? (required, kept in the audit log)" value={comment} onChange={(e) => setComment(e.target.value)} />
        </div>
      </Modal>
    </div>
  )
}

function stripMeta(r: Record<string, unknown>) { const { source_meta: _m, ...rest } = r; return rest }

function collectCols(e: Expr, def: string): string[] {
  if (e.op === 'col') return [`${e.source ?? def}.${e.name}`]
  return (e.args ?? []).flatMap((a) => collectCols(a, def))
}

function SourceSelect({ value, sources, onChange, disabled }: { value: string; sources: string[]; onChange: (v: string) => void; disabled?: boolean }) {
  return <select className="input h-6 w-auto text-[12px] py-0 inline-block" value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>{sources.map((s) => <option key={s}>{s}</option>)}</select>
}
function Inline({ value, options, onChange, disabled }: { value: string; options: string[]; onChange: (v: string) => void; disabled?: boolean }) {
  return (
    <select className="input h-6 w-auto max-w-[180px] text-[12px] py-0 inline-block" value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
      {!options.includes(value) && <option value={value}>{value || '—'}</option>}
      {options.map((o) => <option key={o}>{o}</option>)}
    </select>
  )
}

function FieldEditor({ field, fm, columns, locked, onChange, onRemove }: { field: FieldSpec; fm?: FieldMapping; columns: string[]; locked: boolean; onChange: (e: Expr) => void; onRemove?: () => void }) {
  const leaf = exprLeaf(fm?.expr)
  const [params, setParams] = useState(JSON.stringify(leaf.params ?? {}, null, 0))
  const [constVal, setConstVal] = useState(leaf.constValue === undefined ? '' : JSON.stringify(leaf.constValue))
  const [paramErr, setParamErr] = useState<string | null>(null)
  useEffect(() => { setParams(JSON.stringify(leaf.params ?? {}, null, 0)); setConstVal(leaf.constValue === undefined ? '' : JSON.stringify(leaf.constValue)) }, [fm]) // eslint-disable-line react-hooks/exhaustive-deps
  const complex = fm && fm.expr.op !== 'col' && fm.expr.op !== 'const' && fm.expr.args?.[0]?.op !== 'col'
  const presetOp = leaf.op === 'vat_adjust' && (leaf.params.mode === 'remove') ? 'vat_adjust_remove' : leaf.op
  const apply = (patch: Partial<typeof leaf>) => {
    let p = leaf.params
    try { p = JSON.parse(params || '{}'); setParamErr(null) } catch (e) { setParamErr((e as Error).message) }
    const next = { ...leaf, params: p, ...patch }
    if (patch.op) { const preset = OP_PRESETS.find((x) => x.op === patch.op); next.params = preset?.params ?? {}; setParams(JSON.stringify(next.params)) }
    if (patch.op === 'const' || next.op === 'const') { try { next.constValue = constVal === '' ? null : JSON.parse(constVal) } catch { next.constValue = constVal } }
    onChange(buildExpr(next))
  }
  return (
    <div className="pb-3 pl-7 pr-2 text-[12.5px]">
      <div className="text-muted mb-2">{field.description_th ?? field.description ?? ''}{field.synonyms_th.length ? <span className="ml-2">synonyms: {[...field.synonyms_th.slice(0, 4), ...field.synonyms_en.slice(0, 3)].join(', ')}</span> : null}{field.enum ? <span className="ml-2 mono">{field.enum.join(' | ')}</span> : null}</div>
      {complex ? (
        <div className="text-muted">This field uses a nested expression — edit it in the JSON view.</div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <select className="input h-7 w-auto text-[12.5px] py-0" disabled={locked} value={presetOp} onChange={(e) => apply({ op: e.target.value })}>
            {OP_PRESETS.map((p) => <option key={p.op} value={p.op}>{p.label}</option>)}
          </select>
          {presetOp !== 'const' ? (
            <select className="input h-7 w-auto max-w-[260px] text-[12.5px] py-0" disabled={locked} value={leaf.col ?? ''} onChange={(e) => apply({ col: e.target.value })}>
              <option value="">— choose column —</option>
              {leaf.col && !columns.includes(leaf.col) && <option value={leaf.col}>{leaf.col} (missing)</option>}
              {columns.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          ) : (
            <input className="input h-7 w-56 text-[12.5px] mono" disabled={locked} value={constVal} placeholder='"THB" · true · 7' onChange={(e) => setConstVal(e.target.value)} onBlur={() => apply({})} />
          )}
          {!['col', 'const', 'trim', 'upper', 'normalize_whitespace', 'parse_bool', 'pack_size', 'to_list', 'strip_leading_zeros'].includes(presetOp) && (
            <input className={cn('input h-7 flex-1 min-w-[200px] text-[12px] mono', paramErr && 'border-bad')} disabled={locked} value={params} onChange={(e) => setParams(e.target.value)} onBlur={() => apply({})} title={paramErr ?? 'params (JSON)'} />
          )}
          {onRemove && !locked && <button className="btn btn-ghost btn-sm text-bad" onClick={onRemove}><Trash2 size={12} /> unmap</button>}
        </div>
      )}
      {fm?.evidence?.length ? <ul className="mt-2 text-muted flex flex-col gap-0.5">{fm.evidence.map((e, i) => <li key={i}>• {e}</li>)}</ul> : null}
    </div>
  )
}

// ---------------------------------------------------------------------------------------------
// Sandbox (ADR-018): this mapping next to the approved one, on the round's real files
// ---------------------------------------------------------------------------------------------
type SandboxList = 'price_changes' | 'changed' | 'lost' | 'gained'

const CHECK_TONE: Record<string, string> = { same: 'bg-ok-soft text-ok', vat_added: 'bg-warn-soft text-warn', vat_removed: 'bg-warn-soft text-warn', scaled: 'bg-warn-soft text-warn', different: 'bg-bad-soft text-bad', no_data: 'bg-surface-2 text-ink-2' }

function ChangesPanel({ mappingId, onRevert }: { mappingId: string; onRevert: ((c: FieldChange) => void) | null }) {
  const { data, isLoading, error } = useQuery({ queryKey: ['field-review', mappingId], queryFn: () => api.fieldReview(mappingId) })
  if (isLoading) return <div className="text-muted text-[12.5px] flex items-center gap-2"><Spinner /> comparing with the approved mapping…</div>
  if (error) return <div className="text-bad text-[12.5px]">{(error as Error).message}</div>
  if (!data?.baseline_id) return <div className="text-muted text-[12.5px]">No approved mapping for this customer yet, so every field is new — review them in the field list.</div>
  const changed = data.fields.filter((c) => c.status !== 'unchanged')
  const decide = changed.filter((c) => data.needs_decision.includes(c.pointer))
  const safe = changed.filter((c) => !data.needs_decision.includes(c.pointer))
  return (
    <div className="flex flex-col gap-3 text-[12.5px]">
      <div className="text-muted">vs approved v{data.baseline_version}{data.compared_with ? ' · values checked against the last published round, SKU by SKU' : ' · no published round yet to check values against'}</div>
      <div className="grid grid-cols-3 gap-2">
        {[['Unchanged', data.counts.unchanged ?? 0, ''], ['Changed', (data.counts.changed ?? 0) + (data.counts.added ?? 0) + (data.counts.removed ?? 0), ''], ['Need a decision', decide.length, decide.length ? 'text-warn' : 'text-ok']].map(([k, v, cls]) => <div key={String(k)} className="card p-2"><div className="text-muted text-[11.5px]">{k}</div><div className={cn('tnum text-[17px] font-semibold', cls as string)}>{fmtInt(v as number)}</div></div>)}
      </div>
      {!changed.length && <div className="text-ok">Nothing changed — the proposal reads every field like the approved mapping.</div>}
      {decide.length > 0 && <><div className="font-medium">Needs your decision</div>{decide.map((c) => <ChangeCard key={c.pointer} c={c} onRevert={onRevert} />)}</>}
      {safe.length > 0 && <><div className="font-medium">Safe changes</div>{safe.map((c) => <ChangeCard key={c.pointer} c={c} onRevert={onRevert} />)}</>}
      {changed.length > 0 && <div className="text-muted">Keep the proposal and approve, or press “Use approved” on a field and save the result as a new version.</div>}
    </div>
  )
}

function ChangeCard({ c, onRevert }: { c: FieldChange; onRevert: ((c: FieldChange) => void) | null }) {
  const fmt = (v: unknown) => (v === null || v === undefined ? '∅' : typeof v === 'object' ? JSON.stringify(v) : String(v))
  const side = (x: FieldChange['before']) => (x ? (x.reads.length ? x.reads.join(', ') : describeExpr(x.expr)) : '—')
  return (
    <div className="card p-2.5">
      <div className="flex items-center gap-2">
        <RiskDot risk={c.risk ?? 'low'} /><span className="mono truncate">{c.pointer}</span><Badge tone={c.status === 'removed' ? 'bad' : c.status === 'added' ? 'info' : 'warn'} className="h-5 px-1.5 text-[10.5px]">{c.status}</Badge>
        {onRevert && <button className="btn btn-ghost btn-sm ml-auto text-signal" onClick={() => onRevert(c)}>{c.status === 'added' ? 'Remove' : 'Use approved'}</button>}
      </div>
      <div className="mt-1 mono text-[11.5px]"><span className="text-muted">{side(c.before)}</span> → <span>{c.status === 'removed' ? 'not mapped' : side(c.after)}</span></div>
      {c.before && c.after && describeExpr(c.before.expr) !== describeExpr(c.after.expr) && <div className="text-muted text-[11.5px] mono">{describeExpr(c.before.expr)} → {describeExpr(c.after.expr)}</div>}
      {c.check && (
        <div className={cn('mt-1.5 rounded-[var(--radius-sm)] px-2 py-1', CHECK_TONE[c.check.verdict] ?? 'bg-surface-2')}>
          {c.check.summary}
          {c.check.examples?.length ? <div className="mono text-[11px] opacity-90">{c.check.examples.slice(0, 3).map((e) => `${e.identity}: ${fmt(e.before)} → ${fmt(e.after)}`).join(' · ')}</div> : null}
        </div>
      )}
    </div>
  )
}

function ComparePanel({ mappingId, stored, draft }: { mappingId: string; stored: SandboxReport | null; draft: MappingDocument | null }) {
  const run = useMutation({ mutationFn: (d: MappingDocument | undefined) => api.compareMapping(mappingId, d) })
  const report = draft ? run.data ?? null : run.data ?? stored
  const live = !!run.data
  return (
    <div className="flex flex-col gap-3 text-[12.5px]">
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn btn-sm" disabled={run.isPending} onClick={() => run.mutate(draft ?? undefined)}>
          {run.isPending ? <><Spinner /> running both mappings…</> : draft ? 'Compare these edits with the approved mapping' : report ? 'Run again' : 'Compare with the approved mapping'}
        </button>
        {run.error && <span className="text-bad">{(run.error as Error).message}</span>}
      </div>
      {!report && !run.isPending && <div className="text-muted">{draft ? 'Your unsaved edits are compared only when you ask.' : 'No comparison stored with this version (first mapping of this customer, or a verbatim reuse).'}</div>}
      {report && <SandboxView mappingId={mappingId} report={report} live={live} />}
    </div>
  )
}

function SandboxView({ mappingId, report, live }: { mappingId: string; report: SandboxReport; live: boolean }) {
  const [list, setList] = useState<SandboxList>('price_changes')
  const stored = useQuery({ queryKey: ['sandbox', mappingId, list], queryFn: () => api.sandboxList(mappingId, list, 0, 200), enabled: !live && !!report.delta })
  if (!report.baseline_id) return <div className="text-muted">{report.message ?? 'Nothing to compare with yet.'}</div>
  const d = report.delta
  const o = report.old
  const n = report.new
  const rows: [string, number | undefined, number | undefined, boolean][] = [
    ['Output rows', o.rows_out, n.rows_out, false],
    ['Errors', o.errors, n.errors, (n.errors ?? 0) > (o.errors ?? 0)],
    ['Records without a valid price', o.invalid_prices, n.invalid_prices, (n.invalid_prices ?? 0) > (o.invalid_prices ?? 0)],
    ['Duplicate records', o.duplicates, n.duplicates, (n.duplicates ?? 0) > (o.duplicates ?? 0)],
    ['Rows without identity', o.missing_identity, n.missing_identity, (n.missing_identity ?? 0) > (o.missing_identity ?? 0)],
  ]
  const items: SandboxItem[] = live ? report.details?.[list] ?? [] : stored.data?.items ?? []
  const totals: Record<SandboxList, number> = { price_changes: d?.changed_prices ?? 0, changed: d?.changed_records ?? 0, lost: d?.lost ?? 0, gained: d?.gained ?? 0 }
  const fmt = (v: unknown) => (v === null || v === undefined ? '∅' : typeof v === 'number' ? v.toLocaleString() : typeof v === 'object' ? JSON.stringify(v) : String(v))
  return (
    <>
      <div className={cn('rounded-[var(--radius-md)] px-3 py-2', report.verdict === 'fail' ? 'bg-bad-soft text-bad' : report.verdict === 'pass' ? 'bg-ok-soft text-ok' : 'bg-surface-2 text-ink-2')}>
        <div className="font-medium">{report.verdict === 'fail' ? 'Blocked — this mapping would make the data worse' : report.verdict === 'pass' ? 'Nothing lost or broken compared with the approved mapping' : 'Could not compare'}</div>
        {report.problems?.map((p, i) => <div key={i}>• {p}</div>)}
        {!o.ok && <div>The approved mapping cannot read these files: {o.problems?.join('; ')}</div>}
      </div>
      <div className="text-muted">vs approved v{report.baseline_version} · same files · {report.rows_limit ? `first ${fmtInt(report.rows_limit)} rows per source` : 'all rows'}{live ? ' · not saved' : ''}</div>
      <table className="w-full tnum">
        <thead><tr className="text-muted text-left"><th className="py-1 font-normal">Metric</th><th className="py-1 font-normal text-right">Approved</th><th className="py-1 font-normal text-right">This mapping</th></tr></thead>
        <tbody>{rows.map(([label, a, b, worse]) => (
          <tr key={label} className="border-t border-line"><td className="py-1.5">{label}</td><td className="text-right">{a === undefined ? '–' : fmtInt(a)}</td><td className={cn('text-right', worse && 'text-bad font-medium')}>{b === undefined ? '–' : fmtInt(b)}</td></tr>
        ))}</tbody>
      </table>
      {report.override_conflicts && report.override_conflicts.length > 0 && (
        <div className="card p-3 border-warn/40">
          <div className="font-medium text-warn mb-1">{report.override_conflicts.length} persistent override{report.override_conflicts.length > 1 ? 's' : ''} collide with this mapping</div>
          <ul className="flex flex-col gap-1 text-[12px]">{report.override_conflicts.slice(0, 20).map((c, i) => <li key={i}><Badge tone="warn" className="h-5 px-1.5 text-[10.5px] mr-1.5">{c.kind.replace('_', ' ')}</Badge>{c.message}</li>)}</ul>
          <div className="text-muted text-[11.5px] mt-1.5">Overrides are re-applied on every import of this customer. Keep or remove them on the customer page before approving.</div>
        </div>
      )}
      {d && (
        <>
          <div className="flex flex-wrap gap-1.5">{(['price_changes', 'changed', 'lost', 'gained'] as SandboxList[]).map((k) => (
            <button key={k} className={cn('chip', list === k ? 'bg-signal-soft text-signal border-signal/30' : 'bg-surface-2 border-line', k === 'lost' && totals[k] > 0 && 'text-bad')} onClick={() => setList(k)}>
              {{ price_changes: 'Changed prices', changed: 'Changed records', lost: 'Missing records', gained: 'New records' }[k]} <span className="tnum ml-1">{fmtInt(totals[k])}</span>
            </button>
          ))}{d.large_price_moves > 0 && <Badge tone="warn">{fmtInt(d.large_price_moves)} prices move ≥ 20%</Badge>}</div>
          <div className="border border-line rounded-[var(--radius-md)] max-h-[360px] overflow-auto">
            {stored.isLoading && !live ? <div className="p-3"><Spinner /></div> : items.length === 0 ? <div className="p-3 text-muted">None.</div> : (
              <table className="w-full text-[12px]">
                <tbody>{items.map((it, i) => (
                  <tr key={i} className="border-b border-line last:border-0 align-top">
                    <td className="px-2 py-1.5 mono whitespace-nowrap">{it.identity}</td>
                    {list === 'price_changes' && <><td className="px-2 py-1.5 text-right tnum">{fmt(it.before)}</td><td className="px-1 text-muted">→</td><td className="px-2 py-1.5 tnum">{fmt(it.after)}</td><td className={cn('px-2 py-1.5 text-right tnum', it.pct != null && Math.abs(it.pct) >= 20 && 'text-warn font-medium')}>{it.pct == null ? '' : `${it.pct > 0 ? '+' : ''}${it.pct}%`}</td></>}
                    {list === 'changed' && <td className="px-2 py-1.5 text-muted">{it.changes?.slice(0, 3).map((c) => `${c.pointer}: ${fmt(c.before)} → ${fmt(c.after)}`).join(' · ')}{(it.fields ?? 0) > 3 ? ` · +${(it.fields ?? 0) - 3} more` : ''}</td>}
                  </tr>
                ))}</tbody>
              </table>
            )}
          </div>
          {totals[list] > items.length && <div className="text-muted">Showing {fmtInt(items.length)} of {fmtInt(totals[list])}.</div>}
        </>
      )}
    </>
  )
}
