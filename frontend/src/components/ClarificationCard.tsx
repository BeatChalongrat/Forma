import { useId, useState } from 'react'
import { ArrowLeft, ArrowRight } from 'lucide-react'
import { type Clarification } from '../lib/api'
import { usePreferences } from '../lib/preferences'

type CardState = { step: number; answers: Record<number, string>; custom: Record<number, string>; submitted?: boolean }

export function ClarificationCard({ questions, onAnswer, disabled, saved, onSave }: { saved?: CardState; onSave?: (state: CardState) => void; questions: Clarification[]; onAnswer?: (text: string) => boolean | void | Promise<boolean | void>; disabled?: boolean }) {
  const { t } = usePreferences(), group = useId()
  const [local, setLocal] = useState<CardState>({ step: 0, answers: {}, custom: {} })
  const { step, answers, custom } = saved ?? local
  const question = questions[step], choice = answers[step] || ''
  const unsure = t('I do not know yet; leave this unresolved', 'ยังไม่ทราบ ขอเว้นเรื่องนี้ไว้ก่อน')
  const selected = choice === '__custom' ? custom[step]?.trim() : choice
  const answer = (index: number) => answers[index] === '__custom' ? custom[index]?.trim() : answers[index]
  if (saved?.submitted) return <p className="text-sm text-muted">{t("Answers sent. You can add a correction in the chat.", "ส่งคำตอบแล้ว หากต้องการแก้ไข บอกเพิ่มเติมในแชตได้เลย")}</p>
  function save(next: Partial<CardState>) { const value = { step, answers, custom, ...next }; setLocal(value); onSave?.(value) }
  if (!onAnswer) return <div className="rounded-2xl border border-line p-4 text-sm text-ink-2"><p className="text-xs text-muted m-0">{t('Clarification requested', 'ข้อมูลที่ผู้ช่วยขอเพิ่มเติม')}</p><ul className="mt-2 space-y-2">{questions.map(q => <li key={q.question}>{q.question}</li>)}</ul></div>
  async function advance(value: string) {
    const next = { ...answers, [step]: value }
    save({ answers: next, step: Math.min(step + 1, questions.length - 1) })
    if (step === questions.length - 1) { const accepted = await onAnswer?.(t('Additional information (for this conversation, not approval):', 'ข้อมูลเพิ่มเติมสำหรับบทสนทนานี้ ยังไม่ใช่การอนุมัติไฟล์:') + '\n' + questions.map((q, i) => `${i + 1}. ${q.question}\n${i === step ? value : answer(i) || unsure}`).join('\n')); if (accepted !== false) save({ answers: next, submitted: true }) }
  }
  return <section className="rounded-2xl border border-line bg-surface p-4 sm:p-5 space-y-4" aria-label={t('Clarify before answering', 'ขอข้อมูลก่อนตอบ')}>
    <div className="flex items-center justify-between gap-3"><span className="text-xs font-medium text-muted">{t('One more detail', 'ขอข้อมูลเพิ่มนิดหนึ่ง')}</span><span className="text-xs text-muted">{step + 1} / {questions.length}</span></div>
    <fieldset disabled={disabled} className="space-y-3"><legend className="font-semibold text-navy mb-2">{question.question}</legend><p className="text-sm text-muted leading-6">{question.reason}</p>
      <div className="divide-y divide-line">{question.options.map(option => <label key={option} className={`flex items-start gap-3 rounded-lg p-3 text-sm cursor-pointer hover:bg-surface-2 ${choice === option ? 'bg-surface-2' : ''}`}><input className="mt-1" type="radio" name={group + step} checked={choice === option} onChange={() => { const next = { ...answers, [step]: option }; save({ answers: next }) }} /><span>{option}</span></label>)}
        <label className="flex items-center gap-3 p-3 text-sm cursor-pointer"><input type="radio" name={group + step} checked={choice === '__custom'} onChange={() => { const next = { ...answers, [step]: '__custom' }; save({ answers: next }) }} />{t('Write my own answer', 'พิมพ์คำตอบเอง')}</label>
      </div>
      {choice === '__custom' && <textarea className="input w-full" rows={2} maxLength={200} aria-label={t('Additional details', 'รายละเอียดเพิ่มเติม')} placeholder={t('What can you confirm?', 'ระบุข้อมูลที่คุณยืนยันได้…')} value={custom[step] || ''} onChange={e => { const next = { ...custom, [step]: e.target.value }; save({ custom: next }) }} />}
    </fieldset>
    <div className="flex flex-wrap justify-between gap-2 border-t border-line pt-3"><button className="btn btn-ghost btn-sm" disabled={!step || disabled} onClick={() => { save({ step: step - 1 }) }}><ArrowLeft size={14} />{t('Back', 'ย้อนกลับ')}</button><div className="flex gap-2"><button className="btn btn-ghost btn-sm" disabled={disabled} onClick={() => advance(unsure)}>{t('Not sure / skip', 'ยังไม่ทราบ / ข้าม')}</button><button className="btn btn-primary btn-sm" disabled={!selected || disabled} onClick={() => advance(selected!)}>{step === questions.length - 1 ? t('Send answers', 'ส่งคำตอบ') : t('Next', 'ถัดไป')}<ArrowRight size={14} /></button></div></div>
    <p className="text-xs text-muted">{t('You can also ask another question in the chat below.', 'ถามเรื่องอื่นต่อในช่องแชตด้านล่างได้เช่นกัน')}</p>
  </section>
}
