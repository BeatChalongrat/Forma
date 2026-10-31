import { useId, useLayoutEffect, useMemo, useRef, useState, type RefObject } from 'react'
import { Maximize, Minus, Plus } from 'lucide-react'
import type { FlowContent } from '../lib/api'
import { layoutFlow, type FlowLayout, type PlacedNode } from '../lib/flow'
import { usePreferences } from '../lib/preferences'

const FILL: Record<string, string> = { question: 'var(--flow-question)', problem: 'var(--flow-problem)', done: 'var(--flow-done)' }

function FlowBox({ node, picked, fresh, onPick }: { node: PlacedNode; picked: boolean; fresh: boolean; onPick: () => void }) {
  const fill = picked ? 'var(--flow-picked-fill)' : FILL[node.tone ?? ''] ?? 'var(--flow-node)'
  const border = picked ? 'var(--flow-picked)' : fresh ? 'var(--flow-new)' : 'var(--flow-border)'
  return <g role="button" tabIndex={0} aria-pressed={picked} aria-label={node.detail ? `${node.label}: ${node.detail}` : node.label} className="flow-box" transform={`translate(${node.x} ${node.y})`} onClick={onPick} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onPick() } }}>
    <rect width={node.width} height={node.height} rx={12} style={{ fill, stroke: border, strokeWidth: picked || fresh ? 2 : 1.5 }} />
    {node.labelLines.map((line, i) => <text key={i} x={12} y={28 + i * 20} style={{ fill: 'var(--flow-label)', fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 600 }}>{line}</text>)}
    {node.detailLines.map((line, i) => <text key={i} x={12} y={26 + node.labelLines.length * 20 + i * 17} style={{ fill: 'var(--flow-detail)', fontFamily: 'var(--font-sans)', fontSize: 12.5 }}>{line}</text>)}
  </g>
}

export function FlowCanvas({ flow, title, picked, fresh, onPick, svgRef, layoutRef }: { flow: FlowContent; title: string; picked: string; fresh: string[]; onPick: (id: string, label: string) => void; svgRef: RefObject<SVGSVGElement | null>; layoutRef: RefObject<FlowLayout | null> }) {
  const { t } = usePreferences()
  const layout = useMemo(() => layoutFlow(flow), [flow])
  const box = useRef<HTMLDivElement>(null), [zoom, setZoom] = useState(1), arrow = useId().replaceAll(':', '')
  const fit = () => { if (box.current) setZoom(Math.max(0.4, Math.min(1, (box.current.clientWidth - 8) / layout.width))) }
  useLayoutEffect(() => { layoutRef.current = layout; fit() }, [layout]) // eslint-disable-line react-hooks/exhaustive-deps
  return <div className="flow-canvas">
    <div ref={box} className="flow-scroll">
      <svg ref={svgRef} viewBox={`0 0 ${layout.width} ${layout.height}`} width={layout.width * zoom} height={layout.height * zoom} role="group" aria-label={title}>
        <defs><marker id={arrow} orient="auto" markerWidth="9" markerHeight="9" refX="7" refY="4"><path d="M0 0 L8 4 L0 8 Z" style={{ fill: 'var(--flow-line)' }} /></marker></defs>
        {layout.edges.map(edge => <path key={edge.key} d={edge.path} markerEnd={`url(#${arrow})`} style={{ fill: 'none', stroke: 'var(--flow-line)', strokeWidth: 1.6, strokeLinejoin: 'round', strokeDasharray: edge.back ? '6 5' : undefined }} />)}
        {layout.edges.filter(edge => edge.label).map(edge => <text key={edge.key} x={edge.x} y={edge.y + 4} textAnchor="middle" style={{ fill: 'var(--flow-detail)', fontFamily: 'var(--font-sans)', fontSize: 12 }}>{edge.label}</text>)}
        {layout.nodes.map(node => <FlowBox key={node.id} node={node} picked={picked === node.id} fresh={fresh.includes(node.id)} onPick={() => onPick(node.id, node.label)} />)}
      </svg>
    </div>
    <div className="flow-zoom">
      <button type="button" className="btn btn-ghost btn-sm" aria-label={t('Zoom out', 'ย่อ')} onClick={() => setZoom(z => Math.max(0.4, +(z - 0.1).toFixed(1)))}><Minus size={15} /></button>
      <span className="text-xs text-muted tnum w-10 text-center">{Math.round(zoom * 100)}%</span>
      <button type="button" className="btn btn-ghost btn-sm" aria-label={t('Zoom in', 'ขยาย')} onClick={() => setZoom(z => Math.min(1.6, +(z + 0.1).toFixed(1)))}><Plus size={15} /></button>
      <button type="button" className="btn btn-ghost btn-sm" aria-label={t('Fit to the panel', 'พอดีแผง')} onClick={fit}><Maximize size={15} /></button>
    </div>
  </div>
}
