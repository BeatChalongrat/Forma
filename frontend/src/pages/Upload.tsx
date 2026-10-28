import { useCallback, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowRight, CircleAlert, CircleCheck, Download, FileJson, FileSpreadsheet, Loader2, UploadCloud, UserPlus, X } from 'lucide-react'
import { api, type BatchItem, type BatchItemState } from '../lib/api'
import { cn, fmtBytes, fmtDate, fmtInt } from '../lib/utils'
import { Badge, PageHeader, Section, Spinner } from '../components/ui'
import { useToast } from '../lib/toast'

const ACCEPT = /\.(xlsx|xlsm|csv|txt|tsv)$/i

/** Upload many files at once: each is matched to its customer by format and becomes one JSON (ADR-027). */
export function BulkUpload() {
  const [files, setFiles] = useState<File[]>([])
  const [label, setLabel] = useState('')
  const [auto, setAuto] = useState(true)
  const [over, setOver] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const nav = useNavigate()
  const toast = useToast()
  const add = useCallback((fs: File[]) => setFiles((xs) => [...xs, ...fs.filter((f) => ACCEPT.test(f.name) && !xs.some((x) => x.name === f.name && x.size === f.size))]), [])
  const upload = useMutation({
    mutationFn: () => api.createUpload(files, { label: label.trim() || undefined, auto_publish: auto }),
    onSuccess: (b) => nav(`/uploads/${b.id}`),
    onError: (e: Error) => toast.push({ kind: 'bad', title: 'Upload failed', detail: e.message }),
  })
  const total = files.reduce((a, f) => a + f.size, 0)
  return (
    <>
      <PageHeader title="Upload files" subtitle="Drop every file your customers sent. FORMA works out whose each file is from its format, maps it, and writes one JSON per file." hint="ลากไฟล์ทั้งหมดมาวางพร้อมกัน ระบบดูจาก format ว่าเป็นของลูกค้ารายไหน แปลงให้ แล้วได้ JSON ไฟล์ละ 1 ชุด" />
      <div className="grid lg:grid-cols-[1fr_360px] gap-5">
        <div className="flex flex-col gap-4">
          <div
            onDragOver={(e) => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); add(Array.from(e.dataTransfer.files)) }}
            onClick={() => input.current?.click()}
            className={cn('card border-2 border-dashed p-12 text-center cursor-pointer transition-colors', over ? 'border-brand-blue bg-signal-soft' : 'border-line-2 hover:border-brand-blue/60')}
          >
            <input ref={input} type="file" multiple accept=".xlsx,.xlsm,.csv,.txt,.tsv" className="hidden" onChange={(e) => { add(Array.from(e.target.files ?? [])); e.target.value = '' }} />
            <span className="w-14 h-14 rounded-full mx-auto grid place-items-center text-white" style={{ backgroundImage: 'var(--gradient-brand)' }}><UploadCloud size={26} /></span>
            <div className="heading text-[17px] mt-3">Drop all the files here</div>
            <div className="text-ink-2 mt-1">Excel (.xlsx) or CSV — any number, from any customers</div>
            <div className="hint-th mt-1">หรือคลิกเพื่อเลือกไฟล์ เลือกหลายไฟล์พร้อมกันได้</div>
          </div>
          {files.length > 0 && (
            <Section title={`${files.length} file${files.length > 1 ? 's' : ''} · ${fmtBytes(total)}`} actions={<button className="btn btn-ghost btn-sm" onClick={() => setFiles([])}>Clear</button>}>
              <ul className="max-h-[340px] overflow-auto">
                {files.map((f, i) => (
                  <li key={`${f.name}-${i}`} className="flex items-center gap-3 px-4 py-2 border-b border-line last:border-0">
                    <FileSpreadsheet size={16} className="text-signal shrink-0" />
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
          <Section title="Options">
            <div className="p-4 flex flex-col gap-3">
              <label className="flex items-start gap-2.5 cursor-pointer">
                <input type="checkbox" className="mt-1 accent-[var(--color-brand-blue)]" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
                <span><span className="font-semibold text-navy">Approve and export automatically</span><span className="block hint-th">ถ้าผ่านทุกด่าน (mapping ผ่าน, ทุก record ตรง format, ไม่มีความผิดปกติร้ายแรง) ระบบออก JSON ให้เลย ไม่ผ่านข้อไหนจะรอคนตรวจพร้อมบอกเหตุผล</span></span>
              </label>
              <input className="input" placeholder="Name this upload (optional) — e.g. Oct wave 2" value={label} onChange={(e) => setLabel(e.target.value)} />
            </div>
          </Section>
          <button className="btn btn-primary btn-lg justify-center" disabled={!files.length || upload.isPending} onClick={() => upload.mutate()}>
            {upload.isPending ? <><Spinner className="border-t-white" /> Uploading {files.length} files…</> : <><UploadCloud size={16} /> Upload {files.length || ''} file{files.length === 1 ? '' : 's'}</>}
          </button>
          <div className="card p-4 text-[12.5px]">
            <div className="heading text-[13px]">What happens to each file</div>
            <ol className="mt-2 flex flex-col gap-2">
              {[
                ['Match the format', 'Known format → its customer, automatically. Unknown → you choose the customer.', 'ดูจาก format ว่าเป็นของใคร ถ้าไม่รู้จักจะให้คุณเลือกลูกค้า'],
                ['Map', 'The approved mapping is reused; a changed or new format is proposed and checked.', 'ใช้ mapping เดิม หรือเสนอใหม่ถ้า format เปลี่ยน'],
                ['Create & check the data', 'Every record is checked against the target format and the last round.', 'ตรวจทุก record กับ format และรอบก่อน'],
                ['JSON', 'One JSON per file, named after it — download one or all as a zip.', 'ได้ JSON ไฟล์ละ 1 ชุด ดาวน์โหลดทีละไฟล์หรือทั้งหมด'],
              ].map(([t, en, th], i) => (
                <li key={t} className="flex gap-2.5">
                  <span className="w-5 h-5 rounded-full text-white text-[11px] font-bold grid place-items-center shrink-0 mt-0.5" style={{ backgroundImage: 'var(--gradient-brand)' }}>{i + 1}</span>
                  <span><span className="font-semibold text-navy">{t}</span> <span className="text-ink-2">{en}</span><span className="block hint-th">{th}</span></span>
                </li>
              ))}
            </ol>
          </div>
          <Link to="/imports/new/single" className="text-[12.5px] text-signal font-semibold hover:underline">Several files that make one delivery (products.csv + stock.csv …)? Use a single delivery →</Link>
        </div>
      </div>
    </>
  )
}

const STATE: Record<BatchItemState, { label: string; tone: 'ok' | 'warn' | 'bad' | 'signal' | 'neutral' }> = {
  processing: { label: 'Working', tone: 'neutral' },
  needs_customer: { label: 'Unknown format', tone: 'warn' },
  needs_review: { label: 'Check mapping', tone: 'warn' },
  needs_data_check: { label: 'Check data', tone: 'warn' },
  ready: { label: 'JSON ready', tone: 'ok' },
  failed: { label: 'Failed', tone: 'bad' },
}
const ITEM_STEPS = ['Match format', 'Mapping', 'Create data', 'Check data', 'JSON']

/** One upload: every file, where it stands, and what to do — plus all the JSON in one zip. */
export function UploadPage() {
  const { batchId = '' } = useParams()
  const qc = useQueryClient()
  const toast = useToast()
  const { data: b } = useQuery({ queryKey: ['upload', batchId], queryFn: () => api.upload(batchId), refetchInterval: (q) => (q.state.data && (q.state.data.done || q.state.data.stuck === q.state.data.counts.processing) ? false : 2000) })
  const { data: customers } = useQuery({ queryKey: ['customers'], queryFn: api.customers })
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [target, setTarget] = useState('')
  const [nc, setNc] = useState({ code: '', name: '' })
  const assign = useMutation({
    mutationFn: () => api.assignUpload(batchId, { item_ids: [...picked], ...(target === '__new' ? { new_customer: { code: nc.code.trim(), name: nc.name.trim() || nc.code.trim() } } : { customer_id: target }) }),
    onSuccess: (v) => { qc.setQueryData(['upload', batchId], v); qc.invalidateQueries({ queryKey: ['customers'] }); setPicked(new Set()); setTarget(''); setNc({ code: '', name: '' }); toast.push({ kind: 'ok', title: 'Customer set', detail: 'The files continue on their own.' }) },
    onError: (e: Error) => toast.push({ kind: 'bad', title: 'Not assigned', detail: e.message }),
  })
  const resume = useMutation({
    mutationFn: () => api.resumeUpload(batchId),
    onSuccess: (v) => { qc.setQueryData(['upload', batchId], v); toast.push({ kind: 'ok', title: 'Processing resumed' }) },
    onError: (e: Error) => toast.push({ kind: 'bad', title: 'Not resumed', detail: e.message }),
  })
  const unknown = useMemo(() => (b?.items ?? []).filter((i) => i.state === 'needs_customer'), [b])
  if (!b) return <div className="p-10 text-center"><Spinner /></div>
  const c = b.counts
  const ready = c.ready ?? 0
  const needYou = (c.needs_customer ?? 0) + (c.needs_review ?? 0) + (c.needs_data_check ?? 0)
  const settled = b.total - (c.processing ?? 0)
  return (
    <>
      <PageHeader
        crumbs={<><Link to="/imports" className="hover:text-ink">Imports</Link><span>/</span><span>Upload</span></>}
        title={b.label ?? 'Upload'}
        subtitle={<span className="flex items-center gap-2 flex-wrap"><span>{fmtInt(b.total)} file{b.total === 1 ? '' : 's'} · {fmtDate(b.created_at)} · by {b.created_by}</span>{b.auto_publish ? <Badge tone="signal">publishes automatically</Badge> : <Badge>a person approves</Badge>}</span>}
        hint="แต่ละไฟล์อยู่ขั้นไหน และต้องทำอะไรต่อ — ไฟล์ที่พร้อมดาวน์โหลด JSON ได้ทันที"
        actions={<a className={cn('btn btn-primary btn-lg', !ready && 'opacity-50 pointer-events-none')} href={api.uploadDownloadUrl(b.id)}><Download size={15} /> Download all JSON ({ready})</a>}
      />

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <Count label="JSON ready" value={ready} tone="ok" icon={FileJson} />
        <Count label="Need you" value={needYou} tone={needYou ? 'warn' : undefined} icon={CircleAlert} />
        <Count label="Working" value={c.processing ?? 0} icon={Loader2} spin={!!c.processing} />
        <Count label="Failed" value={c.failed ?? 0} tone={c.failed ? 'bad' : undefined} icon={X} />
      </div>
      <div className="h-2 rounded-full bg-line overflow-hidden mb-6" title={`${settled} of ${b.total} files settled`}>
        <div className="h-full transition-all" style={{ width: `${(ready / Math.max(1, b.total)) * 100}%`, backgroundImage: 'var(--gradient-brand)' }} />
      </div>

      {b.stuck > 0 && (
        <div className="card p-4 mb-5 flex items-center gap-3 border-l-4 border-l-warn">
          <CircleAlert className="text-warn shrink-0" />
          <span className="flex-1"><span className="heading block">{b.stuck} file{b.stuck === 1 ? ' is' : 's are'} waiting but not being processed</span><span className="hint-th">คิวงานเต็มหรือระบบล่มตอนอัปโหลด ไฟล์ยังอยู่ครบ กด Resume เพื่อทำต่อ</span></span>
          <button className="btn btn-primary" disabled={resume.isPending} onClick={() => resume.mutate()}>Resume</button>
        </div>
      )}

      {unknown.length > 0 && (
        <Section className="mb-5" title={`${unknown.length} file${unknown.length > 1 ? 's' : ''} with an unknown format`} hint="ไม่ตรงกับ format ของลูกค้ารายไหน — เลือกลูกค้า (หรือสร้างใหม่) แล้วระบบทำต่อเอง">
          <div className="p-4 flex flex-col gap-3">
            <ul className="flex flex-col gap-1.5">
              {unknown.map((i) => (
                <li key={i.id}>
                  <label className="flex items-center gap-2.5 cursor-pointer">
                    <input type="checkbox" className="accent-[var(--color-brand-blue)]" checked={picked.has(i.id)} onChange={() => setPicked((p) => { const n = new Set(p); if (n.has(i.id)) n.delete(i.id); else n.add(i.id); return n })} />
                    <span className="font-semibold text-navy">{i.file_name}</span>
                    <span className="text-[12px] text-muted truncate">{i.match?.reason}{i.match?.candidates?.length ? ` · candidates: ${i.match.candidates.map((x) => (x.similarity === null ? `${x.customer_code}` : `${x.customer_code} ${Math.round(x.similarity * 100)}%`)).join(', ')}` : ''}</span>
                  </label>
                </li>
              ))}
            </ul>
            <div className="flex items-center gap-2 flex-wrap">
              <button className="btn btn-sm" onClick={() => setPicked(new Set(unknown.map((i) => i.id)))}>Select all</button>
              <select className="input w-64" value={target} onChange={(e) => setTarget(e.target.value)}>
                <option value="">Choose the customer…</option>
                <option value="__new">+ New customer</option>
                {customers?.map((cu) => <option key={cu.id} value={cu.id}>{cu.code} — {cu.name}</option>)}
              </select>
              {target === '__new' && <>
                <input className="input w-32" placeholder="Code" value={nc.code} onChange={(e) => setNc({ ...nc, code: e.target.value })} />
                <input className="input w-52" placeholder="Name" value={nc.name} onChange={(e) => setNc({ ...nc, name: e.target.value })} />
              </>}
              <button className="btn btn-primary" disabled={!picked.size || !target || (target === '__new' && !nc.code.trim()) || assign.isPending} onClick={() => assign.mutate()}><UserPlus size={14} /> Assign {picked.size || ''} file{picked.size === 1 ? '' : 's'}</button>
            </div>
            <div className="hint-th">ไฟล์ของลูกค้าใหม่ mapping แรกจะรอคนตรวจหนึ่งครั้ง รอบต่อไป format เดียวกันจะผ่านอัตโนมัติ</div>
          </div>
        </Section>
      )}

      <Section title="Files" hint="ทุกไฟล์ในการอัปโหลดนี้ — กดปุ่มทางขวาเพื่อทำขั้นต่อไป">
        <table className="table">
          <thead><tr><th>File</th><th>Customer</th><th>Progress</th><th>Status</th><th className="text-right">Records</th><th className="text-right w-[170px]"></th></tr></thead>
          <tbody>{b.items.map((i) => <ItemRow key={i.id} i={i} />)}</tbody>
        </table>
      </Section>
    </>
  )
}

function Count({ label, value, tone, icon: Icon, spin }: { label: string; value: number; tone?: 'ok' | 'warn' | 'bad'; icon: typeof X; spin?: boolean }) {
  return (
    <div className="card p-4 flex items-center gap-3">
      <span className={cn('w-10 h-10 rounded-[10px] grid place-items-center', tone === 'ok' ? 'bg-ok-soft text-ok' : tone === 'warn' ? 'bg-warn-soft text-warn' : tone === 'bad' ? 'bg-bad-soft text-bad' : 'bg-surface-2 text-ink-3')}><Icon size={18} className={cn(spin && 'animate-spin')} /></span>
      <span><span className="block text-[24px] font-bold tnum leading-none text-navy">{fmtInt(value)}</span><span className="text-[12px] text-muted">{label}</span></span>
    </div>
  )
}

function ItemRow({ i }: { i: BatchItem }) {
  const st = STATE[i.state]
  const action = i.state === 'ready' && i.export_id ? <a className="btn btn-outline btn-sm" href={api.downloadUrl(i.export_id)}><Download size={13} /> {i.json_name}</a>
    : i.state === 'needs_review' && i.round_id && i.mapping_version_id ? <Link className="btn btn-sm" to={`/rounds/${i.round_id}/mapping/${i.mapping_version_id}`}>Check mapping <ArrowRight size={13} /></Link>
      : i.state === 'needs_data_check' && i.dataset_version_id ? <Link className="btn btn-sm" to={`/datasets/${i.dataset_version_id}`}>Check data <ArrowRight size={13} /></Link>
        : i.round_id ? <Link className="btn btn-ghost btn-sm" to={`/rounds/${i.round_id}`}>Open round</Link> : null
  return (
    <tr>
      <td><div className="flex items-center gap-2"><FileSpreadsheet size={15} className="text-signal shrink-0" /><span className="font-semibold text-navy truncate max-w-[260px]" title={i.file_name}>{i.file_name}</span></div><div className="text-[11.5px] text-muted pl-6">{fmtBytes(i.size)}</div></td>
      <td>
        {i.customer ? <span className="font-semibold">{i.customer.code}</span> : <span className="text-ink-3">—</span>}
        {i.match && <div className="text-[11.5px] text-muted">{i.match.kind === 'known' ? (i.match.how === 'exact' ? 'known format' : i.match.how === 'same_columns' ? 'known format · columns moved' : i.match.how === 'file_name' ? 'named in the file name' : `similar format · ${Math.round((i.match.similarity ?? 0) * 100)}%`) + (i.match.named && i.match.how !== 'file_name' ? ' · name agrees' : '') : i.customer ? 'chosen by a person' : 'unknown format'}</div>}
      </td>
      <td>
        <div className="flex items-center gap-1" title={ITEM_STEPS.map((s, k) => `${k + 1}. ${s}`).join('  ')}>
          {ITEM_STEPS.map((s, k) => {
            const n = k + 1
            const done = i.state === 'ready' || n < i.step
            const here = n === i.step && i.state !== 'ready'
            return <span key={s} className={cn('h-1.5 w-7 rounded-full', done ? 'bg-ok' : here ? (i.state === 'failed' ? 'bg-bad' : i.state === 'processing' ? 'bg-brand-blue animate-pulse' : 'bg-warn') : 'bg-line')} />
          })}
        </div>
        <div className="text-[11.5px] text-muted mt-1">{i.state === 'ready' ? 'Done' : `Step ${i.step}: ${ITEM_STEPS[i.step - 1]}`}</div>
      </td>
      <td>
        <Badge tone={st.tone}>{i.state === 'processing' ? <Loader2 size={11} className="animate-spin" /> : i.state === 'ready' ? <CircleCheck size={11} /> : null}{st.label}</Badge>
        <div className="text-[11.5px] text-muted mt-1 max-w-[280px]">{i.state === 'needs_data_check' && i.reasons?.length ? i.reasons.join(' · ') : i.message}</div>
      </td>
      <td className="text-right tnum">{fmtInt(i.rows)}</td>
      <td className="text-right">{action}</td>
    </tr>
  )
}
