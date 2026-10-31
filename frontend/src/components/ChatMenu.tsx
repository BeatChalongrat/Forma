import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router-dom'
import { Archive, ArchiveRestore, Ellipsis, Pencil, Pin, PinOff, Trash2 } from 'lucide-react'
import { api, ApiError, type ChatSummary } from '../lib/api'
import { newChatUrl } from '../lib/chats'
import { usePreferences } from '../lib/preferences'
import { useDialog } from '../lib/useDialog'
import { Modal } from './ui'

export function ChatMenu({ chat, identity, busy = false }: { chat: ChatSummary; identity: string; busy?: boolean }) {
  const { t } = usePreferences(), qc = useQueryClient(), nav = useNavigate(), loc = useLocation()
  const trigger = useRef<HTMLButtonElement>(null), id = useId()
  const [open, setOpen] = useState(false), [position, setPosition] = useState({ top: 0, left: 0 })
  const [dialog, setDialog] = useState<'rename' | 'delete' | null>(null), [title, setTitle] = useState(chat.title)
  const menu = useDialog(open, () => setOpen(false))
  const cacheKey = ['assistant-chat', identity, chat.id]
  const refresh = () => { void qc.invalidateQueries({ queryKey: ['assistant-chats', identity] }); void qc.invalidateQueries({ queryKey: cacheKey }) }
  const edit = useMutation({ mutationFn: (body: { title?: string; pinned?: boolean; archived?: boolean }) => api.editChat(chat.id, body), onSuccess: () => { setDialog(null); setOpen(false); refresh() } })
  const remove = useMutation({ mutationFn: () => api.deleteChat(chat.id), onSuccess: async () => {
    setDialog(null)
    try { const key = 'forma.last-chat:' + identity; if (localStorage.getItem(key) === chat.id) localStorage.removeItem(key) } catch { /* optional convenience */ }
    await qc.cancelQueries({ queryKey: cacheKey }); qc.removeQueries({ queryKey: cacheKey })
    if (loc.pathname === '/assistant' && new URLSearchParams(loc.search).get('chat') === chat.id) nav(newChatUrl(), { replace: true })
    void qc.invalidateQueries({ queryKey: ['assistant-chats', identity] }); void qc.invalidateQueries({ queryKey: ['artifacts'] })
  } })
  const pending = edit.isPending || remove.isPending, answering = busy || chat.busy
  const error = remove.error || edit.error
  const errorText = error instanceof ApiError && error.status === 409 ? t('Wait for the reply to finish, then try again.', 'รอให้ผู้ช่วยตอบเสร็จ แล้วลองอีกครั้ง') : t('Could not save the change. Please try again.', 'ยังทำรายการไม่สำเร็จ ลองอีกครั้ง')
  function toggle() {
    if (open) { setOpen(false); return }
    edit.reset(); remove.reset()
    const rect = trigger.current?.getBoundingClientRect(); if (!rect) return
    setPosition({ left: Math.max(8, Math.min(rect.right - 220, window.innerWidth - 228)), top: Math.max(8, rect.bottom + 232 <= window.innerHeight ? rect.bottom + 6 : rect.top - 226) })
    setOpen(true)
  }
  useEffect(() => {
    if (!open) return
    const outside = (event: PointerEvent) => { if (!menu.current?.contains(event.target as Node) && !trigger.current?.contains(event.target as Node)) setOpen(false) }
    const reposition = (event: Event) => { if (!menu.current?.contains(event.target as Node)) setOpen(false) }
    document.addEventListener('pointerdown', outside); window.addEventListener('resize', reposition); document.addEventListener('scroll', reposition, true)
    return () => { document.removeEventListener('pointerdown', outside); window.removeEventListener('resize', reposition); document.removeEventListener('scroll', reposition, true) }
  }, [open, menu])
  function choose(next: 'rename' | 'delete') { setOpen(false); setTitle(chat.title); edit.reset(); remove.reset(); setDialog(next) }
  return <>
    <button ref={trigger} className="chat-menu-trigger" aria-label={t('Chat options: ', 'ตัวเลือกแชต: ') + chat.title} aria-haspopup="menu" aria-expanded={open} aria-controls={open ? id : undefined} onClick={toggle}><Ellipsis size={17} /></button>
    {open && createPortal(<div ref={menu} id={id} role="menu" tabIndex={-1} aria-label={t('Chat options', 'จัดการแชต')} className="chat-menu" style={{ ...position, maxHeight: `calc(100dvh - ${position.top + 8}px)`, overflowY: 'auto' }} onKeyDown={event => {
      const items = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'))
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) { event.preventDefault(); const current = items.indexOf(document.activeElement as HTMLButtonElement); const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (current + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length; items[next]?.focus() }
    }}>
      <button role="menuitem" disabled={pending} onClick={() => choose('rename')}><Pencil size={16} />{t('Rename', 'เปลี่ยนชื่อ')}</button>
      <button role="menuitem" disabled={pending} onClick={() => edit.mutate({ pinned: !chat.pinned })}>{chat.pinned ? <PinOff size={16} /> : <Pin size={16} />}{chat.pinned ? t('Unpin', 'เลิกปักหมุด') : t('Pin', 'ปักหมุด')}</button>
      <div className="border-t border-line my-1 mx-2" role="separator" />
      <button role="menuitem" disabled={pending || answering} onClick={() => edit.mutate({ archived: !chat.archived })}>{chat.archived ? <ArchiveRestore size={16} /> : <Archive size={16} />}{chat.archived ? t('Restore', 'นำแชตกลับมา') : t('Archive', 'เก็บแชต')}</button>
      <button role="menuitem" className="text-bad" disabled={pending || answering} onClick={() => choose('delete')}><Trash2 size={16} />{t('Delete', 'ลบแชต')}</button>
      {answering && <p className="text-xs text-muted px-3 py-1">{t('Reply in progress', 'ผู้ช่วยกำลังตอบอยู่')}</p>}
      {error && <p role="alert" className="text-xs text-bad px-3 py-2">{errorText}</p>}
    </div>, document.body)}
    {dialog && createPortal(<Modal open onClose={() => { if (!pending) setDialog(null) }} title={dialog === 'rename' ? t('Rename chat', 'เปลี่ยนชื่อแชต') : t('Delete this chat?', 'ลบแชตนี้?')} width="max-w-md">
      <form onSubmit={event => { event.preventDefault(); if (pending) return; if (dialog === 'rename') { if (title.trim()) edit.mutate({ title: title.trim() }) } else remove.mutate() }}>
        {dialog === 'rename' ? <label className="grid gap-2 text-sm">{t('Chat title', 'ชื่อแชต')}<input className="input" autoFocus maxLength={160} value={title} onChange={event => setTitle(event.target.value)} required /></label> : <><p className="font-medium break-words">{chat.title}</p><p className="text-sm text-muted mt-3">{t('This permanently deletes the conversation and its messages. Customer files and business data stay unchanged. Archive instead if you may need this chat again.', 'บทสนทนาและข้อความในแชตนี้จะถูกลบถาวร ไฟล์ลูกค้าและข้อมูลงานจะยังอยู่ หากต้องการกลับมาอ่านอีก ให้ใช้ “เก็บแชต” แทน')}</p></>}
        {error && <p role="alert" className="text-bad text-sm mt-3">{errorText}</p>}
        <div className="flex justify-end gap-2 mt-6"><button type="button" className="btn" disabled={pending} onClick={() => setDialog(null)}>{t('Cancel', 'ยกเลิก')}</button><button className={dialog === 'delete' ? 'btn btn-danger' : 'btn btn-primary'} disabled={pending || (dialog === 'rename' && !title.trim())}>{pending ? t('Saving…', 'กำลังทำรายการ…') : dialog === 'rename' ? t('Save', 'บันทึก') : t('Delete chat', 'ลบแชต')}</button></div>
      </form>
    </Modal>, document.body)}
  </>
}
