import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import { api, type ReviewTask } from '../lib/api'
import { AssistantIcon } from '../lib/icons'
import { usePreferences } from '../lib/preferences'
import { Badge, Empty, Modal, PageHeader, Spinner } from '../components/ui'
import { useToast } from '../lib/toast'
import { Workflow } from '../components/Workflow'
import { ReviewWorkspace } from '../components/ReviewWorkspace'
import { VerdictLine } from '../components/VerdictCard'


/** Park a question while the customer answers: it leaves the queue and comes back on the day chosen (ADR-053). */
function WaitDialog({ task, onClose }: { task: ReviewTask; onClose: () => void }) {
  const { t } = usePreferences(), qc = useQueryClient(), toast = useToast()
  const [note, setNote] = useState(''), [until, setUntil] = useState(() => new Date(Date.now() + 7 * 86400000).toISOString().slice(0, 10))
  const wait = useMutation({
    mutationFn: () => api.waitForCustomer(task.id, { until, note }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['review'] }); void qc.invalidateQueries({ queryKey: ['dashboard'] }); onClose(); toast.push({ kind: 'ok', title: t('Moved to waiting for the customer', 'ย้ายไปรอลูกค้าตอบแล้ว') }) },
    onError: (e: Error) => toast.push({ kind: 'bad', title: t('Not saved', 'ยังบันทึกไม่ได้'), detail: e.message }),
  })
  return <Modal open onClose={() => { if (!wait.isPending) onClose() }} title={t('Wait for the customer', 'รอลูกค้าตอบ')} width="max-w-md" footer={<><button className="btn" disabled={wait.isPending} onClick={onClose}>{t('Back', 'กลับ')}</button><button className="btn btn-primary" disabled={wait.isPending || !note.trim() || !until} onClick={() => wait.mutate()}>{t('Wait', 'พักไว้')}</button></>}>
    <div className="grid gap-4">
      <label className="grid gap-1 text-sm">{t('What are you waiting for?', 'รออะไรจากลูกค้า')}<input className="input" autoFocus maxLength={500} placeholder={t('e.g. asked whether prices include VAT', 'เช่น ถามแล้วว่าราคารวม VAT ไหม')} value={note} onChange={e => setNote(e.target.value)} /></label>
      <label className="grid gap-1 text-sm">{t('Bring it back on', 'ให้กลับมาในวันที่')}<input className="input" type="date" value={until} onChange={e => setUntil(e.target.value)} /></label>
      <p className="text-sm text-ink-2 m-0">{t('It leaves your queue and comes back on that day, or sooner if you bring it back.', 'เรื่องนี้จะออกจากคิวของคุณ และกลับมาเองในวันนั้น หรือกดกลับมาก่อนก็ได้')}</p>
    </div>
  </Modal>
}

export function ReviewCenter() {
  const { t, date } = usePreferences()
  const qc = useQueryClient(), toast = useToast()
  const [waiting, setWaiting] = useState<ReviewTask | null>(null)
  const resume = useMutation({ mutationFn: api.resumeReview, onSuccess: () => { void qc.invalidateQueries({ queryKey: ['review'] }); void qc.invalidateQueries({ queryKey: ['dashboard'] }) }, onError: (e: Error) => toast.push({ kind: 'bad', title: t('Not saved', 'ยังบันทึกไม่ได้'), detail: e.message }) })
  const [params, setParams] = useSearchParams()
  const tab = params.get('status') || 'open'
  const round = params.get('round') || ''
  const offset = Math.max(0, Number(params.get('offset')) || 0)

  const query = useQuery({ queryKey: ['review', tab, round, offset], queryFn: () => api.review(tab, round, offset), refetchInterval: tab === 'open' ? 10000 : false })
  const items = query.data ?? []
  const choose = (values: Record<string, string>) => setParams(old => { const next = new URLSearchParams(old); next.delete('offset'); Object.entries(values).forEach(([k, v]) => v ? next.set(k, v) : next.delete(k)); return next })
  const active = params.get('mapping') || (round ? items.find(i => i.mapping_version_id)?.mapping_version_id ?? null : null)
  if (active) return <ReviewWorkspace key={active} id={active} onSaved={id => { choose({ mapping: id }) }} onDone={() => { choose({ mapping: '', round: '' }) }} />
  return <>
    <PageHeader title={t('Questions & issues', 'คำถามและปัญหา')} subtitle={t('Start with one file. Answer what you know, inspect the evidence, and preview before saving. Differences are a reason to check; they do not by themselves prove an error.', 'เริ่มทีละไฟล์ ตอบสิ่งที่ทราบ ตรวจหลักฐาน แล้วดูตัวอย่างก่อนบันทึก ความแตกต่างเป็นเหตุให้ตรวจ ยังไม่ได้แปลว่าข้อมูลผิด')} actions={<Link className="btn" to={round ? `/assistant?round=${round}` : '/assistant'}><AssistantIcon size={16} />{t('Ask the assistant', 'ถามผู้ช่วย')}</Link>} />
    <Workflow status="review" />
    <div className="flex flex-wrap gap-2 mb-4">{[['open', t('Needs your attention', 'รอคุณตรวจ')], ['waiting', t('Waiting for the customer', 'รอลูกค้าตอบ')], ['approved', t('Approved', 'อนุมัติแล้ว')], ['rejected', t('Rejected', 'ไม่อนุมัติ')], ['dismissed', t('Replaced by a newer version', 'มีเวอร์ชันใหม่แล้ว')]].map(([id, label]) => <button key={id} className={`btn btn-sm ${tab === id ? 'bg-signal-soft text-navy border-signal/30' : 'btn-ghost'}`} onClick={() => { choose({ status: id, round: '' }) }}>{label}</button>)}{round && <button className="btn btn-sm" onClick={() => { choose({ round: '' }) }}>{t('Show all files', 'ดูทุกไฟล์')}</button>}</div>
    {query.isPending && <div className="p-6 flex gap-2 text-muted"><Spinner />{t('Loading questions…', 'กำลังโหลดคำถาม…')}</div>}
    {query.error && <p role="alert" className="text-bad">{t('Could not load the queue.', 'โหลดรายการไม่ได้')} <button className="btn btn-sm" onClick={() => void query.refetch()}>{t('Try again', 'ลองใหม่')}</button></p>}
    {!query.isPending && !query.error && !items.length && <Empty title={t('No items here', 'ไม่มีรายการในหมวดนี้')} />}
    {waiting && <WaitDialog key={waiting.id} task={waiting} onClose={() => setWaiting(null)} />}
    <ul className="space-y-3">{items.map(task => <li key={task.id} className="card p-5">
      <div className="flex flex-wrap gap-3 items-start"><div className="flex-1 min-w-[180px]"><div className="font-semibold text-navy break-words">{task.customer?.name?.startsWith(task.customer.code) ? task.customer.name : `${task.customer?.code ?? ''} · ${task.customer?.name ?? ''}`}</div><div className="text-xs text-muted mt-1 break-words">{task.round?.label} · {date(task.created_at)}</div></div>{task.status === 'open' && <Badge tone={task.age_days >= 7 ? 'warn' : 'neutral'}>{task.age_days ? t(`Waiting ${task.age_days} days`, `รอมา ${task.age_days} วัน`) : t('New today', 'มาวันนี้')}</Badge>}<Badge tone={task.details.decision === 'block' ? 'bad' : 'warn'}>{task.details.decision === 'block' ? t('Check the source file', 'ต้องตรวจไฟล์ต้นทาง') : t('Needs confirmation', 'ต้องยืนยันข้อมูล')}</Badge></div>
      <p className="text-sm text-ink-2 mt-3 leading-6">{task.details.verdict ? <span className="font-medium text-navy"><VerdictLine v={task.details.verdict} /></span> : issueSummary(task, t)}</p>
      <ul className="mt-3 space-y-1.5 text-sm text-ink-2 leading-6 break-words list-disc pl-5">{(task.explanations?.length ? task.explanations : [{ en: 'Open the questions to inspect the source and confirm the matching.', th: 'เปิดคำถามเพื่อตรวจต้นทางและยืนยันการจับคู่คอลัมน์' }]).map((r, i) => <li key={i}>{t(r.en, r.th)}</li>)}</ul>
      {task.status === 'waiting' && <p className="mt-3 text-sm rounded-lg bg-surface-2 px-3 py-2">{t('Waiting for the customer', 'รอลูกค้าตอบ')}: {task.waiting_note}{task.waiting_until && <span className="text-muted"> · {t('comes back', 'กลับมาวันที่')} {date(task.waiting_until)}</span>}</p>}
      <div className="flex flex-wrap items-center justify-end gap-2 mt-4">{task.status === 'open' && <button className="btn btn-sm" onClick={() => setWaiting(task)}>{t('Wait for the customer', 'รอลูกค้าตอบ')}</button>}{task.status === 'waiting' && <button className="btn btn-sm" disabled={resume.isPending} onClick={() => resume.mutate(task.id)}>{t('Bring it back now', 'กลับมาตรวจตอนนี้')}</button>}{task.mapping_version_id ? <button className="btn btn-sm" onClick={() => { choose({ mapping: task.mapping_version_id!, round: task.round_id }) }}>{t('Open questions', 'เปิดคำถาม')} <ArrowRight size={14} /></button> : <Link className="btn btn-sm" to={task.dataset_version_id ? `/datasets/${task.dataset_version_id}?tab=findings` : `/rounds/${task.round_id}`}>{t('Check results', 'ตรวจผลลัพธ์')} →</Link>}</div>
    </li>)}</ul>
    <div className="flex justify-between mt-4"><button className="btn btn-sm" disabled={!offset} onClick={() => choose({ offset: String(Math.max(0, offset - 50)) })}>{t('Previous', 'ก่อนหน้า')}</button><button className="btn btn-sm" disabled={items.length < 50} onClick={() => choose({ offset: String(offset + 50) })}>{t('Next', 'ถัดไป')}</button></div>

  </>
}

function issueSummary(task: ReviewTask, t: (en: string, th: string) => string) {
  if (task.details.decision === 'block' || task.kind === 'blocked') return t('The file may not belong to this customer, or its data could not pass the checks. Confirm the source file first.', 'ไฟล์อาจไม่ตรงกับลูกค้ารายนี้ หรือข้อมูลยังไม่ผ่านการตรวจ เริ่มจากตรวจว่าเลือกไฟล์ถูกต้องหรือไม่')
  if (task.details.open_questions?.length) return t('The source file leaves some details unclear. Please confirm them before we use the results.', 'ไฟล์ต้นทางยังบอกข้อมูลบางอย่างไม่ชัดเจน ต้องให้คุณยืนยันก่อนนำผลลัพธ์ไปใช้')
  if (task.kind === 'drift') return t('This file differs from the previous format. Check that the new column matching still means the same thing.', 'รูปแบบไฟล์เปลี่ยนจากครั้งก่อน ตรวจว่าคอลัมน์ที่จับคู่ใหม่ยังหมายถึงข้อมูลเดิม')
  return t('Check the suggested column matching and a sample of the result before approving.', 'ตรวจการจับคู่คอลัมน์ที่ระบบเสนอและตัวอย่างผลลัพธ์ก่อนอนุมัติ')
}
