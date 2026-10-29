import type { ReactNode } from 'react'
import { X } from 'lucide-react'
import { useMediaQuery } from '../lib/useMediaQuery'
import { Drawer } from './ui'

/** Dock on roomy screens; use a keyboard-accessible drawer when space is limited. */
export function WorkspacePanel({ open, onClose, title, children, side = 'right' }: { open: boolean; onClose: () => void; title: string; children: ReactNode; side?: 'left' | 'right' }) {
  const docked = useMediaQuery('(min-width: 1440px)')
  if (!docked) return <Drawer open={open} onClose={onClose} title={title} width="w-[480px]"><div className="workspace-drawer-content">{children}</div></Drawer>
  if (!open) return null
  return <aside className={`workspace-panel workspace-panel-${side}`} aria-label={title}>
    <div className="flex items-center justify-between gap-2 px-4 py-2 border-b border-line shrink-0">
      <h2 className="font-semibold text-[13px]">{title}</h2>
      <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label={`Close ${title}`}><X size={15} /></button>
    </div>
    <div className="flex flex-col min-h-0 flex-1 overflow-auto">{children}</div>
  </aside>
}
