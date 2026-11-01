import type { ReactNode } from 'react'
import { cn } from '../lib/utils'

/** One row of tabs for the full-screen tools: every view is one click away, and nothing is a hidden panel. */
export function WorkspaceTabs<T extends string>({ value, onChange, items, label }: { value: T; onChange: (id: T) => void; label: string; items: { id: T; label: ReactNode; count?: number }[] }) {
  return <div className="workspace-tabs" role="group" aria-label={label}>
    {items.map(item => <button key={item.id} type="button" aria-pressed={value === item.id} className={cn(value === item.id && 'is-on')} onClick={() => onChange(item.id)}>{item.label}{!!item.count && <span className="workspace-tab-count tnum">{item.count}</span>}</button>)}
  </div>
}
