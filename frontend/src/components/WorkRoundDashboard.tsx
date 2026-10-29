import { useEffect, useState } from 'react'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowRight, Pencil } from 'lucide-react'
import { api, type BatchSummary } from '../lib/api'
import { usePreferences } from '../lib/preferences'
import { Modal, Section, Spinner } from './ui'

/** One explicit round: receipt targets are independent of processing progress. */
export function WorkRoundProgress({ batch: b }: { batch: BatchSummary }) {
  const { t } = usePreferences(), qc = useQueryClient()
  const [editing, setEditing] = useState(false), [target, setTarget] = useState('')
  const edit = useMutation({ mutationFn: () => api.editUpload(b.id, { expected_files: target === '' ? null : Number(target) }), onSuccess: value => {
    qc.setQueryData(['upload', b.id], value)
    for (const key of ['work-rounds', 'round-picker', 'dashboard-rounds', 'uploads']) void qc.invalidateQueries({ queryKey: [key] })
    setEditing(false)
  } })
  const ready = b.counts.ready || 0, processing = b.counts.processing || 0, failed = b.counts.failed || 0
  const review = (b.counts.needs_review || 0) + (b.counts.needs_customer || 0) + (b.counts.needs_data_check || 0)
  const missing = b.expected_files === null ? null : Math.max(0, b.expected_files - b.total)
  const extra = b.expected_files === null ? 0 : Math.max(0, b.total - b.expected_files)
  const valid = target === '' || (Number.isInteger(Number(target)) && Number(target) >= 1 && Number(target) <= 1000000)
  const states = [
    { key: 'ready', label: t('Ready to download', 'พร้อมดาวน์โหลด'), count: ready, color: 'bg-ok', text: 'text-ok' },
    { key: 'needs_you', label: t('Needs your review', 'รอคุณตรวจ'), count: review, color: 'bg-warn', text: 'text-warn' },
    { key: 'processing', label: t('Processing', 'กำลังทำ'), count: processing, color: 'bg-signal', text: 'text-signal' },
    { key: 'failed', label: t('Failed', 'มีปัญหา'), count: failed, color: 'bg-bad', text: 'text-bad' },
  ]
  return <div className="p-4 sm:p-5 space-y-5">
    <div className="flex flex-wrap justify-between items-center gap-3"><div className="min-w-0"><p className="font-semibold break-words">{b.label}</p><p className="text-xs text-muted mt-1">{t('Round date', 'วันที่รอบงาน')}: {b.work_date || t('Not set', 'ยังไม่ได้กำหนด')}</p></div><button className="btn btn-sm" onClick={() => { setTarget(b.expected_files?.toString() || ''); edit.reset(); setEditing(true) }}><Pencil size={13} />{t('Set file target', 'ตั้งเป้าจำนวนไฟล์')}</button></div>
    <div className="grid sm:grid-cols-2 gap-5">
      <div className="rounded-xl border border-line p-4"><p className="text-sm text-muted">{t('Files received', 'การรับไฟล์')}</p><p className="text-3xl font-semibold tnum my-2">{b.total.toLocaleString()} <span className="text-base text-muted">/ {b.expected_files?.toLocaleString() ?? '—'} {t('files', 'ไฟล์')}</span></p>
        <div className="h-2 bg-surface-2 rounded-full overflow-hidden" role="progressbar" aria-label={t('Receipt progress', 'ความคืบหน้าการรับไฟล์')} {...(b.expected_files !== null ? { 'aria-valuenow': Math.min(b.total, b.expected_files), 'aria-valuemax': b.expected_files } : {})}><div className="bg-signal h-full rounded-full" style={{ width: b.expected_files ? `${Math.min(100, b.total / b.expected_files * 100)}%` : '0%' }} /></div>
        <p className="text-sm mt-3">{missing === null ? t('No target set — completeness is unknown.', 'ยังไม่ตั้งเป้า จึงยังบอกไม่ได้ว่ารับไฟล์ครบหรือไม่') : missing ? t(`${missing.toLocaleString()} files still expected`, `ยังขาด ${missing.toLocaleString()} ไฟล์`) : extra ? t(`${extra.toLocaleString()} files above target`, `รับเกินเป้า ${extra.toLocaleString()} ไฟล์`) : t('All expected files received', 'รับไฟล์ครบตามเป้าแล้ว')}</p>
      </div>
      <div className="rounded-xl border border-line p-4"><p className="text-sm text-muted">{t('Processing received files', 'ผลของไฟล์ที่รับมาแล้ว')}</p><p className="text-3xl font-semibold tnum my-2">{ready.toLocaleString()} <span className="text-base text-muted">/ {b.total.toLocaleString()} {t('ready', 'ไฟล์พร้อมใช้')}</span></p>
        <div className="h-2 flex rounded-full overflow-hidden bg-surface-2" aria-hidden="true">{states.map(s => <div key={s.key} className={s.color} style={{ width: b.total ? `${s.count / b.total * 100}%` : '0%' }} />)}</div>
        <p className="text-sm mt-3 text-muted">{t('Received does not mean checked and ready.', 'รับไฟล์แล้ว ไม่ได้แปลว่าตรวจเสร็จและพร้อมใช้')}</p>
      </div>
    </div>
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">{states.map(s => <Link key={s.key} to={`/uploads/${b.id}?state=${s.key}`} className="rounded-lg border border-line p-3 hover:bg-surface-2"><div className="flex justify-between gap-2 items-center"><span className="text-sm">{s.label}</span><ArrowRight size={13} className="text-muted" /></div><strong className={`text-2xl tnum ${s.text}`}>{s.count.toLocaleString()}</strong></Link>)}</div>
    {!!b.stuck && <p role="status" className="text-sm text-warn">{t(`${b.stuck} files have stalled. Open this round to resume.`, `มี ${b.stuck} ไฟล์ที่งานค้าง เปิดรอบนี้เพื่อตรวจและทำต่อ`)}</p>}
    <Modal open={editing} onClose={() => { if (!edit.isPending) setEditing(false) }} title={t('Expected files in this round', 'จำนวนไฟล์ที่คาดว่าจะได้รับในรอบนี้')} width="max-w-md"><form className="space-y-4" onSubmit={e => { e.preventDefault(); if (valid) edit.mutate() }}><label className="grid gap-2 text-sm">{t('Expected total files', 'คาดว่าจะได้รับทั้งหมดกี่ไฟล์')}<input autoFocus className="input" type="number" min={1} max={1000000} step={1} value={target} placeholder="300" onChange={e => setTarget(e.target.value)} /></label><p className="text-xs text-muted">{t('Leave blank if unknown. This target never blocks additional uploads or approves files.', 'เว้นว่างได้ถ้ายังไม่ทราบ เป้านี้ไม่จำกัดการอัปโหลดเพิ่มและไม่อนุมัติไฟล์ให้อัตโนมัติ')}</p>{edit.isError && <p role="alert" className="text-bad text-sm">{t('Could not save the target. Try again.', 'บันทึกเป้าไม่ได้ ลองอีกครั้ง')}</p>}<button className="btn btn-primary" disabled={!valid || edit.isPending}>{t('Save', 'บันทึก')}</button></form></Modal>
  </div>
}

export function WorkRoundDashboard() {
  const { t } = usePreferences()
  const [chosen, setChosen] = useState(''), [search, setSearch] = useState(''), [term, setTerm] = useState('')
  useEffect(() => { const timer = setTimeout(() => setTerm(search), 300); return () => clearTimeout(timer) }, [search])
  const rounds = useInfiniteQuery({ queryKey: ['dashboard-rounds', term], queryFn: ({ pageParam }) => api.uploads({ search: term, offset: pageParam, limit: 20 }), initialPageParam: 0, getNextPageParam: (last, pages) => last.length === 20 ? pages.length * 20 : undefined, refetchInterval: 10000 })
  const options = rounds.data?.pages.flat() ?? [], selected = chosen || options[0]?.id
  const round = useQuery({ queryKey: ['upload', selected], queryFn: () => api.upload(selected!), enabled: !!selected, refetchInterval: 10000 })
  return <Section title={t('Work round overview', 'ภาพรวมรอบงาน')} className="mb-6" actions={<Link className="btn btn-ghost btn-sm" to="/imports">{t('All rounds', 'ทุกรอบงาน')}<ArrowRight size={14} /></Link>}>
    <div className="flex flex-wrap gap-3 p-4 border-b border-line"><input className="input sm:max-w-xs" aria-label={t('Find dashboard round', 'ค้นหารอบสำหรับ dashboard')} placeholder={t('Search round name', 'ค้นหาชื่อรอบงาน')} value={search} onChange={e => setSearch(e.target.value)} /><select className="input flex-1 min-w-0 sm:min-w-48" aria-label={t('Dashboard round', 'รอบที่แสดงใน dashboard')} value={selected || ''} onChange={e => setChosen(e.target.value)}><option value="" disabled>{t('Choose round', 'เลือกรอบงาน')}</option>{chosen && !options.some(b => b.id === chosen) && <option value={chosen}>{round.data?.label || t('Selected round', 'รอบที่เลือก')}</option>}{options.map(b => <option key={b.id} value={b.id}>{b.work_date || '—'} · {b.label}</option>)}</select>{rounds.hasNextPage && <button className="btn btn-sm" disabled={rounds.isFetchingNextPage} onClick={() => void rounds.fetchNextPage()}>{t('Older rounds', 'โหลดรอบเก่าเพิ่ม')}</button>}</div>
    {round.data ? <WorkRoundProgress batch={round.data} /> : selected || rounds.isPending ? <div className="p-5">{round.isError || rounds.isError ? <button className="btn" onClick={() => { void rounds.refetch(); void round.refetch() }}>{t('Retry', 'ลองโหลดอีกครั้ง')}</button> : <Spinner />}</div> : <p className="p-5 text-muted">{rounds.isError ? t('Could not load rounds.', 'โหลดรอบงานไม่ได้') : t('No matching round. Upload files to start.', 'ยังไม่มีรอบที่ตรงกัน อัปโหลดไฟล์เพื่อเริ่มงาน')}</p>}
  </Section>
}
