import { Fragment, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import { ArrowRight, Check, Copy, FileSpreadsheet, History, NotebookPen } from 'lucide-react'
import { api, type Me, type MemoryChange, type WorkspaceAnswer } from '../lib/api'
import { usePreferences } from '../lib/preferences'
import { ConfirmAction } from './ConfirmAction'
import { ClarificationCard } from './ClarificationCard'
import { AssistantReport } from './AssistantReport'
import { CodeRunCard } from './ChatFiles'
import { DetailValue } from './Inspection'
import { ArtifactCard } from './ArtifactPanel'
function ReplyText({ text }: { text: string }) {
  return <div className="assistant-prose">{text.split('\n').map((line, i) => {
    if (!line.trim()) return <div key={i} className="h-3" />
    const heading = /^#{1,4}\s/.test(line)
    const clean = line.replace(/^#{1,4}\s+/, '')
    return <p key={i} className={heading ? 'font-medium text-navy mt-3' : undefined}>{clean.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, n) => part.startsWith('**') ? <strong key={n}>{part.slice(2, -2)}</strong> : part.startsWith('`') ? <code key={n}>{part.slice(1, -1)}</code> : <Fragment key={n}>{part}</Fragment>)}</p>
  })}</div>
}

function MemoryNote({ change }: { change: MemoryChange }) {
  const { t } = usePreferences()
  const undo = useMutation({ mutationFn: () => api.deleteMemory(change.id) })
  const said = change.action === 'forget' ? t('Forgotten', 'ลืมแล้ว') : change.action === 'change' ? t('Memory changed', 'แก้ความจำแล้ว') : t('Remembered', 'จำแล้ว')
  return <p className="assistant-memory"><NotebookPen size={14} className="shrink-0" /><span className="min-w-0">{undo.isSuccess ? t('Removed from memory', 'เอาออกจากความจำแล้ว') : `${said}: ${change.topic}`}{change.sensitive && !undo.isSuccess && <span className="text-warn"> · {t('a sensitive topic', 'เรื่องอ่อนไหว')}</span>}</span>{change.action === 'save' && !undo.isSuccess && <button type="button" className="underline" disabled={undo.isPending} onClick={() => undo.mutate()}>{t('Undo', 'ยกเลิก')}</button>}<Link className="underline" to="/memory">{t('Manage', 'จัดการ')}</Link></p>
}

export function AssistantResponse({ reply, me, onClarify, disabled, openArtifact, onOpenArtifact }: { reply: WorkspaceAnswer; me: Me; onClarify?: (text: string) => void; disabled?: boolean; openArtifact?: string; onOpenArtifact?: (id: string) => void }) {
  const { t } = usePreferences()
  const [copied, setCopied] = useState(false)
  const pastChats = reply.evidence.filter(e => e.tool === 'search_chats' || e.tool === 'recent_chats').flatMap(e => e.links ?? [])
  return <>
<div className="flex gap-2 items-center font-medium text-navy"><span className="h-1.5 w-1.5 bg-brand-lime rounded-full" />FORMA</div><ReplyText text={reply.answer} />
            {reply.memory_changes?.map(change => <MemoryNote key={change.id + change.action} change={change} />)}
            {!!pastChats.length && <div className="assistant-past"><History size={14} className="shrink-0" /><span>{t('From earlier chats', 'จากแชตก่อนหน้า')}:</span>{pastChats.map(link => <Link key={link.url} className="underline" to={link.url}>{link.label}</Link>)}</div>}
            {!!reply.artifacts?.length && <div className="space-y-2">{reply.artifacts.map(artifact => <ArtifactCard key={artifact.id + artifact.version} artifact={artifact} open={openArtifact === artifact.id} onOpen={() => onOpenArtifact?.(artifact.id)} />)}</div>}
            {reply.code_runs?.map((run, i, runs) => <CodeRunCard key={run.id} run={run} onFix={i === runs.length - 1 && onClarify && !disabled ? () => onClarify(t('The last run failed. Fix it and try again.', 'รันล่าสุดไม่สำเร็จ ช่วยแก้แล้วลองใหม่')) : undefined} />)}
            {!reply.clarifications?.length && reply.visualizations?.filter((report, i, all) => all.findIndex(r => r.id === report.id) === i).map(report => <AssistantReport key={report.id} report={report} />)}
            {!!reply.clarifications?.length && <ClarificationCard questions={reply.clarifications} onAnswer={onClarify} disabled={disabled} />}
            {!reply.ai && reply.reason !== 'imported_history' && <p className="text-sm text-warn">{t('AI could not answer this time. You can open the evidence below.', 'ครั้งนี้ AI ยังตอบไม่ได้ เปิดข้อมูลอ้างอิงด้านล่างเพื่อตรวจต่อได้')}{me.effective_roles.includes('admin') && <Link className="underline ml-2" to="/settings?tab=ai">{t('Check connection', 'ตรวจการเชื่อมต่อ')}</Link>}</p>}
            {!!reply.actions?.some(a => a.kind === 'act' && a.act?.type === 'verdict.accept') && <div className="space-y-2">{reply.actions.filter(a => a.kind === 'act' && a.act?.type === 'verdict.accept').slice(0, 1).map(a => <ConfirmAction key={a.id} action={a} />)}</div>}
            {!!reply.actions?.length && <div className="space-y-2"><p className="text-xs text-muted">{t('Next steps', 'ทำต่อ')}</p><div className="grid sm:grid-cols-2 gap-2">{reply.actions.filter(a => a.kind !== 'act').filter(a => /^\/(review|imports|uploads|rounds|datasets|customers|settings|guide)(?:[/?]|$)/.test(a.url) && !a.url.includes('\\')).slice(0, 3).map(action => <Link key={action.id} to={action.url} className="flex items-center gap-3 rounded-xl border border-line px-4 py-3 hover:bg-signal-soft hover:border-signal/30 transition-colors min-w-0 group"><span className="min-w-0 flex-1"><span className="block text-sm font-medium text-navy">{action.label}</span>{action.detail && <span className="block text-xs text-muted mt-1 break-words line-clamp-2">{action.detail}</span>}</span><ArrowRight size={17} className="shrink-0 text-muted group-hover:text-navy" /></Link>)}</div></div>}
            <div className="flex items-start gap-3"><button className="btn btn-ghost btn-sm" aria-label={t('Copy answer', 'คัดลอกคำตอบ')} onClick={() => { void navigator.clipboard.writeText(reply.answer).then(() => setCopied(true)).catch(() => setCopied(false)) }}>{copied ? <Check size={15} /> : <Copy size={15} />}</button><div className="text-xs text-muted pt-2 flex-1 min-w-0"><p className="m-0">{t('Evidence used', 'ข้อมูลที่ใช้ตอบ')} ({reply.evidence.length}): {[...new Set(reply.evidence.map(e => e.tool.replaceAll('_', ' ')))].join(' · ')}</p><div className="flex flex-wrap gap-2 mt-2">{reply.evidence.flatMap(e => e.links ?? []).filter((l, n, all) => l.url.startsWith('/') && !l.url.startsWith('//') && all.findIndex(x => x.url === l.url) === n).slice(0, 10).map(link => <Link className="assistant-source" key={link.url} to={link.url}><FileSpreadsheet size={14} /><span className="break-words min-w-0">{link.label}</span></Link>)}</div></div></div>
            {!!reply.evidence.length && <details className="text-xs text-muted"><summary className="cursor-pointer">{t('Inspect evidence snapshots', 'ดูข้อมูลที่ผู้ช่วยใช้ตอบ')}</summary><p className="my-3">{t('These are the retrieved snapshots saved with this answer. They may be partial or older than current data.', 'นี่คือข้อมูลที่ค้นได้และบันทึกพร้อมคำตอบนี้ อาจเป็นข้อมูลบางส่วนหรือเก่ากว่าข้อมูลปัจจุบัน')}</p>{reply.evidence.map((e, i) => <details key={e.id || i} className="border-b border-line py-2"><summary className="cursor-pointer">{i + 1}. {e.tool.replaceAll('_', ' ')} · {e.id}</summary><DetailValue value={e.data} /></details>)}</details>}
</>
}
