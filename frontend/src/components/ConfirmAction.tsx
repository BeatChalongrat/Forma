import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, ShieldCheck, X } from 'lucide-react'
import { api, type AssistantAction } from '../lib/api'
import { usePreferences } from '../lib/preferences'
import { useToast } from '../lib/toast'

/** A "do" action the assistant proposed (ADR-040): nothing happens until the person reads the preview and confirms.
 *  The action itself is server-built (round id + verdict digest); the click goes through the ordinary verdict endpoint. */
export function ConfirmAction({ action }: { action: AssistantAction }) {
  const { prefs, t } = usePreferences(), qc = useQueryClient(), toast = useToast()
  const [open, setOpen] = useState(false), [done, setDone] = useState(false)
  const act = action.act
  const run = useMutation({
    mutationFn: () => api.acceptVerdict(act!.round_id, { digest: act!.digest, decisions: act!.decisions ?? {} }),
    onSuccess: () => { setDone(true); setOpen(false); void qc.invalidateQueries(); toast.push({ kind: 'ok', title: t('Done', 'เรียบร้อย'), detail: t('Recorded in the audit log with the rules that fired.', 'บันทึกใน audit log พร้อมกฎที่ใช้ตัดสิน') }) },
    onError: (e: Error) => toast.push({ kind: 'bad', title: t('Not done', 'ทำไม่ได้'), detail: e.message }),
  })
  if (!act) return null
  if (done) return <div className="flex items-center gap-3 rounded-xl border border-ok/30 bg-ok-soft px-4 py-3 text-sm"><Check size={16} className="text-ok shrink-0" />{action.label}</div>
  return (
    <div className="rounded-xl border border-navy/30 bg-signal-soft px-4 py-3 min-w-0">
      <div className="flex items-start gap-3"><ShieldCheck size={17} className="shrink-0 text-navy mt-0.5" /><span className="min-w-0 flex-1"><span className="block text-sm font-medium text-navy">{action.label}</span>{action.detail && <span className="block text-xs text-muted mt-1 break-words">{action.detail}</span>}</span>
        {!open && <button className="btn btn-sm btn-primary" onClick={() => setOpen(true)}>{t('Review & confirm', 'ดูก่อนยืนยัน')}</button>}</div>
      {open && <div className="mt-3 border-t border-line pt-3 space-y-2 text-sm">
        <p className="font-medium">{t('What will happen', 'จะเกิดอะไรขึ้น')}</p>
        <ul className="list-disc pl-5 text-ink-2">{(act.preview ?? []).map((line, i) => <li key={i}>{prefs.language === 'th' ? line.th : line.en}</li>)}</ul>
        <p className="text-xs text-muted">{t('Goes through the same approval path as the button on the page; the policy still decides and the audit log records it.', 'ผ่านเส้นทางอนุมัติเดียวกับปุ่มบนหน้า นโยบายยังเป็นผู้ตัดสิน และบันทึกใน audit log')}</p>
        <div className="flex gap-2"><button className="btn btn-primary btn-sm" disabled={run.isPending} onClick={() => run.mutate()}><Check size={14} />{run.isPending ? t('Working…', 'กำลังทำ…') : t('Confirm', 'ยืนยัน')}</button><button className="btn btn-ghost btn-sm" onClick={() => setOpen(false)}><X size={14} />{t('Cancel', 'ยกเลิก')}</button></div>
      </div>}
    </div>
  )
}
