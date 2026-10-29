import { Check } from 'lucide-react'
import { usePreferences } from '../lib/preferences'
import { cn } from '../lib/utils'

export function workflowStep(status?: string) {
  if (status === 'published' || status === 'approved_data') return 3
  if (!status || ['uploaded', 'profiled', 'failed'].includes(status)) return 1
  return 2
}

export function Workflow({ status }: { status?: string }) {
  const { t } = usePreferences()
  const current = workflowStep(status)
  const steps = [
    [t('Upload files', 'อัปโหลดไฟล์'), t('We read the files and match their columns.', 'ระบบอ่านไฟล์และจับคู่คอลัมน์ให้')],
    [t('Answer & check', 'ตอบคำถามและตรวจปัญหา'), t('Only review what needs your decision.', 'ตรวจเฉพาะจุดที่ต้องให้คุณตัดสินใจ')],
    [t('Download', 'ดาวน์โหลด'), t('Get validated files ready to use.', 'รับไฟล์ที่ตรวจแล้วพร้อมนำไปใช้')],
  ]
  return <ol className="card p-3 grid sm:grid-cols-3 gap-2 mb-5" aria-label={t('Import progress', 'ขั้นตอนนำเข้า')}>
    {steps.map(([title, detail], i) => {
      const done = current > i + 1 || status === 'published'
      return <li key={i} aria-current={current === i + 1 ? 'step' : undefined} className={cn('flex gap-3 px-3 py-3 rounded-lg', current === i + 1 && 'bg-signal-soft')}>
        <span className={cn('rounded-full w-7 h-7 shrink-0 grid place-items-center text-sm font-semibold', done ? 'step-done' : current === i + 1 ? 'step-number' : 'bg-surface-2 text-muted border border-line')}>{done ? <Check size={15} /> : i + 1}</span>
        <span className="min-w-0"><span className="block font-semibold text-navy">{title}</span><span className="block mt-1 text-xs text-muted leading-5">{detail}</span></span>
      </li>
    })}
  </ol>
}
