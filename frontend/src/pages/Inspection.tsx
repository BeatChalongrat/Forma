import { useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowLeft, ArrowRight, History, RefreshCw, Search } from 'lucide-react'
import { inspectionApi } from '../lib/api'
import { inspectionKinds, inspectionParams, inspectionStatuses } from '../lib/inspection'
import { activityLabel } from '../lib/activity'
import { PageHeader, Spinner } from '../components/ui'
import { usePreferences } from '../lib/preferences'
import './Inspection.css'

const labels: Record<string, string> = { source: 'วิธีที่ได้การจับคู่', decision: 'ผลการตัดสินใจ', self_check: 'การตรวจซ้ำ', verification: 'ผลตรวจการทำงาน', passed: 'ผ่านการตรวจ', agreement: 'จุดที่เห็นตรงกัน', reused_from: 'นำรูปแบบเดิมจาก', workflow: 'ขั้นตอนที่ใช้', tool: 'เครื่องมือ', items: 'รายการ', total: 'ทั้งหมด', offset: 'เริ่มที่รายการ', next_offset: 'หน้าถัดไปเริ่มที่',  before: 'ก่อนแก้', after: 'หลังแก้', decisions: 'คำตอบที่ยืนยัน', knowledge: 'ความรู้ที่นำมาใช้', questions: 'คำถามที่บันทึกไว้', logs: 'บันทึกการทำงาน', checks: 'วิธีทำงานและผลตรวจ', validation: 'ผลตรวจข้อมูล', effect_check: 'ผลกระทบจากคำตอบ', approved_by: 'ผู้อนุมัติ', approved_at: 'เวลาที่อนุมัติ', comment: 'บันทึกผู้ตรวจ', actor: 'ผู้ดำเนินการ', status: 'สถานะปัจจุบัน', target: 'ข้อมูลปลายทาง', answer: 'คำตอบ', reason: 'เหตุผลที่บันทึก', details: 'รายละเอียด', operations: 'ค่าที่แก้ไข', impact: 'ผลกระทบ', result: 'ผลการทำงาน', error: 'ข้อผิดพลาด', model: 'โมเดล', attempts: 'จำนวนครั้งที่ลอง', input_tokens: 'โทเคนขาเข้า', output_tokens: 'โทเคนขาออก', cost_usd: 'ต้นทุนที่ระบบบันทึก (USD)', latency_ms: 'เวลา (มิลลิวินาที)', row_count: 'จำนวนรายการ', template: 'แม่แบบ', sources: 'แหล่งข้อมูล', question: 'คำถามผู้ใช้', reply: 'คำตอบและข้อมูลอ้างอิง', action: 'กิจกรรม', detail: 'หลักฐานการทำงาน', input: 'ข้อมูลที่ใช้', output: 'ผลที่ได้', at: 'เวลา', state: 'สถานะเหตุการณ์', version: 'เวอร์ชัน', document: 'กฎการจับคู่', acknowledged: 'สิ่งที่ผู้ใช้ตรวจแล้ว', created_at: 'วันที่บันทึก', resolved_at: 'วันที่จบการตรวจ', resolver: 'ผู้ตรวจ', note: 'หมายเหตุ', target_schema: 'รูปแบบปลายทาง', created_by: 'ผู้สร้าง', author: 'ผู้แก้ไข' }
function Evidence({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value === null || value === undefined) return <span className="text-muted">ไม่ได้บันทึก</span>
  if (typeof value !== 'object') return <span className="whitespace-pre-wrap break-words">{typeof value === 'boolean' ? (value ? 'ใช่' : 'ไม่ใช่') : String(value)}</span>
  if (depth > 3) return <span className="text-muted">มีรายละเอียดเพิ่มเติมใน “ข้อมูลต้นฉบับ”</span>
  if (Array.isArray(value)) return value.length ? <ol className="space-y-3">{value.slice(0, 20).map((v, i) => <li key={i} className="border-b border-line pb-2"><Evidence value={v} depth={depth + 1} /></li>)}{value.length > 20 && <li>แสดง 20 จาก {value.length} รายการ ดูต่อในข้อมูลต้นฉบับ</li>}</ol> : <span className="text-muted">ไม่มีรายการ</span>
  return <dl className="space-y-3">{Object.entries(value).map(([k, v]) => <div key={k}><dt className="text-xs text-muted mb-1">{labels[k] || k.replaceAll('_', ' ')}</dt><dd><Evidence value={v} depth={depth + 1} /></dd></div>)}</dl>
}
function Receipt({ scope, id, event }: { scope: string; id: string; event: string }) {
  const [view, setView] = useState<'summary' | 'logs' | 'raw'>('summary'), [offset, setOffset] = useState(0)
  const split = event.indexOf(':'), kind = event.slice(0, split), key = event.slice(split + 1)
  const detail = useQuery({ queryKey: ['inspection-detail', scope, id, event, offset], queryFn: () => inspectionApi.detail(scope, id, kind, key, offset), enabled: split > 0 })
  if (detail.isPending) return <Spinner />
  if (detail.isError) return <div role="alert">เปิดเหตุการณ์นี้ไม่ได้ อาจถูกลบหรืออยู่นอกสิทธิ์ของคุณ <button className="btn btn-sm" onClick={() => void detail.refetch()}>ลองอีกครั้ง</button></div>
  const data = detail.data
  const logs = data.data.logs as { next_offset?: number | null; total?: number; items?: unknown[] } | undefined
  const important = kind === 'mapping' ? ['status', 'target_schema', 'approved_by', 'approved_at', 'comment', 'decisions', 'acknowledged', 'effect_check', 'questions', 'knowledge', 'checks', 'validation'] : Object.keys(data.data).filter(k => !['id', 'key', 'code', 'snapshot_id', 'parent_id', 'document', 'customer_id', 'conversation_id', 'turn_id', 'span_id', 'lease_token', 'lease_owner', 'dispatch_token', 'sha256'].includes(k))
  return <div className="space-y-4">
    <h3 className="text-lg font-semibold break-words">{data.title}</h3>
    <p className="text-xs text-muted">{data.key}</p>
    <div className="flex flex-wrap gap-2" role="group" aria-label="มุมมองรายละเอียด"><button className={`btn btn-sm ${view === 'summary' ? 'btn-primary' : ''}`} aria-pressed={view === 'summary'} onClick={() => setView('summary')}>หลักฐานและผลที่บันทึก</button>{logs && <button className={`btn btn-sm ${view === 'logs' ? 'btn-primary' : ''}`} aria-pressed={view === 'logs'} onClick={() => setView('logs')}>บันทึกเครื่องมือ ({logs.total || 0})</button>}<button className={`btn btn-sm ${view === 'raw' ? 'btn-primary' : ''}`} aria-pressed={view === 'raw'} onClick={() => setView('raw')}>ข้อมูลต้นฉบับ</button></div>
    {view === 'logs' ? <Evidence value={logs?.items || []} /> : view === 'raw' ? <pre className="text-xs whitespace-pre-wrap break-words bg-surface-2 p-3 rounded-lg">{JSON.stringify(data.data, null, 2)}</pre> : <div className="space-y-5 text-sm">{important.filter(k => data.data[k] !== undefined && data.data[k] !== null).map(k => <section className="border-b border-line pb-4" key={k}><h4 className="font-semibold mb-2">{labels[k] || k.replaceAll('_', ' ')}</h4><Evidence value={k === 'status' ? inspectionStatuses[String(data.data[k])] || data.data[k] : data.data[k]} /></section>)}</div>}
    {kind === 'mapping' && view !== 'summary' && <div className="flex gap-2"><button className="btn btn-sm" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 20))}>บันทึกก่อนหน้า</button><button className="btn btn-sm" disabled={logs?.next_offset == null} onClick={() => setOffset(logs!.next_offset!)}>บันทึกถัดไป</button></div>}
    <p className="text-xs text-muted">{data.notice}</p>
    <div className="flex flex-wrap gap-2">{data.links.map(link => <Link key={link.url} className="btn btn-sm" to={link.url}>{link.label}<ArrowRight size={14} /></Link>)}</div>
  </div>
}

export function InspectionPage() {
  const { date: time } = usePreferences()
  const timelinePanel = useRef<HTMLElement>(null), detailPanel = useRef<HTMLElement>(null)
  const reveal = (panel: HTMLElement | null) => { if (window.innerWidth <= 1150) panel?.scrollIntoView({ block: 'start', behavior: 'smooth' }) }
  const [params, setParams] = useSearchParams()
  const kind = params.get('kind') === 'assistant' ? 'assistant' : 'imports'
  const scope = params.get('scope') || '', id = params.get('id') || '', event = params.get('event') || '', cursor = params.get('cursor') || '', category = params.get('category') || ''
  const offset = Math.max(0, Number(params.get('offset')) || 0)
  const filters = Object.fromEntries(['search', 'customer', 'batch', 'status', 'start', 'end'].map(k => [k, params.get(k) || '']))
  const change = (values: Record<string, string>) => setParams(inspectionParams(params, values))
  const list = useQuery({ queryKey: ['inspection-items', kind, filters, offset], queryFn: () => inspectionApi.items({ ...filters, kind, offset: String(offset) }) })
  const timeline = useQuery({ queryKey: ['inspection-timeline', scope, id, cursor, category], queryFn: () => inspectionApi.timeline(scope, id, cursor, category), enabled: !!scope && !!id })
  const selectedEvent = timeline.data?.events.find(e => e.key === event)
  return <>
    <PageHeader title="ศูนย์ตรวจสอบย้อนหลัง" subtitle="เลือกงานเพื่อดูว่าเกิดอะไรขึ้น ใช้ข้อมูลอะไร และใครยืนยันอะไรไว้" actions={<button className="btn" onClick={() => { void list.refetch(); if (scope && id) void timeline.refetch() }}><RefreshCw size={15} />อัปเดตข้อมูล</button>} />
    <div className="flex flex-wrap gap-2 mb-4">{[['imports', 'ไฟล์ลูกค้า'], ['assistant', 'ผู้ช่วยและรายงานของฉัน']].map(([value, label]) => <button key={value} className={`btn ${kind === value ? 'btn-primary' : ''}`} aria-pressed={kind === value} onClick={() => setParams(new URLSearchParams({ kind: value }))}>{label}</button>)}<Link className="btn btn-ghost" to="/activity">เปิดห้องทำงาน</Link></div>
    <form key={kind + params.toString()} className="card p-4 mb-5 grid sm:grid-cols-2 lg:grid-cols-4 gap-3" onSubmit={e => { e.preventDefault(); const f = new FormData(e.currentTarget); change({ ...Object.fromEntries([...f.entries()].map(([k, v]) => [k, String(v)])), offset: '', cursor: '', event: '' }) }}>
      <label className="text-sm">ค้นหาชื่อไฟล์ / งาน / รหัส<input name="search" className="input w-full mt-1" defaultValue={filters.search} /></label>
      {kind === 'imports' && <><label className="text-sm">ลูกค้า<input name="customer" className="input w-full mt-1" defaultValue={filters.customer} /></label><label className="text-sm">ชื่อหรือรหัสรอบงาน<input name="batch" className="input w-full mt-1" defaultValue={filters.batch} /></label><label className="text-sm">สถานะปัจจุบัน<select name="status" className="input w-full mt-1" defaultValue={filters.status}><option value="">ทุกสถานะ</option>{['uploaded', 'profiled', 'proposed', 'review', 'published', 'transformed', 'blocked', 'failed', 'cancelled', 'needs_customer', 'superseded'].map(s => <option value={s} key={s}>{inspectionStatuses[s]}</option>)}</select></label></>}
      <label className="text-sm">วันที่รับงานตั้งแต่ (UTC)<input type="date" name="start" defaultValue={filters.start} className="input w-full mt-1" /></label><label className="text-sm">ถึงวันที่ (UTC)<input type="date" name="end" defaultValue={filters.end} className="input w-full mt-1" /></label>
      <div className="flex gap-2 items-end"><button className="btn btn-primary" type="submit"><Search size={15} />ค้นหา</button><button className="btn" type="button" onClick={() => setParams(new URLSearchParams({ kind }))}>ล้างตัวกรอง</button></div>
    </form>
    <div className="inspection-layout">
      <section className="card p-4 min-w-0" aria-label="รายการงาน"><h2 className="font-semibold mb-3">รายการงาน {list.data && <span className="text-muted">({list.data.total})</span>}</h2>
        {list.isPending ? <Spinner /> : list.isError ? <p role="alert">โหลดรายการไม่ได้ ตรวจช่วงวันที่แล้วลองอีกครั้ง</p> : <><div className="space-y-2">{list.data.items.map(item => <button key={item.scope + item.id} aria-pressed={scope === item.scope && id === item.id} className={`inspection-work ${scope === item.scope && id === item.id ? 'is-selected' : ''}`} onClick={() => { change({ scope: item.scope, id: item.id, cursor: '', event: '', category: '' }); reveal(timelinePanel.current) }}><strong className="block break-words">{item.title}</strong>{item.files.map((file, i) => <span className="block break-words text-xs mt-1" key={i}>{file}</span>)}<span className="block text-xs text-muted mt-2">{item.customer || (item.scope === 'chat' ? 'แชตส่วนตัว' : 'ยังไม่ระบุลูกค้า')} · {time(item.at)}</span><span className="block text-xs mt-2">{inspectionStatuses[item.status] || item.status}</span></button>)}{!list.data.items.length && <p className="text-sm text-muted">ไม่พบงานตามตัวกรองนี้</p>}</div><div className="flex gap-2 mt-4"><button className="btn btn-sm" disabled={!offset} onClick={() => change({ offset: String(Math.max(0, offset - 25)) })}>ก่อนหน้า</button><button className="btn btn-sm" disabled={list.data.next_offset === null} onClick={() => change({ offset: String(list.data.next_offset) })}>ถัดไป</button></div></>}
      </section>
      <section className="card p-4 min-w-0" aria-label="ลำดับเหตุการณ์" ref={timelinePanel}>
        {!id ? <div className="py-12 text-center text-muted"><History className="mx-auto mb-3" />เลือกงานจากรายการเพื่อดูประวัติ</div> : timeline.isPending ? <Spinner /> : timeline.isError ? <p role="alert">เปิดประวัติงานนี้ไม่ได้ อาจอยู่นอกสิทธิ์ของคุณ</p> : timeline.data && <>
          <h2 className="font-semibold break-words">{timeline.data.title}</h2><p className="text-sm mt-2">สถานะปัจจุบัน: {inspectionStatuses[timeline.data.status] || timeline.data.status}</p><p className="text-xs text-muted my-3">{timeline.data.notice}</p>
          {!timeline.data.journal_available && <p className="text-warn text-sm mb-3" role="alert">โหลดกิจกรรม agent ไม่สำเร็จ ประวัติที่เห็นยังไม่ครบ กรุณาลองอัปเดตข้อมูล</p>}
          <label className="text-sm">ประเภทเหตุการณ์<select className="input w-full my-2" value={category} onChange={e => change({ category: e.target.value, cursor: '', event: '' })}><option value="">ทั้งหมด</option>{Object.entries(inspectionKinds).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
          <ol className="space-y-2 mt-2">{timeline.data.events.map(e => <li key={e.key}><button className={`inspection-event ${event === e.key ? 'is-selected' : ''}`} onClick={() => { change({ event: e.key }); reveal(detailPanel.current) }}><span className="block text-xs text-muted">{time(e.at)} · {e.actor || 'ระบบ'}{e.attempt !== undefined && ` · ครั้งที่ ${e.attempt}`}</span><strong className="block mt-1 break-words">{e.kind === 'activity' ? activityLabel({ action: e.title }) : e.title}</strong><span className="block text-xs mt-1">{inspectionKinds[e.kind]}{e.state ? ' · ' + (inspectionStatuses[e.state] || e.state) : ''}</span></button></li>)}</ol>
          {!timeline.data.events.length && <p className="text-sm text-muted my-5">ไม่มีบันทึกประเภทนี้ ไม่ได้หมายความว่าผ่านการตรวจ</p>}
          <div className="flex gap-2 mt-4">{cursor && <button className="btn btn-sm" onClick={() => change({ cursor: '', event: '' })}>กลับเหตุการณ์ล่าสุด</button>}<button className="btn btn-sm" disabled={!timeline.data.next_cursor} onClick={() => change({ cursor: timeline.data!.next_cursor || '', event: '' })}>เหตุการณ์เก่ากว่านี้<ArrowRight size={14} /></button></div>
          {scope === 'round' && <Link className="btn btn-sm mt-4" to={`/rounds/${id}`}>เปิดไฟล์และผลลัพธ์<ArrowRight size={14} /></Link>}
        </>}
      </section>
      <aside className="card p-4 min-w-0" aria-label="รายละเอียดเหตุการณ์" ref={detailPanel}>{event && id ? <><button className="btn btn-ghost btn-sm mb-3" onClick={() => { change({ event: '' }); reveal(timelinePanel.current) }}><ArrowLeft size={14} />กลับลำดับเหตุการณ์</button>{selectedEvent && <p className="text-xs text-muted mb-3">{time(selectedEvent.at)}</p>}<Receipt key={`${scope}:${id}:${event}`} scope={scope} id={id} event={event} /></> : <p className="text-sm text-muted py-8">เลือกเหตุการณ์เพื่อดูข้อมูลที่ใช้ คำตอบ ผลตรวจ และหลักฐาน</p>}</aside>
    </div>
  </>
}
