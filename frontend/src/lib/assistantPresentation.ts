import type { WorkspaceAnswer } from './api'

/** Decide what is a deliverable, without deleting the underlying attempts or evidence. */
export function assistantPresentation(reply: WorkspaceAnswer) {
  const latest = new Map<string, NonNullable<WorkspaceAnswer['artifacts']>[number]>()
  for (const artifact of reply.artifacts ?? []) {
    if (!latest.has(artifact.id) || artifact.version > latest.get(artifact.id)!.version) latest.set(artifact.id, artifact)
  }
  const artifacts = [...latest.values()]
  const represented = new Set(artifacts.flatMap(a => a.file ? [a.file.id] : []))
  // A retry of the same output filename replaces its earlier attempt only within this answer.
  // Different filenames stay separate; never infer equivalence from titles or chart appearance.
  const finalByName = new Map<string, NonNullable<WorkspaceAnswer['code_runs']>[number]['files'][number]>()
  for (const run of reply.code_runs ?? []) {
    if (run.status === 'ok') for (const file of run.files) if (file.kind === 'output') finalByName.set(file.name, file)
  }
  const files = [...new Map([...finalByName.values()].filter(f => !represented.has(f.id)).map(f => [f.id, f])).values()]
  const reports = (reply.visualizations ?? []).filter((report, i, all) => all.findIndex(r => r.id === report.id) === i)
  const links = reply.evidence.flatMap(e => e.links ?? []).filter((l, i, all) => safeSourceLink(l.url) && all.findIndex(x => x.url === l.url) === i)
  const runs = reply.code_runs ?? []
  return { artifacts, files, reports, links, lastRunFailed: !!runs.length && runs.at(-1)?.status !== 'ok' }
}

export function safeSourceLink(url: string) {
  return url.startsWith('/') && !url.startsWith('//') && !url.includes('\\') && ![...url].some(c => c.charCodeAt(0) <= 32)
}

const sourceNames: Record<string, [string, string]> = {
  review_summary: ['Full review overview', 'ภาพรวมงานตรวจทั้งหมด'],
  operations_summary: ['Full processing overview', 'ภาพรวมการประมวลผลทั้งหมด'],
  read_pages: ['Details across pages', 'รายละเอียดจากหลายหน้า'],
  workspace_summary: ['Workspace overview', 'ภาพรวมระบบ'], review_queue: ['Review queue', 'งานที่รอตรวจ'],
  runtime_configuration: ['System settings', 'การตั้งค่าระบบ'], run_python: ['Calculation results', 'ผลการคำนวณ'],
  build_report: ['Report data', 'ข้อมูลรายงาน'], search_chats: ['Past conversations', 'บทสนทนาก่อนหน้า'],
  recent_chats: ['Recent conversations', 'บทสนทนาล่าสุด'],
}
export function sourceName(tool: string, language: 'th' | 'en') {
  return sourceNames[tool]?.[language === 'th' ? 1 : 0] ?? tool.replaceAll('_', ' ')
}

/** Coverage belongs to one source and query, never to the whole conversation. */
export function coverageLabel(data: unknown, language: 'th' | 'en'): string {
  if (!data || typeof data !== 'object') return ''
  const d = data as Record<string, unknown>
  const report = d.report as Record<string, unknown> | undefined
  const overview = d.overview as { total?: number; coverage?: { complete?: boolean } } | undefined
  const c = (d.coverage ?? report?.coverage) as { matched?: number; scanned?: number; returned?: number; complete?: boolean } | undefined
  const parts: string[] = []
  const th = language === 'th'
  if (overview?.coverage?.complete && typeof overview.total === 'number') parts.push(th ? `ภาพรวมนับครบ ${overview.total} งานตรวจ` : `Overview counts all ${overview.total} review tasks`)
  if (c && typeof c.matched === 'number') {
    const count = c.scanned ?? c.returned
    if (typeof count === 'number') parts.push(th ? `${c.scanned !== undefined ? 'นับ' : 'ดึงรายละเอียด'} ${count} จาก ${c.matched} รายการในขอบเขตนี้${c.complete ? ' · ครบ' : ' · ยังไม่ครบ'}` : `${count} of ${c.matched} scoped records · ${c.complete ? 'complete' : 'partial'}`)
  }
  if (d.truncated) parts.push(th ? 'รายละเอียดที่ส่งให้ผู้ช่วยถูกย่อ ไม่ใช่ข้อมูลทั้งหมด' : 'Details sent to the assistant were shortened')
  return parts.join(' · ')
}
