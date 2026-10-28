import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowUpRight } from 'lucide-react'
import { api } from '../lib/api'
import { fmtInt, fmtPct, timeAgo } from '../lib/utils'
import { PageHeader, Stat, StatusBadge, Section, Empty, Badge } from '../components/ui'

export function Overview() {
  const { data: d } = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard, refetchInterval: 10000 })
  const { data: queue } = useQuery({ queryKey: ['review', 'open'], queryFn: () => api.review('open'), refetchInterval: 10000 })
  const inFlight = d ? (d.rounds_by_status.uploaded ?? 0) + (d.rounds_by_status.profiled ?? 0) : 0
  return (
    <>
      <PageHeader title="Overview" subtitle="What needs a human, and what the system handled on its own." />
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
        <Stat label="Waiting for review" value={fmtInt(d?.open_reviews)} tone={d?.open_reviews ? 'warn' : undefined} hint={queue?.[0] ? `Top: ${queue[0].customer?.code} · ${queue[0].kind}` : 'Queue is empty'} />
        <Stat label="Handled automatically" value={d?.automation_rate !== null && d?.automation_rate !== undefined ? fmtPct(d.automation_rate) : '–'} hint={`${fmtInt(d?.auto_approved_mappings)} of ${fmtInt(d?.mapping_versions)} mapping versions approved by policy`} />
        <Stat label="Rows published" value={fmtInt(d?.published_rows)} hint={`${fmtInt(d?.approved_mappings)} customers with an approved mapping`} />
        <Stat label="AI spend" value={d ? `$${d.llm.cost_usd.toFixed(2)}` : '–'} hint={d ? (d.llm.provider === 'mock' ? 'Offline mode — deterministic mapper only' : `${fmtInt(d.llm.calls)} calls · ${fmtInt(d.llm.tokens)} tokens · ${d.llm.model}`) : ''} />
      </div>

      <div className="grid lg:grid-cols-[1.4fr_1fr] gap-4">
        <Section title="Recent imports" actions={<Link to="/imports" className="btn btn-ghost btn-sm">All imports <ArrowUpRight size={13} /></Link>}>
          {d?.recent_rounds?.length ? (
            <table className="table">
              <thead><tr><th>Customer</th><th>Round</th><th>Status</th><th>Decision</th><th className="text-right">Rows</th><th className="text-right">Updated</th></tr></thead>
              <tbody>
                {d.recent_rounds.map((r) => (
                  <tr key={r.id} className="row-link" onClick={() => (window.location.href = `/rounds/${r.id}`)}>
                    <td><span className="font-medium">{r.customer?.code}</span> <span className="text-muted">{r.customer?.name}</span></td>
                    <td className="text-ink-2">{r.label}</td>
                    <td><StatusBadge status={r.status} /></td>
                    <td className="text-muted">{r.summary.drift ? <Badge tone={r.summary.drift === 'identical' ? 'ok' : r.summary.drift === 'unrelated_file' ? 'bad' : 'warn'}>{r.summary.drift.replace('_', ' ')}</Badge> : r.summary.decision ?? '–'}</td>
                    <td className="text-right tnum">{fmtInt(r.summary.rows)}</td>
                    <td className="text-right text-muted">{timeAgo(r.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="p-4"><Empty title="No imports yet" body="Upload a customer's ERP export to get started." action={<Link to="/imports/new" className="btn btn-primary">New import</Link>} /></div>
          )}
        </Section>

        <div className="flex flex-col gap-4">
          <Section title="Review queue" actions={<Link to="/review" className="btn btn-ghost btn-sm">Open <ArrowUpRight size={13} /></Link>}>
            {queue?.length ? (
              <ul>
                {queue.slice(0, 6).map((t) => (
                  <li key={t.id} className="px-4 py-2.5 border-b border-line last:border-0 hover:bg-surface-2/60">
                    <Link to={t.mapping_version_id ? `/rounds/${t.round_id}/mapping/${t.mapping_version_id}` : `/rounds/${t.round_id}`} className="block">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-medium">{t.customer?.code} <span className="text-muted font-normal">{t.round?.label}</span></span>
                        <Badge tone={t.kind === 'blocked' ? 'bad' : t.kind === 'drift' ? 'warn' : 'signal'}>{t.kind}</Badge>
                      </div>
                      <div className="text-muted text-[12.5px] mt-0.5 truncate">{t.reasons[0]}</div>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : <div className="p-6 text-center text-muted">Nothing waiting. Policy handled everything.</div>}
          </Section>
          <Section title="Pipeline">
            <div className="p-4 grid grid-cols-3 gap-3 text-center">
              <div><div className="text-[20px] font-semibold tnum">{fmtInt(inFlight)}</div><div className="text-muted text-[12px]">in progress</div></div>
              <div><div className="text-[20px] font-semibold tnum">{fmtInt(d?.rounds_by_status.blocked ?? 0)}</div><div className="text-muted text-[12px]">blocked</div></div>
              <div><div className="text-[20px] font-semibold tnum">{fmtInt(d?.failed_jobs)}</div><div className="text-muted text-[12px]">failed jobs</div></div>
            </div>
          </Section>
        </div>
      </div>
    </>
  )
}
