import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { MessageCircleMore, FileSpreadsheet, Table2 } from 'lucide-react'
import { api, waitForJob, type SourceFile, type TableProfile } from '../lib/api'
import { formatLabel, fmtBytes, fmtDate, fmtInt, cn } from '../lib/utils'
import { PageHeader, StatusBadge, Section, Badge, Confidence, Drawer, KV, Spinner, FindingRow, Empty } from '../components/ui'
import { RoundSteps } from '../components/RoundSteps'
import { useToast } from '../lib/toast'
import { usePreferences } from '../lib/preferences'

const driftTone = (d?: string | null) => (d === 'identical' || d === 'header_moved' ? 'ok' : d === 'unrelated_file' ? 'bad' : d ? 'warn' : 'neutral')

export function RoundPage() {
  const { t } = usePreferences()
  const { roundId = '' } = useParams()
  const qc = useQueryClient()
  const toast = useToast()
  const nav = useNavigate()
  const { data: r, isLoading } = useQuery({ queryKey: ['round', roundId], queryFn: () => api.round(roundId), refetchInterval: (q) => (['uploaded', 'profiled'].includes(q.state.data?.status ?? '') ? 2000 : false) })
  const [fileOpen, setFileOpen] = useState<SourceFile | null>(null)

  const runJob = useMutation({
    mutationFn: async (kind: 'run' | 'propose' | 'transform') => {
      const { job_id } = kind === 'run' ? await api.runPipeline(roundId) : kind === 'propose' ? await api.propose(roundId) : await api.transform(roundId)
      return waitForJob(job_id)
    },
    onSuccess: (j) => { qc.invalidateQueries(); if (j.status === 'failed') toast.push({ kind: 'bad', title: 'Job failed', detail: j.error?.split('\n')[0] }); else toast.push({ kind: 'ok', title: 'Done' }) },
    onError: (e: Error) => toast.push({ kind: 'bad', title: 'Request failed', detail: e.message }),
  })

  const doExport = useMutation({
    mutationFn: (v: { id: string; fmt: string }) => api.exportDataset(v.id, v.fmt),
    onSuccess: (a) => { qc.invalidateQueries(); window.open(api.downloadUrl(a.id), '_blank'); toast.push({ kind: 'ok', title: `Exported ${fmtInt(a.row_count)} records as ${a.fmt.toUpperCase()}` }) },
    onError: (e: Error) => toast.push({ kind: 'bad', title: 'Not exported', detail: e.message }),
  })

  if (isLoading || !r) return <div className="p-10 text-center text-muted"><Spinner /> </div>
  const latestMapping = r.mappings?.[0]
  const latestDataset = r.datasets?.[0]
  const drift = r.summary.drift
  const memory = r.summary.format_memory

  return (
    <>
      <PageHeader
        crumbs={<><Link to="/imports" className="hover:text-ink">Imports</Link><span>/</span><Link to={`/customers/${r.customer_id}`} className="hover:text-ink">{r.customer?.code}</Link></>}
        title={<span>{r.customer?.name} <span className="text-muted font-normal">· {r.label}</span></span>}
        subtitle={<span className="flex items-center gap-2 flex-wrap"><StatusBadge status={r.status} />{drift && <Badge tone={driftTone(drift)}>format: {formatLabel(drift)}</Badge>}<span className="text-muted">{fmtDate(r.created_at)}</span></span>}
        hint="รอบนี้อยู่ขั้นไหน และต้องทำอะไรต่อ ดูที่แถบด้านล่าง"
        actions={<Link className="btn" to={`/assistant?round=${r.id}`}><MessageCircleMore size={16} /> Ask about this file</Link>}
      />

      <RoundSteps r={r} busy={runJob.isPending || doExport.isPending} onRun={(k) => runJob.mutate(k)} onExport={(id, fmt) => doExport.mutate({ id, fmt })} />

      {memory && <div className="mt-5 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line bg-surface px-4 py-3 text-sm">
        <div>
          <p className="font-medium">{memory.status === 'reused'
            ? t(`Reused approved mapping v${memory.mapping_version}`, `ใช้กฎจับคู่ที่เคยอนุมัติ เวอร์ชัน ${memory.mapping_version}`)
            : memory.status === 'known' ? t('An approved mapping matches this format', 'พบกฎที่เคยอนุมัติสำหรับรูปแบบนี้')
              : memory.status === 'changed' ? t('The layout changed; check the mapping', 'รูปแบบไฟล์เปลี่ยน ต้องตรวจการจับคู่')
                : memory.status === 'edited' ? t('Mapping edited by a reviewer', 'ผู้ตรวจปรับกฎจับคู่')
                  : t('New format for this target', 'รูปแบบใหม่สำหรับปลายทางนี้')}</p>
          <p className="text-muted mt-1">{memory.target_schema} · {memory.status === 'reused'
            ? t('No AI remapping. New file values are transformed and validated again.', 'ไม่เรียก AI จับคู่ใหม่ อ่านค่าจากไฟล์ใหม่และตรวจผลอีกครั้ง')
            : t('Customer identification and mapping approval are separate checks.', 'การระบุลูกค้าและการอนุมัติกฎจับคู่เป็นคนละขั้นตอน')}</p>
        </div>
        {memory.mapping_id && <Link className="btn btn-outline btn-sm" to={`/review?mapping=${memory.mapping_id}`}>{t('View saved mapping', 'ดูกฎที่เคยอนุมัติ')}</Link>}
      </div>}
      <details className="mt-6" open={r.status === 'blocked' || r.status === 'failed'}><summary className="text-[14px] font-medium text-ink-2 mb-4">Files, issues &amp; version history</summary>
      <div className="grid lg:grid-cols-[1fr_380px] gap-4">
        <div className="flex flex-col gap-4">
          <Section title="Files">
            <table className="table">
              <thead><tr><th>File</th><th>Sheets</th><th>Tables</th><th>vs approved format</th><th></th></tr></thead>
              <tbody>
                {r.files?.map((f) => (
                  <tr key={f.id} >
                    <td><div className="flex items-center gap-2 flex-wrap"><FileSpreadsheet size={15} className="text-muted" /><span className="font-medium break-all">{f.name}</span><span className="text-muted tnum text-[12px]">{fmtBytes(f.size)}</span>{f.duplicate_of && <Badge tone="info">seen before</Badge>}{f.error && <Badge tone="bad">error</Badge>}</div></td>
                    <td className="text-muted">{f.sheets.join(', ')}</td>
                    <td className="tnum">{f.tables}</td>

                    <td>{f.drift ? <Badge tone={driftTone(f.drift.classification)}>{formatLabel(f.drift.classification)} · {Math.round(f.drift.similarity * 100)}%</Badge> : <span className="text-muted">first time</span>}{f.drift?.matches_other_customer && <Badge tone="bad" className="ml-1">matches {f.drift.matches_other_customer.customer_code}</Badge>}</td>
                    <td className="text-right"><button className="btn btn-ghost btn-sm" onClick={() => setFileOpen(f)} aria-label={`Inspect ${f.name}`}>Inspect</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Section>

          <Section title="Mapping versions">
            {r.mappings?.length ? (
              <table className="table">
                <thead><tr><th>Version</th><th>Status</th><th>Source</th><th>Confidence</th><th>Decision</th><th>Created</th><th></th></tr></thead>
                <tbody>
                  {r.mappings.map((m) => (
                    <tr key={m.id}>
                      <td className="tnum">v{m.version}</td>
                      <td><StatusBadge status={m.status} /></td>
                      <td className="text-muted">{m.policy.source ?? m.created_by}{m.model ? ` · ${m.model}` : ''}</td>
                      <td><Confidence value={m.confidence} /></td>
                      <td className="text-muted">{m.policy.decision?.replace('_', ' ')}{m.approved_by ? ` · ${m.approved_by}` : ''}</td>
                      <td className="text-muted">{fmtDate(m.created_at)}</td>
                      <td className="text-right"><Link className="btn btn-ghost btn-sm" to={`/rounds/${r.id}/mapping/${m.id}`}>Open</Link></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <div className="p-4"><Empty title="No mapping yet" body="Run the pipeline to profile the files and propose a mapping." /></div>}
          </Section>

          <Section title="Datasets">
            {r.datasets?.length ? (
              <table className="table">
                <thead><tr><th>Version</th><th>Status</th><th className="text-right">Rows</th><th>Errors</th><th>By</th><th>Created</th><th></th></tr></thead>
                <tbody>
                  {r.datasets.map((d) => (
                    <tr key={d.id}>
                      <td className="tnum">v{d.version}</td>
                      <td><StatusBadge status={d.status} /></td>
                      <td className="text-right tnum">{fmtInt(d.row_count)}</td>
                      <td className="tnum">{d.findings.filter((f) => f.severity === 'error').length ? <span className="text-bad">{d.findings.filter((f) => f.severity === 'error').length}</span> : <span className="text-ok">0</span>}</td>
                      <td className="text-muted">{d.created_by}{d.note ? ` · ${d.note}` : ''}</td>
                      <td className="text-muted">{fmtDate(d.created_at)}</td>
                      <td className="text-right"><Link className="btn btn-ghost btn-sm" to={`/datasets/${d.id}`}>Open editor</Link></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <div className="p-6 text-center text-muted">{latestMapping?.status === 'approved' ? 'Mapping approved — run Transform to produce the dataset.' : 'The dataset appears here once a mapping is approved and transformed.'}</div>}
          </Section>
        </div>

        <div className="flex flex-col gap-4">
          {latestMapping?.open_questions?.length ? (
            <Section title="Questions to review">
              <ul className="p-4 flex flex-col gap-2 text-[13px]">{latestMapping.open_questions.map((q, i) => <li key={i} className="flex gap-2"><span className="text-warn">?</span><span>{q}</span></li>)}</ul>
            </Section>
          ) : null}
          {r.summary.drift_report && r.summary.drift_report.changes.length > 0 && (
            <Section title="What changed in the format">
              <ul className="p-4 flex flex-col gap-1.5 text-[12.5px]">
                {r.summary.drift_report.changes.slice(0, 12).map((c, i) => (
                  <li key={i} className="flex gap-2"><Badge tone={String(c.kind).includes('removed') ? 'bad' : String(c.kind).includes('renamed') ? 'warn' : 'info'} className="min-h-6 px-1.5 text-[12px]">{String(c.kind).replace('_', ' ')}</Badge><span className="mono truncate">{c.from !== undefined ? `${c.from} → ${c.to}` : (c.header as string) ?? (c.sheet as string) ?? ''}</span></li>
                ))}
              </ul>
            </Section>
          )}
          {latestDataset && (
            <Section title="Data issues">
              <div className="p-3">{latestDataset.findings.length ? latestDataset.findings.slice(0, 10).map((f, i) => <FindingRow key={i} f={f} />) : <div className="text-muted text-center py-3">No findings.</div>}</div>
            </Section>
          )}
          <Section title="Processing history">
            <ul className="p-3 text-[12.5px] flex flex-col gap-1">
              {r.jobs?.map((j) => (
                <li key={j.id} className="flex items-center justify-between gap-2">
                  <span>{j.kind.replace('_', ' ')}</span>
                  <span className="flex items-center gap-2">{j.status === 'running' && <Spinner />}<StatusBadge status={j.status} />{j.error && <span className="text-bad truncate max-w-[160px]" title={j.error}>{j.error.split('\n')[0]}</span>}</span>
                </li>
              ))}
            </ul>
          </Section>
          <button className="btn btn-ghost btn-sm self-start text-muted" onClick={() => nav(`/customers/${r.customer_id}`)}>Customer history →</button>
        </div>
      </div>

      </details>
      <FileDrawer roundId={roundId} file={fileOpen} onClose={() => setFileOpen(null)} />
    </>
  )
}

export function FileDrawer({ roundId, file, onClose }: { roundId: string; file: SourceFile | null; onClose: () => void }) {
  const { data } = useQuery({ queryKey: ['file', roundId, file?.id], queryFn: () => api.file(roundId, file!.id), enabled: !!file })
  const [table, setTable] = useState<TableProfile | null>(null)
  const prof = data?.profile
  return (
    <Drawer open={!!file} onClose={() => { setTable(null); onClose() }} title={file?.name ?? ''} width="w-[720px]">
      {prof && (
        <>
          <div className="grid grid-cols-2 gap-x-6 mb-4">
            <KV k="Kind" v={prof.meta.kind} />
            <KV k="Sheets" v={prof.meta.sheets.length} />
            <KV k="Tables detected" v={prof.tables.length} />
            <KV k="Hidden sheets" v={prof.meta.sheets.filter((s) => s.hidden).length} />
            <KV k="Merged ranges" v={prof.meta.sheets.reduce((a, s) => a + s.merged_ranges.length, 0)} />
            <KV k="Formula cells" v={prof.meta.sheets.reduce((a, s) => a + s.formula_cells, 0)} />
          </div>
          <div className="flex flex-col gap-2">
            {prof.tables.map((t, i) => (
              <button key={i} onClick={() => setTable(table === t ? null : t)} className={cn('card p-3 text-left hover:border-signal/40', table === t && 'border-signal/60')}>
                <div className="flex items-center gap-2"><Table2 size={14} className="text-muted" /><span className="font-medium">{t.sheet}</span><span className="text-muted text-[12px]">header row {t.table.header_row + 1} · {fmtInt(t.n_rows)} rows · {t.columns.length} columns</span>{t.table.title && <span className="text-muted text-[12px] truncate">“{t.table.title}”</span>}</div>
                {table === t && (
                  <table className="table mt-2 text-[12.5px]">
                    <thead><tr><th>Column</th><th>Type</th><th>Nulls</th><th>Unique</th><th>Hints</th><th>Samples</th></tr></thead>
                    <tbody>{t.columns.map((c) => <tr key={c.name}><td className="font-medium">{c.name}</td><td className="text-muted">{c.inferred_type}</td><td className="tnum">{Math.round(c.null_ratio * 100)}%</td><td className="tnum">{Math.round(c.unique_ratio * 100)}%</td><td className="text-muted">{c.hints.join(', ')}</td><td className="text-muted truncate max-w-[220px]">{c.samples.slice(0, 3).join(' · ')}</td></tr>)}</tbody>
                  </table>
                )}
              </button>
            ))}
          </div>
        </>
      )}
    </Drawer>
  )
}
