import { resolveChatDraft } from './chatDraftState'
import { useEffect, useRef, useState } from 'react'
import { api, ApiError, type ChatWorkspaceState } from './api'

export type ChatDraft = { input?: string; pending_chat_id?: string; attachment_ids?: string[]; clarifications?: Record<string, { step: number; answers: Record<number, string>; custom: Record<number, string>; submitted?: boolean }>; unuploaded?: string[] }

// One writer per mounted chat, with a server revision to reject other-tab overwrites.
export function useChatDraft(chatId: string | undefined, identity: string, privateChat: boolean, enabled = true) {
  const key = `forma.draft:${identity}:${chatId ?? 'new'}`
  const [draft, setDraft] = useState<ChatDraft>({}), [ready, setReady] = useState(false), [error, setError] = useState('')
  const [branch, setBranch] = useState<ChatWorkspaceState['branch']>({})
  const state = useRef({ revision: 0, current: {} as ChatDraft, sent: '', writing: false, blocked: false, ready: false })
  useEffect(() => {
    if (!enabled) return
    if (privateChat) { try { localStorage.removeItem(key) } catch { /* optional storage */ } }
    if (!chatId && state.current.ready) return
    let live = true
    async function load() {
      try {
        const server = chatId ? await api.chatState(chatId) : { revision: 0, draft: {}, branch: {}, seen_sequence: 0 }
        let value = server.draft
        let conflict = false
        if (!privateChat) { try { const local = JSON.parse(localStorage.getItem(key) || 'null'); const resolved = resolveChatDraft(server, local); value = resolved.draft; conflict = resolved.conflict; if (resolved.acknowledged) localStorage.removeItem(key) } catch { /* server remains authoritative */ } }
        value = { ...value, clarifications: { ...value.clarifications } }
        for (const [id, card] of Object.entries(server.draft.clarifications ?? {})) if (card.submitted) value.clarifications![id] = { ...card, ...value.clarifications?.[id], submitted: true }
        if (!live) return
        state.current = { revision: server.revision, current: value, sent: JSON.stringify(server.draft), writing: false, blocked: conflict, ready: true }
        if (conflict) setError('มีร่างจากอีกแท็บ ข้อความที่ยังไม่บันทึกของคุณแสดงอยู่ด้านล่าง คัดลอกก่อนเลือกใช้ร่างที่บันทึกไว้')
        setDraft(value); setBranch(server.branch); setReady(true)
      } catch { if (live) {
        if (!privateChat) { try { const local = JSON.parse(localStorage.getItem(key) || 'null'); if (local?.draft) setDraft(local.draft) } catch { /* keep current text */ } }
        setError('ยังโหลดร่างไม่ได้ ข้อความในเครื่องยังอยู่ เชื่อมต่อแล้วลองใช้ร่างที่บันทึกไว้อีกครั้ง')
      } }
    }
    void load()
    const reconnect = () => { if (!state.current.ready) void load() }
    window.addEventListener('online', reconnect)
    return () => { live = false; window.removeEventListener('online', reconnect) }
  }, [chatId, key, privateChat, enabled])

  async function flush() {
    const s = state.current
    if (!chatId || s.writing || s.blocked || !s.ready) return
    s.writing = true
    try {
      while (s.sent !== JSON.stringify(s.current)) {
        const body = s.current, sent = JSON.stringify(body)
        const result = await api.saveChatState(chatId, s.revision, body)
        s.revision = result.revision; s.sent = sent
        if (!privateChat) { try { localStorage.setItem(key, JSON.stringify({ revision: s.revision, draft: s.current, dirty: s.sent !== JSON.stringify(s.current) })) } catch { /* server saved */ } }
      }
      setError('')
    } catch (e) {
      s.blocked = e instanceof ApiError && e.status === 409
      setError(s.blocked ? 'ร่างเปลี่ยนในอีกแท็บ ข้อความที่พิมพ์ยังอยู่ คัดลอกเก็บไว้แล้วเปิดแชตใหม่อีกครั้ง' : 'ยังบันทึกร่างไม่ได้ ข้อความยังอยู่ในเครื่องนี้')
    } finally { s.writing = false }
  }
  function update(patch: Partial<ChatDraft>) {
    const s = state.current
    s.current = { ...s.current, ...patch }; setDraft(s.current)
    if (!privateChat) { try { localStorage.setItem(key, JSON.stringify({ revision: s.revision, draft: s.current, dirty: true })) } catch { setError('เครื่องนี้บันทึกร่างไม่ได้') } }
    void flush()
  }
  useEffect(() => { const reconnect = () => { void flush() }; window.addEventListener('online', reconnect); return () => window.removeEventListener('online', reconnect) })
  async function useSaved() {
    if (!chatId) return
    try {
    const server = await api.chatState(chatId)
    state.current = { revision: server.revision, current: server.draft, sent: JSON.stringify(server.draft), writing: false, blocked: false, ready: true }
    setDraft(server.draft); setError(''); setReady(true)
    if (!privateChat) localStorage.removeItem(key)
    } catch { setError('ยังเชื่อมต่อไม่ได้ ร่างเดิมยังอยู่') }
  }
  return { draft, update, ready, error, branch, useSaved, current: () => state.current.current }
}
