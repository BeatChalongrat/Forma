import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowRight, ChevronRight, Search } from 'lucide-react'
import { api } from '../lib/api'
import { FEATURES, matches, STEPS, useOpenFeature } from '../lib/features'
import { useToast } from '../lib/toast'

/** Every feature, grouped by the step of the job it belongs to, with where it lives and a button that opens it. */
export function GuidePage() {
  const [q, setQ] = useState('')
  const toast = useToast()
  const open = useOpenFeature((m) => toast.push({ kind: 'info', title: 'Nothing to open yet', detail: m }))
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: api.me, staleTime: 60000 })
  const isAdmin = me?.effective_roles.includes('admin') ?? false
  const shown = FEATURES.filter((f) => (!f.adminOnly || isAdmin) && matches(f, q))
  return (
    <div>
      <div className="text-center pt-2 pb-8">
        <span className="pill">Feature guide</span>
        <h1 className="heading text-[34px] leading-tight mt-4">Everything FORMA does,<br />and where to find it</h1>
        <p className="text-ink-2 mt-3 max-w-[640px] mx-auto">ทุกฟีเจอร์เรียงตามขั้นตอนการทำงาน กด Open เพื่อเปิดฟีเจอร์นั้นบนรอบล่าสุด หรือกด <span className="kbd">Ctrl K</span> ที่หน้าไหนก็ได้แล้วพิมพ์สิ่งที่อยากทำ</p>
        <div className="relative max-w-[460px] mx-auto mt-5">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-signal" />
          <input className="input h-10 pl-9 rounded-[10px]" placeholder="Filter — e.g. trace, ราคา, key" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>

      {/* the job in four steps */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-3 mb-10">
        {STEPS.filter((s) => s.id !== 'setup').map((s, i) => (
          <div key={s.id} className="card p-4 relative">
            <div className="w-8 h-8 rounded-full text-white font-bold grid place-items-center text-[13px]" style={{ backgroundImage: 'var(--gradient-brand)' }}>{i + 1}</div>
            <div className="heading mt-3">{s.title.replace(/^\d · /, '')}</div>
            <div className="hint-th mt-1">{s.th}</div>
            {i < 3 && <ChevronRight size={18} className="hidden md:block absolute -right-[15px] top-1/2 -translate-y-1/2 text-line-2" />}
          </div>
        ))}
      </div>

      {STEPS.map((s) => {
        const list = shown.filter((f) => f.step === s.id)
        if (!list.length) return null
        return (
          <section key={s.id} className="mb-9">
            <div className="flex items-baseline gap-3 mb-3">
              <h2 className="heading text-[18px]">{s.title}</h2>
              <span className="hint-th">{s.th}</span>
            </div>
            <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-3">
              {list.map((f) => (
                <div key={f.id} className="card p-4 flex flex-col">
                  <div className="flex items-start gap-3">
                    <span className="w-9 h-9 rounded-[10px] bg-signal-soft text-signal grid place-items-center shrink-0"><f.icon size={18} /></span>
                    <div className="min-w-0">
                      <div className="heading leading-snug">{f.title}</div>
                      <div className="text-[11.5px] text-signal font-semibold mt-0.5">{f.where}</div>
                    </div>
                  </div>
                  <p className="text-[12.5px] text-ink-2 mt-2.5 flex-1">{f.th}</p>
                  <button className="btn btn-outline btn-sm self-start mt-3" onClick={() => void open(f)}>Open <ArrowRight size={13} /></button>
                </div>
              ))}
            </div>
          </section>
        )
      })}
      {!shown.length && <div className="card p-10 text-center text-muted">Nothing matches “{q}”.</div>}
    </div>
  )
}
