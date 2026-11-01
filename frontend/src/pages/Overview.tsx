import { WorkRoundDashboard } from '../components/WorkRoundDashboard'
import { Localized } from '../lib/locale'
import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowRight, Upload, Download, CircleCheck } from 'lucide-react'
import { api, type ImportRound } from '../lib/api'
import { fmtInt, fmtPct, timeAgo } from '../lib/utils'
import { nextStep } from '../lib/steps'
import { usePreferences } from '../lib/preferences'
import { Workflow, workflowStep } from '../components/Workflow'
import { PageHeader, Stat, Section, Empty, Badge, Spinner } from '../components/ui'
import { VerdictLine } from '../components/VerdictCard'
import { Foresight } from '../components/Foresight'

export function Overview() {
  const { t } = usePreferences()
  const dashboard = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard, refetchInterval: 10000 })
  const roundsQuery = useQuery({ queryKey: ['rounds', ''], queryFn: () => api.rounds(), refetchInterval: 10000 })
  const uploadsQuery = useQuery({ queryKey: ['uploads'], queryFn: () => api.uploads(), refetchInterval: 10000 })
  const d = dashboard.data
  const unknown = (uploadsQuery.data ?? []).filter((u) => u.counts.needs_customer || u.stuck)
  const priority = ['blocked', 'failed', 'rejected', 'review', 'transformed', 'approved', 'auto_approved', 'approved_data']
  const waiting = (roundsQuery.data ?? []).filter((r) => priority.includes(r.status)).sort((a, b) => priority.indexOf(a.status) - priority.indexOf(b.status))
  const target = (r: ImportRound) => ['review', 'rejected', 'blocked'].includes(r.status) ? `/review?round=${r.id}` : r.status === 'transformed' && r.summary.dataset_version_id ? `/datasets/${r.summary.dataset_version_id}?tab=diff` : `/rounds/${r.id}`
  const loading = roundsQuery.isPending || uploadsQuery.isPending
  const error = roundsQuery.isError || uploadsQuery.isError || dashboard.isError
  return <>
    <PageHeader title={t("Your workspace", "งานของคุณ")} subtitle={t("Upload files. Answer questions. Download the results.", "อัปโหลดไฟล์ ตอบคำถามที่จำเป็น แล้วดาวน์โหลดผลลัพธ์")}
      actions={<Link to="/imports/new" className="btn btn-primary btn-lg"><Upload size={16} /> {t("Upload files", "อัปโหลดไฟล์")}</Link>} />
    <div className="grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-3 mb-6">
      <Stat label="Handled automatically" value={d?.automation_rate != null ? fmtPct(d.automation_rate) : '–'} hint="Mappings approved by policy" />
      <Stat label="Records exported" value={fmtInt(d?.published_rows)} />
      <Stat label="Customers" value={fmtInt(d?.customers)} />
      <Stat label={t('Learned from you', 'เรียนจากคุณ')} value={d?.learning ? fmtInt(d.learning.aliases + d.learning.constants) : '–'} hint={d?.learning ? t(`${fmtInt(d.learning.uses)} uses · corrections per approval ${d.learning.corrections_per_approval ?? '–'}`, `ใช้แล้ว ${fmtInt(d.learning.uses)} ครั้ง · การแก้ต่อการอนุมัติ ${d.learning.corrections_per_approval ?? '–'}`) : ''} />
      <Stat label={t('Estimated AI cost', 'ค่า AI โดยประมาณ')} value={d ? `$${d.llm.cost_usd.toFixed(2)}` : '–'} hint={d ? `${fmtInt(d.llm.calls)} ${t('usage records · not provider billing', 'รายการที่บันทึก · ไม่ใช่ยอดเรียกเก็บ')}` : ''} />
    </div>
    {error && <div role="alert" className="card p-4 mb-5 text-bad">Some tasks could not be loaded. <button className="btn btn-sm ml-2" onClick={() => { void roundsQuery.refetch(); void uploadsQuery.refetch(); void dashboard.refetch() }}>Try again</button></div>}
    <Workflow />
    <WorkRoundDashboard />
    <Foresight />
    <Section title={t("Needs your attention", "งานที่รอคุณตรวจ")} className="mb-7" actions={<Link to="/review" className="btn btn-ghost btn-sm">{t("Questions & issues", "คำถามและปัญหา")} <ArrowRight size={14} /></Link>}>
      {loading && <div className="p-5 flex gap-2 items-center text-muted"><Spinner /> Loading tasks…</div>}
      {!!(d?.review?.stale || d?.review?.waiting) && <div className="flex flex-wrap gap-x-5 gap-y-1 px-5 py-3 border-b border-line text-sm">
        {!!d.review.stale && <Link to="/review" className="text-warn font-medium hover:underline">{t(`${d.review.stale} questions have waited over ${d.review.stale_after_days} days`, `มี ${d.review.stale} เรื่องรอคุณเกิน ${d.review.stale_after_days} วัน`)}{d.review.oldest_days != null && <span className="font-normal"> · {t(`oldest ${d.review.oldest_days} days`, `นานสุด ${d.review.oldest_days} วัน`)}</span>}</Link>}
        {!!d.review.waiting && <Link to="/review?status=waiting" className="text-ink-2 hover:underline">{t(`${d.review.waiting} waiting for the customer`, `รอลูกค้าตอบ ${d.review.waiting} เรื่อง`)}</Link>}
      </div>}
      {unknown.map((u) => <Link key={u.id} to={`/uploads/${u.id}`} className="flex flex-wrap items-center gap-3 px-5 py-4 border-b border-line hover:bg-surface-2">
        <span className="flex-1 min-w-[180px]"><span className="block font-medium break-words">{u.label}</span><span className="block text-muted text-[13px] mt-1">{u.stuck ? `${u.stuck} files paused. Resume processing to continue.` : `${u.counts.needs_customer} files need a customer before processing can continue.`}</span></span>
        <span className="btn btn-sm">{u.stuck ? 'Resume upload' : 'Choose customer'} <ArrowRight size={14} /></span>
      </Link>)}
      {waiting.slice(0, 6).map((r) => {
        const step = nextStep(r)
        return <Link key={r.id} to={target(r)} className="flex flex-wrap items-center gap-3 px-5 py-4 border-b border-line last:border-0 hover:bg-surface-2">
          <span className="flex-1 min-w-[180px]"><span className="block font-medium break-words">{r.customer?.name ?? r.customer?.code} <span className="font-normal text-muted">· {r.label}</span></span><span className="block text-[13px] text-muted mt-1">{r.summary.verdict ? <VerdictLine v={r.summary.verdict} /> : t(step.action || 'Processing', step.th)}</span></span>
          <Badge tone={step.tone === 'problem' ? 'bad' : 'warn'}>{step.tone === 'problem' ? t('Action required', 'ต้องตรวจสอบ') : t('Ready for review', 'รอคุณตรวจ')}</Badge>
          <span className="btn btn-sm">{t(step.action || "Open", "ตรวจรายการนี้")} <ArrowRight size={14} /></span>
        </Link>
      })}
      {!loading && !error && !waiting.length && !unknown.length && <div className="p-6 flex items-center gap-3 text-ink-2"><CircleCheck size={20} className="text-ok" /><div className="min-w-0"><div className="font-medium"><Localized>Nothing needs your attention</Localized></div><div className="text-[13px] text-muted"><Localized>You can upload another batch or open a recent file below.</Localized></div></div></div>}
      {waiting.length > 6 && <Link to="/imports?view=files" className="block px-5 py-3 text-signal text-[13px] hover:underline">View all {waiting.length} tasks →</Link>}
    </Section>
    <Section title={t("Recent files", "ไฟล์ล่าสุด")} actions={<Link to="/imports?view=files" className="btn btn-ghost btn-sm">{t("Upload history", "ประวัติไฟล์")} <ArrowRight size={14} /></Link>}>
      {d?.recent_rounds?.length ? <table className="table"><thead><tr><th><Localized>Customer / delivery</Localized></th><th><Localized>Next action</Localized></th><th className="text-right"><Localized>Records</Localized></th><th><Localized>Updated</Localized></th><th aria-label={t('Open', 'เปิด')}></th></tr></thead><tbody>
        {d.recent_rounds.map((r) => <tr key={r.id}>
          <td><Link to={`/rounds/${r.id}`} className="font-medium text-navy hover:underline">{r.customer?.name ?? r.customer?.code}</Link><div className="text-muted text-[12.5px] mt-0.5">{r.label}</div></td>
          <td><NextStepCell r={r} /></td><td className="text-right tnum">{fmtInt(r.summary.rows)}</td><td className="text-muted whitespace-nowrap">{timeAgo(r.updated_at)}</td>
          <td><Link to={`/rounds/${r.id}`} className="btn btn-ghost btn-sm" aria-label={`Open ${r.label}`}>{r.status === 'published' ? <Download size={15} /> : <ArrowRight size={15} />}</Link></td>
        </tr>)}
      </tbody></table> : <div className="p-4"><Empty title={dashboard.isPending ? 'Loading files…' : 'Your first file starts here'} body="Upload an Excel or CSV file to create your first delivery." /></div>}
    </Section>
    <div className="mt-5 flex items-center gap-2 text-[13px] text-muted"><span>New to FORMA?</span><Link to="/guide" className="text-signal hover:underline">See how importing works →</Link></div>
  </>
}

export function NextStepCell({ r }: { r: ImportRound }): ReactNode {
  const { t } = usePreferences()
  const s = nextStep(r)
  const tone = s.tone === 'problem' ? 'bad' : s.tone === 'todo' ? 'warn' : s.tone === 'done' ? 'ok' : 'neutral'
  return (
    <span className="flex items-center gap-2 min-w-[160px]">
      <Badge tone={tone} className="whitespace-nowrap shrink-0">{r.status === 'published' ? t('Done', 'เสร็จแล้ว') : s.step === 0 ? t('Closed', 'ปิดแล้ว') : `${workflowStep(r.status)}/3`}</Badge>
      <span className="leading-tight min-w-0"><span className="block text-[12.5px] font-semibold text-ink">{t(s.action ?? (s.tone === 'done' ? 'Downloaded' : 'Working…'), s.th || 'กำลังดำเนินการ')}</span>{r.summary.verdict && s.tone !== 'done' && <span className="block text-[12px] text-muted max-w-[360px]"><VerdictLine v={r.summary.verdict} /></span>}</span>
    </span>
  )
}
