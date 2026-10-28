import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { PageHeader, Section, Badge, RiskDot, Tabs, Json, KV } from '../components/ui'
import { fmtDate } from '../lib/utils'

export function SettingsPage() {
  const [tab, setTab] = useState<'schema' | 'policy' | 'dsl' | 'audit' | 'system'>('schema')
  const { data: schemas } = useQuery({ queryKey: ['schemas'], queryFn: api.schemas })
  const [ref, setRef] = useState<string | null>(null)
  const current = ref ?? schemas?.[0]?.ref
  const { data: schema } = useQuery({ queryKey: ['schema', current], queryFn: () => api.schema(current!), enabled: !!current })
  const { data: policy } = useQuery({ queryKey: ['policy'], queryFn: api.policy, enabled: tab === 'policy' })
  const { data: dsl } = useQuery({ queryKey: ['dsl'], queryFn: api.dsl, enabled: tab === 'dsl' })
  const { data: audit } = useQuery({ queryKey: ['audit'], queryFn: () => api.audit(200), enabled: tab === 'audit' })
  const { data: dash } = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard })
  return (
    <>
      <PageHeader title="Settings" subtitle="The target contract, the approval policy and the transformation vocabulary — all configuration, none of it code." />
      <Tabs value={tab} onChange={setTab} items={[{ id: 'schema', label: 'Target schema' }, { id: 'policy', label: 'Approval policy' }, { id: 'dsl', label: 'Mapping DSL' }, { id: 'audit', label: 'Audit log' }, { id: 'system', label: 'System' }]} />
      <div className="mt-4">
        {tab === 'schema' && (
          <div className="grid lg:grid-cols-[260px_1fr] gap-4">
            <Section title="Schemas">
              <ul>{schemas?.map((s) => <li key={s.ref}><button className={`w-full text-left px-4 py-2.5 border-b border-line hover:bg-surface-2 ${s.ref === current ? 'bg-signal-soft/60' : ''}`} onClick={() => setRef(s.ref)}><div className="font-medium">{s.title} <span className="text-muted tnum">v{s.version}</span></div><div className="text-[12px] text-muted">{s.field_count} fields · {s.status}</div></button></li>)}</ul>
              <div className="p-4 text-[12px] text-muted leading-relaxed">To replace this draft with the real downstream contract, drop a JSON Schema file into <span className="mono">target_schema/</span> (or POST /api/schemas). Mappings, validation, the UI and the AI prompts all read from it.</div>
            </Section>
            {schema && (
              <Section title={`${schema.title} — ${schema.ref}`} actions={<span className="text-[12px] text-muted">identity: {schema.identity_keys.join(', ')}</span>}>
                <div className="px-4 py-2 text-muted text-[12.5px]">{schema.description}</div>
                <table className="table">
                  <thead><tr><th>Field</th><th>Type</th><th>Risk</th><th>Required</th><th>Semantic</th><th>Synonyms (TH / EN)</th></tr></thead>
                  <tbody>
                    {schema.fields?.filter((f) => !f.system).map((f) => (
                      <tr key={f.pointer}>
                        <td className="mono">{f.pointer}{f.is_collection && <Badge tone="neutral" className="ml-2 h-5 text-[11px]">collection</Badge>}</td>
                        <td className="text-muted">{f.json_type.join('/')}{f.format ? ` · ${f.format}` : ''}{f.enum ? ` · ${f.enum.length} values` : ''}</td>
                        <td><span className="flex items-center gap-1.5"><RiskDot risk={f.risk} />{f.risk}</span></td>
                        <td>{f.required ? 'yes' : ''}</td>
                        <td className="text-muted">{f.semantic}</td>
                        <td className="text-muted text-[12px]">{[...f.synonyms_th.slice(0, 4), ...f.synonyms_en.slice(0, 4)].join(', ')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Section>
            )}
          </div>
        )}
        {tab === 'policy' && (
          <div className="grid lg:grid-cols-2 gap-4">
            <Section title="How the gate decides"><div className="p-4 text-[13px] leading-relaxed flex flex-col gap-2">
              <p><span className="font-medium text-ok">Auto-approve</span> only when the customer already has an approved mapping, the structure is unchanged (or only gained columns), every critical field is mapped with ≥ 90% confidence, the simulation has no errors and few warnings, and the row count is in line with the previous round.</p>
              <p><span className="font-medium text-warn">Review</span> for first-time customers, renamed or removed columns, low-confidence prices/discounts/dates, or any engine error. The queue is ordered by the weights below.</p>
              <p><span className="font-medium text-bad">Block</span> when a file's structure matches another customer's approved format, when it is unrelated to this customer's format, or when the transformation yields zero rows.</p>
              <p className="text-muted">Edit <span className="mono">policies/approval.yaml</span> to tune thresholds; no deployment needed.</p>
            </div></Section>
            <Section title="approval.yaml"><Json value={policy} className="rounded-none border-0" /></Section>
          </div>
        )}
        {tab === 'dsl' && (
          <div className="grid lg:grid-cols-[320px_1fr] gap-4">
            <Section title={`${dsl?.ops.length ?? 0} whitelisted operations`}><div className="p-3 flex flex-wrap gap-1.5">{dsl?.ops.map((o) => <span key={o} className="chip bg-surface-2 border-line mono">{o}</span>)}</div><div className="px-4 pb-4 text-[12px] text-muted leading-relaxed">The AI can only compose these. No generated code is ever executed; every op has one deterministic compiler branch and tests.</div></Section>
            <Section title="DSL JSON Schema (what the LLM must emit)"><Json value={dsl?.json_schema} className="rounded-none border-0 max-h-[70vh]" /></Section>
          </div>
        )}
        {tab === 'audit' && (
          <Section title="Last 200 events">
            <table className="table"><thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Entity</th><th>Details</th></tr></thead>
              <tbody>{audit?.map((a) => <tr key={a.id}><td className="text-muted whitespace-nowrap">{fmtDate(a.at)}</td><td>{a.actor}</td><td className="mono">{a.action}</td><td className="mono text-muted">{a.entity_id}</td><td className="text-muted text-[12px] truncate max-w-[420px]">{JSON.stringify(a.details)}</td></tr>)}</tbody></table>
          </Section>
        )}
        {tab === 'system' && (
          <Section title="Runtime"><div className="px-4 py-2">
            <KV k="AI provider" v={dash?.llm.provider === 'mock' ? 'offline (deterministic mapper only) — set FORMA_LLM_PROVIDER / FORMA_LLM_API_KEY to enable' : `${dash?.llm.provider} · ${dash?.llm.model}`} />
            <KV k="AI calls / cost" v={`${dash?.llm.calls ?? 0} · $${(dash?.llm.cost_usd ?? 0).toFixed(4)}`} />
            <KV k="Customers" v={dash?.customers} /><KV k="Mapping versions" v={dash?.mapping_versions} /><KV k="Failed jobs" v={dash?.failed_jobs} />
          </div></Section>
        )}
      </div>
    </>
  )
}
