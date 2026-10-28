import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'
import { CheckCircle2, AlertTriangle, Info, X } from 'lucide-react'
import { cn } from './utils'

type Toast = { id: number; kind: 'ok' | 'bad' | 'info'; title: string; detail?: string }
const Ctx = createContext<{ push: (t: Omit<Toast, 'id'>) => void }>({ push: () => {} })

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([])
  const push = useCallback((t: Omit<Toast, 'id'>) => {
    const id = Date.now() + Math.random()
    setItems((xs) => [...xs, { ...t, id }])
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), t.kind === 'bad' ? 9000 : 4500)
  }, [])
  const value = useMemo(() => ({ push }), [push])
  return (
    <Ctx.Provider value={value}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 w-[360px] max-w-[calc(100vw-2rem)]">
        {items.map((t) => (
          <div key={t.id} className={cn('card p-3 flex gap-2.5 items-start', t.kind === 'bad' && 'border-bad/40', t.kind === 'ok' && 'border-ok/40')}>
            {t.kind === 'ok' ? <CheckCircle2 size={16} className="text-ok mt-0.5 shrink-0" /> : t.kind === 'bad' ? <AlertTriangle size={16} className="text-bad mt-0.5 shrink-0" /> : <Info size={16} className="text-info mt-0.5 shrink-0" />}
            <div className="min-w-0 flex-1">
              <div className="font-medium">{t.title}</div>
              {t.detail && <div className="text-muted text-[12.5px] mt-0.5 break-words">{t.detail}</div>}
            </div>
            <button className="text-muted hover:text-ink" onClick={() => setItems((xs) => xs.filter((x) => x.id !== t.id))} aria-label="Dismiss"><X size={14} /></button>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  )
}
export const useToast = () => useContext(Ctx)
