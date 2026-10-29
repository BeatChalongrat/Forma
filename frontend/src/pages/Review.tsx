import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowRight, MessageCircleMore } from 'lucide-react'
import { api, type ReviewTask } from '../lib/api'
import { usePreferences } from '../lib/preferences'
import { Badge, Empty, PageHeader, Spinner } from '../components/ui'
import { Workflow } from '../components/Workflow'
import { ReviewWorkspace } from '../components/ReviewWorkspace'


export function ReviewCenter() {
  const { t, date } = usePreferences()
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
    <PageHeader title={t('Questions & issues', 'คำถามและปัญหา')} subtitle={t('Start with one file. Answer what you know, inspect the evidence, and preview before saving.', 'เริ่มทีละไฟล์ ตอบสิ่งที่ทราบ ตรวจหลักฐาน แล้วดูตัวอย่างก่อนบันทึก')} actions={<Link className="btn" to={round ? `/assistant?round=${round}` : '/assistant'}><MessageCircleMore size={16} />{t('Ask the assistant', 'ถามผู้ช่วย')}</Link>} />
    <Workflow status="review" />
    <div className="flex flex-wrap gap-2 mb-4">{[['open', t('Needs your attention', 'รอคุณตรวจ')], ['approved', t('Approved', 'อนุมัติแล้ว')], ['rejected', t('Rejected', 'ไม่อนุมัติ')], ['dismissed', t('Replaced by a newer version', 'มีเวอร์ชันใหม่แล้ว')]].map(([id, label]) => <button key={id} className={`btn btn-sm ${tab === id ? 'bg-signal-soft text-navy border-signal/30' : 'btn-ghost'}`} onClick={() => { choose({ status: id, round: '' }) }}>{label}</button>)}{round && <button className="btn btn-sm" onClick={() => { choose({ round: '' }) }}>{t('Show all files', 'ดูทุกไฟล์')}</button>}</div>
    {query.isPending && <div className="p-6 flex gap-2 text-muted"><Spinner />{t('Loading questions…', 'กำลังโหลดคำถาม…')}</div>}
    {query.error && <p role="alert" className="text-bad">{t('Could not load the queue.', 'โหลดรายการไม่ได้')} <button className="btn btn-sm" onClick={() => void query.refetch()}>{t('Try again', 'ลองใหม่')}</button></p>}
    {!query.isPending && !query.error && !items.length && <Empty title={t('No items here', 'ไม่มีรายการในหมวดนี้')} />}
    <ul className="space-y-3">{items.map(task => <li key={task.id} className="card p-5">
      <div className="flex flex-wrap gap-3 items-start"><div className="flex-1 min-w-[180px]"><div className="font-semibold text-navy break-words">{task.customer?.name?.startsWith(task.customer.code) ? task.customer.name : `${task.customer?.code ?? ''} · ${task.customer?.name ?? ''}`}</div><div className="text-xs text-muted mt-1 break-words">{task.round?.label} · {date(task.created_at)}</div></div><Badge tone={task.details.decision === 'block' ? 'bad' : 'warn'}>{task.details.decision === 'block' ? t('Check the source file', 'ต้องตรวจไฟล์ต้นทาง') : t('Needs confirmation', 'ต้องยืนยันข้อมูล')}</Badge></div>
      <p className="text-sm text-ink-2 mt-3 leading-6">{issueSummary(task, t)}</p>
      <div className="flex flex-wrap items-center justify-between gap-3 mt-4"><details className="text-xs text-muted flex-1 min-w-0"><summary>{t('Why this needs review', 'เหตุผลและรายละเอียด')}</summary><ul className="mt-2 space-y-1 break-words">{task.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul></details>{task.mapping_version_id ? <button className="btn btn-sm" onClick={() => { choose({ mapping: task.mapping_version_id!, round: task.round_id }) }}>{t('Open questions', 'เปิดคำถาม')} <ArrowRight size={14} /></button> : <Link className="btn btn-sm" to={task.dataset_version_id ? `/datasets/${task.dataset_version_id}?tab=findings` : `/rounds/${task.round_id}`}>{t('Check results', 'ตรวจผลลัพธ์')} →</Link>}</div>
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
