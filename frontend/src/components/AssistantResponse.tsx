import { Fragment, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, Check, Copy, FileSpreadsheet } from 'lucide-react'
import { type Me, type WorkspaceAnswer } from '../lib/api'
import { usePreferences } from '../lib/preferences'
import { ConfirmAction } from './ConfirmAction'
function ReplyText({ text }: { text: string }) {
  return <div className="assistant-prose">{text.split('\n').map((line, i) => {
    if (!line.trim()) return <div key={i} className="h-3" />
    const heading = /^#{1,4}\s/.test(line)
    const clean = line.replace(/^#{1,4}\s+/, '')
    return <p key={i} className={heading ? 'font-medium text-navy mt-3' : undefined}>{clean.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, n) => part.startsWith('**') ? <strong key={n}>{part.slice(2, -2)}</strong> : part.startsWith('`') ? <code key={n}>{part.slice(1, -1)}</code> : <Fragment key={n}>{part}</Fragment>)}</p>
  })}</div>
}

export function AssistantResponse({ reply, me }: { reply: WorkspaceAnswer; me: Me }) {
  const { t } = usePreferences()
  const [copied, setCopied] = useState(false)
  return <>
<div className="flex gap-2 items-center font-medium text-navy"><span className="h-1.5 w-1.5 bg-brand-lime rounded-full" />FORMA</div><ReplyText text={reply.answer} />
            {!reply.ai && reply.reason !== 'imported_history' && <p className="text-sm text-warn">{t('AI could not answer this time. You can open the evidence below.', 'ครั้งนี้ AI ยังตอบไม่ได้ เปิดข้อมูลอ้างอิงด้านล่างเพื่อตรวจต่อได้')}{me.effective_roles.includes('admin') && <Link className="underline ml-2" to="/settings?tab=ai">{t('Check connection', 'ตรวจการเชื่อมต่อ')}</Link>}</p>}
            {!!reply.actions?.some(a => a.kind === 'act' && a.act?.type === 'verdict.accept') && <div className="space-y-2">{reply.actions.filter(a => a.kind === 'act' && a.act?.type === 'verdict.accept').slice(0, 1).map(a => <ConfirmAction key={a.id} action={a} />)}</div>}
            {!!reply.actions?.length && <div className="space-y-2"><p className="text-xs text-muted">{t('Next steps', 'ทำต่อ')}</p><div className="grid sm:grid-cols-2 gap-2">{reply.actions.filter(a => a.kind !== 'act').filter(a => /^\/(review|imports|uploads|rounds|datasets|customers|settings|guide)(?:[/?]|$)/.test(a.url) && !a.url.includes('\\')).slice(0, 3).map(action => <Link key={action.id} to={action.url} className="flex items-center gap-3 rounded-xl border border-line px-4 py-3 hover:bg-signal-soft hover:border-signal/30 transition-colors min-w-0 group"><span className="min-w-0 flex-1"><span className="block text-sm font-medium text-navy">{action.label}</span>{action.detail && <span className="block text-xs text-muted mt-1 break-words line-clamp-2">{action.detail}</span>}</span><ArrowRight size={17} className="shrink-0 text-muted group-hover:text-navy" /></Link>)}</div></div>}
            <div className="flex items-start gap-3"><button className="btn btn-ghost btn-sm" aria-label={t('Copy answer', 'คัดลอกคำตอบ')} onClick={() => { void navigator.clipboard.writeText(reply.answer).then(() => setCopied(true)).catch(() => setCopied(false)) }}>{copied ? <Check size={15} /> : <Copy size={15} />}</button><details className="text-xs text-muted pt-2 flex-1 min-w-0"><summary>{t('Evidence used', 'ข้อมูลที่ใช้ตอบ')} ({reply.evidence.length})</summary><div className="mt-3 space-y-3"><div className="flex flex-wrap gap-2">{reply.evidence.flatMap(e => e.links ?? []).filter((l, n, all) => l.url.startsWith('/') && !l.url.startsWith('//') && all.findIndex(x => x.url === l.url) === n).slice(0, 10).map(link => <Link className="assistant-source" key={link.url} to={link.url}><FileSpreadsheet size={14} /><span className="break-words min-w-0">{link.label}</span></Link>)}</div>{reply.evidence.map(e => <details key={e.id}><summary>[{e.id}] {e.tool.replaceAll('_', ' ')}</summary><pre className="whitespace-pre-wrap break-all bg-surface-2 p-3 rounded-lg mt-2 max-h-80 overflow-auto">{JSON.stringify(e.data, null, 2)}</pre></details>)}</div></details></div>
</>
}
