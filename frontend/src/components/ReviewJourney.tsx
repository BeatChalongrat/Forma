import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowLeft, ArrowRight, Check, Copy, FileText, Pause, TriangleAlert } from 'lucide-react'
import { api, type Me, type ReviewDraftPayload, type ReviewQuestions } from '../lib/api'
import { useReviewDraft } from '../lib/useReviewDraft'
import { reviewToolUrl } from '../lib/reviewNavigation'
import { reviewFlow } from '../lib/reviewFlow'
import { answerLabel, answerScope } from '../lib/reviewLabels'
import { usePreferences } from '../lib/preferences'
import { AssistantIcon } from '../lib/icons'
import { Spinner } from './ui'
import { RoundHistory } from './Inspection'
import { Evidence, ImportantIssues, Impact } from './ReviewPanels'
import { questionTitle, rule, type Question } from '../lib/reviewPresentation'
import { ReviewOutput } from './ReviewOutput'
import { ReviewDelivery } from './ReviewDelivery'

type Props = { id: string; onSaved: (id: string, state?: string, job?: string | null) => void; onDone: () => void }

export function ReviewJourney(props: Props) {
  const { t } = usePreferences()
  const q = useQuery({ queryKey: ['questions', props.id], queryFn: () => api.reviewQuestions(props.id) })
  const me = useQuery({ queryKey: ['me'], queryFn: api.me })
  if (q.isError || me.isError) return <div role="alert" className="card p-6 space-y-4"><p>{t('Could not load this file.', 'โหลดข้อมูลไฟล์นี้ไม่ได้')}</p><button className="btn" onClick={() => { void q.refetch(); void me.refetch() }}>{t('Try again', 'ลองใหม่')}</button><button className="btn btn-ghost" onClick={props.onDone}>{t('Back to the list', 'กลับรายการ')}</button></div>
  if (!q.data || !me.data) return <p className="p-8 flex gap-2"><Spinner />{t('Loading file…', 'กำลังเปิดไฟล์…')}</p>
  return <Journey key={props.id} {...props} q={q.data} me={me.data} />
}

function Journey({ id, q, me, onSaved, onDone }: Props & { q: ReviewQuestions; me: Me }) {
  const { t, prefs } = usePreferences(), th = prefs.language === 'th'
  const qc = useQueryClient(), nav = useNavigate(), location = useLocation()
  const [params, setParams] = useSearchParams()
  const editable = q.status === 'proposed' && me.effective_roles.includes('reviewer')
  const draft = useReviewDraft(id, me, editable)
  const { payload, store } = draft
  const [checkedDigest, setCheckedDigest] = useState(''), [copyState, setCopyState] = useState('')
  const panelTop = useRef<HTMLDivElement>(null)
  const round = useQuery({ queryKey: ['review-round', q.round_id], queryFn: () => api.round(q.round_id), refetchInterval: 5000 })
  const latest = round.data?.summary.mapping_version_id
  const outdated = !!latest && latest !== id
  const flow = reviewFlow(q, payload.answers, payload.acknowledged)
  const defaultSection = q.status !== 'proposed' ? 'ready' : q.questions[0]?.id || (flow.notes.length ? 'notes' : 'finish')
  const requested = params.get('section') || payload.section || defaultSection
  const section = ['finish', 'notes', 'ready', 'history', 'issues', 'technical', ...q.questions.map(x => x.id)].includes(requested) ? requested : defaultSection
  useEffect(() => {
    // Materialize the resumed step before pushing the next one. Otherwise browser Back to an
    // entry without `section` would resolve to the NEW draft step instead of its original step.
    if (!draft.loading && !params.has('section')) setParams(old => { const next = new URLSearchParams(old); next.set('section', section); return next }, { replace: true })
  }, [draft.loading, params, section, setParams])
  useEffect(() => {
    if (editable && !outdated && !draft.loading && !draft.conflict && params.has('section') && store.snapshot().payload.section !== section) {
      store.change({ ...store.snapshot().payload, section })
    }
  }, [editable, outdated, draft.loading, draft.conflict, params, section, store])
  const panel = params.get('panel') || (['history', 'issues', 'technical'].includes(section) ? section : '')
  const item = q.questions.find(x => x.id === section)
  const decisions = { answers: payload.answers, remember: payload.remember.filter(k => k in payload.answers), acknowledged: payload.acknowledged }
  const signature = JSON.stringify(decisions)
  const preview = useQuery({ queryKey: ['review-preview', id, signature], queryFn: () => api.reviewPreview(id, decisions), enabled: editable && !outdated && !draft.loading && !draft.conflict && section === 'finish' && !panel, retry: false, staleTime: 30000, refetchOnWindowFocus: false })
  const checked = !!preview.data && checkedDigest === preview.data.digest
  const setChecked = (value: boolean) => setCheckedDigest(value ? preview.data?.digest || '' : '')
  const confirm = useMutation({
    mutationFn: async () => {
      if (!await store.flush()) throw new Error(t('Save or resolve the draft before confirming.', 'บันทึกร่างหรือเลือกคำตอบที่ต้องใช้ก่อนยืนยัน'))
      return api.confirmReview(id, { ...decisions, comment: payload.comment, digest: preview.data!.digest })
    },
    onSuccess: result => { store.clearBackup(); void qc.invalidateQueries(); onSaved(result.mapping_id, result.state === 'processing' ? 'ready' : 'finish', result.job_id) },
  })
  const busy = confirm.isPending
  const change = (patch: Partial<ReviewDraftPayload>) => { if (!editable || busy || outdated) return; store.change({ ...store.snapshot().payload, ...patch }); if (patch.answers || patch.remember || patch.acknowledged) setChecked(false); confirm.reset() }
  const select = (value: string) => {
    change({ section: value })
    setParams(old => { const n = new URLSearchParams(old); n.set('section', value); n.delete('panel'); return n })
    requestAnimationFrame(() => panelTop.current?.scrollIntoView({ block: 'start', behavior: 'smooth' }))
  }
  const showPanel = (value: string) => { setParams(old => { const n = new URLSearchParams(old); n.set('section', section); n.set('panel', value); return n }); requestAnimationFrame(() => panelTop.current?.scrollIntoView({ block: 'start' })) }
  const closePanel = () => setParams(old => { const n = new URLSearchParams(old); n.delete('panel'); if (['issues', 'history', 'technical'].includes(n.get('section') || '')) n.set('section', defaultSection); return n })
  const leave = async (action: () => void) => { if (busy) return; if (!editable || outdated || !draft.pending || await store.flush()) { void qc.invalidateQueries({ queryKey: ['review'] }); action() } }
  const returnTo = location.pathname + location.search
  const tool = (path: string) => reviewToolUrl(path, returnTo)
  const openTool = (path: string) => void leave(() => nav(tool(path)))
  const update = (key: string, value: boolean | number | string | undefined) => {
    const answers = { ...payload.answers }, remember = payload.remember.filter(k => k !== key)
    if (value === undefined || typeof value === 'string' && !value.trim() || typeof value === 'number' && !Number.isFinite(value)) delete answers[key]
    else answers[key] = value
    change({ answers, remember, unsure: { ...payload.unsure, [key]: !(key in answers) } })
  }
  const explain = useMutation({ mutationFn: () => api.createChat({ round_id: q.round_id, mapping_id: id }), onSuccess: chat => { void qc.invalidateQueries({ queryKey: ['assistant-chats'] }); nav(`/assistant?chat=${chat.id}`, { state: { send: { request_id: crypto.randomUUID(), question: t('Explain what to check in this file. ', 'ช่วยอธิบายสิ่งที่ต้องตรวจในไฟล์นี้ ') + (item ? questionTitle(item, t, th) : '') } } }) } })
  const copyQuestion = async () => { try { await navigator.clipboard.writeText(`${q.context.customer?.name}\n${q.context.files.map(f => f.name).join(', ')}\n${item ? questionTitle(item, t, th) : flow.notes.join('\n')}`); setCopyState(t('Copied', 'คัดลอกแล้ว')) } catch { setCopyState(t('Select and copy the question above.', 'เลือกข้อความคำถามด้านบนแล้วคัดลอกได้เลย')) } }
  if (draft.loading) return <div className="p-8 space-y-3">{draft.error ? <><p role="alert">{t('Could not retrieve saved answers.', 'โหลดคำตอบที่เก็บไว้ไม่ได้')} {draft.error}</p><button className="btn" onClick={() => void store.load()}>{t('Retry', 'ลองใหม่')}</button></> : <p className="flex gap-2"><Spinner />{t('Restoring your answers…', 'กำลังเปิดคำตอบที่เก็บไว้…')}</p>}<button className="btn btn-ghost" onClick={onDone}>{t('Back to the list', 'กลับรายการ')}</button></div>
  const stage = q.status !== 'proposed' && !outdated ? 3 : section === 'finish' ? 2 : 1
  const ordered = [...q.questions.map(x => x.id), ...(flow.notes.length ? ['notes'] : []), 'finish']
  const index = ordered.indexOf(section)
  const canConfirm = !!preview.data?.preview.ok && !!preview.data.comparison.reliable && !!preview.data.comparison.validation?.passed && !draft.conflict && !outdated && (flow.hasDraft || !flow.remaining && !flow.blocked)
  return <div className="space-y-5 pb-8">
    <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => void leave(onDone)}><ArrowLeft size={15} />{t('Back to the review list', 'กลับรายการที่รอตรวจ')}</button>
    <header className="flex flex-wrap items-start justify-between gap-4"><div className="min-w-0"><h1 className="text-2xl font-semibold text-navy">{t('Review file', 'ตรวจไฟล์')}</h1><p className="mt-2 break-words">{q.context.customer?.name} <span className="text-muted">· {q.context.round_label}</span></p><div className="flex gap-2 flex-wrap mt-3">{q.context.files.map(f => <button key={f.id} className="btn btn-sm max-w-full" onClick={() => openTool(`/rounds/${q.round_id}`)}><FileText size={14} className="shrink-0" /><span className="truncate">{f.name}</span></button>)}</div></div><button className="btn btn-sm" disabled={busy || explain.isPending} onClick={() => void leave(() => explain.mutate())}><AssistantIcon size={16} />{t('Ask about this file', 'ถามเกี่ยวกับไฟล์นี้')}</button></header>
    {explain.isError && <p role="alert" className="text-bad">{explain.error.message}</p>}
    {outdated && <section className="card p-5 space-y-3" role="status"><h2 className="font-semibold">{t('This is an earlier version', 'กำลังดูเวอร์ชันก่อนหน้า')}</h2><p className="text-sm">{t('Your saved draft remains here. Continue on the latest version to avoid answering an outdated question.', 'ร่างคำตอบของเวอร์ชันนี้ยังเก็บไว้ เปิดเวอร์ชันล่าสุดเพื่อดูว่ามีข้อไหนต้องตอบอีก')}</p><button className="btn btn-primary" onClick={() => onSaved(latest!, 'finish')}>{t('Open latest version', 'เปิดเวอร์ชันล่าสุด')}</button></section>}
    <ol className="grid grid-cols-3 gap-2 border-b border-line pb-4" aria-label={t('Review progress', 'ขั้นตอนการตรวจ')}>
      {[t('Answer questions', 'ตอบคำถาม'), t('Check & confirm', 'ตรวจผลและยืนยัน'), t('Get your file', 'รับไฟล์')].map((label, i) => <li key={label} aria-current={stage === i + 1 ? 'step' : undefined} className={`flex gap-2 items-center text-sm ${stage === i + 1 ? 'font-semibold text-navy' : 'text-muted'}`}><span className={`w-7 h-7 shrink-0 rounded-full inline-flex items-center justify-center ${stage === i + 1 ? 'bg-signal-soft' : 'bg-surface-2'}`}>{stage > i + 1 ? <Check size={15} /> : i + 1}</span>{label}</li>)}
    </ol>
    {editable && !outdated && <div className="flex justify-between gap-3 items-center flex-wrap text-xs text-muted"><span role="status">{draft.conflict ? t('Another tab changed this draft', 'อีกแท็บแก้ร่างนี้แล้ว') : draft.error ? t('Draft not yet synced', 'ร่างยังบันทึกไม่สำเร็จ') : draft.pending ? t('Saving draft…', 'กำลังเก็บร่าง…') : t('Draft saved · result not confirmed yet', 'เก็บร่างแล้ว · ยังไม่ยืนยันผลลัพธ์')}</span><button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => void leave(onDone)}><Pause size={13} />{t('Save draft & leave', 'พักไว้ กลับมาทำต่อได้')}</button></div>}
    {draft.conflict && editable && !outdated && <section role="alert" className="card p-5 space-y-3"><h2 className="font-semibold">{t('Choose which draft to continue with', 'เลือกร่างที่จะทำต่อ')}</h2><p className="text-sm">{t('Another tab saved a different draft. Neither version has been confirmed.', 'อีกแท็บบันทึกร่างที่ต่างกัน ทั้งสองร่างยังไม่ใช่คำตอบที่ยืนยันแล้ว')}</p><div className="grid sm:grid-cols-2 gap-4">{[[t('This tab', 'แท็บนี้'), payload.answers], [t('Latest saved draft', 'ร่างที่อีกแท็บเก็บไว้'), draft.conflict.payload.answers || {}]].map(([label, answers]) => <div className="bg-surface-2 p-3 rounded-lg text-sm" key={String(label)}><strong>{String(label)}</strong>{q.questions.map(x => <p className="mt-2" key={x.id}>{questionTitle(x, t, th)}: {x.id in (answers as object) ? answerLabel(x, (answers as Record<string, boolean | number | string>)[x.id], t) : t('Unanswered', 'ยังไม่ตอบ')}</p>)}</div>)}</div><div className="flex gap-2 flex-wrap"><button className="btn" onClick={() => store.resolve(false)}>{t('Use the saved draft', 'ใช้ร่างจากอีกแท็บ')}</button><button className="btn" onClick={() => { store.resolve(true); void store.flush() }}>{t('Save this tab over that draft', 'ใช้ร่างของแท็บนี้แทน')}</button></div></section>}
    {draft.error && !draft.conflict && editable && !outdated && <p role="alert" className="text-bad text-sm">{draft.error} <button className="btn btn-sm" onClick={() => void store.flush()}>{t('Retry saving', 'ลองเก็บร่างอีกครั้ง')}</button></p>}
    <div ref={panelTop} className="scroll-mt-24" />
    <div className="flex gap-2 flex-wrap"><button className={`btn btn-sm ${panel === 'issues' ? 'bg-surface-2' : 'btn-ghost'}`} onClick={() => showPanel('issues')}><TriangleAlert size={14} />{t('File checks', 'จุดที่ต้องตรวจ')}</button><button className="btn btn-sm btn-ghost" onClick={() => showPanel('history')}>{t('Saved answers & history', 'คำตอบที่ยืนยันและประวัติ')}</button><button className="btn btn-sm btn-ghost" onClick={() => showPanel('technical')}>{t('Detailed tools', 'เครื่องมือตรวจละเอียด')}</button></div>
    {panel ? <section className="card p-5 space-y-4"><button className="btn btn-ghost btn-sm" onClick={closePanel}><ArrowLeft size={14} />{t('Back to this file’s current step', 'กลับขั้นตอนที่ทำอยู่ของไฟล์นี้')}</button>{panel === 'issues' ? <ImportantIssues q={q} t={t} th={th} returnTo={returnTo} /> : panel === 'history' ? <RoundHistory roundId={q.round_id} mappingId={id} /> : <><h2 className="font-semibold">{t('Inspect and correct field matching', 'ตรวจและแก้การจับคู่ข้อมูล')}</h2><p className="text-sm text-muted">{t('Your answers are saved as a draft. Use Back to review in the editor to return here.', 'คำตอบเก็บเป็นร่างไว้แล้ว ในหน้าตรวจละเอียดกด “กลับมาตรวจไฟล์นี้” เพื่อทำต่อที่เดิม')}</p><button className="btn" onClick={() => openTool(`/rounds/${q.round_id}/mapping/${id}`)}>{t('Open all fields', 'เปิดตรวจทุกฟิลด์')}<ArrowRight size={14} /></button><h3 className="font-semibold">{t('Original system checks', 'ผลตรวจต้นฉบับจากระบบ')}</h3><ul className="text-xs text-muted space-y-2 break-words">{q.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul></>}</section>
    : q.status !== 'proposed' || outdated ? <ReviewDelivery q={q} me={me} outdated={outdated} returnTo={returnTo} onDone={onDone} />
    : <div className="grid lg:grid-cols-[230px_minmax(0,1fr)] gap-5 items-start">
      <nav className="space-y-1" aria-label={t('Questions in this file', 'คำถามของไฟล์นี้')}><p className="text-xs text-muted px-3 py-2">{q.questions.length ? t(`${flow.drafts} / ${q.questions.length} answered`, `ตอบแล้ว ${flow.drafts} / ${q.questions.length} ข้อ`) : t('No questions to answer', 'ไม่มีคำถามที่ต้องตอบ')}</p>{q.questions.map((x, i) => <button key={x.id} className={`w-full text-left rounded-xl p-3 flex gap-3 text-sm ${section === x.id ? 'bg-surface-2 font-medium' : 'hover:bg-surface-2'}`} disabled={busy} aria-current={section === x.id ? 'step' : undefined} onClick={() => select(x.id)}><span className="text-muted">{payload.answers[x.id] !== undefined ? <Check size={15} /> : i + 1}</span><span>{questionTitle(x, t, th)}<small className="block text-muted mt-1">{payload.unsure[x.id] ? t('Waiting for information', 'รอข้อมูลเพิ่ม') : payload.answers[x.id] !== undefined ? t('Answered in draft', 'มีคำตอบในร่างแล้ว') : t('Unanswered', 'ยังไม่ตอบ')}</small></span></button>)}{flow.notes.length > 0 && <button className={`w-full text-left text-sm rounded-xl p-3 ${section === 'notes' ? 'bg-surface-2' : ''}`} disabled={busy} onClick={() => select('notes')}>{t('Other checks', 'เรื่องที่ต้องตรวจเพิ่ม')} · {flow.acknowledgedCount}/{flow.notes.length}</button>}<button className={`w-full text-left text-sm rounded-xl p-3 ${section === 'finish' ? 'bg-surface-2 font-medium' : ''}`} disabled={busy} onClick={() => select('finish')}>{t('Check & confirm', 'ตรวจผลและยืนยัน')}</button></nav>
      <section className="card p-5 sm:p-6 space-y-6 min-w-0">
        {item && <><div><p className="text-xs text-muted mb-2">{t(`Question ${index + 1} of ${q.questions.length}`, `คำถาม ${index + 1} จาก ${q.questions.length}`)}</p><h2 className="text-xl font-semibold">{questionTitle(item, t, th)}</h2><p className="text-sm text-muted mt-3">{item.reason_th && th ? item.reason_th : item.field.semantic === 'price.vat_included' ? t('The price alone does not tell us whether VAT is included. Check the file or ask its owner.', 'ตัวเลขราคาอย่างเดียวบอกไม่ได้ว่ารวม VAT หรือยัง ตรวจจากเอกสารหรือถามเจ้าของไฟล์ได้') : t('Compare the source with the result below before answering.', 'เทียบข้อมูลต้นทางกับสิ่งที่ระบบอ่านได้ด้านล่างก่อนตอบ')}</p></div>
          <p className="text-sm bg-surface-2 p-4 rounded-xl">{t('Current reading: ', 'ตอนนี้ระบบอ่านว่า: ')}{item.field.semantic === 'price.vat_included' && item.current.op === 'const' ? item.current.value === true ? t('VAT included', 'รวม VAT แล้ว') : t('VAT excluded', 'ยังไม่รวม VAT') : rule(item.current, t)}</p>
          <Evidence key={item.id} id={id} field={item.field.pointer} semantic={item.field.semantic} t={t} />
          <fieldset disabled={!editable || busy || !!draft.conflict} className="space-y-4"><legend className="font-semibold mb-3">{t('Your answer', 'คำตอบของคุณ')}</legend><QuestionInput item={item} answer={payload.answers[item.id]} onChange={v => update(item.id, v)} onFix={() => openTool(`/rounds/${q.round_id}/mapping/${id}?field=${encodeURIComponent(item.field.pointer)}`)} onSource={() => openTool(`/rounds/${q.round_id}`)} />
            <p className="text-xs text-muted">{answerScope(item, payload.remember.includes(item.id) || ['value_map', 'date_format'].includes(item.action || '') || item.kind === 'choice' || item.kind === 'confirm', t)}</p>
            {(item.scope === 'constant_for_this_file' || item.kind === 'boolean' || item.kind === 'number') && <label className="flex gap-2 text-sm"><input type="checkbox" checked={payload.remember.includes(item.id)} disabled={payload.answers[item.id] === undefined} onChange={e => change({ remember: e.target.checked ? [...payload.remember, item.id] : payload.remember.filter(k => k !== item.id) })} />{t('After confirmation, remember for this customer when the format and conditions match.', 'หลังยืนยัน ให้จำไว้ใช้กับลูกค้ารายนี้เมื่อรูปแบบและเงื่อนไขตรงกัน')}</label>}
          </fieldset>
          {payload.unsure[item.id] && <div className="border border-line rounded-xl p-4 space-y-3 text-sm"><p>{t('This stays unanswered. Other answers are kept, and you can come back later.', 'ข้อนี้ยังรอข้อมูลเพิ่ม คำตอบข้ออื่นเก็บไว้แล้ว กลับมาทำต่อภายหลังได้')}</p><button className="btn btn-sm" onClick={() => void copyQuestion()}><Copy size={14} />{t('Copy question for the file owner', 'คัดลอกคำถามไปถามเจ้าของไฟล์')}</button><span role="status">{copyState}</span></div>}
        </>}
        {section === 'notes' && <><h2 className="text-xl font-semibold">{t('Other points to check', 'เรื่องที่ต้องตรวจเพิ่ม')}</h2><p className="text-sm text-muted">{t('Confirm only the items you have checked. Leave uncertain items for later.', 'เลือกเฉพาะเรื่องที่ตรวจแล้ว หากยังไม่ทราบให้เว้นไว้ก่อน')}</p>{flow.notes.map((note, i) => <label key={note} className="flex gap-3 border-b border-line pb-4 text-sm"><input type="checkbox" disabled={!editable || busy || !!draft.conflict} checked={payload.acknowledged.includes(note)} onChange={e => change({ acknowledged: e.target.checked ? [...payload.acknowledged, note] : payload.acknowledged.filter(n => n !== note) })} /><span><strong>{i + 1}. {t('I checked this point', 'ตรวจเรื่องนี้แล้ว')}</strong><span className="block mt-2 break-words text-ink-2">{note}</span></span></label>)}</>}
        {section === 'finish' && <><h2 className="text-xl font-semibold">{t('Check the result before confirming', 'ตรวจผลก่อนยืนยัน')}</h2><p className="text-sm text-muted">{flow.remaining ? t(`${flow.remaining} items still need information. You can save confirmed answers first; the file will stay in review.`, `ยังรอข้อมูล ${flow.remaining} ข้อ บันทึกเฉพาะคำตอบที่มั่นใจได้ ไฟล์จะยังรอตรวจต่อ`) : t('Check your answers and the result below. Confirmation saves your decision and prepares the output.', 'ตรวจคำตอบและตัวอย่างด้านล่าง เมื่อยืนยัน ระบบจะบันทึกการตัดสินใจและเตรียมผลลัพธ์ให้')}</p>
          {q.questions.filter(x => x.id in payload.answers).map(x => <div key={x.id} className="flex flex-wrap items-start gap-3 border-b border-line pb-3 text-sm"><div className="flex-1 min-w-40">{questionTitle(x, t, th)}<strong className="block mt-2">{answerLabel(x, payload.answers[x.id], t)}</strong><p className="text-xs text-muted mt-1">{answerScope(x, payload.remember.includes(x.id) || ['value_map', 'date_format'].includes(x.action || '') || x.kind === 'choice' || x.kind === 'confirm', t)}</p></div><button className="btn btn-sm btn-ghost" disabled={busy} onClick={() => select(x.id)}>{t('Change answer', 'แก้คำตอบ')}</button></div>)}
          {preview.isFetching && <p role="status" className="flex gap-2 text-sm"><Spinner />{t('Checking the complete file with your answers…', 'กำลังตรวจข้อมูลทั้งไฟล์ด้วยคำตอบของคุณ…')}</p>}
          {preview.isError && <div role="alert" className="space-y-3 text-sm"><p className="text-bad">{t('Could not check the result. Your draft is still saved.', 'ยังตรวจผลไม่ได้ ร่างคำตอบยังเก็บอยู่')} {preview.error.message}</p><button className="btn" onClick={() => void preview.refetch()}>{t('Check again', 'ตรวจใหม่')}</button></div>}
          {preview.data && !preview.isFetching && <><ReviewOutput records={preview.data.preview.records.slice(0, 5)} fields={q.fields} total={preview.data.comparison.after_records} /><Impact preview={preview.data} q={q} t={t} th={th} /></>}
          {flow.blocked && <p role="alert" className="text-warn text-sm">{t('File checks still require attention. Saved answers will not approve this file.', 'ยังมีปัญหาที่ต้องแก้ การบันทึกคำตอบจะยังไม่อนุมัติไฟล์นี้')} <button className="btn btn-sm" onClick={() => showPanel('issues')}>{t('Inspect issues', 'ดูจุดที่ต้องแก้')}</button></p>}
          {!flow.remaining && !flow.blocked && <label className="flex gap-3 text-sm"><input type="checkbox" checked={checked} disabled={busy || preview.isFetching || !preview.data} onChange={e => setChecked(e.target.checked)} />{t('I checked the source, answers, and result. They are correct.', 'ฉันตรวจต้นทาง คำตอบ และผลลัพธ์แล้ว ถูกต้องตามที่ต้องการ')}</label>}
          <label className="grid gap-2 text-sm">{t('Note (optional)', 'หมายเหตุ (ไม่บังคับ)')}<textarea className="input" maxLength={2000} rows={2} value={payload.comment} disabled={busy} onChange={e => change({ comment: e.target.value })} /></label>
        </>}
        {confirm.isError && <div role="alert" className="text-bad text-sm space-y-2"><p>{confirm.error.message}</p><button className="btn btn-sm" onClick={() => { confirm.reset(); setChecked(false); void qc.invalidateQueries({ queryKey: ['questions', id] }); void preview.refetch(); void round.refetch() }}>{t('Load latest check', 'ตรวจข้อมูลล่าสุดอีกครั้ง')}</button></div>}
        <footer className="sticky bottom-0 bg-surface border-t border-line pt-4 pb-2 flex justify-between gap-3 flex-wrap"><button className="btn btn-ghost" disabled={busy} onClick={() => index > 0 ? select(ordered[index - 1]) : void leave(onDone)}><ArrowLeft size={15} />{index > 0 ? t('Previous step', 'ย้อนกลับขั้นก่อนหน้า') : t('Review list', 'กลับรายการ')}</button>{section !== 'finish' ? <button className="btn btn-primary" disabled={busy || !editable} onClick={() => select(ordered[index + 1] || 'finish')}>{index + 1 === ordered.length - 1 ? t('Check the result', 'ตรวจผลลัพธ์') : t('Next', 'ข้อถัดไป')}<ArrowRight size={15} /></button> : <button className="btn btn-primary" disabled={!editable || busy || preview.isFetching || !canConfirm || (!flow.remaining && !flow.blocked && !checked)} onClick={() => confirm.mutate()}>{busy ? <Spinner /> : <Check size={15} />}{busy ? t('Saving and checking…', 'กำลังบันทึกและตรวจ…') : flow.remaining || flow.blocked ? t('Save confirmed answers', 'บันทึกคำตอบที่ยืนยันได้') : t('Confirm & prepare file', 'ยืนยันและเตรียมไฟล์')}</button>}</footer>
      </section>
    </div>}
  </div>
}

function QuestionInput({ item, answer, onChange, onFix, onSource }: { item: Question; answer: boolean | number | string | undefined; onChange: (v: boolean | number | string | undefined) => void; onFix: () => void; onSource: () => void }) {
  const { t } = usePreferences()
  return <div className="space-y-3">
    {item.scope === 'source_file' ? <button className="btn" onClick={onSource}>{t('Add or replace source information', 'เปิดเพิ่มหรือแก้ไฟล์ต้นทาง')}</button>
      : item.kind === 'boolean' ? <div className="flex gap-2 flex-wrap">{[true, false].map(v => <button key={String(v)} className={`btn ${answer === v ? 'btn-primary' : ''}`} aria-pressed={answer === v} onClick={() => onChange(v)}>{answerLabel(item, v, t)}</button>)}</div>
      : item.kind === 'text' || item.allow_custom && item.kind !== 'number' ? <input className="input" aria-label={t('Your answer', 'คำตอบของคุณ')} maxLength={5000} value={String(answer ?? '')} onChange={e => onChange(e.target.value)} />
      : item.kind === 'choice' || item.kind === 'enum' ? <div className="flex gap-2 flex-wrap">{(item.options || []).map(v => <button key={v} className={`btn ${answer === v ? 'btn-primary' : ''}`} aria-pressed={answer === v} onClick={() => onChange(v)}>{answerLabel(item, v, t)}</button>)}</div>
      : item.kind === 'number' ? <label className="grid gap-2 text-sm">{t('Same value for every matching row', 'ค่าเดียวกันสำหรับทุกแถวที่ตรงเงื่อนไข')}<input className="input max-w-52" type="number" step={item.field.json_type.includes('integer') ? '1' : 'any'} min={item.field.semantic === 'unit.pack_size' ? 1 : undefined} value={String(answer ?? '')} onChange={e => onChange(e.target.value === '' ? undefined : Number(e.target.value))} /></label>
      : <button className={`btn ${answer === 'confirm' ? 'btn-primary' : ''}`} aria-pressed={answer === 'confirm'} onClick={() => onChange('confirm')}><Check size={15} />{t('I checked: the result is correct', 'ตรวจแล้ว ระบบอ่านถูกต้อง')}</button>}
    <div className="flex gap-2 flex-wrap"><button className="btn btn-ghost" onClick={() => onChange(undefined)}>{t('Not sure yet', 'ยังไม่ทราบ')}</button><button className="btn btn-ghost" onClick={onFix}>{t('The result is wrong — correct it', 'ระบบอ่านผิด ต้องแก้')}</button></div>
  </div>
}
