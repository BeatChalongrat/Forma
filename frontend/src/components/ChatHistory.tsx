import { AssistantWork } from './AssistantWork'
import { Fragment, useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { ChevronDown, Pin, Search, SquarePen } from 'lucide-react'
import { KIND_ICON, useKindLabel } from './ArtifactPanel'
import { ChatMenu } from './ChatMenu'
import { api } from '../lib/api'
import { AssistantIcon } from '../lib/icons'
import { chatIdentity, newChatUrl } from '../lib/chats'
import { usePreferences } from '../lib/preferences'
import { cn } from '../lib/utils'

export function ChatHistory({ compact, onNavigate }: { compact: boolean; onNavigate: () => void }) {
  const { t } = usePreferences(), loc = useLocation(), navigate = useNavigate(), kindLabel = useKindLabel()
  const active = loc.pathname === '/assistant', params = new URLSearchParams(loc.search), selected = params.get('chat'), openWork = params.get('artifact')
  const [view, setView] = useState<'chats' | 'work' | 'archived'>('chats'), archived = view === 'archived'
  const [search, setSearch] = useState(''), [term, setTerm] = useState('')
  const [expanded, setExpanded] = useState(() => {
    try { return localStorage.getItem('forma.assistant-history') !== 'collapsed' } catch { return true }
  })
  const toggleHistory = () => {
    const next = !expanded
    setExpanded(next)
    try { localStorage.setItem('forma.assistant-history', next ? 'expanded' : 'collapsed') } catch { /* private mode */ }
  }
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: api.me })
  const work = useQuery({ queryKey: ['assistant-work', me ? chatIdentity(me) : ''], queryFn: api.assistantWork, enabled: !!me, refetchInterval: 4000 })
  const workCount = work.data?.items.length ?? 0
  useEffect(() => { const timer = setTimeout(() => setTerm(search), 250); return () => clearTimeout(timer) }, [search])
  const history = useInfiniteQuery({ queryKey: ['assistant-chats', me ? chatIdentity(me) : '', term, archived], queryFn: ({ pageParam }) => api.chats(term, pageParam, archived), initialPageParam: 0, getNextPageParam: page => page.next_offset ?? undefined, enabled: !!me && !compact && expanded && view !== 'work', refetchInterval: active && expanded && !compact && view !== 'work' ? 10000 : false })
  const works = useQuery({ queryKey: ['artifacts'], queryFn: api.artifacts, enabled: !compact && expanded && view === 'work' })
  const found = (works.data?.items ?? []).filter(item => `${item.title} ${item.chat_title ?? ''}`.toLowerCase().includes(term.trim().toLowerCase()))
  const searchLabel = { chats: t('Search chats', 'ค้นหาแชต'), work: t('Search work', 'ค้นหาผลงาน'), archived: t('Search archived chats', 'ค้นหาแชตที่เก็บไว้') }[view]
  const tab = (value: typeof view, text: string) => <button type="button" aria-pressed={view === value} className={cn(view === value && 'is-on')} onClick={() => setView(value)}>{text}</button>
  return <div className="mb-1">
    <div className={cn('flex items-center rounded-lg hover:bg-surface-2', active && 'bg-signal-soft text-navy')}>
      {compact ? <Link to="/assistant" onClick={onNavigate} title={t('Assistant', 'ผู้ช่วย')} aria-label={t('Assistant', 'ผู้ช่วย')} className="flex justify-center px-3 py-2.5 flex-1"><AssistantIcon size={18} aria-hidden="true" />{!!workCount && <span className="h-1.5 w-1.5 rounded-full bg-brand-lime" aria-label={t("Assistant updates", "งานและคำตอบใหม่")} />}</Link> : <button type="button" onClick={toggleHistory} aria-expanded={expanded} aria-controls="assistant-chat-history" className="flex items-center gap-3 px-3 py-2.5 min-w-0 w-full text-left rounded-lg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-signal"><AssistantIcon size={18} className="shrink-0" aria-hidden="true" /><span className="flex-1 text-[13px] font-medium">{t('Assistant', 'ผู้ช่วย')}</span>{!!workCount && <span className="text-xs text-muted" aria-label={t('Assistant updates', 'งานและคำตอบใหม่')}>{workCount}</span>}<ChevronDown size={15} className={cn('shrink-0 text-muted transition-transform motion-reduce:transition-none', !expanded && '-rotate-90')} aria-hidden="true" /></button>}
    </div>
    {!compact && <div id="assistant-chat-history" hidden={!expanded} className="pl-3 pr-1 py-2 space-y-2">
      <button onClick={() => { navigate(newChatUrl()); onNavigate() }} className="flex items-center gap-2 text-[12px] px-2 py-2 w-full rounded-lg hover:bg-surface-2 text-left font-medium"><SquarePen size={15} />{t('New chat', 'แชตใหม่')}</button>
      <AssistantWork items={work.data?.items ?? []} /><div className="chat-tabs" role="group" aria-label={t('What to list', 'เลือกรายการ')}>{tab('chats', t('Chat history', 'ประวัติแชต'))}{tab('work', t('Work', 'ผลงาน'))}{tab('archived', t('Archived', 'ที่เก็บไว้'))}</div>
      <label className="flex items-center gap-2 border border-line rounded-lg px-2 py-1.5 text-muted"><Search size={13} className="shrink-0" /><input value={search} onChange={e => setSearch(e.target.value)} maxLength={200} placeholder={searchLabel} aria-label={searchLabel} className="min-w-0 w-full bg-transparent border-0 text-xs" /></label>
      <div className="max-h-60 overflow-y-auto space-y-0.5">
        {view === 'work' ? <>
          {found.map(item => { const Icon = KIND_ICON[item.kind]; return <Link key={item.id} to={`/assistant?chat=${encodeURIComponent(item.conversation_id)}&artifact=${encodeURIComponent(item.id)}`} onClick={onNavigate} title={[item.title, kindLabel(item.kind), `v${item.latest}`, item.chat_title].filter(Boolean).join(' · ')} className={cn('flex min-w-0 items-center gap-1.5 rounded-lg px-2 py-2 text-xs hover:bg-surface-2', openWork === item.id && active && 'bg-surface-2 text-navy font-medium')}><Icon size={13} className="shrink-0 text-muted" /><span className="truncate">{item.title}</span></Link> })}
          {works.isPending && <p className="text-xs text-muted px-2 py-2">{t('Loading…', 'กำลังโหลด…')}</p>}
          {works.data && !found.length && <p className="text-xs text-muted px-2 py-2">{term ? t('No work found', 'ยังไม่มีผลงานที่ตรงกัน') : t('Nothing made yet', 'ยังไม่มีผลงาน')}</p>}
          {works.isError && <button className="text-xs text-bad p-2" onClick={() => void works.refetch()}>{t('Retry loading work', 'โหลดผลงานอีกครั้ง')}</button>}
        </> : <>
        {history.data?.pages.flatMap(page => page.items).map((chat, index, all) => <Fragment key={chat.id}>
          {!archived && (index === 0 || chat.pinned !== all[index - 1].pinned) && <p className="text-[11px] text-muted px-2 pt-2 pb-1">{chat.pinned ? t('Pinned', 'ปักหมุด') : t('Recent chats', 'แชตล่าสุด')}</p>}
          <div className={cn('flex items-center rounded-lg hover:bg-surface-2', selected === chat.id && active && 'bg-surface-2 text-navy font-medium')}>
            <Link to={'/assistant?chat=' + encodeURIComponent(chat.id)} onClick={onNavigate} title={chat.title} className="flex min-w-0 flex-1 items-center gap-1.5 px-2 py-2 text-xs">{chat.pinned && <Pin size={12} className="shrink-0 text-muted" />}<span className="truncate">{chat.title}</span></Link>
            {me && <ChatMenu chat={chat} identity={chatIdentity(me)} />}
          </div>
        </Fragment>)}
        {history.isPending && <p className="text-xs text-muted px-2 py-2">{t('Loading…', 'กำลังโหลด…')}</p>}
        {history.data?.pages[0].total === 0 && <p className="text-xs text-muted px-2 py-2">{t('No chats found', 'ยังไม่มีแชตที่ตรงกัน')}</p>}
        {history.isError && <button className="text-xs text-bad p-2" onClick={() => void history.refetch()}>{t('Retry loading history', 'โหลดประวัติอีกครั้ง')}</button>}
        {history.hasNextPage && <button className="text-xs text-muted p-2 hover:underline" disabled={history.isFetchingNextPage} onClick={() => void history.fetchNextPage()}>{t('Show more', 'แสดงเพิ่มเติม')}</button>}
        </>}
      </div>
    </div>}
  </div>
}
