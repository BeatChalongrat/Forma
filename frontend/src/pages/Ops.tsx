import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { RotateCcw } from 'lucide-react'
import { api, type OpsSummary } from '../lib/api'
import { PageHeader, Stat, Section, Badge, Empty, Spinner } from '../components/ui'
import { fmtInt, timeAgo } from '../lib/utils'
import { useToast } from '../lib/toast'

function ms(v: number | null | undefined): string {
  if (v === null || v === undefined) return '–'
  return v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`
}

export function OpsPage() {
  const qc = useQueryClient()
  const toast = useToast()
  const { data: ops, isError, error } = useQuery({ queryKey: ['ops'], queryFn: api.ops, refetchInterval: 5000 })
  const retry = useMutation({
    mutationFn: (id: string) => api.retryJob(id),
    onSuccess: () => {
      toast.push({ kind: 'ok', title: 'Job re-queued' })
      qc.invalidateQueries({ queryKey: ['ops'] })
    },
    onError: (e: Error) => toast.push({ kind: 'bad', title: 'Retry failed', detail: e.message }),
  })
  if (isError) return <Empty title="Ops is for admins" body={(error as Error).message} />
  const q = ops?.queue
  const ready = ops?.ready
  return (
    <>
      <PageHeader
        title="Ops"
        subtitle="Queue, workers, failures and spend — what an operator needs at 09:00 and at 02:00."
        actions={ready && <Badge tone={ready.status === 'ok' ? 'ok' : 'warn'}>{ready.status === 'ok' ? 'all systems ready' : `degraded: ${Object.entries(ready.checks).filter(([, v]) => !v.ok).map(([k]) => k).join(', ')}`}</Badge>}
      />
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-5">
        <Stat label="Queued" value={fmtInt(q?.queued)} tone={q && q.queued > (q.limit ?? 500) * 0.8 ? 'warn' : undefined} hint={q?.oldest_queued_age_s ? `oldest waiting ${Math.round(q.oldest_queued_age_s)} s` : `limit ${fmtInt(q?.limit)}`} />
        <Stat label="Running" value={fmtInt(q?.running)} hint={q ? `${q.backend} backend · ${fmtInt(q.workers)} workers${q.nodes ? ` · ${q.nodes} node` : ''}` : ''} />
        <Stat label="Failed jobs" value={fmtInt(q?.failed)} tone={q?.failed ? 'bad' : undefined} hint="retry re-queues the same job; finished stages are skipped" />
        <Stat label="LLM spend (7 d)" value={ops ? `$${ops.llm_spend_by_day.reduce((a, d) => a + d.cost_usd, 0).toFixed(2)}` : '–'} hint={ops?.breaker?.open ? 'circuit breaker OPEN — heuristic fallback' : `${fmtInt(ops?.llm_spend_by_day.reduce((a, d) => a + d.calls, 0))} calls`} tone={ops?.breaker?.open ? 'warn' : undefined} />
        <Stat label="Open reviews" value={fmtInt(ops?.review?.open)} hint={ops?.review?.oldest_age_s ? `oldest waiting ${Math.round(ops.review.oldest_age_s / 3600)} h` : 'queue is empty'} />
      </div>

      <div className="grid lg:grid-cols-[1.3fr_1fr] gap-4">
        <Section title="Failed jobs" actions={<span className="text-[12px] text-muted">{ops?.failed_jobs.length ?? 0} shown</span>}>
          {ops?.failed_jobs.length ? (
            <table className="table">
              <thead><tr><th>Kind</th><th>Round</th><th>Error</th><th>Attempts</th><th className="text-right">When</th><th></th></tr></thead>
              <tbody>
                {ops.failed_jobs.map((j) => (
                  <tr key={j.id}>
                    <td className="mono text-[12px]">{j.kind}</td>
                    <td>{j.round_id ? <Link className="text-signal" to={`/rounds/${j.round_id}`}>{j.customer_code ?? j.round_id.slice(0, 12)}</Link> : '–'}</td>
                    <td className="text-bad text-[12.5px] max-w-[360px] truncate" title={j.error ?? ''}>{(j.error ?? '').split('\n')[0]}</td>
                    <td className="tnum">{j.attempts}</td>
                    <td className="text-right text-muted">{j.finished_at ? timeAgo(j.finished_at) : ''}</td>
                    <td className="text-right"><button className="btn btn-ghost btn-sm" disabled={retry.isPending} onClick={() => retry.mutate(j.id)}>{retry.isPending && retry.variables === j.id ? <Spinner /> : <RotateCcw size={13} />} Retry</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <div className="p-6 text-center text-muted">No failed jobs.</div>}
        </Section>

        <div className="flex flex-col gap-4">
          <Section title="LLM spend per day">
            {ops?.llm_spend_by_day.length ? (
              <table className="table">
                <thead><tr><th>Day</th><th className="text-right">Calls</th><th className="text-right">Tokens</th><th className="text-right">USD</th></tr></thead>
                <tbody>{ops.llm_spend_by_day.map((d) => <tr key={d.day}><td>{d.day}</td><td className="text-right tnum">{fmtInt(d.calls)}</td><td className="text-right tnum">{fmtInt(d.tokens)}</td><td className="text-right tnum">${d.cost_usd.toFixed(3)}</td></tr>)}</tbody>
              </table>
            ) : <div className="p-6 text-center text-muted">No model calls in the last 7 days.</div>}
          </Section>
          <Section title="Workers & storage">
            <div className="p-4 text-[13px] flex flex-col gap-1.5">
              <Row k="Job backend" v={q?.backend} />
              <Row k="Workers (this node)" v={fmtInt(q?.workers)} />
              <Row k="In flight (this node)" v={fmtInt(q?.inflight)} />
              <Row k="Storage" v={ops?.storage} />
              <Row k="Database" v={ops?.database} />
              <Row k="Auth" v={ops?.auth} />
              <Row k="Tracing" v={ops?.tracing ? 'on' : 'off'} />
              <Row k="Schema revision" v={<span className="mono text-[12px]">{ops?.schema_revision ?? '–'}</span>} />
            </div>
          </Section>
        </div>
      </div>

      <Section title="Slowest rounds (last 200 jobs)" className="mt-4">
        {ops?.slowest_rounds.length ? (
          <table className="table">
            <thead><tr><th>Customer</th><th>Round</th><th>Job</th><th className="text-right">Profile</th><th className="text-right">Propose</th><th className="text-right">Transform</th><th className="text-right">Total</th></tr></thead>
            <tbody>
              {ops.slowest_rounds.map((r) => (
                <tr key={r.job_id} className="row-link" onClick={() => (window.location.href = `/rounds/${r.round_id}`)}>
                  <td className="font-medium">{r.customer_code ?? '–'}</td>
                  <td className="text-ink-2">{r.label ?? r.round_id}</td>
                  <td className="mono text-[12px]">{r.kind}</td>
                  <td className="text-right tnum">{ms(r.stages.profile)}</td>
                  <td className="text-right tnum">{ms(r.stages.propose)}</td>
                  <td className="text-right tnum">{ms(r.stages.transform)}</td>
                  <td className="text-right tnum font-medium">{ms(r.duration_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <div className="p-6 text-center text-muted">No finished jobs yet.</div>}
      </Section>
    </>
  )
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3 border-b border-line last:border-0 py-1">
      <span className="text-muted">{k}</span>
      <span>{v ?? '–'}</span>
    </div>
  )
}

export type { OpsSummary }
