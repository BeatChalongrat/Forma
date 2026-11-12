import { useChatDraft } from '../lib/chatDraft'
import { ReplyActions } from './ReplyActions'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowUp, BookOpen, BookmarkX, Ghost, ListChecks, FolderOpen, Inbox, Paperclip, Square, SquarePen, X } from 'lucide-react'
import { api, type ChatTurn, type ChatDetail, type Me } from '../lib/api'
import { usePreferences } from '../lib/preferences'
import { Spinner } from './ui'
import { LoadError } from './LoadError'
import { AssistantResponse } from './AssistantResponse'
import { ChatFiles } from './ChatFiles'
import { ChatQuestion } from './ChatQuestion'
import { ArtifactPanel } from './ArtifactPanel'
import { DocumentJobs } from './DocumentJobs'
import { ReportTemplates } from './ReportTemplates'
import { ChatMenu } from './ChatMenu'
import { chatIdentity, newChatUrl } from '../lib/chats'
import { useSidebarHold } from '../lib/sidebar'
import { assistantPresentation } from '../lib/assistantPresentation'

type Send = { clarification_turn_id?: string; question: string; request_id: string; attachment_ids?: string[]; artifact_id?: string; target?: string }

const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp']
const MAX_FILES = 5

export function AssistantPage() {
  const meQuery = useQuery({ queryKey: ['me'], queryFn: api.me })
  const me = meQuery.data
  const { t } = usePreferences()
  const [params] = useSearchParams()
  if (!me) return <div className="p-6 space-y-4">
    <Link className="btn btn-ghost btn-sm" to="/">← {t('Back to home', 'กลับหน้าหลัก')}</Link>
    {meQuery.isError ? <LoadError error={meQuery.error} busy={meQuery.isFetching} retry={() => void meQuery.refetch()} /> : <div role="status"><Spinner /> {t('Loading your account…', 'กำลังโหลดบัญชีของคุณ…')}</div>}
  </div>
  return <AssistantChat key={chatIdentity(me) + params.toString()} me={me} chatId={params.get('chat') || undefined} roundId={params.get('round') || undefined} datasetId={params.get('dataset') || undefined} fresh={params.has('new')} artifactId={params.get('artifact') || undefined} />
}

function AssistantChat({ me, chatId, roundId, datasetId, fresh, artifactId }: { me: Me; chatId?: string; roundId?: string; datasetId?: string; fresh: boolean; artifactId?: string }) {
  const { prefs, t } = usePreferences(), qc = useQueryClient(), nav = useNavigate(), loc = useLocation()
  const identity = chatIdentity(me), lastKey = 'forma.last-chat:' + identity
  const queryKey = ['assistant-chat', identity, chatId]
  const [older, setOlder] = useState<ChatTurn[]>([]), [before, setBefore] = useState<number | null | undefined>(), [loadingOlder, setLoadingOlder] = useState(false), [olderError, setOlderError] = useState(false)
  const [liveText, setLiveText] = useState(''), [streamReady, setStreamReady] = useState(false), [phase, setPhase] = useState<'research' | 'coding' | 'writing'>('research')
  const [connection, setConnection] = useState<'live' | 'reconnecting' | 'following'>('live')
  const [picked, setPicked] = useState<File[]>([]), [uploading, setUploading] = useState(false), [fileError, setFileError] = useState('')
  const [reportVersion, setReportVersion] = useState<{ id: string; number: number }>()
  const [openArtifact, setOpenArtifact] = useState(artifactId), [target, setTarget] = useState(''), [pane, setPane] = useState<'chat' | 'work'>('work')
  const [privacy, setPrivacy] = useState<'normal' | 'memory-off' | 'incognito'>('normal')
  useSidebarHold(!!openArtifact)
  const filePicker = useRef<HTMLInputElement>(null)
  const controller = useRef<AbortController | null>(null)
  useEffect(() => () => controller.current?.abort(), [])
  const [pending, setPending] = useState<Send | null>(null), [migrationError, setMigrationError] = useState(false)
  const textarea = useRef<HTMLTextAreaElement>(null), scroll = useRef<HTMLDivElement>(null), bottom = useRef<HTMLDivElement>(null), nearBottom = useRef(true), started = useRef(false)
  const { data: status } = useQuery({ queryKey: ['assistant-status'], queryFn: api.assistantStatus, refetchInterval: 30000 })
  const chat = useQuery({ queryKey, queryFn: () => api.chat(chatId!), enabled: !!chatId, refetchInterval: 3000, retry: 1 })
  const conversation = chat.data?.conversation
  const draftStore = useChatDraft(chatId, identity, privacy === 'incognito' || !!conversation?.incognito, !chatId || !!conversation)
  const input = draftStore.draft.input ?? ''
  const setInput = (value: string) => draftStore.update({ input: value })
  const [away, setAway] = useState(false)
  async function alternative(turn: ChatTurn, question: string, mode: 'edit' | 'regenerate'): Promise<boolean> {
    if (!chatId) return false
    try {
      const result = await api.chatBranch(chatId, turn.id, question, mode, crypto.randomUUID())
      void qc.invalidateQueries({ queryKey: ['assistant-chats'] })
      nav('/assistant?chat=' + encodeURIComponent(result.id))
      return true
    } catch { setFileError(t('Could not open an alternative. Your original chat is unchanged.', 'ยังเปิดอีกทางเลือกไม่ได้ แชตเดิมยังอยู่ครบ')); return false }
  }
  useEffect(() => { if (conversation?.incognito) { try { localStorage.removeItem(lastKey) } catch { /* optional convenience */ } } }, [conversation?.incognito, lastKey])
  const contextRound = conversation?.round_id || roundId, contextDataset = conversation?.dataset_id || datasetId
  const { data: round } = useQuery({ queryKey: ['round', contextRound], queryFn: () => api.round(contextRound!), enabled: !!contextRound })
  const turns = [...new Map([...older, ...(chat.data?.turns ?? [])].map(turn => [turn.id, turn])).values()].sort((a, b) => a.sequence - b.sequence)
  const refresh = () => { void qc.invalidateQueries({ queryKey }); void qc.invalidateQueries({ queryKey: ['assistant-chats'] }) }
  const saveTurn = (turn: ChatTurn) => qc.setQueryData<ChatDetail>(queryKey, old => old ? { ...old, conversation: { ...old.conversation, busy: false }, turns: [...old.turns.filter(x => x.id !== turn.id), turn].sort((a,b) => a.sequence - b.sequence) } : old)
  const showWork = (turn: ChatTurn) => {
    void qc.invalidateQueries({ queryKey: ['artifact'] }); void qc.invalidateQueries({ queryKey: ['artifacts'] })
    const made = turn.reply ? assistantPresentation(turn.reply).artifacts : [], first = made.find(item => item.kind !== 'image') ?? made[0]
    if (first) { setReportVersion({ id: first.id, number: first.version }); setOpenArtifact(first.id); setPane('work') }
  }
  const send = useMutation({ mutationFn: (body: Send) => {
    const request = new AbortController(); controller.current = request
    return api.chatStream(chatId!, { ...body, language: prefs.language, timezone: prefs.timezone }, event => {
      if (event.type === 'started') { const current = draftStore.current(); draftStore.update({ input: current.input?.trim() === body.question.trim() ? '' : current.input, pending_chat_id: undefined, attachment_ids: (current.attachment_ids ?? []).filter(id => !body.attachment_ids?.includes(id)), unuploaded: [] }); setStreamReady(true); void qc.invalidateQueries({ queryKey }) }
      if (event.type === 'phase') setPhase(event.phase)
      if (event.type === 'delta') setLiveText(text => text + event.text)
      if (event.type === 'reset') setLiveText('')
      if (event.type === 'connection') setConnection(event.state)
      if (event.type === 'snapshot') { if (event.turn.reply?.phase) setPhase(event.turn.reply.phase); setLiveText(event.turn.reply?.answer ?? ''); setStreamReady(event.turn.status === 'pending'); void qc.invalidateQueries({ queryKey }) }
    }, request.signal)
  }, onMutate: body => { setPending(body); setConnection('live'); setLiveText(''); setStreamReady(false); setPhase('research'); nearBottom.current = true }, onSuccess: turn => { saveTurn(turn); showWork(turn); setPending(null); setLiveText('') }, onSettled: () => { setStreamReady(false); refresh() } })
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
    if (chatId && incoming && draftStore.ready && !started.current) {
      started.current = true
      send.mutate(incoming)
      nav(loc.pathname + loc.search, { replace: true, state: null })
    }
  }, [chatId, loc.state, loc.pathname, loc.search, nav, send, draftStore.ready])
  useLayoutEffect(() => {
    const node = textarea.current
    if (node) { node.style.height = 'auto'; node.style.height = Math.min(node.scrollHeight, 180) + 'px' }
  }, [input])
  const busy = !draftStore.ready || send.isPending || !!conversation?.busy || create.isPending || uploading
  const stopRequest = streamReady ? pending?.request_id : turns.find(turn => turn.status === 'pending')?.request_id
  const hasContent = turns.length > 0 || !!pending
  const lastTurn = turns.at(-1)
  const seenTurnId = lastTurn?.id, seenTurnStatus = lastTurn?.status
  useEffect(() => { if (chatId && seenTurnId && seenTurnStatus !== 'pending') { void api.chatSeen(chatId).then(() => qc.invalidateQueries({ queryKey: ['assistant-work'] })) } }, [chatId, seenTurnId, seenTurnStatus, qc])
  useEffect(() => { if (nearBottom.current) bottom.current?.scrollIntoView({ block: 'end' }) }, [turns.length, lastTurn?.status, lastTurn?.reply?.answer, pending, liveText])

  function pick(files: File[]) {
    if (busy) return
    const fits = (f: File) => IMAGE_TYPES.includes(f.type) ? !!status?.vision : !!status?.code_sandbox && status.file_types.some(ext => f.name.toLowerCase().endsWith(ext))
    const refused = files.filter(f => !fits(f))
    setFileError(refused.length ? t(`Cannot use ${refused[0].name} here: ${IMAGE_TYPES.includes(refused[0].type) ? 'this model cannot read images' : 'this file type is not supported'}.`, `ใช้ ${refused[0].name} ไม่ได้: ${IMAGE_TYPES.includes(refused[0].type) ? 'โมเดลนี้อ่านรูปไม่ได้' : 'ไม่รองรับไฟล์ชนิดนี้'}`) : '')
    setPicked(current => [...current, ...files.filter(fits)].slice(0, MAX_FILES))
    draftStore.update({ unuploaded: [...picked, ...files.filter(fits)].slice(0, MAX_FILES).map(f => f.name) })
  }
  async function submit(question: string, clarificationTurnId?: string) {
    if (!question.trim() || busy || conversation?.archived) return false
    if (draftStore.draft.unuploaded?.length && !picked.length) { setFileError(t('Please select the remaining files again, or remove them before sending.', 'เลือกไฟล์ที่ยังไม่ได้อัปโหลดอีกครั้ง หรือเอารายการออกก่อนส่ง')); return false }
    const body: Send = { question: question.trim(), request_id: crypto.randomUUID(), ...(clarificationTurnId ? { clarification_turn_id: clarificationTurnId } : {}) }
    // Files go up only now: opening a new chat reloads this view, and a chat should not start with a stray upload.
    setUploading(true); setFileError('')
    try {
      const id = chatId ?? draftStore.draft.pending_chat_id ?? (await create.mutateAsync({ round_id: roundId, dataset_id: datasetId, memory_off: privacy === 'memory-off', incognito: privacy === 'incognito' })).id
      if (!chatId) draftStore.update({ pending_chat_id: id })
      const ids: string[] = [...(draftStore.draft.attachment_ids ?? [])]
      const remaining = [...picked]
      for (const file of picked) {
        ids.push((await api.attachChatFile(id, file)).id)
        remaining.shift(); setPicked([...remaining])
        draftStore.update({ attachment_ids: [...ids], unuploaded: remaining.map(item => item.name) })
      }
      if (ids.length) body.attachment_ids = ids
      if (openArtifact) { body.artifact_id = openArtifact; if (target) body.target = target }
      setPicked([]); setTarget('')
      if (chatId) await send.mutateAsync(body)
      else {
        const saved = await api.chatState(id)
        await api.saveChatState(id, saved.revision, { input: question, attachment_ids: ids })
        draftStore.update({ input: '', pending_chat_id: undefined, attachment_ids: [], unuploaded: [] })
        void qc.invalidateQueries({ queryKey: ['assistant-chats'] })
        nav('/assistant?chat=' + id, { replace: true, state: { send: body } })
      }
      return true
    } catch (error) {
      setFileError(error instanceof Error ? error.message : t('Could not send. Try again.', 'ส่งไม่สำเร็จ ลองอีกครั้ง')) // keep the draft for retry
      return false
    } finally { setUploading(false) }
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
  const prompts = [t('What needs my attention?', 'มีงานไหนต้องตรวจบ้าง'), t('Help me choose a formal report template and ask which data to use.', 'ช่วยเลือกแม่แบบรายงานทางการ แล้วถามว่าต้องการใช้ข้อมูลช่วงไหน'), t('Find recent imports', 'หาไฟล์ย้อนหลัง')]
  const accept = [...(status?.vision ? IMAGE_TYPES : []), ...(status?.code_sandbox ? status.file_types : [])].join(',')
  const composer = <form className="assistant-composer" onSubmit={e => { e.preventDefault(); void submit(input) }} onDragOver={e => { if (accept) e.preventDefault() }} onDrop={e => { if (accept && e.dataTransfer.files.length) { e.preventDefault(); pick([...e.dataTransfer.files]) } }}>
    {draftStore.error && <p role="alert" className="text-xs text-warn mb-2">{draftStore.error} <button type="button" className="underline" onClick={() => void draftStore.useSaved()}>{t("Use saved draft", "ใช้ร่างที่บันทึกไว้")}</button></p>}
    {!!draftStore.draft.attachment_ids?.length && <p className="text-xs text-muted mb-2">{t('Uploaded attachments retained: ', 'เก็บไฟล์แนบที่อัปโหลดแล้ว: ')}{draftStore.draft.attachment_ids.length} <button type="button" className="underline" onClick={() => draftStore.update({ attachment_ids: [] })}>{t('Remove', 'เอาออก')}</button></p>}
    {!!draftStore.draft.unuploaded?.length && !picked.length && <p className="text-xs text-warn">{t('Select these files again before sending: ', 'เลือกไฟล์ที่ยังไม่ได้อัปโหลดอีกครั้งก่อนส่ง: ')}{draftStore.draft.unuploaded.join(', ')} <button type="button" className="underline" onClick={() => draftStore.update({ unuploaded: [] })}>{t('Remove these files', 'เอารายการเหล่านี้ออก')}</button></p>}
    {!!picked.length && <ul className="flex flex-wrap gap-2 mb-2">{picked.map((file, i) => <li key={file.name + i} className="assistant-picked"><span className="truncate">{file.name}</span><button type="button" className="p-1 rounded-full hover:bg-surface-2" aria-label={t(`Remove ${file.name}`, `เอา ${file.name} ออก`)} onClick={() => { const remaining = picked.filter((_, n) => n !== i); setPicked(remaining); draftStore.update({ unuploaded: remaining.map(file => file.name) }) }}><X size={14} /></button></li>)}</ul>}
    {!!fileError && <p role="alert" className="text-xs text-bad mb-2 px-3">{fileError}</p>}
    {!!target && <div className="assistant-target"><span className="truncate">{t('Change', 'แก้')}: {target}</span><button type="button" className="p-1 rounded-full" aria-label={t('Clear what you picked', 'ยกเลิกสิ่งที่เลือก')} onClick={() => setTarget('')}><X size={14} /></button></div>}
    <div className="assistant-compose-box"><input ref={filePicker} type="file" multiple hidden accept={accept} onChange={e => { pick([...(e.target.files ?? [])]); e.target.value = '' }} /><button type="button" className="assistant-attach" disabled={busy || !accept || picked.length >= MAX_FILES || !!conversation?.archived} onClick={() => filePicker.current?.click()} aria-label={t('Attach a file', 'แนบไฟล์')} title={accept ? t('Attach an image or a data file', 'แนบรูปหรือไฟล์ข้อมูล') : t('The connected model cannot read files', 'โมเดลที่ใช้อยู่อ่านไฟล์แนบไม่ได้')}><Paperclip size={18} /></button><textarea ref={textarea} rows={1} maxLength={2000} value={input} onChange={e => setInput(e.target.value)} onPaste={e => { const files = [...e.clipboardData.files]; if (files.length && accept) { e.preventDefault(); pick(files) } }} aria-label={t('Your question', 'คำถามของคุณ')} placeholder={target ? t('Say how this should change', 'บอกว่าอยากให้จุดนี้เปลี่ยนอย่างไร') : openArtifact ? t('Ask for a change, or ask anything', 'สั่งแก้ผลงานนี้ หรือถามต่อได้เลย') : t('Ask FORMA anything', 'ถาม FORMA ได้เลย')} disabled={!draftStore.ready || !!conversation?.archived} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void submit(input) } }} /><button type={busy ? "button" : "submit"} className="assistant-send" disabled={busy ? !stopRequest || stop.isPending : !input.trim() || !!conversation?.archived} onClick={busy ? () => { if (stopRequest) stop.mutate(stopRequest) } : undefined} aria-label={busy ? t("Stop response", "หยุดคำตอบ") : t("Send question", "ส่งคำถาม")}>{busy ? <Square size={14} fill="currentColor" /> : <ArrowUp size={19} />}</button></div>
    <div className="assistant-composer-note"><span className="inline-flex items-center gap-1.5 min-w-0"><FolderOpen size={13} className="shrink-0" />{scope ? <Link className="truncate hover:underline" to={contextRound ? `/rounds/${contextRound}` : `/datasets/${contextDataset}`}>{scope}</Link> : t('Search all FORMA', 'ค้นหาทั้ง FORMA')}</span><span>{status?.vision ? t('Reads images', 'อ่านรูปได้') : <Link className="hover:underline" to="/settings?tab=ai">{t('This model cannot read images', 'โมเดลนี้อ่านรูปไม่ได้')}</Link>} · {status?.code_sandbox ? t('Can run code', 'รันโค้ดได้') : t('Sandbox is off', 'ห้องทดลองปิดอยู่')}</span></div>
  </form>

  return <div className={`assistant-split${openArtifact ? ' has-work' : ''}`} data-pane={pane}>
    {!!openArtifact && <div className="assistant-panes" role="group" aria-label={t('View', 'มุมมอง')}><button type="button" className={pane === 'chat' ? 'is-on' : ''} aria-pressed={pane === 'chat'} onClick={() => setPane('chat')}>{t('Chat', 'แชต')}</button><button type="button" className={pane === 'work' ? 'is-on' : ''} aria-pressed={pane === 'work'} onClick={() => setPane('work')}>{t('Work', 'ผลงาน')}</button></div>}
    <div className="assistant-page">
    <header className="assistant-header gap-3"><div className="min-w-0"><h1 className="font-medium text-navy truncate">{conversation?.title || t('FORMA assistant', 'ผู้ช่วย FORMA')}</h1>{conversation?.incognito ? <span className="assistant-mode"><Ghost size={13} />{t('Incognito: not in history or memory', 'แชตไม่บันทึก: ไม่อยู่ในประวัติและความจำ')}</span> : conversation?.memory_off ? <span className="assistant-mode"><BookmarkX size={13} />{t('Memory is off in this chat', 'แชตนี้ไม่ใช้ความจำ')}</span> : null}<span className="text-xs text-muted block">{status ? status.enabled ? `${status.provider} · ${status.model}` : t('AI is not connected', 'ยังไม่ได้เชื่อมต่อ AI') : t('Checking connection…', 'กำลังอ่านการเชื่อมต่อ…')}</span></div><div className="flex shrink-0">
      {conversation && <ChatMenu chat={conversation} identity={identity} busy={busy} />}
      {chatId && <Link className="btn btn-ghost btn-sm" to={`/inspection?kind=assistant&scope=chat&id=${encodeURIComponent(chatId)}`}>{t('Work history', 'ประวัติงาน')}</Link>}
      <ReportTemplates onSelect={text => { setInput(text); setPane('chat'); textarea.current?.focus() }} />
      <button className="btn btn-ghost" onClick={() => nav(newChatUrl())} aria-label={t('New chat', 'แชตใหม่')} title={t('New chat', 'แชตใหม่')}><SquarePen size={19} /></button></div></header>
    {conversation?.mapping_id && <div className="px-5 py-2 border-b border-line text-xs text-muted flex flex-wrap gap-2"><Link className="underline text-navy" to={`/review?mapping=${conversation.mapping_id}&round=${conversation.round_id}`}>{t('Return to the reviewed file', 'กลับไปไฟล์ที่กำลังตรวจ')}</Link><span>· {t('This chat has that version’s context. You can ask about other FORMA work too.', 'แชตนี้มีบริบทของเวอร์ชันที่เปิดมา ถามเรื่องอื่นใน FORMA ต่อได้')}</span></div>}
    {connection !== 'live' && send.isPending && <p role="status" className="px-5 py-2 text-xs text-muted">{connection === 'reconnecting' ? t('Connection interrupted. Reconnecting to the saved response…', 'การเชื่อมต่อสะดุด กำลังเชื่อมต่อคำตอบเดิมอีกครั้ง…') : t('Reconnected. Following the original response without resending your question.', 'เชื่อมต่อแล้ว กำลังรับคำตอบเดิมต่อโดยไม่ส่งคำถามซ้ำ')}</p>}
    {stop.isError && <p role="alert" className="text-bad px-5">{t('Could not stop yet. Try Stop again.', 'ยังหยุดไม่ได้ ลองกดหยุดอีกครั้ง')}</p>}
    {edit.isError && <p role="alert" className="text-bad px-5">{t('Could not update chat. Try again.', 'ยังแก้ไขแชตไม่ได้ ลองอีกครั้ง')}</p>}
    {migrationError && <p className="text-xs text-warn px-5 py-2">{t('Could not import the old tab history. It remains in this tab; reload to retry.', 'ยังย้ายประวัติเก่าไม่สำเร็จ ข้อความเดิมยังอยู่ในแท็บนี้ รีโหลดเพื่อลองใหม่')}</p>}
    {chatId && chat.isPending ? <div className="flex-1 grid place-items-center"><Spinner /></div> : chat.isError && !chat.data ? <div role="alert" className="p-6 text-bad">{t('This chat could not be opened. Check your access or try again.', 'ยังเปิดแชตนี้ไม่ได้ ตรวจสอบสิทธิ์หรือลองอีกครั้ง')}<button className="btn ml-2" onClick={() => void chat.refetch()}>{t('Retry', 'ลองอีกครั้ง')}</button></div> : <>
      {draftStore.branch.chat_id && <div className="px-5 py-2 text-xs text-muted border-b border-line">{draftStore.branch.mode === 'regenerate' ? t('Rewriting from saved evidence. Data is not refreshed and actions are not repeated.', 'ตอบใหม่จากหลักฐานเดิม ยังไม่ได้ดึงข้อมูลล่าสุดและไม่ทำรายการซ้ำ') : t('Alternative conversation. Previous actions are unchanged.', 'บทสนทนาอีกทางเลือก การอนุมัติและการแก้ข้อมูลเดิมไม่เปลี่ยน')} <Link className="underline" to={'/assistant?chat=' + encodeURIComponent(draftStore.branch.chat_id)}>{t('Open original chat', 'กลับแชตเดิม')}</Link></div>}{away && <button className="btn btn-sm self-center" onClick={() => { nearBottom.current = true; bottom.current?.scrollIntoView({ block: 'end' }); setAway(false) }}>{t('Latest message', 'ไปข้อความล่าสุด')} ↓</button>}{!hasContent ? <div className="assistant-landing"><div className="assistant-welcome"><p className="text-sm text-muted mb-3">{t('Hello', 'สวัสดี')} {prefs.display_name || me.name}</p><h2>{t('What can I help you with today?', 'วันนี้อยากให้ช่วยเรื่องอะไร?')}</h2></div>{composer}<div className="assistant-privacy" role="group" aria-label={t('Memory for this chat', 'ความจำของแชตนี้')}><button type="button" disabled={!!chatId || !!draftStore.draft.pending_chat_id || busy} className={privacy === 'memory-off' ? 'is-on' : ''} aria-pressed={privacy === 'memory-off'} title={t('Stays in your history, but does not use or add to memory', 'ยังอยู่ในประวัติ แต่ไม่ใช้และไม่เพิ่มความจำ')} onClick={() => setPrivacy(value => value === 'memory-off' ? 'normal' : 'memory-off')}><BookmarkX size={14} />{t('No memory', 'ไม่ใช้ความจำ')}</button><button type="button" disabled={!!chatId || !!draftStore.draft.pending_chat_id || busy} className={privacy === 'incognito' ? 'is-on' : ''} aria-pressed={privacy === 'incognito'} title={t('Not in your history or memory. Gone after a day.', 'ไม่อยู่ในประวัติและความจำ ลบเองภายใน 1 วัน')} onClick={() => setPrivacy(value => value === 'incognito' ? 'normal' : 'incognito')}><Ghost size={14} />{t('Incognito', 'แชตไม่บันทึก')}</button></div><div className="flex flex-wrap justify-center gap-2 px-5 mt-3">{prompts.map((prompt, i) => { const Icon = [ListChecks, BookOpen, Inbox][i]; return <button className="assistant-prompt" disabled={busy} key={prompt} onClick={() => void submit(prompt)}><Icon size={15} />{prompt}</button> })}</div>{create.isError && <p role="alert" className="text-bad text-center p-3">{t('Could not start a chat. Your draft is still here; try again.', 'ยังเริ่มแชตไม่ได้ คำถามยังอยู่ ลองส่งอีกครั้ง')}</p>}</div> : <>
        <div ref={scroll} className="assistant-scroll" onScroll={() => { const el = scroll.current; if (el) { nearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 100; setAway(!nearBottom.current) } }}><div className="assistant-content">
          {!!(before === undefined ? chat.data?.older_before : before) && <button className="btn btn-ghost mx-auto block mb-6" disabled={loadingOlder} onClick={() => void loadOlder()}>{t('Load earlier messages', 'อ่านข้อความก่อนหน้า')}</button>}{olderError && <p className="text-bad text-xs">{t('Could not load earlier messages. Try again.', 'ยังโหลดข้อความเก่าไม่ได้ ลองอีกครั้ง')}</p>}
          <div className="space-y-10" aria-live="polite">{turns.map(turn => <article key={turn.id} className="space-y-5">{!!turn.attachments?.length && <div className="flex flex-col items-end gap-2"><ChatFiles files={turn.attachments} /></div>}<ChatQuestion turn={turn} disabled={busy || !!conversation?.archived} onSend={text => alternative(turn, text, 'edit')} />{turn.status === 'pending' ? <LiveAnswer text={turn.request_id === pending?.request_id ? liveText || turn.reply?.answer || '' : turn.reply?.answer || ''} phase={phase} background={!send.isPending} /> : turn.reply?.answer && <AssistantResponse reply={turn.reply} me={me} clarificationState={draftStore.draft.clarifications?.[turn.id]} onClarificationState={value => draftStore.update({ clarifications: { ...draftStore.draft.clarifications, [turn.id]: value } })} onClarify={turn.status === 'complete' ? text => submit(text, turn.id) : undefined} disabled={busy || !!conversation?.archived} openArtifact={openArtifact} onOpenArtifact={(id, version) => { setReportVersion(version ? { id, number: version } : undefined); setOpenArtifact(id); setTarget(''); setPane('work') }} />}{chatId && turn.status === 'complete' && <ReplyActions chatId={chatId} turnId={turn.id} saved={turn.feedback} disabled={busy} onRegenerate={() => void alternative(turn, turn.question, 'regenerate')} />}{turn.status === 'stopped' && <p className="text-xs text-muted">{t('Stopped. This reply may be incomplete. You can send another question.', 'หยุดแล้ว คำตอบนี้อาจยังไม่ครบ ส่งคำถามต่อได้เลย')}</p>}{turn.status === 'failed' && turn.reply?.reason === 'recovery_paused' && <p className="text-sm text-warn">{t('Recovery paused after reaching a limit or a workflow change. Saved work is retained. Review the activity log before starting another request.', 'พักการกู้คืนเมื่อถึงขีดจำกัดหรือขั้นตอนงานเปลี่ยน ผลงานที่บันทึกแล้วอยู่ครบ ตรวจประวัติการทำงานก่อนเริ่มคำขอใหม่')}</p>}{turn.status === 'failed' && turn.reply?.reason !== 'recovery_paused' && <div className="text-sm text-muted">{t('No complete reply yet. Your question is saved.', 'ยังตอบไม่สำเร็จ คำถามบันทึกไว้แล้ว')}<button disabled={busy || conversation?.archived} className="btn btn-sm ml-2" onClick={() => send.mutate({ question: turn.question, request_id: turn.request_id })}>{t('Retry', 'ลองอีกครั้ง')}</button></div>}{turn.reply?.reason === 'imported_history' && <p className="text-xs text-muted">{t('Imported from previous tab history', 'นำเข้าจากบทสนทนาในแท็บเดิม')}</p>}</article>)}
            {pending && !turns.some(turn => turn.request_id === pending.request_id) && <div className="space-y-5"><div className="assistant-question">{pending.question}</div>{send.isPending && <LiveAnswer text={liveText} phase={phase} />}</div>}
            {send.isError && send.error?.name !== 'AbortError' && <div role="alert" className="text-bad">{t('Connection interrupted. The question may still be processing.', 'การเชื่อมต่อสะดุด ระบบอาจยังตอบอยู่')}<button className="btn btn-sm ml-2" disabled={busy} onClick={() => { if (send.variables) send.mutate(send.variables) }}>{t('Retry', 'ลองอีกครั้ง')}</button></div>}
            {chatId && <DocumentJobs chat={chatId} onOpen={(id, number) => { setReportVersion({ id, number }); setOpenArtifact(id); setPane('work') }} />}
          </div><div ref={bottom} className="h-8" /></div></div>
        {conversation?.archived && <p className="text-xs text-muted text-center p-2">{t('This chat is archived.', 'แชตนี้เก็บไว้แล้ว')}<button className="underline ml-2" onClick={() => edit.mutate({ archived: false })}>{t('Restore to continue', 'นำกลับมาเพื่อคุยต่อ')}</button></p>}
        {composer}
      </>}
    </>}
    </div>
    {!!openArtifact && <ArtifactPanel key={`${openArtifact}:${reportVersion?.id === openArtifact ? reportVersion.number : "latest"}`} initialVersion={reportVersion?.id === openArtifact ? reportVersion.number : undefined} id={openArtifact} target={target} onTarget={setTarget} busy={busy} onAsk={text => void submit(text)} onPrompt={text => { setInput(text); setPane('chat'); textarea.current?.focus() }} onClose={() => { setOpenArtifact(undefined); setReportVersion(undefined); setTarget(''); setPane('chat') }} />}
  </div>
}

function LiveAnswer({ text, phase, background = false }: { text: string; phase: 'research' | 'coding' | 'writing'; background?: boolean }) {
  const { t } = usePreferences()
  return <div className="space-y-3"><div className="flex items-center gap-2 text-[13px] font-medium text-navy"><span className="h-1.5 w-1.5 bg-brand-lime rounded-full" />FORMA</div>{text && <div className="assistant-prose whitespace-pre-wrap break-words">{text}</div>}<div role="status" className="flex items-center gap-2 text-xs text-muted"><Spinner />{background ? t('Still working in the background. You can leave this page and return.', 'กำลังทำงานต่อในเบื้องหลัง ออกจากหน้านี้แล้วกลับมาดูต่อได้') : phase === 'writing' || text ? t('Writing…', 'กำลังตอบ…') : phase === 'coding' ? t('Running code in the sandbox…', 'กำลังรันโค้ดในห้องทดลอง…') : t('Checking data and evidence…', 'กำลังค้นข้อมูลและตรวจหลักฐาน…')}</div></div>
}
