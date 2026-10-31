import { useEffect, useState } from 'react'
import { Download, FlaskConical } from 'lucide-react'
import { api, type ChatFile, type CodeRun } from '../lib/api'
import { usePreferences } from '../lib/preferences'
import { Badge } from './ui'

const VIEWABLE = ['image/png', 'image/jpeg', 'image/webp']
const isViewable = (f: ChatFile) => VIEWABLE.includes(f.media_type)

/** Files need the caller's auth headers, so a plain src or href cannot reach them. */
export function useFileUrl(id: string) {
  const [url, setUrl] = useState<string>()
  useEffect(() => {
    let gone = false, made = ''
    void api.chatFile(id).then(blob => { if (!gone) { made = URL.createObjectURL(blob); setUrl(made) } }).catch(() => undefined)
    return () => { gone = true; if (made) URL.revokeObjectURL(made) }
  }, [id])
  return url
}

export function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  URL.revokeObjectURL(url)
}

export async function saveFile(id: string, name: string) {
  saveBlob(await api.chatFile(id), name)
}

export function ChatImage({ file }: { file: ChatFile }) {
  const url = useFileUrl(file.id)
  return <figure className="m-0 max-w-full"><div className="assistant-image">{url ? <img src={url} alt={file.name} /> : <span className="text-xs text-muted">{file.name}</span>}</div><figcaption className="text-xs text-muted mt-1 break-all">{file.name}</figcaption></figure>
}

export function ChatFileLink({ file }: { file: ChatFile }) {
  const { t } = usePreferences()
  const [error, setError] = useState(false)
  async function save() {
    setError(false)
    try { await saveFile(file.id, file.name) } catch { setError(true) }
  }
  return <button type="button" className="assistant-source" onClick={() => void save()}><Download size={14} className="shrink-0" /><span className="break-all text-left">{file.name}</span>{error && <span className="text-bad">{t('Could not download', 'ดาวน์โหลดไม่ได้')}</span>}</button>
}

export function ChatFiles({ files }: { files: ChatFile[] }) {
  const images = files.filter(isViewable), others = files.filter(f => !isViewable(f))
  return <>
    {!!images.length && <div className="flex flex-wrap gap-3">{images.map(f => <ChatImage key={f.id} file={f} />)}</div>}
    {!!others.length && <div className="flex flex-wrap gap-2">{others.map(f => <ChatFileLink key={f.id} file={f} />)}</div>}
  </>
}

/** What the assistant ran and what came back, shown in full: nothing about a run is hidden from the reader. */
export function CodeRunCard({ run, onFix }: { run: CodeRun; onFix?: () => void }) {
  const { t } = usePreferences()
  const state = run.status === 'ok' ? <Badge tone="ok">{t('Finished', 'รันสำเร็จ')}</Badge>
    : run.status === 'timeout' ? <Badge tone="warn">{t('Took too long', 'เกินเวลาที่กำหนด')}</Badge>
      : run.status === 'killed' ? <Badge tone="bad">{t('Stopped by a limit', 'ถูกหยุดเพราะใช้ทรัพยากรเกิน')}</Badge>
        : <Badge tone="bad">{t('Code failed', 'โค้ดมีข้อผิดพลาด')}</Badge>
  return <section className="assistant-run" aria-label={t('Code run', 'ผลการรันโค้ด')}>
    <header className="flex flex-wrap items-center gap-2"><FlaskConical size={17} className="text-navy shrink-0" aria-hidden="true" /><h3 className="font-medium text-navy text-sm flex-1 min-w-[160px]">{t('Sandbox', 'ห้องทดลอง')}</h3>{state}<span className="text-xs text-muted">{(run.duration_ms / 1000).toFixed(1)} {t('s', 'วินาที')}</span></header>
    <p className="text-xs text-muted">{t('Ran on copies. Nothing in FORMA was changed.', 'รันบนสำเนา ไม่ได้แก้ข้อมูลใน FORMA')}</p>
    <div><p className="assistant-run-label">{t('Code', 'โค้ด')}</p><pre className="assistant-code">{run.code}</pre></div>
    {!!run.stdout && <div><p className="assistant-run-label">{t('Output', 'ผลลัพธ์')}</p><pre className="assistant-code">{run.stdout}</pre></div>}
    {!!run.stderr && run.status !== 'ok' && <div><p className="assistant-run-label">{t('Error', 'ข้อผิดพลาด')}</p><pre className="assistant-code text-bad">{run.stderr}</pre></div>}
    {run.status !== 'ok' && !!onFix && <button type="button" className="btn btn-sm" onClick={onFix}>{t('Let the AI fix it', 'ให้ AI แก้')}</button>}
    <ChatFiles files={run.files} />
    {!!run.omitted.length && <p className="text-xs text-warn">{t('Files left out', 'ไฟล์ที่ไม่ได้เก็บ')}: {run.omitted.join(', ')}</p>}
  </section>
}
