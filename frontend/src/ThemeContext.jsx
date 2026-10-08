import { createContext, useContext, useEffect, useState } from 'react'
import { THEME_VALUES } from './themes.js'

// theme.css's own token resolution: any named theme in THEME_VALUES
// (an explicit user choice, stamped as data-theme) | 'system' (no stamp at
// all -- the un-stamped document, where only prefers-color-scheme decides,
// which only ever resolves light/dark regardless of how many named themes
// exist). Persisted so a reload keeps the user's explicit choice; 'system'
// is the default for a first-ever visit (nothing stamped, no localStorage
// entry). Validates against THEMES.THEME_VALUES rather than a hardcoded
// list, so adding a theme in themes.js is the only change needed here.
const STORAGE_KEY = 'algotrading.theme'
const ThemeContext = createContext(null)

function readStored() {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    return THEME_VALUES.includes(v) ? v : 'system'
  } catch {
    return 'system'
  }
}

export function ThemeProvider({ children }) {
  const [theme, setTheme] = useState(readStored)

  useEffect(() => {
    if (theme === 'system') {
      delete document.documentElement.dataset.theme
    } else {
      document.documentElement.dataset.theme = theme
    }
    try {
      if (theme === 'system') localStorage.removeItem(STORAGE_KEY)
      else localStorage.setItem(STORAGE_KEY, theme)
    } catch {
      // ignore -- e.g. private browsing with storage disabled
    }
  }, [theme])

  return <ThemeContext.Provider value={{ theme, setTheme }}>{children}</ThemeContext.Provider>
}

export function useTheme() {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme must be used within a ThemeProvider')
  return ctx
}
