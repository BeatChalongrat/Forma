import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link2, RefreshCw, Trash2 } from 'lucide-react'
import { api, type CollectionMapping, type FieldSpec, type MappingDocument, type RelEdge, type RelNode, type SourceSpec } from '../lib/api'
import { cn, fmtInt } from '../lib/utils'
import { Badge, Spinner } from '../components/ui'

/**
 * Visual multi-sheet mapping (ADR-019): every table of the round, the keys that connect them — measured on
 * real rows (cardinality, how many keys match, rows that match nothing) — and the links the mapping uses.
 * Drag from a column dot to a column of another table to connect them; click a line to see and act on it.
 */
type End = { node: string; column: string }
type Pending = { from: End; to: End }
type Pos = { x: number; y: number; w: number; h: number; cols: string[] }

const W = 220
const GAP = 90
const HEAD = 46
const ROW = 22
const MAX_COLS = 9

export function RelationshipMap({ roundId, doc, schemaFields, locked, onChange }: {
  roundId: string
  doc: MappingDocument
  schemaFields: FieldSpec[]
  locked: boolean
  onChange: (fn: (d: MappingDocument) => MappingDocument) => void
}) {
  // the diagram follows the Studio's current (possibly unsaved) mapping, refreshed shortly after edits
  const [snapshot, setSnapshot] = useState(doc)
  useEffect(() => { const t = window.setTimeout(() => setSnapshot(doc), 600); return () => window.clearTimeout(t) }, [doc])
  const q = useQuery({ queryKey: ['relationships', roundId, JSON.stringify(snapshot)], queryFn: () => api.relationships(roundId, snapshot) })
  const [picked, setPicked] = useState<RelEdge | Pending | null>(null)
  const [drag, setDrag] = useState<{ node: string; column: string; x1: number; y1: number; x2: number; y2: number } | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const g = q.data
  // a connection drawn by hand is measured right away, before it becomes part of the mapping
  const pending = picked && !('kind' in picked) ? picked : null
  const probeQ = useQuery({
    queryKey: ['relationships-probe', roundId, JSON.stringify(snapshot), JSON.stringify(pending)],
    queryFn: () => api.relationships(roundId, snapshot, pending!),
    enabled: !!pending,
  })
  const collections = schemaFields.filter((f) => f.is_collection)

  const layout = useMemo(() => {
    const pos: Record<string, Pos> = {}
    if (!g) return { pos, width: 0, height: 0 }
    const touched = new Map<string, Set<string>>()
    for (const e of g.edges) {
      for (const end of [e.from, e.to]) { if (!touched.has(end.node)) touched.set(end.node, new Set()); touched.get(end.node)!.add(end.column) }
    }
    const lanes: RelNode[][] = [g.nodes.filter((n) => n.root), g.nodes.filter((n) => !n.root && n.used), g.nodes.filter((n) => !n.used)]
    let height = 0
    lanes.forEach((lane, li) => {
      let y = 16
      for (const n of lane) {
        const want = touched.get(n.id) ?? new Set<string>()
        const cols = n.columns.filter((c) => want.has(c.name) || c.key || c.mapped).map((c) => c.name).slice(0, MAX_COLS)
        const h = HEAD + Math.max(cols.length, 1) * ROW + 10
        pos[n.id] = { x: 16 + li * (W + GAP), y, w: W, h, cols }
        y += h + 28
      }
      height = Math.max(height, y)
    })
    return { pos, width: 16 + 3 * W + 2 * GAP + 16, height }
  }, [g])

  const portY = (node: string, column: string): number | null => {
    const p = layout.pos[node]
    const i = p?.cols.indexOf(column) ?? -1
    return p && i >= 0 ? p.y + HEAD + i * ROW + ROW / 2 : p ? p.y + HEAD / 2 : null
  }
  const path = (e: { from: { node: string; column: string }; to: { node: string; column: string } }): string | null => {
    const a = layout.pos[e.from.node]
    const b = layout.pos[e.to.node]
    const ya = portY(e.from.node, e.from.column)
    const yb = portY(e.to.node, e.to.column)
    if (!a || !b || ya === null || yb === null) return null
    if (a.x === b.x) { const x = a.x + a.w; return `M ${x} ${ya} C ${x + 70} ${ya}, ${x + 70} ${yb}, ${x} ${yb}` }
    const [x1, x2] = a.x < b.x ? [a.x + a.w, b.x] : [a.x, b.x + b.w]
    const dx = (x2 - x1) / 2
    return `M ${x1} ${ya} C ${x1 + dx} ${ya}, ${x2 - dx} ${yb}, ${x2} ${yb}`
  }
  const toSvg = (ev: React.MouseEvent) => { const r = svgRef.current!.getBoundingClientRect(); return { x: ev.clientX - r.left, y: ev.clientY - r.top } }
  const nodeOf = (id: string) => g?.nodes.find((n) => n.id === id)

  // ---- applying a connection to the mapping -------------------------------------------------------
  const ensureSource = (d: MappingDocument, id: string) => {
    const n = nodeOf(id)
    if (!d.sources.some((s) => s.id === id) && n?.source_spec) d.sources.push(structuredClone(n.source_spec) as SourceSpec)
  }
  const linkAsCollection = (e: RelEdge | Pending, target: string) => onChange((d) => {
    ensureSource(d, e.to.node)
    const link = [{ left: e.from.column, right: e.to.column }]
    const existing = d.collections.find((c) => c.target === target)
    if (existing) { existing.source = e.to.node; existing.link = link } else d.collections.push({ target, source: e.to.node, link, fields: [] } as CollectionMapping)
    return d
  })
  // the receiving table gets the lookup table's columns, one match per row (many-to-one)
  const join = (receiver: End, lookup: End) => onChange((d) => {
    ensureSource(d, receiver.node)
    ensureSource(d, lookup.node)
    d.joins = [...d.joins.filter((j) => !(j.left === receiver.node && j.right === lookup.node)), { left: receiver.node, right: lookup.node, on: [{ left: receiver.column, right: lookup.column }], how: 'left', cardinality: 'N:1' }]
    return d
  })
  const removeEdge = (e: RelEdge) => onChange((d) => {
    if (e.kind === 'join') d.joins = d.joins.filter((j) => !(j.left === e.from.node && j.right === e.to.node))
    if (e.kind === 'link' && e.collection) d.collections = d.collections.filter((c) => c.target !== e.collection)
    return d
  })
  const apply = (fn: () => void) => { fn(); setPicked(null) }

  if (q.isLoading) return <div className="p-10 text-center text-muted"><Spinner /> reading every table of the round…</div>
  if (q.error) return <div className="p-6 text-bad">{(q.error as Error).message}</div>
  if (!g) return null
  const isRoot = (id: string) => id === g.root
  const pickedEdge = picked && 'kind' in picked ? picked : null
  const probe = probeQ.data?.probe ?? null
  return (
    <div className="flex-1 min-h-0 grid lg:grid-cols-[1fr_320px]">
      <div className="overflow-auto bg-paper flex flex-col" onMouseLeave={() => setDrag(null)}>
        <div className="sticky top-0 left-0 px-4 py-1.5 flex items-center gap-2 text-[11.5px] text-muted border-b border-line bg-paper/95 z-10">
          <span>{g.nodes.length} tables · measured on {g.rows_limit ? `the first ${fmtInt(g.rows_limit)} rows` : 'all rows'} of each</span>
          {q.isFetching && <Spinner />}
          <button className="btn btn-ghost btn-sm ml-auto" title="Measure again" onClick={() => q.refetch()}><RefreshCw size={12} /></button>
        </div>
        <svg ref={svgRef} width={layout.width} height={Math.max(layout.height, 360)} className="select-none"
          onMouseMove={(ev) => { if (drag) { const p = toSvg(ev); setDrag({ ...drag, x2: p.x, y2: p.y }) } }}
          onMouseUp={() => setDrag(null)}>
          {g.edges.map((e, i) => {
            const d = path(e)
            if (!d) return null
            const bad = e.warnings.length > 0
            const stroke = bad ? 'var(--color-warn)' : e.used ? 'var(--color-signal)' : 'var(--color-muted)'
            const sel = pickedEdge === e
            return (
              <g key={i} className="cursor-pointer" onClick={() => setPicked(e)}>
                <path d={d} fill="none" stroke="transparent" strokeWidth={12} />
                <path d={d} fill="none" stroke={stroke} strokeWidth={sel ? 3 : e.used ? 2 : 1.5} strokeDasharray={e.used ? undefined : '6 5'} />
              </g>
            )
          })}
          {drag && <line x1={drag.x1} y1={drag.y1} x2={drag.x2} y2={drag.y2} stroke="var(--color-signal)" strokeWidth={2} strokeDasharray="4 4" />}
          {g.nodes.map((n) => {
            const p = layout.pos[n.id]
            if (!p) return null
            return (
              <g key={n.id}>
                <rect x={p.x} y={p.y} width={p.w} height={p.h} rx={10} fill="var(--color-surface)" stroke={isRoot(n.id) ? 'var(--color-signal)' : 'var(--color-line-2)'} strokeWidth={isRoot(n.id) ? 2 : 1} strokeDasharray={n.used ? undefined : '5 4'} />
                <text x={p.x + 12} y={p.y + 19} className="fill-[var(--color-ink)] text-[13px] font-semibold">{n.sheet ?? n.id}</text>
                <text x={p.x + 12} y={p.y + 36} className="fill-[var(--color-muted)] text-[11px]">{isRoot(n.id) ? 'main table' : n.used ? n.collections.join(', ') || 'read by the mapping' : 'not used yet'} · {fmtInt(n.rows)} rows</text>
                {p.cols.map((c, i) => {
                  const col = n.columns.find((x) => x.name === c)
                  const cy = p.y + HEAD + i * ROW + ROW / 2
                  return (
                    <g key={c}>
                      <text x={p.x + 12} y={cy + 4} className={cn('text-[12px]', col?.mapped ? 'fill-[var(--color-ink)]' : 'fill-[var(--color-ink-2)]')}>{c.length > 26 ? c.slice(0, 25) + '…' : c}{col?.key ? '  🔑' : ''}</text>
                      {!locked && (
                        <circle cx={p.x + p.w - 10} cy={cy} r={5} className="cursor-crosshair" fill={drag && drag.node !== n.id ? 'var(--color-signal)' : 'var(--color-line-2)'}
                          onMouseDown={(ev) => { ev.stopPropagation(); const s = toSvg(ev); setDrag({ node: n.id, column: c, x1: s.x, y1: s.y, x2: s.x, y2: s.y }) }}
                          onMouseUp={(ev) => {
                            ev.stopPropagation()
                            if (drag && drag.node !== n.id) {
                              const a = { node: drag.node, column: drag.column }
                              const b = { node: n.id, column: c }
                              setPicked(isRoot(b.node) ? { from: b, to: a } : { from: a, to: b }) // main table on the left, as the mapping's links
                            }
                            setDrag(null)
                          }} />
                      )}
                    </g>
                  )
                })}
                {n.columns.length > p.cols.length && <text x={p.x + 12} y={p.y + p.h - 4} className="fill-[var(--color-muted)] text-[10.5px]">+{n.columns.length - p.cols.length} more columns</text>}
              </g>
            )
          })}
        </svg>
      </div>
      <aside className="border-l border-line bg-surface p-4 overflow-auto text-[12.5px] flex flex-col gap-3">
        {!picked && (
          <>
            <div className="font-medium">How the tables connect</div>
            <ul className="flex flex-col gap-1 text-ink-2">
              <li><span className="text-signal font-medium">solid</span> — a link the mapping uses</li>
              <li><span className="text-muted font-medium">dashed</span> — a connection found in the data</li>
              <li><span className="text-warn font-medium">amber</span> — a link with a problem (keys repeat, rows match nothing)</li>
            </ul>
            <div className="text-muted">Click a line for its numbers. To connect two tables yourself, drag from a column's dot to a column of another table.</div>
            {g.problems.map((p, i) => <div key={i} className="text-bad">{p}</div>)}
          </>
        )}
        {picked && <EdgePanel key={`${picked.from.node}.${picked.from.column}>${picked.to.node}.${picked.to.column}`} edge={picked} probe={pending ? probe : null} probing={probeQ.isFetching} root={g.root} nodeLabel={(id) => nodeOf(id)?.sheet ?? id} collections={collections} locked={locked}
          onLink={(t) => apply(() => linkAsCollection(picked, t))} onJoin={(receiver, lookup) => apply(() => join(receiver, lookup))}
          onSwap={pickedEdge ? undefined : () => setPicked({ from: picked.to, to: picked.from })}
          onRemove={pickedEdge ? () => apply(() => removeEdge(pickedEdge)) : undefined} onClose={() => setPicked(null)} />}
      </aside>
    </div>
  )
}

function EdgePanel({ edge, probe, probing, root, nodeLabel, collections, locked, onLink, onJoin, onSwap, onRemove, onClose }: {
  edge: RelEdge | Pending; probe: RelEdge | null; probing: boolean; root: string; nodeLabel: (id: string) => string; collections: FieldSpec[]; locked: boolean
  onLink: (target: string) => void; onJoin: (receiver: End, lookup: End) => void; onSwap?: () => void; onRemove?: () => void; onClose: () => void
}) {
  const e = 'kind' in edge ? edge : null
  const [target, setTarget] = useState(e?.collection ?? '')
  const pct = (v?: number) => (v === undefined ? '–' : `${Math.round(v * 100)}%`)
  const fromRoot = edge.from.node === root
  // a main-table edge is either many rows per record (a collection) or one lookup row per record (a join);
  // from any other table, the referenced side (from) adds its columns to the referencing side (to)
  const [receiver, lookup] = fromRoot ? [edge.from, edge.to] : [edge.to, edge.from]
  const m = e ?? probe // the numbers: of the edge, or of the hand-drawn connection once measured
  const lookupFits = m?.to_unique === true
  const dead = m?.overlap === 0 // no shared key value: offer the actions, recommend none
  return (
    <>
      <div className="flex items-center gap-2">
        <Badge tone={e?.used ? 'signal' : 'neutral'}>{e ? (e.kind === 'suggested' ? 'found in the data' : e.kind) : 'new connection'}</Badge>
        <button className="btn btn-ghost btn-sm ml-auto" onClick={onClose}>✕</button>
      </div>
      <div className="mono text-[12px] leading-relaxed">{nodeLabel(edge.from.node)}.{edge.from.column}<br />↔ {nodeLabel(edge.to.node)}.{edge.to.column}</div>
      {m && !m.missing && (
        <table className="w-full tnum">
          <tbody>
            <tr><td className="text-muted py-0.5">Cardinality</td><td className="text-right">{m.cardinality}</td></tr>
            <tr><td className="text-muted py-0.5">Shared key values</td><td className="text-right">{fmtInt(m.overlap)}</td></tr>
            <tr><td className="text-muted py-0.5">{nodeLabel(edge.from.node)} keys found</td><td className="text-right">{pct(m.from_match)}</td></tr>
            <tr><td className="text-muted py-0.5">{nodeLabel(edge.to.node)} keys found</td><td className="text-right">{pct(m.to_match)}</td></tr>
            <tr><td className="text-muted py-0.5">{nodeLabel(edge.to.node)} rows matching nothing</td><td className={cn('text-right', (m.to_orphan_rows ?? 0) > 0 && 'text-warn')}>{fmtInt(m.to_orphan_rows)}</td></tr>
          </tbody>
        </table>
      )}
      {m?.warnings.map((w, i) => <div key={i} className="rounded-[var(--radius-md)] bg-warn-soft text-warn px-2.5 py-1.5">{w}</div>)}
      {e?.evidence?.map((x, i) => <div key={i} className="text-muted">• {x}</div>)}
      {!e && probing && <div className="text-muted flex items-center gap-2"><Spinner /> measuring on the rows…</div>}
      {!locked && (
        <div className="flex flex-col gap-2 pt-2 border-t border-line">
          {!e?.used && fromRoot && collections.length > 0 && (
            <div className="flex items-center gap-2">
              <select className="input flex-1" value={target} onChange={(ev) => setTarget(ev.target.value)}>
                <option value="">Many rows per record → which list?</option>
                {collections.map((c) => <option key={c.pointer} value={c.pointer}>{c.pointer}</option>)}
              </select>
              <button className={cn('btn btn-sm', !lookupFits && !dead && 'btn-primary')} disabled={!target} onClick={() => onLink(target)}><Link2 size={13} /> Link</button>
            </div>
          )}
          {!e?.used && (
            <button className={cn('btn btn-sm', (lookupFits || !fromRoot) && !dead && 'btn-primary')} onClick={() => onJoin(receiver, lookup)}>
              <Link2 size={13} /> Add {nodeLabel(lookup.node)} columns to {fromRoot ? 'each record' : nodeLabel(receiver.node)}
            </button>
          )}
          {onSwap && !fromRoot && <button className="btn btn-sm btn-ghost" onClick={onSwap}>⇄ Swap direction</button>}
          {onRemove && e?.used && <button className="btn btn-sm btn-ghost text-bad" onClick={onRemove}><Trash2 size={13} /> Remove this {e.kind}</button>}
          {!e?.used && <div className="text-muted text-[11.5px]">Then switch back to the fields and drag the table's columns onto target fields.</div>}
        </div>
      )}
    </>
  )
}
