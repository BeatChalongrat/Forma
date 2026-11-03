import { useState } from 'react'
import { ChevronDown, ChevronRight, Pencil, Route } from 'lucide-react'
import type { FieldSpec } from '../lib/api'
import { concretePattern, escapePointer, parseValue, type NumberStyle } from '../lib/valueInput'
import { usePreferences } from '../lib/preferences'

type Props = { value: unknown; fields: FieldSpec[]; pointer?: string; readOnly: boolean; numberStyle: NumberStyle; onEdit: (pointer: string, value: unknown) => void; onTrace: (pointer: string) => void; depth?: number }
export function JsonRecordEditor({ value, fields, pointer = '', readOnly, numberStyle, onEdit, onTrace, depth = 0 }: Props) {
  const { t, prefs } = usePreferences()
  const spec = fields.find(f => f.pointer === pointer) || fields.find(f => f.pointer === concretePattern(pointer))
  const protectedField = fields.some(f => f.system && (concretePattern(pointer) === f.pointer || concretePattern(pointer).startsWith(f.pointer + '/')))
  const [expanded, setExpanded] = useState(depth < 2)
  const [draft, setDraft] = useState<string | null>(null)
  const [error, setError] = useState('')
  const container = value !== null && typeof value === 'object'
  const title = (prefs.language === 'th' ? spec?.description_th : spec?.description) || spec?.description || spec?.name || pointer.split('/').at(-1)?.replaceAll('~1', '/').replaceAll('~0', '~') || 'JSON'
  const parsed = draft !== null && spec ? parseValue(spec, draft, numberStyle) : null
  const commit = () => {
    if (!parsed?.ok) { setError(parsed && !parsed.ok ? parsed.error : 'Unknown schema field'); return }
    onEdit(pointer, parsed.value); setDraft(null); setError('')
  }
  const children = Array.isArray(value) ? value.map((v, i) => [String(i), v] as const) : container ? Object.entries(value as Record<string, unknown>) : []
  if (container && !Array.isArray(value)) {
    const prefix = concretePattern(pointer) + '/'
    for (const f of fields) {
      if (!f.system && f.pointer.startsWith(prefix) && !f.pointer.slice(prefix.length).includes('/')) {
        const key = f.pointer.slice(prefix.length).replaceAll('~1', '/').replaceAll('~0', '~')
        if (!children.some(([k]) => k === key)) children.push([key, undefined])
      }
    }
  }
  return <div className="min-w-0 py-2">
    <div className="flex items-start gap-2 flex-wrap">
      {container && <button type="button" className="btn btn-ghost btn-sm" aria-label={t('Expand field', 'เปิดข้อมูลย่อย') + ' ' + title} aria-expanded={expanded} onClick={() => setExpanded(v => !v)}>{expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</button>}
      <div className="min-w-0 flex-1"><span className="font-medium break-words">{title}</span><small className="block text-muted break-all">{pointer || '/'} {spec && `· ${spec.json_type.join(' / ')}${spec.required ? ' · required' : ''}`}</small></div>
      {!container && draft === null && <span className="max-w-full break-all text-sm">{value === undefined ? t('Not set', 'ยังไม่มีค่า') : JSON.stringify(value)}</span>}
      {container && <span className="text-xs text-muted">{children.length} {t('items', 'รายการ')}</span>}
      {pointer && <button type="button" className="btn btn-ghost btn-sm" title={t('Trace this value', 'ดูที่มาของค่า')} onClick={() => onTrace(pointer)}><Route size={14} /></button>}
      {spec && !protectedField && !readOnly && <button type="button" className="btn btn-sm" onClick={() => { setDraft(value === undefined || value === null ? '' : typeof value === 'string' ? value : typeof value === 'number' && numberStyle === 'comma' ? String(value).replace('.', ',') : JSON.stringify(value, null, 2)); setError('') }}><Pencil size={13} />{container ? t('Edit JSON', 'แก้ JSON') : t('Edit', 'แก้ค่า')}</button>}
    </div>
    {draft !== null && spec && <div className="mt-3 space-y-2">
      {spec.enum || spec.json_type.includes('boolean') ? <select aria-label={title} className="input" value={draft} onChange={e => setDraft(e.target.value)}><option value="">{t('Choose a value', 'เลือกค่า')}</option>{(spec.enum || [true, false]).map(v => <option key={JSON.stringify(v)} value={typeof v === 'string' ? v : JSON.stringify(v)}>{JSON.stringify(v)}</option>)}</select>
        : container || spec.json_type.includes('object') || spec.json_type.includes('array') ? <textarea aria-label={title} className="input mono min-h-40" value={draft} onChange={e => setDraft(e.target.value)} />
        : <input aria-label={title} className="input" type={spec.format === 'date' ? 'date' : 'text'} value={draft} onChange={e => setDraft(e.target.value)} />}
      {parsed?.ok && <p className="text-xs text-muted break-all">{t('Value to stage', 'ค่าที่จะใส่ในฉบับร่าง')}: {JSON.stringify(parsed.value)}</p>}
      {error && <p role="alert" className="text-bad text-sm">{error}</p>}
      <div className="flex gap-2"><button type="button" className="btn btn-sm btn-primary" onClick={commit}>{t('Stage change', 'เพิ่มในฉบับร่าง')}</button><button type="button" className="btn btn-sm" onClick={() => setDraft(null)}>{t('Cancel', 'ยกเลิก')}</button>{spec.nullable && <button type="button" className="btn btn-sm" onClick={() => { onEdit(pointer, null); setDraft(null) }}>{t('Set null', 'ตั้งเป็น null')}</button>}</div>
      <p className="text-xs text-muted">{t('The server checks the complete target schema before saving a new version.', 'ระบบจะตรวจ schema ปลายทางทั้งหมดก่อนบันทึกเป็นเวอร์ชันใหม่')}</p>
    </div>}
    {container && expanded && depth < 20 && <div className="pl-4 mt-2 divide-y divide-line">{children.map(([key, v]) => <JsonRecordEditor key={key} value={v} fields={fields} pointer={`${pointer}/${escapePointer(key)}`} readOnly={readOnly || protectedField} numberStyle={numberStyle} onEdit={onEdit} onTrace={onTrace} depth={depth + 1} />)}</div>}
  </div>
}
