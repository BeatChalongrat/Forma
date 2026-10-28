import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowRight, ArrowUpRight, BookOpen, CircleCheck, Download, FileWarning, ShieldCheck, Table2, Upload } from 'lucide-react'
import { api, type ImportRound } from '../lib/api'
import { fmtInt, fmtPct, timeAgo, cn } from '../lib/utils'
import { nextStep, ROUND_STEPS } from '../lib/steps'
import { PageHeader, Stat, Section, Empty, Badge } from '../components/ui'

type Todo = { key: string; title: string; th: string; icon: typeof Upload; tone: 'warn' | 'bad' | 'signal' | 'ok'; rounds: ImportRound[]; href: (r: ImportRound) => string; all: string; cta: string }

/** Home: what needs a person now, grouped by the step it is waiting at, then how the system is doing. */
export function Overview() {
  const nav = useNavigate()
  const { data: d } = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard, refetchInterval: 10000 })
  const { data: rounds } = useQuery({ queryKey: ['rounds', ''], queryFn: () => api.rounds(), refetchInterval: 10000 })
  const by = (s: string[]) => (rounds ?? []).filter((r) => s.includes(r.status))
  const studio = (r: ImportRound) => (r.summary.mapping_version_id ? `/rounds/${r.id}/mapping/${r.summary.mapping_version_id}` : `/rounds/${r.id}`)
  const todos: Todo[] = [
    { key: 'mapping', title: 'Check mappings', th: 'ไฟล์ใหม่หรือ format เปลี่ยน ระบบไม่กล้า approve เอง', icon: ShieldCheck, tone: 'warn', rounds: by(['review', 'rejected']), href: studio, all: '/review', cta: 'Open review' },
    { key: 'problem', title: 'Files with a problem', th: 'ไฟล์ผิดราย ไฟล์เสีย หรือขั้นตอนล้มเหลว', icon: FileWarning, tone: 'bad', rounds: by(['blocked', 'failed']), href: (r) => `/rounds/${r.id}`, all: '/imports', cta: 'See imports' },
    { key: 'data', title: 'Data to check & approve', th: 'สร้างข้อมูล ตรวจ แล้วกด Approve', icon: Table2, tone: 'signal', rounds: by(['transformed', 'approved', 'auto_approved']), href: (r) => (r.summary.dataset_version_id ? `/datasets/${r.summary.dataset_version_id}` : `/rounds/${r.id}`), all: '/imports', cta: 'See imports' },
    { key: 'export', title: 'Ready to export', th: 'อนุมัติแล้ว ดาวน์โหลด JSON ส่งต่อได้', icon: Download, tone: 'ok', rounds: by(['approved_data']), href: (r) => `/rounds/${r.id}`, all: '/imports', cta: 'See imports' },
  ]
  const waiting = todos.reduce((a, t) => a + t.rounds.length, 0)

  return (
    <>
      <PageHeader
        title="What needs you today"
        subtitle="Every delivery waiting for a person, grouped by what to do next."
        hint="งานที่รอคุณ เรียงตามขั้นตอน — กดที่รายการเพื่อไปทำต่อได้เลย"
        actions={<Link to="/imports/new" className="btn btn-primary btn-lg"><Upload size={15} /> New import</Link>}
      />

      <div className="grid sm:grid-cols-2 xl:grid-cols-4 gap-4 mb-6">
        {todos.map((t) => <TodoCard key={t.key} t={t} onOpen={(r) => nav(t.href(r))} />)}
      </div>
      {rounds && waiting === 0 && (
        <div className="card p-5 mb-6 flex items-center gap-3"><CircleCheck className="text-ok" /><div><div className="heading">All clear</div><div className="hint-th">ไม่มีงานค้าง ทุกรอบผ่านอัตโนมัติหรือส่งออกแล้ว</div></div></div>
      )}

      {/* how a delivery flows, once, for people new to the tool */}
      <div className="card p-5 mb-6">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div>
            <div className="heading">How every delivery flows</div>
            <div className="hint-th">ทุกไฟล์ที่ลูกค้าส่งมาผ่าน 5 ขั้นนี้ หน้า Import แต่ละรอบจะบอกว่าอยู่ขั้นไหนและต้องกดอะไรต่อ</div>
          </div>
          <Link to="/guide" className="btn btn-outline btn-sm"><BookOpen size={13} /> Where is each feature?</Link>
        </div>
        <ol className="grid grid-cols-1 md:grid-cols-5 gap-2 mt-4">
          {ROUND_STEPS.map((s) => (
            <li key={s.n} className="flex items-start gap-2.5 rounded-[10px] bg-surface-2 px-3 py-2.5">
              <span className="w-6 h-6 rounded-full text-white text-[12px] font-bold grid place-items-center shrink-0" style={{ backgroundImage: 'var(--gradient-brand)' }}>{s.n}</span>
              <span className="leading-tight"><span className="block font-semibold text-navy text-[12.5px]">{s.title}</span><span className="block text-[11.5px] text-muted mt-0.5">{s.th}</span></span>
            </li>
          ))}
        </ol>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
        <Stat label="Handled automatically" value={d?.automation_rate !== null && d?.automation_rate !== undefined ? fmtPct(d.automation_rate) : '–'} hint={`${fmtInt(d?.auto_approved_mappings)} of ${fmtInt(d?.mapping_versions)} mappings approved by policy`} />
        <Stat label="Rows published" value={fmtInt(d?.published_rows)} hint={`${fmtInt(d?.approved_mappings)} customers with an approved mapping`} />
        <Stat label="Customers" value={fmtInt(d?.customers)} hint={`${fmtInt(rounds?.length)} deliveries in total`} />
        <Stat label="AI spend" value={d ? `$${d.llm.cost_usd.toFixed(2)}` : '–'} hint={d ? (d.llm.provider === 'mock' ? 'AI off — rule-based mapping only' : `${fmtInt(d.llm.calls)} calls · ${d.llm.model}`) : ''} />
      </div>

      <Section title="Recent imports" hint="ไฟล์ล่าสุดที่ลูกค้าส่งมา และขั้นที่ต้องทำต่อ" actions={<Link to="/imports" className="btn btn-ghost btn-sm">All imports <ArrowUpRight size={13} /></Link>}>
        {d?.recent_rounds?.length ? (
          <table className="table">
            <thead><tr><th>Customer</th><th>Round</th><th>Next step</th><th className="text-right">Rows</th><th className="text-right">Updated</th></tr></thead>
            <tbody>
              {d.recent_rounds.map((r) => <tr key={r.id} className="row-link" onClick={() => nav(`/rounds/${r.id}`)}>
                <td><span className="font-semibold text-navy">{r.customer?.code}</span> <span className="text-muted">{r.customer?.name}</span></td>
                <td className="text-ink-2">{r.label}</td>
                <td><NextStepCell r={r} /></td>
                <td className="text-right tnum">{fmtInt(r.summary.rows)}</td>
                <td className="text-right text-muted">{timeAgo(r.updated_at)}</td>
              </tr>)}
            </tbody>
          </table>
        ) : (
          <div className="p-4"><Empty title="No imports yet" body="Upload a customer's ERP export to get started." action={<Link to="/imports/new" className="btn btn-primary">New import</Link>} /></div>
        )}
      </Section>
    </>
  )
}

const TONE: Record<Todo['tone'], string> = { warn: 'text-warn bg-warn-soft', bad: 'text-bad bg-bad-soft', signal: 'text-signal bg-signal-soft', ok: 'text-ok bg-ok-soft' }

function TodoCard({ t, onOpen }: { t: Todo; onOpen: (r: ImportRound) => void }) {
  const n = t.rounds.length
  return (
    <div className={cn('card p-4 flex flex-col', n > 0 && 'ring-1 ring-inset ring-brand-blue/20')}>
      <div className="flex items-start justify-between gap-2">
        <span className={cn('w-9 h-9 rounded-[10px] grid place-items-center', TONE[t.tone])}><t.icon size={18} /></span>
        <span className={cn('text-[30px] font-bold tnum leading-none', n ? 'text-navy' : 'text-ink-3')}>{n}</span>
      </div>
      <div className="heading mt-3">{t.title}</div>
      <div className="hint-th mt-0.5 min-h-[32px]">{t.th}</div>
      <ul className="mt-2 flex-1">
        {t.rounds.slice(0, 3).map((r) => (
          <li key={r.id}>
            <button onClick={() => onOpen(r)} className="w-full flex items-center gap-2 py-1.5 border-t border-line text-left text-[12.5px] hover:text-signal">
              <span className="font-semibold text-navy shrink-0">{r.customer?.code}</span>
              <span className="text-muted truncate flex-1">{r.label}</span>
              <ArrowRight size={13} className="shrink-0 text-ink-3" />
            </button>
          </li>
        ))}
        {n === 0 && <li className="py-1.5 border-t border-line text-[12.5px] text-ink-3">Nothing waiting</li>}
      </ul>
      {n > 3 && <Link to={t.all} className="text-[12.5px] font-semibold text-signal mt-2 hover:underline">+{n - 3} more · {t.cta}</Link>}
    </div>
  )
}

export function NextStepCell({ r }: { r: ImportRound }): ReactNode {
  const s = nextStep(r)
  const tone = s.tone === 'problem' ? 'bad' : s.tone === 'todo' ? 'warn' : s.tone === 'done' ? 'ok' : 'neutral'
  return (
    <span className="flex items-center gap-2">
      <Badge tone={tone} className="whitespace-nowrap shrink-0">{s.step <= 5 ? `Step ${s.step}/5` : 'Done'}</Badge>
      <span className="leading-tight"><span className="block text-[12.5px] font-semibold text-ink">{s.action ?? (s.tone === 'done' ? 'Exported' : 'Working…')}</span><span className="block text-[11.5px] text-muted">{s.th}</span></span>
    </span>
  )
}
