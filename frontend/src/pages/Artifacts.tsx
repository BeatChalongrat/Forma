import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, type ArtifactKind } from '../lib/api'
import { usePreferences } from '../lib/preferences'
import { KIND_ICON, useKindLabel } from '../components/ArtifactPanel'
import { Empty, PageHeader, Spinner } from '../components/ui'

export function ArtifactsPage() {
  const { t, prefs } = usePreferences(), label = useKindLabel()
  const { data, isPending, isError, refetch } = useQuery({ queryKey: ['artifacts'], queryFn: api.artifacts })
  const [kind, setKind] = useState<ArtifactKind | 'all'>('all')
  const items = data?.items ?? []
  const kinds = [...new Set(items.map(item => item.kind))]
  const shown = items.filter(item => kind === 'all' || item.kind === kind)
  const date = new Intl.DateTimeFormat(prefs.language === 'th' ? 'th-TH' : 'en-GB', { dateStyle: 'medium', timeZone: prefs.timezone })
  return <div className="space-y-5">
    <PageHeader title={t('My work', 'ผลงานทั้งหมด')} subtitle={t('Everything the assistant made, from every chat. Open one to keep changing it.', 'ทุกอย่างที่ผู้ช่วยสร้างจากทุกแชต เปิดกลับไปแก้ต่อได้')} />
    {isPending ? <Spinner /> : isError ? <div role="alert" className="text-bad">{t('This list could not be loaded.', 'ยังโหลดรายการไม่ได้')}<button className="btn btn-sm ml-2" onClick={() => void refetch()}>{t('Retry', 'ลองอีกครั้ง')}</button></div> : !items.length ? <Empty title={t('Nothing here yet', 'ยังไม่มีผลงาน')} body={t('Ask the assistant for a flow, a document or a slide deck.', 'ลองขอให้ผู้ช่วยวาด flow หรือทำเอกสารและสไลด์')} /> : <>
      <div className="flex flex-wrap gap-2" role="group" aria-label={t('Filter by kind', 'กรองตามชนิด')}>
        <button type="button" className={`artifact-seg-tab${kind === 'all' ? ' is-on' : ''}`} aria-pressed={kind === 'all'} onClick={() => setKind('all')}>{t('All', 'ทั้งหมด')} {items.length}</button>
        {kinds.map(value => <button key={value} type="button" className={`artifact-seg-tab${kind === value ? ' is-on' : ''}`} aria-pressed={kind === value} onClick={() => setKind(value)}>{label(value)} {items.filter(item => item.kind === value).length}</button>)}
      </div>
      <ul className="artifact-library">{shown.map(item => { const Icon = KIND_ICON[item.kind]; return <li key={item.id}><Link to={`/assistant?chat=${encodeURIComponent(item.conversation_id)}&artifact=${encodeURIComponent(item.id)}`} className="artifact-work"><span className="artifact-tile"><Icon size={20} /></span><span className="min-w-0"><b className="block truncate">{item.title}</b><small className="block">{label(item.kind)} · v{item.latest} · {date.format(new Date(item.updated_at))}</small><small className="block truncate">{t('From', 'จากแชต')}: {item.chat_title}</small></span></Link></li> })}</ul>
    </>}
  </div>
}
