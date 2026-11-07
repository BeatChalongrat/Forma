import { useState } from 'react'
import { Link } from 'react-router-dom'
import { inspectionHref } from '../lib/inspection'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FileText, ArrowLeft, ArrowRight } from 'lucide-react'
import { api } from '../lib/api'
import { usePreferences } from '../lib/preferences'
import { saveFile } from './ChatFiles'
import { Modal } from './ui'

export function DocumentJobs({ chat, onOpen }: { chat: string; onOpen: (id: string, version: number) => void }) {
  const { t } = usePreferences()
  const [offset, setOffset] = useState(0), [selected, setSelected] = useState<string>()
  const [technical, setTechnical] = useState(false), [downloadError, setDownloadError] = useState(false)
  const qc = useQueryClient()
  const jobs = useQuery({ queryKey: ['document-jobs', chat, offset], queryFn: () => api.documentJobs(chat, offset), refetchInterval: 3000 })
  const detail = useQuery({ queryKey: ['document-job', selected], queryFn: () => api.documentJob(selected!), enabled: !!selected, refetchInterval: 3000 })
  const action = useMutation({ mutationFn: ({ id, name }: { id: string; name: 'cancel' | 'retry' }) => api.documentAction(id, name), onSuccess: () => { void qc.invalidateQueries({ queryKey: ['document-jobs'] }); void qc.invalidateQueries({ queryKey: ['document-job'] }) } })
  if (jobs.isError) return <p role="alert">{t('Could not load report jobs. Refresh to try again.', 'โหลดงานรายงานไม่ได้ ลองรีเฟรชอีกครั้ง')}</p>
  if (!jobs.data?.items.length) return null
  const labels: Record<string, string> = { queued: t('Queued', 'รอสร้างรายงาน'), running: t('Creating and checking', 'กำลังสร้างและตรวจ'), done: t('Ready for your review', 'พร้อมให้คุณตรวจทาน'), failed: t('Needs attention', 'สร้างไม่สำเร็จ'), cancelled: t('Stopped', 'หยุดแล้ว') }
  return <section className="space-y-3 my-5" aria-label={t('Formal reports', 'รายงานทางการ')}>
    <h3 className="flex gap-2 items-center font-semibold"><FileText size={17} />{t('Formal reports', 'รายงานทางการ')}</h3>
    {jobs.data.items.map(job => <article key={job.id} className="rounded-xl border border-line p-4 space-y-3">
      <div className="flex justify-between flex-wrap gap-2"><strong>{job.title}</strong><span className="text-sm text-muted" role="status">{labels[job.status] ?? job.status}</span></div>
      <p className="text-sm text-muted">{job.formats.map(f => f.toUpperCase()).join(' · ')} · {job.template} v{job.template_version}</p>
      {job.brief && <p className="text-sm">{t('For', 'สำหรับ')} {job.brief.audience} · {job.brief.purpose}</p>}
      {job.revision && <p className="text-sm text-muted">{t('Edited sections', 'แก้เฉพาะส่วน')}: {job.revision.changes.map(change => change.after.title).join(' · ')} · {t('Original data preserved', 'ใช้ข้อมูลเดิม ไม่เปลี่ยนตัวเลข')}</p>}
      {job.company_template && <p className="text-sm text-muted">{t('Company template', 'แม่แบบบริษัท')}: {job.company_template.name}</p>}
      {['queued', 'running'].includes(job.status) && <><progress className="w-full" value={job.progress} max={100} aria-label={labels[job.status]} /><p className="text-sm">{job.message || t('You can continue chatting while this runs.', 'คุยต่อได้ระหว่างรอ ระบบจะสร้างรายงานให้ต่อ')}</p></>}
      {job.status === 'done' && <p className="text-sm">{t('Automatic data and rendering checks passed. Review the wording and appearance before sharing.', 'ผ่านการตรวจข้อมูลและการเรนเดอร์อัตโนมัติแล้ว โปรดตรวจเนื้อหาและหน้าตาก่อนส่งต่อ')}</p>}
      {job.error && <p role="alert" className="text-bad text-sm">{job.error}</p>}
      <div className="flex flex-wrap gap-2">
        <Link className="btn btn-sm" to={inspectionHref('chat', chat, 'job:' + job.id)}>ประวัติงานทั้งหมด</Link>
        {job.artifacts.map((a, i) => <button key={a.id} className="btn btn-sm" onClick={() => onOpen(a.id, a.version)}>{t('Open', 'เปิด')} {job.formats[i]?.toUpperCase()} · v{a.version} <ArrowRight size={14} /></button>)}
        <button className="btn btn-sm" onClick={() => { setSelected(job.id); setTechnical(false) }}>{t('Report details', 'รายละเอียดรายงาน')}</button>
        {['queued', 'running'].includes(job.status) && <button className="btn btn-sm" disabled={action.isPending} onClick={() => action.mutate({ id: job.id, name: 'cancel' })}>{t('Stop report', 'หยุดงานรายงาน')}</button>}
        {['failed', 'cancelled'].includes(job.status) && <button className="btn btn-sm" disabled={action.isPending} onClick={() => action.mutate({ id: job.id, name: 'retry' })}>{t('Retry with the same data', 'ลองใหม่ด้วยข้อมูลชุดเดิม')}</button>}
      </div>
    </article>)}
    {action.isError && <p role="alert" className="text-bad">{t('Could not change this job. Refresh and try again.', 'เปลี่ยนสถานะงานไม่ได้ ลองรีเฟรชแล้วทำอีกครั้ง')}</p>}
    {(offset > 0 || jobs.data.next_offset !== null) && <div className="flex gap-2"><button className="btn btn-sm" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 20))}><ArrowLeft size={14} />{t('Previous', 'ก่อนหน้า')}</button><button className="btn btn-sm" disabled={jobs.data.next_offset === null} onClick={() => setOffset(jobs.data!.next_offset!)}>{t('Older reports', 'รายงานเก่า')}<ArrowRight size={14} /></button></div>}
    <Modal open={!!selected} onClose={() => setSelected(undefined)} title={t('Report evidence and checks', 'ข้อมูลอ้างอิงและผลตรวจรายงาน')} width="max-w-3xl">
        <p className="text-sm mb-3">{t('Retry preserves this snapshot. Request a new report to use updated data.', 'การลองใหม่ใช้ข้อมูลชุดนี้ หากต้องการข้อมูลล่าสุดให้ขอรายงานใหม่')}</p>
        {detail.isError ? <p role="alert">{t('Could not load details.', 'โหลดรายละเอียดไม่ได้')}</p> : detail.data ? <>
          <p className="text-sm mb-2">{t('Template', 'แม่แบบ')}: {detail.data.template} · v{detail.data.template_version}</p>
          <p className="break-all text-xs text-muted mb-4">SHA-256: {detail.data.snapshot_id}</p>
          {detail.data.outline?.length ? <section className="mb-4"><h4 className="font-semibold">{t('Report outline', 'เนื้อหาในรายงาน')}</h4><ol className="text-sm mt-2 space-y-1 list-decimal pl-5">{detail.data.outline.map(section => <li key={section.id}>{section.title}</li>)}</ol></section> : null}
          <h4 className="font-semibold">{t('Sources used', 'ข้อมูลที่ใช้ในรายงาน')}</h4>
          <ul className="my-3 space-y-3 text-sm">{detail.data.sources.map((source, i) => <li key={i}><strong>{source.title}</strong><p>{source.scope}</p><p className="text-muted">{source.rows} {t('rows', 'แถว')} · {source.complete ? t('Complete for this scope', 'ครบตามขอบเขตนี้') : t('Partial data', 'ข้อมูลบางส่วน')} · {source.generated_at}</p></li>)}</ul>
          <h4 className="font-semibold">{t('Quality checks', 'ผลตรวจ')}</h4>
          {detail.data.revision && <section className="my-4 space-y-3"><h4 className="font-semibold">{t('What changed', 'สิ่งที่แก้จากเวอร์ชันก่อน')}</h4>{detail.data.revision.changes.map(change => <div key={change.section_id} className="grid gap-3 sm:grid-cols-2 text-sm border border-line rounded-lg p-3"><div><strong>{t('Before', 'ก่อนแก้')} · {change.before.title}</strong>{change.before.points.map((point, i) => <p key={i}>{point}</p>)}</div><div><strong>{t('After', 'หลังแก้')} · {change.after.title}</strong>{change.after.points.map((point, i) => <p key={i}>{point}</p>)}</div></div>)}<p className="text-sm text-muted">{t('Other sections and source data are unchanged. Page numbers may move when text reflows.', 'ส่วนอื่นและข้อมูลต้นทางคงเดิม เลขหน้าอาจเปลี่ยนเมื่อข้อความจัดหน้าใหม่')}</p></section>}
          {detail.data.quality ? <><p className="text-sm my-2">{t('Tables match the frozen data. All pages were rendered. Human review of the meaning and final layout is still required.', 'ตารางตรงกับข้อมูลที่บันทึกไว้ และสร้างตัวอย่างทุกหน้าแล้ว ยังต้องให้คนตรวจความหมายและหน้าตาสุดท้ายก่อนส่งต่อ')}</p>
            <ul className="my-3 space-y-2 text-sm">{Object.entries(detail.data.quality.files).map(([name, file]) => <li key={name}>{name} · {t(`${file.rendered_pages} of ${file.pages} pages rendered`, `สร้างตัวอย่างแล้ว ${file.rendered_pages}/${file.pages} หน้า`)}</li>)}</ul></> : <p className="my-3">{labels[detail.data.status] ?? detail.data.status}</p>}
          <div className="flex flex-wrap gap-2 my-4">{detail.data.files.filter(f => f.name.endsWith('.json')).map(f => <button className="btn btn-sm" key={f.id} onClick={() => { setDownloadError(false); void saveFile(f.id, f.name).catch(() => setDownloadError(true)) }}>{f.name.endsWith('snapshot.json') ? t('Download source snapshot', 'ดาวน์โหลดข้อมูลอ้างอิง') : t('Download check results', 'ดาวน์โหลดผลตรวจ')}</button>)}<button className="btn btn-sm" onClick={() => setTechnical(!technical)}>{technical ? t('Back to summary', 'กลับไปดูสรุป') : t('Inspect technical details', 'ดูรายละเอียดเชิงเทคนิค')}</button></div>
          {downloadError && <p role="alert">{t('Download failed. Try again.', 'ดาวน์โหลดไม่ได้ ลองอีกครั้ง')}</p>}
          {detail.data.error_detail && <pre className="text-xs whitespace-pre-wrap text-bad">{detail.data.error_detail}</pre>}
          {technical && <><h4>{t('Frozen sources and report specification', 'แหล่งข้อมูลและข้อกำหนดรายงานที่บันทึกไว้')}</h4><pre className="text-xs whitespace-pre-wrap break-words mt-3">{JSON.stringify(detail.data.snapshot, null, 2)}</pre></>}
        </> : <p>{t('Loading…', 'กำลังโหลด…')}</p>}

    </Modal>
  </section>
}
