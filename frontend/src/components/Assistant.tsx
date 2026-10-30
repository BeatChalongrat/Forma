import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowUp, BookOpen, ClipboardCheck, FolderOpen, History, Square, SquarePen } from 'lucide-react'
import { api, type ChatTurn, type ChatDetail, type Me } from '../lib/api'
import { usePreferences } from '../lib/preferences'
import { Spinner } from './ui'
import { AssistantResponse } from './AssistantResponse'
import { ChatMenu } from './ChatMenu'
import { chatIdentity, newChatUrl } from '../lib/chats'

type Send = { question: string; request_id: string }

export function AssistantPage() {
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: api.me })
  const [params] = useSearchParams()
  if (!me) return <div className="p-6"><Spinner /></div>
  return <AssistantChat key={chatIdentity(me) + params.toString()} me={me} chatId={params.get('chat') || undefined} roundId={params.get('round') || undefined} datasetId={params.get('dataset') || undefined} fresh={params.has('new')} />
}

function AssistantChat({ me, chatId, roundId, datasetId, fresh }: { me: Me; chatId?: string; roundId?: string; datasetId?: string; fresh: boolean }) {
  const { prefs, t } = usePreferences(), qc = useQueryClient(), nav = useNavigate(), loc = useLocation()
  const identity = chatIdentity(me), lastKey = 'forma.last-chat:' + identity
  const queryKey = ['assistant-chat', identity, chatId]
  const [input, setInput] = useState(''), [older, setOlder] = useState<ChatTurn[]>([]), [before, setBefore] = useState<number | null | undefined>(), [loadingOlder, setLoadingOlder] = useState(false), [olderError, setOlderError] = useState(false)
  const [liveText, setLiveText] = useState(''), [streamReady, setStreamReady] = useState(false), [phase, setPhase] = useState<'research' | 'writing'>('research')
  const controller = useRef<AbortController | null>(null)
  useEffect(() => () => controller.current?.abort(), [])
  const [pending, setPending] = useState<Send | null>(null), [migrationError, setMigrationError] = useState(false)
  const textarea = useRef<HTMLTextAreaElement>(null), scroll = useRef<HTMLDivElement>(null), bottom = useRef<HTMLDivElement>(null), nearBottom = useRef(true), started = useRef(false)
  const { data: status } = useQuery({ queryKey: ['assistant-status'], queryFn: api.assistantStatus, refetchInterval: 30000 })
  const chat = useQuery({ queryKey, queryFn: () => api.chat(chatId!), enabled: !!chatId, refetchInterval: 3000, retry: 1 })
  const conversation = chat.data?.conversation
  const contextRound = conversation?.round_id || roundId, contextDataset = conversation?.dataset_id || datasetId
  const { data: round } = useQuery({ queryKey: ['round', contextRound], queryFn: () => api.round(contextRound!), enabled: !!contextRound })
  const turns = [...new Map([...older, ...(chat.data?.turns ?? [])].map(turn => [turn.id, turn])).values()].sort((a, b) => a.sequence - b.sequence)
  const refresh = () => { void qc.invalidateQueries({ queryKey }); void qc.invalidateQueries({ queryKey: ['assistant-chats'] }) }
  const saveTurn = (turn: ChatTurn) => qc.setQueryData<ChatDetail>(queryKey, old => old ? { ...old, conversation: { ...old.conversation, busy: false }, turns: [...old.turns.filter(x => x.id !== turn.id), turn].sort((a,b) => a.sequence - b.sequence) } : old)
  const send = useMutation({ mutationFn: (body: Send) => {
    const request = new AbortController(); controller.current = request
    return api.chatStream(chatId!, { ...body, language: prefs.language, timezone: prefs.timezone }, event => {
      if (event.type === 'started') { setStreamReady(true); void qc.invalidateQueries({ queryKey }) }
      if (event.type === 'phase') setPhase(event.phase)
      if (event.type === 'delta') setLiveText(text => text + event.text)
      if (event.type === 'reset') setLiveText('')
    }, request.signal)
  }, onMutate: body => { setPending(body); setInput(''); setLiveText(''); setStreamReady(false); setPhase('research'); nearBottom.current = true }, onSuccess: turn => { saveTurn(turn); setPending(null); setLiveText('') }, onSettled: () => { setStreamReady(false); refresh() } })
  const stop = useMutation({ mutationFn: (requestId: string) => api.chatStop(chatId!, requestId), onSuccess: turn => { saveTurn(turn); controller.current?.abort(); setPending(null); setStreamReady(false); refresh() } })
  const create = useMutation({ mutationFn: api.createChat })
  const edit = useMutation({ mutationFn: (body: { title?: string; archived?: boolean }) => api.editChat(chatId!, body), onSuccess: () => { refresh() } })

  // Browser storage holds only a selected ID. Messages are loaded from the account-scoped database.
  useEffect(() => {
    if (chatId) { try { localStorage.setItem(lastKey, chatId) } catch { /* optional convenience */ } }
    else if (!fresh && !roundId && !datasetId) {
      try { const last = localStorage.getItem(lastKey); if (last) nav('/assistant?chat=' + encodeURIComponent(last), { replace: true }) } catch { /* sidebar remains available */ }
    }
  }, [chatId, fresh, roundId, datasetId, lastKey, nav])

  // Import still-open legacy tabs once. The server discards client-supplied evidence/actions.
  useEffect(() => {
    let cancelled = false
    async function migrate() {
      try {
        for (const key of Object.keys(sessionStorage).filter(k => k.startsWith('forma.chat:'))) {
          const parts: unknown = JSON.parse(key.slice('forma.chat:'.length))
          if (!Array.isArray(parts) || JSON.stringify(parts.slice(0, 4)) !== identity) continue
          const saved: unknown = JSON.parse(sessionStorage.getItem(key) || '[]')
          if (!Array.isArray(saved) || !saved.length) continue
          const messages = saved.filter(m => typeof m?.question === 'string' && typeof m?.reply?.answer === 'string').slice(-12).map(m => ({ question: String(m.question).slice(0, 2000), answer: String(m.reply.answer).slice(0, 6000) }))
          if (!messages.length) continue
          const result = await api.importChat({ messages, round_id: typeof parts[4] === 'string' ? parts[4] : undefined, dataset_id: typeof parts[5] === 'string' ? parts[5] : undefined })
          sessionStorage.removeItem(key)
          void qc.invalidateQueries({ queryKey: ['assistant-chats'] })
          if (!cancelled && !chatId && !fresh && !roundId && !datasetId && !localStorage.getItem(lastKey)) nav('/assistant?chat=' + result.id, { replace: true })
        }
      } catch { if (!cancelled) setMigrationError(true) }
    }
    void migrate()
    return () => { cancelled = true }
  }, [identity, qc, chatId, fresh, roundId, datasetId, lastKey, nav])

  useEffect(() => {
    const incoming = (loc.state as { send?: Send } | null)?.send
    if (chatId && incoming && !started.current) {
      started.current = true
      send.mutate(incoming)
      nav(loc.pathname + loc.search, { replace: true, state: null })
    }
  }, [chatId, loc.state, loc.pathname, loc.search, nav, send])
  useLayoutEffect(() => {
    const node = textarea.current
    if (node) { node.style.height = 'auto'; node.style.height = Math.min(node.scrollHeight, 180) + 'px' }
  }, [input])
  const busy = send.isPending || !!conversation?.busy || create.isPending
  const stopRequest = streamReady ? pending?.request_id : turns.find(turn => turn.status === 'pending')?.request_id
  const hasContent = turns.length > 0 || !!pending
  const lastTurn = turns.at(-1)
  useEffect(() => { if (nearBottom.current) bottom.current?.scrollIntoView({ block: 'end' }) }, [turns.length, lastTurn?.status, pending, liveText])

  async function submit(question: string) {
    if (!question.trim() || busy || conversation?.archived) return
    const body = { question: question.trim(), request_id: crypto.randomUUID() }
    if (chatId) send.mutate(body)
    else {
      try {
        const created = await create.mutateAsync({ round_id: roundId, dataset_id: datasetId })
        void qc.invalidateQueries({ queryKey: ['assistant-chats'] })
        nav('/assistant?chat=' + created.id, { replace: true, state: { send: body } })
      } catch { /* Keep the draft for retry. */ }
    }
  }
  async function loadOlder() {
    const cursor = before === undefined ? chat.data?.older_before : before
    if (!chatId || !cursor) return
    setLoadingOlder(true); setOlderError(false)
    const height = scroll.current?.scrollHeight ?? 0
    try {
      const page = await api.chat(chatId, cursor)
      setOlder(current => [...page.turns, ...current]); setBefore(page.older_before)
      requestAnimationFrame(() => { if (scroll.current) scroll.current.scrollTop += scroll.current.scrollHeight - height })
    } catch { setOlderError(true) } finally { setLoadingOlder(false) }
  }
  const scope = round?.label || contextRound || contextDataset
  const prompts = [t('What needs my attention?', 'มีงานไหนต้องตรวจบ้าง'), t('How do I import files?', 'นำเข้าไฟล์อย่างไร'), t('Find recent imports', 'หาไฟล์ย้อนหลัง')]
  const composer = <form className="assistant-composer" onSubmit={e => { e.preventDefault(); void submit(input) }}>
    <div className="assistant-compose-box"><textarea ref={textarea} rows={1} maxLength={2000} value={input} onChange={e => setInput(e.target.value)} aria-label={t('Your question', 'คำถามของคุณ')} placeholder={t('Ask FORMA anything', 'ถาม FORMA ได้เลย')} disabled={!!conversation?.archived} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void submit(input) } }} /><button type={busy ? "button" : "submit"} className="assistant-send" disabled={busy ? !stopRequest || stop.isPending : !input.trim() || !!conversation?.archived} onClick={busy ? () => { if (stopRequest) stop.mutate(stopRequest) } : undefined} aria-label={busy ? t("Stop response", "หยุดคำตอบ") : t("Send question", "ส่งคำถาม")}>{busy ? <Square size={14} fill="currentColor" /> : <ArrowUp size={19} />}</button></div>
    <div className="assistant-composer-note"><span className="inline-flex items-center gap-1.5 min-w-0"><FolderOpen size={13} className="shrink-0" />{scope ? <Link className="truncate hover:underline" to={contextRound ? `/rounds/${contextRound}` : `/datasets/${contextDataset}`}>{scope}</Link> : t('Search all FORMA', 'ค้นหาทั้ง FORMA')}</span><span>{t('Chats are saved automatically', 'บันทึกแชตให้อัตโนมัติ')}</span></div>
  </form>

  return <div className="assistant-page">
    <header className="assistant-header gap-3"><div className="min-w-0"><h1 className="font-medium text-navy truncate">{conversation?.title || t('FORMA assistant', 'ผู้ช่วย FORMA')}</h1><span className="text-xs text-muted">{status ? status.enabled ? `${status.provider} · ${status.model}` : t('AI is not connected', 'ยังไม่ได้เชื่อมต่อ AI') : t('Checking connection…', 'กำลังอ่านการเชื่อมต่อ…')}</span></div><div className="flex shrink-0">
      {conversation && <ChatMenu chat={conversation} identity={identity} busy={busy} />}
      <button className="btn btn-ghost" onClick={() => nav(newChatUrl())} aria-label={t('New chat', 'แชตใหม่')} title={t('New chat', 'แชตใหม่')}><SquarePen size={19} /></button></div></header>
    {conversation?.mapping_id && <div className="px-5 py-2 border-b border-line text-xs text-muted flex flex-wrap gap-2"><Link className="underline text-navy" to={`/review?mapping=${conversation.mapping_id}&round=${conversation.round_id}`}>{t('Return to the reviewed file', 'กลับไปไฟล์ที่กำลังตรวจ')}</Link><span>· {t('This chat has that version’s context. You can ask about other FORMA work too.', 'แชตนี้มีบริบทของเวอร์ชันที่เปิดมา ถามเรื่องอื่นใน FORMA ต่อได้')}</span></div>}
    {stop.isError && <p role="alert" className="text-bad px-5">{t('Could not stop yet. Try Stop again.', 'ยังหยุดไม่ได้ ลองกดหยุดอีกครั้ง')}</p>}
    {edit.isError && <p role="alert" className="text-bad px-5">{t('Could not update chat. Try again.', 'ยังแก้ไขแชตไม่ได้ ลองอีกครั้ง')}</p>}
    {migrationError && <p className="text-xs text-warn px-5 py-2">{t('Could not import the old tab history. It remains in this tab; reload to retry.', 'ยังย้ายประวัติเก่าไม่สำเร็จ ข้อความเดิมยังอยู่ในแท็บนี้ รีโหลดเพื่อลองใหม่')}</p>}
    {chatId && chat.isPending ? <div className="flex-1 grid place-items-center"><Spinner /></div> : chat.isError && !chat.data ? <div role="alert" className="p-6 text-bad">{t('This chat could not be opened. Check your access or try again.', 'ยังเปิดแชตนี้ไม่ได้ ตรวจสอบสิทธิ์หรือลองอีกครั้ง')}<button className="btn ml-2" onClick={() => void chat.refetch()}>{t('Retry', 'ลองอีกครั้ง')}</button></div> : <>
      {!hasContent ? <div className="assistant-landing"><div className="assistant-welcome"><p className="text-sm text-muted mb-3">{t('Hello', 'สวัสดี')} {prefs.display_name || me.name}</p><h2>{t('What can I help you with today?', 'วันนี้อยากให้ช่วยเรื่องอะไร?')}</h2></div>{composer}<div className="flex flex-wrap justify-center gap-2 px-5 mt-3">{prompts.map((prompt, i) => { const Icon = [ClipboardCheck, BookOpen, History][i]; return <button className="assistant-prompt" disabled={busy} key={prompt} onClick={() => void submit(prompt)}><Icon size={15} />{prompt}</button> })}</div>{create.isError && <p role="alert" className="text-bad text-center p-3">{t('Could not start a chat. Your draft is still here; try again.', 'ยังเริ่มแชตไม่ได้ คำถามยังอยู่ ลองส่งอีกครั้ง')}</p>}</div> : <>
        <div ref={scroll} className="assistant-scroll" onScroll={() => { const el = scroll.current; if (el) nearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 100 }}><div className="assistant-content">
          {!!(before === undefined ? chat.data?.older_before : before) && <button className="btn btn-ghost mx-auto block mb-6" disabled={loadingOlder} onClick={() => void loadOlder()}>{t('Load earlier messages', 'อ่านข้อความก่อนหน้า')}</button>}{olderError && <p className="text-bad text-xs">{t('Could not load earlier messages. Try again.', 'ยังโหลดข้อความเก่าไม่ได้ ลองอีกครั้ง')}</p>}
          <div className="space-y-10" aria-live="polite">{turns.map(turn => <article key={turn.id} className="space-y-5"><div className="assistant-question">{turn.question}</div>{turn.status === 'pending' ? <LiveAnswer text={turn.request_id === pending?.request_id ? liveText || turn.reply?.answer || '' : turn.reply?.answer || ''} phase={phase} /> : turn.reply?.answer && <AssistantResponse reply={turn.reply} me={me} onClarify={turn.id === lastTurn?.id && turn.status === 'complete' ? text => void submit(text) : undefined} disabled={busy || !!conversation?.archived} />}{turn.status === 'stopped' && <p className="text-xs text-muted">{t('Stopped. This reply may be incomplete. You can send another question.', 'หยุดแล้ว คำตอบนี้อาจยังไม่ครบ ส่งคำถามต่อได้เลย')}</p>}{turn.status === 'failed' && <div className="text-sm text-muted">{t('No complete reply yet. Your question is saved.', 'ยังตอบไม่สำเร็จ คำถามบันทึกไว้แล้ว')}<button disabled={busy || conversation?.archived} className="btn btn-sm ml-2" onClick={() => send.mutate({ question: turn.question, request_id: turn.request_id })}>{t('Retry', 'ลองอีกครั้ง')}</button></div>}{turn.reply?.reason === 'imported_history' && <p className="text-xs text-muted">{t('Imported from previous tab history', 'นำเข้าจากบทสนทนาในแท็บเดิม')}</p>}</article>)}
            {pending && !turns.some(turn => turn.request_id === pending.request_id) && <div className="space-y-5"><div className="assistant-question">{pending.question}</div>{send.isPending && <LiveAnswer text={liveText} phase={phase} />}</div>}
            {send.isError && send.error?.name !== 'AbortError' && <div role="alert" className="text-bad">{t('Connection interrupted. The question may still be processing.', 'การเชื่อมต่อสะดุด ระบบอาจยังตอบอยู่')}<button className="btn btn-sm ml-2" disabled={busy} onClick={() => { if (send.variables) send.mutate(send.variables) }}>{t('Retry', 'ลองอีกครั้ง')}</button></div>}
          </div><div ref={bottom} className="h-8" /></div></div>
        {conversation?.archived && <p className="text-xs text-muted text-center p-2">{t('This chat is archived.', 'แชตนี้เก็บไว้แล้ว')}<button className="underline ml-2" onClick={() => edit.mutate({ archived: false })}>{t('Restore to continue', 'นำกลับมาเพื่อคุยต่อ')}</button></p>}
        {composer}
      </>}
    </>}
  </div>
}

function LiveAnswer({ text, phase }: { text: string; phase: 'research' | 'writing' }) {
  const { t } = usePreferences()
  return <div className="space-y-3"><div className="flex items-center gap-2 text-[13px] font-medium text-navy"><span className="h-1.5 w-1.5 bg-brand-lime rounded-full" />FORMA</div>{text && <div className="assistant-prose whitespace-pre-wrap break-words">{text}</div>}<div role="status" className="flex items-center gap-2 text-xs text-muted"><Spinner />{phase === 'writing' || text ? t('Writing…', 'กำลังตอบ…') : t('Checking data and evidence…', 'กำลังค้นข้อมูลและตรวจหลักฐาน…')}</div></div>
}
