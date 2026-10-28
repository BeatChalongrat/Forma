import { useEffect, useState } from 'react'

export type Theme = 'light' | 'dark'

const KEY = 'forma.color-mode'

function read(): Theme {
  try {
    const t = localStorage.getItem(KEY)
    if (t === 'light' || t === 'dark') return t
  } catch { /* ignore */ }
  return 'light' // the aCommerce look is light; dark mode is opt-in
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(read)
  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    try { localStorage.setItem(KEY, theme) } catch { /* ignore */ }
  }, [theme])
  return [theme, () => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))]
}
