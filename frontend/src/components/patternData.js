// Shared pattern / strategy lists (ALGO-34). Several places use them -- the
// chart's Indicators panel, Market Watch pattern selection, Strategy Watch,
// the pattern editor -- so they are fetched once per page and shared, and
// refreshed together after an edit (refreshPatterns / refreshStrategies).
import { useEffect, useState } from 'react'
import { listPatterns, listStrategies } from '../api/client.js'

function sharedList(fetcher, pick) {
  let data = null
  let pending = null
  const subscribers = new Set()
  function refresh() {
    pending = fetcher().then(pick).catch(() => []).then((rows) => {
      data = rows
      pending = null
      subscribers.forEach((fn) => fn(rows))
      return rows
    })
    return pending
  }
  function useList() {
    const [rows, setRows] = useState(data)
    useEffect(() => {
      subscribers.add(setRows)
      if (data == null && !pending) refresh()
      else if (data != null) setRows(data)
      return () => { subscribers.delete(setRows) }
    }, [])
    return rows // null while loading
  }
  // start loading before anything renders the list (no-op once loaded or loading)
  function prefetch() {
    if (data == null && !pending) refresh()
  }
  return { useList, refresh, prefetch }
}

const patterns = sharedList(listPatterns, (r) => r.patterns ?? [])
const strategies = sharedList(listStrategies, (r) => (Array.isArray(r) ? r : []).filter((s) => s.strategy_type !== 'recommendation_parent'))

/** All patterns (system + mine), or null while loading. */
export const usePatterns = patterns.useList
export const refreshPatterns = patterns.refresh
export const prefetchPatterns = patterns.prefetch
/** Real strategies (container rows left out), or null while loading. */
export const useStrategies = strategies.useList
export const refreshStrategies = strategies.refresh
export const prefetchStrategies = strategies.prefetch

export const PATTERN_TYPES = [
  { type: 'bullish', label: 'Bullish', color: 'var(--good)' },
  { type: 'bearish', label: 'Bearish', color: 'var(--critical)' },
  { type: 'caution', label: 'Caution (pink)', color: '#d9739b' },
  { type: 'avoid', label: 'Avoid (red)', color: 'var(--critical)' },
]
