import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api, type PilotObservations } from '../lib/api'
import { usePreferences } from '../lib/preferences'

export function PilotQuestionFeedback({ mappingId, questionId, canEdit, title }: { mappingId: string; questionId: string; canEdit: boolean; title?: string }) {
  const { t } = usePreferences()
  const query = useQuery({ queryKey: ['pilot-observations', mappingId], queryFn: () => api.pilotObservations(mappingId) })
  if (query.isError) return <p role="alert">{t('Could not load pilot notes.', 'โหลดบันทึกการทดลองไม่ได้')} <button className="btn btn-sm" onClick={() => void query.refetch()}>{t('Try again', 'ลองใหม่')}</button></p>
  if (!query.data) return <p role="status">{t('Loading pilot notes…', 'กำลังโหลดบันทึกการทดลอง…')}</p>
  return <FeedbackForm key={`${questionId}:${query.data.latest[questionId]?.id || ''}`} {...{ mappingId, questionId, canEdit, title }} data={query.data} />
}

function FeedbackForm({ mappingId, questionId, canEdit, title, data }: { mappingId: string; questionId: string; canEdit: boolean; title?: string; data: PilotObservations }) {
  const { t } = usePreferences(), qc = useQueryClient()
  const previous = data.latest[questionId]
  const [category, setCategory] = useState(previous?.category || 'not_assessed')
  const [note, setNote] = useState(previous?.note || '')
  const [location, setLocation] = useState(previous?.evidence_location || '')
  const save = useMutation({ mutationFn: () => api.savePilotObservation(mappingId, { question_id: questionId, category, note, evidence_location: location }), onSuccess: result => qc.setQueryData(['pilot-observations', mappingId], result) })
  const options = [
    ['not_assessed', t('Not assessed yet', 'ยังไม่ได้ตรวจสาเหตุ')],
    ['missing_information', t('The file lacks information we need', 'ไฟล์ไม่มีข้อมูลที่จำเป็น ต้องถามเจ้าของข้อมูล')],
    ['evidence_in_file', t('The file already contains the answer', 'มีหลักฐานตอบได้ในไฟล์ แต่ระบบยังถาม')],
    ['conflicting_evidence', t('The file contains conflicting evidence', 'ข้อมูลในไฟล์ขัดกัน ต้องให้คนตัดสินใจ')],
  ]
  const evidenceRequired = ['evidence_in_file', 'conflicting_evidence'].includes(category)
  return <section className="border border-line rounded-xl p-4 space-y-3 text-sm" aria-label={t('Pilot question feedback', 'บันทึกคุณภาพคำถาม')}>
    <h3 className="font-semibold">{t('Pilot: why did this question need a person?', 'บันทึกการทดลอง: ทำไมข้อนี้ต้องถามคน?')}</h3>
    {title && <p>{title}</p>}
    <p className="text-muted">{t('This note does not answer the question or teach the system.', 'บันทึกนี้แยกจากคำตอบ ไม่เปลี่ยนข้อมูลหรือสอนให้ระบบจำ')}</p>
    <fieldset disabled={!canEdit || save.isPending} className="space-y-3">
      <label className="block">{t('Reason', 'สาเหตุ')}<select className="input w-full mt-1" value={category} onChange={e => setCategory(e.target.value)}>{options.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label className="block">{t('What did you check / what is missing?', 'ตรวจอะไรแล้ว / ยังขาดข้อมูลอะไร')}<textarea className="input w-full mt-1" maxLength={2000} value={note} onChange={e => setNote(e.target.value)} /></label>
      {evidenceRequired && <label className="block">{t('File, sheet and cells / column', 'ไฟล์ ชีต และเซลล์หรือคอลัมน์ที่พบหลักฐาน')}<input className="input w-full mt-1" maxLength={1000} value={location} onChange={e => setLocation(e.target.value)} /></label>}
      <button type="button" className="btn btn-sm" disabled={(category !== 'not_assessed' && !note.trim()) || (evidenceRequired && !location.trim())} onClick={() => save.mutate()}>{save.isPending ? t('Saving…', 'กำลังบันทึก…') : t('Save observation', 'บันทึกสาเหตุ')}</button>
    </fieldset>
    {save.isError && <p role="alert" className="text-bad">{save.error.message}</p>}
    {previous && <p role="status" className="text-muted">{t('Saved by', 'บันทึกแล้วโดย')} {previous.actor} · {new Date(previous.created_at).toLocaleString()}</p>}
    {data.history.filter(x => x.question_id === questionId).length > 1 && <ul className="text-xs text-muted space-y-1" aria-label={t('Observation history', 'ประวัติการแก้สาเหตุ')}>{data.history.filter(x => x.question_id === questionId).slice(0, 5).map(x => <li key={x.id}>{options.find(o => o[0] === x.category)?.[1]} · {x.note} · {x.actor}</li>)}</ul>}
  </section>
}
