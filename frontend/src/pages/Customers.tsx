import { Localized } from '../lib/locale'
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Pencil, Trash2, Upload } from 'lucide-react'
import { api, type Customer, type ImportRound, type MappingVersion } from '../lib/api'
import { formatLabel, fmtDate, fmtInt, timeAgo } from '../lib/utils'
import { PageHeader, Section, StatusBadge, Badge, Confidence, Empty, KV, Modal } from '../components/ui'
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
          <thead><tr><th>Code</th><th>Name</th><th>ERP</th><th>Mapping</th><th className="text-right">Rounds</th><th className="text-right">Last import</th><th>Next expected</th></tr></thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.id} className="row-link" onClick={() => nav(`/customers/${c.id}`)}>
                <td className="font-medium">{c.code}</td><td>{c.name}</td><td className="text-muted">{c.erp_type ?? '–'}</td>
                <td>{c.has_approved_mapping ? <Badge tone="ok">approved</Badge> : <Badge tone="neutral">none yet</Badge>}</td>
                <td className="text-right tnum">{typeof c.rounds === 'number' ? c.rounds : c.rounds?.length ?? 0}</td>
                <td className="text-right text-muted">{timeAgo(c.last_round_at)}</td>
                <td>{c.next_expected?.expected ? <span className="inline-flex items-center gap-2"><Badge tone={c.next_expected.status === 'overdue' ? 'bad' : c.next_expected.status === 'due' ? 'warn' : 'neutral'}>{c.next_expected.status === 'overdue' ? `${c.next_expected.late_days}d late` : c.next_expected.status === 'due' ? 'due' : c.next_expected.expected}</Badge><span className="text-muted text-[12px]">every {c.next_expected.every_days}d</span></span> : <span className="text-muted">–</span>}</td>
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
  const [editing, setEditing] = useState(false)
  if (!c) return null
  const rounds = (Array.isArray(c.rounds) ? c.rounds : []) as ImportRound[]
  const mappings = (c.mappings ?? []) as MappingVersion[]
  return (
    <>
      <PageHeader crumbs={<Link to="/customers" className="hover:text-ink">Customers</Link>} title={<span>{c.code} <span className="text-muted font-normal">{c.name}</span></span>} subtitle={c.erp_type ? `ERP: ${c.erp_type}` : undefined}
        actions={<><button className="btn" onClick={() => setEditing(true)}><Pencil size={14} /> Edit</button><Link to="/imports/new" className="btn btn-primary"><Upload size={14} /> New import</Link></>} />
      {editing && <EditCustomer customer={c} onClose={() => setEditing(false)} />}
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
                      <td>{r.summary.drift ? <Badge tone={['identical', 'header_moved'].includes(r.summary.drift) ? 'ok' : r.summary.drift === 'unrelated_file' ? 'bad' : 'warn'}>{formatLabel(r.summary.drift)}</Badge> : <span className="text-muted">first</span>}</td>
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
            <div className="px-4 py-2"><KV k="Code" v={c.code} /><KV k="ERP" v={c.erp_type ?? '–'} /><KV k="Target format" v={c.default_target_schema ?? 'default'} /><KV k="Status" v={c.active ? 'active' : <Badge tone="warn">inactive</Badge>} /><KV k="Created" v={fmtDate(c.created_at)} /><KV k="Notes" v={c.notes ?? '–'} /></div>
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

/** Correct customer master data here instead of in the database; every change is audited. */
function EditCustomer({ customer, onClose }: { customer: Customer; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const { data: schemas } = useQuery({ queryKey: ['schemas'], queryFn: api.schemas })
  const [f, setF] = useState({ code: customer.code, name: customer.name, erp_type: customer.erp_type ?? '', notes: customer.notes ?? '', active: customer.active, default_target_schema: customer.default_target_schema ?? '' })
  const save = useMutation({
    mutationFn: () => api.updateCustomer(customer.id, { code: f.code, name: f.name, erp_type: f.erp_type || null, notes: f.notes || null, active: f.active, default_target_schema: f.default_target_schema || null }),
    onSuccess: () => { qc.invalidateQueries(); toast.push({ kind: 'ok', title: 'Customer updated' }); onClose() },
    onError: (e: Error) => toast.push({ kind: 'bad', title: 'Not saved', detail: e.message }),
  })
  const ids = [...new Set((schemas ?? []).map((s) => s.schema_id))]
  const field = (label: string, el: React.ReactNode) => <label className="flex flex-col gap-1.5"><span className="text-muted">{label}</span>{el}</label>
  return (
    <Modal open onClose={onClose} title={`Edit ${customer.code}`} width="max-w-lg" footer={<><button className="btn" onClick={onClose}><Localized>Cancel</Localized></button><button className="btn btn-primary" disabled={save.isPending || !f.code.trim() || !f.name.trim()} onClick={() => save.mutate()}><Localized>Save</Localized></button></>}>
      <div className="grid grid-cols-2 gap-3 text-[13px]">
        {field('Code', <input className="input" value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} />)}
        {field('ERP', <input className="input" value={f.erp_type} onChange={(e) => setF({ ...f, erp_type: e.target.value })} placeholder="SAP, Express, Odoo, …" />)}
        <div className="col-span-2">{field('Name', <input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />)}</div>
        <div className="col-span-2">{field('Target format for new imports', (
          <select className="input" value={f.default_target_schema} onChange={(e) => setF({ ...f, default_target_schema: e.target.value })}>
            <option value="">Default format</option>
            {ids.map((id) => <option key={id} value={id}>{id} (latest)</option>)}
            {(schemas ?? []).map((s) => <option key={s.ref} value={s.ref}>{s.ref} (pinned)</option>)}
          </select>
        ))}</div>
        <div className="col-span-2">{field('Notes', <textarea className="input h-20 py-2" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} />)}</div>
        <label className="flex items-center gap-2 col-span-2 cursor-pointer"><input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} /> Active customer</label>
      </div>
    </Modal>
  )
}
