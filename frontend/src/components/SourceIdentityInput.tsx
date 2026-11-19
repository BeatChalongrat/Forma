import { usePreferences } from '../lib/preferences'

export function SourceIdentityInput({ columns, examples, answer, onChange }: { columns: string[]; examples?: { column: string; values: string[] }[]; answer: unknown; onChange: (value: string | undefined) => void }) {
  const { t } = usePreferences()
  let selected: string[] = []
  try { const parsed: unknown = JSON.parse(typeof answer === 'string' ? answer : '[]'); if (Array.isArray(parsed)) selected = parsed.filter((v): v is string => typeof v === 'string' && columns.includes(v)) } catch { /* An unfinished draft is not a key. */ }
  return <fieldset className="space-y-3">
    <legend className="text-sm">{t('Which columns together identify one lot or source record?', 'คอลัมน์ใดรวมกันแล้วใช้แยกหนึ่งล็อตหรือหนึ่งรายการต้นทาง?')}</legend>
    <p className="text-sm text-muted">{t('Include location or status if they identify separate records. Do not select quantity just to hide a conflict. Leave unanswered if unsure.', 'หากแยกตามตำแหน่งหรือสถานะ ให้เลือกด้วย อย่าเลือกยอดจำนวนเพียงเพื่อทำให้คีย์ไม่ซ้ำ หากไม่ทราบให้เว้นไว้')}</p>
    <div className="grid gap-3">{columns.map(column => <label key={column} className="flex items-center gap-2"><input type="checkbox" checked={selected.includes(column)} onChange={e => { const next = e.target.checked ? [...selected, column] : selected.filter(c => c !== column); onChange(next.length ? JSON.stringify(next) : undefined) }} /><span>{column}<small className="block text-muted">{examples?.find(e => e.column === column)?.values.join(' · ')}</small></span></label>)}</div>
  </fieldset>
}
