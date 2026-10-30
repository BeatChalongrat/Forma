import { useId, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowUpRight, ChartColumn, ChartLine, Download, Table2 } from 'lucide-react'
import type { AssistantReport as Report } from '../lib/api'
import { usePreferences } from '../lib/preferences'

const palette = ['var(--report-blue)', 'var(--report-teal)', 'var(--report-amber)', 'var(--report-rose)']
const safeLink = (url: string) => /^\/(uploads|imports|review|datasets)(?:[/?]|$)/.test(url) && !url.includes('\\')
const numeric = (value: unknown) => value === null || value === undefined || value === '' || !Number.isFinite(Number(value)) ? null : Number(value)

export function AssistantReport({ report }: { report: Report }) {
  const { prefs, t } = usePreferences()
  const [view, setView] = useState(report.views.includes(report.view) ? report.view : 'table')
  const [hover, setHover] = useState<string | null>(null)
  const id = useId()
  const lang = prefs.language, locale = lang === 'th' ? 'th-TH' : 'en-US'
  const title = report.title[lang], scope = report.scope[lang]
  const label = (key: string) => report.columns.find(c => c.key === key)?.label[lang] || key
  const status: Record<string, string> = {
    ready: t('Ready to download', 'พร้อมดาวน์โหลด'), needs_customer: t('Choose a customer', 'รอระบุลูกค้า'),
    needs_review: t('Review mapping', 'รอตรวจการจับคู่'), needs_data_check: t('Check output', 'รอตรวจผลลัพธ์'),
    processing: t('Processing', 'กำลังประมวลผล'), failed: t('Failed', 'ผิดพลาด'),
  }
  // The table retains the server's exact decimal text; only chart coordinates use JavaScript numbers.
  const cell = (value: string | number | null | undefined, kind = 'text') => value == null ? '—'
    : kind === 'status' ? status[String(value)] || String(value)
    : typeof value === 'number' ? value.toLocaleString(locale) : String(value)
  const compact = (value: number) => new Intl.NumberFormat(locale, { maximumFractionDigits: 6, notation: Math.abs(value) >= 1000000 ? 'compact' : 'standard' }).format(value)
  const describe = (row: Report['rows'][number], key: string) => `${row.label} · ${label(key)}: ${cell(row[key], 'number')}`
  const csv = () => {
    const quote = (raw: unknown) => {
      let value = raw == null ? '' : String(raw)
      if (/^[\s]*[=+@-]/.test(value) && !/^-?\d+(\.\d+)?$/.test(value)) value = "'" + value
      return '"' + value.replaceAll('"', '""') + '"'
    }
    const lines = [report.columns.map(c => c.label[lang]), ...report.rows.map(r => report.columns.map(c => r[c.key]))]
    const blob = new Blob(['\uFEFF' + lines.map(row => row.map(quote).join(',')).join('\r\n')], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob), link = document.createElement('a')
    link.href = url; link.download = `forma-report-${report.id}.csv`; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  const values = report.rows.flatMap(row => report.series.map(k => numeric(row[k])).filter((v): v is number => v !== null))
  const low = Math.min(0, ...values), high = Math.max(0, ...values)
  const rawStep = (high - low || 1) / 4, power = Math.pow(10, Math.floor(Math.log10(rawStep)))
  let step = ([1, 2, 5, 10].find(n => n * power >= rawStep) || 10) * power
  if (values.every(Number.isInteger)) step = Math.max(1, step)
  const min = Math.floor(low / step) * step, max = Math.max(min + step, Math.ceil(high / step) * step)
  const ticks = Array.from({ length: Math.round((max - min) / step) + 1 }, (_, i) => min + step * i)
  const left = view === 'bar' ? 180 : 64, right = 670, top = 24
  const barGroup = Math.max(48, report.series.length * 15 + 18)
  const bottom = view === 'bar' ? top + report.rows.length * barGroup : 244
  const height = bottom + 40
  const x = (v: number) => left + (v - min) / (max - min) * (right - left)
  const y = (v: number) => bottom - (v - min) / (max - min) * (bottom - top)
  const dateX = (i: number) => left + (report.rows.length === 1 ? .5 : i / (report.rows.length - 1)) * (right - left)
  const table = <div className="assistant-report-table" tabIndex={0} role="region" aria-label={t('Report data', 'ข้อมูลรายงาน')}>
    <table><caption className="sr-only">{title} — {scope}</caption><thead><tr>{report.columns.map(c => <th key={c.key} scope="col" className={c.type === 'number' ? 'report-number' : ''}>{c.label[lang]}</th>)}</tr></thead>
      <tbody>{report.rows.map((row, i) => <tr key={i}>{report.columns.map((c, j) => <td key={c.key} className={c.type === 'number' ? 'report-number' : ''}>
        {j === 0 && typeof row.source_url === 'string' && safeLink(row.source_url) ? <Link to={row.source_url} className="report-row-link">{cell(row[c.key], c.type)}<ArrowUpRight size={13} /></Link> : cell(row[c.key], c.type)}
      </td>)}</tr>)}</tbody></table>
  </div>
  return <section className="assistant-report" aria-labelledby={id}>
    <header className="assistant-report-header"><div className="min-w-0"><div className="report-eyebrow">{t('FROM YOUR DATA', 'รายงานจากข้อมูลระบบ')} <span>· {t('Source', 'อ้างอิง')} {report.evidence_id}</span></div><h3 id={id}>{title}</h3><p>{scope}</p></div>
      <button type="button" className="report-icon-button" onClick={csv} title={t('Download displayed rows as CSV', 'ดาวน์โหลดแถวที่แสดงเป็น CSV')} aria-label={t('Download displayed rows as CSV', 'ดาวน์โหลดแถวที่แสดงเป็น CSV')}><Download size={17} /></button>
    </header>
    <div className="assistant-report-toolbar"><div className="report-view-switch" role="group" aria-label={t('Report view', 'มุมมองรายงาน')}>
      {report.views.map(v => { const Icon = v === 'bar' ? ChartColumn : v === 'line' ? ChartLine : Table2; return <button type="button" key={v} aria-pressed={view === v} onClick={() => { setView(v); setHover(null) }}><Icon size={15} />{v === 'table' ? t('Table', 'ตาราง') : v === 'bar' ? t('Bars', 'กราฟแท่ง') : t('Trend', 'กราฟเส้น')}</button> })}
    </div><span className="report-row-count">{report.rows.length.toLocaleString(locale)} {t('rows', 'แถว')}</span></div>
    {!report.rows.length ? <p className="report-empty">{t('No records match this scope. Try another period or filter.', 'ไม่พบข้อมูลตามขอบเขตนี้ ลองเปลี่ยนช่วงเวลาหรือตัวกรอง')}</p> : view === 'table' ? table : <>
      <div className="report-legend">{report.series.map((key, i) => <span key={key}><i style={{ background: palette[i % palette.length] }} />{label(key)}</span>)}</div>
      <div className="assistant-report-chart" tabIndex={0} role="region" aria-label={title}>
        <svg viewBox={`0 0 720 ${height}`} role="img" aria-label={`${title}. ${t('Switch to Table for exact values.', 'สลับเป็นตารางเพื่อดูค่าที่แน่นอน')}`}>
          <title>{title}</title><desc>{scope}</desc>
          {ticks.map((tick, i) => <g key={i} className="report-grid">{view === 'bar' ? <><line x1={x(tick)} x2={x(tick)} y1={top} y2={bottom} /><text x={x(tick)} y={bottom + 23} textAnchor="middle">{compact(tick)}</text></> : <><line x1={left} x2={right} y1={y(tick)} y2={y(tick)} /><text x={left - 12} y={y(tick) + 4} textAnchor="end">{compact(tick)}</text></>}</g>)}
          {view === 'bar' ? report.rows.map((row, rowIndex) => <g key={rowIndex}>
            <text className="report-axis-label" x={left - 14} y={top + rowIndex * barGroup + barGroup / 2} textAnchor="end"><title>{String(row.label)}</title>{String(row.label).length > 23 ? String(row.label).slice(0, 22) + '…' : row.label}</text>
            {report.series.map((key, i) => { const value = numeric(row[key]), yy = top + rowIndex * barGroup + 9 + i * 15; return value === null ? <text key={key} className="report-axis-label" x={x(0) + 5} y={yy + 10}>—</text> : <rect key={key} x={Math.min(x(0), x(value))} y={yy} width={Math.max(value === 0 ? 1 : 2, Math.abs(x(value) - x(0)))} height={10} rx={3} fill={palette[i % palette.length]} tabIndex={0} aria-label={describe(row, key)} onMouseEnter={() => setHover(describe(row, key))} onFocus={() => setHover(describe(row, key))}><title>{describe(row, key)}</title></rect> })}
          </g>) : report.series.map((key, i) => {
            let connected = false
            const path = report.rows.map((row, n) => { const v = numeric(row[key]); if (v === null) { connected = false; return '' }; const part = `${connected ? 'L' : 'M'}${dateX(n)},${y(v)}`; connected = true; return part }).join(' ')
            return <g key={key}><path d={path} fill="none" stroke={palette[i % palette.length]} strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round" />{report.rows.map((row, n) => { const v = numeric(row[key]); return v === null ? null : <circle key={n} cx={dateX(n)} cy={y(v)} r={report.rows.length > 40 ? 2.5 : 4} fill={palette[i % palette.length]} stroke="var(--color-surface)" strokeWidth={1.5} tabIndex={0} aria-label={describe(row, key)} onMouseEnter={() => setHover(describe(row, key))} onFocus={() => setHover(describe(row, key))}><title>{describe(row, key)}</title></circle> })}</g>
          })}
          {view === 'line' && [...new Set(Array.from({ length: Math.min(6, report.rows.length) }, (_, i) => Math.round(i * (report.rows.length - 1) / Math.max(1, Math.min(6, report.rows.length) - 1))))].map(i => <text key={i} className="report-axis-label" x={dateX(i)} y={bottom + 25} textAnchor="middle">{String(report.rows[i].label).slice(5)}</text>)}
        </svg>
      </div><div className="report-inspection" aria-live="polite">{hover || t('Hover or focus a mark to inspect its value. Exact values are available in Table.', 'ชี้หรือโฟกัสที่กราฟเพื่อดูค่า เปิดตารางเพื่อดูตัวเลขทั้งหมด')}</div>
    </>}
    <footer className="assistant-report-footer"><p className="report-note">{report.notes[0]?.[lang]}</p><details><summary>{t('Scope & calculation notes', 'ขอบเขตและวิธีนับ')}</summary><ul>{report.notes.map((note, i) => <li key={i}>{note[lang]}</li>)}</ul></details>
      <div className="report-sources">{report.links.filter(link => safeLink(link.url)).map((link, i) => <Link key={i} to={link.url}>{link.label}<ArrowUpRight size={13} /></Link>)}</div>
      <p className="report-snapshot">{t('Saved snapshot', 'ข้อมูล ณ เวลาที่ตอบ')} · {new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short', timeZone: prefs.timezone }).format(new Date(report.generated_at))} · {prefs.timezone}</p>
    </footer>
  </section>
}
