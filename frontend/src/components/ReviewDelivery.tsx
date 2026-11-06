import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowLeft, Download, Check } from 'lucide-react'
import { api, type Me, type ReviewQuestions } from '../lib/api'
import { reviewToolUrl } from '../lib/reviewNavigation'
import { usePreferences } from '../lib/preferences'
import { Spinner } from './ui'
import { ReviewOutput } from './ReviewOutput'

export function ReviewDelivery({ q, me, outdated, returnTo, onDone }: { q: ReviewQuestions; me: Me; outdated: boolean; returnTo: string; onDone: () => void }) {
  const { t } = usePreferences(), qc = useQueryClient()
  const [checkedDataset, setCheckedDataset] = useState('')
  const r = useQuery({ queryKey: ['review-round', q.round_id], queryFn: () => api.round(q.round_id), refetchInterval: 2500 })
  const candidate = r.data?.datasets?.find(d => d.id === r.data?.summary.dataset_version_id && d.mapping_version_id === q.mapping_id)
  const data = useQuery({ queryKey: ['review-delivery', candidate?.id], queryFn: () => api.dataset(candidate!.id), enabled: !!candidate, refetchInterval: 2500 })
  const rows = useQuery({ queryKey: ['review-delivery-rows', candidate?.id], queryFn: () => api.rows(candidate!.id, { limit: 5 }), enabled: !!candidate })
  const ds = data.data
  const checked = !!ds && checkedDataset === ds.id
  const job = r.data?.jobs?.find(j => ['finish_review', 'transform_round', 'run_pipeline'].includes(j.kind))
  const running = job && ['queued', 'running'].includes(job.status)
  const refresh = () => { void qc.invalidateQueries({ queryKey: ['review-round', q.round_id] }); void qc.invalidateQueries({ queryKey: ['review-delivery'] }); void qc.invalidateQueries({ queryKey: ['review'] }); void qc.invalidateQueries({ queryKey: ['dashboard'] }) }
  const start = useMutation({ mutationFn: () => job?.status === 'failed' && job.can_retry ? api.retryJob(job.id) : api.transform(q.round_id, q.mapping_id), onSuccess: refresh })
  const finish = useMutation({ mutationFn: async () => {
    if (!ds) throw new Error('No result available')
    if (ds.status === 'draft') await api.approveDataset(ds.id)
    if (me.effective_roles.includes('approver')) return api.exportDataset(ds.id, 'json')
    return null
  }, onSettled: refresh })
  const artifact = ds?.exports?.find(e => e.fmt === 'json') || (finish.data?.dataset_version_id === ds?.id ? finish.data : null)
  const proposed = q.status === 'proposed', rejected = q.status === 'rejected'
  const errors = ds?.invalid_records || ds?.findings.filter(f => f.severity === 'error').length || 0
  const editor = reviewToolUrl(`/datasets/${ds?.id}?tab=findings`, returnTo)
  return <section className="card p-5 sm:p-7 space-y-5" aria-label={t('Result and download', 'ผลลัพธ์และดาวน์โหลด')}>
    {outdated || rejected || proposed ? <><h2 className="text-xl font-semibold">{t('This version is read-only', 'เวอร์ชันนี้ใช้ดูย้อนหลัง')}</h2><p className="text-sm text-muted">{t('Open the latest review to continue. Saved answers and decisions are in the history above.', 'เปิดเวอร์ชันล่าสุดเพื่อทำต่อ ดูคำตอบและการตัดสินใจเดิมได้จากประวัติด้านบน')}</p></>
      : running ? <><h2 className="text-xl font-semibold flex gap-3 items-center"><Spinner />{t('Preparing your file', 'กำลังเตรียมไฟล์ให้คุณ')}</h2><p className="text-sm">{t('Your answers and confirmation are saved. You may work on other files and return here later.', 'บันทึกคำตอบและการยืนยันแล้ว ไปทำไฟล์อื่นต่อได้ แล้วกลับมารับไฟล์ที่นี่')}</p><p className="text-sm text-muted" role="status">{job.status === 'queued' ? t('Waiting for an available worker', 'รอคิวประมวลผล') : t('Creating and checking the output', 'กำลังสร้างและตรวจผลลัพธ์')}</p></>
      : ds ? <>
        <div><h2 className="text-xl font-semibold flex items-center gap-2">{artifact ? <><Check size={22} className="text-ok" />{t('Your file is ready', 'ไฟล์พร้อมดาวน์โหลดแล้ว')}</> : t('Check the final output', 'ตรวจข้อมูลก่อนรับไฟล์')}</h2><p className="text-sm text-muted mt-2">{t(`${ds.row_count} records · data version ${ds.version}`, `${ds.row_count} รายการ · ข้อมูลเวอร์ชัน ${ds.version}`)}</p></div>
        {rows.data && <ReviewOutput records={rows.data.rows} fields={rows.data.columns} total={rows.data.total} />}
        {rows.isError && <p role="alert" className="text-bad">{t('Could not load the result sample.', 'โหลดตัวอย่างผลลัพธ์ไม่ได้')} <button className="btn btn-sm" onClick={() => void rows.refetch()}>{t('Retry', 'ลองใหม่')}</button></p>}
        {errors > 0 ? <div className="space-y-3"><p className="text-bad">{t(`${errors} checks still need correction. Download becomes available after correcting the data.`, `ยังมี ${errors} จุดที่ต้องแก้ ดาวน์โหลดได้เมื่อข้อมูลผ่านการตรวจแล้ว`)}</p><Link className="btn" to={editor}>{t('Open the data to correct it', 'เปิดแก้ข้อมูลที่มีปัญหา')}</Link></div> : artifact ? <a className="btn btn-primary" href={api.downloadUrl(artifact.id)}><Download size={16} />{t('Download JSON', 'ดาวน์โหลด JSON')}</a> : <div className="space-y-4">
          <p className="text-sm">{ds.status === 'draft' ? t('Field matching is confirmed. This delivery also requires confirmation of the generated data before export.', 'ยืนยันการจับคู่แล้ว งานนี้ต้องให้คนตรวจข้อมูลที่สร้างจริงก่อนส่งออกอีกครั้ง') : t('Data approved. Create the download file when ready.', 'ข้อมูลอนุมัติแล้ว สร้างไฟล์ดาวน์โหลดได้เลย')}</p>
          {ds.status === 'draft' && <label className="flex gap-2 text-sm"><input type="checkbox" checked={checked} onChange={e => setCheckedDataset(e.target.checked ? ds.id : '')} />{t('I checked the generated data.', 'ฉันตรวจข้อมูลที่สร้างแล้ว ถูกต้อง')}</label>}
          {me.effective_roles.includes(ds.status === 'draft' ? 'reviewer' : 'approver') ? <button className="btn btn-primary" disabled={finish.isPending || ds.status === 'draft' && (!checked || !rows.data)} onClick={() => finish.mutate()}>{finish.isPending ? <Spinner /> : <Check size={15} />}{ds.status === 'draft' ? me.effective_roles.includes('approver') ? t('Confirm data & prepare download', 'ยืนยันข้อมูลและเตรียมดาวน์โหลด') : t('Confirm data', 'ยืนยันข้อมูล') : t('Prepare JSON download', 'สร้างไฟล์ JSON สำหรับดาวน์โหลด')}</button> : <p className="text-sm text-warn">{t('Waiting for a person with approval/export permission.', 'รอผู้มีสิทธิ์ยืนยันข้อมูลหรือส่งออกไฟล์')}</p>}
        </div>}
        <div className="flex gap-2 flex-wrap"><Link className="btn btn-sm" to={reviewToolUrl(`/datasets/${ds.id}`, returnTo)}>{t('View / edit all records', 'ดูหรือแก้ข้อมูลทั้งหมด')}</Link><Link className="btn btn-sm" to={reviewToolUrl(`/datasets/${ds.id}?tab=diff`, returnTo)}>{t('Inspect changes and warnings', 'ดูสิ่งที่เปลี่ยนและข้อควรตรวจ')}</Link></div>
      </> : r.isError || data.isError ? <><p role="alert" className="text-bad">{t('Could not load the processing status.', 'โหลดสถานะการเตรียมไฟล์ไม่ได้')}</p><button className="btn" onClick={refresh}>{t('Retry', 'ลองใหม่')}</button></>
      : !r.data || candidate && data.isPending ? <p className="flex gap-2"><Spinner />{t('Checking output status…', 'กำลังตรวจสถานะผลลัพธ์…')}</p>
      : <><h2 className="text-xl font-semibold">{job?.status === 'failed' ? t('File preparation needs attention', 'การเตรียมไฟล์ยังไม่สำเร็จ') : t('Ready to prepare the output', 'พร้อมสร้างผลลัพธ์')}</h2><p className="text-sm">{t('Your confirmation is saved. Preparing this output uses the confirmed mapping without asking the model to map again.', 'การยืนยันเก็บไว้แล้ว การสร้างผลลัพธ์จะใช้การจับคู่ที่ตรวจไว้ ไม่เรียกโมเดลมาจับคู่ใหม่')}</p><button className="btn btn-primary" disabled={start.isPending || !me.effective_roles.includes('reviewer')} onClick={() => start.mutate()}>{start.isPending ? <Spinner /> : null}{job?.status === 'failed' ? t('Try preparing the file again', 'ลองเตรียมไฟล์อีกครั้ง') : t('Prepare file', 'เตรียมไฟล์')}</button></>}
    {(start.error || finish.error) && <div role="alert" className="text-bad text-sm space-y-2"><p>{(start.error || finish.error)?.message}</p>{ds && <Link className="btn btn-sm" to={reviewToolUrl(`/datasets/${ds.id}?tab=diff`, returnTo)}>{t('Review the points holding this file', 'เปิดตรวจจุดที่ยังไม่ผ่าน')}</Link>}</div>}
    <footer className="border-t border-line pt-4"><button className="btn btn-ghost" onClick={onDone}><ArrowLeft size={15} />{t('Back to the review list', 'กลับรายการที่รอตรวจ')}</button></footer>
  </section>
}
