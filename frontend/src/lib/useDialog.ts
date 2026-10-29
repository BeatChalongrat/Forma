import { useEffect, useRef } from 'react'

// A stack keeps nested dialogs from competing for Escape, focus and scroll locking.
const dialogs: HTMLElement[] = []
let originalOverflow = ''

export function useDialog(open: boolean, onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null)
  const close = useRef(onClose)
  useEffect(() => { close.current = onClose }, [onClose])
  useEffect(() => {
    const panel = ref.current
    if (!open || !panel) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    if (!dialogs.length) { originalOverflow = document.body.style.overflow; document.body.style.overflow = 'hidden' }
    dialogs.push(panel)
    const focusable = () => Array.from(panel.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]')).filter((el) => el.getClientRects().length > 0 && !el.closest('[hidden], [inert]'))
    const focusFirst = () => (panel.querySelector<HTMLElement>('[autofocus]') ?? focusable()[0] ?? panel).focus()
    focusFirst()
    const onFocus = (event: FocusEvent) => {
      if (dialogs.at(-1) === panel && !panel.contains(event.target as Node)) focusFirst()
    }
    const onKey = (event: KeyboardEvent) => {
      if (dialogs.at(-1) !== panel) return
      if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); close.current(); return }
      if (event.key !== 'Tab') return
      const items = focusable()
      if (!items.length) { event.preventDefault(); panel.focus(); return }
      if (event.shiftKey && (document.activeElement === items[0] || document.activeElement === panel)) { event.preventDefault(); items.at(-1)?.focus() }
      else if (!event.shiftKey && document.activeElement === items.at(-1)) { event.preventDefault(); items[0].focus() }
    }
    document.addEventListener('keydown', onKey, true)
    document.addEventListener('focusin', onFocus)
    return () => {
      document.removeEventListener('keydown', onKey, true)
      document.removeEventListener('focusin', onFocus)
      dialogs.splice(dialogs.indexOf(panel), 1)
      if (!dialogs.length) document.body.style.overflow = originalOverflow
      if (previous?.isConnected) previous.focus()
    }
  }, [open])
  return ref
}
