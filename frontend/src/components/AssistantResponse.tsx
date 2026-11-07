import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import { ArrowRight, Check, Copy, Database, History, NotebookPen } from 'lucide-react'
import { api, type Me, type MemoryChange, type WorkspaceAnswer } from '../lib/api'
import { usePreferences } from '../lib/preferences'
import { ConfirmAction } from './ConfirmAction'
import { ClarificationCard } from './ClarificationCard'
import { AssistantReport } from './AssistantReport'
import { ChatFiles } from './ChatFiles'
import { AssistantOutput } from './AssistantOutput'
import { AssistantDetails } from './AssistantDetails'
import { assistantPresentation, sourceName } from '../lib/assistantPresentation'
export function ReplyText({ text }: { text: string }) {
  return <div className="assistant-prose"><Markdown remarkPlugins={[remarkGfm]} skipHtml components={{ img: ({ alt }) => <span>{alt}</span>, a: ({ href, children }) => <a href={href} target={href?.startsWith('/') ? undefined : '_blank'} rel="noopener noreferrer">{children}</a>, table: ({ children }) => <div className="overflow-x-auto"><table>{children}</table></div> }}>{text}</Markdown></div>
}

function MemoryNote({ change }: { change: MemoryChange }) {
  const { t } = usePreferences()
  const undo = useMutation({ mutationFn: () => api.deleteMemory(change.id) })
  const said = change.action === 'forget' ? t('Forgotten', 'ลืมแล้ว') : change.action === 'change' ? t('Memory changed', 'แก้ความจำแล้ว') : t('Remembered', 'จำแล้ว')
  return <p className="assistant-memory"><NotebookPen size={14} className="shrink-0" /><span className="min-w-0">{undo.isSuccess ? t('Removed from memory', 'เอาออกจากความจำแล้ว') : `${said}: ${change.topic}`}{change.sensitive && !undo.isSuccess && <span className="text-warn"> · {t('a sensitive topic', 'เรื่องอ่อนไหว')}</span>}</span>{change.action === 'save' && !undo.isSuccess && <button type="button" className="underline" disabled={undo.isPending} onClick={() => undo.mutate()}>{t('Undo', 'ยกเลิก')}</button>}<Link className="underline" to="/memory">{t('Manage', 'จัดการ')}</Link></p>
}

export function AssistantResponse({ clarificationState, onClarificationState, reply, me, onClarify, disabled, openArtifact, onOpenArtifact }: { clarificationState?: import('../lib/chatDraft').ChatDraft['clarifications'] extends Record<string, infer V> | undefined ? V : never; onClarificationState?: (state: { step: number; answers: Record<number, string>; custom: Record<number, string>; submitted?: boolean }) => void; reply: WorkspaceAnswer; me: Me; onClarify?: (text: string) => boolean | void | Promise<boolean | void>; disabled?: boolean; openArtifact?: string; onOpenArtifact?: (id: string, version?: number) => void }) {
  const { prefs, t } = usePreferences()
  const [copied, setCopied] = useState(false), [details, setDetails] = useState<'sources' | 'runs' | null>(null)
  const output = assistantPresentation(reply)
  const sources = [...new Set(reply.evidence.map(e => sourceName(e.tool, prefs.language)))]
  const sourceSummary = sources.slice(0, 2).join(' · ') + (sources.length > 2 ? t(` +${sources.length - 2} more`, ` และอีก ${sources.length - 2}`) : '')
  const links = (reply.actions ?? []).filter(a => a.kind !== 'act' && /^\/(review|imports|uploads|rounds|datasets|customers|settings|guide)(?:[/?]|$)/.test(a.url) && !a.url.includes('\\')).slice(0, 3)
  const fix = onClarify && !disabled ? () => { setDetails(null); onClarify(t('The last run failed. Fix it and try again.', 'รันล่าสุดไม่สำเร็จ ช่วยแก้แล้วลองใหม่')) } : undefined
  const omitted = (reply.code_runs ?? []).reduce((sum, run) => sum + run.omitted.length, 0)
  return <>
    <div className="flex gap-2 items-center font-medium text-navy"><span className="h-1.5 w-1.5 bg-brand-lime rounded-full" />FORMA</div>
    <ReplyText text={reply.answer} />
    {reply.memory_changes?.map(change => <MemoryNote key={change.id + change.action} change={change} />)}
    {!!output.artifacts.length && <div className="space-y-3">{output.artifacts.map(artifact => <AssistantOutput key={artifact.id + ':' + artifact.version} artifact={artifact} open={openArtifact === artifact.id} onOpen={() => onOpenArtifact?.(artifact.id, artifact.version)} />)}</div>}
    {!!output.files.length && <div className="space-y-3"><ChatFiles files={output.files} /></div>}
    {!reply.clarifications?.length && output.reports.map(report => <AssistantReport key={report.id} report={report} />)}
    {output.lastRunFailed && <div role="alert" className="text-sm text-warn flex flex-wrap gap-2 items-center"><span>{t('The last attempt did not complete. Any files shown are from an earlier successful attempt.', 'รันล่าสุดไม่สำเร็จ หากมีผลงานแสดงอยู่จะเป็นฉบับก่อนหน้าที่รันสำเร็จ')}</span><button className="underline" onClick={() => setDetails('runs')}>{t('See what happened', 'ดูว่าเกิดอะไรขึ้น')}</button>{fix && <button className="btn btn-sm" onClick={fix}>{t('Try fixing it', 'ให้ผู้ช่วยแก้')}</button>}</div>}
    {!!omitted && <p className="text-xs text-warn">{t(`${omitted} file(s) could not be retained.`, `มีไฟล์ที่ไม่ได้เก็บ ${omitted} รายการ`)} <button className="underline" onClick={() => setDetails('runs')}>{t('See details', 'ดูรายละเอียด')}</button></p>}
    {!!reply.clarifications?.length && <ClarificationCard saved={clarificationState} onSave={onClarificationState} questions={reply.clarifications} onAnswer={onClarify} disabled={disabled} />}
    {!reply.ai && reply.reason !== 'imported_history' && reply.reason !== 'recovery_paused' && <p className="text-sm text-warn">{t('AI could not answer this time. Open Sources to inspect the available data.', 'ครั้งนี้ AI ยังตอบไม่ได้ เปิดข้อมูลอ้างอิงเพื่อตรวจต่อได้')}{me.effective_roles.includes('admin') && <Link className="underline ml-2" to="/settings?tab=ai">{t('Check connection', 'ตรวจการเชื่อมต่อ')}</Link>}</p>}
    {!!reply.actions?.some(a => a.kind === 'act' && a.act?.type === 'verdict.accept') && <div className="space-y-2">{reply.actions.filter(a => a.kind === 'act' && a.act?.type === 'verdict.accept').slice(0, 1).map(a => <ConfirmAction key={a.id} action={a} />)}</div>}
    {!!links.length && <div className="flex flex-wrap gap-2">{links.map(action => <Link key={action.id} to={action.url} className="btn btn-sm" title={action.detail}>{action.label}<ArrowRight size={14} /></Link>)}</div>}
    <div className="assistant-reply-tools">
      <button className="btn btn-ghost btn-sm" aria-label={t('Copy answer', 'คัดลอกคำตอบ')} onClick={() => { void navigator.clipboard.writeText(reply.answer).then(() => setCopied(true)).catch(() => setCopied(false)) }}>{copied ? <Check size={15} /> : <Copy size={15} />}</button>
      {!!reply.evidence.length && <button className="assistant-detail-link" onClick={() => setDetails('sources')} title={sourceSummary}><Database size={14} /><span>{t('Sources', 'อ้างอิง')}: {sourceSummary}</span></button>}
      {!!reply.code_runs?.length && <button className="assistant-detail-link" onClick={() => setDetails('runs')}><History size={14} /><span>{t('Work details', 'รายละเอียดการทำงาน')} ({reply.code_runs.length})</span></button>}
    </div>
    {details && <AssistantDetails key={details} initial={details} reply={reply} onClose={() => setDetails(null)} onFix={fix} />}
  </>
}
