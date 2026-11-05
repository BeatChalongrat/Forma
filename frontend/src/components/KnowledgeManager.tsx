import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { usePreferences } from '../lib/preferences'
import { fmtDate } from '../lib/utils'
import { RuleComparison, EventList } from './Inspection'
import { Modal, Spinner } from './ui'
import { KnowledgeRevision, KnowledgeImpacts, KnowledgeChanges } from './KnowledgeRevision'

export function KnowledgeManager() {
  const { t } = usePreferences(), qc = useQueryClient()
  const [params, setParams] = useSearchParams()
  const [customer, setCustomer] = useState(''), [search, setSearch] = useState(''), [draft, setDraft] = useState(''), [status, setStatus] = useState('confirmed'), [offset, setOffset] = useState(0)
  const [retiring, setRetiring] = useState('')
  const customers = useQuery({ queryKey: ['customers'], queryFn: api.customers })
  const me = useQuery({ queryKey: ['me'], queryFn: api.me })
  const q = useQuery({ queryKey: ['learning', customer, search, status, offset], queryFn: () => api.learning(customer, offset, search, status) })
  const forget = useMutation({ mutationFn: api.forgetLearned, onSuccess: () => { setRetiring(''); void qc.invalidateQueries({ queryKey: ['learning'] }); void qc.invalidateQueries({ queryKey: ['knowledge'] }); void qc.invalidateQueries({ queryKey: ['knowledge-impacts'] }) } })
  const promote = useMutation({ mutationFn: api.promoteAlias, onSuccess: () => void qc.invalidateQueries({ queryKey: ['learning'] }) })
  const id = params.get('knowledge') || ''
  const select = (value: string) => setParams(p => { const next = new URLSearchParams(p); if (value) next.set('knowledge', value); else next.delete('knowledge'); return next })
  return <div className="space-y-5">
    <p className="text-sm text-muted">{t('Approved mapping knowledge is scoped to customers, formats and target contracts. Personal assistant memory is managed separately.', 'ความรู้การจับคู่ที่อนุมัติแล้วมีขอบเขตลูกค้า รูปแบบไฟล์ และเงื่อนไขปลายทาง แยกจากความจำส่วนตัวของผู้ช่วย')} <Link className="underline" to="/memory">{t('Personal memory', 'จัดการความจำส่วนตัว')}</Link></p>
    {q.data && <div className="grid sm:grid-cols-4 gap-3">{[[t('Column aliases', 'ชื่อคอลัมน์'), q.data.stats.aliases + q.data.stats.promoted], [t('Constants', 'ค่าคงที่'), q.data.stats.constants], [t('Confirmed decisions', 'คำตอบที่ยืนยันแล้ว'), q.data.stats.decisions], [t('Recorded uses', 'จำนวนครั้งที่ใช้'), q.data.stats.uses]].map(([label, count]) => <div key={label} className="card p-4"><span className="text-sm text-muted">{label}</span><p className="text-2xl font-semibold mt-2">{count}</p></div>)}</div>}
    <form className="card p-4 flex flex-wrap gap-3 items-end" onSubmit={e => { e.preventDefault(); setSearch(draft); setOffset(0) }}>
      <label className="grid gap-1 text-sm flex-1 min-w-44">{t('Search field, header or teacher', 'ค้นหาฟิลด์ คอลัมน์ หรือผู้สอน')}<input className="input" value={draft} maxLength={200} onChange={e => setDraft(e.target.value)} /></label>
      <label className="grid gap-1 text-sm">{t('Customer', 'ลูกค้า')}<select className="input" value={customer} onChange={e => { setCustomer(e.target.value); setOffset(0) }}><option value="">{t('All accessible', 'ทั้งหมดที่เข้าถึงได้')}</option>{customers.data?.map(c => <option key={c.id} value={c.id}>{c.code}</option>)}</select></label>
      <label className="grid gap-1 text-sm">{t('Status', 'สถานะ')}<select className="input" value={status} onChange={e => { setStatus(e.target.value); setOffset(0) }}><option value="confirmed">{t('Active', 'ใช้งานอยู่')}</option><option value="retired">{t('Retired', 'เลิกใช้แล้ว')}</option><option value="all">{t('All', 'ทั้งหมด')}</option></select></label><button className="btn" type="submit">{t('Search', 'ค้นหา')}</button>
    </form>
    {q.isPending ? <Spinner /> : q.isError ? <p role="alert">{q.error.message}</p> : <div className="card overflow-hidden"><div className="overflow-auto"><table className="table"><thead><tr><th>{t('Knowledge', 'ความรู้')}</th><th>{t('Scope', 'ขอบเขต')}</th><th>{t('Teacher / date', 'ผู้สอน / วันที่')}</th><th>{t('Status / uses', 'สถานะ / การใช้')}</th><th /></tr></thead><tbody>{q.data.learned.items.map(x => <tr key={x.id}><td><button className="underline text-left break-all" onClick={() => select(x.id)}>{x.target}</button><p className="text-xs text-muted break-words max-w-md">{x.kind} · {JSON.stringify(x.decision?.answer ?? x.column ?? x.header ?? x.value)}</p></td><td>{customers.data?.find(c => c.id === x.customer_id)?.code || (x.customer_id ? x.customer_id : t('All customers', 'ทุกลูกค้า'))}<small className="block text-muted">{x.decision && (x.decision.action === 'select_rows' || (x.decision.action === 'constant' && !x.decision.remember) ? t('This delivery only', 'เฉพาะรอบนี้') : t('Matching format and requirements', 'รูปแบบและเงื่อนไขที่ตรงกัน'))}</small></td><td className="text-sm">{x.actor}<small className="block text-muted">{fmtDate(x.created_at || '')}</small></td><td>{x.status} · {x.uses}</td><td>{x.status === 'confirmed' && me.data?.effective_roles.includes('approver') && <button className="btn btn-sm" onClick={() => { forget.reset(); setRetiring(x.id) }}>{t('Retire', 'เลิกใช้')}</button>}</td></tr>)}</tbody></table></div><div className="p-4 flex gap-3 items-center"><span className="text-xs text-muted">{q.data.learned.total} {t('items', 'รายการ')} · {offset + (q.data.learned.items.length ? 1 : 0)}–{offset + q.data.learned.items.length}</span><button className="btn btn-sm" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 50))}>{t('Previous', 'ก่อนหน้า')}</button><button className="btn btn-sm" disabled={offset + 50 >= q.data.learned.total} onClick={() => setOffset(offset + 50)}>{t('Next', 'ถัดไป')}</button></div></div>}
    <Modal open={!!id} onClose={() => select('')} title={t('Knowledge details', 'รายละเอียดความรู้')} width="max-w-4xl"><div className="p-5">{id && <KnowledgeDetails key={id} id={id} onSelect={select} />}</div></Modal>
    {!!q.data?.stats.series.length && <section className="card p-4"><h2 className="font-semibold">{t('Corrections by month', 'การแก้และการอนุมัติรายเดือน')}</h2><div className="overflow-auto mt-3"><table className="table"><thead><tr><th>{t('Month', 'เดือน')}</th><th>{t('Approved', 'อนุมัติ')}</th><th>{t('Accepted as proposed', 'รับตามที่เสนอ')}</th><th>{t('Corrections', 'การแก้')}</th><th>{t('Per approval', 'ต่อการอนุมัติ')}</th></tr></thead><tbody>{q.data.stats.series.map(x => <tr key={x.month}><td>{x.month}</td><td>{x.mappings_approved}</td><td>{x.accepted_as_proposed}</td><td>{x.corrections}</td><td>{x.corrections_per_approval ?? '—'}</td></tr>)}</tbody></table></div></section>}

    {!!q.data?.suggestions.length && me.data?.effective_roles.includes('admin') && <section className="card p-5 space-y-3"><h2 className="font-semibold">{t('Shared vocabulary suggestions', 'ชื่อคอลัมน์ที่เสนอให้ใช้ร่วมกัน')}</h2>{q.data.suggestions.map(x => <div key={x.header + x.target} className="flex flex-wrap gap-3 items-center"><span>{x.header} → {x.target} · {x.customers} {t('customers', 'ลูกค้า')}</span><button className="btn btn-sm" disabled={promote.isPending} onClick={() => promote.mutate({ header: x.header, target: x.target })}>{t('Use for everyone', 'ใช้กับทุกลูกค้า')}</button></div>)}{promote.isError && <p role="alert">{promote.error.message}</p>}</section>}
    <Modal open={!!retiring} onClose={() => setRetiring('')} title={t('Retire this knowledge?', 'เลิกใช้ความรู้นี้?')}><div className="p-5 space-y-4"><p>{t('Future runs will not reuse this rule. Existing results stay in history. Open its details to edit the answer or select affected files for review.', 'รอบถัดไปจะไม่ใช้กฎนี้ซ้ำ ผลลัพธ์เดิมยังอยู่ในประวัติ ถ้าต้องการเปลี่ยนคำตอบให้ใช้ปุ่มแก้คำตอบก่อนเลิกใช้ หรือเปิดรายละเอียดเพื่อเลือกไฟล์ที่ต้องส่งกลับมาตรวจ')}</p>{forget.isError && <p role="alert">{forget.error.message}</p>}<button className="btn btn-primary" disabled={forget.isPending} onClick={() => forget.mutate(retiring)}>{t('Retire', 'ยืนยันเลิกใช้')}</button></div></Modal>
  </div>
}

function KnowledgeDetails({ id, onSelect }: { id: string; onSelect: (id: string) => void }) {
  const { t } = usePreferences(), [offset, setOffset] = useState(0)
  const me = useQuery({ queryKey: ['me'], queryFn: api.me })
  const q = useQuery({ queryKey: ['knowledge', id, offset], queryFn: () => api.knowledge(id, offset) })
  if (q.isPending) return <Spinner />
  if (q.isError) return <p role="alert">{q.error.message}</p>
  const d = q.data
  const fact = d.after && typeof d.after === 'object' ? d.after as Record<string, unknown> : null
  const display = (value: unknown) => typeof value === 'string' ? value : JSON.stringify(value) ?? '—'
  return <div className="divide-y divide-line">
    <section className="pb-6 space-y-4">
      <div className="flex flex-wrap justify-between items-start gap-3">
        <div><p className="text-xs text-muted mb-1">{t('Target field', 'ฟิลด์ปลายทาง')}</p><h2 className="text-lg font-semibold break-all">{d.target}</h2></div>
        <span className="text-sm text-muted">{d.status === 'confirmed' ? t('Active', 'ใช้งานอยู่') : d.status === 'retired' ? t('Retired', 'เลิกใช้แล้ว') : d.status}</span>
      </div>
      {fact?.question !== undefined && <>
        <p className="text-sm leading-relaxed break-words">{String(fact.question)}</p>
        <dl className="grid sm:grid-cols-2 gap-4 bg-surface-2 rounded-lg p-4">
          {fact.source_value !== undefined && <div><dt className="text-xs text-muted mb-2">{t('Source value', 'ค่าที่พบในไฟล์')}</dt><dd className="text-xl font-medium break-words">{display(fact.source_value)}</dd></div>}
          <div><dt className="text-xs text-muted mb-2">{t('Confirmed answer', 'คำตอบที่ยืนยันแล้ว')}</dt><dd className="text-xl font-semibold break-words">{display(fact.answer)}</dd></div>
        </dl>
      </>}
      <p className="text-xs text-muted">{t('Confirmed by', 'ผู้ยืนยัน')}: {d.actor} · {fmtDate(d.created_at || '')}</p>
    </section>
    {d.kind === 'decision' && me.data?.effective_roles.includes('approver') && <KnowledgeRevision id={id} active={d.status === 'confirmed'} />}
    <section className="py-6 space-y-4">
      <h3 className="font-semibold">{t('When this knowledge applies', 'ใช้ความรู้นี้เมื่อไร')}</h3>
      <p className="text-sm leading-relaxed">{d.status === 'confirmed' && d.reusable ? t('Reusable only when the system checks that the scope and requirements match.', 'นำไปใช้ซ้ำได้เมื่อระบบตรวจว่าขอบเขตและเงื่อนไขตรงกันแล้ว') : t('Evidence for this delivery only; not reusable.', 'เป็นหลักฐานเฉพาะรอบนี้ ไม่นำไปใช้ซ้ำ')}</p>
      <dl className="grid sm:grid-cols-2 gap-4 text-sm">
        <div className="min-w-0"><dt className="text-xs text-muted mb-1">{t('Target contract', 'รูปแบบปลายทาง')}</dt><dd className="break-all">{d.target_schema || '—'}</dd></div>
        <div className="min-w-0"><dt className="text-xs text-muted mb-1">{t('File layout reference', 'รหัสรูปแบบไฟล์')}</dt><dd className="break-all font-mono text-xs">{d.layout_hash || t('Not recorded', 'ไม่มีบันทึก')}</dd></div>
        <div className="sm:col-span-2"><dt className="text-xs text-muted mb-2">{t('Requirements', 'เงื่อนไขที่บันทึกไว้')}</dt><dd className="whitespace-pre-wrap break-words leading-relaxed">{d.requirements || t('No requirements recorded.', 'ไม่มีเงื่อนไขที่บันทึกไว้')}</dd></div>
      </dl>
      {!!Object.keys(d.interpretation_context?.columns || {}).length && <div className="overflow-auto"><h4 className="text-sm font-medium mb-2">{t('Observed interpretation conditions', 'เงื่อนไขความหมายที่พบในไฟล์')}</h4><table className="table text-sm"><thead><tr><th>{t('Column', 'คอลัมน์')}</th><th>{t('Condition', 'เงื่อนไข')}</th><th>{t('Observed', 'ค่าที่พบ')}</th></tr></thead><tbody>{Object.entries(d.interpretation_context?.columns || {}).flatMap(([column, conditions]) => Object.entries(conditions).map(([kind, values]) => <tr key={column + kind}><td>{column.replace(/^\d+:/, '')}</td><td>{({ country: t('Country', 'ประเทศ'), locale: t('Locale', 'ภาษาและภูมิภาค'), currency: t('Currency', 'สกุลเงิน'), unit: t('Unit codes', 'รหัสหน่วย'), date: t('Date convention', 'รูปแบบวันที่'), number: t('Number convention', 'รูปแบบตัวเลข') } as Record<string, string>)[kind] || kind}</td><td>{values.map(value => ({ day_first: t('Day first', 'วันก่อนเดือน'), month_first: t('Month first', 'เดือนก่อนวัน'), ambiguous_date: t('Day/month still ambiguous', 'วันกับเดือนยังคลุมเครือ'), thai_date: t('Possible Thai date', 'พบรูปแบบวันที่ไทย'), decimal_comma: t('Comma decimal', 'จุลภาคเป็นทศนิยม'), decimal_dot: t('Dot decimal', 'จุดเป็นทศนิยม') } as Record<string, string>)[value] || value).join(' · ')}</td></tr>))}</tbody></table><p className="text-xs text-muted mt-2">{t('Observations from file profiling; these are not proof of every row or of business meaning.', 'ข้อมูลจากการสำรวจไฟล์ ยังไม่ใช่ข้อยืนยันของทุกแถวหรือความหมายทางธุรกิจ')}</p></div>}
    </section>
    <section className="py-6 space-y-4">
      <h3 className="font-semibold">{t('Rule changes', 'กฎที่เปลี่ยนหลังยืนยันคำตอบ')}</h3>
      <RuleComparison before={d.before} after={fact?.after ?? d.after} />
      <div className="flex flex-wrap gap-3">{d.mapping_id && <Link className="btn btn-sm" to={`/review?mapping=${d.mapping_id}&section=history`}>{t('Original decision', 'การตัดสินใจต้นทาง')}</Link>}{d.mapping_id && d.round_id && <Link className="btn btn-sm" to={`/rounds/${d.round_id}/mapping/${d.mapping_id}`}>{t('Create a revised mapping', 'เปิดการจับคู่เพื่อสร้างเวอร์ชันแก้ไข')}</Link>}</div>
    </section>
    {d.kind === 'decision' && <KnowledgeImpacts id={id} retired={d.status === 'retired'} canEdit={!!me.data?.effective_roles.includes('approver')} />}
    <section className="py-6 space-y-3">
      <h3 className="font-semibold">{t('Conflicting answers', 'คำตอบที่ขัดกัน')}</h3>
      {!d.conflicts.length ? <p className="text-sm text-muted">{t('No conflicting answers recorded in the same scope.', 'ไม่พบคำตอบที่ขัดกันในขอบเขตเดียวกัน')}</p> : d.conflicts.map(c => <button key={c.id} className="btn btn-sm mr-2" onClick={() => { setOffset(0); onSelect(c.id) }}>{display(c.answer)} →</button>)}
    </section>
    {d.kind === 'decision' && <KnowledgeChanges id={id} />}
    <section className="pt-6 space-y-3">
      <h3 className="font-semibold">{t('Usage history', 'ประวัติการนำไปใช้')}</h3>
      {d.legacy_use_count > 0 && <p className="text-xs text-muted">{t('Earlier usage count (individual events may not be recorded)', 'จำนวนครั้งในอดีต (อาจไม่มีบันทึกรายเหตุการณ์)')}: {d.legacy_use_count}</p>}
      <EventList data={d.usage} onOffset={setOffset} />
    </section>
  </div>
}

export function AuditBrowser() {
  const { t } = usePreferences(), [offset, setOffset] = useState(0)
  const [draft, setDraft] = useState({ actor: '', action: '', entity_id: '', start: '', end: '' }), [filters, setFilters] = useState(draft)
  const q = useQuery({ queryKey: ['audit-events', filters, offset], queryFn: () => api.auditEvents({ ...Object.fromEntries(Object.entries(filters).filter(([, v]) => v)), offset }) })
  return <section className="card p-5 space-y-5"><form className="flex flex-wrap gap-3 items-end" onSubmit={e => { e.preventDefault(); setFilters(draft); setOffset(0) }}>{(['actor', 'action', 'entity_id', 'start', 'end'] as const).map(key => <label key={key} className="grid gap-1 text-sm">{{ actor: t('Actor', 'ผู้ทำ'), action: t('Action', 'การกระทำ'), entity_id: t('Entity ID', 'รหัสข้อมูล'), start: t('From (UTC)', 'ตั้งแต่ (UTC)'), end: t('To (UTC)', 'ถึง (UTC)') }[key]}<input className="input" type={key === 'start' || key === 'end' ? 'date' : 'text'} value={draft[key]} onChange={e => setDraft({ ...draft, [key]: e.target.value })} /></label>)}<button className="btn" type="submit">{t('Filter', 'กรอง')}</button></form>{q.isPending ? <Spinner /> : q.isError ? <p role="alert">{q.error.message}</p> : <EventList data={q.data} onOffset={setOffset} />}</section>
}
