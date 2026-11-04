import { Localized } from '../lib/locale'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { FileSpreadsheet, Upload, X, Plus } from 'lucide-react'
import { api, waitForJob, type Job } from '../lib/api'
import { fmtBytes, cn } from '../lib/utils'
import { PageHeader, Section, Spinner, Modal } from '../components/ui'
import { NextStepCell } from './Overview'
import { useToast } from '../lib/toast'
import { dateInZone, usePreferences } from '../lib/preferences'
import { Workflow } from '../components/Workflow'
import { WorkRounds } from '../components/WorkRounds'
import { FileMenu } from '../components/FileMenu'
import { CLOSED_STATUSES } from '../components/RoundMenu'
import { refusedNames, splitReadable, UPLOAD_ACCEPT, UPLOAD_TYPES } from '../lib/uploads'

export function Imports() {
  const { prefs, t, date } = usePreferences()
  const [params, setParams] = useSearchParams()
  const timezone = params.get('timezone') || prefs.timezone
  const filters = Object.fromEntries(['status', 'customer_id', 'search', 'start', 'end'].map(k => [k, params.get(k) || undefined]))
  const offset = Math.max(0, Number(params.get('offset')) || 0)
  const invalid = !!(filters.start && filters.end && filters.start > filters.end)
  const filesView = params.get('view') === 'files' || ['search', 'status', 'customer_id', 'start', 'end', 'offset'].some(k => params.has(k))
  const query = useQuery({ queryKey: ['history', filters, offset, timezone], queryFn: () => api.history({ ...filters, offset, timezone }), enabled: !invalid && filesView, refetchInterval: filesView ? 10000 : false, placeholderData: keepPreviousData })
  const { data: customers } = useQuery({ queryKey: ['customers'], queryFn: api.customers })
  const [searchText, setSearchText] = useState(filters.search ?? '')
  useEffect(() => { setSearchText(filters.search ?? '') }, [filters.search])
  useEffect(() => {
    if (searchText === (params.get('search') || '')) return
    const timer = setTimeout(() => setParams(current => { const next = new URLSearchParams(current); next.delete('offset'); searchText ? next.set('search', searchText) : next.delete('search'); return next }, { replace: true }), 300)
    return () => clearTimeout(timer)
  }, [searchText, params, setParams])
  const change = (values: Record<string, string>) => setParams(current => {
    const next = new URLSearchParams(current)
    next.delete('offset')
    Object.entries(values).forEach(([k, v]) => v ? next.set(k, v) : next.delete(k))
    return next
  }, { replace: true })
  const statuses = [['', t('All statuses', 'ทุกสถานะ')], ['review', t('Needs answers', 'รอตอบคำถาม')], ['blocked', t('Check file', 'ต้องตรวจไฟล์')], ['transformed', t('Check results', 'ตรวจผลลัพธ์')], ['closed', t('Cancelled, moved or replaced', 'ยกเลิก ย้าย หรือถูกแทนที่')], ['approved_data', t('Ready to download', 'พร้อมดาวน์โหลด')], ['published', t('Downloaded', 'ส่งออกแล้ว')], ['failed', t('Processing failed', 'ประมวลผลไม่สำเร็จ')], ['uploaded', t('Reading files', 'กำลังอ่านไฟล์')], ['profiled', t('Matching fields', 'กำลังจับคู่ข้อมูล')], ['approved', t('Ready to create results', 'พร้อมสร้างผลลัพธ์')], ['auto_approved', t('Matched automatically', 'จับคู่ผ่านอัตโนมัติ')], ['rejected', t('Needs correction', 'ต้องแก้ไข')]]
  if (!filesView) return <WorkRounds />
  return <>
    <PageHeader title={t('Upload history', 'ประวัติไฟล์')} subtitle={t('Find past deliveries by the date the files were received.', 'ค้นหาไฟล์ย้อนหลังตามวันที่รับไฟล์เข้าระบบ')} actions={<Link to="/imports/new" className="btn btn-primary"><Upload size={16} />{t('Upload files', 'อัปโหลดไฟล์')}</Link>} />
    <div className="flex gap-2 mb-4"><Link className="btn btn-ghost" to="/imports">{t('Work rounds', 'ตามรอบงาน')}</Link><span className="btn bg-signal-soft" aria-current="page">{t('All files', 'ไฟล์ทั้งหมด')}</span></div>
    <div className="card p-4 mb-5 space-y-4">
      <div className="flex flex-wrap gap-2">{[[0, t('Today', 'วันนี้')], [6, t('Last 7 days', '7 วันล่าสุด')], [29, t('Last 30 days', '30 วันล่าสุด')]].map(([days, label]) => <button key={days} className="btn btn-sm" onClick={() => change({ start: dateInZone(timezone, Number(days)), end: dateInZone(timezone) })}>{label}</button>)}<button className="btn btn-ghost btn-sm" onClick={() => { setSearchText(''); setParams({ view: 'files' }) }}>{t('Clear filters', 'ล้างตัวกรอง')}</button></div>
      <div className="grid sm:grid-cols-2 xl:grid-cols-5 gap-3">
        <label className="grid gap-1 text-sm">{t('Uploaded from', 'อัปโหลดตั้งแต่')}<input className="input min-w-0" type="date" value={filters.start ?? ''} onChange={e => change({ start: e.target.value })} /></label>
        <label className="grid gap-1 text-sm">{t('Through', 'จนถึงวันที่')}<input className="input min-w-0" type="date" value={filters.end ?? ''} onChange={e => change({ end: e.target.value })} /></label>
        <label className="grid gap-1 text-sm">{t('Customer', 'ลูกค้า')}<select className="input min-w-0" value={filters.customer_id ?? ''} onChange={e => change({ customer_id: e.target.value })}><option value="">{t('All customers', 'ลูกค้าทั้งหมด')}</option>{customers?.map(c => <option key={c.id} value={c.id}>{c.code} · {c.name}</option>)}</select></label>
        <label className="grid gap-1 text-sm">{t('Status', 'สถานะ')}<select className="input min-w-0" value={filters.status ?? ''} onChange={e => change({ status: e.target.value })}>{statuses.map(([v, label]) => <option key={v} value={v}>{label}</option>)}</select></label>
        <label className="grid gap-1 text-sm">{t('File, delivery or customer', 'ไฟล์ ชุดอัปโหลด หรือลูกค้า')}<input className="input min-w-0" maxLength={200} value={searchText} placeholder={t('Search…', 'ค้นหา…')} onChange={e => setSearchText(e.target.value)} /></label>
      </div>
      <p className="text-xs text-muted">{t('Upload dates use', 'วันที่อัปโหลดใช้เขตเวลา')} {timezone} · {t('End date includes the whole day.', 'รวมทั้งวันของวันที่สิ้นสุด')}</p>
      {invalid && <p role="alert" className="text-bad">{t('End date must be on or after the start date.', 'วันที่สิ้นสุดต้องไม่ก่อนวันที่เริ่มต้น')}</p>}
    </div>
    <Section title={t(`${query.data?.total ?? 0} deliveries`, `${query.data?.total ?? 0} รอบนำเข้า`)} actions={<span className="h-5 min-w-40 text-xs text-muted flex items-center justify-end gap-2" role="status">{!invalid && query.isFetching && <><Spinner />{t('Updating results…', 'กำลังอัปเดตผล…')}</>}</span>}>
      {query.error && <p role="alert" className="p-4 text-bad">{t('Could not load history.', 'โหลดประวัติไม่ได้')} <button className="btn btn-sm" onClick={() => void query.refetch()}>{t('Try again', 'ลองใหม่')}</button></p>}
      {!invalid && <table className="table"><thead><tr><th>{t('Customer / file', 'ลูกค้า / ไฟล์')}</th><th>{t('Next step', 'ขั้นตอนถัดไป')}</th><th>{t('Uploaded', 'วันที่อัปโหลด')}</th></tr></thead><tbody>
        {query.isPending && <tr><td colSpan={3} className="p-8 text-center text-muted">{t('Loading files…', 'กำลังโหลดไฟล์…')}</td></tr>}
        {query.data?.items.map(r => <tr key={r.id}><td className="max-w-[480px]"><Link to={`/rounds/${r.id}`} className="font-medium text-navy hover:underline break-words">{r.customer?.code} · {r.label || r.customer?.name}</Link><div className="text-xs text-muted mt-1 space-y-1">{r.files?.map(f => <div className="flex items-center gap-2" key={f.id}><span className="flex-1 min-w-0 break-words">{f.name}</span><FileMenu roundId={r.id} customerId={r.customer_id} status={r.status} file={f} fileCount={r.files?.length} /></div>)}{CLOSED_STATUSES.includes(r.status) && r.closed?.reason && <p>“{r.closed.reason}”</p>}</div></td><td><NextStepCell r={r} />{r.successor_id && <Link className="block text-xs underline mt-1" to={`/rounds/${r.successor_id}`}>{t('Open the current round', 'เปิดรอบที่ใช้อยู่')}</Link>}</td><td className="whitespace-nowrap text-muted">{date(r.uploaded_at)}</td></tr>)}
        {query.data?.total === 0 && <tr><td colSpan={3} className="text-center p-8 text-muted">{t('No files match these filters.', 'ไม่พบไฟล์ที่ตรงกับตัวกรองนี้')}</td></tr>}
      </tbody></table>}
      <div className="p-4 flex items-center justify-between gap-3 border-t border-line"><button className="btn btn-sm" disabled={offset === 0} onClick={() => change({ offset: String(Math.max(0, offset - 50)) })}>{t('Previous', 'ก่อนหน้า')}</button><span className="text-sm text-muted">{t('Page', 'หน้า')} {Math.floor(offset / 50) + 1}</span><button className="btn btn-sm" disabled={!query.data || offset + 50 >= query.data.total} onClick={() => change({ offset: String(offset + 50) })}>{t('Next', 'ถัดไป')}</button></div>
    </Section>
  </>
}

export function NewImport() {
  const { t } = usePreferences()
  const { data: customers } = useQuery({ queryKey: ['customers'], queryFn: api.customers })
  const [customerId, setCustomerId] = useState('')
  const [label, setLabel] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [job, setJob] = useState<Job | null>(null)
  const [newCust, setNewCust] = useState(false)
  const [form, setForm] = useState({ code: '', name: '', erp_type: '' })
  const [replaces, setReplaces] = useState('')
  const [weekAgo] = useState(() => new Date(Date.now() - 7 * 86400000).toISOString())
  const { data: customerRounds } = useQuery({ queryKey: ['rounds', 'customer', customerId], queryFn: () => api.rounds({ customer_id: customerId }), enabled: !!customerId })
  const inputRef = useRef<HTMLInputElement>(null)
  const nav = useNavigate()
  const qc = useQueryClient()
  const toast = useToast()

  const add = useCallback((fs: File[]) => {
    const { readable, refused } = splitReadable(fs)
    if (refused.length) {
      const { names, more } = refusedNames(refused)
      toast.push({ kind: 'bad', title: t('FORMA cannot read these files', 'FORMA อ่านไฟล์เหล่านี้ไม่ได้'), detail: `${names}${more ? t(` and ${more} more`, ` และอีก ${more} ไฟล์`) : ''} · ${t('Accepted', 'รับ')} ${UPLOAD_TYPES.join(' ')}` })
    }
    setFiles((xs) => [...xs, ...readable])
  }, [t, toast])
  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    add(Array.from(e.dataTransfer.files))
  }, [add])

  const createCustomer = useMutation({
    mutationFn: () => api.createCustomer({ code: form.code, name: form.name || form.code, erp_type: form.erp_type || undefined }),
    onSuccess: (c) => { qc.invalidateQueries({ queryKey: ['customers'] }); setCustomerId(c.id); setNewCust(false); toast.push({ kind: 'ok', title: `Customer ${c.code} created` }) },
    onError: (e: Error) => toast.push({ kind: 'bad', title: 'Could not create customer', detail: e.message }),
  })

  const run = useMutation({
    mutationFn: async () => {
      if (replaces) {
        const r = await api.replaceRound(replaces, files, t('A corrected delivery from the customer', 'ไฟล์แก้ไขจากลูกค้า'), label || undefined)
        return { round: r, job: await waitForJob(r.job_id, setJob) }
      }
      const r = await api.createRound({ customer_id: customerId, label: label || undefined })
      await api.uploadFiles(r.id, files)
      const { job_id } = await api.runPipeline(r.id, { actor: 'operator' })
      const j = await waitForJob(job_id, setJob)
      return { round: r, job: j }
    },
    onSuccess: ({ round, job }) => {
      qc.invalidateQueries()
      if (job.status === 'failed') toast.push({ kind: 'bad', title: 'Pipeline failed', detail: job.error?.split('\n')[0] })
      nav(`/rounds/${round.id}`)
    },
    onError: (e: Error) => toast.push({ kind: 'bad', title: 'Import failed', detail: e.message }),
  })

  const selected = useMemo(() => customers?.find((c) => c.id === customerId), [customers, customerId])
  const canRun = customerId && files.length > 0 && !run.isPending
  const recent = useMemo(() => (customerRounds ?? []).filter(r => !CLOSED_STATUSES.includes(r.status) && r.created_at.localeCompare(weekAgo) > 0).slice(0, 3), [customerRounds, weekAgo])

  return (
    <>
      <PageHeader title={t('Combine files for one customer', 'รวมไฟล์ของลูกค้ารายเดียว')} subtitle={t('Use related files together in one import, such as products, prices and stock. FORMA matches their fields and relationships to produce one dataset for review.', 'ใช้ไฟล์ที่เกี่ยวข้องกันในงานนำเข้าเดียว เช่น สินค้า ราคา และสต็อก ระบบจับคู่คอลัมน์และความสัมพันธ์เพื่อสร้างข้อมูลชุดเดียวให้ตรวจ')} />
      <Workflow />
      <p className="text-muted mb-4">{t('Use only files for the same customer and delivery. If each file should produce its own result,', 'ใช้เฉพาะไฟล์ของลูกค้ารายเดียวและข้อมูลชุดเดียวกัน หากแต่ละไฟล์ต้องมีผลลัพธ์แยกกัน ให้')} <Link to="/imports/new" className="text-signal underline underline-offset-4">{t('upload files separately', 'อัปโหลดแบบแยกไฟล์')}</Link></p>
      <div className="grid lg:grid-cols-[1fr_360px] gap-4">
        <div className="flex flex-col gap-4">
          <div
            onDragOver={(e) => e.preventDefault()} onDrop={onDrop} onClick={() => inputRef.current?.click()} role="button" tabIndex={0} aria-label="Choose files" onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); inputRef.current?.click() } }}
            className={cn('card border-dashed p-10 text-center cursor-pointer hover:border-signal/50 transition-colors', files.length && 'py-6')}
          >
            <input ref={inputRef} type="file" multiple accept={UPLOAD_ACCEPT} className="hidden" onChange={(e) => { add(Array.from(e.target.files ?? [])); e.target.value = '' }} />
            <Upload className="mx-auto text-muted" size={28} />
            <div className="mt-2 font-medium"><Localized>Drop Excel or CSV files here</Localized></div>
            <div className="text-muted text-[12.5px]">{t('Excel or CSV · e.g. products.xlsx + prices.csv + stock.csv for the same customer', 'Excel หรือ CSV · เช่น products.xlsx + prices.csv + stock.csv ของลูกค้ารายเดียว')}</div>
          </div>
          {files.length > 0 && (
            <Section title={`${files.length} file${files.length > 1 ? 's' : ''}`}>
              <ul>
                {files.map((f, i) => (
                  <li key={i} className="flex items-center gap-3 px-4 py-2 border-b border-line last:border-0">
                    <FileSpreadsheet size={16} className="text-muted" />
                    <span className="flex-1 min-w-0 truncate" title={f.name}>{f.name}</span>
                    <span className="text-muted tnum text-[12px]">{fmtBytes(f.size)}</span>
                    <button className="btn btn-ghost btn-sm" onClick={() => setFiles((xs) => xs.filter((_, j) => j !== i))} aria-label="Remove"><X size={13} /></button>
                  </li>
                ))}
              </ul>
            </Section>
          )}
        </div>
        <div className="flex flex-col gap-4">
          <Section title="Customer">
            <div className="p-4 flex flex-col gap-3">
              <select className="input" value={customerId} onChange={(e) => { setCustomerId(e.target.value); setReplaces('') }}>
                <option value="">{t('Select a customer…', 'เลือกลูกค้า…')}</option>
                {customers?.map((c) => <option key={c.id} value={c.id}>{c.code} — {c.name}</option>)}
              </select>
              <button className="btn btn-ghost btn-sm self-start" onClick={() => setNewCust(true)}><Plus size={13} />{t('New customer', 'เพิ่มลูกค้า')}</button>
              {selected && (
                <div className="text-[12.5px] text-muted border-t border-line pt-3">
                  {selected.has_approved_mapping ? <span className="text-ok">{t('An approved mapping is available. FORMA checks compatibility and validates the new data before applying the approval policy.', 'มีการจับคู่ที่เคยอนุมัติแล้ว ระบบจะตรวจรูปแบบและข้อมูลใหม่ก่อนพิจารณาตามกฎอนุมัติ')}</span> : <span>{t('No approved mapping yet. Review the proposed field matching before using the results.', 'ยังไม่มีการจับคู่ที่อนุมัติ ต้องตรวจการจับคู่คอลัมน์ก่อนนำผลลัพธ์ไปใช้')}</span>}
                </div>
              )}
              {recent.length > 0 && <fieldset className="grid gap-2 text-[13px] border-t border-line pt-3">
                <legend className="font-medium">{t(`${selected?.code} had a round in the last 7 days. This file is:`, `${selected?.code} เพิ่งมีรอบใน 7 วันที่ผ่านมา ไฟล์นี้คือ?`)}</legend>
                <label className="flex items-start gap-2"><input type="radio" className="mt-1" name="replaces" checked={!replaces} onChange={() => setReplaces('')} /><span>{t('A new round', 'รอบใหม่')}<small className="block text-muted">{t('A new delivery, unrelated to the earlier one', 'ส่งของใหม่ ไม่เกี่ยวกับรอบเดิม')}</small></span></label>
                {recent.map(r => <label key={r.id} className="flex items-start gap-2"><input type="radio" className="mt-1" name="replaces" checked={replaces === r.id} onChange={() => setReplaces(r.id)} /><span className="min-w-0 break-words">{t(`Replaces ${r.label ?? r.id}`, `แทน ${r.label ?? r.id}`)}<small className="block text-muted">{t('A correction; its old JSON is withdrawn', 'ไฟล์แก้ไขของรอบเดิม JSON เก่าจะถูกถอน')}</small></span></label>)}
              </fieldset>}
              <label className="grid gap-1 text-[13px]">{t('Import name (optional)', 'ชื่อชุดนำเข้า (ไม่บังคับ)')}<input className="input" placeholder={t('e.g. Products, prices and stock — October', 'เช่น สินค้า ราคา และสต็อก เดือนตุลาคม')} value={label} onChange={(e) => setLabel(e.target.value)} /></label>
            </div>
          </Section>
          <button className="btn btn-primary h-10 justify-center text-[14px]" disabled={!canRun} onClick={() => run.mutate()}>
            {run.isPending ? <><Spinner className="border-t-white" /> {job?.message ?? t('Uploading…', 'กำลังอัปโหลด…')}</> : t('Upload and process together', 'อัปโหลดและประมวลผลร่วมกัน')}
          </button>
          {run.isPending && job && (
            <div className="card p-3 text-[12.5px]">
              <div className="flex justify-between"><span>{job.message ?? job.status}</span><span className="tnum text-muted">{job.progress}%</span></div>
              <div className="h-1 bg-line rounded-full mt-2 overflow-hidden"><div className="h-full bg-signal transition-all" style={{ width: `${job.progress}%` }} /></div>
            </div>
          )}
          <div className="card p-3.5 text-[12.5px] leading-relaxed">
            <div className="heading text-[13px]">{t('What happens next', 'หลังอัปโหลด ระบบทำอะไรต่อ')}</div>
            <ol className="mt-1.5 flex flex-col gap-1 text-ink-2 list-decimal pl-4">
              <li>{t('Read the source files and check their structure against approved formats.', 'อ่านไฟล์ต้นทางและตรวจโครงสร้างกับรูปแบบที่เคยอนุมัติ')}</li>
              <li>{t('Match fields and link related tables using the mapping rules, such as matching prices to products by SKU.', 'จับคู่คอลัมน์และเชื่อมตารางตามกฎการจับคู่ เช่น เชื่อมราคากับสินค้าด้วยรหัสสินค้า')}</li>
              <li>{t('Validate the combined dataset. If information is unclear, ask you to review before using the results.', 'ตรวจข้อมูลชุดที่สร้างขึ้น หากข้อมูลไม่ชัดเจน จะให้คุณตรวจและตอบคำถามก่อนนำผลลัพธ์ไปใช้')}</li>
            </ol>
          </div>
        </div>
      </div>

      <Modal open={newCust} onClose={() => setNewCust(false)} title="New customer" footer={<><button className="btn" onClick={() => setNewCust(false)}><Localized>Cancel</Localized></button><button className="btn btn-primary" disabled={!form.code || createCustomer.isPending} onClick={() => createCustomer.mutate()}>Create</button></>} width="max-w-md">
        <div className="flex flex-col gap-3">
          <label className="text-[12.5px] text-muted">Code<input className="input mt-1" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} placeholder="e.g. C123" /></label>
          <label className="text-[12.5px] text-muted">Name<input className="input mt-1" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Company name" /></label>
          <label className="text-[12.5px] text-muted">ERP (optional)<input className="input mt-1" value={form.erp_type} onChange={(e) => setForm({ ...form, erp_type: e.target.value })} placeholder="SAP, Express, Odoo, …" /></label>
        </div>
      </Modal>
    </>
  )
}
