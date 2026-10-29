import { Link } from 'react-router-dom'
import { Download, ArrowRight } from 'lucide-react'
import type { ImportRound } from '../lib/api'
import { usePreferences } from '../lib/preferences'
import { Workflow } from './Workflow'
import { VerdictCard } from './VerdictCard'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'

type Props = { r: ImportRound; busy: boolean; onRun: (kind: 'run' | 'propose' | 'transform') => void; onExport: (datasetId: string, fmt: string) => void }

export function RoundSteps({ r, busy, onRun, onExport }: Props) {
  const { t } = usePreferences(), qc = useQueryClient()
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: api.me })
  const verdictQuery = useQuery({ queryKey: ['verdict', r.id], queryFn: () => api.verdict(r.id), refetchInterval: ['uploaded', 'profiled', 'approved', 'auto_approved', 'approved_data'].includes(r.status) ? 5000 : false })
  const accepting = useMutation({ mutationFn: (decisions: Record<string, boolean | number | string | null>) => api.acceptVerdict(r.id, { digest: verdictQuery.data!.verdict!.digest, decisions }), onSuccess: () => { void qc.invalidateQueries() } })
  const verdict = verdictQuery.data?.verdict ?? null
  const canAct = !!me?.effective_roles.includes('reviewer') && !['published', 'approved_data'].includes(r.status)
  const mapping = r.mappings?.[0]
  const dataset = r.datasets?.[0]
  const ready = ['approved_data', 'published'].includes(r.status) && dataset && ['approved', 'published'].includes(dataset.status)
  const processing = ['uploaded', 'profiled', 'failed'].includes(r.status)
  const matched = ['approved', 'auto_approved'].includes(r.status)
  const explanation = ready ? t('Your data passed the checks. Download it in the format you need.', 'ข้อมูลผ่านการตรวจแล้ว เลือกรูปแบบที่ต้องการดาวน์โหลดได้เลย')
    : matched ? t('The columns are matched. Next, we create the result and check each record.', 'จับคู่คอลัมน์เรียบร้อย ขั้นต่อไปคือสร้างผลลัพธ์และตรวจข้อมูลทีละรายการ')
      : r.status === 'transformed' ? t('The result is ready to check. Review changes and any invalid records before approval.', 'สร้างผลลัพธ์แล้ว ตรวจสิ่งที่เปลี่ยนและรายการที่มีปัญหาก่อนอนุมัติ')
        : r.status === 'blocked' ? t('This file could be for a different customer or could not produce valid data. Check the source before continuing.', 'ไฟล์นี้อาจเป็นของลูกค้ารายอื่นหรือยังสร้างข้อมูลที่ใช้ได้ไม่ได้ ตรวจไฟล์ต้นทางก่อนทำต่อ')
          : processing ? t('We read the sheets, identify the columns and check whether they match a known format.', 'ระบบจะอ่านแต่ละชีต ดูว่าคอลัมน์คือข้อมูลอะไร แล้วเทียบกับรูปแบบที่เคยใช้')
            : t('Some fields need your confirmation. Answer the questions, preview the result, then approve when correct.', 'มีข้อมูลบางจุดที่ต้องให้คุณยืนยัน ตอบคำถาม ดูตัวอย่างผลลัพธ์ แล้วจึงอนุมัติเมื่อถูกต้อง')
  return <div className="mb-6">
    <Workflow status={r.status} />
    {verdict && !ready && <div className="mb-4"><VerdictCard v={verdict} editable={canAct} busy={busy || accepting.isPending} onAccept={(d) => accepting.mutate(d)} error={accepting.error?.message} /></div>}
    <div className="card p-5">
      <div className="flex flex-wrap items-center gap-4"><div className="flex-1 min-w-[200px]"><h2 className="font-semibold text-navy">{t('What happens next', 'ขั้นตอนต่อไป')}</h2><p className="text-sm text-ink-2 mt-2 leading-6">{explanation}</p></div>
        <div className="flex flex-wrap gap-2">
          {ready && dataset ? <>{['json', 'jsonl', 'csv'].map(fmt => <button key={fmt} className={fmt === 'json' ? 'btn btn-primary' : 'btn'} disabled={busy} onClick={() => onExport(dataset.id, fmt)}><Download size={15} />{fmt === 'json' ? t('Download JSON', 'ดาวน์โหลด JSON') : fmt.toUpperCase()}</button>)}</>
            : matched ? <button className="btn btn-primary" disabled={busy} onClick={() => onRun('transform')}>{t('Create & check results', 'สร้างและตรวจผลลัพธ์')} <ArrowRight size={15} /></button>
              : processing ? <button className="btn btn-primary" disabled={busy} onClick={() => onRun('run')}>{busy ? t('Processing…', 'กำลังประมวลผล…') : t('Process files', 'ประมวลผลไฟล์')}</button>
                : r.status === 'transformed' && dataset ? <Link className="btn btn-primary" to={`/datasets/${dataset.id}?tab=diff`}>{t('Check results', 'ตรวจผลลัพธ์')} <ArrowRight size={15} /></Link>
                  : <Link className="btn btn-primary" to={`/review?round=${r.id}`}>{t('Answer questions', 'ตอบคำถามและตรวจปัญหา')} <ArrowRight size={15} /></Link>}
        </div>
      </div>
      <details className="mt-4 pt-3 border-t border-line text-sm"><summary className="text-muted">{t('Processing details & advanced tools', 'รายละเอียดการประมวลผลและเครื่องมือเพิ่มเติม')}</summary>
        <p className="text-muted mt-3 leading-6">{t('Read files → match source columns to target fields → validate sample data → review uncertain points → create full results → approve and download.', 'อ่านไฟล์ → จับคู่คอลัมน์ต้นทางกับข้อมูลปลายทาง → ทดลองและตรวจตัวอย่าง → ให้คนยืนยันจุดที่ไม่แน่ใจ → สร้างผลลัพธ์ทั้งหมด → อนุมัติและดาวน์โหลด')}</p>
        <div className="flex flex-wrap gap-2 mt-3">
          {mapping && <Link className="btn btn-sm" to={`/rounds/${r.id}/mapping/${mapping.id}?tab=changes`}>{t('Edit field matching', 'แก้การจับคู่คอลัมน์')}</Link>}
          {dataset && <Link className="btn btn-sm" to={`/datasets/${dataset.id}`}>{t('Open data editor', 'เปิดตารางข้อมูล')}</Link>}
          <button className="btn btn-sm" disabled={busy} onClick={() => onRun('propose')}>{t('Recheck field matching', 'ตรวจการจับคู่ใหม่')}</button>
        </div>
      </details>
    </div>
  </div>
}
