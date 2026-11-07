import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowRight, FileText } from 'lucide-react'
import { api } from '../lib/api'
import { usePreferences } from '../lib/preferences'
import { Modal } from './ui'

export function ReportTemplates({ onSelect }: { onSelect: (prompt: string) => void }) {
  const [open, setOpen] = useState(false)
  const [formats, setFormats] = useState(['docx', 'pptx', 'pdf'])
  const { t } = usePreferences()
  const catalog = useQuery({ queryKey: ['report-templates'], queryFn: api.reportTemplates, enabled: open, staleTime: 60000 })
  const descriptions: Record<string, string> = {
    executive: t('A concise management overview with measured results and sources.', 'สรุปภาพรวมให้หัวหน้า พร้อมตัวเลขและแหล่งข้อมูล'),
    monthly: t('A dated report with charts, full tables and scope.', 'สรุปตามช่วงเวลา พร้อมกราฟ ตาราง และขอบเขตข้อมูล'),
    presentation: t('Editable slides with charts and source references.', 'สไลด์แก้ไขต่อได้ พร้อมกราฟและข้อมูลอ้างอิง'),
    incident: t('Document observed problems; confirm causes before concluding.', 'รายงานปัญหาที่พบและหลักฐาน โดยไม่เดาสาเหตุ'),
    process: t('A process diagram with steps and decision points.', 'แผนผังขั้นตอน พร้อมเงื่อนไขและจุดตัดสินใจ'),
  }
  return <>
    <button className="btn btn-ghost btn-sm" onClick={() => setOpen(true)}><FileText size={16} /><span>{t('Report templates', 'แม่แบบรายงาน')}</span></button>
    <Modal open={open} onClose={() => setOpen(false)} title={t('Choose a formal report', 'เลือกแม่แบบรายงานทางการ')}>
      <p className="text-sm text-muted mb-4">{t('Choose a template, then describe the data you need in chat. No report starts until you send the message.', 'เลือกแม่แบบแล้วระบุข้อมูลที่ต้องการในแชต ระบบจะเริ่มเมื่อคุณส่งข้อความ')}</p>
      <fieldset className="flex flex-wrap gap-4 mb-5"><legend className="mb-2 text-sm">{t('File formats', 'รูปแบบไฟล์')}</legend>{['docx', 'pptx', 'pdf'].map(fmt => <label className="flex items-center gap-2" key={fmt}><input type="checkbox" checked={formats.includes(fmt)} onChange={e => setFormats(old => e.target.checked ? [...old, fmt] : old.filter(f => f !== fmt))} />{fmt.toUpperCase()}</label>)}</fieldset>
      <div className="space-y-2">{catalog.data?.templates.map(template => <button key={template.id} disabled={!formats.length} className="w-full rounded-xl border border-line p-4 text-left hover:bg-surface-2 disabled:opacity-50 flex items-center gap-4" onClick={() => { onSelect(t(`Use the ${template.id} report template and create ${formats.join(', ')}. Ask me which period, customer and data to include before creating it.`, `ใช้แม่แบบ${template.name} สร้างไฟล์ ${formats.join(', ')} ช่วยถามช่วงเวลา ลูกค้า และข้อมูลที่ต้องการก่อนเริ่มทำรายงาน`)); setOpen(false) }}><span className="flex-1"><strong>{t(template.id.replace(/-/g, ' '), template.name)}</strong><span className="block text-sm text-muted mt-1">{descriptions[template.id]}</span></span><ArrowRight size={16} /></button>)}</div>
      {catalog.isLoading && <p>{t('Loading templates…', 'กำลังโหลดแม่แบบ…')}</p>}
      {catalog.isError && <p role="alert">{t('Templates could not be loaded. Please try again.', 'โหลดแม่แบบไม่ได้ กรุณาลองอีกครั้ง')}</p>}
    </Modal>
  </>
}
