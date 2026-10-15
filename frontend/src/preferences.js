// User preferences (2026-10-15): the screen opens the way the user left it --
// timeframe, toggles, ticked patterns/strategies, open charts and tabs, theme.
// Saved per user on the server (/api/preferences), so it follows the user to
// any browser. Loaded once before the app renders (main.jsx), so reads are
// synchronous; a local copy covers the moment before the server answers, or
// a server that can't be reached. Writes are batched into one PUT.
import { useCallback, useState } from 'react'

const LOCAL_KEY = 'algotrading.preferences'
const SAVE_DELAY_MS = 400

let cache = readLocal()
let pending = {}
let timer = null

function readLocal() {
  try {
    const v = JSON.parse(localStorage.getItem(LOCAL_KEY))
    return v && typeof v === 'object' ? v : {}
  } catch {
    return {}
  }
}

function writeLocal() {
  try { localStorage.setItem(LOCAL_KEY, JSON.stringify(cache)) } catch { /* private mode etc. */ }
}

// Called once at startup; resolves (never rejects) within timeoutMs.
export function loadPreferences(timeoutMs = 2500) {
  const ctrl = new AbortController()
  const t = setTimeout(() => ctrl.abort(), timeoutMs)
  return fetch('/api/preferences', { signal: ctrl.signal })
    .then((r) => (r.ok ? r.json() : null))
    .then((server) => {
      if (server && typeof server === 'object' && !Array.isArray(server)) {
        cache = { ...server, ...pending } // anything not yet sent still counts
        writeLocal()
      }
    })
    .catch(() => {})
    .finally(() => clearTimeout(t))
}

export function getPref(key, fallback) {
  return cache[key] !== undefined ? cache[key] : fallback
}

export function setPref(key, value) {
  if (JSON.stringify(cache[key]) === JSON.stringify(value)) return
  cache = { ...cache, [key]: value }
  writeLocal()
  pending[key] = value === undefined ? null : value
  clearTimeout(timer)
  timer = setTimeout(flush, SAVE_DELAY_MS)
}

function flush(keepalive = false) {
  clearTimeout(timer)
  if (!Object.keys(pending).length) return
  const body = JSON.stringify(pending)
  pending = {}
  fetch('/api/preferences', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body, keepalive }).catch(() => {})
}

if (typeof window !== 'undefined') {
  window.addEventListener('pagehide', () => flush(true))
}

// useState that remembers its last value for this user. `valid` (optional)
// rejects a stored value the UI no longer accepts.
export function usePreference(key, fallback, valid) {
  const [value, setValue] = useState(() => {
    const v = getPref(key, fallback)
    return valid && !valid(v) ? fallback : v
  })
  const set = useCallback((next) => {
    setValue((prev) => {
      const v = typeof next === 'function' ? next(prev) : next
      setPref(key, v)
      return v
    })
  }, [key])
  return [value, set]
}
