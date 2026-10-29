import { WorkRoundProgress } from '../components/WorkRoundDashboard'
import { Localized } from '../lib/locale'
import { dateInZone, usePreferences } from '../lib/preferences'
import { Workflow } from '../components/Workflow'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ArrowRight, CircleAlert, CircleCheck, Download, FileSpreadsheet, Loader2, UploadCloud, UserPlus, X } from 'lucide-react'
import { api, type BatchItem, type BatchItemState } from '../lib/api'
import { cn, fmtBytes, fmtDate, fmtInt } from '../lib/utils'
import { Badge, PageHeader, Section, Spinner, Modal } from '../components/ui'
import { useToast } from '../lib/toast'

const ACCEPT = /\.(xlsx|xlsm|csv|txt|tsv)$/i

/** Upload many files at once: each is matched to its customer by format and becomes one JSON (ADR-027). */
export function BulkUpload() {
  const { t, prefs } = usePreferences()
  const [params] = useSearchParams()
  const [batchId, setBatchId] = useState(params.get('batch') || '')
  const [workDate, setWorkDate] = useState(() => dateInZone(prefs.timezone))
  const [roundSearch, setRoundSearch] = useState(''), [roundTerm, setRoundTerm] = useState('')
  const [progress, setProgress] = useState(0)
  useEffect(() => { const timer = setTimeout(() => setRoundTerm(roundSearch), 300); return () => clearTimeout(timer) }, [roundSearch])
  const choices = useInfiniteQuery({ queryKey: ['round-picker', roundTerm], initialPageParam: 0, queryFn: ({ pageParam }) => api.uploads({ limit: 20, offset: pageParam, search: roundTerm }), getNextPageParam: (page, pages) => page.length === 20 ? pages.length * 20 : undefined })
  const existing = useQuery({ queryKey: ['upload', batchId], queryFn: () => api.upload(batchId), enabled: !!batchId })
  const [files, setFiles] = useState<File[]>([])
  const [label, setLabel] = useState('')
  const [auto, setAuto] = useState(true)
  const [over, setOver] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const nav = useNavigate()
  const toast = useToast()
  const add = useCallback((fs: File[]) => setFiles((xs) => [...xs, ...fs.filter((f) => ACCEPT.test(f.name) && !xs.some((x) => x.name === f.name && x.size === f.size))]), [])
  const qc = useQueryClient()
  const upload = useMutation({
    mutationFn: async () => {
      // Bounded requests keep several hundred files from becoming one huge in-memory request.
      let id = batchId, skipped = 0, completed = 0
      setProgress(0)
      for (let at = 0; at < files.length;) {
        const chunk: File[] = []; let bytes = 0
        while (at < files.length && chunk.length < 10 && (!chunk.length || bytes + files[at].size <= 20 * 1024 * 1024)) { const f = files[at++]; chunk.push(f); bytes += f.size }
        if (!id) { const b = await api.createUpload(chunk, { label: label.trim() || t(`Round ${workDate}`, `รอบ ${workDate}`), work_date: workDate, auto_publish: auto }); id = b.id; setBatchId(id) }
        else { const b = await api.appendUpload(id, chunk); skipped += b.skipped }
        completed += chunk.length; setProgress(completed)
      }
      return { id, skipped }
    },
    onSuccess: (b) => { void qc.invalidateQueries({ queryKey: ['work-rounds'] }); void qc.invalidateQueries({ queryKey: ['upload', b.id] }); void qc.invalidateQueries({ queryKey: ['uploads'] }); if (b.skipped) toast.push({ kind: 'ok', title: t(`Skipped ${b.skipped} identical files`, `ข้ามไฟล์ที่มีแล้ว ${b.skipped} ไฟล์`) }); nav(`/uploads/${b.id}`) },
    onError: (e: Error) => toast.push({ kind: 'bad', title: 'Upload failed', detail: e.message }),
  })
  const total = files.reduce((a, f) => a + f.size, 0)
  return (
    <>
      <PageHeader title={t("Upload files", "อัปโหลดไฟล์")} subtitle={t("Choose a work round, then add files. You can return later and add more to the same round.", "เลือกรอบงานแล้วเพิ่มไฟล์ กลับมาอัปโหลดเพิ่มเข้ารอบเดิมได้จนไฟล์ครบ")} />
      <Workflow />
      <div className="grid lg:grid-cols-[1fr_360px] gap-5">
        <div className="flex flex-col gap-4" inert={upload.isPending}>
          <div
            onDragOver={(e) => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); add(Array.from(e.dataTransfer.files)) }}
            onClick={() => input.current?.click()} role="button" tabIndex={0} aria-label="Choose files" onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.current?.click() } }}
            className={cn('card border-2 border-dashed p-12 text-center cursor-pointer transition-colors', over ? 'border-brand-blue bg-signal-soft' : 'border-line-2 hover:border-brand-blue/60')}
          >
            <input ref={input} type="file" multiple accept=".xlsx,.xlsm,.csv,.txt,.tsv" className="hidden" onChange={(e) => { add(Array.from(e.target.files ?? [])); e.target.value = '' }} />
            <span className="w-14 h-14 mx-auto grid place-items-center text-signal"><UploadCloud size={26} /></span>
            <div className="heading text-[17px] mt-3"><Localized>Drop all the files here</Localized></div>
            <div className="text-ink-2 mt-1"><Localized>Excel (.xlsx) or CSV — any number, from any customers</Localized></div>
            <div className="hint-th mt-1">หรือคลิกเพื่อเลือกไฟล์ เลือกหลายไฟล์พร้อมกันได้</div>
          </div>
          {files.length > 0 && (
            <Section title={`${files.length} file${files.length > 1 ? 's' : ''} · ${fmtBytes(total)}`} actions={<button className="btn btn-ghost btn-sm" onClick={() => setFiles([])}><Localized>Clear</Localized></button>}>
              <ul className="max-h-[340px] overflow-auto">
                {files.map((f, i) => (
                  <li key={`${f.name}-${i}`} className="flex items-center gap-3 px-4 py-2 border-b border-line last:border-0">
                    <FileSpreadsheet size={16} className="text-signal shrink-0" />
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
          <Section title={t('Work round', 'เลือกรอบงาน')}>
            <fieldset disabled={upload.isPending} className="p-4 grid gap-3">
              <label className="text-sm grid gap-1">{t('Find an existing round', 'ค้นหารอบเดิม')}<input className="input" maxLength={200} value={roundSearch} onChange={e => setRoundSearch(e.target.value)} placeholder={t('Search by name', 'ค้นหาชื่อรอบ')} /></label>
              <label className="text-sm grid gap-1">{t('Destination round', 'อัปโหลดเข้ารอบ')}<select className="input" value={batchId} onChange={e => { setBatchId(e.target.value); setProgress(0); upload.reset() }}><option value="">{t('Create a new round', 'สร้างรอบใหม่')}</option>{batchId && !choices.data?.pages.flat().some(b => b.id === batchId) && <option value={batchId}>{existing.data?.label || batchId}</option>}{choices.data?.pages.flat().map(b => <option key={b.id} value={b.id}>{b.work_date || t('No date', 'ยังไม่มีวันที่')} · {b.label} ({b.total})</option>)}</select></label>
              {choices.hasNextPage && <button type="button" className="btn btn-sm" disabled={choices.isFetchingNextPage} onClick={() => void choices.fetchNextPage()}>{t('Load older rounds', 'โหลดรอบเก่าเพิ่มเติม')}</button>}
              {choices.isError && <button type="button" className="text-bad text-xs" onClick={() => void choices.refetch()}>{t('Retry loading rounds', 'โหลดรายชื่อรอบอีกครั้ง')}</button>}
              {!batchId ? <><label className="text-sm grid gap-1">{t('Round date', 'วันที่รอบงาน')}<input className="input" type="date" value={workDate} onChange={e => setWorkDate(e.target.value)} required /></label><label className="text-sm grid gap-1">{t('Round name (optional)', 'ชื่อรอบงาน (ไม่บังคับ)')}<input className="input" maxLength={255} placeholder={t('e.g. October delivery', 'เช่น รอบส่งข้อมูลปลายเดือน')} value={label} onChange={e => setLabel(e.target.value)} /></label></> : <p className="text-sm text-muted">{t('Files will be added to this round. Existing files and results stay in place.', 'เพิ่มไฟล์เข้ารอบนี้ โดยเก็บไฟล์และผลลัพธ์เดิมไว้')} <Link className="text-signal underline" to={'/uploads/' + batchId}>{t('Open round', 'ดูรอบนี้')}</Link></p>}
              <p className="text-xs text-muted">{t('Round date is a label. Actual upload dates are recorded separately.', 'วันที่รอบงานเป็นป้ายกำกับ ระบบบันทึกวันอัปโหลดจริงแยกต่างหาก')}</p>
            </fieldset>
          </Section>
          <Section title="Options">
            <div className="p-4 flex flex-col gap-3">
              <label className="flex items-start gap-2.5 cursor-pointer">
                <input type="checkbox" className="mt-1 accent-[var(--color-brand-blue)]" disabled={!!batchId || upload.isPending} checked={batchId ? existing.data?.auto_publish ?? false : auto} onChange={(e) => setAuto(e.target.checked)} />
                <span><span className="font-semibold text-navy"><Localized>Approve and export automatically</Localized></span><span className="block hint-th">ถ้าผ่านทุกด่าน (mapping ผ่าน, ทุก record ตรง format, ไม่มีความผิดปกติร้ายแรง) ระบบออก JSON ให้เลย ไม่ผ่านข้อไหนจะรอคนตรวจพร้อมบอกเหตุผล</span></span>
              </label>
              {!!batchId && <p className="text-xs text-muted">{t('Uses the existing round’s approval setting.', 'ใช้การตั้งค่าอนุมัติของรอบเดิม')}</p>}
            </div>
          </Section>
          <button className="btn btn-primary btn-lg justify-center" disabled={!files.length || upload.isPending || (!batchId && !workDate) || (!!batchId && !existing.data)} onClick={() => upload.mutate()}>
            {upload.isPending ? <><Spinner className="border-t-white" /> {t(`Uploading ${files.length} files…`, `กำลังอัปโหลด ${files.length} ไฟล์…`)}</> : <><UploadCloud size={16} /> {t(`Upload ${files.length || ""} files`, `อัปโหลด ${files.length || ""} ไฟล์`)}</>}
          </button>
          {upload.isPending && <p role="status" className="text-sm text-muted">{t(`Uploaded ${progress} / ${files.length} files`, `ส่งแล้ว ${progress} / ${files.length} ไฟล์`)}</p>}
          {upload.isError && <p role="alert" className="text-sm text-bad">{t('Upload interrupted. Retry to continue; identical files in this round will be skipped.', 'อัปโหลดหยุดกลางทาง กดอัปโหลดอีกครั้งเพื่อทำต่อ ระบบข้ามไฟล์ที่มีแล้วในรอบนี้')}</p>}
          <details className="text-[13px] text-muted px-1">
            <summary className="hover:text-ink"><Localized>What happens after upload?</Localized></summary>
            <ol className="mt-2 flex flex-col gap-2">
              {[
                ['Match the format', 'Known format → its customer, automatically. Unknown → you choose the customer.', 'ดูจาก format ว่าเป็นของใคร ถ้าไม่รู้จักจะให้คุณเลือกลูกค้า'],
                ['Map', 'The approved mapping is reused; a changed or new format is proposed and checked.', 'ใช้ mapping เดิม หรือเสนอใหม่ถ้า format เปลี่ยน'],
                ['Create & check the data', 'Every record is checked against the target format and the last round.', 'ตรวจทุก record กับ format และรอบก่อน'],
                ['JSON', 'One JSON per file, named after it — download one or all as a zip.', 'ได้ JSON ไฟล์ละ 1 ชุด ดาวน์โหลดทีละไฟล์หรือทั้งหมด'],
              ].map(([title, en, th], i) => (
                <li key={title} className="flex gap-2.5">
                  <span className="w-5 h-5 rounded-full step-number text-[11px] font-bold grid place-items-center shrink-0 mt-0.5">{i + 1}</span>
                  <span><span className="text-ink-2">{t(en, th)}</span></span>
                </li>
              ))}
            </ol>
          </details>
          <Link to="/imports/new/single" className="text-[12.5px] text-signal font-semibold hover:underline">{t("Several files that belong to one delivery? Combine them here →", "หลายไฟล์ที่ต้องรวมเป็นชุดเดียวกัน? อัปโหลดแบบรวมไฟล์ที่นี่ →")}</Link>
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

/** One upload: every file, where it stands, and what to do — plus all the JSON in one zip. */
export function UploadPage() {
  const { t, prefs } = usePreferences()
  const { batchId = '' } = useParams()
  const [filters, setFilters] = useSearchParams()
  const [editing, setEditing] = useState(false), [roundLabel, setRoundLabel] = useState(''), [roundDate, setRoundDate] = useState('')
  const state = filters.get('state') || '', search = filters.get('file') || ''
  const page = Math.max(0, Math.floor(Number(filters.get('page')) || 0))
  const change = (values: Record<string, string>) => setFilters(current => { const n = new URLSearchParams(current); n.delete('page'); Object.entries(values).forEach(([k,v]) => v ? n.set(k,v) : n.delete(k)); return n }, { replace: true })
  const qc = useQueryClient()
  const toast = useToast()
  const { data: b, isError, refetch } = useQuery({ queryKey: ['upload', batchId], queryFn: () => api.upload(batchId), refetchInterval: (q) => (q.state.data && (q.state.data.done || q.state.data.stuck === q.state.data.counts.processing) ? 10000 : 2000) })
  const edit = useMutation({ mutationFn: () => api.editUpload(batchId, { label: roundLabel.trim(), work_date: roundDate }), onSuccess: value => { qc.setQueryData(['upload', batchId], value); void qc.invalidateQueries({ queryKey: ['work-rounds'] }); void qc.invalidateQueries({ queryKey: ['round-picker'] }); void qc.invalidateQueries({ queryKey: ['uploads'] }); setEditing(false) } })
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
  if (!b) return <div className="p-10 text-center">{isError ? <button className="btn" onClick={() => void refetch()}>{t('Could not load round. Retry', 'โหลดรอบงานไม่ได้ ลองอีกครั้ง')}</button> : <Spinner />}</div>
  const c = b.counts
  const ready = c.ready ?? 0
  const visible = b.items.filter(i => (!state || (state === 'needs_you' ? ['needs_customer', 'needs_review', 'needs_data_check'].includes(i.state) : i.state === state)) && (!search || `${i.file_name} ${i.customer?.code || ''}`.toLocaleLowerCase().includes(search.toLocaleLowerCase())))
  return (
    <>
      <PageHeader
        crumbs={<><Link to="/imports" className="hover:text-ink">{t('All work rounds', 'รอบงานทั้งหมด')}</Link><span>/</span><span>{t('This round', 'รอบนี้')}</span></>}
        title={b.label ?? 'Upload'}
        subtitle={<span className="flex items-center gap-2 flex-wrap"><span>{t('Round date', 'วันที่รอบงาน')}: {b.work_date || t('Not set', 'ยังไม่ได้กำหนด')} · {fmtInt(b.total)} {t('files', 'ไฟล์')} · {t('Created', 'สร้างเมื่อ')} {fmtDate(b.created_at)}</span><Badge tone={b.complete ? 'ok' : 'neutral'}>{b.complete ? t('Received files ready', 'ไฟล์ที่รับมาพร้อมแล้ว') : t('Work remaining', 'ยังมีงานต้องทำ')}</Badge></span>}
        hint="แต่ละไฟล์อยู่ขั้นไหน และต้องทำอะไรต่อ — ไฟล์ที่พร้อมดาวน์โหลด JSON ได้ทันที"
        actions={<><Link className="btn" to={'/imports/new?batch=' + b.id}><UploadCloud size={15} />{t('Add files to this round', 'เพิ่มไฟล์เข้ารอบนี้')}</Link><a className={cn('btn btn-primary', !ready && 'pointer-events-none')} href={api.uploadDownloadUrl(b.id)} aria-disabled={!ready} tabIndex={ready ? 0 : -1} onClick={e => { if (!ready) e.preventDefault() }}><Download size={15} />{t('Download ready files', 'ดาวน์โหลดไฟล์ที่พร้อม')} ({ready})</a></>}
      />

      <button className="btn btn-sm mb-4" onClick={() => { setRoundLabel(b.label || ''); setRoundDate(b.work_date || dateInZone(prefs.timezone)); edit.reset(); setEditing(true) }}>{t('Edit round name / date', 'แก้ชื่อ / วันที่รอบงาน')}</button>
      <Modal open={editing} onClose={() => { if (!edit.isPending) setEditing(false) }} title={t('Work round label', 'ป้ายกำกับรอบงาน')} width="max-w-md"><form className="grid gap-4" onSubmit={e => { e.preventDefault(); edit.mutate() }}><label className="text-sm grid gap-1">{t('Name', 'ชื่อรอบงาน')}<input className="input" value={roundLabel} maxLength={255} required onChange={e => setRoundLabel(e.target.value)} /></label><label className="text-sm grid gap-1">{t('Round date', 'วันที่รอบงาน')}<input className="input" type="date" value={roundDate} required onChange={e => setRoundDate(e.target.value)} /></label><p className="text-xs text-muted">{t('Changes the label only. Actual upload dates stay unchanged.', 'แก้เฉพาะป้ายกำกับ วันอัปโหลดจริงของแต่ละไฟล์ยังอยู่เหมือนเดิม')}</p>{edit.isError && <p role="alert" className="text-bad text-sm">{t('Could not save. Try again.', 'บันทึกไม่ได้ ลองอีกครั้ง')}</p>}<button className="btn btn-primary justify-self-end" disabled={edit.isPending || !roundLabel.trim() || !roundDate}>{t('Save', 'บันทึก')}</button></form></Modal>

      <Section title={t("Round overview", "ภาพรวมรอบนี้")} className="mb-5"><WorkRoundProgress batch={b} /></Section>

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

      <div className="flex flex-wrap gap-2 my-4">{[['', t('All', 'ทั้งหมด')], ['needs_you', t('Needs you', 'รอคุณตอบ')], ['processing', t('Processing', 'กำลังทำ')], ['ready', t('Ready', 'พร้อมดาวน์โหลด')], ['failed', t('Failed', 'มีปัญหา')]].map(([v,label]) => <button key={v} className={cn('btn btn-sm', state === v && 'bg-signal-soft')} aria-pressed={state === v} onClick={() => change({ state: v })}>{label}</button>)}<input aria-label={t('Find a file in this round', 'ค้นหาไฟล์ในรอบนี้')} className="input sm:max-w-xs" value={search} maxLength={200} placeholder={t('File or customer code', 'ชื่อไฟล์หรือรหัสลูกค้า')} onChange={e => change({ file: e.target.value })} /></div>
      <Section title={t(`Files (${visible.length})`, `ไฟล์ (${visible.length})`)} hint="ทุกไฟล์ในรอบนี้ — กดปุ่มทางขวาเพื่อทำขั้นต่อไป">
        <table className="table">
          <thead><tr><th>File</th><th><Localized>Customer</Localized></th><th>Progress</th><th>Status</th><th className="text-right"><Localized>Records</Localized></th><th className="text-right w-[170px]"></th></tr></thead>
          <tbody>{visible.slice(page * 50, page * 50 + 50).map((i) => <ItemRow key={i.id} i={i} />)}{!visible.length && <tr><td colSpan={6} className="p-6 text-center text-muted">{t('No matching files', 'ไม่พบไฟล์ที่ตรงกับตัวกรอง')}</td></tr>}</tbody>
        </table>
        <div className="flex justify-between items-center gap-3 p-4 border-t border-line"><button className="btn btn-sm" disabled={!page} onClick={() => change({ page: String(page - 1) })}>{t('Previous', 'ก่อนหน้า')}</button><span className="text-xs text-muted">{t('Page', 'หน้า')} {page + 1}</span><button className="btn btn-sm" disabled={(page + 1) * 50 >= visible.length} onClick={() => change({ page: String(page + 1) })}>{t('Next', 'ถัดไป')}</button></div>
      </Section>
    </>
  )
}

function ItemRow({ i }: { i: BatchItem }) {
  const { t } = usePreferences()
  const stage = i.state === "ready" ? 3 : ["needs_review", "needs_data_check"].includes(i.state) || i.step >= 3 ? 2 : 1
  const stages = [t("Upload", "อัปโหลด"), t("Answer & check", "ตอบคำถามและตรวจปัญหา"), t("Download", "ดาวน์โหลด")]
  const st = STATE[i.state]
  const action = i.state === 'ready' && i.export_id ? <a className="btn btn-outline btn-sm" title={i.json_name} href={api.downloadUrl(i.export_id)}><Download size={13} /> JSON</a>
    : i.state === 'needs_review' && i.round_id && i.mapping_version_id ? <Link className="btn btn-sm" to={`/review?round=${i.round_id}`}>{t("Answer questions", "ตอบคำถาม")} <ArrowRight size={13} /></Link>
      : i.state === 'needs_data_check' && i.dataset_version_id ? <Link className="btn btn-sm" to={`/datasets/${i.dataset_version_id}`}>{t("Check results", "ตรวจผลลัพธ์")} <ArrowRight size={13} /></Link>
        : i.round_id ? <Link className="btn btn-ghost btn-sm" to={`/rounds/${i.round_id}`}><Localized>Open round</Localized></Link> : null
  return (
    <tr>
      <td><div className="flex items-center gap-2"><FileSpreadsheet size={15} className="text-signal shrink-0" /><span className="font-semibold text-navy truncate max-w-[260px]" title={i.file_name}>{i.file_name}</span></div><div className="text-[11.5px] text-muted pl-6">{fmtBytes(i.size)}</div></td>
      <td>
        {i.customer ? <span className="font-semibold">{i.customer.code}</span> : <span className="text-ink-3">—</span>}
        {i.match && <div className="text-[11.5px] text-muted">{i.match.kind === 'known' ? (i.match.how === 'exact' ? 'known format' : i.match.how === 'same_columns' ? 'known format · columns moved' : i.match.how === 'file_name' ? 'named in the file name' : `similar format · ${Math.round((i.match.similarity ?? 0) * 100)}%`) + (i.match.named && i.match.how !== 'file_name' ? ' · name agrees' : '') : i.customer ? 'chosen by a person' : 'unknown format'}</div>}
      </td>
      <td>
        <div className="flex items-center gap-1" title={stages.map((s, k) => `${k + 1}. ${s}`).join('  ')}>
          {stages.map((s, k) => {
            const n = k + 1
            const done = i.state === 'ready' || n < stage
            const here = n === stage && i.state !== 'ready'
            return <span key={s} className={cn('h-1.5 w-7 rounded-full', done ? 'bg-ok' : here ? (i.state === 'failed' ? 'bg-bad' : i.state === 'processing' ? 'bg-brand-blue animate-pulse' : 'bg-warn') : 'bg-line')} />
          })}
        </div>
        <div className="text-[11.5px] text-muted mt-1">{i.state === 'ready' ? t('Ready to download', 'พร้อมดาวน์โหลด') : `${stage}/3 · ${stages[stage - 1]}`}
        <details className="text-xs text-muted mt-2"><summary>{t('What is happening?', 'ระบบกำลังทำอะไร?')}</summary><p className="mt-1">{i.message}</p></details></div>
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
