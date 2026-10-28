import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Check, X, ArrowRight } from 'lucide-react'
import { api } from '../lib/api'
import { cn, timeAgo } from '../lib/utils'
import { PageHeader, Badge, Section, Confidence, Tabs, Empty } from '../components/ui'
import { useToast } from '../lib/toast'

export function ReviewCenter() {
  const [tab, setTab] = useState<'open' | 'approved' | 'rejected' | 'dismissed'>('open')
  const { data } = useQuery({ queryKey: ['review', tab], queryFn: () => api.review(tab), refetchInterval: tab === 'open' ? 8000 : false })
  const qc = useQueryClient()
  const toast = useToast()
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const act = useMutation({
    mutationFn: async ({ ids, approve }: { ids: string[]; approve: boolean }) => {
      for (const id of ids) {
        if (approve) await api.approveMapping(id, { actor: 'operator', comment: 'bulk approve' })
        else await api.rejectMapping(id, { actor: 'operator', comment: 'bulk reject' })
      }
    },
    onSuccess: (_, v) => { qc.invalidateQueries(); setSelected(new Set()); toast.push({ kind: 'ok', title: `${v.ids.length} mapping${v.ids.length > 1 ? 's' : ''} ${v.approve ? 'approved' : 'rejected'}` }) },
    onError: (e: Error) => toast.push({ kind: 'bad', title: 'Action failed', detail: e.message }),
  })
  const mappings = (data ?? []).filter((t) => t.mapping_version_id)
  const toggle = (id: string) => setSelected((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })
  const bulkable = mappings.filter((t) => t.kind !== 'blocked' && t.details.decision !== 'block')
  return (
    <>
      <PageHeader title="Review" subtitle="Ordered by business risk: wrong files and low-confidence prices first, cosmetic drift last."
        actions={selected.size > 0 && <><span className="text-muted text-[12.5px]">{selected.size} selected</span><button className="btn btn-danger" onClick={() => act.mutate({ ids: [...selected], approve: false })}><X size={14} /> Reject</button><button className="btn btn-primary" onClick={() => act.mutate({ ids: [...selected], approve: true })}><Check size={14} /> Approve selected</button></>} />
      <Tabs value={tab} onChange={setTab} items={[{ id: 'open', label: 'Open', count: tab === 'open' ? data?.length : undefined }, { id: 'approved', label: 'Approved' }, { id: 'rejected', label: 'Rejected' }, { id: 'dismissed', label: 'Superseded' }]} />
      <div className="mt-4">
        {data?.length ? (
          <Section title={`${data.length} item${data.length > 1 ? 's' : ''}`} actions={tab === 'open' && bulkable.length > 0 ? <button className="btn btn-ghost btn-sm" onClick={() => setSelected(selected.size === bulkable.length ? new Set() : new Set(bulkable.map((t) => t.mapping_version_id!)))}>{selected.size === bulkable.length ? 'Clear' : 'Select all non-blocked'}</button> : undefined}>
            <ul>
              {data.map((t) => {
                const blocked = t.kind === 'blocked' || t.details.decision === 'block'
                return (
                  <li key={t.id} className={cn('px-4 py-3 border-b border-line last:border-0 flex gap-3 items-start', blocked && 'bg-bad-soft/30')}>
                    {tab === 'open' && t.mapping_version_id && <input type="checkbox" className="mt-1 accent-[var(--color-signal)]" disabled={blocked} checked={selected.has(t.mapping_version_id)} onChange={() => toggle(t.mapping_version_id!)} />}
                    <div className="w-12 shrink-0 text-center"><div className="tnum text-[18px] font-semibold leading-none">{t.priority}</div><div className="text-[10.5px] text-muted mt-0.5">priority</div></div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-medium">{t.customer?.code}</span><span className="text-muted">{t.customer?.name}</span>
                        <Badge tone={blocked ? 'bad' : t.kind === 'drift' ? 'warn' : 'signal'}>{blocked ? 'blocked' : t.kind}</Badge>
                        <span className="text-muted text-[12px]">{t.round?.label} · {timeAgo(t.created_at)}</span>
                        {t.resolver && <span className="text-muted text-[12px]">· {t.status} by {t.resolver}</span>}
                      </div>
                      <ul className="text-[12.5px] text-ink-2 mt-1 flex flex-col gap-0.5">{t.reasons.map((r, i) => <li key={i}>• {r}</li>)}</ul>
                      {t.details.open_questions?.length ? <div className="text-[12px] text-warn mt-1">{t.details.open_questions.length} question{t.details.open_questions.length > 1 ? 's' : ''} from the mapper: {t.details.open_questions[0]}</div> : null}
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      {t.mapping_version_id && <MappingConf id={t.mapping_version_id} />}
                      <Link className="btn btn-sm" to={t.mapping_version_id ? `/rounds/${t.round_id}/mapping/${t.mapping_version_id}` : `/rounds/${t.round_id}`}>Open <ArrowRight size={13} /></Link>
                    </div>
                  </li>
                )
              })}
            </ul>
          </Section>
        ) : <Empty title={tab === 'open' ? 'Queue is empty' : 'Nothing here'} body={tab === 'open' ? 'Every recent import was handled by policy or already reviewed.' : undefined} />}
      </div>
    </>
  )
}

function MappingConf({ id }: { id: string }) {
  const { data } = useQuery({ queryKey: ['mapping-lite', id], queryFn: () => api.mapping(id), staleTime: 60000 })
  return <Confidence value={data?.confidence} />
}
