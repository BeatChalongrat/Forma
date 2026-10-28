import { type ReactNode, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, Check, Download, FileClock, GitCompareArrows, Network, ArrowLeftRight, Play, RefreshCw, ShieldAlert, ClipboardCheck, X } from 'lucide-react'
import type { DatasetVersion, ImportRound, MappingVersion } from '../lib/api'
import { nextStep, ROUND_STEPS } from '../lib/steps'
import { cn, fmtInt } from '../lib/utils'
import { Confidence, RiskLevel, Spinner } from './ui'

type Props = {
  r: ImportRound
  busy: boolean
  onRun: (kind: 'run' | 'propose' | 'transform') => void
  onExport: (datasetId: string, fmt: string) => void
}

/** Where this delivery stands in the five steps, and the one thing to do next with the tools that help. */
export function RoundSteps({ r, busy, onRun, onExport }: Props) {
  const ns = nextStep(r)
  const mapping = r.mappings?.[0]
  const dataset = r.datasets?.[0]
  const tables = (r.files ?? []).reduce((a, f) => a + f.tables, 0)
  const summary: Record<number, string> = {
    1: `${r.files?.length ?? 0} file${r.files?.length === 1 ? '' : 's'} · ${tables} table${tables === 1 ? '' : 's'}${r.summary.drift ? ` · ${r.summary.drift.replace('_', ' ')}` : ''}`,
    2: mapping ? `${mapping.status === 'approved' ? (mapping.approved_by === 'policy' ? 'approved automatically' : 'approved') : (mapping.policy.decision ?? mapping.status).replace('_', ' ')}${mapping.policy.risk_level ? ` · ${mapping.policy.risk_level} risk` : ''}` : '–',
    3: dataset ? `${fmtInt(dataset.row_count)} records` : '–',
    4: dataset ? (['approved', 'published'].includes(dataset.status) ? 'approved' : dataset.invalid_records ? `${fmtInt(dataset.invalid_records)} break the format` : 'waiting for approval') : '–',
    5: r.status === 'published' ? 'exported' : '–',
  }
  return (
    <div className="flex flex-col gap-4 mb-6">
      <ol className="card p-4 grid grid-cols-1 md:grid-cols-5 gap-2">
        {ROUND_STEPS.map((s) => {
          const state = ns.step > s.n ? 'done' : ns.step === s.n ? (ns.tone === 'problem' ? 'problem' : 'current') : 'next'
          return (
            <li key={s.n} className={cn('relative rounded-[10px] px-3 py-2.5 flex items-start gap-2.5', state === 'current' && 'bg-signal-soft', state === 'problem' && 'bg-bad-soft')}>
              <span className={cn('w-7 h-7 rounded-full grid place-items-center text-[12px] font-bold shrink-0', state === 'done' && 'bg-ok text-white', state === 'next' && 'bg-surface-2 text-ink-3 border border-line', state === 'problem' && 'bg-bad text-white', state === 'current' && 'text-white')} style={state === 'current' ? { backgroundImage: 'var(--gradient-brand)' } : undefined}>
                {state === 'done' ? <Check size={14} /> : state === 'problem' ? <X size={14} /> : s.n}
              </span>
              <span className="min-w-0 leading-tight">
                <span className={cn('block font-semibold text-[13px]', state === 'next' ? 'text-ink-3' : 'text-navy')}>{s.title}</span>
                <span className="block text-[11.5px] text-muted mt-0.5">{s.th}</span>
                <span className={cn('block text-[11.5px] mt-1 font-semibold truncate', state === 'problem' ? 'text-bad' : state === 'done' ? 'text-ok' : 'text-ink-2')}>{summary[s.n]}</span>
              </span>
            </li>
          )
        })}
      </ol>
      <CurrentStep r={r} mapping={mapping} dataset={dataset} busy={busy} onRun={onRun} onExport={onExport} />
    </div>
  )
}

function CurrentStep({ r, mapping, dataset, busy, onRun, onExport }: Props & { mapping?: MappingVersion; dataset?: DatasetVersion }) {
  const ns = nextStep(r)
  const studio = mapping ? `/rounds/${r.id}/mapping/${mapping.id}` : null
  const editor = dataset ? `/datasets/${dataset.id}` : null
  const exportable = r.datasets?.find((d) => d.status === 'approved' || d.status === 'published')
  let body: ReactNode = null
  let primary: ReactNode = null
  let tools: { to: string; label: string; icon: typeof Network }[] = []

  if (ns.step === 1) {
    body = r.summary.error ? <div className="text-bad">{r.summary.error}</div> : <div className="flex items-center gap-2 text-ink-2"><Spinner /> Reading the files and comparing them with this customer's approved format…</div>
    if (r.status === 'failed' || r.summary.error) primary = <button className="btn btn-primary" disabled={busy} onClick={() => onRun('run')}><Play size={14} /> Run again</button>
  } else if (ns.step === 2 && mapping) {
    body = <MappingDecision m={mapping} />
    primary = studio && <Link to={studio} className="btn btn-primary">Open Mapping Studio <ArrowRight size={14} /></Link>
    tools = studio ? [
      { to: `${studio}?tab=changes`, label: 'What changed vs approved', icon: ArrowLeftRight },
      { to: `${studio}?tab=compare`, label: 'Compare outputs', icon: GitCompareArrows },
      { to: `${studio}?view=relationships`, label: 'How the sheets connect', icon: Network },
    ] : []
  } else if (ns.step === 3) {
    body = <div className="text-ink-2">The mapping is approved. Create the data to turn every row into a record of the target format.<div className="hint-th mt-1">กด Create data เพื่อแปลงทุกแถวเป็น JSON ตาม Target format (ไม่กี่วินาที)</div></div>
    primary = <button className="btn btn-primary" disabled={busy} onClick={() => onRun('transform')}><Play size={14} /> Create data</button>
    if (studio) tools = [{ to: studio, label: 'View the mapping', icon: Network }]
  } else if (ns.step === 4 && dataset) {
    body = (
      <div className="text-ink-2">
        {fmtInt(dataset.row_count)} records are ready. Check them, fix what is wrong, then approve.
        {r.summary.publish?.decision === 'review' && <div className="mt-1.5 text-warn font-semibold">Held for a person: {r.summary.publish.reasons.join(' · ')}</div>}
        {dataset.invalid_records > 0 && <div className="mt-1.5 text-bad font-semibold">{fmtInt(dataset.invalid_records)} record{dataset.invalid_records > 1 ? 's' : ''} do not match the target format — fix or remove them before approving.</div>}
        <div className="hint-th mt-1">เปิด Data Editor เพื่อดูสิ่งที่เปลี่ยนจากรอบก่อน ความผิดปกติ และแก้ข้อมูล แล้วกด Approve data</div>
      </div>
    )
    primary = editor && <Link to={editor} className="btn btn-primary">Open Data Editor <ArrowRight size={14} /></Link>
    tools = editor ? [
      { to: `${editor}?tab=diff`, label: 'Changes & anomalies', icon: ShieldAlert },
      { to: `${editor}?tab=findings`, label: 'Problems', icon: ClipboardCheck },
      { to: `${editor}?tool=trace`, label: 'Trace a value', icon: FileClock },
    ] : []
  } else if (ns.step >= 5 && exportable) {
    body = <div className="text-ink-2">{ns.step === 5 ? 'The data is approved. Download it in the target format.' : 'Exported. You can download it again in any format.'}<div className="hint-th mt-1">ไฟล์มีแค่ field ตาม Target format · JSON = ทั้งชุด, JSONL = หนึ่ง record ต่อบรรทัด, CSV = เปิดใน Excel</div></div>
    primary = (
      <div className="flex items-center gap-2">
        <button className="btn btn-primary" disabled={busy} onClick={() => onExport(exportable.id, 'json')}><Download size={14} /> Export JSON</button>
        <button className="btn" disabled={busy} onClick={() => onExport(exportable.id, 'jsonl')}>JSONL</button>
        <button className="btn" disabled={busy} onClick={() => onExport(exportable.id, 'csv')}>CSV</button>
      </div>
    )
    tools = [{ to: `/datasets/${exportable.id}`, label: 'Open the data', icon: ClipboardCheck }]
  }

  return (
    <div className={cn('card p-5 border-l-4', ns.tone === 'problem' ? 'border-l-bad' : ns.tone === 'done' ? 'border-l-ok' : 'border-l-brand-blue')}>
      <div className="flex items-start gap-4 flex-wrap">
        <div className="flex-1 min-w-[280px]">
          <div className="text-[11px] font-semibold uppercase tracking-[0.1em] text-signal">{ns.step <= 5 ? `Step ${ns.step} of 5 · next` : 'Finished'}</div>
          <div className="heading text-[17px] mt-0.5">{ns.action ?? (ns.tone === 'done' ? 'Exported' : 'Working…')}</div>
          <div className="hint-th">{ns.th}</div>
          <div className="mt-3 text-[13px]">{body}</div>
        </div>
        <div className="flex flex-col items-end gap-2 shrink-0">
          {primary}
          {ns.step === 2 && <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => onRun('propose')}><RefreshCw size={13} className={cn(busy && 'animate-spin')} /> Propose again</button>}
        </div>
      </div>
      {tools.length > 0 && (
        <div className="mt-4 pt-3 border-t border-line flex items-center gap-2 flex-wrap">
          <span className="text-[11.5px] text-muted mr-1">Tools for this step · เครื่องมือของขั้นนี้</span>
          {tools.map((t) => <Link key={t.to} to={t.to} className="btn btn-outline btn-sm"><t.icon size={13} /> {t.label}</Link>)}
        </div>
      )}
    </div>
  )
}

function MappingDecision({ m }: { m: MappingVersion }) {
  const d = m.policy.decision
  const [all, setAll] = useState(false)
  const reasons = m.policy.reasons
  const factors = m.policy.risk_level === 'high' ? m.policy.risk_factors ?? [] : []
  const hidden = Math.max(0, reasons.length - 3) + Math.max(0, factors.length - 2)
  return (
    <div>
      <div className="font-semibold text-ink flex items-center gap-2 flex-wrap">
        <RiskLevel level={m.policy.risk_level} factors={m.policy.risk_factors} />
        {d === 'review' && 'Policy sent this mapping to a person — check it and approve.'}
        {d === 'block' && 'Blocked — this file does not look like it belongs to this customer, or produced no data.'}
        {d === 'auto_approve' && 'Approved automatically — the structure matched an approved format.'}
      </div>
      <ul className="text-[12.5px] text-ink-2 mt-1.5 flex flex-col gap-0.5">{(all ? reasons : reasons.slice(0, 3)).map((x, i) => <li key={i} className={cn(!all && 'truncate')}>• {x}</li>)}</ul>
      {factors.length > 0 && (
        <div className="mt-1.5 text-[12.5px]">
          <div className="font-semibold text-bad">Why high risk</div>
          <ul className="text-bad/90 flex flex-col gap-0.5">{(all ? factors : factors.slice(0, 2)).map((x, i) => <li key={i} className={cn(!all && 'truncate')}>• {x}</li>)}</ul>
        </div>
      )}
      {hidden > 0 && <button className="text-[12px] font-semibold text-signal hover:underline mt-1" onClick={() => setAll(!all)}>{all ? 'Show less' : `Show all (${hidden} more)`}</button>}
      <div className="flex items-center gap-4 mt-2 text-[12px] text-muted flex-wrap">
        <span className="flex items-center gap-1.5">confidence <Confidence value={m.confidence} /></span>
        {m.policy.simulation && <span className="tnum">{fmtInt(m.policy.simulation.rows_out)} rows simulated · {m.policy.simulation.findings.error} errors</span>}
        {m.policy.sandbox?.delta && <span className={cn('tnum', m.policy.sandbox.verdict === 'fail' && 'text-bad font-semibold')}>vs approved v{m.policy.sandbox.baseline_version}: {fmtInt(m.policy.sandbox.delta.changed_prices)} prices change · {fmtInt(m.policy.sandbox.delta.lost)} records missing</span>}
      </div>
    </div>
  )
}
