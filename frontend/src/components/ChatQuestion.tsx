import { useState } from 'react'
import { Check, Copy, Pencil } from 'lucide-react'
import type { ChatTurn } from '../lib/api'
import { usePreferences } from '../lib/preferences'

export function ChatQuestion({ turn, disabled, onSend }: { turn: ChatTurn; disabled: boolean; onSend: (text: string) => Promise<boolean> }) {
  const { t, prefs, date } = usePreferences()
  const [copied, setCopied] = useState(false), [copyError, setCopyError] = useState(false)
  const [editing, setEditing] = useState(false), [draft, setDraft] = useState(turn.question), [sending, setSending] = useState(false)
  const parsed = new Date(/(?:Z|[+-]\d{2}:\d{2})$/.test(turn.created_at) ? turn.created_at : turn.created_at + 'Z')
  const time = Number.isNaN(parsed.valueOf()) ? '' : new Intl.DateTimeFormat(prefs.language, { timeZone: prefs.timezone, hour: 'numeric', minute: '2-digit' }).format(parsed)
  return <div className="flex flex-col items-end gap-1.5">
    {editing ? <form className="w-full max-w-[720px] rounded-2xl bg-surface-2 border border-line p-4 space-y-3" onSubmit={async e => {
      e.preventDefault(); if (!draft.trim() || disabled || sending) return
      setSending(true)
      try { if (await onSend(draft.trim())) setEditing(false) } finally { setSending(false) }
    }}>
      <textarea className="w-full bg-transparent resize-y min-h-24" aria-label={t('Edit question', 'แก้ไขคำถาม')} value={draft} onChange={e => setDraft(e.target.value)} maxLength={2000} disabled={disabled || sending} autoFocus />
      <p className="text-xs text-muted">{t('Opens an alternative from this question. The original chat and work are kept.', 'เปิดอีกทางเลือกจากคำถามนี้ แชตและผลงานเดิมยังอยู่ครบ')}</p>
      <div className="flex justify-end gap-2"><button type="button" className="btn btn-sm" disabled={sending} onClick={() => setEditing(false)}>{t('Cancel', 'ยกเลิก')}</button><button className="btn btn-primary btn-sm" disabled={disabled || sending || !draft.trim()}>{t('Send updated question', 'เปิดทางเลือกที่แก้ไข')}</button></div>
    </form> : <div className="assistant-question">{turn.question}</div>}
    <div className="flex items-center gap-1 text-xs text-muted">
      <time dateTime={Number.isNaN(parsed.valueOf()) ? undefined : parsed.toISOString()} title={date(Number.isNaN(parsed.valueOf()) ? undefined : parsed.toISOString())} className="mr-1">{time}</time>
      <button type="button" className="btn btn-ghost btn-sm" title={t('Copy message', 'คัดลอกข้อความ')} aria-label={t('Copy message', 'คัดลอกข้อความ')} onClick={() => { void navigator.clipboard.writeText(turn.question).then(() => { setCopied(true); setCopyError(false) }).catch(() => setCopyError(true)) }}>{copied ? <Check size={14} /> : <Copy size={14} />}</button>
      <button type="button" className="btn btn-ghost btn-sm" title={t('Edit and ask again', 'แก้ไขและถามใหม่')} aria-label={t('Edit and ask again', 'แก้ไขและถามใหม่')} disabled={disabled || sending} onClick={() => { setDraft(turn.question); setEditing(true) }}><Pencil size={14} /></button>
      {copyError && <span role="status">{t('Could not copy', 'คัดลอกไม่สำเร็จ')}</span>}
    </div>
  </div>
}
