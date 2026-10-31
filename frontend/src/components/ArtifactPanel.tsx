import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, CircleCheck, Copy, Database, Download, FileText, Globe, Image as ImageIcon, Lock, Presentation, RotateCcw, Sheet, TriangleAlert, Workflow, X, type LucideIcon } from 'lucide-react'
import { api, type ArtifactDetail, type ArtifactKind, type ArtifactRef, type ArtifactVersion, type ChatFile, type SheetPreview } from '../lib/api'
import { flowChanges, flowToPng, flowToSvg, type FlowLayout } from '../lib/flow'
import { usePreferences } from '../lib/preferences'
import { Spinner } from './ui'
import { FlowCanvas } from './FlowCanvas'
import { saveFile, saveBlob, useFileUrl } from './ChatFiles'

export const KIND_ICON: Record<ArtifactKind, LucideIcon> = { flow: Workflow, deck: Presentation, doc: FileText, sheet: Sheet, web: Globe, image: ImageIcon, text: FileText, file: FileText }

export function useKindLabel() {
  const { t } = usePreferences()
  return (kind: ArtifactKind) => ({ flow: t('Flow', 'แผนภาพ'), deck: t('Slides', 'สไลด์'), doc: t('Document', 'เอกสาร'), sheet: t('Spreadsheet', 'ตาราง'), web: t('Web page', 'หน้าเว็บ'), image: t('Picture', 'รูป'), text: t('Text', 'ข้อความ'), file: t('File', 'ไฟล์') })[kind]
}

/** A work the assistant made, in the chat: selecting it opens the panel. */
export function ArtifactCard({ artifact, open, onOpen }: { artifact: ArtifactRef; open: boolean; onOpen: () => void }) {
  const label = useKindLabel(), Icon = KIND_ICON[artifact.kind]
  return <button type="button" className={`artifact-card${open ? ' is-open' : ''}`} onClick={onOpen} aria-pressed={open}><span className="artifact-tile"><Icon size={18} /></span><span className="min-w-0"><b className="block truncate">{artifact.title}</b><small className="block truncate">{label(artifact.kind)} · v{artifact.version}</small></span></button>
}

function versionNote(version: ArtifactVersion, t: (en: string, th: string) => string) {
  if (version.restored_from) return t(`Back to v${version.restored_from}`, `นำ v${version.restored_from} กลับมา`)
  return version.note || (version.number === 1 ? t('First draft', 'ร่างแรก') : t('Changed', 'ฉบับแก้ไข'))
}

function PageImage({ id, alt, picked, onPick }: { id: string; alt: string; picked: boolean; onPick: () => void }) {
  const url = useFileUrl(id)
  return <button type="button" className={`artifact-page${picked ? ' is-picked' : ''}`} onClick={onPick} aria-pressed={picked}>{url ? <img src={url} alt={alt} /> : <span className="grid place-items-center h-40"><Spinner /></span>}</button>
}

function DeckView({ pages, picked, onPick }: { pages: string[]; picked: string; onPick: (target: string) => void }) {
  const { t } = usePreferences(), [current, setCurrent] = useState(0)
  const page = Math.min(current, pages.length - 1), label = t(`Slide ${page + 1}`, `สไลด์ ${page + 1}`)
  return <div className="artifact-deck">
    <div className="artifact-thumbs" role="group" aria-label={t('Slides', 'สไลด์')}>{pages.map((id, i) => <button key={id} type="button" className={`artifact-thumb${i === page ? ' is-on' : ''}`} onClick={() => setCurrent(i)} aria-label={t(`Slide ${i + 1}`, `สไลด์ ${i + 1}`)} aria-pressed={i === page}><Thumb id={id} /><small>{i + 1}</small></button>)}</div>
    <div className="min-w-0 flex-1"><PageImage id={pages[page]} alt={label} picked={picked === label} onPick={() => onPick(picked === label ? '' : label)} /></div>
  </div>
}

function Thumb({ id }: { id: string }) {
  const url = useFileUrl(id)
  return url ? <img src={url} alt="" /> : <span className="block h-14" />
}

function DocView({ pages, picked, onPick }: { pages: string[]; picked: string; onPick: (target: string) => void }) {
  const { t } = usePreferences()
  return <div className="artifact-doc">{pages.map((id, i) => { const label = t(`Page ${i + 1}`, `หน้า ${i + 1}`); return <PageImage key={id} id={id} alt={label} picked={picked === label} onPick={() => onPick(picked === label ? '' : label)} /> })}</div>
}

const column = (n: number) => { let name = ''; for (let i = n + 1; i > 0; i = Math.floor((i - 1) / 26)) name = String.fromCharCode(65 + (i - 1) % 26) + name; return name }

function SheetView({ sheets, picked, onPick }: { sheets: SheetPreview[]; picked: string; onPick: (target: string) => void }) {
  const { t } = usePreferences(), [tab, setTab] = useState(0), [cell, setCell] = useState<{ name: string; row: number; column: number }>()
  const sheet = sheets[Math.min(tab, sheets.length - 1)]
  const columns = Math.max(...sheet.rows.map(row => row.length), 1)
  const pick = (row: number, col: number) => {
    const name = column(col) + (row + 1), next = cell?.name === name ? undefined : { name, row, column: col }
    setCell(next); onPick(next ? t(`Cell ${name} on sheet ${sheet.name}`, `ช่อง ${name} ชีต ${sheet.name}`) : '')
  }
  return <div className="space-y-3">
    {sheets.length > 1 && <div className="flex flex-wrap gap-2" role="group" aria-label={t('Sheets', 'ชีต')}>{sheets.map((item, i) => <button key={item.name} type="button" className={`artifact-seg-tab${i === tab ? ' is-on' : ''}`} aria-pressed={i === tab} onClick={() => { setTab(i); setCell(undefined); onPick('') }}>{item.name}</button>)}</div>}
    {!!cell && <div className="artifact-formula"><b>{cell.name}</b><code>{sheet.formulas[cell.name] ?? sheet.rows[cell.row]?.[cell.column] ?? ''}</code></div>}
    <div className="overflow-x-auto"><table className="artifact-grid"><thead><tr><th aria-hidden="true" />{Array.from({ length: columns }, (_, i) => <th key={i} scope="col">{column(i)}</th>)}</tr></thead><tbody>{sheet.rows.map((row, r) => <tr key={r}><th scope="row">{r + 1}</th>{Array.from({ length: columns }, (_, c) => { const name = column(c) + (r + 1); return <td key={c} title={row[c] || undefined} className={`${/^-?[\d,.]+%?$/.test(row[c] ?? '') ? 'text-right tnum' : ''}${cell?.name === name && picked ? ' is-picked' : ''}`} onClick={() => pick(r, c)}>{row[c] ?? ''}</td> })}</tr>)}</tbody></table></div>
    {sheet.total_rows > sheet.rows.length && <p className="text-xs text-muted">{t(`Showing ${sheet.rows.length} of ${sheet.total_rows} rows. The file has them all.`, `แสดง ${sheet.rows.length} จาก ${sheet.total_rows} แถว ไฟล์จริงมีครบ`)}</p>}
  </div>
}

// A page the model wrote runs in a frame with no origin of its own, and this policy leaves it no network either.
const FRAME_POLICY = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data:; form-action 'none'; base-uri 'none'">`

function WebView({ file, title }: { file: ChatFile; title: string }) {
  const { t } = usePreferences(), [html, setHtml] = useState<string>()
  useEffect(() => { let gone = false; void api.chatFile(file.id).then(blob => blob.text()).then(text => { if (!gone) setHtml(text) }).catch(() => setHtml('')); return () => { gone = true } }, [file.id])
  return <div className="artifact-web">
    <p className="artifact-safe"><Lock size={15} className="shrink-0" />{t('This page runs in its own frame, with no network and no way to touch FORMA.', 'หน้านี้รันในกรอบแยก ไม่มีเน็ต และแตะข้อมูลหรือปุ่มของ FORMA ไม่ได้')}</p>
    {html === undefined ? <div className="grid place-items-center h-40"><Spinner /></div> : <iframe title={title} sandbox="allow-scripts" referrerPolicy="no-referrer" srcDoc={FRAME_POLICY + html} />}
  </div>
}

function TextView({ file }: { file: ChatFile }) {
  const [text, setText] = useState<string>()
  useEffect(() => { let gone = false; void api.chatFile(file.id).then(blob => blob.slice(0, 200000).text()).then(value => { if (!gone) setText(value) }).catch(() => setText('')); return () => { gone = true } }, [file.id])
  return text === undefined ? <Spinner /> : <pre className="assistant-code artifact-text">{text}</pre>
}

function ImageView({ file }: { file: ChatFile }) {
  const url = useFileUrl(file.id)
  return url ? <img className="artifact-picture" src={url} alt={file.name} /> : <Spinner />
}

function WorkBody({ detail, version, picked, onTarget, onAsk, flowPick, onFlowPick, svgRef, layoutRef, fresh }: { detail: ArtifactDetail; version: ArtifactVersion; picked: string; onTarget: (target: string) => void; onAsk: (text: string) => void; flowPick: string; onFlowPick: (id: string, label: string) => void; svgRef: React.RefObject<SVGSVGElement | null>; layoutRef: React.RefObject<FlowLayout | null>; fresh: string[] }) {
  const { t } = usePreferences(), preview = version.preview, file = version.file
  if (detail.kind === 'flow' && version.content) return <FlowCanvas flow={version.content} title={detail.title} picked={flowPick} fresh={fresh} onPick={onFlowPick} svgRef={svgRef} layoutRef={layoutRef} />
  if (!file) return <p className="text-sm text-muted">{t('This work has no file.', 'ผลงานนี้ไม่มีไฟล์')}</p>
  if (preview?.status === 'ready' && preview.pages?.length) return detail.kind === 'deck' ? <DeckView key={version.number} pages={preview.pages} picked={picked} onPick={onTarget} /> : <DocView pages={preview.pages} picked={picked} onPick={onTarget} />
  if (preview?.status === 'ready' && preview.sheets?.length) return <SheetView key={version.number} sheets={preview.sheets} picked={picked} onPick={onTarget} />
  if (detail.kind === 'web') return <WebView key={file.id} file={file} title={detail.title} />
  if (detail.kind === 'image') return <ImageView file={file} />
  if (detail.kind === 'text') return <TextView key={file.id} file={file} />
  if (preview?.status === 'failed') return <div className="artifact-note"><TriangleAlert size={18} className="text-warn shrink-0" /><div className="space-y-2"><p>{t('This file could not be shown here, though it can still be downloaded.', 'ยังแสดงตัวอย่างไฟล์นี้ไม่ได้ แต่ดาวน์โหลดได้ตามปกติ')}</p><button type="button" className="btn btn-sm" onClick={() => onAsk(t(`The file ${file.name} would not open. Check it and fix it.`, `ไฟล์ ${file.name} เปิดแสดงตัวอย่างไม่ได้ ช่วยตรวจและแก้ให้หน่อย`))}>{t('Let the AI fix it', 'ให้ AI แก้')}</button></div></div>
  return <p className="text-sm text-muted">{t('Download this file to open it.', 'ดาวน์โหลดเพื่อเปิดไฟล์นี้')}</p>
}

export function ArtifactPanel({ id, target, onTarget, onAsk, onPrompt, onClose, busy }: { id: string; target: string; onTarget: (target: string) => void; onAsk: (text: string) => void; onPrompt: (text: string) => void; onClose: () => void; busy: boolean }) {
  const { t, prefs } = usePreferences(), qc = useQueryClient()
  const { data: detail, isError, refetch } = useQuery({ queryKey: ['artifact', id], queryFn: () => api.artifact(id) })
  const [chosen, setChosen] = useState<{ number: number; latest: number }>(), [view, setView] = useState<'work' | 'code'>('work'), [copied, setCopied] = useState(false), [box, setFlowPick] = useState(''), [failed, setFailed] = useState(false)
  const svgRef = useRef<SVGSVGElement>(null), layoutRef = useRef<FlowLayout>(null)
  const flowPick = target ? box : ''
  const restore = useMutation({ mutationFn: (n: number) => api.restoreArtifact(id, n), onSuccess: result => { qc.setQueryData(['artifact', id], result); void qc.invalidateQueries({ queryKey: ['artifacts'] }); setChosen(undefined) } })
  if (isError) return <aside className="artifact-panel" aria-label={t('Work', 'ผลงาน')}><div className="p-6 text-bad" role="alert">{t('This work could not be opened.', 'ยังเปิดผลงานนี้ไม่ได้')}<button className="btn btn-sm ml-2" onClick={() => void refetch()}>{t('Retry', 'ลองอีกครั้ง')}</button></div></aside>
  if (!detail) return <aside className="artifact-panel" aria-label={t('Work', 'ผลงาน')}><div className="grid place-items-center h-full"><Spinner /></div></aside>
  // A choice lasts until a newer version arrives: then the newest one is what the reader asked for.
  const number = chosen?.latest === detail.latest ? chosen.number : detail.latest
  const version = detail.versions.find(v => v.number === number) ?? detail.versions[detail.versions.length - 1]
  const previous = detail.versions.find(v => v.number === version.number - 1)
  const changes = detail.kind === 'flow' && version.content ? flowChanges(previous?.content, version.content) : null
  const code = version.code ?? ''
  const when = version.generated_at ? new Intl.DateTimeFormat(prefs.language === 'th' ? 'th-TH' : 'en-GB', { dateStyle: 'medium', timeStyle: 'short', timeZone: prefs.timezone }).format(new Date(version.generated_at)) : ''
  const isOld = version.number !== detail.latest
  async function save(kind: 'file' | 'pdf' | 'svg' | 'png') {
    setFailed(false)
    try {
      if (kind === 'file' && version.file) await saveFile(version.file.id, version.file.name)
      else if (kind === 'pdf' && version.preview?.pdf) await saveFile(version.preview.pdf, detail!.name.replace(/\.[^.]+$/, '') + '.pdf')
      else if ((kind === 'svg' || kind === 'png') && svgRef.current && layoutRef.current) {
        const svg = flowToSvg(svgRef.current, layoutRef.current)
        saveBlob(kind === 'svg' ? new Blob([svg], { type: 'image/svg+xml' }) : await flowToPng(svg, layoutRef.current), `${detail!.title.replace(/[\\/:*?"<>|]/g, ' ').trim()}.${kind}`)
      }
    } catch { setFailed(true) }
  }
  const ext = detail.name.split('.').pop()?.toUpperCase() ?? ''
  return <aside className="artifact-panel" aria-label={t('Work', 'ผลงาน')}>
    <header className="artifact-head">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="flex-1 min-w-[160px] truncate">{detail.title}</h2>
        <div className="artifact-seg" role="group" aria-label={t('View', 'มุมมอง')}><button type="button" className={view === 'work' ? 'is-on' : ''} aria-pressed={view === 'work'} onClick={() => setView('work')}>{t('Work', 'ผลงาน')}</button><button type="button" className={view === 'code' ? 'is-on' : ''} aria-pressed={view === 'code'} onClick={() => setView('code')}>{t('Code', 'โค้ด')}</button></div>
        <button type="button" className="btn btn-sm" disabled={!code} onClick={() => { void navigator.clipboard.writeText(code).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) }).catch(() => setCopied(false)) }}>{copied ? <Check size={15} /> : <Copy size={15} />}{t('Copy', 'คัดลอก')}</button>
        {detail.kind === 'flow' ? <><button type="button" className="btn btn-sm" disabled={view !== 'work'} onClick={() => void save('png')}><Download size={15} />PNG</button><button type="button" className="btn btn-sm" disabled={view !== 'work'} onClick={() => void save('svg')}><Download size={15} />SVG</button></> : <>{version.file && <button type="button" className="btn btn-sm" onClick={() => void save('file')}><Download size={15} />{ext}</button>}{version.preview?.pdf && !detail.name.toLowerCase().endsWith('.pdf') && <button type="button" className="btn btn-sm" onClick={() => void save('pdf')}><Download size={15} />PDF</button>}</>}
        <button type="button" className="btn btn-ghost btn-sm" aria-label={t('Close the panel', 'ปิดแผงผลงาน')} onClick={onClose}><X size={17} /></button>
      </div>
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label={t('Versions', 'เวอร์ชัน')}>
        {detail.versions.map(v => <button key={v.number} type="button" className={`artifact-version${v.number === version.number ? ' is-on' : ''}`} aria-pressed={v.number === version.number} onClick={() => { setChosen({ number: v.number, latest: detail.latest }); setFlowPick(''); onTarget('') }}><b>v{v.number}</b><span className="truncate">{versionNote(v, t)}</span></button>)}
        {isOld && <button type="button" className="btn btn-sm" disabled={restore.isPending || busy} onClick={() => restore.mutate(version.number)}><RotateCcw size={15} />{t(`Bring v${version.number} back as v${detail.latest + 1}`, `นำ v${version.number} กลับมาเป็น v${detail.latest + 1}`)}</button>}
      </div>
      <div className="artifact-source">
        <span><Database size={14} className="shrink-0" />{version.uses_data ? t(`Real data as of ${when}, counted for your access`, `ข้อมูลจริง ณ ${when} นับตามสิทธิ์ของคุณ`) : t('No customer data in this work', 'ผลงานนี้ไม่ได้ใช้ข้อมูลลูกค้า')}</span>
        {version.checked > 0 && <span className="text-ok"><CircleCheck size={14} className="shrink-0" />{t(`The AI looked at ${version.checked} picture(s) of it`, `AI ตรวจภาพแล้ว ${version.checked} ภาพ`)}</span>}
        {!!changes && !!previous && <span>{t('Since', 'เทียบ')} v{previous.number}: {[changes.addedCount && t(`${changes.addedCount} added`, `เพิ่ม ${changes.addedCount} กล่อง`), changes.changedCount && t(`${changes.changedCount} changed`, `แก้ ${changes.changedCount} กล่อง`), changes.removedCount && t(`${changes.removedCount} removed`, `ลบ ${changes.removedCount} กล่อง`)].filter(Boolean).join(' · ') || t('arrows only', 'เปลี่ยนเฉพาะเส้น')}</span>}
      </div>
      {detail.stale && !isOld && <div className="artifact-stale"><TriangleAlert size={15} className="shrink-0" /><span className="flex-1 min-w-[180px]">{t('The data has changed since this was made. This version stays as it is.', 'ข้อมูลเปลี่ยนหลังจากสร้างผลงานนี้ ฉบับนี้จะไม่ถูกแก้เอง')}</span><button type="button" className="btn btn-sm" disabled={busy} onClick={() => onAsk(t('Make this again with the latest data.', 'ทำผลงานนี้ใหม่ด้วยข้อมูลล่าสุด'))}>{t('Make it again with the latest data', 'ทำใหม่ด้วยข้อมูลล่าสุด')}</button></div>}
      {!!flowPick && view === 'work' && <div className="artifact-picked"><span className="flex-1 min-w-0 truncate">{t('Picked', 'เลือกแล้ว')}: {target}</span><button type="button" className="btn btn-sm" onClick={() => onPrompt(t('Change this box: ', 'แก้กล่องนี้: '))}>{t('Change this box', 'แก้กล่องนี้')}</button><button type="button" className="btn btn-sm" onClick={() => onPrompt(t('Add a step after this box: ', 'เพิ่มขั้นต่อจากกล่องนี้: '))}>{t('Add a step after it', 'เพิ่มขั้นต่อ')}</button></div>}
      {failed && <p role="alert" className="text-xs text-bad">{t('Could not download. Try again.', 'ดาวน์โหลดไม่ได้ ลองอีกครั้ง')}</p>}
      {restore.isError && <p role="alert" className="text-xs text-bad">{t('Could not bring this version back.', 'นำเวอร์ชันนี้กลับมาไม่ได้')}</p>}
    </header>
    <div className="artifact-body">
      {view === 'code' ? <pre className="assistant-code artifact-text">{code || t('This work has no code.', 'ผลงานนี้ไม่มีโค้ด')}</pre> : <WorkBody detail={detail} version={version} picked={target} onTarget={onTarget} onAsk={onAsk} flowPick={flowPick} onFlowPick={(node, label) => { const next = flowPick === node ? '' : node; setFlowPick(next); onTarget(next ? label : '') }} svgRef={svgRef} layoutRef={layoutRef} fresh={changes?.added ?? []} />}
    </div>
  </aside>
}
