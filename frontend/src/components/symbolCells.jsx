import { useEffect, useState } from 'react'
import { getActivityCounts, getRecentActivities, getWatchSelection, putWatchSelection } from '../api/client.js'
import Button from './kit/Button.jsx'

// Shared grid-cell building blocks -- originally all lived inside
// SymbolTable.jsx, extracted so both the frozen MarketWatchClassic grid and
// the new InstrumentGrid can use the exact same real, backend-wired
// popovers instead of one of them drifting into a duplicate.

export const DIRECTION_COLOR = { up: 'var(--up)', down: 'var(--down)', flat: undefined }

// Shared panel chrome for the click-to-open popovers below (events, watch
// selection) -- same surface/border/shadow the kit's Card uses, just
// absolutely positioned instead of flowing in the page.
export const POPOVER_STYLE = {
  position: 'absolute', top: '100%', right: 0, zIndex: 10, maxHeight: 320,
  overflowY: 'auto', background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 'var(--radius-md)', boxShadow: 'var(--shadow-md)', padding: 10,
  fontSize: 12.5, textAlign: 'left', color: 'var(--text)',
}

export function changeCell(absolute, percentage) {
  if (absolute == null) return '—'
  const color = absolute > 0 ? 'var(--up)' : absolute < 0 ? 'var(--down)' : undefined
  const pct = percentage != null ? ` (${percentage.toFixed(2)}%)` : ''
  return <span className="num" style={{ color }}>{absolute.toFixed(2)}{pct}</span>
}

export function formatEventTs(iso) {
  // Explicit Asia/Kolkata rather than the browser's own local timezone --
  // matches Recommendations.jsx's own formatTs convention (an NSE-only
  // system should always read in IST, wherever it's viewed from).
  return new Date(iso).toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true,
  })
}

// "☰ list" icon + popover of an instrument's recent events (doji formed,
// MACD crossover, etc, newest first) -- fetched on click, not up front for
// every row, so a watchlist of 50 stocks doesn't fire 50 requests on load.
// Small count badge for one event category -- fetched once per row on
// mount (today's counts only), unlike the popover's own events list which
// is lazy/on-click. A watchlist-sized row count (today: ~13) makes one
// request per row acceptable without a batched endpoint; revisit if
// watchlists grow much larger.
export function CountBadge({ label, count, title }) {
  return (
    <span
      title={title}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 3, fontSize: 11, fontWeight: 600,
        border: '1px solid var(--border)', borderRadius: 10, padding: '1px 7px',
        color: count > 0 ? 'var(--accent-text)' : 'var(--text-faint)',
        background: count > 0 ? 'var(--accent-soft)' : 'transparent',
      }}
    >
      {label} {count}
    </span>
  )
}

export function EventsCell({ instrumentId, compact = false }) {
  const [open, setOpen] = useState(false)
  const [events, setEvents] = useState(null) // null = not loaded yet
  const [error, setError] = useState(null)
  const [counts, setCounts] = useState(null)

  useEffect(() => {
    getActivityCounts(instrumentId).then(setCounts).catch(() => {})
  }, [instrumentId])

  function toggle(e) {
    e.stopPropagation()
    const next = !open
    setOpen(next)
    if (next && events === null) {
      getRecentActivities(instrumentId).then(setEvents).catch((err) => setError(err.message))
    }
  }

  useEffect(() => {
    if (!open) return
    const closeOnOutsideClick = () => setOpen(false)
    document.addEventListener('click', closeOnOutsideClick)
    return () => document.removeEventListener('click', closeOnOutsideClick)
  }, [open])

  const csCount = counts?.candle_pattern ?? 0
  const indCount = counts?.indicator ?? 0

  return (
    <span style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      {compact ? (
        <span
          title={`${csCount} candle formation(s), ${indCount} indicator crossover(s) today`}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 3, fontSize: 11, fontWeight: 600,
            border: '1px solid var(--border)', borderRadius: 10, padding: '1px 7px',
            color: (csCount + indCount) > 0 ? 'var(--accent-text)' : 'var(--text-faint)',
            background: (csCount + indCount) > 0 ? 'var(--accent-soft)' : 'transparent',
          }}
        >
          C{csCount} I{indCount}
        </span>
      ) : (
        <>
          <CountBadge label="CS" count={csCount} title="Candle formations today" />
          <CountBadge label="IND" count={indCount} title="Indicator crossovers today" />
        </>
      )}
      <button
        onClick={toggle} title="Recent events"
        style={{ fontSize: 14, lineHeight: 1, padding: '2px 6px', background: 'transparent', border: 'none', color: 'var(--text-dim)' }}
      >
        ☰
      </button>
      {open && (
        <div onClick={(e) => e.stopPropagation()} style={{ ...POPOVER_STYLE, minWidth: 240, maxHeight: 260 }}>
          {error && <div style={{ color: 'var(--critical)' }}>{error}</div>}
          {!error && events === null && <div style={{ color: 'var(--text-faint)' }}>Loading…</div>}
          {!error && events?.length === 0 && <div style={{ color: 'var(--text-faint)' }}>No recent events</div>}
          {events?.map((ev, i) => (
            <div key={i} style={{ padding: '4px 0', borderBottom: i < events.length - 1 ? '1px solid var(--border)' : undefined }}>
              <div>{ev.label}</div>
              <div style={{ color: 'var(--text-faint)', fontSize: 11.5 }}>{ev.timeframe} · {formatEventTs(ev.ts)} IST</div>
            </div>
          ))}
        </div>
      )}
    </span>
  )
}

// "what to watch" for one instrument -- which formations/indicators (both
// just PATTERN_CATALOG-backed `elements`) and which strategies show up for
// it on Market Watch. Explicit instruction, 2026-10-03: "for each added
// instrument, I should be able to setup what to watch... By default, all
// should be selected." The server only stores EXCLUSIONS (see
// InstrumentWatchExclusion's own docstring), so every checkbox starts
// checked and unchecking one adds it to the saved exclusion set -- this
// component just has to invert that once on load and once on save.
// `elements`/`strategies` are the GLOBAL catalogs (same for every row),
// fetched once by the page and passed down, not re-fetched per row.
export function WatchSelectionCell({ instrumentId, elements, strategies }) {
  const [open, setOpen] = useState(false)
  const [excludedKeys, setExcludedKeys] = useState(null) // null = not loaded yet; Set of "type:code"
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  // Eager, on mount -- same convention as EventsCell's CS/IND counts badge
  // (fetched once per row on mount, not lazily on first open), so the
  // "(N off)" badge is visible at a glance without opening the popover.
  useEffect(() => {
    getWatchSelection(instrumentId)
      .then((res) => setExcludedKeys(new Set(res.excluded.map((x) => `${x.item_type}:${x.item_code}`))))
      .catch(() => {})
  }, [instrumentId])

  function toggle(e) {
    e.stopPropagation()
    setOpen((prev) => !prev)
  }

  useEffect(() => {
    if (!open) return
    const closeOnOutsideClick = () => setOpen(false)
    document.addEventListener('click', closeOnOutsideClick)
    return () => document.removeEventListener('click', closeOnOutsideClick)
  }, [open])

  function toggleKey(key) {
    setExcludedKeys((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  function save() {
    const excluded = [...excludedKeys].map((key) => {
      const [item_type, item_code] = key.split(':')
      return { item_type, item_code }
    })
    setSaving(true)
    setError(null)
    putWatchSelection(instrumentId, excluded)
      .then(() => setOpen(false))
      .catch((err) => setError(err.message))
      .finally(() => setSaving(false))
  }

  const excludedCount = excludedKeys?.size ?? 0

  return (
    <span style={{ position: 'relative', display: 'inline-flex', alignItems: 'center' }}>
      <button
        onClick={toggle} title="What to watch for this instrument"
        style={{ fontSize: 14, lineHeight: 1, padding: '2px 6px', background: 'transparent', border: 'none', color: 'var(--text-dim)' }}
      >
        ⚙{excludedCount > 0 ? ` (${excludedCount} off)` : ''}
      </button>
      {open && (
        <div onClick={(e) => e.stopPropagation()} style={{ ...POPOVER_STYLE, width: 280 }}>
          {error && <div style={{ color: 'var(--critical)' }}>{error}</div>}
          {!error && excludedKeys === null && <div style={{ color: 'var(--text-faint)' }}>Loading…</div>}
          {!error && excludedKeys !== null && (
            <>
              {Object.entries(
                // Only "event" elements are watchable patterns -- "numeric"
                // ones (RSI/MACD_LINE/... raw values) aren't something that
                // "fires," so they don't belong in a watch picker. Grouped
                // by `kind` (PatternDefinition's finer category -- single_
                // candle/structure/graph_formation/...), NOT `element_type`
                // (event/numeric), which would put all 41 patterns in one
                // "event" bucket -- a real bug found 2026-10-04 verifying
                // this exact picker in the browser.
                elements.filter((el) => el.element_type === 'event').reduce((groups, el) => {
                  (groups[el.kind ?? 'other'] ??= []).push(el)
                  return groups
                }, {}),
              ).map(([category, items]) => (
                <div key={category} style={{ marginBottom: 8 }}>
                  <div style={{ fontWeight: 700, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.03em', color: 'var(--text-faint)', marginBottom: 3 }}>{category}</div>
                  {items.map((el) => {
                    const key = `pattern:${el.code}`
                    return (
                      <label key={key} style={{ display: 'block', padding: '2px 0', cursor: 'pointer' }}>
                        <input
                          type="checkbox" checked={!excludedKeys.has(key)}
                          onChange={() => toggleKey(key)}
                        />{' '}{el.code}
                      </label>
                    )
                  })}
                </div>
              ))}
              {strategies.length > 0 && (
                <div style={{ marginBottom: 8 }}>
                  <div style={{ fontWeight: 700, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.03em', color: 'var(--text-faint)', marginBottom: 3 }}>Strategy</div>
                  {strategies.map((s) => {
                    const key = `strategy:${s.id}`
                    return (
                      <label key={key} style={{ display: 'block', padding: '2px 0', cursor: 'pointer' }}>
                        <input
                          type="checkbox" checked={!excludedKeys.has(key)}
                          onChange={() => toggleKey(key)}
                        />{' '}{s.name}
                      </label>
                    )
                  })}
                </div>
              )}
              <Button onClick={save} disabled={saving} style={{ width: '100%' }}>
                {saving ? 'Saving…' : 'Save'}
              </Button>
            </>
          )}
        </div>
      )}
    </span>
  )
}
