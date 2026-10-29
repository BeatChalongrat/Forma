import { useEffect, useState } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowRight, Plus } from 'lucide-react'
import { api } from '../lib/api'
import { usePreferences } from '../lib/preferences'
import { Badge, PageHeader, Spinner } from './ui'

export function WorkRounds() {
  const { t, date } = usePreferences()
  const [params, setParams] = useSearchParams()
  const [search, setSearch] = useState(params.get('round_search') || '')
  const term = params.get('round_search') || '', start = params.get('round_start') || '', end = params.get('round_end') || ''
  const invalid = !!(start && end && start > end)
  useEffect(() => { setSearch(term) }, [term])
  useEffect(() => {
    if (search === term) return
    const timer = setTimeout(() => setParams(p => { const n = new URLSearchParams(p); search ? n.set('round_search', search) : n.delete('round_search'); return n }, { replace: true }), 300)
    return () => clearTimeout(timer)
  }, [search, term, setParams])
  const list = useInfiniteQuery({ queryKey: ['work-rounds', term, start, end], initialPageParam: 0, queryFn: ({ pageParam }) => api.uploads({ offset: pageParam, limit: 20, search: term, start: start || undefined, end: end || undefined }), getNextPageParam: (page, pages) => page.length === 20 ? pages.length * 20 : undefined, enabled: !invalid, refetchInterval: 10000 })
  const change = (key: string, value: string) => setParams(p => { const n = new URLSearchParams(p); value ? n.set(key, value) : n.delete(key); return n }, { replace: true })
  const rows = list.data?.pages.flat() ?? []
  return <>
    <PageHeader title={t('Work rounds', 'รอบงาน')} subtitle={t('Choose a named date to focus on. Add files to the same round over several uploads.', 'เลือกรอบตามชื่อและวันที่ อัปโหลดเพิ่มเข้ารอบเดิมได้หลายครั้งจนไฟล์ครบ')} actions={<Link className="btn btn-primary" to="/imports/new"><Plus size={16} />{t('Upload files', 'อัปโหลดไฟล์')}</Link>} />
    <div className="flex gap-2 mb-4"><span className="btn bg-signal-soft" aria-current="page">{t('Work rounds', 'ตามรอบงาน')}</span><Link className="btn btn-ghost" to="/imports?view=files">{t('All files', 'ไฟล์ทั้งหมด')}</Link></div>
    <div className="card p-4 mb-4 grid gap-3 sm:grid-cols-3">
      <label className="text-sm grid gap-1">{t('Round name', 'ค้นหาชื่อรอบงาน')}<input className="input" value={search} maxLength={200} onChange={e => setSearch(e.target.value)} /></label>
      <label className="text-sm grid gap-1">{t('Round date from', 'วันที่รอบงาน ตั้งแต่')}<input className="input" type="date" value={start} onChange={e => change('round_start', e.target.value)} /></label>
      <label className="text-sm grid gap-1">{t('Through', 'ถึงวันที่')}<input className="input" type="date" value={end} onChange={e => change('round_end', e.target.value)} /></label>
      <p className="text-xs text-muted sm:col-span-3">{t('Round dates are labels you choose, separate from actual upload dates. Older rounds without a date remain visible when no date filter is selected.', 'วันที่รอบงานคือวันที่คุณเลือก แยกจากวันที่อัปโหลดจริง รอบเก่าที่ยังไม่มีวันที่ดูได้เมื่อไม่กรองวันที่')}</p>
      <button className="btn btn-sm justify-self-start" onClick={() => { setSearch(''); setParams({}) }}>{t('Clear filters', 'ล้างตัวกรอง')}</button>
    </div>
    <div className="h-6 mb-2 text-xs text-muted flex justify-end gap-2" role="status">{list.isFetching && !invalid && <><Spinner />{t('Updating…', 'กำลังอัปเดต…')}</>}</div>
    {invalid ? <p role="alert" className="text-bad">{t('End date must follow start date.', 'วันที่สิ้นสุดต้องไม่ก่อนวันที่เริ่มต้น')}</p> : <div className="grid gap-3">
      {list.isError && <div role="alert" className="card p-4 text-bad">{t('Could not load rounds.', 'โหลดรอบงานไม่ได้')} <button className="btn btn-sm" onClick={() => void list.refetch()}>{t('Retry', 'ลองใหม่')}</button></div>}
      {!list.isPending && !list.isError && !rows.length && <div className="card p-8 text-muted text-center">{t('No matching work rounds. You can also search older individual files in All files.', 'ไม่พบรอบงานที่ตรงกัน ดูไฟล์เก่าแบบรายไฟล์ได้ที่ “ไฟล์ทั้งหมด”')}</div>}
      {rows.map(b => { const needs = (b.counts.needs_review || 0) + (b.counts.needs_data_check || 0) + (b.counts.needs_customer || 0); return <article className="card p-5" key={b.id}>
        <div className="flex flex-wrap items-center gap-3"><Link className="flex-1 min-w-0 font-semibold text-navy hover:underline break-words" to={'/uploads/' + b.id}>{b.label}</Link><Badge tone={b.complete ? 'ok' : b.stuck || b.counts.failed ? 'bad' : 'neutral'}>{b.complete ? t('Received files ready', 'ไฟล์ที่รับมาพร้อมแล้ว') : b.stuck ? t('Processing stalled', 'มีงานค้าง') : t('In progress', 'ยังมีงานต้องทำ')}</Badge></div>
        <p className="text-xs text-muted mt-2">{t('Round date', 'วันที่รอบงาน')}: {b.work_date || t('Not set', 'ยังไม่ได้กำหนด')} · {t('Created', 'สร้างเมื่อ')} {date(b.created_at)} · {b.total} {t('files', 'ไฟล์')}{b.expected_files !== null && <span> · {t('Target', 'เป้า')} {b.expected_files} · {t('Missing', 'ยังขาด')} {Math.max(0, b.expected_files - b.total)}</span>}</p>
        <div className="flex flex-wrap gap-x-6 gap-y-2 my-4 text-sm"><span>{t('Needs you', 'รอคุณตอบ')} <strong>{needs}</strong></span><span>{t('Processing', 'กำลังทำ')} <strong>{b.counts.processing || 0}</strong></span><span className="text-ok">{t('Ready', 'พร้อมดาวน์โหลด')} <strong>{b.counts.ready || 0}</strong></span><span className={b.counts.failed ? 'text-bad' : 'text-muted'}>{t('Failed', 'มีปัญหา')} <strong>{b.counts.failed || 0}</strong></span></div>
        <div className="flex flex-wrap gap-2"><Link className="btn btn-primary btn-sm" to={'/uploads/' + b.id}>{t('Open round', 'ทำงานในรอบนี้')}<ArrowRight size={14} /></Link><Link className="btn btn-sm" to={'/imports/new?batch=' + b.id}><Plus size={14} />{t('Add files', 'เพิ่มไฟล์เข้ารอบ')}</Link></div>
      </article> })}
      {list.hasNextPage && <button className="btn justify-self-center" disabled={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()}>{t('Show older rounds', 'ดูรอบเก่าเพิ่มเติม')}</button>}
    </div>}
  </>
}
