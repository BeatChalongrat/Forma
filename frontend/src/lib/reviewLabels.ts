import type { ReviewQuestions } from './api'

type Question = ReviewQuestions['questions'][number]
type Translate = (en: string, th: string) => string
const units: Record<string, string> = { BOTTLE: 'ขวด', PCS: 'ชิ้น', PACK: 'แพ็ก', BOX: 'กล่อง', CARTON: 'ลัง', KG: 'กิโลกรัม', L: 'ลิตร' }

export function answerLabel(q: Question, value: unknown, t: Translate): string {
  if (value === null || value === undefined) return t('Not answered', 'ยังไม่ตอบ')
  if (value === 'confirm') return t('Source and result checked', 'ตรวจต้นทางและผลแล้ว ถูกต้อง')
  if (typeof value === 'boolean') return q.field.semantic === 'price.vat_included'
    ? value ? t('VAT included', 'รวม VAT แล้ว') : t('VAT excluded', 'ยังไม่รวม VAT')
    : value ? t('Yes', 'ใช่') : t('No', 'ไม่ใช่')
  const text = String(value)
  if (q.option_labels?.[text]) return q.option_labels[text]
  if (q.field.semantic === 'unit.sell' && units[text]) return t(text, `${units[text]} (${text})`)
  return text
}

export function answerScope(q: Question, remember: boolean, t: Translate): string {
  if (q.scope === 'record_in_this_delivery' || q.scope === 'records_in_this_delivery') return t('Only the listed records in this upload; not reused for later files', 'เฉพาะรายการที่ระบุในไฟล์ครั้งนี้ ไม่ใช้กับไฟล์ครั้งถัดไป')
  const scope = q.scope === 'source_value' ? t(`Only source code “${q.source_value}”`, `เฉพาะรหัส “${q.source_value}”`)
    : q.scope === 'source_column' || q.kind === 'choice' ? t('The source column', 'คอลัมน์ต้นทางนี้')
    : q.kind === 'confirm' ? t('Confirm this reading', 'ยืนยันการอ่านค่านี้')
    : t('Every matching row in this file', 'ทุกแถวที่จับคู่ในไฟล์นี้')
  return `${scope} · ${remember ? t('Reuse after approval only when customer, format and meaning checks match', 'จำหลังอนุมัติ ใช้ซ้ำเมื่อลูกค้า รูปแบบ และเงื่อนไขความหมายตรงกัน') : t('No new reusable rule', 'ไม่สร้างกฎใหม่ไว้ใช้ข้ามรอบ')}`
}
