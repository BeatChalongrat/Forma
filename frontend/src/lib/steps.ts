import type { ImportRound } from './api'

/** The five steps every delivery goes through, and where a round stands in them. */
export const ROUND_STEPS = [
  { n: 1, title: 'Files received', th: 'รับไฟล์และอ่านทุกชีต' },
  { n: 2, title: 'Mapping', th: 'คอลัมน์ไปลง field ไหน' },
  { n: 3, title: 'Create data', th: 'แปลงเป็น JSON ตาม format' },
  { n: 4, title: 'Check & approve data', th: 'ตรวจ แก้ แล้วอนุมัติ' },
  { n: 5, title: 'Export', th: 'ดาวน์โหลด JSON ส่งต่อ' },
] as const

export type NextStep = {
  /** the step the round is at (1–5); 6 = finished */
  step: number
  /** what a person does next, or null when the system is working / nothing is left */
  action: string | null
  th: string
  tone: 'todo' | 'problem' | 'working' | 'done'
}

export function nextStep(r: Pick<ImportRound, 'status'>): NextStep {
  switch (r.status) {
    case 'uploaded':
    case 'profiled':
      return { step: 1, action: null, th: 'ระบบกำลังอ่านไฟล์', tone: 'working' }
    case 'review':
      return { step: 2, action: 'Check the mapping', th: 'ตรวจ mapping แล้วกด Approve', tone: 'todo' }
    case 'blocked':
      return { step: 2, action: 'Check the file', th: 'ไฟล์ไม่เหมือน format ของลูกค้ารายนี้ ตรวจว่าส่งไฟล์ถูกไหม', tone: 'problem' }
    case 'rejected':
      return { step: 2, action: 'Fix the mapping', th: 'mapping ถูกปฏิเสธ แก้ใน Studio หรือเสนอใหม่', tone: 'problem' }
    case 'failed':
      return { step: 1, action: 'See what failed', th: 'มีขั้นตอนล้มเหลว ดูรายละเอียดแล้วลองใหม่', tone: 'problem' }
    case 'auto_approved':
    case 'approved':
      return { step: 3, action: 'Create the data', th: 'mapping ผ่านแล้ว กดสร้างข้อมูล', tone: 'todo' }
    case 'transformed':
      return { step: 4, action: 'Check & approve the data', th: 'ตรวจข้อมูล แก้ถ้าจำเป็น แล้วกด Approve', tone: 'todo' }
    case 'approved_data':
      return { step: 5, action: 'Export JSON', th: 'ข้อมูลอนุมัติแล้ว ดาวน์โหลดได้เลย', tone: 'todo' }
    case 'published':
      return { step: 6, action: null, th: 'ส่งออกแล้ว', tone: 'done' }
    default:
      return { step: 1, action: null, th: '', tone: 'working' }
  }
}
