import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, Check } from 'lucide-react'
import type { Verdict, VerdictBrief, VerdictDecision } from '../lib/api'
import { usePreferences } from '../lib/preferences'
import { cn } from '../lib/utils'
import { Badge, type Tone } from './ui'

/** The one-line conclusion a row shows (ADR-036). */
export function VerdictLine({ v, className }: { v: VerdictBrief | null | undefined; className?: string }) {
  const { prefs } = usePreferences()
  if (!v) return null
  return <span className={cn('inline-flex items-center gap-1.5 min-w-0', className)}><ConfidenceDot word={v.word} /><span className="truncate">{v.conclusion[prefs.language]}</span></span>
}

export function ConfidenceDot({ word }: { word: VerdictBrief['word'] }) {
  const { t } = usePreferences()
  const cls = word === 'sure' ? 'bg-ok' : word === 'likely' ? 'bg-warn' : 'bg-ink-3'
  const label = word === 'sure' ? t('sure', 'มั่นใจสูง') : word === 'likely' ? t('likely', 'ค่อนข้างมั่นใจ') : t('unsure', 'ไม่แน่ใจ')
  return <span className={cn('inline-block w-2 h-2 rounded-full shrink-0', cls)} title={label} aria-label={label} />
}

const OUTCOME: Record<Verdict['outcome'], { en: string; th: string; tone: Tone }> = {
  accept: { en: 'Accept', th: 'ยอมรับ', tone: 'ok' },
  answer: { en: 'Answer', th: 'ตอบคำถาม', tone: 'warn' },
  assign_customer: { en: 'Move to the right customer', th: 'ย้ายไปลูกค้าที่ถูกต้อง', tone: 'bad' },
  fix_mapping: { en: 'Fix the field matching', th: 'แก้การจับคู่', tone: 'warn' },
  check_data: { en: 'Check the data', th: 'ตรวจข้อมูล', tone: 'warn' },
  fix_data: { en: 'Fix the records', th: 'แก้รายการที่ผิด', tone: 'bad' },
  reject_file: { en: 'Reject the file', th: 'ปฏิเสธไฟล์', tone: 'bad' },
  wait: { en: 'Processing', th: 'กำลังประมวลผล', tone: 'neutral' },
}

type Props = {
  v: Verdict
  /** can act on the verdict (reviewer and the version is still open) */
  editable: boolean
  busy?: boolean
  /** accept with the chosen decisions (only for outcomes that are one click: accept / answer / check_data) */
  onAccept?: (decisions: Record<string, boolean | number | string | null>) => void
  /** open a decision in the surrounding form (the review page's question) */
  onOpenDecision?: (id: string) => void
  /** open the move dialog for a `assign_customer` verdict (ADR-052) */
  onMove?: () => void
  compact?: boolean
  error?: string | null
}

/** The verdict card: conclusion, confidence, what only a person decides, the evidence, and what was folded away. */
export function VerdictCard({ v, editable, busy, onAccept, onOpenDecision, onMove, compact, error }: Props) {
  const { prefs, t } = usePreferences()
  const lang = prefs.language
  const [decisions, setDecisions] = useState<Record<string, boolean | number | string | null>>({})
  const o = OUTCOME[v.outcome]
  const oneClick = ['accept', 'answer', 'check_data'].includes(v.outcome)
  const needed = v.must_decide.filter((d) => d.kind !== 'confirm' || v.outcome === 'answer')
  const answered = needed.filter((d) => decisions[d.id] !== undefined && decisions[d.id] !== null)
  const canAct = editable && !busy && oneClick && (v.outcome === 'accept' ? v.must_decide.length === 0 : answered.length === needed.length && needed.length > 0)
  const firstLink = v.evidence.find((e) => e.link)?.link
  const confidenceWord = v.word === 'sure' ? t('sure', 'มั่นใจสูง') : v.word === 'likely' ? t('likely', 'ค่อนข้างมั่นใจ') : t('unsure', 'ไม่แน่ใจ')
  return (
    <section className={cn('card shadow-none', compact ? 'p-4' : 'p-5 sm:p-6')} aria-label={t('Verdict', 'คำตัดสิน')}>
      <div className="flex flex-wrap items-start gap-3">
        <div className="flex-1 min-w-0 basis-60">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted"><span>{t('Review summary', 'สรุปการตรวจ')}</span><Badge tone={o.tone}>{o[lang]}</Badge><span>{confidenceWord}</span></div>
          <p className={cn('mt-3 font-medium text-navy leading-relaxed break-words', compact ? 'text-base' : 'text-lg')}>{v.conclusion[lang]}</p>
          {v.narrative && <p className="mt-2 text-sm text-ink-2">{v.narrative.text}</p>}
        </div>
        {!compact && (oneClick ? (
          <button className="btn btn-primary" disabled={!canAct} onClick={() => onAccept?.(decisions)}>
            {busy ? t('Working…', 'กำลังทำ…') : v.outcome === 'accept' ? <><Check size={15} />{t('Accept and continue', 'ยอมรับและทำต่อ')}</> : <>{t('Save my answers', 'บันทึกคำตอบ')}<ArrowRight size={15} /></>}
          </button>
        ) : v.outcome === 'assign_customer' && onMove ? (
          <button className="btn btn-primary" disabled={!editable || busy} onClick={onMove}>{o[lang]}<ArrowRight size={15} /></button>
        ) : firstLink ? (
          <Link className="btn btn-primary" to={firstLink}>{o[lang]}<ArrowRight size={15} /></Link>
        ) : null)}
      </div>

      {!compact && v.must_decide.length > 0 && (
        <div className="mt-6 divide-y divide-line">
          <h3 className="text-sm font-medium pb-3">{t('Questions to confirm', 'คำถามที่ต้องยืนยัน')} <span className="text-muted ml-1">({v.must_decide.length})</span></h3>
          {v.must_decide.map((d, i) => <Decision key={d.id} n={i + 1} d={d} value={decisions[d.id]} editable={editable && !busy} onChange={(val) => setDecisions((cur) => ({ ...cur, [d.id]: val }))} onOpen={onOpenDecision} />)}
        </div>
      )}

      {v.evidence.length > 0 && (
        <div className="mt-2 border-t border-line pt-2">
          <p className="py-3 m-0 text-sm text-ink-2">{t('Supporting evidence', 'หลักฐานประกอบ')} <span className="text-muted ml-1">({v.evidence.length})</span></p>
          <ul className="mt-2 space-y-1.5 text-sm">{v.evidence.map((e) => <li key={e.id} className="flex gap-2"><span className="text-muted tnum shrink-0">{e.id}.</span>{e.link ? <Link className="text-signal hover:underline break-words" to={e.link}>{e.text[lang]}</Link> : <span className="break-words">{e.text[lang]}</span>}</li>)}</ul>
        </div>
      )}

      {v.suppressed.count > 0 && (
        <p className="mt-3 text-xs text-muted">
          {t('Folded away', 'พับเก็บไว้')}: {v.suppressed.groups.map((g) => g.text[lang]).join(' · ')}
        </p>
      )}
      {error && <p role="alert" className="mt-3 text-sm text-bad">{error}</p>}
    </section>
  )
}

function Decision({ n, d, value, editable, onChange, onOpen }: { n: number; d: VerdictDecision; value: boolean | number | string | null | undefined; editable: boolean; onChange: (v: boolean | number | string | null) => void; onOpen?: (id: string) => void }) {
  const { prefs, t } = usePreferences()
  const lang = prefs.language
  const isVat = d.semantic === 'price.vat_included'
  const yes = isVat ? t('VAT included', 'รวม VAT แล้ว') : t('Yes', 'ใช่')
  const no = isVat ? t('VAT excluded', 'ยังไม่รวม VAT') : t('No', 'ไม่ใช่')
  return (
    <div className="py-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0"><p className="font-medium"><span className="text-muted mr-1">{n}.</span>{d.question[lang]}</p><p className="text-xs text-muted mt-0.5">{d.why[lang]}{d.default !== null && d.default !== undefined && d.default_reason ? ` · ${t('Default', 'ค่าตั้งต้น')}: ${String(d.default === true ? yes : d.default === false ? no : d.default)} (${d.default_reason[lang]})` : ''}</p></div>
        {onOpen && <button className="btn btn-ghost btn-sm" onClick={() => onOpen(d.id)}>{t('Look at the evidence', 'ดูหลักฐาน')}<ArrowRight size={13} /></button>}
      </div>
      <fieldset disabled={!editable} className="mt-2 flex flex-wrap gap-2">
        {d.kind === 'boolean' && [true, false].map((opt) => <button key={String(opt)} className={cn('btn btn-sm', value === opt && 'btn-primary')} aria-pressed={value === opt} onClick={() => onChange(opt)}>{opt ? yes : no}</button>)}
        {(d.kind === 'choice' || d.kind === 'enum') && d.options.map((opt) => <button key={String(opt)} className={cn('btn btn-sm', value === opt && 'btn-primary')} aria-pressed={value === opt} onClick={() => onChange(String(opt))}>{d.option_labels?.[String(opt)] || String(opt)}</button>)}
        {(d.kind === 'text' || d.allow_custom) && <input className="input" maxLength={5000} aria-label={d.question[lang]} value={value === undefined || value === null ? '' : String(value)} onChange={e => onChange(e.target.value || null)} />}
        {d.kind === 'number' && <input className="input max-w-40" type="number" aria-label={d.question[lang]} value={value === undefined || value === null ? '' : String(value)} onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))} />}
        {d.kind === 'confirm' && <button className={cn('btn btn-sm', value === 'confirm' && 'btn-primary')} aria-pressed={value === 'confirm'} onClick={() => onChange('confirm')}><Check size={14} />{t('Correct as read', 'อ่านถูกต้องแล้ว')}</button>}
        <button className={cn('btn btn-ghost btn-sm', value === 'unknown' && 'bg-surface-2')} aria-pressed={value === 'unknown'} onClick={() => onChange('unknown')}>{t('Not sure', 'ยังไม่ทราบ')}</button>
      </fieldset>
      {d.scope === 'constant_for_this_file' && <p className="text-xs text-muted mt-2">{t('Applies to every record in this file. Use the field editor if values differ by row.', 'ใช้กับทุกรายการในไฟล์นี้ ถ้าค่าต่างกันแต่ละแถว ให้แก้การจับคู่คอลัมน์')}</p>}
    </div>
  )
}
