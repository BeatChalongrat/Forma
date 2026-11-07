import { useState } from 'react'
import { Link } from 'react-router-dom'
import type { WorkspaceAnswer } from '../lib/api'
import { safeSourceLink, sourceName } from '../lib/assistantPresentation'
import { usePreferences } from '../lib/preferences'
import { CodeRunCard } from './ChatFiles'
import { DetailValue } from './Inspection'
import { Modal } from './ui'

export function AssistantDetails({ reply, initial, onClose, onFix }: { reply: WorkspaceAnswer; initial: 'sources' | 'runs'; onClose: () => void; onFix?: () => void }) {
  const { prefs, t } = usePreferences()
  const [tab, setTab] = useState(initial), [source, setSource] = useState(0), [run, setRun] = useState(Math.max(0, (reply.code_runs?.length ?? 1) - 1))
  const [raw, setRaw] = useState(false)
  const evidence = reply.evidence[source], selectedRun = reply.code_runs?.[run]
  return <Modal open onClose={onClose} title={t('Sources and work details', 'แหล่งข้อมูลและรายละเอียดการทำงาน')} width="max-w-4xl">
    <div className="flex flex-wrap gap-2 mb-5" role="group" aria-label={t('Details view', 'มุมมองรายละเอียด')}>
      {!!reply.evidence.length && <button className={`btn btn-sm ${tab === 'sources' ? 'btn-primary' : ''}`} aria-pressed={tab === 'sources'} onClick={() => setTab('sources')}>{t('Sources', 'ข้อมูลอ้างอิง')} ({reply.evidence.length})</button>}
      {!!reply.code_runs?.length && <button className={`btn btn-sm ${tab === 'runs' ? 'btn-primary' : ''}`} aria-pressed={tab === 'runs'} onClick={() => setTab('runs')}>{t('Execution history', 'ประวัติการทำงาน')} ({reply.code_runs.length})</button>}
    </div>
    {tab === 'sources' && evidence && <div className="space-y-4">
      <p className="text-sm text-muted">{t('Sources saved with this answer. They may contain only part of the data and may differ from current records.', 'อ้างอิงข้อมูลที่บันทึกตอนตอบ อาจเป็นข้อมูลบางส่วนและต่างจากข้อมูลปัจจุบัน')}</p>
      <div className="flex flex-wrap gap-2">{reply.evidence.map((e, i) => <button key={`${e.id}:${i}`} className={`btn btn-sm ${i === source ? 'btn-primary' : ''}`} aria-pressed={i === source} onClick={() => { setSource(i); setRaw(false) }}>{sourceName(e.tool, prefs.language)} · {i + 1}</button>)}</div>
      <h3 className="font-medium">{sourceName(evidence.tool, prefs.language)}</h3>
      <div className="flex flex-wrap gap-2">{evidence.links?.filter(l => safeSourceLink(l.url)).map((l, i) => <Link className="btn btn-sm" key={i} to={l.url}>{l.label}</Link>)}</div>
      <button className="btn btn-sm" aria-pressed={raw} onClick={() => setRaw(!raw)}>{raw ? t('Close saved data', 'ปิดข้อมูลที่บันทึก') : t('Inspect saved data', 'ดูข้อมูลที่บันทึก')}</button>
      {raw && <div className="border-t border-line pt-4 text-xs"><DetailValue value={evidence.data} /></div>}
    </div>}
    {tab === 'runs' && selectedRun && <div className="space-y-4">
      <p className="text-sm text-muted">{t('Earlier attempts are kept here. The conversation shows the last successful output for each filename.', 'เก็บทุกครั้งที่ลองไว้ที่นี่ ในบทสนทนาจะแสดงผลงานที่สำเร็จล่าสุดของแต่ละชื่อไฟล์')}</p>
      <div className="flex flex-wrap gap-2">{reply.code_runs?.map((r, i) => <button className={`btn btn-sm ${run === i ? 'btn-primary' : ''}`} key={r.id} aria-pressed={run === i} onClick={() => setRun(i)}>{t('Attempt', 'ครั้งที่')} {i + 1} · {r.status === 'ok' ? t('Completed', 'สำเร็จ') : t('Not completed', 'ไม่สำเร็จ')}</button>)}</div>
      <CodeRunCard key={selectedRun.id} run={selectedRun} onFix={run === (reply.code_runs?.length ?? 0) - 1 ? onFix : undefined} />
    </div>}
  </Modal>
}
