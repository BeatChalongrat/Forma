export type InspectionScope = 'round' | 'item' | 'chat'
export type InspectionItem = { scope: InspectionScope; id: string; title: string; status: string; at: string; customer: string | null; files: string[] }
export type InspectionEvent = { key: string; kind: string; id: string; at: string; title: string; actor: string; action?: string; state?: string; attempt?: number; job_id?: string }
export type InspectionTimeline = { scope: InspectionScope; id: string; title: string; status: string; events: InspectionEvent[]; next_cursor: string | null; journal_available: boolean; notice: string }
export type InspectionDetail = { key: string; title: string; kind: string; data: Record<string, unknown>; links: { label: string; url: string }[]; notice: string }
export function inspectionHref(scope: InspectionScope, id: string, event = '') {
  const p = new URLSearchParams({ scope, id, kind: scope === 'chat' ? 'assistant' : 'imports' })
  if (event) p.set('event', event)
  return '/inspection?' + p
}
/** URL owns navigation so back/forward restores list filters, the work, and the selected receipt. */
export function inspectionParams(current: URLSearchParams, changes: Record<string, string>) {
  const next = new URLSearchParams(current)
  for (const [key, value] of Object.entries(changes)) {
    if (value) next.set(key, value)
    else next.delete(key)
  }
  return next
}
export function inspectionFilters(filters: Record<string, string>) {
  return new URLSearchParams(Object.entries(filters).filter(([, value]) => value !== ''))
}
export const inspectionKinds: Record<string, string> = { round: 'รับชุดไฟล์', file: 'ไฟล์ต้นทาง', item: 'ไฟล์ในรอบงาน', mapping: 'การจับคู่ข้อมูล', review: 'คำถามและการตรวจ', dataset: 'ผลลัพธ์ข้อมูล', change: 'การแก้ไขข้อมูล', export: 'ไฟล์ดาวน์โหลด', knowledge: 'ความรู้และคำตอบ', llm: 'การใช้โมเดล', job: 'งานประมวลผล', activity: 'กิจกรรมและเครื่องมือ', audit: 'การตัดสินใจและการเปลี่ยนแปลง', turn: 'บทสนทนา', artifact: 'เวอร์ชันผลงาน', chat: 'แชต' }
export const inspectionStatuses: Record<string, string> = { uploaded: 'รับไฟล์แล้ว', profiled: 'อ่านไฟล์แล้ว', proposed: 'เสนอการจับคู่แล้ว', review: 'รอตรวจ', auto_approved: 'ผ่านกฎอัตโนมัติ', transformed: 'สร้างข้อมูลแล้ว', published: 'เผยแพร่แล้ว', approved: 'อนุมัติแล้ว', rejected: 'ไม่อนุมัติ', blocked: 'ต้องแก้ปัญหา', failed: 'ไม่สำเร็จ', cancelled: 'ยกเลิก', moved: 'ย้ายลูกค้าแล้ว', superseded: 'มีเวอร์ชันใหม่', received: 'รับไฟล์แล้ว', needs_customer: 'รอเลือกลูกค้า', conversation: 'บทสนทนา', queued: 'รอคิว', running: 'กำลังทำ', done: 'จบงานประมวลผล', started: 'เริ่ม', completed: 'เสร็จ', info: 'บันทึก', waiting: 'รอคำตอบ', open: 'รอตรวจ' }
