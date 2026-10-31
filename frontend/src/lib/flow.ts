import dagre from '@dagrejs/dagre'
import type { FlowContent, FlowNode } from './api'

const NODE_WIDTH = 232, PADDING = 12, LABEL_LINE = 20, DETAIL_LINE = 17, EDGE_LINE = 15
const LABEL_FONT = '600 14px "Noto Sans Thai Variable", "Montserrat Variable", sans-serif'
const DETAIL_FONT = '400 12.5px "Noto Sans Thai Variable", "Montserrat Variable", sans-serif'
const EDGE_FONT = '400 12px "Noto Sans Thai Variable", "Montserrat Variable", sans-serif'

export type PlacedNode = FlowNode & { x: number; y: number; width: number; height: number; labelLines: string[]; detailLines: string[] }
export type PlacedEdge = { key: string; path: string; label?: string; back: boolean; x: number; y: number }
export type FlowLayout = { width: number; height: number; nodes: PlacedNode[]; edges: PlacedEdge[] }

const segmenter = new Intl.Segmenter('th', { granularity: 'word' })
const measure = document.createElement('canvas').getContext('2d')

/** Thai has no spaces between words, so lines break where the browser's word segmenter says they may. */
function wrap(text: string, font: string, width: number, maxLines: number): string[] {
  if (!measure) return [text]
  measure.font = font
  const lines: string[] = []
  let line = ''
  for (const { segment } of segmenter.segment(text)) {
    if (line && measure.measureText(line + segment).width > width) { lines.push(line.trimEnd()); line = segment.trimStart() }
    else line += segment
  }
  if (line) lines.push(line.trimEnd())
  return lines.length > maxLines ? [...lines.slice(0, maxLines - 1), lines.slice(maxLines - 1).join('')] : lines
}

function textWidth(text: string): number {
  if (!measure) return text.length * 7
  measure.font = EDGE_FONT
  return measure.measureText(text).width
}

export function layoutFlow(flow: FlowContent): FlowLayout {
  const graph = new dagre.graphlib.Graph({ multigraph: true })
  graph.setGraph({ rankdir: flow.direction === 'right' ? 'LR' : 'TB', nodesep: 44, ranksep: 64, edgesep: 24, marginx: 20, marginy: 20 })
  graph.setDefaultEdgeLabel(() => ({}))
  const lines = new Map(flow.nodes.map(node => [node.id, {
    labelLines: wrap(node.label, LABEL_FONT, NODE_WIDTH - PADDING * 2, 2),
    detailLines: node.detail ? wrap(node.detail, DETAIL_FONT, NODE_WIDTH - PADDING * 2, 3) : [],
  }]))
  for (const node of flow.nodes) {
    const text = lines.get(node.id)!
    graph.setNode(node.id, { width: NODE_WIDTH, height: PADDING * 2 + text.labelLines.length * LABEL_LINE + text.detailLines.length * DETAIL_LINE })
  }
  flow.edges.forEach((edge, i) => graph.setEdge(
    { v: edge.from, w: edge.to, name: String(i) },
    { width: edge.label ? textWidth(edge.label) + 12 : 0, height: edge.label ? EDGE_LINE + 6 : 0, labelpos: 'c' },
  ))
  dagre.layout(graph)
  const nodes = flow.nodes.map(node => {
    const box = graph.node(node.id)
    return { ...node, ...lines.get(node.id)!, x: box.x - box.width / 2, y: box.y - box.height / 2, width: box.width, height: box.height }
  })
  const edges = flow.edges.map((edge, i) => {
    const line = graph.edge({ v: edge.from, w: edge.to, name: String(i) })
    const points: { x: number; y: number }[] = line.points ?? []
    return { key: String(i), path: points.map((point, n) => `${n ? 'L' : 'M'}${point.x.toFixed(1)} ${point.y.toFixed(1)}`).join(' '), label: edge.label, back: !!edge.back, x: line.x ?? 0, y: line.y ?? 0 }
  })
  const size = graph.graph()
  return { width: Math.ceil(size.width ?? 0), height: Math.ceil(size.height ?? 0), nodes, edges }
}

const COLOURS: Record<string, string> = {
  '--flow-node': '#ffffff', '--flow-border': '#cfd7e3', '--flow-label': '#203e71', '--flow-detail': '#46506a', '--flow-line': '#7b8aa3',
  '--flow-question': '#fdf3e1', '--flow-problem': '#fdecec', '--flow-done': '#edf7e5', '--flow-new': '#35652b', '--flow-picked': '#cfd7e3', '--flow-picked-fill': '#ffffff',
}

/** The picture as a file: real colours, and fonts a machine without this app's web fonts still has. */
export function flowToSvg(svg: SVGSVGElement, layout: FlowLayout): string {
  const copy = svg.cloneNode(true) as SVGSVGElement
  copy.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  copy.setAttribute('width', String(layout.width))
  copy.setAttribute('height', String(layout.height))
  const ground = document.createElementNS('http://www.w3.org/2000/svg', 'rect')
  ground.setAttribute('width', '100%'); ground.setAttribute('height', '100%'); ground.setAttribute('fill', '#ffffff')
  copy.prepend(ground)
  let text = new XMLSerializer().serializeToString(copy)
  for (const [name, value] of Object.entries(COLOURS)) text = text.replaceAll(`var(${name})`, value)
  return text.replaceAll('var(--font-sans)', '&quot;Noto Sans Thai&quot;, &quot;Leelawadee UI&quot;, Tahoma, sans-serif')
}

export function flowToPng(svg: string, layout: FlowLayout, scale = 2): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }))
    const image = new Image()
    image.onload = () => {
      const canvas = document.createElement('canvas')
      canvas.width = layout.width * scale; canvas.height = layout.height * scale
      const context = canvas.getContext('2d')
      if (!context) { URL.revokeObjectURL(url); reject(new Error('no canvas')); return }
      context.drawImage(image, 0, 0, canvas.width, canvas.height)
      URL.revokeObjectURL(url)
      canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('no image')), 'image/png')
    }
    image.onerror = () => { URL.revokeObjectURL(url); reject(new Error('no image')) }
    image.src = url
  })
}

/** What this version did to the one before it, in the terms a reader cares about. */
export function flowChanges(before: FlowContent | null | undefined, after: FlowContent) {
  if (!before) return { added: [] as string[], addedCount: 0, removedCount: 0, changedCount: 0 }
  const old = new Map(before.nodes.map(node => [node.id, node]))
  const added = after.nodes.filter(node => !old.has(node.id)).map(node => node.id)
  const changed = after.nodes.filter(node => { const was = old.get(node.id); return was && (was.label !== node.label || (was.detail ?? '') !== (node.detail ?? '')) }).map(node => node.id)
  return { added: [...added, ...changed], addedCount: added.length, removedCount: before.nodes.filter(node => !after.nodes.some(next => next.id === node.id)).length, changedCount: changed.length }
}
