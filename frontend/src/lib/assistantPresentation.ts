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
  workspace_summary: ['Workspace overview', 'ภาพรวมระบบ'], review_queue: ['Review queue', 'งานที่รอตรวจ'],
  runtime_configuration: ['System settings', 'การตั้งค่าระบบ'], run_python: ['Calculation results', 'ผลการคำนวณ'],
  build_report: ['Report data', 'ข้อมูลรายงาน'], search_chats: ['Past conversations', 'บทสนทนาก่อนหน้า'],
  recent_chats: ['Recent conversations', 'บทสนทนาล่าสุด'],
}
export function sourceName(tool: string, language: 'th' | 'en') {
  return sourceNames[tool]?.[language === 'th' ? 1 : 0] ?? tool.replaceAll('_', ' ')
}
