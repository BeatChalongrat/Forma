import { Fragment, useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { ChevronDown, Sparkles, Pin, Search, SquarePen } from 'lucide-react'
import { ChatMenu } from './ChatMenu'
import { api } from '../lib/api'
import { chatIdentity, newChatUrl } from '../lib/chats'
import { usePreferences } from '../lib/preferences'
import { cn } from '../lib/utils'

export function ChatHistory({ compact, onNavigate }: { compact: boolean; onNavigate: () => void }) {
  const { t } = usePreferences(), loc = useLocation(), navigate = useNavigate()
  const active = loc.pathname === '/assistant', selected = new URLSearchParams(loc.search).get('chat')
  const [expanded, setExpanded] = useState(true), [search, setSearch] = useState(''), [term, setTerm] = useState(''), [archived, setArchived] = useState(false)
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: api.me })
  useEffect(() => { const timer = setTimeout(() => setTerm(search), 250); return () => clearTimeout(timer) }, [search])
  const history = useInfiniteQuery({ queryKey: ['assistant-chats', me ? chatIdentity(me) : '', term, archived], queryFn: ({ pageParam }) => api.chats(term, pageParam, archived), initialPageParam: 0, getNextPageParam: page => page.next_offset ?? undefined, enabled: !!me && expanded && !compact, refetchInterval: active ? 10000 : false })
  return <div className="mb-1">
    <div className={cn('flex items-center rounded-lg hover:bg-surface-2', active && 'bg-signal-soft text-navy')}>
      <Link to="/assistant" onClick={onNavigate} title={t('Assistant', 'ผู้ช่วย')} className={cn('flex items-center gap-3 px-3 py-2.5 min-w-0 flex-1', compact && 'justify-center')}><Sparkles size={20} className="shrink-0" aria-hidden="true" />{!compact && <span className="text-[13px] font-medium">{t('Assistant', 'ผู้ช่วย')}</span>}</Link>
      {!compact && <button className="p-2 mr-1 rounded-md hover:bg-surface-2" onClick={() => setExpanded(!expanded)} aria-expanded={expanded} aria-controls="assistant-chat-history" aria-label={t('Chat history', 'ประวัติแชต')}><ChevronDown size={15} className={cn('transition-transform', !expanded && '-rotate-90')} /></button>}
    </div>
    {!compact && expanded && <div id="assistant-chat-history" className="pl-3 pr-1 py-2 space-y-2">
      <button onClick={() => { navigate(newChatUrl()); onNavigate() }} className="flex items-center gap-2 text-[12px] px-2 py-2 w-full rounded-lg hover:bg-surface-2 text-left font-medium"><SquarePen size={15} />{t('New chat', 'แชตใหม่')}</button>
      <label className="flex items-center gap-2 border border-line rounded-lg px-2 py-1.5 text-muted"><Search size={13} className="shrink-0" /><input value={search} onChange={e => setSearch(e.target.value)} maxLength={200} placeholder={t('Search chats', 'ค้นหาแชต')} aria-label={t('Search chats', 'ค้นหาแชต')} className="min-w-0 w-full bg-transparent border-0 text-xs" /></label>
      <div className="flex items-center justify-between text-[11px] text-muted px-2"><span>{archived ? t('Archived', 'ที่เก็บไว้') : t('Chat history', 'ประวัติแชต')}</span><button className="hover:underline py-1" onClick={() => setArchived(!archived)}>{archived ? t('Back', 'กลับ') : t('Archived', 'ที่เก็บไว้')}</button></div>
      <div className="max-h-60 overflow-y-auto space-y-0.5">
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
      </div>
    </div>}
  </div>
}
