import type { AnswerPreview, ReviewProgress, ReviewQuestions } from './api'

export function reviewFlow(q: ReviewQuestions, answers: Record<string, boolean | number | string> = {}, acknowledged: string[] = [], preview: AnswerPreview | null = null) {
  const ids = new Set(q.questions.map(x => x.id))
  const drafts = Object.entries(answers).filter(([id, value]) => ids.has(id) && (typeof value !== 'string' || !!value.trim()) && (typeof value !== 'number' || Number.isFinite(value))).length
  const notes = [...new Set(q.open_questions.filter(note => !q.questions.some(x => x.question === note)))]
  const acknowledgedCount = notes.filter(note => acknowledged.includes(note)).length
  const verification = q.checks.verification as { complete?: boolean; passed?: boolean } | null | undefined
  const blocked = q.blocked || !!verification && (!verification.complete || !verification.passed)
  const pending = q.questions.length + notes.length
  const remaining = pending - drafts - acknowledgedCount
  return {
    drafts, notes, pending, remaining, blocked, acknowledgedCount,
    saved: q.progress?.saved_answers || 0,
    hasDraft: drafts + acknowledgedCount > 0,
    stage: pending === 0 ? 3 : preview || remaining === 0 ? 2 : 1,
    defaultSection: q.status !== 'proposed' ? 'finish' : blocked ? 'issues' : q.questions[0]?.id || 'finish',
    canApprove: q.status === 'proposed' && !blocked && pending === 0,
  }
}

export function progressLabel(p: ReviewProgress, t: (en: string, th: string) => string) {
  if (p.state === 'approved') return t('Approved', 'อนุมัติแล้ว')
  if (p.state === 'rejected') return t('Rejected', 'ไม่อนุมัติ')
  if (p.state === 'superseded') return t('Replaced by a newer version', 'มีเวอร์ชันใหม่แล้ว')
  if (p.state === 'blocked') return t('Check the source file', 'ต้องแก้ปัญหาก่อน')
  if (p.remaining_questions) return t(`${p.remaining_questions} questions left`, `เหลือคำถาม ${p.remaining_questions} ข้อ`)
  if (p.remaining_notes) return t(`${p.remaining_notes} notes to check`, `เหลือเรื่องต้องตรวจ ${p.remaining_notes} ข้อ`)
  return t('Ready for final review', 'รอตรวจผลและอนุมัติ')
}
