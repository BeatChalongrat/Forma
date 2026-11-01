import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useLocation } from 'react-router-dom'
import { ArrowRight, CircleAlert, CircleCheck, TriangleAlert, X } from 'lucide-react'
import { api, ApiError, type BatchSummary } from '../lib/api'
import { usePreferences } from '../lib/preferences'
import { cn, fmtInt } from '../lib/utils'

const STORAGE = 'forma.tracked-round'
type Tracked = { id: string; quiet: boolean }

function readTracked(): Tracked | null {
  try {
    const data: unknown = JSON.parse(sessionStorage.getItem(STORAGE) || 'null')
    return data && typeof data === 'object' && 'id' in data && typeof data.id === 'string' ? { id: data.id, quiet: 'quiet' in data && data.quiet === true } : null
  } catch { return null }
}

const stalled = (b: BatchSummary) => b.stuck > 0 && b.stuck === (b.counts.processing ?? 0)
const working = (b: BatchSummary) => !b.done && !stalled(b)
const waiting = (b: BatchSummary) => (b.counts.needs_customer ?? 0) + (b.counts.needs_review ?? 0) + (b.counts.needs_data_check ?? 0)

/** Follows a round that is still working across pages. It disappears on the round's own page once done, because that page has it all; elsewhere it stays until read. */
export function UploadCard() {
  const { t } = usePreferences(), { pathname } = useLocation()
  const here = pathname.match(/^\/uploads\/([^/]+)/)?.[1] ?? null
  const [tracked, setTracked] = useState(readTracked)
  const follow = (next: Tracked | null) => {
    setTracked(next)
    try { if (next) sessionStorage.setItem(STORAGE, JSON.stringify(next)); else sessionStorage.removeItem(STORAGE) } catch { /* private mode */ }
  }
  const seen = useQuery({ queryKey: ['upload-summary', here], queryFn: () => api.uploadSummary(here ?? ''), enabled: !!here && here !== tracked?.id, refetchInterval: 5000 })
  const round = useQuery({
    queryKey: ['upload-summary', tracked?.id], queryFn: () => api.uploadSummary(tracked?.id ?? ''), enabled: !!tracked, retry: false,
    refetchInterval: q => q.state.data?.done ? false : q.state.data && stalled(q.state.data) ? 10000 : 3000,
  })
  const b = round.data, gone = round.error instanceof ApiError && round.error.status === 404, read = !!b && b.done && here === b.id
  useEffect(() => { if (here && seen.data?.id === here && working(seen.data)) follow({ id: here, quiet: false }) }, [here, seen.data])
  useEffect(() => { if (gone || read) follow(null) }, [gone, read])
  if (!tracked || !b || b.id !== tracked.id || (here === b.id && stalled(b)) || (tracked.quiet && working(b))) return null

  const need = waiting(b), failed = b.counts.failed ?? 0, ready = b.counts.ready ?? 0, left = b.counts.processing ?? 0, finished = b.total - left
  const running = working(b), stop = stalled(b)
  const close = stop ? null : <button type="button" className="upload-card-close" aria-label={t('Close', 'ปิด')} title={t('Close', 'ปิด')} onClick={() => running ? follow({ id: b.id, quiet: true }) : follow(null)}><X size={15} /></button>
  const open = !running && !stop && need ? { to: `/uploads/${b.id}?state=needs_you`, text: t('See the files waiting for you', 'ดูไฟล์ที่รอคุณ') }
    : !running && !stop && failed ? { to: `/uploads/${b.id}?state=failed`, text: t('See the files with problems', 'ดูไฟล์ที่มีปัญหา') }
      : { to: `/uploads/${b.id}`, text: stop ? t('Open this round to resume', 'เปิดรอบนี้เพื่อทำต่อ') : t('Open this round', 'เปิดรอบนี้') }

  return <aside className={cn('upload-card', pathname === '/assistant' && 'is-raised', stop && 'is-stopped')} aria-label={t('Upload progress', 'ความคืบหน้าการอัปโหลด')}>
    {stop ? <>
      <p className="upload-card-title" role="status"><TriangleAlert size={16} className="upload-card-gold" /><span>{t(`Stopped: ${fmtInt(b.stuck)} files waiting in the queue`, `งานหยุด ${fmtInt(b.stuck)} ไฟล์ค้างในคิว`)}</span></p>
      <p className="upload-card-note">{t('These files have not been worked on and nothing is lost. You can resume on the round page.', 'ระบบยังไม่ได้ทำไฟล์เหล่านี้ ไฟล์ไม่หาย กดทำต่อได้ที่หน้ารอบงาน')}</p>
    </> : running ? <>
      <div className="upload-card-top"><span className="brand-rule" /><span className="upload-card-label">{b.label}</span>{close}</div>
      <p className="upload-card-big"><b className="tnum">{fmtInt(finished)}</b><span className="tnum">/ {fmtInt(b.total)} {t('files', 'ไฟล์')}</span><span className="upload-card-map tnum">{t(`mapped ${fmtInt(b.mapped)}`, `map แล้ว ${fmtInt(b.mapped)}`)}</span></p>
      <div className="upload-card-line" role="progressbar" aria-label={t('Files finished', 'ไฟล์ที่ทำเสร็จ')} aria-valuemin={0} aria-valuemax={b.total} aria-valuenow={finished}><i style={{ width: `${b.total ? finished / b.total * 100 : 0}%` }} /></div>
    </> : <p className="upload-card-title" role="status">{need ? <CircleAlert size={16} className="upload-card-gold" /> : failed ? <TriangleAlert size={16} className="upload-card-gold" /> : <CircleCheck size={16} className="upload-card-lime" />}<span>{need ? t(`Done: ${fmtInt(need)} files need you`, `จบแล้ว รอคุณ ${fmtInt(need)} ไฟล์`) : failed ? t(`Done: ${fmtInt(failed)} files have problems`, `จบแล้ว มีปัญหา ${fmtInt(failed)} ไฟล์`) : t(`All ${fmtInt(b.total)} files are ready`, `เรียบร้อยทั้ง ${fmtInt(b.total)} ไฟล์`)}</span>{close}</p>}
    {!stop && (need || failed || running ? <div className="upload-card-stats tnum"><span>{t('Ready', 'พร้อมใช้')}<b>{fmtInt(ready)}</b></span><span className="upload-card-gold">{t('Need you', 'รอคุณ')}<b>{fmtInt(need)}</b></span><span>{t('Problems', 'มีปัญหา')}<b>{fmtInt(failed)}</b></span></div> : <p className="upload-card-note">{t('Every JSON is ready to download.', 'JSON พร้อมดาวน์โหลดครบ')}</p>)}
    {here !== b.id && <Link to={open.to} className="upload-card-open"><span>{open.text}</span><ArrowRight size={14} /></Link>}
  </aside>
}
