import { Localized } from '../lib/locale'
import { usePreferences } from '../lib/preferences'
import { providerName } from '../lib/providerName'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, Brain, Check, CodeXml, FileJson, FlaskConical, ImagePlus, KeyRound, Monitor, Plug, Plus, RotateCcw, Save, Scale, ScrollText, Star, Trash2, Upload, Wifi, type LucideIcon } from 'lucide-react'
import { api, ApiError, type AISettings, type AiProbe, type BackupKey, type Policy, type SchemaPreview } from '../lib/api'
import { PageHeader, Section, Badge, RiskDot, Json, KV, Modal, Spinner, Stat } from '../components/ui'
import { cn, fmtDate } from '../lib/utils'
import { useToast } from '../lib/toast'

const TABS = ['ai', 'formats', 'policy', 'learning', 'dsl', 'audit', 'system'] as const
type Tab = (typeof TABS)[number]

const SECTIONS: { id: Tab; label: string; hint: string; icon: LucideIcon }[] = [
  { id: 'ai', label: 'AI', hint: 'เชื่อม AI, key สำรอง, เลือก model', icon: Plug },
  { id: 'formats', label: 'Target formats', hint: 'อัป Target JSON และตั้งค่าเริ่มต้น', icon: FileJson },
  { id: 'policy', label: 'Approval policy', hint: 'เกณฑ์ approve อัตโนมัติ และเกณฑ์ความผิดปกติ', icon: Scale },
  { id: 'learning', label: 'What FORMA learned', hint: 'สิ่งที่ระบบเรียนจากการแก้ของคุณ และแนวโน้มการแก้ต่อรอบ', icon: Brain },
  { id: 'dsl', label: 'Mapping DSL', hint: 'รายการคำสั่งแปลงข้อมูลที่ใช้ได้', icon: CodeXml },
  { id: 'audit', label: 'Audit log', hint: 'ใครทำอะไร เมื่อไร', icon: ScrollText },
  { id: 'system', label: 'System', hint: 'การตั้งค่าเครื่อง server', icon: Monitor },
]

/** Admins change AI, the approval policy and target formats here — validated, audited, applied without a restart. */
export function SettingsPage() {
  const [params, setParams] = useSearchParams()
  const tab: Tab = (TABS as readonly string[]).includes(params.get('tab') ?? '') ? (params.get('tab') as Tab) : 'ai'
  const setTab = (t: Tab) => setParams({ tab: t }, { replace: true })
  return (
    <>
      <PageHeader title="Settings" subtitle="AI provider, target formats and the approval policy — edited here, checked before saving, recorded in the audit log, applied without a restart." hint="ตั้งค่า AI (key หลัก/สำรอง, model) · Target format ที่ระบบปลายทางต้องการ · กฎว่าเมื่อไรระบบ approve เองได้" />
      <div className="grid lg:grid-cols-[230px_minmax(0,1fr)] gap-6 items-start">
        <nav aria-label="Settings sections" className="flex flex-wrap lg:flex-col gap-1">
          {SECTIONS.map(({ id, label, hint, icon: Icon }) => <button key={id} onClick={() => setTab(id)} title={hint} aria-current={tab === id ? 'page' : undefined} className={cn('flex items-center gap-3 min-h-11 rounded-lg px-3 text-left text-[14px] font-medium hover:bg-surface-2', tab === id ? 'bg-signal-soft text-navy font-semibold' : 'text-ink-2')}><Icon size={20} className="shrink-0" aria-hidden="true" /><Localized>{label}</Localized></button>)}
        </nav>
        <div className="min-w-0">
        {tab === 'ai' && <AITab />}
        {tab === 'formats' && <FormatsTab />}
        {tab === 'policy' && <PolicyTab />}
        {tab === 'learning' && <LearningTab />}
        {tab === 'dsl' && <DslTab />}
        {tab === 'audit' && <AuditTab />}
        {tab === 'system' && <SystemTab />}
        </div>
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
  const { t } = usePreferences()
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
      <ConnectCard key={`${ai.provider}:${ai.base_url}:${ai.model}:${ai.updated_at}`} ai={ai} />
      <AssistantAbilities ai={ai} />
      <BackupKeysCard ai={ai} />
      <div><button className="btn btn-ghost btn-sm whitespace-normal text-left h-auto max-w-full" onClick={() => setAdvanced(!advanced)}>{advanced ? t('Hide advanced settings', 'ซ่อนการตั้งค่าขั้นสูง') : t('Show advanced settings — provider, model, gateway URL, timeout', 'การตั้งค่าขั้นสูง — ผู้ให้บริการ โมเดล Gateway และเวลารอ')}</button></div>
      {advanced && (
    <div className="grid lg:grid-cols-[1fr_340px] gap-4">
      <Section title="Advanced settings" actions={ai.offline ? <Badge tone="warn">offline · deterministic mapper only</Badge> : <Badge tone="ok"><Plug size={12} /> {ai.active_provider} · {ai.model}</Badge>}>
        <div className="p-4 grid sm:grid-cols-2 gap-4 text-[13px]">
          <label className="flex flex-col gap-1.5"><span className="text-muted">Provider</span>
            <select className="input" value={f.provider} onChange={(e) => { const p = e.target.value; const known = ai.models[p] ?? []; setDraft({ ...f, provider: p, model: !known.length || known.includes(f.model) ? f.model : known[0] }) }}>{ai.providers.map((p) => <option key={p} value={p}>{p === 'mock' ? 'off (mock)' : p}</option>)}</select>
          </label>
          <label className="flex flex-col gap-1.5"><span className="text-muted">Model</span><ModelPicker provider={f.provider} models={ai.models} value={f.model} onChange={(m) => set('model', m)} /></label>
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
          {dirty && <button className="btn" onClick={() => { setDraft(null); setKey('') }}><Localized>Discard</Localized></button>}
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
  const { t } = usePreferences()
  const qc = useQueryClient()
  const [editing, setEditing] = useState(ai.offline)
  const [key, setKey] = useState('')
  const [provider, setProvider] = useState(ai.provider === 'mock' ? 'openai' : ai.provider)
  const [url, setUrl] = useState(ai.base_url ?? (ai.provider === 'anthropic' ? 'https://api.anthropic.com' : 'https://api.openai.com/v1'))
  const [model, setModel] = useState(ai.provider === 'mock' ? '' : ai.model)
  const discovery = useMutation({ mutationFn: () => api.discoverModels({ provider, api_key: key.trim() || undefined, base_url: url.trim() || undefined }) })
  const connect = useMutation({ mutationFn: () => api.connectAi(key.trim(), provider, model.trim(), url.trim()), onSuccess: () => { setKey(''); setEditing(false); qc.invalidateQueries() } })
  const test = useMutation({ mutationFn: api.testAi })
  const busy = discovery.isPending || connect.isPending
  const providerChanged = (value: string) => { setProvider(value); setUrl(value === 'anthropic' ? 'https://api.anthropic.com' : value === 'openai' ? 'https://api.openai.com/v1' : ''); setModel(''); discovery.reset(); connect.reset() }
  return <Section title={t('AI connection', 'การเชื่อมต่อ AI')} actions={<Badge tone={ai.offline ? 'warn' : 'ok'}>{ai.offline ? t('AI is off', 'ยังไม่เปิด AI') : t('Connection configured', 'ตั้งค่าการเชื่อมต่อแล้ว')}</Badge>}>
    {!editing ? <div className="p-5 space-y-4"><div className="flex items-start justify-between flex-wrap gap-4"><div><div className="font-semibold text-navy text-lg">{providerName(ai.provider, ai.base_url)}</div><div className="text-sm mt-1">{ai.active_model ?? ai.model}</div><div className="text-xs text-muted mt-2 break-all">{ai.base_url || (ai.provider === 'anthropic' ? 'https://api.anthropic.com' : 'https://api.openai.com/v1')}</div></div><div className="flex gap-2 flex-wrap"><button className="btn" onClick={() => setEditing(true)}>{t('Edit connection', 'แก้ไขการเชื่อมต่อ')}</button><button className="btn" disabled={test.isPending} onClick={() => test.mutate()}>{test.isPending ? t('Testing…', 'กำลังทดสอบ…') : t('Test connection', 'ทดสอบการเชื่อมต่อ')}</button></div></div><p className="text-xs text-muted">{t('Your key is saved securely. The assistant and file mapping use this connection.', 'บันทึกคีย์ไว้แล้ว ผู้ช่วยและการจับคู่ข้อมูลใช้การเชื่อมต่อนี้')}</p>{ai.warning && <p className="text-warn text-sm">{ai.warning}</p>}{test.data && <p role="status" className={test.data.ok ? 'text-ok text-sm' : 'text-bad text-sm'}>{test.data.ok ? t('Connection test passed', 'ทดสอบการเชื่อมต่อผ่านแล้ว') : t('Connection test failed. Edit the connection to check its settings.', 'ทดสอบไม่ผ่าน เปิดแก้ไขการเชื่อมต่อเพื่อตรวจการตั้งค่า')}</p>}{test.error && <p role="alert" className="text-bad text-sm">{t('Could not test the connection.', 'ยังทดสอบการเชื่อมต่อไม่ได้')}</p>}</div> : <>
    <form className="p-5 space-y-4" onSubmit={e => { e.preventDefault(); connect.mutate() }}>
      <p className="text-sm text-ink-2 leading-6">{t('Choose your provider, endpoint and model. Direct OpenAI and Anthropic connections use the standard endpoint below. For another gateway, use its OpenAI-compatible base URL.', 'เลือกผู้ให้บริการ Endpoint และโมเดล ถ้าใช้ OpenAI หรือ Anthropic โดยตรงใช้ URL มาตรฐานด้านล่างได้เลย ถ้าใช้ gateway อื่นให้ใส่ Base URL ที่รองรับ OpenAI')}</p>
      <fieldset disabled={busy} className="grid sm:grid-cols-2 gap-4 min-w-0">
        <label className="grid gap-1.5 text-sm">{t('Provider', 'ผู้ให้บริการ')}<select className="input" value={provider} onChange={e => providerChanged(e.target.value)}><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option><option value="openai_compatible">OpenAI-compatible / Gateway</option></select></label>
        <label className="grid gap-1.5 text-sm">{t('Base endpoint URL', 'Base endpoint URL')}<input className="input mono min-w-0" type="url" value={url} required onChange={e => { setUrl(e.target.value); discovery.reset() }} placeholder="https://your-gateway/v1" /></label>
        <label className="grid gap-1.5 text-sm sm:col-span-2">{t('API key', 'API key')}<input className="input mono" type="password" autoComplete="off" value={key} onChange={e => { setKey(e.target.value); discovery.reset() }} placeholder={ai.api_key_set ? t('Leave blank to reuse the saved key for the same endpoint', 'เว้นว่างเพื่อใช้คีย์ที่บันทึกไว้กับ endpoint เดิม') : t('Paste API key', 'วาง API key')} /></label>
        <div className="sm:col-span-2 flex items-center gap-3 flex-wrap"><button type="button" className="btn" onClick={() => discovery.mutate()} disabled={!url.trim() || (!key.trim() && !ai.api_key_set)}>{discovery.isPending ? t('Loading models…', 'กำลังดึงโมเดล…') : t('Load models from API', 'ดึงรายชื่อโมเดลจาก API')}</button><span className="text-xs text-muted">{t('This reads the catalogue without saving your settings.', 'อ่านรายชื่อโมเดล ยังไม่บันทึกการตั้งค่า')}</span></div>
        {!!discovery.data?.models.length && <label className="grid gap-1.5 text-sm sm:col-span-2">{t('Models reported by this API', 'โมเดลที่ API นี้แจ้งว่ามีให้ใช้')}<select className="input mono" value={discovery.data.models.includes(model) ? model : ''} onChange={e => setModel(e.target.value)}><option value="">{t('Choose a model…', 'เลือกโมเดล…')}</option>{discovery.data.models.map(m => <option key={m} value={m}>{m}</option>)}</select></label>}
        <label className="grid gap-1.5 text-sm sm:col-span-2">{t('Model ID (you can also type it)', 'Model ID (พิมพ์เองได้)')}<input className="input mono" value={model} required maxLength={200} onChange={e => setModel(e.target.value)} placeholder={t('Choose above or enter the exact model ID', 'เลือกด้านบนหรือใส่ชื่อโมเดลให้ตรงกับผู้ให้บริการ')} /></label>
      </fieldset>
      {discovery.data && <p role="status" className="text-sm text-muted">{discovery.data.source === 'provider' ? t(`${discovery.data.models.length} models loaded. Availability does not guarantee JSON/chat support; Connect checks the selected model.`, `ดึงได้ ${discovery.data.models.length} โมเดล รายชื่อยังไม่ยืนยันว่ารองรับงาน JSON/แชต ปุ่มเชื่อมต่อจะทดสอบโมเดลที่เลือกอีกครั้ง`) : t('This API could not return a model list. Enter a model ID manually and connect.', 'API นี้ยังดึงรายชื่อโมเดลไม่ได้ ใส่ Model ID เองแล้วทดสอบเชื่อมต่อได้')}{discovery.data.truncated && t(' More models may exist; enter an exact ID if missing.', ' อาจมีโมเดลเพิ่มเติม หากไม่พบให้พิมพ์ ID เอง')}</p>}
      {(discovery.error || connect.error) && <p role="alert" className="text-bad text-sm">{(discovery.error || connect.error)?.message}</p>}
      <div className="flex gap-3 items-center flex-wrap"><button className="btn btn-primary" disabled={busy || !model.trim() || !url.trim() || (!key.trim() && !ai.api_key_set)}><Wifi size={15} />{connect.isPending ? t('Testing connection…', 'กำลังทดสอบ…') : t('Test & save connection', 'ทดสอบและบันทึกการเชื่อมต่อ')}</button>{!ai.offline && <button type="button" className="btn" disabled={busy} onClick={() => { setKey(''); setProvider(ai.provider); setUrl(ai.base_url || (ai.provider === 'anthropic' ? 'https://api.anthropic.com' : 'https://api.openai.com/v1')); setModel(ai.model); discovery.reset(); connect.reset(); setEditing(false) }}>{t('Cancel', 'ยกเลิก')}</button>}<span className="text-xs text-muted">{t('Makes one small model request. Saved only when successful.', 'เรียกโมเดลทดสอบสั้น ๆ หนึ่งครั้ง บันทึกเมื่อทดสอบผ่านเท่านั้น')}</span></div>
    </form></>}
  </Section>
}

/** Known models of the provider in a list, "Other model…" for any name (gateways, new models).
 *  ``saveOnBlur``: a typed name is sent when the field is left (or Enter), not on every keystroke. */
function ModelPicker({ provider, models, value, onChange, allowDefault, disabled, saveOnBlur }: { provider: string; models: Record<string, string[]>; value: string; onChange: (m: string) => void; allowDefault?: boolean; disabled?: boolean; saveOnBlur?: boolean }) {
  const known = models[provider] ?? []
  const [typing, setTyping] = useState(false)
  const [draft, setDraft] = useState<string | null>(null)
  if (typing || !known.length || (value !== '' && !known.includes(value))) {
    const commit = () => { if (draft !== null) { onChange(draft.trim()); setDraft(null) } }
    return (
      <span className="flex gap-1">
        <input className="input mono flex-1 min-w-0" value={saveOnBlur ? draft ?? value : value} disabled={disabled} placeholder="model name"
          onChange={(e) => (saveOnBlur ? setDraft(e.target.value) : onChange(e.target.value))}
          onBlur={commit} onKeyDown={(e) => { if (e.key === 'Enter') commit() }} />
        {known.length > 0 && <button type="button" className="btn btn-ghost btn-sm" onClick={() => { setTyping(false); onChange(allowDefault ? '' : known[0]) }}>List</button>}
      </span>
    )
  }
  return (
    <select className="input mono w-full" value={value} disabled={disabled} onChange={(e) => { if (e.target.value === '__other') { setTyping(true); onChange('') } else onChange(e.target.value) }}>
      {allowDefault && <option value="">Default model</option>}
      {known.map((m) => <option key={m} value={m}>{m}</option>)}
      <option value="__other">Other model…</option>
    </select>
  )
}

const PROVIDER_LABEL: Record<string, string> = { anthropic: 'Anthropic', openai: 'OpenAI', openai_compatible: 'Gateway' }

/** What the assistant can do beyond text: read images (ADR-049) and run code (ADR-048). */
function AssistantAbilities({ ai }: { ai: AISettings }) {
  const { t } = usePreferences()
  const qc = useQueryClient()
  const toast = useToast()
  const { data: status } = useQuery({ queryKey: ['assistant-status'], queryFn: api.assistantStatus })
  const test = useMutation({
    mutationFn: api.testVision,
    onSuccess: (r) => { qc.setQueryData(['ai-settings'], r.ai); void qc.invalidateQueries({ queryKey: ['assistant-status'] }); toast.push({ kind: r.ai.supports_vision ? 'ok' : 'bad', title: r.ai.supports_vision ? t('A connected model reads images', 'มีโมเดลที่อ่านรูปได้') : t('No connected model reads images', 'ยังไม่มีโมเดลที่อ่านรูปได้'), detail: r.message ?? r.results.map((x) => `${x.model}: ${x.message}`).join(' · ') }) },
    onError: (e: Error) => toast.push({ kind: 'bad', title: t('Could not test', 'ทดสอบไม่สำเร็จ'), detail: e.message }),
  })
  const seen = (v: boolean | null) => v === null ? <Badge tone="warn">{t('Not tested', 'ยังไม่ได้ทดสอบ')}</Badge> : v ? <Badge tone="ok">{t('Reads images', 'อ่านรูปได้')}</Badge> : <Badge tone="neutral">{t('Cannot read images', 'อ่านรูปไม่ได้')}</Badge>
  return (
    <Section title={t('What the assistant can do', 'ความสามารถของผู้ช่วย')}>
      <div className="p-4 flex flex-col gap-4 text-[13.5px]">
        <div className="flex flex-wrap items-start gap-3">
          <ImagePlus size={20} className="shrink-0 mt-0.5 text-navy" aria-hidden="true" />
          <div className="flex-1 min-w-[260px]">
            <p className="font-medium">{t('Read images', 'อ่านรูป')}</p>
            <p className="text-[12.5px] text-muted mt-0.5">{t('The test sends one small coloured picture to each connected model. It contains no customer data.', 'การทดสอบจะส่งรูปสีเล็กๆ ไปที่โมเดลทุกตัวที่เชื่อมอยู่ ไม่มีข้อมูลลูกค้า')}</p>
            <ul className="mt-2 flex flex-col gap-1.5">
              <li className="flex flex-wrap items-center gap-2"><span className="mono text-[12.5px]">{ai.active_model}</span>{seen(ai.vision)}</li>
              {ai.backups.map((b) => <li key={b.id} className="flex flex-wrap items-center gap-2"><span className="mono text-[12.5px]">{b.model}</span>{seen(b.vision)}</li>)}
            </ul>
          </div>
          <button className="btn" disabled={ai.offline || test.isPending} onClick={() => test.mutate()}>{test.isPending ? t('Testing…', 'กำลังทดสอบ…') : t('Test image reading', 'ทดสอบการอ่านรูป')}</button>
        </div>
        <div className="flex flex-wrap items-start gap-3 border-t border-line pt-4">
          <FlaskConical size={20} className="shrink-0 mt-0.5 text-navy" aria-hidden="true" />
          <div className="flex-1 min-w-[260px]">
            <p className="font-medium">{t('Run code in a sandbox', 'รันโค้ดในห้องทดลอง')}</p>
            <p className="text-[12.5px] text-muted mt-0.5">{t('Python in a separate box with no internet and no passwords. It works on copies and cannot change FORMA data.', 'Python ในกล่องแยก ไม่มีอินเทอร์เน็ตและไม่มีรหัสผ่าน ทำงานกับสำเนา แก้ข้อมูลใน FORMA ไม่ได้')}</p>
          </div>
          {status?.code_sandbox ? <Badge tone="ok">{t('Ready', 'พร้อมใช้')}</Badge> : <Badge tone="warn">{t('Not available', 'ยังไม่พร้อม')}</Badge>}
        </div>
      </div>
    </Section>
  )
}

/** Backup keys (ADR-025): tried in order when the main key fails — error, rate limit, no credit. */
function BackupKeysCard({ ai }: { ai: AISettings }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [adding, setAdding] = useState(false)
  const [key, setKey] = useState('')
  const [provider, setProvider] = useState('')
  const [model, setModel] = useState('')
  const [url, setUrl] = useState('')
  const [probes, setProbes] = useState<Record<string, AiProbe>>({})
  const k = key.trim()
  const detected = k.startsWith('sk-ant-') ? 'anthropic' : k.startsWith('sk-') ? 'openai' : null
  const chosen = detected ?? provider
  const done = (title: string) => (r: AISettings) => { qc.setQueryData(['ai-settings'], r); qc.invalidateQueries(); toast.push({ kind: 'ok', title }) }
  const failed = (e: Error) => toast.push({ kind: 'bad', title: 'Not changed', detail: e.message })
  const add = useMutation({
    mutationFn: () => api.addBackupKey({ api_key: k, ...(detected ? {} : { provider }), ...(model.trim() ? { model: model.trim() } : {}), ...(url.trim() ? { base_url: url.trim() } : {}) }),
    onSuccess: (r) => { setKey(''); setModel(''); setUrl(''); setProvider(''); setAdding(false); qc.setQueryData(['ai-settings'], r.ai); qc.invalidateQueries(); toast.push({ kind: 'ok', title: 'Backup key added', detail: `${r.test.provider} · ${r.test.model} · answered in ${r.test.latency_ms} ms` }) },
  })
  const update = useMutation({ mutationFn: (v: { id: string; model: string }) => api.updateBackupKey(v.id, { model: v.model }), onSuccess: done('Backup model changed'), onError: failed })
  const remove = useMutation({ mutationFn: (id: string) => api.removeBackupKey(id), onSuccess: done('Backup key removed'), onError: failed })
  const order = useMutation({ mutationFn: (ids: string[]) => api.orderBackupKeys(ids), onSuccess: done('Order saved'), onError: failed })
  const test = useMutation({
    mutationFn: api.testAi,
    onSuccess: (r) => setProbes(Object.fromEntries([[r.route ?? 'primary', r], ...(r.backups ?? []).map((b) => [b.id, b] as const)])),
    onError: failed,
  })
  const move = (i: number, d: -1 | 1) => { const ids = ai.backups.map((b) => b.id); [ids[i], ids[i + d]] = [ids[i + d], ids[i]]; order.mutate(ids) }
  const ready = !!k && !!chosen && (chosen !== 'openai_compatible' || (!!url.trim() && !!model.trim()))
  const status = (b: BackupKey) => {
    const pr = probes[b.id]
    if (pr) return <Badge tone={pr.ok ? 'ok' : 'bad'}>{pr.ok ? `ok · ${pr.latency_ms} ms` : pr.message.slice(0, 80)}</Badge>
    if (b.warning) return <Badge tone="bad">{b.warning}</Badge>
    if (b.circuit_open) return <Badge tone="warn">resting after failures</Badge>
    if (ai.active_route === b.id) return <Badge tone="signal">answering (no main key)</Badge>
    return <Badge tone="neutral">standby</Badge>
  }
  return (
    <Section title="Backup keys" actions={<div className="flex gap-2">
      {ai.backups.length > 0 && <button className="btn btn-sm" disabled={test.isPending} onClick={() => test.mutate()}><Wifi size={13} /> {test.isPending ? 'Testing…' : 'Test all keys'}</button>}
      {!adding && ai.backups.length < ai.max_backups && <button className="btn btn-sm btn-primary" onClick={() => setAdding(true)}><Plus size={13} /> Add backup key</button>}
    </div>}>
      <div className="px-4 pt-3 pb-1 text-[12.5px] text-ink-2">When the main key fails — an error, a rate limit, no credit left — FORMA tries these keys in order, each with its own model. A key that keeps failing rests for a while and the next one answers; if every key fails, the deterministic mapper takes over and the round goes to review.</div>
      {probes.primary && <div className="px-4 pt-2 text-[12.5px] flex items-center gap-2"><span className="text-muted">Main key</span><Badge tone={probes.primary.ok ? 'ok' : 'bad'}>{probes.primary.ok ? `ok · ${probes.primary.latency_ms} ms` : probes.primary.message.slice(0, 80)}</Badge></div>}
      {ai.backups.length > 0 ? (
        <table className="table mt-2">
          <thead><tr><th className="w-10">#</th><th>Provider</th><th className="w-64">Model</th><th>Key</th><th>Status</th><th className="w-36" /></tr></thead>
          <tbody>{ai.backups.map((b, i) => (
            <tr key={b.id}>
              <td className="tnum text-muted">{i + 1}</td>
              <td>{PROVIDER_LABEL[b.provider] ?? b.provider}{b.base_url && <div className="text-[11.5px] text-muted mono truncate max-w-[220px]">{b.base_url}</div>}</td>
              <td><ModelPicker provider={b.provider} models={ai.models} value={b.model} disabled={update.isPending} saveOnBlur onChange={(m) => m.trim() && m !== b.model && update.mutate({ id: b.id, model: m })} /></td>
              <td className="mono text-muted">{b.api_key_hint ?? '—'}</td>
              <td>{status(b)}</td>
              <td><div className="flex justify-end gap-1">
                <button className="btn btn-ghost btn-sm" title="Try earlier" disabled={i === 0 || order.isPending} onClick={() => move(i, -1)}><ArrowUp size={13} /></button>
                <button className="btn btn-ghost btn-sm" title="Try later" disabled={i === ai.backups.length - 1 || order.isPending} onClick={() => move(i, 1)}><ArrowDown size={13} /></button>
                <button className="btn btn-ghost btn-sm" title="Remove" disabled={remove.isPending} onClick={() => remove.mutate(b.id)}><Trash2 size={13} /></button>
              </div></td>
            </tr>
          ))}</tbody>
        </table>
      ) : !adding && <div className="px-4 py-3 text-[12.5px] text-muted">No backup key — when the main key fails, the deterministic mapper takes over.</div>}
      {adding && (
        <div className="m-4 p-3 rounded-[var(--radius-md)] border border-line flex flex-col gap-3 text-[13px]">
          <div className="flex flex-wrap items-center gap-2">
            <input className="input mono flex-1 min-w-[260px]" type="password" autoComplete="off" autoFocus value={key} onChange={(e) => setKey(e.target.value)} placeholder="Paste the backup API key" />
            {k && (detected
              ? <Badge tone="signal">{PROVIDER_LABEL[detected]} key</Badge>
              : <select className="input w-52" value={provider} onChange={(e) => { setProvider(e.target.value); setModel('') }}><option value="">Which provider is this?</option><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option><option value="openai_compatible">Gateway</option></select>)}
          </div>
          {chosen && (
            <div className="grid sm:grid-cols-2 gap-3">
              <label className="flex flex-col gap-1.5"><span className="text-muted">Model</span><ModelPicker provider={chosen} models={ai.models} value={model} allowDefault={chosen !== 'openai_compatible'} onChange={setModel} /></label>
              {chosen === 'openai_compatible' && <label className="flex flex-col gap-1.5"><span className="text-muted">Gateway URL</span><input className="input mono" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://your-gateway/v1" /></label>}
            </div>
          )}
          <div className="flex items-center gap-2">
            <button className="btn btn-primary" disabled={!ready || add.isPending} onClick={() => add.mutate()}><Wifi size={14} /> {add.isPending ? 'Checking the key…' : 'Test & add'}</button>
            <button className="btn" onClick={() => { setAdding(false); setKey(''); add.reset() }}><Localized>Cancel</Localized></button>
            <span className="text-[12px] text-muted">The key is tried with one tiny call and saved encrypted only if it answers.</span>
          </div>
          {add.error && <div className="rounded-[var(--radius-md)] bg-bad-soft text-bad px-3 py-2 text-[12.5px]">{add.error.message}</div>}
        </div>
      )}
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
          <td className="mono">{f.pointer}{f.is_collection && <Badge tone="neutral" className="ml-2 min-h-6 text-[12px]">collection</Badge>}</td>
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
      <button className="btn" onClick={onClose}><Localized>Cancel</Localized></button>
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

function CalibrationSection() {
  const { prefs, t } = usePreferences(), qc = useQueryClient(), toast = useToast()
  const lang = prefs.language
  const q = useQuery({ queryKey: ['calibration'], queryFn: api.calibration })
  const apply = useMutation({ mutationFn: (b: { id: string; digest: string }) => api.applyProposal(b), onSuccess: () => { void qc.invalidateQueries(); toast.push({ kind: 'ok', title: t('Applied', 'ปรับแล้ว'), detail: t('Applies to the next proposal; the report will show whether it was right.', 'มีผลกับข้อเสนอถัดไป รายงานจะบอกว่าถูกหรือไม่') }) }, onError: (e: Error) => toast.push({ kind: 'bad', title: t('Not applied', 'ปรับไม่ได้'), detail: e.message }) })
  const d = q.data
  if (q.isError) return <Section title={t('How often FORMA was right', 'ระบบตัดสินถูกบ่อยแค่ไหน')} className="mb-5"><div className="p-5 text-sm text-muted">{q.error.message}</div></Section>
  const rules = d ? Object.entries(d.report.per_rule) : []
  const dec = d?.report.per_decision
  return <>
    <Section title={t('What the policy could allow', 'สิ่งที่กฎยอมให้ได้มากขึ้น')} hint="ข้อเสนอจากผลลัพธ์จริง: อะไรที่คนอนุมัติตามเดิมทุกครั้ง ระบบเสนอให้ผ่านเอง คุณเป็นคนกดรับ" className="mb-5">
      {d?.proposals.length ? <ul className="divide-y divide-line">{d.proposals.map((x) => <li key={x.id} className="px-4 py-3 flex flex-wrap items-start gap-3"><div className="flex-1 min-w-[240px]"><div className="font-medium">{x.title[lang]}</div><div className="text-sm text-ink-2 mt-1">{x.why[lang]}</div><div className="text-xs text-muted mt-1 mono">{x.scope === 'customer' ? `${x.customer_code} · ` : ''}{x.path}: {JSON.stringify(x.current)} → {JSON.stringify(x.value)} · {t(`would have saved ${x.saves} reviews`, `จะลด review ได้ ${x.saves} รายการ`)}</div></div><button className="btn btn-primary btn-sm" disabled={apply.isPending} onClick={() => apply.mutate({ id: x.id, digest: x.digest })}><Check size={13} />{t('Apply', 'ปรับตามนี้')}</button></li>)}</ul>
        : <div className="p-5 text-sm text-muted">{q.isPending ? t('Loading…', 'กำลังโหลด…') : t('No proposal yet: the evidence is too thin (3 or more matching outcomes are needed) or the policy already allows it.', 'ยังไม่มีข้อเสนอ: หลักฐานยังน้อย (ต้องมีผลลัพธ์แบบเดียวกันอย่างน้อย 3 ครั้ง) หรือกฎยอมให้อยู่แล้ว')}</div>}
    </Section>
    <Section title={t('How often FORMA was right', 'ระบบตัดสินถูกบ่อยแค่ไหน')} hint="นับจากสิ่งที่คนทำหลังการตัดสิน: อนุมัติตามเดิม แก้ ปฏิเสธ หรือต้องแก้ข้อมูลทีหลัง" className="mb-5">
      {d ? <div className="px-4 py-3 space-y-4">
        <div className="grid sm:grid-cols-2 gap-3">{(['auto', 'review'] as const).map((k) => { const c = dec?.[k]; return <div key={k} className="rounded-lg border border-line p-3"><div className="text-xs text-muted">{k === 'auto' ? t('Automatic approvals', 'อนุมัติอัตโนมัติ') : t('Reviews', 'ส่งให้คนตรวจ')}</div><div className="text-2xl font-semibold mt-1">{c ? `${Math.round((c.precision ?? 0) * 100)}%` : '–'} <span className="text-sm font-normal text-muted">{c ? t(`right of ${c.n}`, `ถูก จาก ${c.n}`) : t('no decisions yet', 'ยังไม่มีการตัดสิน')}</span></div>{c && <div className="text-xs text-muted mt-1">{k === 'auto' ? t(`${c.auto_then_edited ?? 0} edited later · ${c.auto_then_rolled_back ?? 0} rolled back`, `แก้ทีหลัง ${c.auto_then_edited ?? 0} · ย้อนกลับ ${c.auto_then_rolled_back ?? 0}`) : t(`${c.accepted_unchanged ?? 0} accepted as proposed · ${c.corrected ?? 0} corrected · ${c.rejected ?? 0} rejected`, `รับตามเดิม ${c.accepted_unchanged ?? 0} · แก้ ${c.corrected ?? 0} · ปฏิเสธ ${c.rejected ?? 0}`)}</div>}</div> })}</div>
        {rules.length > 0 && <table className="table"><thead><tr><th>{t('Verdict rule', 'กฎที่ตัดสิน')}</th><th className="text-right">{t('Decisions', 'ครั้ง')}</th><th className="text-right">{t('Right', 'ถูก')}</th><th className="text-right">{t('Corrected', 'แก้')}</th><th className="text-right">{t('Rejected', 'ปฏิเสธ')}</th><th className="text-right">{t('Precision', 'ความแม่น')}</th></tr></thead><tbody>{rules.map(([rule, c]) => <tr key={rule}><td className="mono">{rule}</td><td className="text-right tnum">{c.n}</td><td className="text-right tnum">{c.right}</td><td className="text-right tnum">{c.corrected ?? 0}</td><td className="text-right tnum">{c.rejected ?? 0}</td><td className="text-right tnum font-semibold">{c.precision == null ? '–' : `${Math.round(c.precision * 100)}%`}</td></tr>)}</tbody></table>}
        <p className="text-xs text-muted">{t(`Since ${d.report.since} · ${d.report.decisions} decisions`, `ตั้งแต่ ${d.report.since} · ${d.report.decisions} การตัดสิน`)}</p>
      </div> : <div className="p-5 text-sm text-muted">{t('Loading…', 'กำลังโหลด…')}</div>}
    </Section>
  </>
}

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
      <CalibrationSection />
      <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
        <Badge tone={view.source === 'ui' ? 'signal' : 'neutral'}>{view.source === 'ui' ? 'edited here' : 'from policies/approval.yaml'}</Badge>
        {view.updated_at && <span className="text-muted">last changed by {view.updated_by} · {fmtDate(view.updated_at)}</span>}
        <div className="ml-auto flex gap-2">
          {draft && <button className="btn" onClick={() => setDraft(null)}><Localized>Discard</Localized></button>}
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
          <div className="mt-3">{num('High risk when this share of records changes', p.review.high_impact_ratio ?? 0.2, (v) => edit((x) => { x.review.high_impact_ratio = v }), { pct: true })}</div>
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
            {flag('Trust the data check: a moved layout whose values are identical on the same SKUs needs no review', !!p.sandbox.trust_data_checks, (v) => edit((x) => { x.sandbox.trust_data_checks = v }))}
          </div></Section>
          {p.anomaly && <Section title="Anomalies: what counts as unusual"><div className="px-4 py-2">
            {num('Price moves more than (%) vs last round', p.anomaly.max_price_change_pct, (v) => edit((x) => { x.anomaly!.max_price_change_pct = v }))}
            {num('Discount above (%)', p.anomaly.max_discount_pct, (v) => edit((x) => { x.anomaly!.max_discount_pct = v }))}
            {num('Stock falls more than (%)', p.anomaly.max_stock_drop_pct, (v) => edit((x) => { x.anomaly!.max_stock_drop_pct = v }))}
            {num('SKUs gone since last round (%)', p.anomaly.max_removed_pct, (v) => edit((x) => { x.anomaly!.max_removed_pct = v }))}
            {num('Record count vs usual (± %)', p.anomaly.row_count_tolerance_pct, (v) => edit((x) => { x.anomaly!.row_count_tolerance_pct = v }))}
            {num('Price outlier in its category (robust deviations)', p.anomaly.outlier_mad, (v) => edit((x) => { x.anomaly!.outlier_mad = v }))}
            {num('Rounds in the usual-size baseline', p.anomaly.history_rounds, (v) => edit((x) => { x.anomaly!.history_rounds = Math.round(v) }))}
            {flag('Approving data with high-severity anomalies needs an acknowledgement', p.anomaly.block_approval_on_high, (v) => edit((x) => { x.anomaly!.block_approval_on_high = v }))}
          </div></Section>}
          {p.publish && <Section title="Automatic publishing (bulk upload)" hint="เมื่อไรระบบ approve ข้อมูลและออก JSON เองได้ โดยไม่ต้องรอคน"><div className="px-4 py-2">
            {flag('Publish automatically when every check passes', p.publish.enabled, (v) => edit((x) => { x.publish!.enabled = v }))}
            {num('Allowed high-severity anomalies', p.publish.max_high_anomalies, (v) => edit((x) => { x.publish!.max_high_anomalies = Math.round(v) }))}
            {num('Allowed engine errors (duplicates, broken links)', p.publish.max_error_findings, (v) => edit((x) => { x.publish!.max_error_findings = Math.round(v) }))}
            <div className="hint-th py-1.5">Always required: an approved mapping and every record matching the target format. ต้องมี mapping ที่ approve แล้ว และทุก record ตรง format เสมอ</div>
          </div></Section>}
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

function LearningTab() {
  const { t } = usePreferences(), qc = useQueryClient(), toast = useToast()
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: api.me })
  const [customer, setCustomer] = useState('')
  const { data: customers } = useQuery({ queryKey: ['customers'], queryFn: api.customers })
  const q = useQuery({ queryKey: ['learning', customer], queryFn: () => api.learning(customer) })
  const promote = useMutation({ mutationFn: (b: { header: string; target: string }) => api.promoteAlias(b), onSuccess: () => { void qc.invalidateQueries({ queryKey: ['learning'] }); toast.push({ kind: 'ok', title: t('Promoted', 'เพิ่มให้ทุกลูกค้าแล้ว') }) }, onError: (e: Error) => toast.push({ kind: 'bad', title: t('Not promoted', 'เพิ่มไม่ได้'), detail: e.message }) })
  const forget = useMutation({ mutationFn: (id: string) => api.forgetLearned(id), onSuccess: () => void qc.invalidateQueries({ queryKey: ['learning'] }) })
  const d = q.data, s = d?.stats
  const names = new Map((customers ?? []).map((c) => [c.id, c.code]))
  const canPromote = !!me?.effective_roles.includes('admin'), canForget = !!me?.effective_roles.includes('approver')
  return (
    <div className="space-y-5">
      <div className="grid sm:grid-cols-2 xl:grid-cols-4 gap-3">
        <Stat label={t('Column aliases learned', 'ชื่อคอลัมน์ที่เรียนรู้แล้ว')} value={s ? s.aliases + s.promoted : '–'} hint={s ? t(`${s.promoted} shared with everyone`, `${s.promoted} รายการใช้กับทุกลูกค้า`) : ''} />
        <Stat label={t('Conventions confirmed', 'ค่าคงที่ที่ยืนยันแล้ว')} value={s?.constants ?? '–'} hint={t('e.g. VAT included, units per pack', 'เช่น รวม VAT, ชิ้นต่อแพ็ก')} />
        <Stat label={t('Customers who taught FORMA', 'ลูกค้าที่ระบบเรียนจาก')} value={s?.customers_taught ?? '–'} />
        <Stat label={t('Times a learned field was used', 'จำนวนครั้งที่ใช้สิ่งที่เรียน')} value={s?.uses ?? '–'} tone={s?.uses ? 'ok' : 'neutral'} />
      </div>
      <Section title={t('Corrections per approval, by month', 'การแก้ต่อการอนุมัติ รายเดือน')} hint="ยิ่งลดลงเรื่อยๆ แปลว่าระบบเดาถูกมากขึ้น">
        {s?.series.length ? <table className="table"><thead><tr><th>{t('Month', 'เดือน')}</th><th className="text-right">{t('Approved', 'อนุมัติ')}</th><th className="text-right">{t('Accepted as proposed', 'รับตามที่เสนอ')}</th><th className="text-right">{t('Corrections', 'การแก้')}</th><th className="text-right">{t('Per approval', 'ต่อการอนุมัติ')}</th><th className="text-right">{t('Learned fields used', 'ใช้สิ่งที่เรียน')}</th></tr></thead>
          <tbody>{s.series.map((m) => <tr key={m.month}><td className="mono">{m.month}</td><td className="text-right tnum">{m.mappings_approved}</td><td className="text-right tnum">{m.accepted_as_proposed}</td><td className="text-right tnum">{m.corrections}</td><td className="text-right tnum font-semibold">{m.corrections_per_approval ?? '–'}</td><td className="text-right tnum">{m.learned_fields_used}</td></tr>)}</tbody></table>
          : <div className="p-5 text-sm text-muted">{q.isPending ? t('Loading…', 'กำลังโหลด…') : t('Nothing learned yet. Approve a mapping you corrected in the Studio or the review page, and it appears here.', 'ยังไม่มีอะไรให้เรียน อนุมัติ mapping ที่คุณแก้ใน Studio หรือหน้า Review แล้วจะมาแสดงที่นี่')}</div>}
      </Section>
      {canPromote && <Section title={t('Suggested for everyone', 'เสนอให้ใช้กับทุกลูกค้า')} hint="header เดียวกันที่ลูกค้าหลายรายแก้ไปลง field เดียวกัน">
        {d?.suggestions.length ? <table className="table"><thead><tr><th>{t('Header', 'หัวคอลัมน์')}</th><th>{t('Field', 'ฟิลด์')}</th><th className="text-right">{t('Customers', 'ลูกค้า')}</th><th></th></tr></thead>
          <tbody>{d.suggestions.map((x) => <tr key={x.header + x.target}><td className="font-medium">{x.header}</td><td>{x.field.description || x.field.name} <span className="text-muted mono text-[11px]">{x.target}</span></td><td className="text-right tnum">{x.customers}</td><td className="text-right"><button className="btn btn-sm btn-primary" disabled={promote.isPending} onClick={() => promote.mutate({ header: x.header, target: x.target })}><Plus size={13} />{t('Use for everyone', 'ใช้กับทุกลูกค้า')}</button></td></tr>)}</tbody></table>
          : <div className="p-5 text-sm text-muted">{t('No header has been taught by three or more customers yet.', 'ยังไม่มีหัวคอลัมน์ที่ลูกค้าตั้งแต่ 3 รายสอนเหมือนกัน')}</div>}
      </Section>}
      <Section title={t('Learned so far', 'สิ่งที่เรียนรู้แล้ว')} actions={<select className="input" aria-label={t('Customer', 'ลูกค้า')} value={customer} onChange={(e) => setCustomer(e.target.value)}><option value="">{t('All customers', 'ทุกลูกค้า')}</option>{(customers ?? []).map((c) => <option key={c.id} value={c.id}>{c.code}</option>)}</select>}>
        {d?.learned.items.length ? <table className="table"><thead><tr><th>{t('Customer', 'ลูกค้า')}</th><th>{t('What', 'อะไร')}</th><th>{t('Field', 'ฟิลด์')}</th><th>{t('Taught by', 'สอนโดย')}</th><th className="text-right">{t('Used', 'ใช้แล้ว')}</th><th></th></tr></thead>
          <tbody>{d.learned.items.map((x) => <tr key={x.id}><td>{x.customer_id ? names.get(x.customer_id) ?? x.customer_id : <Badge tone="signal">{t('everyone', 'ทุกราย')}</Badge>}</td><td>{x.kind === 'constant' ? t(`fixed value ${JSON.stringify(x.value)}`, `ค่าคงที่ ${JSON.stringify(x.value)}`) : <span>“{x.column ?? x.header}”</span>}</td><td className="mono text-[12px]">{x.target}</td><td className="text-muted">{x.actor} · {fmtDate(x.created_at ?? '')}</td><td className="text-right tnum">{x.uses}</td><td className="text-right">{canForget && <button className="btn btn-ghost btn-sm" title={t('Forget', 'ลืม')} onClick={() => forget.mutate(x.id)}><Trash2 size={13} /></button>}</td></tr>)}</tbody></table>
          : <div className="p-5 text-sm text-muted">{t('Nothing yet.', 'ยังไม่มี')}</div>}
        {d && d.learned.total > d.learned.items.length && <p className="px-4 py-2 text-xs text-muted">{t(`Showing ${d.learned.items.length} of ${d.learned.total}`, `แสดง ${d.learned.items.length} จาก ${d.learned.total}`)}</p>}
      </Section>
    </div>
  )
}

function AuditTab() {
  const { data: audit, error } = useQuery({ queryKey: ['audit'], queryFn: () => api.audit(200) })
  return (
    <Section title="Last 200 events">
      {error ? <div className="p-5 text-sm text-muted">{error.message}</div> : <table className="table"><thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Entity</th><th>Details</th></tr></thead>
        <tbody>{audit?.map((a) => <tr key={a.id}><td className="text-muted whitespace-nowrap">{fmtDate(a.at)}</td><td>{a.actor}</td><td className="mono">{a.action}</td><td className="mono text-muted">{a.entity_id}</td><td className="text-muted text-[12px] truncate max-w-[420px]">{JSON.stringify(a.details)}</td></tr>)}</tbody></table>}
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
