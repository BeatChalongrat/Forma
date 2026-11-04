import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Camera, Check, ChevronDown, ChevronRight, Clock3, Image, Languages, LogOut, Monitor, Moon, Sun } from 'lucide-react'
import { api, type Preferences } from '../lib/api'
import { usePreferences } from '../lib/preferences'
import { logout } from '../lib/auth'
import { cn } from '../lib/utils'

export function Profile({ onOpenChange }: { onOpenChange?: (open: boolean) => void }) {
  const { prefs, t } = usePreferences()
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: api.me })
  const qc = useQueryClient()
  const root = useRef<HTMLDivElement>(null), trigger = useRef<HTMLButtonElement>(null), panel = useRef<HTMLDivElement>(null), file = useRef<HTMLInputElement>(null)
  const [open, setOpen] = useState(false)
  useEffect(() => { onOpenChange?.(open) }, [open, onOpenChange])
  const name = prefs.display_name || me?.name || t('Your account', 'บัญชีของคุณ')
  const roleNames: Record<string, string> = { admin: t('Administrator', 'ผู้ดูแลระบบ'), approver: t('Approver', 'ผู้อนุมัติ'), reviewer: t('Reviewer', 'ผู้ตรวจข้อมูล'), importer: t('File uploader', 'ผู้นำเข้าไฟล์'), viewer: t('Viewer', 'ผู้ดูข้อมูล') }
  const role = me?.effective_roles.includes('admin') ? roleNames.admin : (me?.roles.map(r => roleNames[r] || r).join(' · ') || t('Account', 'ผู้ใช้งาน'))
  const [view, setView] = useState<'main' | 'profile' | 'regional'>('main')
  const [draft, setDraft] = useState(prefs)
  const [photoError, setPhotoError] = useState('')
  const [photoBusy, setPhotoBusy] = useState(false)
  const save = useMutation({ mutationFn: (value: Preferences) => api.savePreferences(value), onSuccess: p => { qc.setQueryData(['preferences'], p); setDraft(p); setView('main') } })
  useEffect(() => {
    if (!open) return
    panel.current?.querySelector<HTMLButtonElement>('button')?.focus()
    const outside = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false) }
    const escape = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.preventDefault(); setOpen(false); trigger.current?.focus() } }
    document.addEventListener('pointerdown', outside); document.addEventListener('keydown', escape)
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape) }
  }, [open, view])
  const navigate = (next: typeof view) => { setDraft(prefs); setPhotoError(''); save.reset(); setView(next) }
  const avatar = (src: string | undefined, name: string, large = false) => <span className={cn('rounded-full bg-surface-2 text-navy font-medium grid place-items-center overflow-hidden shrink-0 uppercase', large ? 'w-12 h-12' : 'w-9 h-9 text-xs')}>{src ? <img src={src} alt={t('Profile photo', 'รูปโปรไฟล์')} className="w-full h-full object-cover" /> : (name || me?.name || 'OP').slice(0, 2)}</span>
  const choosePhoto = async (selected?: File) => {
    if (!selected) return
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(selected.type) || selected.size > 5 * 1024 * 1024) { setPhotoError(t('Choose a JPG, PNG or WebP image under 5 MB.', 'เลือกรูป JPG, PNG หรือ WebP ขนาดไม่เกิน 5 MB')); return }
    setPhotoBusy(true); setPhotoError('')
    try {
      const bitmap = await createImageBitmap(selected)
      const canvas = document.createElement('canvas'); canvas.width = canvas.height = 192
      const context = canvas.getContext('2d'); if (!context) throw new Error('Canvas unavailable')
      const size = Math.min(bitmap.width, bitmap.height)
      context.drawImage(bitmap, (bitmap.width - size) / 2, (bitmap.height - size) / 2, size, size, 0, 0, 192, 192); bitmap.close()
      setDraft(d => ({ ...d, avatar: canvas.toDataURL('image/jpeg', 0.85) }))
    } catch { setPhotoError(t('Could not read this image.', 'อ่านรูปนี้ไม่ได้ ลองเลือกรูปอื่น')) } finally { setPhotoBusy(false) }
  }
  return <div ref={root} className="relative ml-2" onBlur={e => { if (e.relatedTarget && !e.currentTarget.contains(e.relatedTarget)) setOpen(false) }}>
    <button ref={trigger} className="flex items-center gap-2 rounded-lg px-1.5 py-1 text-left hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-signal" aria-label={t('Open profile and preferences', 'เปิดโปรไฟล์และการตั้งค่า')} aria-haspopup="dialog" aria-expanded={open} aria-controls="profile-popover" onClick={() => { if (!open) navigate('main'); setOpen(!open) }}>{avatar(prefs.avatar, prefs.display_name)}<span className="min-w-0 max-w-24 sm:max-w-40"><span className="block truncate text-[13px] font-medium text-navy">{name}</span><span className="block truncate text-[11px] text-muted">{role}</span></span><ChevronDown size={14} className={cn('shrink-0 text-muted transition-transform', open && 'rotate-180')} aria-hidden="true" /></button>
    {open && <div ref={panel} id="profile-popover" role="dialog" aria-label={t('Profile menu', 'เมนูโปรไฟล์')} className="absolute right-0 top-[calc(100%+12px)] z-50 w-[300px] max-w-[calc(100vw-24px)] max-h-[calc(100dvh-90px)] overflow-y-auto rounded-2xl border border-line bg-popover shadow-xl p-2 text-sm">
      {view === 'main' ? <>
        <div className="flex gap-3 items-center px-3 py-3">{avatar(prefs.avatar, prefs.display_name)}<div className="min-w-0"><div className="font-medium truncate">{name}</div><div className="text-xs text-muted">{role}</div></div></div>
        <button className="profile-menu-row" onClick={() => navigate('profile')}><Image size={16} />{t('Photo & display name', 'รูปและชื่อโปรไฟล์')}<ChevronRight size={15} className="ml-auto" /></button>
        <div className="profile-menu-divider" /><div className="text-xs text-muted px-3 py-1 flex gap-2 items-center"><Languages size={14} />{t('Language', 'ภาษา')}</div>
        {(['th', 'en'] as const).map(language => <button key={language} className="profile-menu-row pl-10" aria-pressed={prefs.language === language} disabled={save.isPending} onClick={() => save.mutate({ ...prefs, language })}>{language === 'th' ? 'ไทย' : 'English'}{prefs.language === language && <Check size={15} className="ml-auto text-signal" />}</button>)}
        <div className="profile-menu-divider" /><div className="text-xs text-muted px-3 py-1">{t('Appearance', 'หน้าตา')}</div>
        <div className="flex bg-surface-2 rounded-lg p-1 gap-1 mx-3 my-2">{([{ value: 'light', Icon: Sun, title: t('Light', 'สว่าง') }, { value: 'dark', Icon: Moon, title: t('Dark', 'มืด') }, { value: 'system', Icon: Monitor, title: t('Use device setting', 'ตามอุปกรณ์') }] as const).map(({ value, Icon, title }) => <button key={value} className={cn('flex-1 grid place-items-center rounded-md py-2', prefs.theme === value && 'bg-surface shadow-sm text-navy')} aria-label={title} title={title} aria-pressed={prefs.theme === value} disabled={save.isPending} onClick={() => save.mutate({ ...prefs, theme: value })}><Icon size={16} /></button>)}</div>
        <button className="profile-menu-row" onClick={() => navigate('regional')}><Clock3 size={16} />{t('Time & date', 'เวลาและวันที่')}<ChevronRight size={15} className="ml-auto" /></button>
        <div className="profile-menu-divider" />
        {me?.mode === 'oidc' ? <button className="profile-menu-row" onClick={logout}><LogOut size={16} />{t('Sign out', 'ออกจากระบบ')}</button> : <p className="text-xs text-muted px-3 py-2">{t('Local account · no sign-in session', 'บัญชีในเครื่อง · ยังไม่มีเซสชันเข้าสู่ระบบ')}</p>}
      </> : <>
        <button className="profile-menu-row font-medium" onClick={() => navigate('main')}><ArrowLeft size={16} />{view === 'profile' ? t('Photo & display name', 'รูปและชื่อโปรไฟล์') : t('Time & date', 'เวลาและวันที่')}</button>
        <form className="p-3 grid gap-4" onSubmit={e => { e.preventDefault(); save.mutate(draft) }}>
          {view === 'profile' ? <><div className="flex items-center gap-3">{avatar(draft.avatar, draft.display_name, true)}<button type="button" className="btn btn-sm" disabled={photoBusy} onClick={() => file.current?.click()}><Camera size={16} />{t('Change photo', 'เปลี่ยนรูป')}</button><input ref={file} type="file" accept="image/jpeg,image/png,image/webp" className="hidden" onChange={e => void choosePhoto(e.target.files?.[0])} /></div>{draft.avatar && <button type="button" className="text-xs text-muted text-left" onClick={() => setDraft(d => ({ ...d, avatar: '' }))}>{t('Remove photo', 'นำรูปออก')}</button>}<label className="grid gap-1.5">{t('Display name', 'ชื่อที่แสดง')}<input className="input" maxLength={80} value={draft.display_name} onChange={e => setDraft(d => ({ ...d, display_name: e.target.value }))} /></label><p className="text-xs text-muted">{t('Your login and permissions stay the same.', 'ชื่อที่แสดงไม่มีผลต่อบัญชีและสิทธิ์การใช้งาน')}</p></> : <><label className="grid gap-1.5">{t('Time zone', 'เขตเวลา')}<input className="input" value={draft.timezone} onChange={e => setDraft(d => ({ ...d, timezone: e.target.value }))} list="profile-zones" required /><datalist id="profile-zones">{['Asia/Bangkok', 'Asia/Singapore', 'Asia/Tokyo', 'UTC', 'Europe/London', 'America/New_York'].map(z => <option key={z}>{z}</option>)}</datalist></label><label className="grid gap-1.5">{t('Date format', 'รูปแบบวันที่')}<select className="input" value={draft.date_format} onChange={e => setDraft(d => ({ ...d, date_format: e.target.value as Preferences['date_format'] }))}><option value="day-first">DD/MM/YYYY</option><option value="iso">YYYY-MM-DD</option></select></label></>}
          {photoError && <p role="alert" className="text-bad text-xs">{photoError}</p>}<button className="btn btn-primary" disabled={save.isPending || photoBusy}>{save.isPending ? t('Saving…', 'กำลังบันทึก…') : t('Save', 'บันทึก')}</button>
        </form>
      </>}
      {save.error && <p role="alert" className="text-bad text-xs px-3 py-2">{t('Could not save. Check the values and try again.', 'บันทึกไม่ได้ ตรวจสอบข้อมูลแล้วลองอีกครั้ง')}</p>}
    </div>}
  </div>
}
