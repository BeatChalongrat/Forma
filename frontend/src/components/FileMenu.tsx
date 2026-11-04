import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { ArrowRightLeft, Ellipsis, Eye, FileUp, XCircle } from 'lucide-react'
import { api, waitForJob } from '../lib/api'
import { fmtBytes } from '../lib/utils'
import { UPLOAD_ACCEPT, splitReadable } from '../lib/uploads'
import { usePreferences } from '../lib/preferences'
import { useToast } from '../lib/toast'
import { CLOSED_STATUSES } from './RoundMenu'
import { Modal, Spinner } from './ui'

type Action = 'replace' | 'move' | 'cancel'
type FileInfo = { id: string; name: string; size: number }

export function FileMenu({ roundId, customerId, status, file, fileCount = 1, onView }: {
  roundId: string; customerId: string; status: string; file: FileInfo; fileCount?: number; onView?: () => void
}) {
  const { t } = usePreferences(), qc = useQueryClient(), toast = useToast(), navigate = useNavigate(), id = useId()
  const [open, setOpen] = useState(false), [action, setAction] = useState<Action | null>(null)
  const [position, setPosition] = useState({ top: 0, left: 0 })
  const [replacement, setReplacement] = useState<File | null>(null), [target, setTarget] = useState(''), [reason, setReason] = useState(''), [error, setError] = useState('')
  const trigger = useRef<HTMLButtonElement>(null), menu = useRef<HTMLDivElement>(null)
  const me = useQuery({ queryKey: ['me'], queryFn: api.me })
  const customers = useQuery({ queryKey: ['customers'], queryFn: api.customers, enabled: action === 'move' })
  const canChange = !!me.data?.effective_roles.includes('importer') && !CLOSED_STATUSES.includes(status)
  const change = useMutation({
    mutationFn: async () => {
      if (action === 'replace' && replacement) return api.replaceFile(roundId, file.id, replacement, reason)
      if (action === 'move') return api.moveFile(roundId, file.id, target, reason)
      if (action === 'cancel') return api.cancelFile(roundId, file.id, reason)
      throw new Error('Choose an action and a file')
    },
    onSuccess: value => {
      setAction(null)
      void qc.invalidateQueries()
      navigate(`/rounds/${value.id}`)
      toast.push({ kind: 'ok', title: t('File updated', 'บันทึกแล้ว'), detail: value.job_ids.length ? t('Preparing new results. Questions will appear if anything needs confirmation.', 'กำลังสร้างผลลัพธ์ใหม่ ถ้ามีเรื่องต้องยืนยัน ระบบจะแสดงคำถามให้ตรวจ') : t('This file is no longer in use. Its history is still available.', 'ยกเลิกการใช้แล้ว ยังเปิดดูไฟล์และประวัติย้อนหลังได้') })
      void Promise.all(value.job_ids.map(id => waitForJob(id))).then(jobs => {
        void qc.invalidateQueries()
        if (jobs.some(job => job.status === 'failed')) toast.push({ kind: 'bad', title: t('Some files need checking', 'มีไฟล์ที่ประมวลผลไม่สำเร็จ'), detail: t('Open the file to check the problem and retry.', 'เปิดไฟล์เพื่อดูปัญหาและลองใหม่ได้') })
      }).catch(() => { void qc.invalidateQueries() })
    },
    onError: () => setError(t('Could not update the file. It may still be processing or have a newer version. Refresh and try again.', 'ยังทำรายการไม่ได้ ไฟล์อาจกำลังประมวลผลหรือมีเวอร์ชันใหม่แล้ว ลองรีเฟรชแล้วทำอีกครั้ง')),
  })
  useEffect(() => {
    if (!open) return
    menu.current?.querySelector<HTMLButtonElement>('button')?.focus()
    const outside = (e: PointerEvent) => { if (!menu.current?.contains(e.target as Node) && !trigger.current?.contains(e.target as Node)) setOpen(false) }
    const away = () => setOpen(false)
    document.addEventListener('pointerdown', outside)
    document.addEventListener('scroll', away, true)
    window.addEventListener('resize', away)
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('scroll', away, true); window.removeEventListener('resize', away) }
  }, [open])
  const choose = (next: Action) => {
    setOpen(false); setAction(next); setReplacement(null); setTarget(''); setError(''); change.reset()
    setReason(next === 'replace' ? t('Corrected file from customer', 'ได้รับไฟล์แก้ไขจากลูกค้า') : next === 'move' ? t('Wrong customer selected', 'เลือกลูกค้าผิด') : t('Uploaded by mistake', 'อัปโหลดผิด'))
  }
  const title = action === 'replace' ? t('Replace this file', 'แทนที่ไฟล์นี้') : action === 'move' ? t('Move to another customer', 'ย้ายไฟล์ไปลูกค้าอื่น') : t('Stop using this file', 'ยกเลิกการใช้ไฟล์นี้')
  const close = () => { if (!change.isPending) setAction(null) }
  const menuButton = (next: Action, Icon: typeof Eye, label: string) => <button role="menuitem" className={next === 'cancel' ? 'text-bad' : undefined} onClick={() => choose(next)}><Icon size={16} />{label}</button>
  return <>
    <button ref={trigger} type="button" className="btn btn-ghost btn-sm" aria-label={t(`Manage file: ${file.name}`, `จัดการไฟล์: ${file.name}`)} aria-haspopup="menu" aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => {
      const rect = trigger.current?.getBoundingClientRect()
      if (!rect) return
      setPosition({ left: Math.max(8, Math.min(rect.right - 230, window.innerWidth - 238)), top: rect.bottom + 190 < window.innerHeight ? rect.bottom + 6 : Math.max(8, rect.top - 190) })
      setOpen(!open)
    }}><Ellipsis size={16} /></button>
    {open && createPortal(<div ref={menu} id={id} role="menu" aria-label={t('File actions', 'จัดการไฟล์')} className="chat-menu" style={position} onKeyDown={e => {
      if (e.key === 'Escape') { e.preventDefault(); setOpen(false); trigger.current?.focus() }
      if (e.key === 'Tab') setOpen(false)
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault()
        const buttons = [...e.currentTarget.querySelectorAll<HTMLButtonElement>('button')]
        const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
        buttons[(index + (e.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length]?.focus()
      }
    }}>
      <button role="menuitem" onClick={() => { setOpen(false); if (onView) onView(); else navigate(`/rounds/${roundId}?file=${encodeURIComponent(file.id)}`) }}><Eye size={16} />{t('View file', 'ดูไฟล์')}</button>
      {canChange && <>{menuButton('replace', FileUp, t('Replace this file', 'แทนที่ไฟล์นี้'))}{menuButton('move', ArrowRightLeft, t('Move to another customer', 'ย้ายลูกค้า'))}<hr className="my-1 border-line" />{menuButton('cancel', XCircle, t('Stop using this file', 'ยกเลิกการใช้'))}</>}
    </div>, document.body)}
    <Modal open={!!action} onClose={close} title={title} width="max-w-xl" footer={<><button className="btn" disabled={change.isPending} onClick={close}>{t('Back', 'กลับ')}</button><button className={action === 'cancel' ? 'btn btn-danger' : 'btn btn-primary'} disabled={change.isPending || !reason.trim() || (action === 'replace' && !replacement) || (action === 'move' && !target)} onClick={() => change.mutate()}>{change.isPending && <Spinner />}{action === 'replace' ? t('Confirm replacement', 'ยืนยันแทนที่ไฟล์') : action === 'move' ? t('Confirm move', 'ยืนยันย้ายลูกค้า') : t('Confirm cancellation', 'ยืนยันยกเลิกการใช้')}</button></>}>
      <div className="space-y-4 text-left">
        <dl className="grid sm:grid-cols-2 gap-4 border border-line rounded-lg p-4"><div className="min-w-0"><dt className="text-xs text-muted mb-1">{t('Current file', 'ไฟล์เดิม')}</dt><dd className="font-medium break-words">{file.name}</dd><dd className="text-xs text-muted mt-1">{fmtBytes(file.size)}</dd></div>{action === 'replace' && <div className="min-w-0"><dt className="text-xs text-muted mb-1">{t('Replacement file', 'ไฟล์ใหม่')}</dt><dd className="font-medium break-words">{replacement?.name || t('Choose a file below', 'เลือกไฟล์ด้านล่าง')}</dd>{replacement && <dd className="text-xs text-muted mt-1">{fmtBytes(replacement.size)}</dd>}</div>}</dl>
        {action === 'replace' && <label className="grid gap-2 text-sm">{t('Choose the corrected file', 'เลือกไฟล์ที่แก้ไขแล้ว')}<input className="input" type="file" accept={UPLOAD_ACCEPT} onChange={e => { const picked = e.target.files?.[0]; setError(''); if (!picked) { setReplacement(null); return } if (splitReadable([picked]).refused.length) { setReplacement(null); setError(t('Choose a supported Excel or CSV file.', 'เลือกไฟล์ Excel หรือ CSV ที่ระบบรองรับ')); return } setReplacement(picked) }} /></label>}
        {action === 'move' && <label className="grid gap-2 text-sm">{t('Correct customer', 'ลูกค้าที่ถูกต้อง')}<select className="input" value={target} onChange={e => setTarget(e.target.value)}><option value="">{t('Choose a customer', 'เลือกลูกค้า')}</option>{customers.data?.filter(c => c.id !== customerId).map(c => <option key={c.id} value={c.id}>{c.code} · {c.name}</option>)}</select>{customers.isError && <button className="text-left underline" onClick={() => void customers.refetch()}>{t('Load customers again', 'โหลดรายชื่อลูกค้าอีกครั้ง')}</button>}</label>}
        <p className="text-sm leading-relaxed">{action === 'replace' ? t('The selected file will be replaced and new results created. The old file and results remain in history.', 'ระบบจะใช้ไฟล์ใหม่แทนไฟล์ที่เลือก แล้วสร้างผลลัพธ์ใหม่ ไฟล์และผลลัพธ์เดิมยังดูย้อนหลังได้') : action === 'move' ? t('This file will be processed for the selected customer. Its previous results will stop being used.', 'ระบบจะประมวลผลไฟล์นี้ให้ลูกค้าที่เลือก และเลิกใช้ผลลัพธ์เดิม') : t('This file and its results will stop being used. The file and history are kept.', 'ระบบจะเลิกใช้ไฟล์นี้และผลลัพธ์ของมัน โดยเก็บไฟล์และประวัติไว้ให้ดูย้อนหลัง')}</p>
        {fileCount > 1 && <p className="text-sm text-muted">{t(`The other ${fileCount - 1} file(s) stay with the same customer. Their combined results will be checked again.`, `อีก ${fileCount - 1} ไฟล์ยังอยู่กับลูกค้าเดิม ระบบจะสร้างและตรวจผลลัพธ์รวมอีกครั้ง`)}</p>}
        <label className="grid gap-2 text-sm">{t('Reason', 'เหตุผล')}<input className="input" value={reason} maxLength={500} onChange={e => setReason(e.target.value)} /></label>
        {error && <p className="text-sm text-bad" role="alert">{error}</p>}
      </div>
    </Modal>
  </>
}
