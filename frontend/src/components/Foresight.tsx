import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { CalendarClock, Eye } from 'lucide-react'
import { api } from '../lib/api'
import { usePreferences } from '../lib/preferences'
import { Badge, Section } from './ui'

/** What FORMA expects before a file arrives (ADR-039): who is late, who will probably need a check. */
export function Foresight() {
  const { prefs, t } = usePreferences()
  const lang = prefs.language
  const q = useQuery({ queryKey: ['foresight'], queryFn: api.foresight, refetchInterval: 60000 })
  const d = q.data
  if (!d || (!d.expected.length && !d.watchlist.length)) return null
  return (
    <Section title={t('Expected deliveries', 'ไฟล์ที่คาดว่าจะมา')} hint="คาดจากช่วงห่างของรอบก่อนๆ ของลูกค้าแต่ละราย ไม่ใช่สัญญา" className="mb-7" actions={<Link to="/customers" className="btn btn-ghost btn-sm">{t('All customers', 'ลูกค้าทั้งหมด')}</Link>}>
      {d.expected.length > 0 && <ul className="divide-y divide-line">{d.expected.slice(0, 8).map((x) => <li key={x.customer_id} className="flex flex-wrap items-center gap-3 px-5 py-3"><CalendarClock size={16} className={x.status === 'overdue' ? 'text-bad shrink-0' : 'text-warn shrink-0'} /><span className="flex-1 min-w-[200px] text-sm">{x.text[lang]}</span><Badge tone={x.status === 'overdue' ? 'bad' : 'warn'}>{x.status === 'overdue' ? t(`${x.late_days} days late`, `เลย ${x.late_days} วัน`) : t('due today', 'ครบกำหนดวันนี้')}</Badge><Link to={`/customers/${x.customer_id}`} className="btn btn-ghost btn-sm">{x.code}</Link></li>)}</ul>}
      {d.watchlist.length > 0 && <div className="px-5 py-3 border-t border-line"><div className="text-xs text-muted mb-2 inline-flex items-center gap-1"><Eye size={13} />{t('Likely to need a check next time', 'รอบหน้าน่าจะต้องตรวจ')}</div><ul className="space-y-1 text-sm">{d.watchlist.slice(0, 6).map((w) => <li key={w.customer_id}><Link to={`/rounds/${w.last_round_id}`} className="hover:underline">{w.text[lang]}</Link></li>)}</ul></div>}
    </Section>
  )
}
