import { useId, useState } from 'react'
import { ArrowLeft, ArrowRight } from 'lucide-react'
import { type Clarification } from '../lib/api'
import { usePreferences } from '../lib/preferences'

export function ClarificationCard({ questions, onAnswer, disabled }: { questions: Clarification[]; onAnswer?: (text: string) => void; disabled?: boolean }) {
  const { t } = usePreferences(), group = useId()
  const [step, setStep] = useState(0), [answers, setAnswers] = useState<Record<number, string>>({})
  const [custom, setCustom] = useState<Record<number, string>>({})
  const question = questions[step], choice = answers[step] || ''
  const unsure = t('I do not know yet; leave this unresolved', 'ยังไม่ทราบ ขอเว้นเรื่องนี้ไว้ก่อน')
  const selected = choice === '__custom' ? custom[step]?.trim() : choice
  const answer = (index: number) => answers[index] === '__custom' ? custom[index]?.trim() : answers[index]
  if (!onAnswer) return <details className="rounded-2xl border border-line p-4 text-sm text-muted"><summary>{t('Clarification requested', 'ข้อมูลที่ผู้ช่วยขอเพิ่มเติม')} ({questions.length})</summary><ul className="mt-3 space-y-2">{questions.map(q => <li key={q.question}>{q.question}</li>)}</ul></details>
  function advance(value: string) {
    const next = { ...answers, [step]: value }
    setAnswers(next)
    if (step < questions.length - 1) setStep(step + 1)
    else onAnswer?.(t('Additional information (for this conversation, not approval):', 'ข้อมูลเพิ่มเติมสำหรับบทสนทนานี้ ยังไม่ใช่การอนุมัติไฟล์:') + '\n' + questions.map((q, i) => `${i + 1}. ${q.question}\n${i === step ? value : answer(i) || unsure}`).join('\n'))
  }
  return <section className="rounded-2xl border border-line bg-surface p-4 sm:p-5 space-y-4" aria-label={t('Clarify before answering', 'ขอข้อมูลก่อนตอบ')}>
    <div className="flex items-center justify-between gap-3"><span className="text-xs font-medium text-muted">{t('One more detail', 'ขอข้อมูลเพิ่มนิดหนึ่ง')}</span><span className="text-xs text-muted">{step + 1} / {questions.length}</span></div>
    <fieldset disabled={disabled} className="space-y-3"><legend className="font-semibold text-navy mb-2">{question.question}</legend><p className="text-sm text-muted leading-6">{question.reason}</p>
      <div className="divide-y divide-line">{question.options.map(option => <label key={option} className={`flex items-start gap-3 rounded-lg p-3 text-sm cursor-pointer hover:bg-surface-2 ${choice === option ? 'bg-surface-2' : ''}`}><input className="mt-1" type="radio" name={group + step} checked={choice === option} onChange={() => setAnswers(a => ({ ...a, [step]: option }))} /><span>{option}</span></label>)}
        <label className="flex items-center gap-3 p-3 text-sm cursor-pointer"><input type="radio" name={group + step} checked={choice === '__custom'} onChange={() => setAnswers(a => ({ ...a, [step]: '__custom' }))} />{t('Write my own answer', 'พิมพ์คำตอบเอง')}</label>
      </div>
      {choice === '__custom' && <textarea className="input w-full" rows={2} maxLength={200} aria-label={t('Additional details', 'รายละเอียดเพิ่มเติม')} placeholder={t('What can you confirm?', 'ระบุข้อมูลที่คุณยืนยันได้…')} value={custom[step] || ''} onChange={e => setCustom(a => ({ ...a, [step]: e.target.value }))} />}
    </fieldset>
    <div className="flex flex-wrap justify-between gap-2 border-t border-line pt-3"><button className="btn btn-ghost btn-sm" disabled={!step || disabled} onClick={() => setStep(step - 1)}><ArrowLeft size={14} />{t('Back', 'ย้อนกลับ')}</button><div className="flex gap-2"><button className="btn btn-ghost btn-sm" disabled={disabled} onClick={() => advance(unsure)}>{t('Not sure / skip', 'ยังไม่ทราบ / ข้าม')}</button><button className="btn btn-primary btn-sm" disabled={!selected || disabled} onClick={() => advance(selected!)}>{step === questions.length - 1 ? t('Send answers', 'ส่งคำตอบ') : t('Next', 'ถัดไป')}<ArrowRight size={14} /></button></div></div>
    <p className="text-xs text-muted">{t('You can also ask another question in the chat below.', 'ถามเรื่องอื่นต่อในช่องแชตด้านล่างได้เช่นกัน')}</p>
  </section>
}
