import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Download, Pencil, Plus, Trash2 } from 'lucide-react'
import { api, type MemoryItem, type Preferences } from '../lib/api'
import { usePreferences } from '../lib/preferences'
import { Badge, Empty, PageHeader, Section, Spinner } from '../components/ui'
import { saveBlob } from '../components/ChatFiles'

function Setting({ label, hint, checked, onChange, disabled }: { label: string; hint: string; checked: boolean; onChange: (value: boolean) => void; disabled?: boolean }) {
  return <label className="memory-setting"><input type="checkbox" checked={checked} disabled={disabled} onChange={e => onChange(e.target.checked)} /><span><b>{label}</b><small>{hint}</small></span></label>
}

function Topic({ item }: { item: MemoryItem }) {
  const { t, date } = usePreferences(), qc = useQueryClient()
  const [editing, setEditing] = useState(false), [topic, setTopic] = useState(item.topic), [content, setContent] = useState(item.content), [sure, setSure] = useState(false)
  const refresh = () => qc.invalidateQueries({ queryKey: ['memory'] })
  const save = useMutation({ mutationFn: () => api.editMemory(item.id, { topic, content }), onSuccess: () => { setEditing(false); void refresh() } })
  const remove = useMutation({ mutationFn: () => api.deleteMemory(item.id), onSuccess: () => void refresh() })
  if (editing) return <li className="memory-topic"><form className="space-y-2" onSubmit={e => { e.preventDefault(); save.mutate() }}>
    <input className="input w-full" aria-label={t('Topic', 'หัวข้อ')} maxLength={120} value={topic} onChange={e => setTopic(e.target.value)} />
    <textarea className="input h-24 py-2" aria-label={t('What to remember', 'สิ่งที่จำ')} maxLength={1000} value={content} onChange={e => setContent(e.target.value)} />
    {save.isError && <p role="alert" className="text-xs text-bad">{save.error.message}</p>}
    <div className="flex gap-2"><button className="btn btn-primary btn-sm" disabled={save.isPending || !topic.trim() || !content.trim()}>{t('Save', 'บันทึก')}</button><button type="button" className="btn btn-sm" onClick={() => { setEditing(false); setTopic(item.topic); setContent(item.content) }}>{t('Cancel', 'ยกเลิก')}</button></div>
  </form></li>
  return <li className="memory-topic">
    <div className="flex flex-wrap items-center gap-2"><h3 className="flex-1 min-w-[160px] m-0 text-sm font-medium text-navy">{item.topic}</h3>{item.sensitive && <Badge tone="warn">{t('Sensitive', 'อ่อนไหว')}</Badge>}
      <button className="btn btn-sm" onClick={() => setEditing(true)}><Pencil size={14} />{t('Edit', 'แก้')}</button>
      {sure ? <button className="btn btn-sm text-bad" disabled={remove.isPending} onClick={() => remove.mutate()}>{t('Delete for good', 'ลบถาวร')}</button> : <button className="btn btn-sm" onClick={() => setSure(true)}><Trash2 size={14} />{t('Delete', 'ลบ')}</button>}
    </div>
    <p className="m-0 text-sm whitespace-pre-wrap">{item.content}</p>
    <p className="m-0 text-xs text-muted">{item.origin === 'user' ? t('You added this', 'คุณเพิ่มเอง') : t('From a chat', 'จำจากแชต')} · {date(item.updated_at)}{item.source_chat_id && <> · <Link className="underline" to={`/assistant?chat=${encodeURIComponent(item.source_chat_id)}`}>{t('Open the chat', 'เปิดแชตนั้น')}</Link></>}</p>
  </li>
}

export function MemoryPage() {
  const { t, prefs } = usePreferences(), qc = useQueryClient()
  const { data, isPending, isError, refetch } = useQuery({ queryKey: ['memory'], queryFn: api.memory })
  const [topic, setTopic] = useState(''), [content, setContent] = useState(''), [sure, setSure] = useState(false)
  const items = data?.items ?? []
  const setting = useMutation({ mutationFn: (change: Partial<Preferences>) => api.savePreferences({ ...prefs, ...change }), onSuccess: value => qc.setQueryData(['preferences'], value) })
  const add = useMutation({ mutationFn: () => api.addMemory({ topic, content }), onSuccess: () => { setTopic(''); setContent(''); void qc.invalidateQueries({ queryKey: ['memory'] }) } })
  const reset = useMutation({ mutationFn: api.resetMemory, onSuccess: () => { setSure(false); void qc.invalidateQueries({ queryKey: ['memory'] }) } })
  return <div className="space-y-6">
    <PageHeader title={t('Memory', 'ความจำ')} subtitle={t('What the assistant knows about you across chats. You can read, change and delete all of it.', 'สิ่งที่ผู้ช่วยรู้เกี่ยวกับคุณข้ามแชต เปิดดู แก้ และลบได้ทุกข้อ')} />
    <Section title={t('How memory works for you', 'การตั้งค่าความจำ')}>
      <div className="divide-y divide-line">
        <Setting label={t('Use memory', 'ใช้ความจำ')} hint={t('Off pauses it: what is kept stays, but is not used and nothing new is added.', 'ปิดเพื่อพักไว้ ความจำเดิมยังอยู่ แต่ไม่ถูกใช้และไม่เพิ่มใหม่')} checked={prefs.memory_enabled} disabled={setting.isPending} onChange={value => setting.mutate({ memory_enabled: value })} />
        <Setting label={t('Search earlier chats', 'ค้นแชตเก่า')} hint={t('The assistant can look through your earlier chats and link to them.', 'ผู้ช่วยค้นแชตก่อนหน้าได้ และแสดงลิงก์ไปยังแชตนั้น')} checked={prefs.chat_search} disabled={setting.isPending} onChange={value => setting.mutate({ chat_search: value })} />
        <Setting label={t('Include sensitive topics', 'จำเรื่องอ่อนไหว')} hint={t('Health, religion, politics and gender identity stay out of memory unless this is on.', 'สุขภาพ ศาสนา การเมือง และอัตลักษณ์ทางเพศจะไม่ถูกจำ ถ้าไม่เปิดตัวเลือกนี้')} checked={prefs.memory_sensitive} disabled={setting.isPending} onChange={value => setting.mutate({ memory_sensitive: value })} />
      </div>
      <p className="m-0 px-4 py-3 border-t border-line text-xs text-muted">{t('ID, bank and card numbers, passwords and keys are never kept. Memory helps the assistant work with you; it never decides a mapping, a value or an approval.', 'เลขบัตร เลขบัญชี รหัสผ่าน และคีย์จะไม่ถูกจำเด็ดขาด ความจำช่วยให้ผู้ช่วยทำงานกับคุณได้ดีขึ้น แต่ไม่มีผลกับ mapping ค่าข้อมูล หรือการอนุมัติ')}</p>
      {setting.isError && <p role="alert" className="m-0 px-4 pb-3 text-xs text-bad">{t('Could not save. Try again.', 'บันทึกไม่ได้ ลองอีกครั้ง')}</p>}
    </Section>
    <Section title={t('Add something yourself', 'เพิ่มเอง')}>
      <form className="p-4 space-y-3" onSubmit={e => { e.preventDefault(); add.mutate() }}>
        <input className="input w-full" aria-label={t('Topic', 'หัวข้อ')} placeholder={t('Topic, such as How I like reports', 'หัวข้อ เช่น รูปแบบรายงานที่ชอบ')} maxLength={120} value={topic} onChange={e => setTopic(e.target.value)} />
        <textarea className="input h-20 py-2" aria-label={t('What to remember', 'สิ่งที่จำ')} placeholder={t('What the assistant should remember', 'สิ่งที่อยากให้ผู้ช่วยจำ')} maxLength={1000} value={content} onChange={e => setContent(e.target.value)} />
        {add.isError && <p role="alert" className="text-xs text-bad">{add.error.message}</p>}
        <button className="btn btn-primary btn-sm" disabled={add.isPending || !topic.trim() || !content.trim()}><Plus size={14} />{t('Add', 'เพิ่ม')}</button>
      </form>
    </Section>
    <Section title={`${t('What is remembered', 'สิ่งที่จำไว้')} (${items.length})`} actions={!!items.length && <div className="flex flex-wrap gap-2">
      <button className="btn btn-sm" onClick={() => saveBlob(new Blob([JSON.stringify(items, null, 2)], { type: 'application/json' }), 'forma-memory.json')}><Download size={14} />{t('Export', 'ส่งออก')}</button>
      {sure ? <><button className="btn btn-sm text-bad" disabled={reset.isPending} onClick={() => reset.mutate()}>{t(`Delete all ${items.length} for good`, `ลบทั้ง ${items.length} ข้อถาวร`)}</button><button className="btn btn-sm" onClick={() => setSure(false)}>{t('Cancel', 'ยกเลิก')}</button></> : <button className="btn btn-sm" onClick={() => setSure(true)}><Trash2 size={14} />{t('Reset memory', 'ล้างความจำ')}</button>}
    </div>}>
      {isPending ? <div className="p-4"><Spinner /></div> : isError ? <div role="alert" className="p-4 text-bad">{t('This list could not be loaded.', 'ยังโหลดรายการไม่ได้')}<button className="btn btn-sm ml-2" onClick={() => void refetch()}>{t('Retry', 'ลองอีกครั้ง')}</button></div> : !items.length ? <div className="p-4"><Empty title={t('Nothing remembered yet', 'ยังไม่มีความจำ')} body={t('Tell the assistant your name or how you like reports, or say "remember this".', 'ลองบอกชื่อหรือรูปแบบรายงานที่ชอบกับผู้ช่วย หรือพิมพ์ว่า "จำไว้ว่า..."')} /></div> : <ul className="divide-y divide-line list-none m-0 p-0">{items.map(item => <Topic key={item.id + item.updated_at} item={item} />)}</ul>}
    </Section>
  </div>
}
