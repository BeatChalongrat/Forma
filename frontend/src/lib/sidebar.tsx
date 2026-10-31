import { createContext, useContext, useEffect } from 'react'

export const SidebarHold = createContext<(held: boolean) => void>(() => undefined)

/** While a page needs the width, the main menu shrinks to icons. It goes back as it was afterwards. */
export function useSidebarHold(held: boolean) {
  const hold = useContext(SidebarHold)
  useEffect(() => { hold(held); return () => hold(false) }, [hold, held])
}
