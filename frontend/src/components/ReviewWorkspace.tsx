import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowLeft, ArrowRight, Check, CircleHelp, FileText, TriangleAlert } from 'lucide-react'
import { api, type AnswerPreview, type Expr, type FieldSpec, type ReviewQuestions } from '../lib/api'
import { AssistantIcon } from '../lib/icons'
import { usePreferences } from '../lib/preferences'
import { Badge, Spinner } from './ui'
import { VerdictCard } from './VerdictCard'

type T = (en: string, th: string) => string
type Question = ReviewQuestions['questions'][number]
const text = (v: unknown): string => v === null || v === undefined ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v)
const semanticLabels: Record<string, string> = {
  sku: 'รหัสสินค้า', product_name: 'ชื่อสินค้า', description: 'รายละเอียดสินค้า', barcode: 'บาร์โค้ด', brand: 'ยี่ห้อ', category_path: 'หมวดสินค้า', currency: 'สกุลเงิน', status: 'สถานะสินค้า',
  'unit.sell': 'หน่วยขาย', 'unit.pack_size': 'จำนวนชิ้นต่อแพ็ก', 'price.amount': 'ราคาขาย', 'price.list_name': 'รายการราคา', 'price.vat_included': 'การรวม VAT', 'price.vat_rate': 'อัตรา VAT', 'price.effective_from': 'วันที่เริ่มใช้ราคา', 'price.effective_to': 'วันที่สิ้นสุดราคา',
  'stock.qty': 'จำนวนสต็อก', 'stock.warehouse': 'คลังสินค้า', 'stock.reserved': 'จำนวนที่จองไว้', 'promo.code': 'รหัสโปรโมชัน', 'promo.name': 'ชื่อโปรโมชัน', 'promo.type': 'ประเภทโปรโมชัน', 'promo.start_date': 'วันเริ่มโปรโมชัน', 'promo.end_date': 'วันสิ้นสุดโปรโมชัน', 'promo.price': 'ราคาโปรโมชัน', 'discount.percent': 'ส่วนลดเปอร์เซ็นต์', 'discount.amount': 'ส่วนลดจำนวนเงิน',
}
const fieldName = (f: FieldSpec, th: boolean) => (th ? f.description_th || semanticLabels[f.semantic || ''] : f.description) || f.description || f.name
const editor = (q: ReviewQuestions, field?: string, tab = '') => `/rounds/${q.round_id}/mapping/${q.mapping_id}?${tab ? `tab=${tab}&` : ''}${field ? `field=${encodeURIComponent(field)}` : ''}`
function title(item: Question, t: T, th: boolean) {
  if (item.kind === 'choice') return t(`Which column holds ${fieldName(item.field, false)}?`, `คอลัมน์ไหนคือ${fieldName(item.field, true)}?`)
  if (item.field.semantic === 'price.vat_included') return t('Does this price include VAT?', 'ราคาในไฟล์รวม VAT แล้วหรือยัง?')
  if (item.field.semantic === 'unit.pack_size') return t('How many units are in one pack?', 'หนึ่งแพ็กมีสินค้ากี่ชิ้น?')
  return fieldName(item.field, th)
}
function valueLabel(value: unknown, semantic: string | null | undefined, t: T): string {
  if (typeof value !== 'boolean') return text(value)
  if (semantic === 'price.vat_included') return value ? t('VAT included', 'รวม VAT แล้ว') : t('VAT excluded', 'ยังไม่รวม VAT')
  return value ? t('Yes', 'ใช่') : t('No', 'ไม่ใช่')
}
function rule(expr: Expr, t: T): string {
  if (expr.op === 'const') return t(`Use a fixed value: ${text(expr.value)}`, `ใช้ค่าที่กำหนดไว้: ${text(expr.value)}`)
  if (expr.op === 'col') return t(`Read “${expr.name}”`, `อ่านคอลัมน์ “${expr.name}”`)
  const names: Record<string, string> = { pack_size: t('Read units per pack', 'แปลงข้อความเป็นจำนวนชิ้นต่อแพ็ก'), parse_number_th: t('Read as a number', 'แปลงข้อความเป็นตัวเลข'), parse_bool: t('Read yes / no', 'แปลงเป็นใช่ / ไม่ใช่'), default: t('Use a default if empty', 'ใช้ค่าสำรองเมื่อช่องว่าง'), coalesce: t('Use the first available value', 'ใช้ค่าแรกที่มีข้อมูล'), unit_normalize: t('Standardize the unit', 'ปรับชื่อหน่วยให้เป็นมาตรฐาน'), trim: t('Remove outer spaces', 'ตัดช่องว่างหน้าและหลัง'), cast: t('Convert the value type', 'แปลงชนิดข้อมูล') }
  return `${names[expr.op] || t('Transform source values', 'แปลงค่าจากต้นทาง')}${expr.args?.length ? ` ← ${expr.args.map(a => rule(a, t)).join(' · ')}` : ''}`
}

export function ReviewWorkspace({ id, onSaved, onDone }: { id: string; onSaved: (id: string) => void; onDone: () => void }) {
  const nav = useNavigate()
  const { prefs, t } = usePreferences(), th = prefs.language === 'th', qc = useQueryClient()
  const query = useQuery({ queryKey: ['questions', id], queryFn: () => api.reviewQuestions(id) })
  const explain = useMutation({
    mutationFn: api.createChat,
    onSuccess: chat => {
      const q = query.data!
      const focused = q.questions.find(x => x.id === selected)
      const question = t('Explain the problems in this file and mapping version. What is known, what is uncertain, and what should I check next?', 'ช่วยอธิบายปัญหาของไฟล์และเวอร์ชันที่เปิดอยู่นี้ อะไรยืนยันได้แล้ว อะไรยังไม่แน่ใจ และต้องตรวจอะไรต่อ?') + (focused ? ' ' + t('Focus on: ', 'เน้นเรื่อง: ') + title(focused, t, th) : '')
      void qc.invalidateQueries({ queryKey: ['assistant-chats'] })
      nav('/assistant?chat=' + chat.id, { state: { send: { question, request_id: crypto.randomUUID() } } })
    },
  })
  const verdictQuery = useQuery({ queryKey: ['verdict', query.data?.round_id], queryFn: () => api.verdict(query.data!.round_id), enabled: !!query.data?.round_id })
  const accepting = useMutation({
    mutationFn: (decisions: Record<string, boolean | number | string | null>) => api.acceptVerdict(query.data!.round_id, { digest: verdictQuery.data!.verdict!.digest, decisions }),
    onSuccess: (res) => { void qc.invalidateQueries(); if (res.did === 'apply_answers' && res.mapping_id) onSaved(res.mapping_id); else onDone() },
  })
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: api.me })
  const [selected, setSelectedState] = useState<string | null>(null)
  const content = useRef<HTMLElement>(null)
  const setSelected = (value: string) => { setSelectedState(value); requestAnimationFrame(() => content.current?.scrollIntoView({ block: 'start' })) }
  const [answers, setAnswers] = useState<Record<string, boolean | number | string>>({})
  const [unsure, setUnsure] = useState<Record<string, boolean>>({})
  const [preview, setPreview] = useState<AnswerPreview | null>(null)
  const [checked, setChecked] = useState(false), [comment, setComment] = useState('')
  const previewing = useMutation({ mutationFn: () => api.previewAnswers(id, answers), onSuccess: p => { setPreview(p); setSelected('finish') } })
  const saving = useMutation({ mutationFn: () => api.applyAnswers(id, answers, preview!.digest), onSuccess: m => { void qc.invalidateQueries(); onSaved(m.id) } })
  const approve = useMutation({ mutationFn: () => api.approveMapping(id, { actor: 'operator', comment }), onSuccess: () => { void qc.invalidateQueries(); onDone() } })
  const q = query.data, busy = saving.isPending || previewing.isPending || approve.isPending || accepting.isPending
  if (query.isPending) return <div className="p-8 flex gap-3"><Spinner />{t('Loading file evidence…', 'กำลังโหลดข้อมูลสำหรับตรวจไฟล์…')}</div>
  if (!q) return <div role="alert" className="p-6 space-y-3"><p>{t('Could not load this review.', 'โหลดข้อมูลการตรวจไม่ได้')}</p><button className="btn" onClick={() => void query.refetch()}>{t('Retry', 'ลองใหม่')}</button><button className="btn" onClick={onDone}>{t('Back', 'กลับ')}</button></div>
  const editable = !!me?.effective_roles.includes('reviewer') && q.status === 'proposed'
  const current = selected || 'issues', item = q.questions.find(x => x.id === current)
  const update = (key: string, value: boolean | number | string | undefined) => {
    setAnswers(a => { const n = { ...a }; if (value === undefined) delete n[key]; else n[key] = value; return n })
    setUnsure(a => ({ ...a, [key]: value === undefined })); setPreview(null); previewing.reset()
  }
  const error = previewing.error || saving.error || approve.error
  return <div className="space-y-5 pb-8">
    <button className="btn btn-ghost btn-sm" onClick={onDone} disabled={busy}><ArrowLeft size={15} />{t('Review queue', 'กลับรายการที่รอตรวจ')}</button>
    <header className="flex flex-wrap justify-between gap-4 items-start">
      <div className="min-w-0"><div className="flex gap-3 items-center flex-wrap"><h1 className="text-2xl font-semibold text-navy">{t('Check this file', 'ตรวจไฟล์นี้')}</h1><Badge tone={q.blocked ? 'bad' : 'warn'}>{q.blocked ? t('Source check required', 'ต้องตรวจต้นทางก่อน') : t('Waiting for review', 'รอตรวจ')}</Badge></div><p className="mt-2 break-words">{q.context.customer?.name.startsWith(q.context.customer.code) ? q.context.customer.name : `${q.context.customer?.code} · ${q.context.customer?.name}`} <span className="text-muted">/ {q.context.round_label}</span></p><p className="text-xs text-muted mt-1">{t('Mapping version', 'การจับคู่เวอร์ชัน')} {q.context.mapping_version} · {t('Answer → preview → save and recheck', 'ตอบคำถาม → ดูผลที่จะเปลี่ยน → บันทึกและตรวจอีกครั้ง')}</p></div>
      <button className="btn btn-sm" disabled={explain.isPending} onClick={() => explain.mutate({ round_id: q.round_id, mapping_id: id })}>{explain.isPending ? <Spinner /> : <AssistantIcon size={16} />}{t('Explain this file', 'ให้ผู้ช่วยอธิบายไฟล์นี้')}</button>
    </header>
    {explain.isError && <p role="alert" className="text-bad text-sm">{t('Could not open the assistant. Please try again.', 'ยังเปิดผู้ช่วยไม่ได้ ลองกดอีกครั้ง')}</p>}
    <div className="flex flex-wrap gap-2">{q.context.files.map(f => <Link key={f.id} className="btn btn-sm max-w-full text-left" to={`/rounds/${q.round_id}`}><FileText size={14} className="shrink-0" /><span className="break-all">{f.name}</span></Link>)}</div>
    {verdictQuery.data?.verdict && verdictQuery.data.verdict.stage === 'mapping' && verdictQuery.data.verdict.subject.id === q.mapping_id
      ? <VerdictCard v={verdictQuery.data.verdict} editable={editable} busy={busy} onAccept={(d) => accepting.mutate(d)} onOpenDecision={(fid) => { if (q.questions.some(x => x.id === fid)) setSelected(fid) }} error={accepting.error?.message} />
      : verdictQuery.isPending && <div className="card p-4 flex gap-2 items-center text-sm text-muted"><Spinner />{t('Weighing the evidence…', 'กำลังชั่งน้ำหนักหลักฐาน…')}</div>}
    {!editable && <p className="bg-surface-2 border border-line rounded-xl p-3 text-sm">{t('Read-only: this version is already reviewed, or your role cannot change it.', 'ดูข้อมูลได้อย่างเดียว: เวอร์ชันนี้ตรวจไปแล้ว หรือสิทธิ์ของคุณไม่สามารถแก้ไขได้')}</p>}
    <div className="grid lg:grid-cols-[250px_minmax(0,1fr)] gap-5 items-start">
      <nav aria-label={t('Review sections', 'หัวข้อที่ต้องตรวจ')} className="card p-2 space-y-1 lg:sticky lg:top-20 min-w-0">
        <Nav active={current === 'issues'} onClick={() => setSelected('issues')}><TriangleAlert size={16} className="shrink-0 text-warn" /><span>{t('1. Check important issues', '1. ตรวจปัญหาสำคัญก่อน')}</span></Nav>
        <p className="text-xs text-muted px-3 pt-4 pb-2">{t('2. Answer what you can confirm', '2. ตอบสิ่งที่ยืนยันได้')} · {Object.keys(answers).length}/{q.questions.length}</p>
        {q.questions.map((x, i) => <Nav key={x.id} active={current === x.id} onClick={() => setSelected(x.id)}><span className="text-muted text-xs shrink-0">{i + 1}</span><span className="flex-1">{title(x, t, th)}{unsure[x.id] && <small className="block text-warn mt-1">{t('Awaiting information', 'รอข้อมูลเพิ่มเติม')}</small>}</span>{answers[x.id] !== undefined ? <Check size={16} className="text-ok shrink-0" /> : <CircleHelp size={15} className="text-muted shrink-0" />}</Nav>)}
        {!q.questions.length && <p className="text-sm text-muted px-3 py-2">{t('No field questions remain', 'ไม่มีคำถามเกี่ยวกับฟิลด์ค้างอยู่')}</p>}
        <div className="border-t border-line mt-3 pt-2"><Nav active={current === 'finish'} onClick={() => setSelected('finish')}>{t('3. Preview & save', '3. ตรวจผลและบันทึก')}</Nav><Nav active={current === 'technical'} onClick={() => setSelected('technical')}>{t('Technical data', 'ข้อมูลเทคนิค')}</Nav></div>
        <p className="px-3 py-2 text-xs text-muted">{t('Answers are drafts until saved. Unknown answers stay unresolved.', 'คำตอบยังเป็นฉบับร่างจนกว่าจะบันทึก ข้อที่ไม่ทราบจะยังค้างให้ตรวจ')}</p>
      </nav>
      <main ref={content} className="min-w-0 space-y-4 scroll-mt-20">
        {current === 'issues' && <>
          <div className="card"><ImportantIssues q={q} t={t} th={th} /></div>
          <div className="flex justify-end"><button className="btn" onClick={() => setSelected(q.questions[0]?.id || 'finish')}>{t('Go to questions', 'ไปตอบคำถาม')}<ArrowRight size={15} /></button></div>
        </>}
        {item && <section className="card p-4 sm:p-6 space-y-5">
          <div><p className="text-xs text-muted mb-2">{t('Question', 'คำถาม')} {q.questions.indexOf(item) + 1}/{q.questions.length}</p><h2 className="text-xl font-semibold text-navy">{title(item, t, th)}</h2></div>
          <div className="bg-surface-2 rounded-xl p-4 text-sm space-y-2"><p className="font-medium">{t('What the system currently does', 'ตอนนี้ระบบอ่านอย่างไร')}</p><p className="break-words">{item.field.semantic === 'price.vat_included' && item.current.op === 'const' ? t(`The mapping currently assumes VAT is ${item.current.value === true ? 'included' : 'excluded'}.`, `การจับคู่ตอนนี้ตั้งค่าไว้ว่า${item.current.value === true ? 'รวม VAT แล้ว' : 'ยังไม่รวม VAT'}`) : rule(item.current, t)}</p><p className="text-muted">{item.field.semantic === 'price.vat_included' ? t('The price number alone cannot prove VAT inclusion. Confirm from the file documentation or its owner; do not guess.', 'ตัวเลขราคาอย่างเดียวยืนยันเรื่อง VAT ไม่ได้ ต้องตรวจเอกสารประกอบหรือถามเจ้าของไฟล์ หากไม่ทราบให้เว้นไว้') : t('This field has not met the confidence requirement. Compare actual source values with what the system produces below.', 'ฟิลด์นี้ยังไม่ผ่านเกณฑ์ความมั่นใจ ตรวจค่าจริงในต้นทางเทียบกับค่าที่ระบบอ่านได้ด้านล่าง')}</p></div>
          <Evidence key={item.id} id={id} field={item.id} semantic={item.field.semantic} t={t} />
          <fieldset disabled={!editable || busy} className="border-t border-line pt-5 space-y-3"><legend className="font-semibold pt-3">{t('Your decision', 'คุณยืนยันได้ว่าอย่างไร')}</legend>
            {item.kind === 'boolean' ? <div className="flex flex-wrap gap-2">{[true, false].map(v => <button key={String(v)} className={`btn ${answers[item.id] === v ? 'btn-primary' : ''}`} aria-pressed={answers[item.id] === v} onClick={() => update(item.id, v)}>{item.field.semantic === 'price.vat_included' ? v ? t('VAT included', 'รวม VAT แล้ว') : t('VAT excluded', 'ยังไม่รวม VAT') : v ? t('Yes', 'ใช่') : t('No', 'ไม่ใช่')}</button>)}</div>
            : item.kind === 'choice' ? <div className="flex flex-wrap gap-2">{(item.options || []).map(name => <button key={name} className={`btn ${answers[item.id] === name ? 'btn-primary' : ''}`} aria-pressed={answers[item.id] === name} onClick={() => update(item.id, name)}>{name}</button>)}</div>
            : item.kind === 'number' ? <label className="grid gap-2 text-sm">{t('Fixed value for every matching row (only if all have the same value)', 'กำหนดค่าเดียวทุกแถวที่จับคู่ (ใช้เมื่อทุกแถวมีค่าเดียวกันเท่านั้น)')}<input className="input max-w-48" aria-label={title(item, t, th)} type="number" step={item.field.json_type.includes('integer') ? '1' : 'any'} min={item.field.semantic === 'unit.pack_size' ? 1 : undefined} value={answers[item.id] === undefined ? '' : String(answers[item.id])} onChange={e => update(item.id, e.target.value === '' ? undefined : Number(e.target.value))} /></label>
            : <button className={`btn ${answers[item.id] === 'confirm' ? 'btn-primary' : ''}`} aria-pressed={answers[item.id] === 'confirm'} onClick={() => update(item.id, 'confirm')}><Check size={15} />{t('The source and result are correct', 'ตรวจต้นทางและผลแล้ว ถูกต้อง')}</button>}
            <div className="flex flex-wrap gap-2"><Link className="btn" to={editor(q, item.id)}>{t('Read incorrectly — fix it', 'อ่านผิด ต้องแก้')}<ArrowRight size={14} /></Link><button className="btn btn-ghost" aria-pressed={!!unsure[item.id]} onClick={() => update(item.id, undefined)}>{t('Not sure / ask the file owner', 'ยังไม่ทราบ / ต้องถามเจ้าของไฟล์')}</button></div>
            {unsure[item.id] && <p role="status" className="text-sm text-warn">{t('Left unanswered. This question will not be marked resolved.', 'เว้นคำตอบไว้ ข้อนี้จะยังไม่ถูกปิด และไม่ถือว่ายืนยันแล้ว')}</p>}
          </fieldset>
          <div className="flex flex-wrap justify-between gap-3 border-t border-line pt-4"><button className="btn btn-ghost" onClick={() => setSelected(q.questions[q.questions.indexOf(item) + 1]?.id || 'finish')}>{t('Next', 'ถัดไป')}<ArrowRight size={14} /></button><button className="btn btn-primary" disabled={!editable || busy || !Object.keys(answers).length} onClick={() => previewing.mutate()}>{previewing.isPending ? t('Checking…', 'กำลังตรวจ…') : t('Preview my answers', 'ดูผลจากคำตอบ')}</button></div>
        </section>}
        {current === 'finish' && <section className="card p-4 sm:p-6 space-y-5"><h2 className="text-xl font-semibold">{t('Review before saving', 'ตรวจผลก่อนบันทึก')}</h2>
          <p className="text-sm text-muted">{t(`${Object.keys(answers).length} answers selected; ${q.questions.length - Object.keys(answers).length} unanswered. Saving creates a new version and rechecks the policy.`, `เลือกคำตอบแล้ว ${Object.keys(answers).length} ข้อ ยังไม่ตอบ ${q.questions.length - Object.keys(answers).length} ข้อ เมื่อบันทึกจะสร้างเวอร์ชันใหม่และตรวจตามกฎอีกครั้ง`)}</p>
          {Object.keys(answers).length > 0 && <div className="space-y-2">{q.questions.filter(x => answers[x.id] !== undefined).map(x => <div key={x.id} className="flex gap-4 justify-between text-sm border-b border-line py-2"><span>{title(x, t, th)}</span><strong>{answers[x.id] === 'confirm' ? t('Confirmed', 'ยืนยันการอ่านค่า') : typeof answers[x.id] === 'boolean' ? answers[x.id] ? t('Yes / included', 'ใช่ / รวมแล้ว') : t('No / excluded', 'ไม่ใช่ / ยังไม่รวม') : text(answers[x.id])}</strong></div>)}</div>}
          {!preview && !!Object.keys(answers).length && <button className="btn btn-primary" disabled={!editable || busy} onClick={() => previewing.mutate()}>{previewing.isPending ? t('Comparing…', 'กำลังเปรียบเทียบ…') : t('Preview changes', 'ดูผลที่จะเปลี่ยน')}</button>}
          {preview && <Impact preview={preview} q={q} t={t} th={th} />}
          {preview && <button className="btn btn-primary" disabled={!editable || busy || !preview.preview.ok || !preview.comparison.reliable} onClick={() => saving.mutate()}>{saving.isPending ? t('Saving…', 'กำลังบันทึก…') : t('Save answers & recheck', 'บันทึกคำตอบและตรวจอีกครั้ง')}</button>}
          {!!q.open_questions.length && <div className="bg-warn-soft p-4 rounded-xl text-sm"><strong>{t('Other clarification may remain', 'อาจยังมีข้อสงสัยที่ต้องตรวจเพิ่ม')}</strong><p className="mt-1">{t('Saving these answers does not automatically resolve all file issues.', 'การบันทึกคำตอบไม่ได้ปิดทุกปัญหาของไฟล์โดยอัตโนมัติ')}</p><Link className="btn btn-sm mt-3" to={editor(q, undefined, 'changes')}>{t('Inspect all fields', 'เปิดตรวจทุกฟิลด์')}<ArrowRight size={14} /></Link></div>}
          {q.blocked && <p className="text-bad text-sm">{t('Source restrictions still apply; answering questions cannot approve this file.', 'ข้อจำกัดของไฟล์ต้นทางยังคงอยู่ การตอบคำถามไม่ใช่การอนุมัติไฟล์')}</p>}
          {editable && !q.blocked && !q.questions.length && !q.open_questions.length && <div className="border-t border-line pt-4 space-y-3"><label className="flex gap-2 text-sm"><input type="checkbox" checked={checked} onChange={e => setChecked(e.target.checked)} />{t('I reviewed the source, matching and all issues.', 'ฉันตรวจไฟล์ต้นทาง การจับคู่ และปัญหาทั้งหมดแล้ว')}</label><label className="grid gap-2 text-sm">{t('Review note', 'บันทึกการตรวจ')}<textarea className="input" value={comment} maxLength={2000} onChange={e => setComment(e.target.value)} /></label><button className="btn btn-primary" disabled={!checked || busy} onClick={() => approve.mutate()}>{t('Approve field matching', 'อนุมัติการจับคู่ข้อมูล')}</button></div>}
        </section>}
        {current === 'technical' && <section className="card p-4 sm:p-6 space-y-6 text-xs text-ink-2"><h2 className="text-xl font-semibold text-navy">{t('Technical data', 'ข้อมูลเทคนิค')}</h2>
          <div><h3 className="text-sm font-semibold text-ink">{t('Original system messages', 'ข้อความระบบต้นฉบับ')}</h3><ul className="space-y-2 mt-2 break-words">{[...q.reasons, ...q.open_questions].map((r, i) => <li key={i}>{r}</li>)}</ul></div>
          {q.questions.map(x => <div key={x.id}><h3 className="text-sm font-semibold text-ink">{title(x, t, th)}</h3><p className="my-2 break-words">{x.note}</p><ul className="space-y-1">{x.evidence.map((e, i) => <li key={i}>{e}</li>)}</ul><pre className="whitespace-pre-wrap break-all mt-2">{JSON.stringify(x.current, null, 2)}</pre></div>)}
          {preview && <div><h3 className="text-sm font-semibold text-ink">{t('Preview and validation', 'ผลทดลองและข้อผิดพลาด')}</h3><pre className="whitespace-pre-wrap break-all max-h-96 overflow-auto mt-2">{JSON.stringify({ findings: preview.preview.findings, errors: preview.preview.errors, sample: preview.preview.records?.slice(0, 3) }, null, 2)}</pre></div>}
        </section>}
        {error && <p role="alert" className="text-bad rounded-xl border border-bad/30 p-4 text-sm">{error.message}</p>}
      </main>
    </div>
  </div>
}

function Nav({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return <button className={`w-full flex items-start gap-3 text-left p-3 rounded-lg text-sm ${active ? 'bg-signal-soft text-navy font-medium' : 'text-ink-2 hover:bg-surface-2'}`} aria-current={active ? 'step' : undefined} onClick={onClick}>{children}</button>
}

function Evidence({ id, field, semantic, t }: { id: string; field: string; semantic: string | null; t: T }) {
  const query = useQuery({ queryKey: ['question-evidence', id, field], queryFn: () => api.reviewEvidence(id, field), staleTime: 60000, retry: false })
  const [location, setLocation] = useState<number | null>(null)
  if (query.isPending) return <div className="p-5 flex gap-3 text-sm"><Spinner />{t('Reading actual source rows…', 'กำลังอ่านแถวจริงจากไฟล์ต้นทาง…')}</div>
  if (query.isError) return <div role="alert" className="bg-warn-soft p-4 rounded-xl text-sm">{t('Source evidence could not be loaded. Do not confirm without checking the file.', 'โหลดหลักฐานต้นทางไม่ได้ อย่ายืนยันโดยยังไม่ได้ตรวจไฟล์')}<button className="btn btn-sm ml-2" onClick={() => void query.refetch()}>{t('Retry', 'ลองใหม่')}</button></div>
  const d = query.data
  return <div className="space-y-3"><h3 className="font-semibold">{t('Actual source → system result', 'ข้อมูลจริงในไฟล์ → สิ่งที่ระบบอ่านได้')}</h3>
    <p className="text-xs text-muted">{t(`Showing ${d.examples.length} examples from a preview limited to ${d.sample_limit} rows per source. This is not a full-file check.`, `แสดง ${d.examples.length} ตัวอย่าง จากการทดลองอ่านไม่เกิน ${d.sample_limit} แถวต่อแหล่งข้อมูล ยังไม่ได้ตรวจครบทั้งไฟล์`)}</p>
    {d.constant && <p className="text-sm bg-warn-soft rounded-lg p-3">{d.context_only ? t('Prices below are context only. The VAT flag is an assumption, not a value read from a VAT cell.', 'ราคาด้านล่างเป็นข้อมูลประกอบเท่านั้น ค่า VAT เป็นค่าที่ตั้งไว้ ไม่ได้อ่านจากช่อง VAT ในไฟล์') : t('This is a fixed value in the rule, not a value read from a source cell.', 'ค่านี้ถูกกำหนดไว้ในกฎ ไม่ได้อ่านจากเซลล์ต้นทาง')}</p>}
    {d.unavailable || !d.examples.length ? <p className="text-warn text-sm">{t('No output examples are available. Inspect the source and mapping; no values have been invented.', 'ยังไม่มีตัวอย่างผลลัพธ์ให้แสดง ต้องตรวจต้นทางและกฎการจับคู่ก่อน ระบบไม่ได้เติมค่าตัวอย่างขึ้นมาเอง')}</p> : <div className="overflow-x-auto rounded-xl border border-line"><table className="table text-sm"><thead><tr><th>{t('Record', 'รายการ')}</th><th>{t('Source value', 'ค่าในไฟล์')}</th><th>{d.constant ? t('Current assumption', 'ค่าที่ตั้งไว้') : t('System reads', 'ระบบอ่านได้')}</th></tr></thead><tbody>{d.examples.map((x, i) => <tr key={i}><td className="align-top break-all">{x.identity}{x.item !== null && <small className="block text-muted">{t('Item', 'ลำดับย่อย')} {x.item + 1}</small>}</td><td className="align-top min-w-[150px]">{x.cells.length ? <>{x.cells.map((c, j) => <div key={j} className="mb-2"><span className="text-xs text-muted break-words">{c.header}</span><p className="break-all font-medium">{text(c.raw)}</p></div>)}<button className="text-xs text-signal underline underline-offset-4" onClick={() => setLocation(location === i ? null : i)} aria-expanded={location === i}>{t('View source location', 'ดูตำแหน่งต้นฉบับ')}</button>{location === i && <div className="mt-2 bg-surface-2 rounded p-2 space-y-2">{x.cells.map((c, j) => <p key={j} className="text-xs break-all">{c.file}<br />{c.sheet || t('CSV', 'CSV')} · {c.column && c.row ? `${c.column}${c.row}` : t('Exact cell unavailable', 'ไม่มีพิกัดเซลล์ที่ยืนยันได้')}{c.row && <span> · {t('row', 'แถว')} {c.row}</span>}<br />{c.verified === true ? t('Cell value verified', 'ตรวจค่าตรงกับเซลล์แล้ว') : t('Position from extracted source', 'ตำแหน่งจากการอ่านต้นทาง')}</p>)}</div>}</> : <span className="text-muted">{d.constant ? t('Fixed by rule', 'กำหนดไว้ในกฎ') : t('Source cell unavailable', 'ยังยืนยันเซลล์ต้นทางไม่ได้')}</span>}{!x.source_complete && !d.constant && <p className="text-xs text-warn mt-2">{t('Source trace is incomplete.', 'หลักฐานต้นทางยังไม่ครบ')}</p>}</td><td className="align-top font-semibold break-all">{x.value === null || x.value === undefined ? <span className="text-warn font-normal">{t('No value produced', 'ระบบยังไม่ได้ให้ค่า')}</span> : valueLabel(x.value, semantic, t)}</td></tr>)}</tbody></table></div>}
  </div>
}

function ImportantIssues({ q, t, th }: { q: ReviewQuestions; t: T; th: boolean }) {
  const wrong = q.reasons.find(r => r.includes('structure is identical to customer'))
  const other = wrong?.match(/customer (.+?)'s approved format/)?.[1]
  const rowChange = q.reasons.find(r => r.startsWith('row count changed'))?.match(/(\d+%)/)?.[1]
  return <section className="p-4 sm:p-6 space-y-5"><div><h2 className="text-xl font-semibold">{t('Check these before answering', 'ตรวจเรื่องเหล่านี้ก่อนตอบคำถาม')}</h2><p className="text-sm text-muted mt-2">{t('Differences are signals to investigate, not proof that the data is wrong.', 'ความแตกต่างเป็นสัญญาณให้ตรวจ ยังไม่ได้แปลว่าข้อมูลผิด')}</p></div>
    {(wrong || q.blocked) && <div className="rounded-xl border border-bad/30 bg-bad-soft p-4 space-y-2"><h3 className="font-semibold text-bad">{t('Confirm the source file and customer', 'ตรวจว่าไฟล์เป็นของลูกค้ารายนี้จริง')}</h3><p className="text-sm">{other ? t(`Its structure matches a previously approved format for ${other}. This does not prove the owner; check the file sender and customer.`, `โครงสร้างไฟล์เหมือนรูปแบบที่เคยอนุมัติของ ${other} แต่ยังสรุปเจ้าของไฟล์ไม่ได้ ต้องตรวจชื่อผู้ส่งและลูกค้าให้ตรงกัน`) : t('The policy has blocked this file. Check the source and detailed issues before approval.', 'กฎของระบบระงับไฟล์นี้ไว้ ต้องตรวจต้นทางและสาเหตุให้เรียบร้อยก่อนอนุมัติ')}</p><Link className="btn btn-sm" to={`/rounds/${q.round_id}`}>{t('Open source file & customer', 'เปิดไฟล์ต้นทางและข้อมูลลูกค้า')}<ArrowRight size={14} /></Link></div>}
    {q.changes.length > 0 && <div className="space-y-3"><h3 className="font-semibold">{t('Data and field matching that changed', 'ข้อมูลและการจับคู่ที่เปลี่ยนไป')}</h3><p className="text-xs text-muted">{q.comparison_dataset_id ? t('Values compare the stored sample with the previous published dataset. Examples may include unchanged values.', 'ค่าเปรียบเทียบจากตัวอย่างที่บันทึกไว้กับชุดข้อมูลที่เผยแพร่ก่อนหน้า ตัวอย่างอาจมีค่าที่ไม่เปลี่ยนด้วย') : t('No previous published data comparison is available; only mapping changes can be shown.', 'ยังไม่มีข้อมูลที่เผยแพร่ก่อนหน้าให้เทียบ แสดงได้เฉพาะการเปลี่ยนกฎการจับคู่')}</p>{q.changes.map(f => <div key={f.pointer} className="border border-line rounded-xl p-4 space-y-3"><div className="flex flex-wrap justify-between gap-2"><h4 className="font-semibold">{fieldName(f.field, th)}</h4><Badge tone={f.risk === 'critical' || f.risk === 'high' ? 'warn' : 'neutral'}>{f.status === 'removed' ? t('No longer mapped', 'ไม่ได้จับคู่แล้ว') : f.status === 'added' ? t('New mapping', 'เพิ่มการจับคู่') : t('Changed', 'มีการเปลี่ยนแปลง')}</Badge></div>
      {f.check && <p className="text-sm text-ink-2">{f.check.verdict === 'no_data' ? t('Not enough matching records to compare.', 'มีรายการที่จับคู่กันไม่พอ จึงยังเทียบค่าไม่ได้') : t(`${f.check.pairs} matching records compared; ${Math.round((f.check.equal_share ?? 0) * 100)}% kept the same value.`, `เทียบรายการที่ตรงกัน ${f.check.pairs} รายการ มี ${Math.round((f.check.equal_share ?? 0) * 100)}% ที่ค่าเท่าเดิม`)}{f.check.ratio !== undefined && <span> · {t('Ratio', 'อัตราส่วนค่าใหม่ต่อค่าเดิม')} ×{f.check.ratio}</span>}</p>}
      {!!f.check?.examples?.length && <div className="overflow-x-auto"><table className="table text-sm"><thead><tr><th>{t('Record', 'รายการ')}</th><th>{t('Previous published data', 'ข้อมูลเผยแพร่ก่อนหน้า')}</th><th>{t('Current proposal', 'ข้อเสนอครั้งนี้')}</th></tr></thead><tbody>{f.check.examples.map((e, i) => <tr key={i}><td className="break-all">{e.identity}</td><td className="break-all">{text(e.before)}</td><td className={e.before !== e.after ? 'text-warn font-medium break-all' : 'break-all'}>{text(e.after)}</td></tr>)}</tbody></table></div>}
      {!f.check?.examples?.length && <div className="grid sm:grid-cols-2 gap-3 text-sm"><p><span className="text-muted">{t('Previous rule', 'กฎเดิม')}</span><br />{f.before ? rule(f.before.expr, t) : '—'}</p><p><span className="text-muted">{t('Current rule', 'กฎครั้งนี้')}</span><br />{f.after ? rule(f.after.expr, t) : '—'}</p></div>}
      <Link className="btn btn-sm" to={editor(q, f.pointer, 'changes')}>{t('Inspect / correct this field', 'ตรวจและแก้ฟิลด์นี้')}<ArrowRight size={14} /></Link>
    </div>)}</div>}
    {(Number(q.checks.errors) > 0 || Number(q.checks.warnings) > 0) && <div className="border border-warn/30 bg-warn-soft rounded-xl p-4 text-sm space-y-2"><strong>{t('Other validation findings', 'ผลตรวจที่ต้องดูเพิ่มเติม')}</strong><p>{t(`${Number(q.checks.errors) || 0} errors and ${Number(q.checks.warnings) || 0} warnings in the stored validation sample.`, `ตัวอย่างที่ตรวจไว้พบข้อผิดพลาด ${Number(q.checks.errors) || 0} จุด และข้อควรตรวจ ${Number(q.checks.warnings) || 0} จุด`)}</p><Link className="btn btn-sm" to={editor(q, undefined, 'findings')}>{t('Inspect validation findings', 'เปิดดูจุดที่ตรวจไม่ผ่าน')}<ArrowRight size={14} /></Link></div>}
    {!!q.checks.drift && q.checks.drift !== 'identical' && <div className="bg-surface-2 rounded-xl p-4 text-sm"><strong>{t('File layout changed', 'รูปแบบไฟล์เปลี่ยน')}</strong><p className="mt-1">{q.checks.drift === 'columns_removed' ? t('Some previously available columns are missing. Check whether their values still have a valid source.', 'มีคอลัมน์ที่เคยมีหายไป ต้องตรวจว่าค่าที่ใช้อยู่ยังมีต้นทางถูกต้องหรือไม่') : t('The file layout differs from the previously approved format. Check source columns and field matching.', 'โครงสร้างต่างจากรูปแบบที่เคยอนุมัติ ตรวจคอลัมน์ต้นทางและการจับคู่อีกครั้ง')}</p></div>}
    {rowChange && <p className="text-sm bg-warn-soft rounded-xl p-4">{t(`Record count changed ${rowChange}. Check for missing or extra data.`, `จำนวนรายการเปลี่ยน ${rowChange} ต้องตรวจว่ามีข้อมูลตกหล่นหรือเพิ่มมาหรือไม่`)}</p>}
    {!wrong && !q.blocked && !q.changes.length && !rowChange && <p className="text-sm bg-surface-2 rounded-xl p-4">{t('Start by comparing source values with each question. Uncertain fields still need your decision.', 'เริ่มจากเทียบค่าต้นทางในแต่ละคำถาม ฟิลด์ที่ระบบยังไม่มั่นใจยังต้องให้คุณตัดสินใจ')}</p>}
    <Link className="btn btn-sm" to={editor(q, undefined, 'changes')}>{t('View all fields and checks', 'ตรวจทุกฟิลด์และผลตรวจทั้งหมด')}<ArrowRight size={14} /></Link>
  </section>
}

function Impact({ preview: p, q, t, th }: { preview: AnswerPreview; q: ReviewQuestions; t: T; th: boolean }) {
  const c = p.comparison
  const spec = (pointer: string) => q.fields.find(f => f.pointer === pointer.replace(/\/\d+(?=\/|$)/g, '/*'))
  const label = (pointer: string) => { const f = spec(pointer); return f ? fieldName(f, th) : pointer }
  const changes = c.rows.flatMap(r => (r.changes || []).map(d => ({ identity: r.identity, ...d })))
  return <div className="space-y-4"><div className="bg-surface-2 rounded-xl p-4 space-y-2"><h3 className="font-semibold">{t('Preview only — not saved', 'ผลทดลอง — ยังไม่ได้บันทึก')}</h3><p className="text-sm">{t(`Compared ${c.before_records} previous and ${c.after_records} new sample records. ${c.counts.changed_records} records change; ${c.counts.lost} disappear; ${c.counts.gained} appear.`, `เทียบตัวอย่างก่อน ${c.before_records} รายการ กับหลัง ${c.after_records} รายการ เปลี่ยนค่า ${c.counts.changed_records} รายการ หายไป ${c.counts.lost} รายการ เพิ่มมา ${c.counts.gained} รายการ`)}</p><p className="text-xs text-muted">{t(`Limited to ${c.sample_limit} source rows per source. These are sample counts, not the total impact on the file.`, `ทดลองไม่เกิน ${c.sample_limit} แถวต่อแหล่งข้อมูล จำนวนนี้เป็นผลในตัวอย่าง ไม่ใช่จำนวนที่กระทบทั้งไฟล์`)}</p></div>
    {!c.reliable && <p role="alert" className="text-bad">{t('The comparison could not complete. Inspect source fields before saving.', 'เปรียบเทียบไม่สำเร็จ ต้องตรวจต้นทางก่อนบันทึก')}</p>}
    {changes.length > 0 ? <div className="overflow-auto max-h-96 border border-line rounded-xl"><table className="table text-sm"><thead className="sticky top-0 bg-surface"><tr><th>{t('Record', 'รายการ')}</th><th>{t('Field', 'ข้อมูล')}</th><th>{t('Before', 'ก่อน')}</th><th>{t('After', 'หลัง')}</th></tr></thead><tbody>{changes.map((d, i) => <tr key={i}><td className="break-all">{d.identity}</td><td className="break-words">{label(d.pointer)}</td><td className="break-all">{valueLabel(d.before, spec(d.pointer)?.semantic, t)}</td><td className="font-medium text-signal break-all">{valueLabel(d.after, spec(d.pointer)?.semantic, t)}</td></tr>)}</tbody></table></div> : c.reliable && <p className="text-sm">{t('No values change in this sample. Confirming a mapping records your decision without changing its values.', 'ไม่พบค่าที่เปลี่ยนในตัวอย่างนี้ การยืนยันการอ่านค่าจะบันทึกการตัดสินใจโดยไม่ได้เปลี่ยนค่า')}</p>}
    {!!p.preview.findings?.filter(f => f.severity === 'error').length && <p className="text-bad text-sm">{t('Some validation errors remain. Saving rechecks the policy and does not approve the file.', 'ยังพบข้อผิดพลาดในการตรวจข้อมูล การบันทึกจะตรวจตามกฎอีกครั้ง ไม่ใช่การอนุมัติไฟล์')}</p>}
  </div>
}
