import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { usePreferences } from '../lib/preferences'

export function AssistantWork({ items }: { items: Awaited<ReturnType<typeof api.assistantWork>>["items"] }) {
  const { t } = usePreferences()
  if (!items.length) return null
  return <section aria-label={t('Assistant work', 'งานของผู้ช่วย')} className="text-xs px-2 py-2 space-y-1 border-t border-line">
    <p className="text-muted mb-2">{t('In progress and new replies', 'งานที่กำลังทำและคำตอบใหม่')}</p>
    {items.slice(0, 4).map(item => <Link key={item.chat_id} to={'/assistant?chat=' + encodeURIComponent(item.chat_id)} className="block rounded-lg p-2 hover:bg-surface-2"><span className="block truncate">{item.title}</span><span className={item.status === 'failed' ? 'text-warn' : 'text-muted'}>{item.status === 'running' ? t('Working…', 'กำลังทำ…') : item.status === 'queued' ? t('Waiting to start', 'รอเริ่มงาน') : item.status === 'needs_input' ? t('Waiting for your answer', 'รอคำตอบจากคุณ') : item.status === 'complete' ? t('New reply', 'มีคำตอบใหม่') : item.status === 'stopped' ? t('Stopped', 'หยุดแล้ว') : t('Needs attention', 'ต้องตรวจสอบ')}</span></Link>)}
  </section>
}
