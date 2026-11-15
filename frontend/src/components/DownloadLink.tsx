import { useRef, useState, type ReactNode } from 'react'
import { downloadFile } from '../lib/download'
import { usePreferences } from '../lib/preferences'

/** Keep navigation in place and show a retryable error instead of opening an unauthenticated tab. */
export function DownloadLink({ href, children, className, title, disabled = false }: { href: string; children: ReactNode; className?: string; title?: string; disabled?: boolean }) {
  const { t } = usePreferences()
  const lock = useRef(false)
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  return <span className="inline-flex flex-col gap-1 min-w-0">
    <a href={href} title={title} className={className} aria-disabled={disabled || busy} aria-busy={busy} tabIndex={disabled ? -1 : 0} onClick={event => {
      event.preventDefault()
      if (disabled || lock.current) return
      lock.current = true; setBusy(true); setError('')
      void downloadFile(href).catch(error => setError(error.message)).finally(() => { lock.current = false; setBusy(false) })
    }}>{busy ? t('Downloading…', 'กำลังดาวน์โหลด…') : children}</a>
    {error && <span role="alert" className="text-bad text-xs">{error}</span>}
  </span>
}
