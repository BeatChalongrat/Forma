import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Check, FileJson, KeyRound, RotateCcw, Save, Star, Upload, Wifi } from 'lucide-react'
import { api, ApiError, type AISettings, type Policy, type SchemaPreview } from '../lib/api'
import { PageHeader, Section, Badge, RiskDot, Tabs, Json, KV, Modal, Spinner } from '../components/ui'
import { cn, fmtDate } from '../lib/utils'
import { useToast } from '../lib/toast'

type Tab = 'ai' | 'formats' | 'policy' | 'dsl' | 'audit' | 'system'

/** Admins change AI, the approval policy and target formats here — validated, audited, applied without a restart. */
export function SettingsPage() {
  const [tab, setTab] = useState<Tab>('ai')
  return (
    <>
      <PageHeader title="Settings" subtitle="AI provider, target formats and the approval policy — edited here, checked before saving, recorded in the audit log, applied without a restart." />
      <Tabs value={tab} onChange={setTab} items={[{ id: 'ai', label: 'AI' }, { id: 'formats', label: 'Target formats' }, { id: 'policy', label: 'Approval policy' }, { id: 'dsl', label: 'Mapping DSL' }, { id: 'audit', label: 'Audit log' }, { id: 'system', label: 'System' }]} />
      <div className="mt-4">
        {tab === 'ai' && <AITab />}
        {tab === 'formats' && <FormatsTab />}
        {tab === 'policy' && <PolicyTab />}
        {tab === 'dsl' && <DslTab />}
        {tab === 'audit' && <AuditTab />}
        {tab === 'system' && <SystemTab />}
      </div>
    </>
  )
}

function problemsOf(e: Error): string {
  const d = e instanceof ApiError ? (e.details as { problems?: string[] } | undefined) : undefined
  return d?.problems?.join(' · ') ?? e.message
}

// ---------------------------------------------------------------------------------------------
// AI
// ---------------------------------------------------------------------------------------------
type AIForm = { provider: string; model: string; base_url: string; timeout_s: string; max_retries: string }
const formOf = (ai: AISettings): AIForm => ({ provider: ai.provider, model: ai.model, base_url: ai.base_url ?? '', timeout_s: String(ai.timeout_s), max_retries: String(ai.max_retries) })

function AITab() {
  const qc = useQueryClient()
  const toast = useToast()
  const { data: ai } = useQuery({ queryKey: ['ai-settings'], queryFn: api.aiSettings })
  const [draft, setDraft] = useState<AIForm | null>(null)
  const [key, setKey] = useState('')
  const [advanced, setAdvanced] = useState(false)
  const f = draft ?? (ai ? formOf(ai) : null)
  const set = (k: keyof AIForm, v: string) => f && setDraft({ ...f, [k]: v })
  const save = useMutation({
    mutationFn: () => api.saveAiSettings({ provider: f!.provider, model: f!.model.trim() || null, base_url: f!.base_url.trim() || null, timeout_s: Number(f!.timeout_s), max_retries: Number(f!.max_retries), ...(key.trim() ? { api_key: key.trim() } : {}) }),
    onSuccess: (r) => { setDraft(null); setKey(''); qc.invalidateQueries(); toast.push({ kind: r.warning ? 'info' : 'ok', title: 'AI settings saved', detail: r.warning ?? `${r.active_provider} · ${r.model}` }) },
    onError: (e: Error) => toast.push({ kind: 'bad', title: 'Not saved', detail: e.message }),
  })
  const removeKey = useMutation({ mutationFn: () => api.saveAiSettings({ api_key: '' }), onSuccess: () => { qc.invalidateQueries(); toast.push({ kind: 'ok', title: 'Saved API key removed' }) } })
  const test = useMutation({ mutationFn: api.testAi, onError: (e: Error) => toast.push({ kind: 'bad', title: 'Test failed', detail: e.message }) })
  if (!ai || !f) return <div className="p-10 text-center"><Spinner /></div>
  const dirty = draft !== null || key.trim() !== ''
  return (
    <div className="flex flex-col gap-4">
      <ConnectCard ai={ai} />
      <div><button className="btn btn-ghost btn-sm" onClick={() => setAdvanced(!advanced)}>{advanced ? 'Hide' : 'Show'} advanced settings — provider, model, gateway URL, timeout</button></div>
      {advanced && (
    <div className="grid lg:grid-cols-[1fr_340px] gap-4">
      <Section title="Advanced settings" actions={ai.offline ? <Badge tone="warn">offline · deterministic mapper only</Badge> : <Badge tone="ok"><Bot size={12} /> {ai.active_provider} · {ai.model}</Badge>}>
        <div className="p-4 grid sm:grid-cols-2 gap-4 text-[13px]">
          <label className="flex flex-col gap-1.5"><span className="text-muted">Provider</span>
            <select className="input" value={f.provider} onChange={(e) => set('provider', e.target.value)}>{ai.providers.map((p) => <option key={p} value={p}>{p === 'mock' ? 'off (mock)' : p}</option>)}</select>
          </label>
          <label className="flex flex-col gap-1.5"><span className="text-muted">Model</span><input className="input mono" value={f.model} onChange={(e) => set('model', e.target.value)} placeholder="gpt-4.1-mini · claude-…" /></label>
          <label className="flex flex-col gap-1.5 sm:col-span-2"><span className="text-muted">API key {ai.api_key_set && <span className="text-ink-2">— saved {ai.api_key_hint} ({ai.api_key_source === 'ui' ? 'from this page' : 'from the server environment'})</span>}</span>
            <input className="input mono" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder={ai.api_key_set ? 'leave empty to keep the saved key' : 'paste the key; it is stored encrypted and never shown again'} />
          </label>
          <label className="flex flex-col gap-1.5 sm:col-span-2"><span className="text-muted">Gateway URL (optional — any /chat/completions-compatible endpoint)</span><input className="input mono" value={f.base_url} onChange={(e) => set('base_url', e.target.value)} placeholder="https://your-gateway/v1" /></label>
          <label className="flex flex-col gap-1.5"><span className="text-muted">Timeout (seconds)</span><input className="input tnum" type="number" min={1} max={600} value={f.timeout_s} onChange={(e) => set('timeout_s', e.target.value)} /></label>
          <label className="flex flex-col gap-1.5"><span className="text-muted">Retries on invalid output</span><input className="input tnum" type="number" min={0} max={10} value={f.max_retries} onChange={(e) => set('max_retries', e.target.value)} /></label>
        </div>
        {ai.warning && <div className="mx-4 mb-3 rounded-[var(--radius-md)] bg-warn-soft text-warn px-3 py-2 text-[12.5px]">{ai.warning}</div>}
        <div className="px-4 pb-4 flex flex-wrap items-center gap-2">
          <button className="btn btn-primary" disabled={!dirty || save.isPending} onClick={() => save.mutate()}><Save size={14} /> Save</button>
          {dirty && <button className="btn" onClick={() => { setDraft(null); setKey('') }}>Discard</button>}
          <button className="btn" disabled={test.isPending || dirty} onClick={() => test.mutate()}><Wifi size={14} /> {test.isPending ? 'Testing…' : 'Test connection'}</button>
          {ai.api_key_source === 'ui' && <button className="btn btn-ghost" onClick={() => removeKey.mutate()}><KeyRound size={14} /> Remove saved key</button>}
          {test.data && <Badge tone={test.data.ok ? 'ok' : 'bad'}>{test.data.ok ? `connected · ${test.data.latency_ms} ms` : test.data.message}</Badge>}
        </div>
      </Section>
      <Section title="How it is used"><div className="p-4 text-[12.5px] text-ink-2 leading-relaxed flex flex-col gap-2">
        <p>Without a provider FORMA still maps every file with its deterministic mapper. A provider adds refinement of hard columns, adaptation when a customer's format changes, diagnosis and the copilot.</p>
        <p>The key is encrypted with the server's <span className="mono">FORMA_SECRET_KEY</span> and only its last four characters are ever shown. Removing it here falls back to the server's <span className="mono">FORMA_LLM_API_KEY</span>, if any.</p>
        <p>Every API and worker process picks up a change within a few seconds — no restart.</p>
        {ai.updated_at && <p className="text-muted">Last changed by {ai.updated_by} · {fmtDate(ai.updated_at)}</p>}
      </div></Section>
    </div>
      )}
    </div>
  )
}


/** Paste a key, press Connect: the provider is recognised, a model chosen, the key tried and saved encrypted. */
function ConnectCard({ ai }: { ai: AISettings }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [key, setKey] = useState('')
  const [provider, setProvider] = useState('')
  const [replacing, setReplacing] = useState(false)
  const k = key.trim()
  const detected = k.startsWith('sk-ant-') ? 'anthropic' : k.startsWith('sk-') ? 'openai' : null
  const connect = useMutation({
    mutationFn: () => api.connectAi(key, detected ? undefined : provider || undefined),
    onSuccess: (r) => { setKey(''); setReplacing(false); qc.invalidateQueries(); toast.push({ kind: 'ok', title: 'AI connected', detail: `${r.ai.active_provider} · ${r.ai.model} · answered in ${r.test.latency_ms} ms` }) },
  })
  const disconnect = useMutation({
    mutationFn: () => api.saveAiSettings({ api_key: '', provider: null, model: null }),
    onSuccess: () => { qc.invalidateQueries(); toast.push({ kind: 'ok', title: 'AI disconnected', detail: 'FORMA keeps mapping with its deterministic mapper.' }) },
  })
  if (!ai.offline && !replacing) {
    return (
      <Section title="AI">
        <div className="p-4 flex flex-wrap items-center gap-3 text-[13px]">
          <Badge tone="ok"><Check size={12} /> Connected</Badge>
          <span className="text-ink-2">{ai.active_provider} · <span className="mono">{ai.model}</span>{ai.api_key_hint && <> · key {ai.api_key_hint}</>}</span>
          <div className="ml-auto flex gap-2">
            <button className="btn btn-sm" onClick={() => setReplacing(true)}><KeyRound size={13} /> Replace key</button>
            {ai.api_key_source === 'ui' && <button className="btn btn-sm btn-ghost" disabled={disconnect.isPending} onClick={() => disconnect.mutate()}>Disconnect</button>}
          </div>
        </div>
      </Section>
    )
  }
  return (
    <Section title="Connect AI" actions={!replacing ? <Badge tone="warn">offline · deterministic mapper only</Badge> : undefined}>
      <div className="p-4 flex flex-col gap-3 text-[13px]">
        <p className="text-ink-2">Paste your API key and press Connect. OpenAI (<span className="mono">sk-…</span>) and Anthropic (<span className="mono">sk-ant-…</span>) keys are recognised automatically; FORMA picks the model, tries the key with one tiny call and saves it encrypted — nothing else to fill in.</p>
        <div className="flex flex-wrap items-center gap-2">
          <input className="input mono flex-1 min-w-[280px]" type="password" autoComplete="off" autoFocus value={key} onChange={(e) => setKey(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && k && (detected || provider)) connect.mutate() }} placeholder="Paste API key here" />
          {k && (detected
            ? <Badge tone="signal">{detected === 'anthropic' ? 'Anthropic' : 'OpenAI'} key</Badge>
            : <select className="input w-52" value={provider} onChange={(e) => setProvider(e.target.value)}><option value="">Which provider is this?</option><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option><option value="openai_compatible">Gateway (set its URL under advanced)</option></select>)}
          <button className="btn btn-primary" disabled={!k || connect.isPending || (!detected && !provider)} onClick={() => connect.mutate()}><Wifi size={14} /> {connect.isPending ? 'Checking the key…' : 'Connect'}</button>
          {replacing && <button className="btn" onClick={() => { setReplacing(false); setKey('') }}>Cancel</button>}
        </div>
        {connect.error && <div className="rounded-[var(--radius-md)] bg-bad-soft text-bad px-3 py-2 text-[12.5px]">{connect.error.message}</div>}
      </div>
    </Section>
  )
}

// ---------------------------------------------------------------------------------------------
// Target formats
// ---------------------------------------------------------------------------------------------
function FormatsTab() {
  const qc = useQueryClient()
  const toast = useToast()
  const { data: schemas } = useQuery({ queryKey: ['schemas'], queryFn: api.schemas })
  const [ref, setRef] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const current = ref ?? schemas?.find((s) => s.is_default)?.ref ?? schemas?.[0]?.ref
  const sel = schemas?.find((s) => s.ref === current)
  const { data: schema } = useQuery({ queryKey: ['schema', current], queryFn: () => api.schema(current!), enabled: !!current })
  const makeDefault = useMutation({
    mutationFn: (r: string) => api.setDefaultFormat(r),
    onSuccess: (r) => { qc.invalidateQueries(); toast.push({ kind: 'ok', title: `Default format: ${r.target_schema}`, detail: 'New rounds of customers without their own format use it.' }) },
    onError: (e: Error) => toast.push({ kind: 'bad', title: 'Not changed', detail: e.message }),
  })
  return (
    <div className="grid lg:grid-cols-[280px_1fr] gap-4">
      <Section title="Formats" actions={<button className="btn btn-sm btn-primary" onClick={() => setOpen(true)}><Upload size={13} /> New format</button>}>
        <ul>{schemas?.map((s) => (
          <li key={s.ref}><button className={cn('w-full text-left px-4 py-2.5 border-b border-line hover:bg-surface-2', s.ref === current && 'bg-signal-soft/60')} onClick={() => setRef(s.ref)}>
            <div className="font-medium flex items-center gap-1.5">{s.title ?? s.schema_id} <span className="text-muted tnum">v{s.version}</span>{s.is_default && <Star size={12} className="text-warn fill-current" />}</div>
            <div className="text-[12px] text-muted">{s.field_count} fields · {s.source === 'ui' ? 'added here' : 'built-in file'}{s.is_latest ? '' : ' · older version'}</div>
          </button></li>
        ))}</ul>
        <div className="p-4 text-[12px] text-muted leading-relaxed">A format is a JSON Schema with an <span className="mono">x-forma</span> block (identity key, semantics, synonyms). Mapping, validation, AI prompts and exports all follow it — no code change. ★ = used for customers that have no format of their own.</div>
      </Section>
      {schema && sel && (
        <Section title={`${schema.title ?? schema.schema_id} — ${schema.ref}`} actions={<div className="flex items-center gap-2"><span className="text-[12px] text-muted">identity: {schema.identity_keys.join(', ')}</span>{!sel.is_default && <button className="btn btn-sm" disabled={makeDefault.isPending} onClick={() => makeDefault.mutate(sel.is_latest ? sel.schema_id : sel.ref)}><Star size={13} /> Make default</button>}</div>}>
          {schema.description && <div className="px-4 py-2 text-muted text-[12.5px]">{schema.description}</div>}
          <FieldTable fields={schema.fields?.filter((f) => !f.system) ?? []} />
        </Section>
      )}
      <NewFormatModal open={open} onClose={() => setOpen(false)} onRegistered={(r) => { setRef(r); setOpen(false) }} />
    </div>
  )
}

function FieldTable({ fields }: { fields: NonNullable<SchemaPreview['fields']> }) {
  return (
    <table className="table">
      <thead><tr><th>Field</th><th>Type</th><th>Risk</th><th>Required</th><th>Semantic</th><th>Synonyms (TH / EN)</th></tr></thead>
      <tbody>{fields.map((f) => (
        <tr key={f.pointer}>
          <td className="mono">{f.pointer}{f.is_collection && <Badge tone="neutral" className="ml-2 h-5 text-[11px]">collection</Badge>}</td>
          <td className="text-muted">{f.json_type.join('/')}{f.format ? ` · ${f.format}` : ''}{f.enum ? ` · ${f.enum.length} values` : ''}</td>
          <td><span className="flex items-center gap-1.5"><RiskDot risk={f.risk} />{f.risk}</span></td>
          <td>{f.required ? 'yes' : ''}</td>
          <td className="text-muted mono">{f.semantic}</td>
          <td className="text-muted text-[12px]">{[...f.synonyms_th.slice(0, 4), ...f.synonyms_en.slice(0, 4)].join(', ')}</td>
        </tr>
      ))}</tbody>
    </table>
  )
}

function NewFormatModal({ open, onClose, onRegistered }: { open: boolean; onClose: () => void; onRegistered: (ref: string) => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [text, setText] = useState('')
  const [checked, setChecked] = useState<{ text: string; preview: SchemaPreview } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { data: semantics } = useQuery({ queryKey: ['semantics'], queryFn: api.semantics, enabled: open })
  const parse = (): unknown => { try { return JSON.parse(text) } catch (e) { setError(`Not valid JSON: ${(e as Error).message}`); return undefined } }
  const check = useMutation({
    mutationFn: (doc: unknown) => api.previewSchema(doc),
    onSuccess: (p) => { setChecked({ text, preview: p }); setError(null) },
    onError: (e: Error) => { setChecked(null); setError(e.message) },
  })
  const register = useMutation({
    mutationFn: (doc: unknown) => api.registerSchema(doc),
    onSuccess: (s) => { qc.invalidateQueries(); toast.push({ kind: 'ok', title: `Registered ${s.ref}`, detail: 'Assign it to a customer, or make it the default.' }); setText(''); setChecked(null); onRegistered(s.ref) },
    onError: (e: Error) => setError(e.message),
  })
  const preview = checked && checked.text === text ? checked.preview : null
  const onFile = async (file: File | undefined) => { if (file) { setText(await file.text()); setChecked(null); setError(null) } }
  return (
    <Modal open={open} onClose={onClose} title="New target format" width="max-w-5xl" footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn" disabled={!text.trim() || check.isPending} onClick={() => { const d = parse(); if (d !== undefined) check.mutate(d) }}><Check size={14} /> Check</button>
      <button className="btn btn-primary" disabled={!preview || register.isPending} onClick={() => { const d = parse(); if (d !== undefined) register.mutate(d) }}><FileJson size={14} /> Register {preview?.ref}</button>
    </>}>
      <div className="grid lg:grid-cols-[1fr_260px] gap-4 text-[13px]">
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-2"><label className="btn btn-sm cursor-pointer"><Upload size={13} /> Load .json file<input type="file" accept=".json,application/json" className="hidden" onChange={(e) => void onFile(e.target.files?.[0])} /></label><span className="text-muted text-[12px]">or paste the JSON Schema the downstream team gave you</span></div>
          <textarea className="input mono h-56 py-2 text-[12px]" spellCheck={false} value={text} onChange={(e) => setText(e.target.value)} placeholder={'{\n  "type": "object",\n  "properties": { "item_code": { "type": "string", "x-forma": { "semantic": "sku", "synonyms_th": ["รหัสสินค้า"] } } },\n  "x-forma": { "schema_id": "catalog", "identity_keys": ["/item_code"] }\n}'} />
          {error && <div className="rounded-[var(--radius-md)] bg-bad-soft text-bad px-3 py-2 text-[12.5px]">{error}</div>}
          {preview && (
            <div className="flex flex-col gap-3">
              <div className="flex flex-wrap items-center gap-2"><Badge tone="ok">valid · {preview.ref}</Badge><span className="text-muted">{preview.field_count} fields · identity {preview.identity_keys.join(', ')}</span>{preview.previous && <span className="text-muted">· replaces {preview.previous}: +{preview.diff?.added.length ?? 0} / −{preview.diff?.removed.length ?? 0} / ~{preview.diff?.changed.length ?? 0} fields</span>}</div>
              {preview.findings.length > 0 && <ul className="flex flex-col gap-1">{preview.findings.map((x, i) => <li key={i} className={cn('text-[12.5px]', x.level === 'warning' ? 'text-warn' : 'text-muted')}>• <span className="mono">{x.pointer}</span> {x.message}</li>)}</ul>}
              <div className="max-h-64 overflow-auto border border-line rounded-[var(--radius-md)]"><FieldTable fields={preview.fields.filter((f) => !f.system)} /></div>
            </div>
          )}
        </div>
        <div className="text-[12px]">
          <div className="font-medium mb-1">Semantics FORMA understands</div>
          <div className="text-muted mb-2">Tag each field with <span className="mono">x-forma.semantic</span> so Thai dates, prices, VAT, units, stock and promotions are handled for it.</div>
          <ul className="max-h-[420px] overflow-auto flex flex-col gap-1">{Object.entries(semantics ?? {}).map(([k, v]) => <li key={k}><span className="mono text-ink">{k}</span> <span className="text-muted">— {v}</span></li>)}</ul>
        </div>
      </div>
    </Modal>
  )
}

// ---------------------------------------------------------------------------------------------
// Approval policy
// ---------------------------------------------------------------------------------------------
const DRIFT = ['identical', 'header_moved', 'columns_added', 'columns_renamed', 'columns_removed', 'types_changed', 'sheets_added', 'sheets_removed']
const RISKS = ['critical', 'high', 'medium', 'low']

function PolicyTab() {
  const qc = useQueryClient()
  const toast = useToast()
  const { data: view } = useQuery({ queryKey: ['policy-settings'], queryFn: api.policySettings })
  const [draft, setDraft] = useState<Policy | null>(null)
  const [newSem, setNewSem] = useState('')
  const p = draft ?? view?.effective
  const edit = (fn: (x: Policy) => void) => { if (!p) return; const next = structuredClone(p); fn(next); setDraft(next) }
  const save = useMutation({ mutationFn: () => api.savePolicy(draft!), onSuccess: () => { setDraft(null); qc.invalidateQueries(); toast.push({ kind: 'ok', title: 'Policy saved', detail: 'Applies to the next proposal; nothing already decided changes.' }) }, onError: (e: Error) => toast.push({ kind: 'bad', title: 'Policy not saved', detail: problemsOf(e) }) })
  const reset = useMutation({ mutationFn: api.resetPolicy, onSuccess: () => { setDraft(null); qc.invalidateQueries(); toast.push({ kind: 'ok', title: 'Back to approval.yaml' }) } })
  if (!view || !p) return <div className="p-10 text-center"><Spinner /></div>
  const aa = p.auto_approve
  const num = (label: string, value: number, onChange: (v: number) => void, opts: { pct?: boolean; step?: number } = {}) => (
    <label key={label} className="flex items-center justify-between gap-3 py-1.5"><span className="text-ink-2">{label}</span>
      <span className="flex items-center gap-1"><input className="input tnum w-24 text-right" type="number" step={opts.step ?? (opts.pct ? 1 : 1)} value={opts.pct ? Math.round(value * 1000) / 10 : value} onChange={(e) => onChange(opts.pct ? Number(e.target.value) / 100 : Number(e.target.value))} />{opts.pct && <span className="text-muted">%</span>}</span>
    </label>
  )
  const flag = (label: string, value: boolean, onChange: (v: boolean) => void) => (
    <label key={label} className="flex items-center gap-2 py-1.5 cursor-pointer"><input type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)} /><span className="text-ink-2">{label}</span></label>
  )
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
        <Badge tone={view.source === 'ui' ? 'signal' : 'neutral'}>{view.source === 'ui' ? 'edited here' : 'from policies/approval.yaml'}</Badge>
        {view.updated_at && <span className="text-muted">last changed by {view.updated_by} · {fmtDate(view.updated_at)}</span>}
        <div className="ml-auto flex gap-2">
          {draft && <button className="btn" onClick={() => setDraft(null)}>Discard</button>}
          {view.source === 'ui' && <button className="btn" disabled={reset.isPending} onClick={() => reset.mutate()}><RotateCcw size={14} /> Reset to file</button>}
          <button className="btn btn-primary" disabled={!draft || save.isPending} onClick={() => save.mutate()}><Save size={14} /> Save policy</button>
        </div>
      </div>
      <div className="grid lg:grid-cols-3 gap-4 text-[13px]">
        <Section title="Auto-approve when…"><div className="px-4 py-2">
          {num('Mapping confidence at least', aa.min_mapping_confidence, (v) => edit((x) => { x.auto_approve.min_mapping_confidence = v }), { pct: true })}
          {num('Critical fields at least', aa.min_field_confidence_critical, (v) => edit((x) => { x.auto_approve.min_field_confidence_critical = v }), { pct: true })}
          {num('High-risk fields at least', aa.min_field_confidence_high, (v) => edit((x) => { x.auto_approve.min_field_confidence_high = v }), { pct: true })}
          {num('Warnings at most', aa.max_warning_findings, (v) => edit((x) => { x.auto_approve.max_warning_findings = Math.round(v) }))}
          {num('Row count change at most', aa.max_row_change_ratio, (v) => edit((x) => { x.auto_approve.max_row_change_ratio = v }), { pct: true })}
          {flag('Customer must have an approved mapping already', aa.require_previous_approval_for_auto, (v) => edit((x) => { x.auto_approve.require_previous_approval_for_auto = v }))}
          {flag('Allow engine errors', aa.allow_errors, (v) => edit((x) => { x.auto_approve.allow_errors = v }))}
          <div className="text-muted mt-2 mb-1">Format changes that may still pass automatically</div>
          <div className="grid grid-cols-2">{DRIFT.map((d) => flag(d.replace(/_/g, ' '), aa.allowed_drift.includes(d), (v) => edit((x) => { x.auto_approve.allowed_drift = v ? [...x.auto_approve.allowed_drift, d] : x.auto_approve.allowed_drift.filter((y) => y !== d) })))}</div>
        </div></Section>
        <Section title="Review queue order (weights)"><div className="px-4 py-2">
          {Object.entries(p.review.priority_weights).map(([k, v]) => num(k.replace(/_/g, ' '), v, (n) => edit((x) => { x.review.priority_weights[k] = Math.round(n) })))}
          <div className="text-muted text-[12px] mt-2">Higher weight = earlier in the Review queue.</div>
        </div></Section>
        <div className="flex flex-col gap-4">
          <Section title="Always block"><div className="px-4 py-2">
            {flag('File unrelated to this customer (wrong file)', !!p.block.on_unrelated_file, (v) => edit((x) => { x.block.on_unrelated_file = v }))}
            {flag('Transformation produced zero rows', !!p.block.on_zero_rows, (v) => edit((x) => { x.block.on_zero_rows = v }))}
            {flag('Records without an identity key', !!p.block.on_missing_identity, (v) => edit((x) => { x.block.on_missing_identity = v }))}
            <div className="text-muted text-[12px] mt-2">A blocked mapping can only be approved by an approver with a written reason.</div>
          </div></Section>
          <Section title="Sandbox: new mapping vs approved"><div className="px-4 py-2">
            {flag('Compare every new mapping with the approved one', p.sandbox.enabled, (v) => edit((x) => { x.sandbox.enabled = v }))}
            {num('Block when records go missing (more than)', p.sandbox.max_lost_records, (v) => edit((x) => { x.sandbox.max_lost_records = Math.round(v) }))}
            {num('Block on extra errors (more than)', p.sandbox.max_new_errors, (v) => edit((x) => { x.sandbox.max_new_errors = Math.round(v) }))}
            {num('Block on extra invalid prices (more than)', p.sandbox.max_new_invalid_prices, (v) => edit((x) => { x.sandbox.max_new_invalid_prices = Math.round(v) }))}
            {num('Block on extra duplicates (more than)', p.sandbox.max_new_duplicates, (v) => edit((x) => { x.sandbox.max_new_duplicates = Math.round(v) }))}
            {num('Changed prices a human must see (more than)', p.sandbox.max_changed_prices_auto, (v) => edit((x) => { x.sandbox.max_changed_prices_auto = Math.round(v) }))}
          </div></Section>
          <Section title="Risk by meaning (overrides the format)"><div className="px-4 py-2">
            {Object.entries(p.risk_overrides).map(([sem, risk]) => (
              <div key={sem} className="flex items-center gap-2 py-1"><span className="mono flex-1 truncate">{sem}</span>
                <select className="input w-28" value={risk} onChange={(e) => edit((x) => { x.risk_overrides[sem] = e.target.value })}>{RISKS.map((r) => <option key={r}>{r}</option>)}</select>
                <button className="btn btn-sm btn-ghost" onClick={() => edit((x) => { delete x.risk_overrides[sem] })}>✕</button>
              </div>
            ))}
            <div className="flex items-center gap-2 pt-2"><input className="input mono flex-1" placeholder="semantic, e.g. promo.price" value={newSem} onChange={(e) => setNewSem(e.target.value)} /><button className="btn btn-sm" disabled={!newSem.trim()} onClick={() => { edit((x) => { x.risk_overrides[newSem.trim()] = 'critical' }); setNewSem('') }}>Add</button></div>
          </div></Section>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------------------------
// Reference tabs
// ---------------------------------------------------------------------------------------------
function DslTab() {
  const { data: dsl } = useQuery({ queryKey: ['dsl'], queryFn: api.dsl })
  return (
    <div className="grid lg:grid-cols-[320px_1fr] gap-4">
      <Section title={`${dsl?.ops.length ?? 0} whitelisted operations`}><div className="p-3 flex flex-wrap gap-1.5">{dsl?.ops.map((o) => <span key={o} className="chip bg-surface-2 border-line mono">{o}</span>)}</div><div className="px-4 pb-4 text-[12px] text-muted leading-relaxed">The AI can only compose these. No generated code is ever executed; every op has one deterministic compiler branch and tests.</div></Section>
      <Section title="DSL JSON Schema (what the LLM must emit)"><Json value={dsl?.json_schema} className="rounded-none border-0 max-h-[70vh]" /></Section>
    </div>
  )
}

function AuditTab() {
  const { data: audit } = useQuery({ queryKey: ['audit'], queryFn: () => api.audit(200) })
  return (
    <Section title="Last 200 events">
      <table className="table"><thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Entity</th><th>Details</th></tr></thead>
        <tbody>{audit?.map((a) => <tr key={a.id}><td className="text-muted whitespace-nowrap">{fmtDate(a.at)}</td><td>{a.actor}</td><td className="mono">{a.action}</td><td className="mono text-muted">{a.entity_id}</td><td className="text-muted text-[12px] truncate max-w-[420px]">{JSON.stringify(a.details)}</td></tr>)}</tbody></table>
    </Section>
  )
}

function SystemTab() {
  const { data: dash } = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard })
  return (
    <Section title="Runtime"><div className="px-4 py-2">
      <KV k="AI provider" v={dash?.llm.provider === 'mock' ? 'offline (deterministic mapper only) — set it in the AI tab' : `${dash?.llm.provider} · ${dash?.llm.model}`} />
      <KV k="AI calls / cost" v={`${dash?.llm.calls ?? 0} · $${(dash?.llm.cost_usd ?? 0).toFixed(4)}`} />
      <KV k="Customers" v={dash?.customers} /><KV k="Mapping versions" v={dash?.mapping_versions} /><KV k="Failed jobs" v={dash?.failed_jobs} />
    </div></Section>
  )
}
