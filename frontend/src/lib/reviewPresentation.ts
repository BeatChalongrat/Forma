import type { Expr, FieldSpec, ReviewQuestions } from './api'
export type T = (en: string, th: string) => string
export type Question = ReviewQuestions['questions'][number]
export const text = (v: unknown): string => v === null || v === undefined ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v)
const semanticLabels: Record<string, string> = {
  sku: 'รหัสสินค้า', product_name: 'ชื่อสินค้า', description: 'รายละเอียดสินค้า', barcode: 'บาร์โค้ด', brand: 'ยี่ห้อ', category_path: 'หมวดสินค้า', currency: 'สกุลเงิน', status: 'สถานะสินค้า',
  'unit.sell': 'หน่วยขาย', 'unit.pack_size': 'จำนวนชิ้นต่อแพ็ก', 'price.amount': 'ราคาขาย', 'price.list_name': 'รายการราคา', 'price.vat_included': 'การรวม VAT', 'price.vat_rate': 'อัตรา VAT', 'price.effective_from': 'วันที่เริ่มใช้ราคา', 'price.effective_to': 'วันที่สิ้นสุดราคา',
  'stock.qty': 'จำนวนสต็อก', 'stock.warehouse': 'คลังสินค้า', 'stock.reserved': 'จำนวนที่จองไว้', 'promo.code': 'รหัสโปรโมชัน', 'promo.name': 'ชื่อโปรโมชัน', 'promo.type': 'ประเภทโปรโมชัน', 'promo.start_date': 'วันเริ่มโปรโมชัน', 'promo.end_date': 'วันสิ้นสุดโปรโมชัน', 'promo.price': 'ราคาโปรโมชัน', 'discount.percent': 'ส่วนลดเปอร์เซ็นต์', 'discount.amount': 'ส่วนลดจำนวนเงิน',
}
export const fieldName = (f: FieldSpec, th: boolean) => (th ? f.description_th || semanticLabels[f.semantic || ''] : f.description) || f.description || f.name
export const editor = (q: ReviewQuestions, field?: string, tab = '') => `/rounds/${q.round_id}/mapping/${q.mapping_id}?${tab ? `tab=${tab}&` : ''}${field ? `field=${encodeURIComponent(field)}` : ''}`
export function questionTitle(item: Question, t: T, th: boolean) {
  if (th && item.question_th) return item.question_th
  if (item.question) return item.question
  if (item.kind === 'choice') return t(`Which column holds ${fieldName(item.field, false)}?`, `คอลัมน์ไหนคือ${fieldName(item.field, true)}?`)
  if (item.field.semantic === 'price.vat_included') return t('Does this price include VAT?', 'ราคาในไฟล์รวม VAT แล้วหรือยัง?')
  if (item.field.semantic === 'unit.pack_size') return t('How many units are in one pack?', 'หนึ่งแพ็กมีสินค้ากี่ชิ้น?')
  return fieldName(item.field, th)
}
export function valueLabel(value: unknown, semantic: string | null | undefined, t: T): string {
  if (typeof value !== 'boolean') return text(value)
  if (semantic === 'price.vat_included') return value ? t('VAT included', 'รวม VAT แล้ว') : t('VAT excluded', 'ยังไม่รวม VAT')
  return value ? t('Yes', 'ใช่') : t('No', 'ไม่ใช่')
}
export function rule(expr: Expr, t: T): string {
  if (expr.op === 'const') return t(`Use a fixed value: ${text(expr.value)}`, `ใช้ค่าที่กำหนดไว้: ${text(expr.value)}`)
  if (expr.op === 'col') return t(`Read “${expr.name}”`, `อ่านคอลัมน์ “${expr.name}”`)
  const names: Record<string, string> = { pack_size: t('Read units per pack', 'แปลงข้อความเป็นจำนวนชิ้นต่อแพ็ก'), parse_number_th: t('Read as a number', 'แปลงข้อความเป็นตัวเลข'), parse_bool: t('Read yes / no', 'แปลงเป็นใช่ / ไม่ใช่'), default: t('Use a default if empty', 'ใช้ค่าสำรองเมื่อช่องว่าง'), coalesce: t('Use the first available value', 'ใช้ค่าแรกที่มีข้อมูล'), unit_normalize: t('Standardize the unit', 'ปรับชื่อหน่วยให้เป็นมาตรฐาน'), trim: t('Remove outer spaces', 'ตัดช่องว่างหน้าและหลัง'), cast: t('Convert the value type', 'แปลงชนิดข้อมูล') }
  return `${names[expr.op] || t('Transform source values', 'แปลงค่าจากต้นทาง')}${expr.args?.length ? ` ← ${expr.args.map(a => rule(a, t)).join(' · ')}` : ''}`
}
