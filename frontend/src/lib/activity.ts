export type Receipt = { id: number; job_id: string; attempt: number; span_id: string; parent_id: string | null; role: string; action: string; state: string; at: string; duration_ms: number | null; detail: Record<string, unknown> | null }
export type ActivityJob = { id: string; status: string; kind: string; attempts: number; files: string[]; round_id: string | null; batch_id: string | null; round_status: string | null; latest: Receipt | null; lease_expires_at: string | null; message: string | null; progress: number; error: string | null; result: Record<string, unknown> | null; format_memory: { status?: string; reused_from?: string } | null }
export type ActivityList = { items: ActivityJob[]; total: number; counts: Record<string, number>; next_offset: number | null; journal_available: boolean }
export type ActivityDetail = { job: ActivityJob; attempt: number; attempts: number[]; events: Receipt[]; previous_cursor: number | null; mapping: { id: string; document: unknown; agent_log: unknown; questions: unknown; validation: unknown; policy: unknown; status: string } | null }
export type Station = 'system' | 'analyst' | 'mapper' | 'reviewer' | 'human' | 'ready'
export function station(job: ActivityJob, now = Date.now()): Station {
  if (job.status === 'running' && job.lease_expires_at && Date.parse(job.lease_expires_at) > now) {
    const role = job.latest?.attempt === job.attempts ? job.latest.role : 'system'
    return role === 'execute' ? 'reviewer' : ['analyst', 'mapper', 'reviewer'].includes(role || '') ? role as Station : 'system'
  }
  if (job.status !== 'done') return 'system'
  if (job.round_status === 'published') return 'ready'
  if (['review', 'blocked', 'proposed'].includes(job.round_status || '') || job.result?.state === 'needs_customer') return 'human'
  return 'system'
}
export function isLive(job: ActivityJob, now = Date.now()) {
  return job.status === 'running' && !!job.lease_expires_at && Date.parse(job.lease_expires_at) > now
}
export const actions: Record<string, [string, string]> = {
  'agent.analyst': ['สำรวจไฟล์และหลักฐาน', 'Evidence analyst'], 'agent.mapper': ['สร้างการจับคู่ข้อมูล', 'Mapping agent'],
  'agent.reviewer': ['ตรวจความหมายกับต้นทาง', 'Independent semantic review'], 'agent.execute': ['ตรวจผลแปลงทั้งไฟล์', 'Full execution'],
  inspect: ['เปิดอ่านข้อมูลต้นทาง', 'Source inspection'], verify: ['ทดลองแปลงและตรวจข้อมูล', 'Deterministic validation'],
  'sandbox.python': ['รัน Python ในพื้นที่ทดสอบ', 'Isolated sandbox'], 'model.request': ['ขอคำตอบจากโมเดล', 'Structured model request'],
  'format.reused': ['ใช้การจับคู่ที่เคยอนุมัติ', 'Known format · ยังต้องตรวจผล'],
  'stage.identify': ['ตรวจรูปแบบและเจ้าของไฟล์', 'Format matching'], 'stage.profile': ['อ่านชีตและโครงสร้างไฟล์', 'File profiling'],
  'stage.propose': ['เตรียมการจับคู่และตรวจผล', 'Mapping pipeline'], 'stage.transform': ['แปลงเป็นข้อมูลปลายทาง', 'Transform engine'],
  'stage.publish': ['ตรวจเงื่อนไขและสร้างไฟล์ส่งออก', 'Publication policy'], job: ['ประมวลผลงาน', 'Job lifecycle'], progress: ['อัปเดตการทำงาน', 'Worker progress'],
}
export function activityLabel(event?: Pick<Receipt, 'action'> & { detail?: Receipt['detail'] } | null) {
  if (!event) return 'ยังไม่มีบันทึกกิจกรรม'
  if (event.action === 'progress' && typeof event.detail?.message === 'string') return event.detail.message
  return actions[event.action]?.[0] || event.action
}
