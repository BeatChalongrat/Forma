import type { ArtifactRef } from '../lib/api'
import { usePreferences } from '../lib/preferences'
import { ArtifactCard } from './ArtifactPanel'
import { ChatFileLink, useFileUrl } from './ChatFiles'

function OutputImage({ id, name }: { id: string; name: string }) {
  const url = useFileUrl(id)
  return <span className="assistant-image">{url ? <img src={url} alt={name} /> : <span className="text-xs text-muted">{name}</span>}</span>
}

export function AssistantOutput({ artifact, open, onOpen }: { artifact: ArtifactRef; open: boolean; onOpen: () => void }) {
  const { t } = usePreferences()
  const image = artifact.kind === 'image' && artifact.file && ['image/png', 'image/jpeg', 'image/webp'].includes(artifact.file.media_type)
  if (!image || open) return <ArtifactCard artifact={artifact} open={open} onOpen={onOpen} />
  return <section className="assistant-output" aria-label={artifact.title}>
    <button type="button" className="assistant-output-preview" onClick={onOpen} aria-label={t(`Open ${artifact.title}, version ${artifact.version}`, `เปิด ${artifact.title} เวอร์ชัน ${artifact.version}`)}>
      <OutputImage id={artifact.file!.id} name={artifact.file!.name} />
    </button>
    <div className="flex items-center flex-wrap gap-3 px-3 py-2"><button className="min-w-0 text-left flex-1" onClick={onOpen}><strong className="block text-sm break-words">{artifact.title}</strong><span className="text-xs text-muted">v{artifact.version} · {t('Open work', 'เปิดผลงาน')}</span></button><ChatFileLink file={artifact.file!} /></div>
  </section>
}
