import { useCallback, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { FileSpreadsheet, UploadCloud, X, Plus } from 'lucide-react'
import { api, waitForJob, type Job } from '../lib/api'
import { fmtBytes, fmtInt, timeAgo, cn } from '../lib/utils'
import { PageHeader, Section, Badge, Spinner, Modal } from '../components/ui'
import { NextStepCell } from './Overview'
import { useToast } from '../lib/toast'

export function Imports() {
  const [status, setStatus] = useState('')
  const { data } = useQuery({ queryKey: ['rounds', status], queryFn: () => api.rounds({ status: status || undefined }), refetchInterval: 8000 })
  const { data: uploads } = useQuery({ queryKey: ['uploads'], queryFn: api.uploads, refetchInterval: 5000 })
  const nav = useNavigate()
  const statuses: [string, string][] = [['', 'All'], ['review', 'Check mapping'], ['blocked', 'Blocked'], ['approved', 'Create data'], ['transformed', 'Check data'], ['approved_data', 'Ready to export'], ['published', 'Exported'], ['failed', 'Failed']]
  return (
    <>
      <PageHeader title="Imports" subtitle="Every delivery from every customer, newest first." hint="ทุกไฟล์ที่ลูกค้าส่งมา คอลัมน์ Next step บอกว่าต้องทำอะไรต่อ — กดที่แถวเพื่อเปิดรอบนั้น" actions={<Link to="/imports/new" className="btn btn-primary btn-lg"><UploadCloud size={15} /> Upload files</Link>} />
      {!!uploads?.length && (
        <Section className="mb-5" title="Recent uploads" hint="การอัปโหลดหลายไฟล์ล่าสุด — กดเพื่อดูแต่ละไฟล์และดาวน์โหลด JSON">
          <ul>
            {uploads.slice(0, 5).map((u) => {
              const ready = u.counts.ready ?? 0
              const need = (u.counts.needs_customer ?? 0) + (u.counts.needs_review ?? 0) + (u.counts.needs_data_check ?? 0)
              return (
                <li key={u.id} className="border-b border-line last:border-0">
                  <Link to={`/uploads/${u.id}`} className="flex items-center gap-4 px-4 py-2.5 hover:bg-surface-2/60">
                    <span className="min-w-0 flex-1"><span className="block font-semibold text-navy truncate">{u.label}</span><span className="block text-[11.5px] text-muted">{u.total} files · {timeAgo(u.created_at)}</span></span>
                    <span className="w-40 h-1.5 rounded-full bg-line overflow-hidden"><span className="block h-full" style={{ width: `${(ready / Math.max(1, u.total)) * 100}%`, backgroundImage: 'var(--gradient-brand)' }} /></span>
                    <span className="text-[12px] tnum w-24 text-right"><b className="text-ok">{ready}</b> JSON ready</span>
                    {u.stuck > 0 ? <Badge tone="bad">stuck — resume</Badge> : need > 0 ? <Badge tone="warn">{need} need you</Badge> : !u.done ? <Badge>working…</Badge> : <Badge tone="ok">done</Badge>}
                  </Link>
                </li>
              )
            })}
          </ul>
        </Section>
      )}
      <div className="flex items-center gap-1.5 mb-4 flex-wrap">
        {statuses.map(([s, label]) => (
          <button key={s} onClick={() => setStatus(s)} className={cn('btn btn-sm rounded-full', status === s ? 'bg-signal-soft border-signal/40 text-navy' : 'btn-ghost text-ink-2')}>{label}</button>
        ))}
      </div>
      <Section title={`${fmtInt(data?.length)} rounds`}>
        <table className="table">
          <thead><tr><th>Customer</th><th>Round</th><th>Files</th><th>Next step</th><th>Format</th><th className="text-right">Rows</th><th className="text-right">Updated</th></tr></thead>
          <tbody>
            {data?.map((r) => (
              <tr key={r.id} className="row-link" onClick={() => nav(`/rounds/${r.id}`)}>
                <td><span className="font-semibold text-navy">{r.customer?.code}</span> <span className="text-muted">{r.customer?.name}</span></td>
                <td>{r.label}</td>
                <td className="text-muted max-w-[260px] truncate" title={r.files?.map((f) => f.name).join(', ')}>{r.files?.map((f) => f.name).join(', ')}</td>
                <td><NextStepCell r={r} /></td>
                <td>{r.summary.drift ? <Badge tone={r.summary.drift === 'identical' ? 'ok' : r.summary.drift === 'unrelated_file' ? 'bad' : 'warn'}>{r.summary.drift.replace('_', ' ')}</Badge> : <span className="text-muted">{r.summary.has_previous_approval === false ? 'first time' : '–'}</span>}</td>
                <td className="text-right tnum">{fmtInt(r.summary.rows)}</td>
                <td className="text-right text-muted">{timeAgo(r.updated_at)}</td>
              </tr>
            ))}
            {data && data.length === 0 && <tr><td colSpan={7} className="text-center text-muted py-8">No rounds match this filter.</td></tr>}
          </tbody>
        </table>
      </Section>
    </>
  )
}

export function NewImport() {
  const { data: customers } = useQuery({ queryKey: ['customers'], queryFn: api.customers })
  const [customerId, setCustomerId] = useState('')
  const [label, setLabel] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [job, setJob] = useState<Job | null>(null)
  const [newCust, setNewCust] = useState(false)
  const [form, setForm] = useState({ code: '', name: '', erp_type: '' })
  const inputRef = useRef<HTMLInputElement>(null)
  const nav = useNavigate()
  const qc = useQueryClient()
  const toast = useToast()

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    const fs = Array.from(e.dataTransfer.files).filter((f) => /\.(xlsx|xlsm|csv|txt|tsv)$/i.test(f.name))
    setFiles((xs) => [...xs, ...fs])
  }, [])

  const createCustomer = useMutation({
    mutationFn: () => api.createCustomer({ code: form.code, name: form.name || form.code, erp_type: form.erp_type || undefined }),
    onSuccess: (c) => { qc.invalidateQueries({ queryKey: ['customers'] }); setCustomerId(c.id); setNewCust(false); toast.push({ kind: 'ok', title: `Customer ${c.code} created` }) },
    onError: (e: Error) => toast.push({ kind: 'bad', title: 'Could not create customer', detail: e.message }),
  })

  const run = useMutation({
    mutationFn: async () => {
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

  return (
    <>
      <PageHeader title="New import" subtitle="Drop the files a customer sent. FORMA profiles them, finds or proposes a mapping, and tells you whether anything needs a decision." hint="1) ลากไฟล์มาวาง  2) เลือกลูกค้า  3) กด Upload and run — ระบบจะพาไปหน้ารอบนั้นและบอกขั้นต่อไป" />
      <div className="grid lg:grid-cols-[1fr_360px] gap-4">
        <div className="flex flex-col gap-4">
          <div
            onDragOver={(e) => e.preventDefault()} onDrop={onDrop} onClick={() => inputRef.current?.click()}
            className={cn('card border-dashed p-10 text-center cursor-pointer hover:border-signal/50 transition-colors', files.length && 'py-6')}
          >
            <input ref={inputRef} type="file" multiple accept=".xlsx,.xlsm,.csv,.txt,.tsv" className="hidden" onChange={(e) => setFiles((xs) => [...xs, ...Array.from(e.target.files ?? [])])} />
            <UploadCloud className="mx-auto text-muted" size={28} />
            <div className="mt-2 font-medium">Drop Excel or CSV files here</div>
            <div className="text-muted text-[12.5px]">.xlsx · .xlsm · .csv — several files per round are fine (products, prices, stock, promotions)</div>
          </div>
          {files.length > 0 && (
            <Section title={`${files.length} file${files.length > 1 ? 's' : ''}`}>
              <ul>
                {files.map((f, i) => (
                  <li key={i} className="flex items-center gap-3 px-4 py-2 border-b border-line last:border-0">
                    <FileSpreadsheet size={16} className="text-muted" />
                    <span className="flex-1 truncate">{f.name}</span>
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
              <select className="input" value={customerId} onChange={(e) => setCustomerId(e.target.value)}>
                <option value="">Select a customer…</option>
                {customers?.map((c) => <option key={c.id} value={c.id}>{c.code} — {c.name}</option>)}
              </select>
              <button className="btn btn-ghost btn-sm self-start" onClick={() => setNewCust(true)}><Plus size={13} /> New customer</button>
              {selected && (
                <div className="text-[12.5px] text-muted border-t border-line pt-3">
                  {selected.has_approved_mapping ? <span className="text-ok">Has an approved mapping — unchanged formats are approved automatically.</span> : <span>First import for this customer — the proposed mapping will go to review.</span>}
                </div>
              )}
              <input className="input" placeholder="Round label (optional, e.g. Oct 2026 wave 2)" value={label} onChange={(e) => setLabel(e.target.value)} />
            </div>
          </Section>
          <button className="btn btn-primary h-10 justify-center text-[14px]" disabled={!canRun} onClick={() => run.mutate()}>
            {run.isPending ? <><Spinner className="border-t-white" /> {job?.message ?? 'Uploading…'}</> : 'Upload and run'}
          </button>
          {run.isPending && job && (
            <div className="card p-3 text-[12.5px]">
              <div className="flex justify-between"><span>{job.message ?? job.status}</span><span className="tnum text-muted">{job.progress}%</span></div>
              <div className="h-1 bg-line rounded-full mt-2 overflow-hidden"><div className="h-full bg-signal transition-all" style={{ width: `${job.progress}%` }} /></div>
            </div>
          )}
          <div className="card p-3.5 text-[12.5px] leading-relaxed">
            <div className="heading text-[13px]">What happens next</div>
            <ol className="mt-1.5 flex flex-col gap-1 text-ink-2 list-decimal pl-4">
              <li>Every sheet is read and compared with this customer's approved format <span className="text-muted">— อ่านทุกชีตและเทียบกับ format เดิม</span></li>
              <li>A mapping is reused, repaired or proposed <span className="text-muted">— ใช้ mapping เดิม ซ่อม หรือเสนอใหม่</span></li>
              <li>Policy approves it, or asks you to check it <span className="text-muted">— ผ่านอัตโนมัติ หรือส่งให้คนตรวจ</span></li>
            </ol>
          </div>
        </div>
      </div>

      <Modal open={newCust} onClose={() => setNewCust(false)} title="New customer" footer={<><button className="btn" onClick={() => setNewCust(false)}>Cancel</button><button className="btn btn-primary" disabled={!form.code || createCustomer.isPending} onClick={() => createCustomer.mutate()}>Create</button></>} width="max-w-md">
        <div className="flex flex-col gap-3">
          <label className="text-[12.5px] text-muted">Code<input className="input mt-1" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} placeholder="e.g. C123" /></label>
          <label className="text-[12.5px] text-muted">Name<input className="input mt-1" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Company name" /></label>
          <label className="text-[12.5px] text-muted">ERP (optional)<input className="input mt-1" value={form.erp_type} onChange={(e) => setForm({ ...form, erp_type: e.target.value })} placeholder="SAP, Express, Odoo, …" /></label>
        </div>
      </Modal>
    </>
  )
}
