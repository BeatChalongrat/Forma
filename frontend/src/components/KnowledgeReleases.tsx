import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { knowledgeTransfer, type TransferPlan } from '../lib/api'
import { usePreferences } from '../lib/preferences'

export function KnowledgeReleases() {
  const { t } = usePreferences(), qc = useQueryClient()
  const [offset, setOffset] = useState(0), [selected, setSelected] = useState<string[]>([])
  const [origin, setOrigin] = useState('unknown'), [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false), [message, setMessage] = useState(''), [error, setError] = useState('')
  const [file, setFile] = useState<File | null>(null), [plan, setPlan] = useState<TransferPlan | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const data = useQuery({ queryKey: ['knowledge-transfer', offset], queryFn: () => knowledgeTransfer.list(offset) })
  const releases = useQuery({ queryKey: ['knowledge-releases'], queryFn: knowledgeTransfer.releases })
  const label = (value: string) => {
    const labels: Record<string, [string, string]> = {
      unknown: ['Unverified source', 'ยังไม่ทราบที่มา'], synthetic: ['Synthetic data', 'ข้อมูลจำลอง'],
      real_trial: ['Real ERP trial', 'ทดลองด้วย ERP จริง'], production: ['Production data', 'ข้อมูลใช้งานจริง'],
      pending: ['Awaiting review', 'รอตรวจ'], allowed: ['Allowed', 'อนุญาตแล้ว'], revoked: ['Withdrawn', 'ถอนการใช้แล้ว'],
      active: ['Active', 'ใช้งานอยู่'], ready: ['Ready to import', 'พร้อมนำเข้า'], blocked: ['Needs attention', 'ต้องแก้ก่อนนำเข้า'],
      schema: ['Output format', 'รูปแบบปลายทาง'], customer: ['Customer', 'ลูกค้า'], mapping: ['Mapping', 'กฎจับคู่'],
      answer: ['Confirmed answer', 'คำตอบที่ยืนยัน'], file: ['Source evidence', 'ไฟล์หลักฐาน'],
      reuse: ['Use existing', 'ใช้รายการเดิม'], create: ['Create', 'สร้างรายการใหม่'], 'copy evidence': ['Copy evidence', 'คัดลอกหลักฐาน'],
      strict: ['Reviewed knowledge only', 'ใช้เฉพาะความรู้ที่ตรวจแล้ว'], legacy: ['Existing development policy', 'นโยบายข้อมูลเดิมของ dev'],
    }
    return labels[value] ? t(...labels[value]) : value
  }
  const toggle = (key: string) => setSelected(s => s.includes(key) ? s.filter(v => v !== key) : [...s, key])
  async function action(fn: () => Promise<void>) {
    setBusy(true); setError(''); setMessage('')
    try { await fn(); await qc.invalidateQueries({ queryKey: ['knowledge-transfer'] }); await qc.invalidateQueries({ queryKey: ['knowledge-releases'] }) }
    catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  async function classify(subject: string, state: string) {
    await knowledgeTransfer.classify({ subject, origin, state, reason, include_answers: true })
    setSelected([])
    setMessage(t('Provenance saved. No files were deleted.', 'บันทึกแหล่งที่มาแล้ว ไม่มีการลบไฟล์'))
  }
  return <div className="space-y-8">
    <header className="space-y-2">
      <h2 className="text-xl font-semibold">{t('Move reviewed knowledge', 'เตรียมความรู้สำหรับใช้งานจริง')}</h2>
      <p className="text-sm text-muted">{t('Review on the source, download a package, then inspect and import it on the destination. Chats, credentials and processing jobs are excluded.', 'ตรวจความรู้ที่ต้นทาง → ดาวน์โหลดแพ็กเกจ → เปิดระบบปลายทางเพื่อตรวจและนำเข้า ไม่ย้ายแชต รหัสลับ หรืองานประมวลผล')}</p>
      <p className="text-xs text-muted">{t('Recall policy', 'โหมดเรียกใช้ความรู้')}: {label(data.data?.policy || '—')} · {t('Database identity', 'รหัสฐานข้อมูล')}: {data.data?.instance || '—'}</p>
    </header>
    {(error || data.error || releases.error) && <p role="alert" className="text-red-600">{error || data.error?.message || releases.error?.message}</p>}
    {message && <p role="status" className="text-sm">{message}</p>}
    <section className="space-y-4">
      <h3 className="font-semibold">{t('1. Review the source', '1. ตรวจและเลือกความรู้ต้นทาง')}</h3>
      <div className="grid sm:grid-cols-2 gap-4">
        <label className="space-y-1 text-sm"><span>{t('Source of the data', 'ข้อมูลมาจากไหน')}</span><select className="input w-full" value={origin} onChange={e => setOrigin(e.target.value)}>
          <option value="unknown">{t('Not yet verified', 'ยังไม่ทราบที่มา')}</option><option value="synthetic">{t('Synthetic test data', 'ข้อมูลจำลอง')}</option>
          <option value="real_trial">{t('Real ERP file used in a trial', 'ไฟล์ ERP จริงที่ใช้ทดลอง')}</option><option value="production">{t('Production data', 'ข้อมูลใช้งานจริง')}</option>
        </select></label>
        <label className="space-y-1 text-sm"><span>{t('Review or withdrawal reason', 'เหตุผลที่ยืนยันหรือถอนการใช้')}</span><input className="input w-full" value={reason} onChange={e => setReason(e.target.value)} placeholder={t('What did you check?', 'ตรวจอะไรแล้ว และยืนยันจากไหน')} /></label>
      </div>
      <p className="text-xs text-muted">{t('Allowing a mapping includes its confirmed answers. Review dependencies first. Real files alone do not prove correct mapping.', 'การอนุญาตรวมคำตอบที่ยืนยันแล้วของ mapping นี้ด้วย ต้องตรวจความรู้ต้นทางที่อ้างอิงก่อน ไฟล์จริงไม่ได้แปลว่าจับคู่ถูกโดยอัตโนมัติ')}</p>
      {data.isLoading && <p>{t('Loading…', 'กำลังโหลด…')}</p>}
      <div className="divide-y divide-line">
        {data.data?.items.map(item => <article key={item.subject} className="py-4 space-y-3">
          <div className="flex flex-wrap justify-between gap-3">
            <label className="flex gap-3 items-start"><input type="checkbox" disabled={!item.eligible || busy} checked={selected.includes(item.subject)} onChange={() => toggle(item.subject)} />
              <span><strong>{item.customer} · v{item.version}</strong><span className="block text-sm text-muted">{item.target_schema} · {label(item.origin)} · {label(item.state)}</span></span></label>
            <Link className="btn btn-sm" to={`/review?mapping=${encodeURIComponent(item.mapping_id)}`}>{t('Inspect mapping', 'ดูการจับคู่และหลักฐาน')}</Link>
          </div>
          {item.dependencies.length > 0 && <p className="text-xs text-muted break-all">{t('Depends on', 'อ้างอิงความรู้')}: {item.dependencies.join(', ')}</p>}
          <div className="flex flex-wrap gap-2">
            <button className="btn btn-sm" disabled={busy || !reason.trim()} onClick={() => action(() => classify(item.subject, 'pending'))}>{t('Save as pending', 'บันทึกที่มา · รอตรวจ')}</button>
            <button className="btn btn-sm" disabled={busy || !reason.trim() || !['real_trial', 'production'].includes(origin)} onClick={() => action(() => classify(item.subject, 'allowed'))}>{t('Allow reviewed knowledge', 'ตรวจแล้ว · อนุญาตให้ใช้')}</button>
            <button className="btn btn-sm" disabled={busy || !reason.trim()} onClick={() => action(() => classify(item.subject, 'revoked'))}>{t('Stop reusing', 'หยุดนำไปใช้')}</button>
          </div>
          {item.corrections.map(c => <label key={c.subject} className="flex items-center gap-2 text-sm"><input type="checkbox" disabled={!c.eligible || busy} checked={selected.includes(c.subject)} onChange={() => toggle(c.subject)} /><span>{c.target || c.kind} · {c.eligible ? t('Reviewed answer', 'คำตอบที่ตรวจแล้ว') : t('Not yet eligible', 'ยังไม่อนุญาตให้ใช้')}</span></label>)}
        </article>)}
      </div>
      {!data.isLoading && data.data?.items.length === 0 && <p className="text-sm text-muted">{t('No mapping records in this database yet. Import a reviewed package below, or review uploaded files first.', 'ฐานนี้ยังไม่มีรายการจับคู่ นำเข้าแพ็กเกจที่ตรวจแล้วด้านล่าง หรือเริ่มจากตรวจไฟล์ที่อัปโหลดก่อน')}</p>}
      <div className="flex gap-3"><button className="btn btn-sm" disabled={!offset || busy} onClick={() => setOffset(Math.max(0, offset - 50))}>{t('Previous', 'ก่อนหน้า')}</button><button className="btn btn-sm" disabled={data.data?.next_offset == null || busy} onClick={() => setOffset(data.data!.next_offset!)}>{t('Next', 'ถัดไป')}</button></div>
      <p className="text-sm text-muted">{t('Packages contain the selected rules AND original evidence files. Transfer only to an authorised destination.', 'แพ็กเกจมีทั้งกฎที่เลือกและไฟล์ต้นทางที่ใช้เป็นหลักฐาน ส่งต่อเฉพาะระบบที่ได้รับอนุญาตให้ใช้ข้อมูลลูกค้า')}</p>
      <button className="btn btn-primary" disabled={busy || !selected.length} onClick={() => action(async () => {
        const blob = await knowledgeTransfer.export(selected), url = URL.createObjectURL(blob), a = document.createElement('a')
        a.href = url; a.download = 'forma-knowledge.zip'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
        setMessage(t('Package downloaded. Open the destination to preview it.', 'ดาวน์โหลดแล้ว เปิดระบบปลายทางเพื่อตรวจแพ็กเกจก่อนนำเข้า'))
      })}>{t('Download selected package', 'ดาวน์โหลดชุดที่เลือก')} ({selected.length})</button>
    </section>
    <section className="border-t border-line pt-6 space-y-4">
      <h3 className="font-semibold">{t('2. Inspect a package on the destination', '2. ตรวจแพ็กเกจบนระบบปลายทาง')}</h3>
      <input aria-label={t('Knowledge ZIP package', 'แพ็กเกจความรู้ ZIP')} type="file" accept=".zip" disabled={busy} onChange={e => { setFile(e.target.files?.[0] || null); setPlan(null); setConfirmed(false) }} />
      <button className="btn" disabled={!file || busy} onClick={() => action(async () => { setPlan(await knowledgeTransfer.preview(file!)); setConfirmed(false) })}>{t('Preview changes', 'ตรวจรายการก่อนนำเข้า')}</button>
      {plan && <div className="space-y-3">
        <p className="text-sm break-all">{t('Package reference', 'รหัสแพ็กเกจ')}: {plan.digest} · {label(plan.status)}</p>
        {plan.errors.map((e, i) => <p role="alert" key={i} className="text-sm text-red-600">{e}</p>)}
        <div className="overflow-auto max-h-96"><table className="w-full text-sm"><thead><tr><th className="text-left p-2">{t('Type', 'ประเภท')}</th><th className="text-left p-2">{t('Item', 'รายการ')}</th><th className="text-left p-2">{t('Change', 'สิ่งที่จะทำ')}</th></tr></thead><tbody>{plan.items.map((i, n) => <tr key={n} className="border-t border-line"><td className="p-2">{label(i.kind)}</td><td className="p-2 break-all">{i.name}</td><td className="p-2">{label(i.action)}</td></tr>)}</tbody></table></div>
        <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />{t('I checked the source, evidence and destination. These rules may be reused.', 'ตรวจที่มา หลักฐาน และระบบปลายทางแล้ว อนุญาตให้นำความรู้ชุดนี้ไปใช้')}</label>
        <button className="btn btn-primary" disabled={busy || !confirmed || !file || plan.status !== 'ready' || plan.already_imported} onClick={() => action(async () => {
          await knowledgeTransfer.apply(file!, plan.plan_digest); setPlan(null); setConfirmed(false)
          setMessage(t('Imported. Existing processing jobs and published results were not changed.', 'นำเข้าแล้ว ไม่มีการเปลี่ยนงานประมวลผลหรือผลลัพธ์ที่เผยแพร่เดิม'))
        })}>{t('Confirm import', 'ยืนยันนำเข้า')}</button>
      </div>}
    </section>
    <section className="border-t border-line pt-6 space-y-4">
      <h3 className="font-semibold">{t('3. Imported releases', '3. ชุดความรู้ที่นำเข้าแล้ว')}</h3>
      <p className="text-sm text-muted">{t('Withdrawal stops future recall, including dependent knowledge. Published JSON remains unchanged.', 'ถอนแล้วจะหยุดใช้ในงานถัดไป รวมถึงความรู้ที่อ้างอิงชุดนี้ ไม่ย้อนแก้ JSON ที่เผยแพร่แล้ว')}</p>
      {releases.data?.items.map(r => <div key={r.id} className="flex flex-wrap items-center justify-between gap-3 py-3 border-b border-line">
        <div className="min-w-0"><p className="text-xs font-mono break-all">{r.id}</p><p className="text-sm text-muted">{r.actor} · {label(r.state)}</p></div>
        <button className="btn btn-sm" disabled={busy || r.state !== 'active' || !reason.trim()} onClick={() => action(async () => { const result = await knowledgeTransfer.revoke(r.id, reason); setMessage(t(`Withdrawn. ${result.review_mapping_ids.length} mappings need review.`, `ถอนการใช้แล้ว มี ${result.review_mapping_ids.length} mapping ที่ควรตรวจต่อ`)) })}>{t('Withdraw this release', 'ถอนการใช้ชุดนี้')}</button>
      </div>)}
      {!releases.data?.items.length && <p className="text-sm text-muted">{t('No imported releases yet.', 'ยังไม่มีชุดความรู้ที่นำเข้า')}</p>}
    </section>
  </div>
}
