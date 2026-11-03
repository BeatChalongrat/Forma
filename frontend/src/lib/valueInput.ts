import type { FieldSpec } from './api'

export type NumberStyle = 'plain' | 'dot' | 'comma'
export type ParsedValue = { ok: true; value: unknown } | { ok: false; error: string }

/** Explicit input grammar; never infer a locale, round integers or coerce unknown booleans. */
export function parseValue(f: Pick<FieldSpec, 'name' | 'json_type' | 'nullable' | 'enum' | 'format'>, text: string, style: NumberStyle = 'plain'): ParsedValue {
  const raw = text.trim()
  const fail = (message: string): ParsedValue => ({ ok: false, error: `${f.name}: ${message}` })
  if (!raw && f.nullable) return { ok: true, value: null }
  let value: unknown = text
  if (f.json_type.includes('number') || f.json_type.includes('integer')) {
    const grammar = style === 'comma' ? /^[+-]?(?:\d+|\d{1,3}(?:\.\d{3})+)(?:,\d+)?$/ : style === 'dot' ? /^[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?$/ : /^[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?$/
    if (!grammar.test(raw)) return fail('รูปแบบตัวเลขไม่ตรงกับที่เลือก / Check the selected number format')
    const normalized = style === 'comma' ? raw.replaceAll('.', '').replace(',', '.') : style === 'dot' ? raw.replaceAll(',', '') : raw
    const n = Number(normalized)
    if (!Number.isFinite(n)) return fail('ต้องเป็นตัวเลขที่มีค่าจำกัด / A finite number is required')
    if ((f.json_type.includes('integer') || Number.isInteger(n)) && !Number.isSafeInteger(n)) return fail('ต้องเป็นจำนวนเต็ม ไม่ปัดค่าให้อัตโนมัติ / Enter an exact safe integer')
    value = n
  } else if (f.json_type.includes('boolean')) {
    const token = raw.toLowerCase()
    if (['true', '1', 'yes', 'y', 'ใช่'].includes(token)) value = true
    else if (['false', '0', 'no', 'n', 'ไม่ใช่'].includes(token)) value = false
    else return fail('เลือก true หรือ false / Unknown boolean; choose true or false')
  } else if (f.json_type.includes('array') || f.json_type.includes('object')) {
    try { value = JSON.parse(raw) } catch { return fail('JSON ไม่ถูกต้อง / Invalid JSON') }
    if (f.json_type.includes('array') && !Array.isArray(value)) return fail('ต้องเป็น JSON array')
    if (!f.json_type.includes('array') && (typeof value !== 'object' || value === null || Array.isArray(value))) return fail('ต้องเป็น JSON object')
  }
  if (f.format === 'date' && typeof value === 'string') {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || Number.isNaN(Date.parse(value)) || new Date(value).toISOString().slice(0, 10) !== value) return fail('ใช้วันที่จริงรูปแบบ YYYY-MM-DD / Enter a valid ISO date')
  }
  if (f.enum && !f.enum.some(v => JSON.stringify(v) === JSON.stringify(value))) return fail(`ค่าที่เลือกได้ / Allowed: ${f.enum.map(v => JSON.stringify(v)).join(', ')}`)
  return { ok: true, value }
}

export const escapePointer = (key: string) => key.replaceAll('~', '~0').replaceAll('/', '~1')
export function concretePattern(pointer: string): string { return pointer.replace(/\/\d+(?=\/|$)/g, '/*') }
