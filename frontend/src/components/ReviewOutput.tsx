import { useState } from 'react'
import type { FieldSpec } from '../lib/api'
import { usePreferences } from '../lib/preferences'
import { fieldName } from '../lib/reviewPresentation'

function values(record: unknown, path: string[]): unknown[] {
  if (!path.length) return [record]
  if (path[0] === '*') return Array.isArray(record) ? record.flatMap(v => values(v, path.slice(1))) : []
  return record && typeof record === 'object' ? values((record as Record<string, unknown>)[path[0].replace(/~1/g, '/').replace(/~0/g, '~')], path.slice(1)) : []
}

/** A schema-driven sample, including nested collections. No product-specific column assumptions. */
export function ReviewOutput({ records, fields, total }: { records: Record<string, unknown>[]; fields: FieldSpec[]; total: number }) {
  const { t, prefs } = usePreferences()
  const [json, setJson] = useState(false)
  const columns = fields.filter(f => !f.system && !f.is_collection && records.some(r => values(r, f.pointer.split('/').slice(1)).some(v => v !== undefined)))
  return <div className="space-y-3">
    <div className="flex justify-between gap-3 items-center flex-wrap"><h3 className="font-semibold">{t('Result sample', 'ตัวอย่างข้อมูลที่จะได้รับ')}</h3><div className="flex gap-1" aria-label={t('Preview format', 'รูปแบบตัวอย่าง')}><button className={`btn btn-sm ${!json ? 'bg-surface-2' : 'btn-ghost'}`} aria-pressed={!json} onClick={() => setJson(false)}>{t('Table', 'ตาราง')}</button><button className={`btn btn-sm ${json ? 'bg-surface-2' : 'btn-ghost'}`} aria-pressed={json} onClick={() => setJson(true)}>JSON</button></div></div>
    <p className="text-xs text-muted">{t(`Showing ${records.length} of ${total} records. Scroll sideways to see all fields.`, `แสดง ${records.length} จาก ${total} รายการ เลื่อนแนวนอนเพื่อดูข้อมูลทุกช่อง`)}</p>
    {json ? <pre className="text-xs bg-surface-2 rounded-xl p-4 max-h-96 overflow-auto" tabIndex={0}>{JSON.stringify(records, null, 2)}</pre> : <div className="overflow-auto max-h-96 rounded-xl border border-line" tabIndex={0}><table className="table text-sm"><thead><tr>{columns.map(f => <th className="whitespace-nowrap" key={f.pointer}>{fieldName(f, prefs.language === 'th')}</th>)}</tr></thead><tbody>{records.map((r, i) => <tr key={i}>{columns.map(f => <td key={f.pointer} className="min-w-32 max-w-80 whitespace-normal break-words">{values(r, f.pointer.split('/').slice(1)).map(v => v == null ? '—' : typeof v === 'boolean' ? v ? t('Yes', 'ใช่') : t('No', 'ไม่ใช่') : typeof v === 'object' ? JSON.stringify(v) : String(v)).join(' · ') || '—'}</td>)}</tr>)}</tbody></table></div>}
  </div>
}
