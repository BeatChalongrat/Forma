import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, type EffectCheck, type EventPage } from '../lib/api'
import { usePreferences } from '../lib/preferences'
import { fmtDate } from '../lib/utils'
import { Spinner } from './ui'

export function DetailValue({ value }: { value: unknown }) {
  return <pre className="text-xs whitespace-pre-wrap break-words bg-surface-2 p-3 rounded-lg max-h-96 overflow-auto">{JSON.stringify(value, null, 2)}</pre>
}
export function RuleComparison({ before, after }: { before: unknown; after: unknown }) {
  const { t } = usePreferences()
  return <div className="grid md:grid-cols-2 border border-line rounded-lg overflow-hidden">
    {[[t('Before confirmation', 'ก่อนยืนยัน'), before], [t('After confirmation', 'หลังยืนยัน'), after]].map(([label, value], index) => <div key={index} className={`min-w-0 p-4 ${index ? 'border-t md:border-t-0 md:border-l border-line bg-surface-2' : ''}`}>
      <h4 className="text-sm font-medium mb-3">{String(label)}</h4>
      <pre className="text-xs whitespace-pre-wrap break-words leading-relaxed">{value == null ? t('No rule recorded', 'ไม่มีบันทึกกฎ') : JSON.stringify(value)}</pre>
    </div>)}
  </div>
}
export function EffectResult({ check }: { check?: EffectCheck | null }) {
  const { t } = usePreferences()
  return <div className="border border-line rounded-xl p-4 space-y-2">
    <h3 className="font-medium">{t('Complete-file answer check', 'ผลตรวจคำตอบกับข้อมูลทั้งไฟล์')}</h3>
    {!check ? <p className="text-sm text-muted">{t('No recorded check for this version. This does not mean it passed.', 'เวอร์ชันนี้ไม่มีผลตรวจที่บันทึกไว้ ไม่ได้หมายความว่าตรวจผ่าน')}</p> : <>
      <p className={check.passed ? 'text-ok' : 'text-bad'}>{check.passed ? t('Passed', 'ผ่านการตรวจผลกระทบ') : t('Not passed', 'ยังไม่ผ่าน')} {check.records_checked !== undefined && `· ${check.records_checked} ${t('records checked', 'รายการที่ตรวจ')}`}</p>
      {check.error && <p role="alert" className="text-sm text-bad break-words">{check.error}</p>}
      {check.remaining_errors !== undefined && <p className="text-sm">{t('Remaining errors', 'ข้อผิดพลาดที่ยังเหลือ')}: {check.remaining_errors}</p>}
      {check.checked_at && <p className="text-xs text-muted">{fmtDate(check.checked_at)} · {check.actor}</p>}
      <p className="text-xs text-muted">{t('Checks identities, unrelated fields and new schema errors. Business meaning still needs human confirmation.', 'ตรวจรหัสรายการ ฟิลด์ที่ไม่ควรเปลี่ยน และข้อผิดพลาดใหม่ตาม schema ความหมายทางธุรกิจยังต้องอาศัยผู้ยืนยัน')}</p>
    </>}
  </div>
}

export function MappingInspection({ id }: { id: string }) {
  const { t } = usePreferences()
  const [offset, setOffset] = useState(0)
  const q = useQuery({ queryKey: ['mapping-trace', id, offset], queryFn: () => api.mappingTrace(id, offset) })
  if (q.isPending) return <Spinner />
  if (q.isError) return <p role="alert">{q.error.message}<button className="btn ml-2" onClick={() => void q.refetch()}>{t('Retry', 'ลองใหม่')}</button></p>
  const d = q.data
  const source = typeof d.checks.source === 'string' ? d.checks.source : ''
  const sourceText: Record<string, string> = {
    memory: t('The system reused a previously approved mapping. Check the saved field rules and the checks for this delivery below.', 'ระบบใช้กฎจับคู่ที่เคยอนุมัติไว้ ดูกฎรายช่องและผลตรวจของไฟล์ครั้งนี้ได้ด้านล่าง'),
    heuristic: t('The system proposed matches using its built-in rules. The field view records the source columns and supporting evidence.', 'ระบบใช้กฎที่มีอยู่เสนอการจับคู่ เปิดดูได้ว่าแต่ละช่องใช้คอลัมน์ไหนและมีหลักฐานอะไร'),
    llm: t('AI proposed the mapping. The field view records its source columns and supporting evidence; the checks below record whether the proposal passed.', 'AI เสนอการจับคู่ เปิดดูคอลัมน์ต้นทางและหลักฐานรายช่องได้ ส่วนด้านล่างแสดงว่าข้อเสนอนี้ผ่านการตรวจอะไรแล้วบ้าง'),
  }
  return <div className="space-y-5">
    <header><h3 className="text-lg font-semibold">{t('Mapping version', 'การจับคู่เวอร์ชัน')} {d.version}</h3><p className="text-sm text-muted">{d.target_schema} · {d.status} · {fmtDate(d.created_at)}</p>{d.parent_id && <Link className="text-sm underline mt-2" to={`/review?mapping=${d.parent_id}&section=history`}>{t('Open parent version', 'เปิดเวอร์ชันก่อนหน้า')}</Link>}{d.approved_by && <p className="text-sm">{t('Approved by', 'อนุมัติโดย')} {d.approved_by} · {d.approved_at && fmtDate(d.approved_at)}</p>}{d.comment && <p className="text-sm break-words mt-2">{d.comment}</p>}</header>
    <section className="space-y-3 border-b border-line pb-5"><h3 className="font-medium">{t('How were these fields matched?', 'ระบบจับคู่ข้อมูลนี้อย่างไร')}</h3><p className="text-sm text-ink-2 leading-relaxed">{sourceText[source] || t('Open the field view to see the source column, transformation rule and recorded evidence for each match.', 'เปิดดูรายช่องเพื่อดูว่าใช้คอลัมน์ไหน แปลงค่าอย่างไร และบันทึกหลักฐานอะไรไว้')}</p>{d.round_id && <Link className="btn btn-sm" to={`/rounds/${d.round_id}/mapping/${d.mapping_id}`}>{t('Source columns and evidence', 'ดูคอลัมน์ต้นทางและเหตุผลรายช่อง')}</Link>}</section>
    <EffectResult check={d.effect_check} />
    <section className="space-y-3"><h3 className="font-medium">{t('Answers recorded in this version', 'คำตอบที่บันทึกในเวอร์ชันนี้')}</h3>{!d.decisions.length && <p className="text-sm text-muted">{t('No new structured answers recorded.', 'ไม่มีคำตอบแบบมีโครงสร้างเพิ่มในเวอร์ชันนี้')}</p>}{d.decisions.map((x, i) => <div key={i} className="border border-line rounded-lg p-4 space-y-2"><p className="font-medium">{x.question || x.target}</p><p className="text-sm break-words">{JSON.stringify(x.answer)} · {x.actor} {x.answered_at && `· ${fmtDate(x.answered_at)}`}</p><p className="text-xs text-muted break-all">{x.target} · {x.scope} {x.remember ? '· remember' : ''}</p><RuleComparison before={x.before} after={x.after} /></div>)}{d.acknowledged.map((x, i) => <p key={i} className="text-sm">{t('Reviewed note', 'ยืนยันข้อสงสัย')}: {x.question} · {x.actor}</p>)}</section>
    <section className="space-y-2"><h3 className="font-medium">{t('Knowledge used', 'ความรู้ที่นำมาใช้')}</h3>{d.knowledge.length ? d.knowledge.map(x => <Link key={x.id} className="block underline text-sm" to={`/settings?tab=learning&knowledge=${encodeURIComponent(x.id)}`}>{x.target || x.id} · {x.method}</Link>) : <p className="text-sm text-muted">{t('No knowledge-use links recorded. Older runs may only have a memory entry in the log.', 'ไม่มีลิงก์การใช้ความรู้ที่บันทึกไว้ รอบเก่าอาจมีเพียงรายการ memory ใน log')}</p>}</section>
    <section className="space-y-2"><h3 className="font-medium">{t('Recorded execution checks', 'ผลตรวจการประมวลผลที่บันทึกไว้')}</h3>{d.checks.verification && typeof d.checks.verification === 'object' ? <p className="text-sm">{(d.checks.verification as { passed?: boolean }).passed ? t('Execution / contract verification passed', 'ผ่านการตรวจการประมวลผลและเงื่อนไข schema') : t('Execution / contract verification not passed', 'การตรวจการประมวลผลและเงื่อนไข schema ยังไม่ผ่าน')}</p> : <p className="text-sm text-muted">{t('No verification snapshot', 'ไม่มีบันทึกผลตรวจนี้')}</p>}<div className="space-y-2"><h4 className="text-sm text-muted">{t('Check details and original policy result', 'รายละเอียดผลตรวจและกฎที่ประเมินตอนสร้างเวอร์ชัน')}</h4><DetailValue value={d.checks} /></div></section>
    <section className="space-y-2"><h3 className="font-medium">{t('AI execution log', 'รายละเอียดการทำงานของ AI')} · {d.logs.total}</h3><p className="text-xs text-muted">{t('Recorded tool activity and results, not a reconstruction of the model’s internal thoughts. Cost values are estimates.', 'แสดงกิจกรรมและผลที่บันทึกจริง ไม่ได้สร้างความคิดภายในของโมเดลขึ้นมาใหม่ ค่าใช้จ่ายเป็นค่าประเมิน')}</p>{d.logs.items.map((x, i) => <div key={i} className="border-b border-line py-2"><div className="flex justify-between gap-3 mb-2"><span>{offset + i + 1}. {String(x.agent || x.tool || 'run')}</span><span className="text-xs text-muted">{String(x.result || x.model || '')}</span></div><DetailValue value={x} /></div>)}<div className="flex gap-2"><button className="btn btn-sm" disabled={!offset} onClick={() => { setOffset(Math.max(0, offset - 20)) }}>{t('Previous', 'ก่อนหน้า')}</button><button className="btn btn-sm" disabled={d.logs.next_offset === null} onClick={() => { setOffset(d.logs.next_offset!) }}>{t('Next', 'ถัดไป')}</button></div></section>
  </div>
}

export function EventList({ data, onOffset, onMapping }: { data: EventPage; onOffset: (n: number) => void; onMapping?: (id: string) => void }) {
  const { t } = usePreferences()
  if (!data.items.length && data.total === 0) return <p className="text-sm text-muted">{t('No recorded events yet.', 'ยังไม่มีเหตุการณ์ที่บันทึกไว้')}</p>
  return <div className="space-y-3"><p className="text-xs text-muted">{data.total} {t('events', 'เหตุการณ์')} · {data.offset + (data.items.length ? 1 : 0)}–{data.offset + data.items.length}</p>{data.items.map(e => <article className="border-b border-line pb-3 space-y-2" key={e.id}><div className="flex justify-between gap-3 flex-wrap"><span className="font-medium text-sm">{({ 'review.answers': t('Answers saved and checked', 'บันทึกคำตอบและตรวจผลแล้ว'), 'mapping.propose': t('Mapping proposed', 'เสนอการจับคู่ข้อมูล'), 'mapping.approve': t('Mapping approved', 'อนุมัติการจับคู่'), 'mapping.edit': t('New mapping version', 'สร้างเวอร์ชันการจับคู่ใหม่'), 'mapping.reject': t('Mapping rejected', 'ไม่อนุมัติการจับคู่'), 'dataset.transform': t('Output generated', 'สร้างข้อมูลผลลัพธ์'), 'dataset.edit': t('Output edited', 'แก้ข้อมูลผลลัพธ์'), 'dataset.export': t('File exported', 'ส่งออกไฟล์'), 'dataset.rollback': t('Restored earlier output', 'สร้างเวอร์ชันจากข้อมูลก่อนหน้า'), 'learning.use': t('Knowledge retrieved', 'นำความรู้มาใช้') } as Record<string, string>)[e.action] || e.action}</span><span className="text-xs text-muted">{fmtDate(e.at)} · {e.actor}</span></div><div className="flex gap-3 flex-wrap text-xs"><span className="text-muted break-all">{e.entity_id}</span>{e.entity_id.startsWith('map_') && (onMapping ? <button className="underline" onClick={() => onMapping(e.entity_id)}>{t('Inspect this version', 'ดูเวอร์ชันนี้')}</button> : <Link className="underline" to={`/review?mapping=${e.entity_id}&section=history`}>{t('Inspect this version', 'ดูเวอร์ชันนี้')}</Link>)}{e.entity_id.startsWith('dsv_') && <Link className="underline" to={`/datasets/${e.entity_id}?tab=versions`}>{t('Open dataset', 'เปิดชุดข้อมูล')}</Link>}{typeof e.details.mapping_id === 'string' && <Link className="underline" to={`/review?mapping=${encodeURIComponent(e.details.mapping_id)}&section=history`}>{t('Mapping used', 'เปิดการจับคู่ที่ใช้')}</Link>}{typeof e.details.round_id === 'string' && <Link className="underline" to={`/rounds/${encodeURIComponent(e.details.round_id)}`}>{t('Open round', 'เปิดรอบงาน')}</Link>}{typeof e.details.new_round === 'string' && <Link className="underline" to={`/rounds/${encodeURIComponent(e.details.new_round)}`}>{t('Open updated files', 'ดูไฟล์หลังแก้ไข')}</Link>}{typeof e.details.detached_round_id === 'string' && <Link className="underline" to={`/rounds/${encodeURIComponent(e.details.detached_round_id)}`}>{t('Open moved or cancelled file', 'ดูไฟล์ที่ย้ายหรือยกเลิก')}</Link>}</div><DetailValue value={e.details} /></article>)}<div className="flex gap-2"><button className="btn btn-sm" disabled={!data.offset} onClick={() => onOffset(Math.max(0, data.offset - data.limit))}>{t('Previous', 'ก่อนหน้า')}</button><button className="btn btn-sm" disabled={data.next_offset === null} onClick={() => onOffset(data.next_offset!)}>{t('Next', 'ถัดไป')}</button></div></div>
}

export function RoundHistory({ roundId, mappingId }: { roundId: string; mappingId?: string }) {
  const { t } = usePreferences()
  const [offset, setOffset] = useState(0), [selected, setSelected] = useState(mappingId || '')
  const [action, setAction] = useState('')
  const q = useQuery({ queryKey: ['round-history', roundId, offset, action], queryFn: () => api.roundHistory(roundId, offset, action) })
  return <div className="space-y-5"><Link className="btn btn-sm" to={`/inspection?scope=round&id=${encodeURIComponent(roundId)}`}>ดูประวัติงานทั้งหมดในที่เดียว</Link><div className="flex flex-wrap justify-between gap-3"><h2 className="text-xl font-semibold">{t('Decision history', 'ประวัติการตัดสินใจ')}</h2><select className="input w-auto" aria-label={t('Event filter', 'กรองเหตุการณ์')} value={action} onChange={e => { setAction(e.target.value); setOffset(0) }}><option value="">{t('All steps', 'ทุกขั้นตอน')}</option><option value="review.">{t('Answers', 'คำตอบ')}</option><option value="mapping.">{t('Mapping', 'การจับคู่')}</option><option value="learning.">{t('Knowledge', 'การใช้ความรู้')}</option><option value="dataset.">{t('Dataset', 'ชุดข้อมูล')}</option></select></div>{q.isPending ? <Spinner /> : q.isError ? <p role="alert">{q.error.message}</p> : <EventList data={q.data} onOffset={setOffset} onMapping={setSelected} />}{selected && <div className="border-t border-line pt-5"><MappingInspection key={selected} id={selected} /></div>}</div>
}
