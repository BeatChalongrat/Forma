import { createContext, useContext, useEffect, useMemo, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, type Preferences } from './api'

const defaults: Preferences = { display_name: '', language: 'th', theme: 'light', timezone: 'Asia/Bangkok', date_format: 'day-first' }
const Context = createContext(defaults)

export function PreferencesProvider({ children }: { children: ReactNode }) {
  const { data } = useQuery({ queryKey: ['preferences'], queryFn: api.preferences, staleTime: 60000 })
  const prefs = data ?? defaults
  useEffect(() => {
    const media = matchMedia('(prefers-color-scheme: dark)')
    const apply = () => document.documentElement.classList.toggle('dark', prefs.theme === 'dark' || (prefs.theme === 'system' && media.matches))
    apply()
    media.addEventListener('change', apply)
    return () => media.removeEventListener('change', apply)
  }, [prefs.theme])
  useEffect(() => { document.documentElement.lang = prefs.language }, [prefs.language])
  return <Context.Provider value={prefs}>{children}</Context.Provider>
}

export function usePreferences() {
  const prefs = useContext(Context)
  return useMemo(() => ({
    prefs,
    t: (en: string, th: string) => prefs.language === 'th' ? th : en,
    date: (value?: string | null) => {
      if (!value) return '–'
      const d = new Date(value)
      if (Number.isNaN(d.valueOf())) return '–'
      const parts = new Intl.DateTimeFormat('en-GB', { timeZone: prefs.timezone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(d)
      const v = Object.fromEntries(parts.map(p => [p.type, p.value]))
      return `${prefs.date_format === 'iso' ? `${v.year}-${v.month}-${v.day}` : `${v.day}/${v.month}/${v.year}`} ${v.hour}:${v.minute}`
    },
  }), [prefs])
}

export function dateInZone(timezone: string, daysAgo = 0) {
  const now = new Date()
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(now)
  const v = Object.fromEntries(parts.map(p => [p.type, p.value]))
  const d = new Date(`${v.year}-${v.month}-${v.day}T12:00:00Z`)
  d.setUTCDate(d.getUTCDate() - daysAgo)
  return d.toISOString().slice(0, 10)
}
