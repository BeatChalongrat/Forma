import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Trash2, Upload } from 'lucide-react'
import { api, type ImportRound, type MappingVersion } from '../lib/api'
import { fmtDate, fmtInt, timeAgo } from '../lib/utils'
import { PageHeader, Section, StatusBadge, Badge, Confidence, Empty, KV } from '../components/ui'
import { useToast } from '../lib/toast'

export function Customers() {
  const { data } = useQuery({ queryKey: ['customers'], queryFn: api.customers })
  const [q, setQ] = useState('')
  const nav = useNavigate()
  const rows = (data ?? []).filter((c) => !q || `${c.code} ${c.name} ${c.erp_type ?? ''}`.toLowerCase().includes(q.toLowerCase()))
  return (
    <>
      <PageHeader title="Customers" subtitle={`${fmtInt(data?.length)} customers sending ERP exports every two weeks.`} actions={<input className="input w-64" placeholder="Search code, name, ERP…" value={q} onChange={(e) => setQ(e.target.value)} />} />
      <Section title={`${rows.length} shown`}>
        <table className="table">
          <thead><tr><th>Code</th><th>Name</th><th>ERP</th><th>Mapping</th><th className="text-right">Rounds</th><th className="text-right">Last import</th></tr></thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.id} className="row-link" onClick={() => nav(`/customers/${c.id}`)}>
                <td className="font-medium">{c.code}</td><td>{c.name}</td><td className="text-muted">{c.erp_type ?? '–'}</td>
                <td>{c.has_approved_mapping ? <Badge tone="ok">approved</Badge> : <Badge tone="neutral">none yet</Badge>}</td>
                <td className="text-right tnum">{typeof c.rounds === 'number' ? c.rounds : c.rounds?.length ?? 0}</td>
                <td className="text-right text-muted">{timeAgo(c.last_round_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
    </>
  )
}

export function CustomerPage() {
  const { customerId = '' } = useParams()
  const qc = useQueryClient()
  const toast = useToast()
  const { data: c } = useQuery({ queryKey: ['customer', customerId], queryFn: () => api.customer(customerId) })
  const delOverride = useMutation({ mutationFn: (id: string) => api.deleteOverride(customerId, id), onSuccess: () => { qc.invalidateQueries({ queryKey: ['customer', customerId] }); toast.push({ kind: 'ok', title: 'Override removed' }) } })
  if (!c) return null
  const rounds = (Array.isArray(c.rounds) ? c.rounds : []) as ImportRound[]
  const mappings = (c.mappings ?? []) as MappingVersion[]
  return (
    <>
      <PageHeader crumbs={<Link to="/customers" className="hover:text-ink">Customers</Link>} title={<span>{c.code} <span className="text-muted font-normal">{c.name}</span></span>} subtitle={c.erp_type ? `ERP: ${c.erp_type}` : undefined}
        actions={<Link to="/imports/new" className="btn btn-primary"><Upload size={14} /> New import</Link>} />
      <div className="grid lg:grid-cols-[1fr_360px] gap-4">
        <div className="flex flex-col gap-4">
          <Section title="Import history">
            {rounds.length ? (
              <table className="table">
                <thead><tr><th>Round</th><th>Status</th><th>Format</th><th className="text-right">Rows</th><th className="text-right">When</th></tr></thead>
                <tbody>
                  {rounds.map((r) => (
                    <tr key={r.id} className="row-link" onClick={() => (window.location.href = `/rounds/${r.id}`)}>
                      <td>{r.label}</td><td><StatusBadge status={r.status} /></td>
                      <td>{r.summary.drift ? <Badge tone={['identical', 'header_moved'].includes(r.summary.drift) ? 'ok' : r.summary.drift === 'unrelated_file' ? 'bad' : 'warn'}>{r.summary.drift.replace('_', ' ')}</Badge> : <span className="text-muted">first</span>}</td>
                      <td className="text-right tnum">{fmtInt(r.summary.rows)}</td><td className="text-right text-muted">{fmtDate(r.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <div className="p-4"><Empty title="No imports yet" /></div>}
          </Section>
          <Section title="Mapping memory">
            {mappings.length ? (
              <table className="table">
                <thead><tr><th>Version</th><th>Status</th><th>Origin</th><th>Confidence</th><th>Approved by</th><th>Fingerprint</th><th></th></tr></thead>
                <tbody>
                  {mappings.map((m) => (
                    <tr key={m.id}>
                      <td className="tnum">v{m.version}</td><td><StatusBadge status={m.status} /></td><td className="text-muted">{m.policy?.source ?? m.created_by}</td>
                      <td><Confidence value={m.confidence} /></td><td className="text-muted">{m.approved_by ?? '–'}</td><td className="mono text-muted">{(m.policy?.checks as { drift?: string } | undefined)?.drift ?? ''}</td>
                      <td className="text-right">{m.round_id && <Link className="btn btn-ghost btn-sm" to={`/rounds/${m.round_id}/mapping/${m.id}`}>Open</Link>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <div className="p-6 text-muted text-center">No mapping versions yet.</div>}
          </Section>
        </div>
        <div className="flex flex-col gap-4">
          <Section title="Details">
            <div className="px-4 py-2"><KV k="Code" v={c.code} /><KV k="ERP" v={c.erp_type ?? '–'} /><KV k="Created" v={fmtDate(c.created_at)} /><KV k="Notes" v={c.notes ?? '–'} /></div>
          </Section>
          <Section title={`Persistent overrides (${c.overrides?.length ?? 0})`}>
            {c.overrides?.length ? (
              <ul>
                {c.overrides.map((o) => (
                  <li key={o.id} className="px-4 py-2.5 border-b border-line last:border-0 flex items-start gap-2 text-[12.5px]">
                    <div className="flex-1 min-w-0">
                      <div className="mono">{o.target} ← {JSON.stringify(o.value)}</div>
                      <div className="text-muted mt-0.5">when {'identity' in o.where ? `SKU = ${String(o.where.identity)}` : 'rule matches'} · {o.author} · {fmtDate(o.created_at)}</div>
                    </div>
                    <button className="btn btn-ghost btn-sm text-bad" onClick={() => delOverride.mutate(o.id)} title="Deactivate"><Trash2 size={13} /></button>
                  </li>
                ))}
              </ul>
            ) : <div className="p-4 text-[12.5px] text-muted">Persistent edits made in the Data Editor appear here and are re-applied on every future import for this customer.</div>}
          </Section>
        </div>
      </div>
    </>
  )
}
