import { useRef, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowLeft, ArrowRight, FileSpreadsheet, Monitor, Pause, Play, RefreshCw } from 'lucide-react'
import { activityApi } from '../lib/api'
import { actions, activityLabel, isLive, station, type ActivityJob, type Receipt, type Station } from '../lib/activity'
import { PageHeader } from '../components/ui'
import './Activity.css'

const desks: { id: Station; name: string; sub: string }[] = [
  { id: 'analyst', name: '01 · สำรวจข้อมูล', sub: 'Evidence analyst' },
  { id: 'mapper', name: '02 · จับคู่ข้อมูล', sub: 'Mapping agent' },
  { id: 'reviewer', name: '03 · ตรวจความหมาย', sub: 'Semantic reviewer' },
  { id: 'system', name: 'รับไฟล์ / ตรวจด้วยกฎ', sub: 'System' },
  { id: 'human', name: 'รอคุณยืนยัน', sub: 'Human review' },
  { id: 'ready', name: 'ผลลัพธ์พร้อมใช้', sub: 'Published output' },
]
const states: Record<string, string> = { queued: 'รอคิว', running: 'กำลังทำ', done: 'จบงานประมวลผล', failed: 'ทำไม่สำเร็จ', cancelled: 'หยุดแล้ว', started: 'เริ่ม', completed: 'เสร็จ', info: 'บันทึก' }
const time = (v: string) => new Date(v).toLocaleString('th-TH', { hour12: false })
const filename = (j: ActivityJob) => j.files.join(', ') || j.id
function Data({ value }: { value: unknown }) { return <pre className="office-data">{JSON.stringify(value, null, 2) ?? 'ไม่มีข้อมูล'}</pre> }

export function ActivityPage() {
  const [params, setParams] = useSearchParams()
  const batch = params.get('batch') || '', round = params.get('round') || '', selected = params.get('job') || ''
  const status = params.get('status') || '', search = params.get('q') || '', offset = Math.max(0, Number(params.get('offset')) || 0)
  const attempt = params.get('attempt') || '', before = params.get('before') || ''
  const [paused, setPaused] = useState(false), [reduced, setReduced] = useState(false)
  const [tab, setTab] = useState('activity'), [picked, setPicked] = useState<Receipt | null>(null)
  const [desk, setDesk] = useState<Station | null>(null)
  const inspector = useRef<HTMLElement>(null), room = useRef<HTMLDivElement>(null)
  function change(values: Record<string, string>, replace = false) {
    setParams(current => { const next = new URLSearchParams(current); Object.entries(values).forEach(([k, v]) => v ? next.set(k, v) : next.delete(k)); return next }, { replace })
  }
  const filters = { batch, round_id: round, search, status, offset }
  const list = useQuery({ queryKey: ['activity-jobs', filters], queryFn: () => activityApi.jobs(filters), placeholderData: keepPreviousData, refetchInterval: paused ? false : 3000 })
  const live = useQuery({ queryKey: ['activity-live', batch, round], queryFn: () => activityApi.jobs({ batch, round_id: round, status: 'running', limit: 100 }), refetchInterval: paused ? false : 3000 })
  const detail = useQuery({ queryKey: ['activity-detail', selected, attempt, before], queryFn: () => activityApi.detail(selected, attempt, before), enabled: !!selected, refetchInterval: paused || before ? false : 3000 })
  const currentJob = list.data?.items.find(j => j.id === selected) || live.data?.items.find(j => j.id === selected)
  const rows = list.data?.items || []
  const active = live.data?.items || []
  const roomJobs = [...new Map([...rows, ...active].map(j => [j.id, j])).values()]
  const events = detail.data?.events || [], event = picked?.job_id === selected && picked.attempt === detail.data?.attempt ? picked : events.at(-1)
  const pair = useQuery({ queryKey: ['activity-span', selected, event?.span_id], queryFn: () => activityApi.span(selected, event!.span_id), enabled: !!event, refetchInterval: paused ? false : 3000 })
  const historical = !!attempt && Number(attempt) !== detail.data?.job.attempts
  function open(job: ActivityJob, receipt?: Receipt | null) {
    change({ job: job.id, attempt: '', before: '' }); setPicked(receipt || null); setTab('activity')
    if (window.matchMedia('(max-width: 1100px)').matches) setTimeout(() => inspector.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50)
  }
  return <div className={`agent-office ${reduced || paused ? 'office-still' : ''}`}>
    <Link className="btn btn-ghost btn-sm mb-4" to={batch ? `/uploads/${encodeURIComponent(batch)}` : round ? `/rounds/${encodeURIComponent(round)}` : '/imports'}><ArrowLeft size={15} />{batch ? 'กลับรอบงานนี้' : round ? 'กลับไฟล์นี้' : 'กลับประวัติไฟล์'}</Link>
    <PageHeader title="ห้องทำงาน FORMA" subtitle="ดูงานที่กำลังทำ กดโต๊ะหรือไฟล์เพื่อเปิดเครื่องมือ หลักฐาน และประวัติการทำงานจริง" actions={<button className="btn" onClick={() => setPaused(v => !v)}>{paused ? <Play size={15} /> : <Pause size={15} />}{paused ? 'ติดตามต่อ' : 'พักการแสดงผล'}</button>} />
    <div className="office-summary">
      {['queued', 'running', 'done', 'failed'].map(s => <button key={s} aria-pressed={status === s} onClick={() => change({ status: status === s ? '' : s, offset: '' })}><span>{states[s]}</span><strong>{list.data ? list.data.counts[s] || 0 : '—'}</strong><small>งาน</small></button>)}
    </div>
    <p className="office-note">นับงานประมวลผล ไม่ใช่จำนวนไฟล์ที่อนุมัติ · ไฟล์หนึ่งอาจมีหลายงาน · {paused ? 'พักเฉพาะหน้าจอ งานยังทำต่อ' : 'อัปเดตทุก 3 วินาที'}{list.dataUpdatedAt ? ` · ล่าสุด ${new Date(list.dataUpdatedAt).toLocaleTimeString('th-TH')}` : ''}</p>
    {(list.isError || live.isError || list.data?.journal_available === false || live.data?.journal_available === false) && <p role="alert" className="office-warning">เชื่อมต่อข้อมูลกิจกรรมไม่ได้ ข้อมูลที่เห็นอาจไม่ล่าสุด <button className="btn btn-sm" onClick={() => { void list.refetch(); void live.refetch() }}>ลองใหม่</button></p>}
    <div className="office-layout">
      <section className="office-left">
        <div className="office-room" ref={room}>
          <div className="office-room-heading"><strong>LIVE · พื้นที่ทำงาน</strong><label><input type="checkbox" checked={reduced} onChange={e => setReduced(e.target.checked)} /> ลดการเคลื่อนไหว</label></div>
          <div className="office-desks">
            {desks.map(d => {
              const assigned = roomJobs.filter(j => station(j) === d.id)
              const working = assigned.filter(j => isLive(j)), focus = working.find(j => j.id === selected) || working[0]
              return <div className={`office-station office-${d.id} ${focus && !live.isError ? 'office-busy' : ''}`} key={d.id}>
                {['analyst', 'mapper', 'reviewer'].includes(d.id) && <button className="office-bubble" onClick={() => { setDesk(d.id); if (focus) open(focus, focus.latest) }}>
                  <small>{d.sub}</small><strong>{focus ? activityLabel(focus.latest) : 'ยังไม่มีงานที่กำลังทำ'}</strong>
                  <span>{focus ? filename(focus) : 'จะแสดงเมื่อมีเหตุการณ์จาก worker'}</span>
                  {focus && <small>{actions[focus.latest?.action || '']?.[1]} · {working.length} งาน · ดูรายละเอียด →</small>}
                </button>}
                <button className="office-desk-button" aria-pressed={desk === d.id} onClick={() => { setDesk(desk === d.id ? null : d.id); if (focus) open(focus, focus.latest) }}>
                  <span className="office-desk-name">{d.name}</span>
                  <span className="office-furniture" aria-hidden="true"><i className="office-monitor" /><i className="office-keyboard" /><i className="office-paper" /></span>
                  <span className="office-person" aria-hidden="true"><i /></span>
                  <small>{working.length ? `${working.length} งานกำลังทำ` : `${assigned.length} งานในรายการที่แสดง`}</small>
                </button>
                <div className="office-tokens">{assigned.slice(0, 4).map(j => <button key={j.id} aria-label={`เปิด ${filename(j)}`} title={filename(j)} aria-pressed={selected === j.id} onClick={() => open(j)}><FileSpreadsheet size={16} /><span>{filename(j)}</span></button>)}{assigned.length > 4 && <small>และอีก {assigned.length - 4} งาน</small>}</div>
              </div>
            })}
          </div>
          <p className="office-note">รูปแบบที่เคยอนุมัติอาจใช้การจับคู่เดิมและข้ามโต๊ะ AI แต่ยังตรวจข้อมูล · กรอบแสดงกิจกรรมจากเครื่องมือ ไม่ใช่ข้อความความคิดภายในโมเดล</p>
          {currentJob && <div className="office-follow"><strong>กำลังติดตาม: {filename(currentJob)}</strong><div className="office-follow-track"><span style={{ left: `${desks.findIndex(d => d.id === station(currentJob)) * 18}%` }}><FileSpreadsheet size={18} /></span></div><small>{desks.find(d => d.id === station(currentJob))?.name} · ตำแหน่งเปลี่ยนตามสถานะจริง</small></div>}
        </div>
        <form className="office-filter" onSubmit={e => { e.preventDefault(); change({ q: String(new FormData(e.currentTarget).get('q') || '').trim(), offset: '' }) }}><label className="sr-only" htmlFor="office-search">ค้นชื่อไฟล์</label><input key={search} id="office-search" name="q" className="input" defaultValue={search} placeholder="ค้นชื่อไฟล์…" /><button className="btn">ค้นหา</button><button className="btn btn-ghost" type="button" onClick={() => { setDesk(null); change({ q: '', status: '', offset: '' }) }}>ทั้งหมด</button></form>
        {desk && <p className="office-note">โต๊ะ: {desks.find(d => d.id === desk)?.name} <button className="underline" onClick={() => setDesk(null)}>เลิกกรองโต๊ะ</button></p>}
        <div className="office-files">{rows.filter(j => !desk || station(j) === desk).map(j => <button key={j.id} aria-pressed={selected === j.id} onClick={() => open(j)}><FileSpreadsheet size={20} /><span><strong>{filename(j)}</strong><small>{states[j.status] || j.status} · ครั้งที่ {j.attempts} · {j.format_memory?.status === 'reused' ? 'ใช้รูปแบบเดิม · ' : ''}{j.status === 'running' && !isLive(j) ? 'สัญญาณ worker ขาด รอระบบตรวจสอบ' : activityLabel(j.latest)}</small></span><ArrowRight size={16} /></button>)}</div>
        {!rows.length && <p className="office-empty">{list.isPending ? 'กำลังโหลดงาน…' : 'ไม่พบงานตามตัวกรองนี้'}</p>}
        <div className="office-pager"><button className="btn btn-sm" disabled={!offset} onClick={() => change({ offset: String(Math.max(0, offset - 50)) })}>ก่อนหน้า</button><span>{list.data?.total || 0} งานที่พบ</span><button className="btn btn-sm" disabled={list.data?.next_offset == null} onClick={() => change({ offset: String(list.data?.next_offset) })}>ถัดไป</button></div>
      </section>
      <aside className="office-inspector" ref={inspector} tabIndex={-1} aria-label="รายละเอียดกิจกรรม">
        <button className="btn btn-ghost btn-sm office-back-room" onClick={() => room.current?.scrollIntoView({ behavior: 'smooth' })}><ArrowLeft size={14} />กลับห้องทำงาน</button>
        {!selected ? <div className="office-empty"><Monitor size={32} /><h2>เลือกโต๊ะหรือไฟล์</h2><p>ดูว่ากำลังทำอะไร ใช้หลักฐานไหน และได้ผลอย่างไร</p></div> : <>
          <h2>{currentJob ? filename(currentJob) : selected}</h2>
          {detail.isError && <p role="alert">โหลดรายละเอียดไม่ได้ <button className="btn" onClick={() => void detail.refetch()}><RefreshCw size={14} />ลองใหม่</button></p>}
          {detail.isPending && <p>กำลังโหลดรายละเอียด…</p>}
          {detail.data && <>
            <Link className="btn btn-sm" to={detail.data.job.round_id ? `/inspection?scope=round&id=${detail.data.job.round_id}&event=job:${selected}` : `/inspection?search=${encodeURIComponent(selected)}`}>ประวัติงานทั้งหมด · คำตอบ การแก้ไข และผลลัพธ์</Link>
            <div className="office-inspector-meta"><span>{states[detail.data.job.status]} · {detail.data.job.kind}</span><label>การทำงานครั้งที่ <select className="input" value={detail.data.attempt} onChange={e => change({ attempt: e.target.value, before: '' })}>{detail.data.attempts.map(n => <option key={n} value={n}>{n}</option>)}</select></label></div>
            {historical && <p className="office-warning">ดูประวัติครั้งก่อน · สถานะด้านบนเป็นสถานะงานล่าสุด</p>}
            <nav className="office-tabs" aria-label="รายละเอียด"><button aria-pressed={tab === 'activity'} onClick={() => setTab('activity')}>กิจกรรมและ log</button><button aria-pressed={tab === 'mapping'} onClick={() => setTab('mapping')}>การจับคู่ / ผลตรวจ</button><button aria-pressed={tab === 'result'} onClick={() => setTab('result')}>ผลลัพธ์งาน</button></nav>
            {tab === 'activity' && <>
              {!events.length && <p className="office-empty">ไม่มีบันทึกละเอียดสำหรับครั้งนี้ งานที่ทำก่อนเพิ่มหน้าติดตามยังเปิดผลลัพธ์และ log เดิมได้ที่แท็บการจับคู่</p>}
              <div className="office-timeline">{events.map(e => <button key={e.id} aria-pressed={event?.id === e.id} onClick={() => setPicked(e)}><span className={`office-event-dot office-event-${e.state}`} /><span><strong>{activityLabel(e)}</strong><small>{time(e.at)} · {e.role} · {states[e.state] || e.state}{e.duration_ms != null ? ` · ${(e.duration_ms / 1000).toFixed(2)} วินาที` : ''}</small></span></button>)}</div>
              <div className="office-pager"><button className="btn btn-sm" disabled={!detail.data.previous_cursor} onClick={() => { setPicked(null); change({ before: String(detail.data?.previous_cursor) }) }}>เก่ากว่านี้</button>{before && <button className="btn btn-sm" onClick={() => { setPicked(null); change({ before: '' }) }}>กลับล่าสุด</button>}</div>
              {event && <section className="office-evidence"><h3>{activityLabel(event)}</h3><p>{actions[event.action]?.[1] || event.action} · {states[event.state] || event.state}</p><small>{time(event.at)} · event #{event.id} · ครั้งที่ {event.attempt}</small>{pair.isError && <p role="alert">โหลดข้อมูลเริ่ม–จบไม่ได้ <button className="underline" onClick={() => void pair.refetch()}>ลองใหม่</button></p>}{(pair.data || [event]).map(r => <div key={r.id}><h3>{r.state === 'started' ? 'ข้อมูลที่ใช้ / คำสั่งที่เรียก' : r.state === 'failed' ? 'ข้อผิดพลาดที่เกิดขึ้น' : 'ผลที่ได้ / บันทึก'}</h3><Data value={r.detail} /></div>)}<p className="office-note">ข้อมูลเป็นหลักฐานจากการทำงาน อาจถูกจำกัดขนาดตามที่ระบุในผล ไม่ใช่การอนุมัติข้อมูล</p></section>}
            </>}
            {tab === 'mapping' && (detail.data.mapping ? <div className="office-evidence"><h3>คำถามที่ต้องยืนยัน</h3><Data value={detail.data.mapping.questions} /><h3>การจับคู่ที่บันทึก</h3><Data value={detail.data.mapping.document} /><h3>ผลตรวจและการตัดสิน</h3><Data value={{ validation: detail.data.mapping.validation, policy: detail.data.mapping.policy }} /><h3>บันทึกจาก agent</h3><Data value={detail.data.mapping.agent_log} /></div> : <p className="office-empty">ยังไม่มีผลการจับคู่ที่บันทึกสำหรับงานครั้งนี้ ดูหลักฐานระหว่างทำใน log ได้</p>)}
            {tab === 'result' && <div className="office-evidence"><h3>{historical ? 'ผลครั้งนี้อยู่ในเหตุการณ์จบงาน' : 'ผลที่บันทึกในระบบ'}</h3><Data value={historical ? events.filter(e => e.action === 'job' && e.state !== 'started').map(e => e.detail) : { result: detail.data.job.result, error: detail.data.job.error }} /></div>}
            {detail.data.job.round_id && <div className="office-next"><Link className="btn" to={`/rounds/${detail.data.job.round_id}?activityJob=${selected}`}>ดูไฟล์และผลลัพธ์ <ArrowRight size={15} /></Link><Link className="btn" to={`/review?round=${detail.data.job.round_id}`}>ไปตอบคำถาม <ArrowRight size={15} /></Link></div>}
          </>}
        </>}
      </aside>
    </div>
  </div>
}
