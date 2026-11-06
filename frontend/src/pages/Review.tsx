import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import { api, type FieldSpec, type ReviewTask } from '../lib/api'
import { AssistantIcon } from '../lib/icons'
import { usePreferences } from '../lib/preferences'
import { Badge, Empty, Modal, PageHeader, Spinner } from '../components/ui'
import { useToast } from '../lib/toast'
import { Workflow } from '../components/Workflow'
import { ReviewWorkspace } from '../components/ReviewWorkspace'
import { progressLabel } from '../lib/reviewFlow'
import { reviewReturn } from '../lib/reviewNavigation'


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

const semanticLabels: Record<string, string> = { 'price.vat_included': 'ราคารวม VAT', 'unit.pack_size': 'จำนวนต่อแพ็ก' }
const fieldName = (f: FieldSpec, th: boolean) => (th ? f.description_th || semanticLabels[f.semantic || ''] : f.description) || f.description || f.name

/** One answer for the same question asked by several files (ADR-059). Shown above the queue: it teaches the most per click. */
function QuestionGroups() {
  const { t, prefs } = usePreferences(), qc = useQueryClient(), toast = useToast(), th = prefs.language === 'th'
  const q = useQuery({ queryKey: ['review', 'groups'], queryFn: api.questionGroups, refetchInterval: 15000 })
  const answer = useMutation({
    mutationFn: api.answerQuestionGroup,
    onSuccess: (r) => { void qc.invalidateQueries({ queryKey: ['review'] }); void qc.invalidateQueries({ queryKey: ['dashboard'] }); toast.push({ kind: 'ok', title: t(`Answered for ${r.answered} files`, `ตอบให้ ${r.answered} ไฟล์แล้ว`) }) },
    onError: (e: Error) => toast.push({ kind: 'bad', title: t('Not saved', 'ยังบันทึกไม่ได้'), detail: e.message }),
  })
  const groups = q.data ?? []
  if (!groups.length) return null
  return <section className="card p-5 mb-4 border-signal/30" aria-label={t('Same question in several files', 'คำถามเดียวกันในหลายไฟล์')}>
    <h2 className="font-semibold text-navy">{t('The same question in several files', 'คำถามเดียวกันในหลายไฟล์')}</h2>
    <p className="text-sm text-ink-2 mt-1">{t('One answer settles every file listed. Each file still gets its own new version, previewed and rechecked by the policy.', 'ตอบครั้งเดียวใช้กับทุกไฟล์ที่ระบุ แต่ละไฟล์ยังได้เวอร์ชันใหม่ของตัวเอง ผ่านการตรวจของกฎเหมือนเดิม')}</p>
    <ul className="mt-3 divide-y divide-line">{groups.map(g => <li key={g.key} className="py-3 flex flex-wrap items-center gap-3">
      <div className="flex-1 min-w-[220px]"><div className="font-medium">{g.kind === 'choice' ? t(`Which column holds ${fieldName(g.field, false)}?`, `คอลัมน์ไหนคือ${fieldName(g.field, true)}?`) : fieldName(g.field, th)}</div><div className="text-xs text-muted mt-1 break-words">{g.members.map(m => m.customer_code).join(' · ')} · {t(`${g.members.length} files`, `${g.members.length} ไฟล์`)}</div></div>
      <div className="flex flex-wrap gap-2">{g.kind === 'choice' ? g.options.map(o => <button key={o} className="btn btn-sm" disabled={answer.isPending} onClick={() => answer.mutate({ key: g.key, answer: o })}>{o}</button>)
        : [true, false].map(v => <button key={String(v)} className="btn btn-sm" disabled={answer.isPending} onClick={() => answer.mutate({ key: g.key, answer: v })}>{g.field.semantic === 'price.vat_included' ? v ? t('VAT included', 'รวม VAT แล้ว') : t('VAT excluded', 'ยังไม่รวม VAT') : v ? t('Yes', 'ใช่') : t('No', 'ไม่ใช่')}</button>)}</div>
    </li>)}</ul>
  </section>
}

export function ReviewCenter() {
  const nav = useNavigate(), location = useLocation()
  const { t, date } = usePreferences()
  const qc = useQueryClient(), toast = useToast()
  const [waiting, setWaiting] = useState<ReviewTask | null>(null)
  const confirm = useMutation({ mutationFn: (id: string) => api.confirmAudit(id), onSuccess: () => { void qc.invalidateQueries({ queryKey: ['review'] }); void qc.invalidateQueries({ queryKey: ['dashboard'] }); toast.push({ kind: 'ok', title: t('Spot check recorded', 'บันทึกการสุ่มตรวจแล้ว') }) }, onError: (e: Error) => toast.push({ kind: 'bad', title: t('Not saved', 'ยังบันทึกไม่ได้'), detail: e.message }) })
  const resume = useMutation({ mutationFn: api.resumeReview, onSuccess: () => { void qc.invalidateQueries({ queryKey: ['review'] }); void qc.invalidateQueries({ queryKey: ['dashboard'] }) }, onError: (e: Error) => toast.push({ kind: 'bad', title: t('Not saved', 'ยังบันทึกไม่ได้'), detail: e.message }) })
  const [params, setParams] = useSearchParams()
  const tab = params.get('status') || 'open'
  const round = params.get('round') || ''
  const offset = Math.max(0, Number(params.get('offset')) || 0)

  const query = useQuery({ queryKey: ['review', tab, round, offset], queryFn: () => api.review(tab, round, offset), refetchInterval: tab === 'open' ? 10000 : false })
  const items = query.data ?? []
  const choose = (values: Record<string, string>) => setParams(old => { const next = new URLSearchParams(old); next.delete('offset'); Object.entries(values).forEach(([k, v]) => v ? next.set(k, v) : next.delete(k)); return next })
  const active = params.get('mapping')
  const openReview = (mapping: string, roundId: string) => {
    const next = new URLSearchParams(params)
    next.set('returnTo', location.pathname + location.search)
    next.set('returnScroll', String(document.getElementById('main-content')?.scrollTop || 0))
    next.set('mapping', mapping); next.set('round', roundId); next.delete('section'); next.delete('panel')
    setParams(next)
  }
  if (active) return <ReviewWorkspace key={active} id={active} onSaved={(id, section = 'finish', job) => {
    setParams(old => { const next = new URLSearchParams(old); next.set('mapping', id); next.set('section', section); next.delete('panel'); if (job) next.set('job', job); else next.delete('job'); return next }, { replace: true })
  }} onDone={() => nav(reviewReturn(params.get('returnTo')), { state: { restoreScroll: Math.max(0, Number(params.get('returnScroll')) || 0) } })} />
  return <>
    <PageHeader title={t('Questions & issues', 'คำถามและปัญหา')} subtitle={t('Start with one file. Answer what you know, inspect the evidence, and preview before saving. Differences are a reason to check; they do not by themselves prove an error.', 'เริ่มทีละไฟล์ ตอบสิ่งที่ทราบ ตรวจหลักฐาน แล้วดูตัวอย่างก่อนบันทึก ความแตกต่างเป็นเหตุให้ตรวจ ยังไม่ได้แปลว่าข้อมูลผิด')} actions={<Link className="btn" to={round ? `/assistant?round=${round}` : '/assistant'}><AssistantIcon size={16} />{t('Ask the assistant', 'ถามผู้ช่วย')}</Link>} />
    <Workflow status="review" />
    <div className="flex flex-wrap gap-2 mb-4">{[['open', t('Needs your attention', 'รอคุณตรวจ')], ['waiting', t('Waiting for the customer', 'รอลูกค้าตอบ')], ['approved', t('Approved', 'อนุมัติแล้ว')], ['rejected', t('Rejected', 'ไม่อนุมัติ')], ['dismissed', t('Replaced by a newer version', 'มีเวอร์ชันใหม่แล้ว')]].map(([id, label]) => <button key={id} className={`btn btn-sm ${tab === id ? 'bg-signal-soft text-navy border-signal/30' : 'btn-ghost'}`} onClick={() => { choose({ status: id, round: '' }) }}>{label}</button>)}{round && <button className="btn btn-sm" onClick={() => { choose({ round: '' }) }}>{t('Show all files', 'ดูทุกไฟล์')}</button>}</div>
    {query.isPending && <div className="p-6 flex gap-2 text-muted"><Spinner />{t('Loading questions…', 'กำลังโหลดคำถาม…')}</div>}
    {query.error && <p role="alert" className="text-bad">{t('Could not load the queue.', 'โหลดรายการไม่ได้')} <button className="btn btn-sm" onClick={() => void query.refetch()}>{t('Try again', 'ลองใหม่')}</button></p>}
    {!query.isPending && !query.error && !items.length && <Empty title={t('No items here', 'ไม่มีรายการในหมวดนี้')} />}
    {waiting && <WaitDialog key={waiting.id} task={waiting} onClose={() => setWaiting(null)} />}
    {tab === 'open' && !round && <QuestionGroups />}
    <ul className="space-y-3">{items.map(task => <li key={task.id} className="card p-5">
      <div className="flex flex-wrap gap-3 items-start"><div className="flex-1 min-w-[180px]"><div className="font-semibold text-navy break-words">{task.customer?.name?.startsWith(task.customer.code) ? task.customer.name : `${task.customer?.code ?? ''} · ${task.customer?.name ?? ''}`}</div><div className="text-xs text-muted mt-1 break-words">{task.round?.label} · {date(task.created_at)}</div></div>{task.status === 'open' && <Badge tone={task.age_days >= 7 ? 'warn' : 'neutral'}>{task.age_days ? t(`Waiting ${task.age_days} days`, `รอมา ${task.age_days} วัน`) : t('New today', 'มาวันนี้')}</Badge>}<Badge tone={task.kind === 'audit' ? 'neutral' : task.details.decision === 'block' ? 'bad' : 'warn'}>{task.kind === 'audit' ? t('Spot check', 'สุ่มตรวจ') : task.progress ? progressLabel(task.progress, t) : task.details.decision === 'block' ? t('Check the source file', 'ต้องตรวจไฟล์ต้นทาง') : t('Needs confirmation', 'ต้องยืนยันข้อมูล')}</Badge></div>
      {task.progress ? <div className="mt-3 space-y-2">
        <p className="text-sm text-muted">{t(`${task.progress.saved_answers} answers confirmed`, `ยืนยันคำตอบแล้ว ${task.progress.saved_answers} ข้อ`)}{task.progress.remaining_notes > 0 && <span> · {t(`${task.progress.remaining_notes} additional notes to check`, `มีเรื่องต้องตรวจเพิ่ม ${task.progress.remaining_notes} ข้อ`)}</span>}</p>
        {task.draft && <p className="text-sm text-signal">{t(`Your draft: ${task.draft.answers} answers selected, not confirmed yet`, `ร่างของคุณ: เลือกคำตอบไว้ ${task.draft.answers} ข้อ ยังไม่ยืนยันผล`)}</p>}
        {!!task.progress.topics.length && <ul className="text-sm space-y-1 break-words">{task.progress.topics.map((topic, i) => <li key={i}>{i + 1}. {t(topic.en, topic.th)}</li>)}{task.progress.remaining_questions > task.progress.topics.length && <li className="text-muted">{t(`And ${task.progress.remaining_questions - task.progress.topics.length} more questions`, `และอีก ${task.progress.remaining_questions - task.progress.topics.length} คำถาม`)}</li>}</ul>}
        {task.progress.state === 'final_review' && <p className="text-sm">{t('No questions remain in this version. Review the output before approval.', 'เวอร์ชันนี้ไม่มีคำถามค้างแล้ว ตรวจผลลัพธ์ก่อนอนุมัติ')}</p>}
        {task.progress.state === 'blocked' && <p className="text-sm">{t('File checks still need attention. Answering questions alone will not clear these issues.', 'ผลตรวจไฟล์ยังมีปัญหา ต้องตรวจและแก้ก่อน การตอบคำถามอย่างเดียวยังไม่พอ')}</p>}
      </div> : <p className="text-sm text-ink-2 mt-3 leading-6">{issueSummary(task, t)}</p>}
      {task.status === 'waiting' && <p className="mt-3 text-sm rounded-lg bg-surface-2 px-3 py-2">{t('Waiting for the customer', 'รอลูกค้าตอบ')}: {task.waiting_note}{task.waiting_until && <span className="text-muted"> · {t('comes back', 'กลับมาวันที่')} {date(task.waiting_until)}</span>}</p>}
      <div className="flex flex-wrap items-center justify-end gap-2 mt-4">{task.status === 'open' && <button className="btn btn-sm" onClick={() => setWaiting(task)}>{t('Wait for the customer', 'รอลูกค้าตอบ')}</button>}{task.status === 'waiting' && <button className="btn btn-sm" disabled={resume.isPending} onClick={() => resume.mutate(task.id)}>{t('Bring it back now', 'กลับมาตรวจตอนนี้')}</button>}{task.kind === 'audit' && task.status === 'open' && <button className="btn btn-sm btn-primary" disabled={confirm.isPending} onClick={() => confirm.mutate(task.id)}>{t('Looks right', 'ถูกต้องแล้ว')}</button>}{task.mapping_version_id ? <button className="btn btn-sm" onClick={() => { openReview(task.mapping_version_id!, task.round_id) }}>{task.draft ? t('Continue draft', 'ทำต่อจากร่าง') : task.progress?.state === 'final_review' ? t('Review & approve', 'ตรวจผลและอนุมัติ') : task.progress?.state === 'blocked' ? t('Check issues', 'ตรวจปัญหา') : task.progress?.state === 'notes' ? t('Review pending notes', 'ตรวจเรื่องที่ค้าง') : task.progress?.state === 'questions' ? t('Continue answering', 'ตอบคำถาม') : t('Open review', 'เปิดการตรวจ')} <ArrowRight size={14} /></button> : <Link className="btn btn-sm" to={task.dataset_version_id ? `/datasets/${task.dataset_version_id}?tab=findings` : `/rounds/${task.round_id}`}>{t('Check results', 'ตรวจผลลัพธ์')} →</Link>}</div>
    </li>)}</ul>
    <div className="flex justify-between mt-4"><button className="btn btn-sm" disabled={!offset} onClick={() => choose({ offset: String(Math.max(0, offset - 50)) })}>{t('Previous', 'ก่อนหน้า')}</button><button className="btn btn-sm" disabled={items.length < 50} onClick={() => choose({ offset: String(offset + 50) })}>{t('Next', 'ถัดไป')}</button></div>

  </>
}

function issueSummary(task: ReviewTask, t: (en: string, th: string) => string) {
  if (task.kind === 'audit') return t('This file was approved automatically. It was picked for a spot check: look at the matching and a sample, then confirm it or correct it in the mapping editor.', 'ไฟล์นี้ผ่านอัตโนมัติ ระบบสุ่มมาให้ตรวจ ดูการจับคู่กับตัวอย่างผล แล้วยืนยันว่าถูก หรือแก้ในตัวแก้การจับคู่')
  if (task.details.decision === 'block' || task.kind === 'blocked') return t('The file may not belong to this customer, or its data could not pass the checks. Confirm the source file first.', 'ไฟล์อาจไม่ตรงกับลูกค้ารายนี้ หรือข้อมูลยังไม่ผ่านการตรวจ เริ่มจากตรวจว่าเลือกไฟล์ถูกต้องหรือไม่')
  if (task.details.open_questions?.length) return t('The source file leaves some details unclear. Please confirm them before we use the results.', 'ไฟล์ต้นทางยังบอกข้อมูลบางอย่างไม่ชัดเจน ต้องให้คุณยืนยันก่อนนำผลลัพธ์ไปใช้')
  if (task.kind === 'drift') return t('This file differs from the previous format. Check that the new column matching still means the same thing.', 'รูปแบบไฟล์เปลี่ยนจากครั้งก่อน ตรวจว่าคอลัมน์ที่จับคู่ใหม่ยังหมายถึงข้อมูลเดิม')
  return t('Check the suggested column matching and a sample of the result before approving.', 'ตรวจการจับคู่คอลัมน์ที่ระบบเสนอและตัวอย่างผลลัพธ์ก่อนอนุมัติ')
}
