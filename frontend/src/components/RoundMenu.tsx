import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowRightLeft, Ellipsis, FileUp, Undo2, XCircle } from 'lucide-react'
import { api, waitForJob, type ImportRound } from '../lib/api'
import { usePreferences } from '../lib/preferences'
import { useToast } from '../lib/toast'
import { Modal, Spinner } from './ui'

export const CLOSED_STATUSES = ['cancelled', 'moved', 'superseded']
type Round = { id: string; status: string; customer_id: string; label: string | null; summary?: ImportRound['summary'] }
type Dialog = 'move' | 'replace' | 'cancel' | null

/** What a closed round says about itself, with a link to the round that took its place (ADR-052). */
export function ClosedNote({ r }: { r: ImportRound }) {
  const { t, date } = usePreferences(), qc = useQueryClient(), toast = useToast()
  const reopen = useMutation({ mutationFn: () => api.reopenRound(r.id), onSuccess: () => { void qc.invalidateQueries(); toast.push({ kind: 'ok', title: t('Round reopened', 'เปิดรอบคืนแล้ว') }) }, onError: (e: Error) => toast.push({ kind: 'bad', title: t('Not reopened', 'เปิดคืนไม่ได้'), detail: e.message }) })
  if (!CLOSED_STATUSES.includes(r.status)) return null
  const next = r.successor
  const what = r.status === 'cancelled' ? t('This round was cancelled', 'รอบนี้ถูกยกเลิกแล้ว')
    : r.status === 'moved' ? t(`This file was moved to ${next?.customer_code ?? 'another customer'}`, `ไฟล์นี้ถูกย้ายไป ${next?.customer_code ?? 'ลูกค้าอื่น'} แล้ว`)
      : t('This round was replaced by a newer file', 'รอบนี้ถูกแทนที่ด้วยไฟล์ใหม่แล้ว')
  return <div className="mt-5 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line bg-surface-2 px-4 py-3 text-sm" role="status">
    <div className="min-w-0">
      <p className="font-medium">{what}</p>
      <p className="text-muted mt-1">{r.closed?.reason}{r.closed?.at && <> · {date(r.closed.at)}</>}{r.closed?.by && <> · {r.closed.by}</>}</p>
    </div>
    {next ? <Link className="btn btn-sm" to={`/rounds/${next.id}`}>{t('Open the current round', 'เปิดรอบที่ใช้อยู่')}</Link>
      : r.status === 'cancelled' ? <button className="btn btn-sm" disabled={reopen.isPending} onClick={() => reopen.mutate()}><Undo2 size={14} />{t('Reopen', 'เปิดคืน')}</button> : null}
  </div>
}

/** The three ways to close a round without deleting it: move, replace, cancel. */
export function RoundMenu({ round, compact, moveRequest }: { round: Round; compact?: boolean; moveRequest?: number }) {
  const { t } = usePreferences(), qc = useQueryClient(), toast = useToast(), nav = useNavigate(), id = useId()
  const [open, setOpen] = useState(false), [position, setPosition] = useState({ top: 0, left: 0 }), [dialog, setDialog] = useState<Dialog>(null)
  const [reason, setReason] = useState(''), [target, setTarget] = useState(''), [files, setFiles] = useState<File[]>([]), [label, setLabel] = useState('')
  const trigger = useRef<HTMLButtonElement>(null), menu = useRef<HTMLDivElement>(null)
  const { data: customers } = useQuery({ queryKey: ['customers'], queryFn: api.customers, enabled: dialog === 'move' })
  const suggested = round.summary?.matches_other_customer?.customer_id
  useEffect(() => { if (dialog === 'move' && !target && suggested) setTarget(suggested) }, [dialog, target, suggested])
  useEffect(() => { if (moveRequest) choose('move') }, [moveRequest])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!open) return
    const outside = (event: PointerEvent) => { if (!menu.current?.contains(event.target as Node) && !trigger.current?.contains(event.target as Node)) setOpen(false) }
    const away = () => setOpen(false)
    document.addEventListener('pointerdown', outside); window.addEventListener('resize', away); document.addEventListener('scroll', away, true)
    return () => { document.removeEventListener('pointerdown', outside); window.removeEventListener('resize', away); document.removeEventListener('scroll', away, true) }
  }, [open])
  const done = (title: string, to?: string) => { void qc.invalidateQueries(); setDialog(null); toast.push({ kind: 'ok', title }); if (to) nav(to) }
  const fail = (e: Error) => toast.push({ kind: 'bad', title: t('Not done', 'ยังทำไม่ได้'), detail: e.message })
  const cancel = useMutation({ mutationFn: () => api.cancelRound(round.id, reason), onSuccess: () => done(t('Round cancelled', 'ยกเลิกรอบแล้ว')), onError: fail })
  const move = useMutation({ mutationFn: () => api.moveRound(round.id, { customer_id: target, reason }), onSuccess: r => { done(t('Moved. The file is being processed again.', 'ย้ายแล้ว กำลังประมวลผลใหม่'), `/rounds/${r.id}`); void waitForJob(r.job_id).then(() => qc.invalidateQueries()) }, onError: fail })
  const replace = useMutation({ mutationFn: () => api.replaceRound(round.id, files, reason, label || undefined), onSuccess: r => { done(t('Replaced. The new file is being processed.', 'แทนที่แล้ว กำลังประมวลผลไฟล์ใหม่'), `/rounds/${r.id}`); void waitForJob(r.job_id).then(() => qc.invalidateQueries()) }, onError: fail })
  if (CLOSED_STATUSES.includes(round.status)) return null
  const busy = cancel.isPending || move.isPending || replace.isPending
  function toggle() {
    if (open) { setOpen(false); return }
    const rect = trigger.current?.getBoundingClientRect(); if (!rect) return
    setPosition({ left: Math.max(8, Math.min(rect.right - 220, window.innerWidth - 228)), top: rect.bottom + 150 <= window.innerHeight ? rect.bottom + 6 : rect.top - 150 })
    setOpen(true)
  }
  function choose(next: Dialog) { setOpen(false); setReason(''); setTarget(''); setFiles([]); setLabel(''); cancel.reset(); move.reset(); replace.reset(); setDialog(next) }
  const close = () => { if (!busy) setDialog(null) }
  const reasonField = (placeholder: string) => <label className="grid gap-1 text-sm">{t('Reason', 'เหตุผล')}<input className="input" maxLength={500} placeholder={placeholder} value={reason} onChange={e => setReason(e.target.value)} autoFocus /></label>
  const consequences = (items: string[]) => <ul className="list-disc pl-5 text-sm text-ink-2 space-y-1">{items.map(x => <li key={x}>{x}</li>)}</ul>
  const other = customers?.find(c => c.id === target)
  return <>
    <button ref={trigger} type="button" className={compact ? 'chat-menu-trigger' : 'btn'} aria-label={t('More actions for this round', 'การกระทำอื่นกับรอบนี้')} aria-haspopup="menu" aria-expanded={open} aria-controls={open ? id : undefined} onClick={toggle}><Ellipsis size={16} /></button>
    {open && createPortal(<div ref={menu} id={id} role="menu" aria-label={t('Round actions', 'จัดการรอบงาน')} className="chat-menu" style={position}>
      <button role="menuitem" onClick={() => choose('move')}><ArrowRightLeft size={16} />{t('Move to another customer', 'ย้ายไปลูกค้าอื่น')}</button>
      <button role="menuitem" onClick={() => choose('replace')}><FileUp size={16} />{t('Replace with a new file', 'แทนที่ด้วยไฟล์ใหม่')}</button>
      <hr className="my-1 border-line" />
      <button role="menuitem" className="text-bad" onClick={() => choose('cancel')}><XCircle size={16} />{t('Cancel this round', 'ยกเลิกรอบนี้')}</button>
    </div>, document.body)}

    <Modal open={dialog === 'move'} onClose={close} title={t('Move the file to another customer', 'ย้ายไฟล์ไปลูกค้าอื่น')} width="max-w-md" footer={<><button className="btn" disabled={busy} onClick={close}>{t('Back', 'กลับ')}</button><button className="btn btn-primary" disabled={busy || !target || !reason.trim()} onClick={() => move.mutate()}>{move.isPending ? <Spinner /> : null}{t('Move and run', 'ย้ายและรันต่อ')}</button></>}>
      <div className="grid gap-4">
        <label className="grid gap-1 text-sm">{t('Move to', 'ย้ายไปที่')}<select className="input" value={target} onChange={e => setTarget(e.target.value)}><option value="">{t('Choose the customer…', 'เลือกลูกค้า…')}</option>{customers?.filter(c => c.id !== round.customer_id).map(c => <option key={c.id} value={c.id}>{c.code} — {c.name}{c.id === suggested ? ` · ${t('suggested', 'ระบบแนะนำ')}` : ''}</option>)}</select></label>
        {reasonField(t('e.g. the customer sent it to the wrong mailbox', 'เช่น ลูกค้าส่งผิดอีเมล'))}
        {consequences([
          t(`A new round is created for ${other?.code ?? 'that customer'} and processed at once`, `สร้างรอบใหม่ให้ ${other?.code ?? 'ลูกค้านั้น'} แล้วรันต่อทันที`),
          t('This round is closed as "moved" and leaves every queue', 'รอบนี้ปิดเป็น "ย้ายแล้ว" ไม่นับในคิว'),
          t('The file is not copied; the same stored file is used', 'ไฟล์ต้นทางไม่ถูกคัดลอก ใช้ไฟล์เดิม'),
        ])}
      </div>
    </Modal>

    <Modal open={dialog === 'replace'} onClose={close} title={t('Replace with a new file', 'แทนที่ด้วยไฟล์ใหม่')} width="max-w-md" footer={<><button className="btn" disabled={busy} onClick={close}>{t('Back', 'กลับ')}</button><button className="btn btn-primary" disabled={busy || !files.length || !reason.trim()} onClick={() => replace.mutate()}>{replace.isPending ? <Spinner /> : null}{t('Upload and replace', 'อัปโหลดและแทนที่')}</button></>}>
      <div className="grid gap-4">
        <label className="grid gap-1 text-sm">{t('New file', 'ไฟล์ใหม่')}<input className="input py-1.5" type="file" multiple accept=".xlsx,.xlsm,.csv,.txt,.tsv" onChange={e => setFiles(Array.from(e.target.files ?? []))} /></label>
        <label className="grid gap-1 text-sm">{t('Round name (optional)', 'ชื่อรอบ (ไม่บังคับ)')}<input className="input" maxLength={255} placeholder={round.label ?? ''} value={label} onChange={e => setLabel(e.target.value)} /></label>
        {reasonField(t('e.g. the customer corrected two prices', 'เช่น ลูกค้าแก้ราคา 2 รายการ'))}
        {consequences([
          t('The new round is marked as replacing this one', 'รอบใหม่ลิงก์ว่าแทนรอบนี้'),
          t('This round becomes "replaced" and its JSON leaves the work round’s zip', 'รอบนี้เปลี่ยนเป็น "ถูกแทนที่" และ JSON ของมันหายจาก zip'),
          t('The approved mapping is reused; an unchanged layout passes on its own', 'ใช้กฎจับคู่เดิม ถ้ารูปแบบไม่เปลี่ยนจะผ่านเอง'),
        ])}
      </div>
    </Modal>

    <Modal open={dialog === 'cancel'} onClose={close} title={t('Cancel this round', 'ยกเลิกรอบนี้')} width="max-w-md" footer={<><button className="btn" disabled={busy} onClick={close}>{t('Back', 'กลับ')}</button><button className="btn btn-danger" disabled={busy || !reason.trim()} onClick={() => cancel.mutate()}>{cancel.isPending ? <Spinner /> : null}{t('Cancel the round', 'ยกเลิกรอบ')}</button></>}>
      <div className="grid gap-4">
        {reasonField(t('e.g. a test file, uploaded by mistake', 'เช่น ไฟล์ทดสอบ อัปโหลดผิด'))}
        {consequences([
          t('It leaves the review queue and every count', 'หายจากคิวตรวจและตัวนับทุกหน้า'),
          t('No JSON comes out of this round', 'ไม่มี JSON ออกจากรอบนี้'),
          t('The files and history stay, and the round can be reopened', 'ไฟล์และประวัติยังอยู่ ดูย้อนหลังได้ และเปิดคืนได้'),
        ])}
      </div>
    </Modal>
  </>
}
