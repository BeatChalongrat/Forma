import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type AnswerPreview, type ReviewQuestions } from '../lib/api'
import { usePreferences } from '../lib/preferences'
import { answerLabel, answerScope } from '../lib/reviewLabels'
import { Spinner } from './ui'
import { fmtDate } from '../lib/utils'

type Answer = boolean | number | string
type Question = ReviewQuestions['questions'][number]

export function AnswerInput({ question: q, value, onChange, disabled }: { question: Question; value: Answer | undefined; onChange: (v: Answer | undefined) => void; disabled?: boolean }) {
  const { t } = usePreferences()
  const choices = q.kind === 'boolean' ? [true, false] : q.kind === 'confirm' ? ['confirm'] : q.kind === 'choice' || q.kind === 'enum' && !q.allow_custom ? q.options || [] : []
  return <fieldset disabled={disabled} className="space-y-3">
    {choices.length ? <div className="flex flex-wrap gap-2">{choices.map(option => <button type="button" key={String(option)} className={`btn ${value === option ? 'btn-primary' : ''}`} aria-pressed={value === option} onClick={() => onChange(option)}>{answerLabel(q, option, t)}</button>)}</div>
      : <label className="grid gap-2 text-sm">{t('Correct answer', 'คำตอบที่ถูกต้อง')}<input className="input" type={q.kind === 'number' ? 'number' : 'text'} step={q.field.json_type.includes('integer') ? '1' : 'any'} maxLength={5000} value={value === undefined ? '' : String(value)} onChange={e => onChange(e.target.value === '' ? undefined : q.kind === 'number' ? Number(e.target.value) : e.target.value)} /></label>}
    <button type="button" className="btn btn-ghost" onClick={() => onChange(undefined)}>{t('Not sure — leave unanswered', 'ยังไม่ทราบ — เว้นคำตอบไว้')}</button>
  </fieldset>
}

export function KnowledgeRevision({ id, active }: { id: string; active: boolean }) {
  const { t } = usePreferences(), qc = useQueryClient()
  const [editing, setEditing] = useState(false), [answer, setAnswer] = useState<Answer>(), [reason, setReason] = useState(''), [preview, setPreview] = useState<AnswerPreview | null>(null)
  const [saved, setSaved] = useState('')
  const form = useQuery({ queryKey: ['knowledge-form', id], queryFn: () => api.knowledgeForm(id), enabled: editing })
  const previewing = useMutation({ mutationFn: () => api.previewKnowledge(id, { answer: answer!, reason }), onSuccess: setPreview })
  const saving = useMutation({ mutationFn: () => api.reviseKnowledge(id, { answer: answer!, reason, digest: preview!.digest }), onSuccess: m => { setSaved(m.id); setPreview(null); void qc.invalidateQueries({ queryKey: ['learning'] }); void qc.invalidateQueries({ queryKey: ['knowledge'] }); void qc.invalidateQueries({ queryKey: ['knowledge-impacts', id] }) } })
  const error = form.error || previewing.error || saving.error
  return <section className="py-6 space-y-4">
    <h3 className="font-semibold">{t('Correct a remembered answer', 'แก้คำตอบที่จำไว้')}</h3>
    {!editing && active && <button className="btn" onClick={() => setEditing(true)}>{t('Edit answer', 'แก้คำตอบ')}</button>}
    {!editing && !active && <p className="text-sm text-muted">{t('This answer is retired. Open its correction history below to view the replacement.', 'คำตอบนี้เลิกใช้แล้ว ดูคำตอบใหม่ได้จากประวัติการแก้ไขด้านล่าง')}</p>}
    {editing && !saved && (form.isPending ? <Spinner /> : form.data && <>
      <p className="text-sm">{form.data.question.question_th || form.data.question.question}</p>
      <p className="text-sm text-muted">{t('Previous answer', 'คำตอบเดิม')}: <strong className="text-ink">{answerLabel(form.data.question, form.data.answer, t)}</strong></p>
      <AnswerInput question={form.data.question} value={answer} onChange={v => { setAnswer(v); setPreview(null) }} disabled={!form.data.active || saving.isPending || previewing.isPending} />
      {!form.data.active && <p role="status">{t('This answer has already been retired. Open its latest mapping to review the replacement.', 'คำตอบนี้เลิกใช้แล้ว เปิดการจับคู่ล่าสุดเพื่อตรวจคำตอบใหม่')}</p>}
      <label className="grid gap-2 text-sm">{t('Reason for correction', 'เหตุผลที่แก้คำตอบ')}<textarea className="input" disabled={saving.isPending || previewing.isPending} maxLength={500} value={reason} onChange={e => { setReason(e.target.value); setPreview(null) }} /></label>
      <p className="text-sm text-muted">{answerScope(form.data.question, true, t)}</p>
      <p className="text-sm text-muted">{t('Saving stops reuse of the old answer immediately. The new answer needs approval. Existing JSON and downloads remain in history.', 'เมื่อบันทึกจะหยุดใช้คำตอบเก่าทันที คำตอบใหม่ต้องผ่านการอนุมัติ ส่วน JSON และไฟล์ดาวน์โหลดเดิมยังอยู่ในประวัติ')}</p>
      <button className="btn" disabled={answer === undefined || !reason.trim() || !form.data.active || previewing.isPending || saving.isPending} onClick={() => previewing.mutate()}>{previewing.isPending ? t('Checking all rows…', 'กำลังตรวจทุกแถว…') : t('Preview changes', 'ดูผลก่อนบันทึก')}</button>
      {preview && <div className="space-y-3 border-t border-line pt-4">
        <p>{answerLabel(form.data.question, form.data.answer, t)} → <strong>{answerLabel(form.data.question, answer, t)}</strong></p>
        <p className="text-sm">{t('Records before / after', 'จำนวนรายการก่อน / หลัง')}: {preview.comparison.before_records} / {preview.comparison.after_records} · {t('Changed', 'เปลี่ยนแปลง')}: {preview.comparison.counts.changed_records}</p>
        <p className="text-sm">{preview.comparison.validation?.passed ? t('All records checked; no new errors or unrelated field changes.', 'ตรวจครบทุกแถว ไม่พบข้อผิดพลาดใหม่หรือการเปลี่ยนฟิลด์อื่น') : preview.comparison.validation?.error || t('Verification failed', 'ยังไม่ผ่านการตรวจ')}</p>
        {preview.comparison.rows.length > 0 && <div className="overflow-auto"><table className="table"><thead><tr><th>{t('Record', 'รายการ')}</th><th>{t('Before', 'ก่อน')}</th><th>{t('After', 'หลัง')}</th></tr></thead><tbody>{preview.comparison.rows.slice(0, 5).flatMap(row => (row.changes || []).map((change, i) => <tr key={row.identity + i}><td>{row.identity}<small className="block text-muted">{change.pointer}</small></td><td>{answerLabel(form.data.question, change.before, t)}</td><td>{answerLabel(form.data.question, change.after, t)}</td></tr>))}</tbody></table><p className="text-xs text-muted">{t('Up to 5 example records; counts above cover the entire file.', 'แสดงตัวอย่างไม่เกิน 5 รายการ ตัวเลขด้านบนตรวจจากทั้งไฟล์')}</p></div>}
        <button className="btn btn-primary" disabled={saving.isPending || !preview.preview.ok || !preview.comparison.reliable || !preview.comparison.validation?.passed} onClick={() => saving.mutate()}>{t('Save revision for approval', 'บันทึกคำตอบใหม่เพื่อรออนุมัติ')}</button>
      </div>}
    </>)}
    {saved && <div className="space-y-3"><p role="status">{t('Revision saved. The previous answer will no longer be reused.', 'บันทึกเวอร์ชันใหม่แล้ว ระบบจะไม่ใช้คำตอบเดิมซ้ำ')}</p><Link className="btn btn-primary" to={`/review?mapping=${saved}`}>{t('Review and approve the new answer', 'ตรวจและอนุมัติคำตอบใหม่')}</Link></div>}
    {error && <p role="alert" className="text-bad text-sm">{error.message}</p>}
  </section>
}

export function KnowledgeImpacts({ id, retired, canEdit }: { id: string; retired: boolean; canEdit: boolean }) {
  const { t } = usePreferences(), qc = useQueryClient()
  const [offset, setOffset] = useState(0), [selected, setSelected] = useState<Record<string, string>>({})
  const q = useQuery({ queryKey: ['knowledge-impacts', id, offset], queryFn: () => api.knowledgeImpacts(id, offset) })
  const sending = useMutation({ mutationFn: () => api.reviewKnowledgeImpacts(id, selected), onSuccess: () => { setSelected({}); void qc.invalidateQueries({ queryKey: ['knowledge-impacts', id] }); void qc.invalidateQueries({ queryKey: ['review'] }) } })
  return <section className="py-6 space-y-4"><h3 className="font-semibold">{t('Files that used this answer', 'ไฟล์ที่เคยใช้คำตอบนี้')}</h3>
    <p className="text-sm text-muted">{t('Includes indirect reuse in later rounds. Select files to review again after correcting or retiring the answer. This does not replace published results.', 'รวมการนำไปใช้ต่อในรอบหลัง เลือกส่งกลับมาตรวจได้เมื่อแก้หรือเลิกใช้คำตอบแล้ว ผลลัพธ์ที่เผยแพร่เดิมจะไม่ถูกเขียนทับ')}</p>
    {q.isPending ? <Spinner /> : q.isError ? <p role="alert">{q.error.message}</p> : <>
      <p className="text-sm">{q.data.total} {t('deliveries', 'ชุดไฟล์')}</p>
      <div className="overflow-auto"><table className="table"><thead><tr><th /><th>{t('Files / delivery', 'ไฟล์ / รอบ')}</th><th>{t('Status', 'สถานะ')}</th><th /></tr></thead><tbody>{q.data.items.map(item => <tr key={item.round_id}><td><input type="checkbox" aria-label={t('Review', 'ตรวจอีกครั้ง') + ' ' + (item.label || item.round_id)} disabled={!canEdit || !retired || !item.can_review || sending.isPending} checked={!!selected[item.round_id]} onChange={e => setSelected(old => { const next = { ...old }; if (e.target.checked && item.mapping_id) next[item.round_id] = item.mapping_id; else delete next[item.round_id]; return next })} /></td><td><p>{item.label || item.round_id}</p><p className="text-xs text-muted">{item.files.map(f => f.name).join(', ')}</p></td><td className="text-sm">{item.historical_only ? t('Historical version only', 'ใช้ในเวอร์ชันเก่า') : item.can_review ? t('Can be reviewed again', 'ส่งกลับมาตรวจได้') : t('Source unavailable or closed', 'ต้นทางปิดใช้งานหรือไม่มีไฟล์')}</td><td><Link className="btn btn-sm" to={`/rounds/${item.round_id}`}>{t('Open file', 'ดูไฟล์')}</Link></td></tr>)}</tbody></table></div>
      <div className="flex gap-2"><button className="btn btn-sm" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 50))}>{t('Previous', 'ก่อนหน้า')}</button><button className="btn btn-sm" disabled={q.data.next_offset === null} onClick={() => setOffset(q.data.next_offset!)}>{t('Next', 'ถัดไป')}</button></div>
    </>}
    {canEdit && <button className="btn" disabled={!retired || !Object.keys(selected).length || sending.isPending} onClick={() => sending.mutate()}>{sending.isPending ? t('Preparing review…', 'กำลังเตรียมตรวจ…') : t(`Send ${Object.keys(selected).length} files for review`, `ส่ง ${Object.keys(selected).length} ชุดกลับมาตรวจ`)}</button>}
    {sending.isError && <p role="alert" className="text-bad">{sending.error.message}</p>}
    {sending.data && <div className="space-y-2"><p role="status">{t('New review versions created. Answer and approve each before exporting again.', 'สร้างเวอร์ชันรอตรวจแล้ว ตอบคำถามและอนุมัติก่อนสร้างผลลัพธ์ใหม่')}</p>{sending.data.items.map(item => <Link className="btn btn-sm mr-2" key={item.mapping_id} to={`/review?mapping=${item.mapping_id}&round=${item.round_id}`}>{t('Open questions', 'เปิดคำถาม')}</Link>)}</div>}
  </section>
}

export function KnowledgeChanges({ id }: { id: string }) {
  const { t } = usePreferences(), [offset, setOffset] = useState(0)
  const q = useQuery({ queryKey: ['knowledge', id, 'changes', offset], queryFn: () => api.knowledgeChanges(id, offset) })
  return <section className="py-6 space-y-4"><h3 className="font-semibold">{t('Answer correction history', 'ประวัติการแก้คำตอบ')}</h3>{q.isPending ? <Spinner /> : q.isError ? <p role="alert">{q.error.message}</p> : <div className="space-y-4">{!q.data.items.length && <p className="text-sm text-muted">{t('No corrections yet', 'ยังไม่มีการแก้คำตอบ')}</p>}{q.data.items.map(event => <article key={event.id} className="border-b border-line pb-4 space-y-2"><p className="text-sm">{event.actor} · {fmtDate(event.at)}</p><p>{String(event.details.reason || '')}</p><p className="text-sm">{(() => { const question = { field: event.details.field || {}, kind: 'text' } as Question; return `${answerLabel(question, event.details.old_answer, t)} → ${answerLabel(question, event.details.new_answer, t)}` })()}</p>{typeof event.details.mapping_id === 'string' && <Link className="btn btn-sm" to={`/review?mapping=${event.details.mapping_id}&section=history`}>{t('Open the revised version', 'ดูเวอร์ชันที่แก้ไข')}</Link>}</article>)}<div className="flex gap-2"><button className="btn btn-sm" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - q.data.limit))}>{t('Previous', 'ก่อนหน้า')}</button><button className="btn btn-sm" disabled={q.data.next_offset === null} onClick={() => setOffset(q.data.next_offset!)}>{t('Next', 'ถัดไป')}</button></div></div>}</section>
}
