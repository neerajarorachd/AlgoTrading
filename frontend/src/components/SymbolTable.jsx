import { useEffect, useRef, useState } from 'react'
import { getActivityCounts, getRecentActivities, getWatchSelection, putWatchSelection } from '../api/client.js'
import OrderPopup from './OrderPopup.jsx'

const DIRECTION_COLOR = { up: 'green', down: 'crimson', flat: undefined }
const VISIBLE_ROWS = 5
const ROW_HEIGHT_PX = 33
const MAX_OPEN_CHARTS = 4

function changeCell(absolute, percentage) {
  if (absolute == null) return '—'
  const color = absolute > 0 ? 'green' : absolute < 0 ? 'crimson' : undefined
  const pct = percentage != null ? ` (${percentage.toFixed(2)}%)` : ''
  return <span style={{ color }}>{absolute.toFixed(2)}{pct}</span>
}

function formatEventTs(iso) {
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
function CountBadge({ label, count, title }) {
  return (
    <span
      title={title}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 3, fontSize: 11,
        border: '1px solid #ccc', borderRadius: 10, padding: '1px 6px', color: count > 0 ? '#333' : '#aaa',
      }}
    >
      {label} {count}
    </span>
  )
}

function EventsCell({ instrumentId }) {
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

  return (
    <span style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <CountBadge label="CS" count={counts?.candle_pattern ?? 0} title="Candle formations today" />
      <CountBadge label="IND" count={counts?.indicator ?? 0} title="Indicator crossovers today" />
      <button onClick={toggle} title="Recent events" style={{ fontSize: 14, lineHeight: 1, padding: '2px 6px' }}>
        ☰
      </button>
      {open && (
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            position: 'absolute', top: '100%', right: 0, zIndex: 10, minWidth: 240, maxHeight: 260,
            overflowY: 'auto', background: 'white', border: '1px solid #ccc', borderRadius: 4,
            boxShadow: '0 2px 8px rgba(0,0,0,0.15)', padding: 8, fontSize: 12, textAlign: 'left',
          }}
        >
          {error && <div style={{ color: 'crimson' }}>{error}</div>}
          {!error && events === null && <div style={{ color: '#888' }}>Loading…</div>}
          {!error && events?.length === 0 && <div style={{ color: '#888' }}>No recent events</div>}
          {events?.map((ev, i) => (
            <div key={i} style={{ padding: '3px 0', borderBottom: i < events.length - 1 ? '1px solid #eee' : undefined }}>
              <div>{ev.label}</div>
              <div style={{ color: '#888' }}>{ev.timeframe} · {formatEventTs(ev.ts)} IST</div>
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
// fetched once by MarketWatch and passed down, not re-fetched per row.
function WatchSelectionCell({ instrumentId, elements, strategies }) {
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
      <button onClick={toggle} title="What to watch for this instrument" style={{ fontSize: 14, lineHeight: 1, padding: '2px 6px' }}>
        ⚙{excludedCount > 0 ? ` (${excludedCount} off)` : ''}
      </button>
      {open && (
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            position: 'absolute', top: '100%', right: 0, zIndex: 10, width: 280, maxHeight: 320,
            overflowY: 'auto', background: 'white', border: '1px solid #ccc', borderRadius: 4,
            boxShadow: '0 2px 8px rgba(0,0,0,0.15)', padding: 8, fontSize: 12, textAlign: 'left',
          }}
        >
          {error && <div style={{ color: 'crimson' }}>{error}</div>}
          {!error && excludedKeys === null && <div style={{ color: '#888' }}>Loading…</div>}
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
                <div key={category} style={{ marginBottom: 6 }}>
                  <div style={{ fontWeight: 600, color: '#555', marginBottom: 2 }}>{category}</div>
                  {items.map((el) => {
                    const key = `pattern:${el.code}`
                    return (
                      <label key={key} style={{ display: 'block', padding: '1px 0', cursor: 'pointer' }}>
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
                <div style={{ marginBottom: 6 }}>
                  <div style={{ fontWeight: 600, color: '#555', marginBottom: 2 }}>strategy</div>
                  {strategies.map((s) => {
                    const key = `strategy:${s.id}`
                    return (
                      <label key={key} style={{ display: 'block', padding: '1px 0', cursor: 'pointer' }}>
                        <input
                          type="checkbox" checked={!excludedKeys.has(key)}
                          onChange={() => toggleKey(key)}
                        />{' '}{s.name}
                      </label>
                    )
                  })}
                </div>
              )}
              <button type="button" onClick={save} disabled={saving} style={{ width: '100%' }}>
                {saving ? 'Saving…' : 'Save'}
              </button>
            </>
          )}
        </div>
      )}
    </span>
  )
}

export default function SymbolTable({
  symbols, liveTicks, backfillStatus, openSymbols, onToggleOpen, onRemove,
  focusedIndex, onFocusedIndexChange, elements = [], strategies = [], scoreByInstrument = {},
}) {
  const containerRef = useRef(null)
  const [popupInstrument, setPopupInstrument] = useState(null) // the row to show OrderPopup for, or null

  useEffect(() => {
    if (focusedIndex < 0 || !containerRef.current) return
    const row = containerRef.current.querySelectorAll('tbody tr')[focusedIndex]
    row?.scrollIntoView({ block: 'nearest' })
  }, [focusedIndex])

  function handleKeyDown(e) {
    if (symbols.length === 0) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      onFocusedIndexChange((focusedIndex + 1 + symbols.length) % symbols.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      onFocusedIndexChange((focusedIndex - 1 + symbols.length) % symbols.length)
    } else if ((e.key === 'Enter' || e.key === ' ') && focusedIndex >= 0) {
      e.preventDefault()
      onToggleOpen(symbols[focusedIndex])
    }
  }

  return (
    <div
      ref={containerRef}
      tabIndex={0}
      onKeyDown={handleKeyDown}
      style={{
        maxHeight: (VISIBLE_ROWS + 1) * ROW_HEIGHT_PX, overflowY: 'auto',
        border: '1px solid #ccc', outline: 'none',
      }}
    >
      <table border="1" cellPadding="6" style={{ borderCollapse: 'collapse', width: '100%' }}>
        <thead style={{ position: 'sticky', top: 0, background: 'white' }}>
          <tr>
            <th>Symbol</th><th>Exchange</th><th>LTP</th><th>Chg</th><th>Chg %</th><th>Dir</th>
            <th>Gap</th><th>Day</th><th>Candle</th><th>Events</th><th>Watch</th><th>Trade</th><th></th>
          </tr>
        </thead>
        <tbody>
          {symbols.map((row, index) => {
            const tick = liveTicks[row.symbol]
            const isOpen = openSymbols.some((s) => s.id === row.id)
            const isFocused = index === focusedIndex
            const atCapacity = !isOpen && openSymbols.length >= MAX_OPEN_CHARTS
            const status = backfillStatus[row.symbol]
            return (
              <tr
                key={row.id}
                onClick={() => onToggleOpen(row)}
                onMouseEnter={() => onFocusedIndexChange(index)}
                title={atCapacity ? `Max ${MAX_OPEN_CHARTS} charts open at once` : undefined}
                style={{
                  cursor: 'pointer',
                  background: isOpen ? '#dfe8ff' : isFocused ? '#f3f3f3' : undefined,
                  opacity: atCapacity ? 0.6 : 1,
                }}
              >
                <td>
                  {row.symbol}
                  {status && status.status !== 'done' && (
                    <div style={{ fontSize: 11, color: status.status === 'failed' ? 'crimson' : '#888' }}>
                      {status.message}
                    </div>
                  )}
                </td>
                <td>{row.exchange}</td>
                <td style={{ color: tick && DIRECTION_COLOR[tick.direction] }}>
                  {tick ? tick.ltp.toFixed(2) : '—'}
                </td>
                <td style={{ color: tick && DIRECTION_COLOR[tick.direction] }}>
                  {tick ? tick.absolute_change.toFixed(2) : '—'}
                </td>
                <td style={{ color: tick && DIRECTION_COLOR[tick.direction] }}>
                  {tick ? `${tick.percentage_change.toFixed(2)}%` : '—'}
                </td>
                <td>{tick ? tick.direction : '—'}</td>
                <td>{tick ? changeCell(tick.gap_absolute, tick.gap_percentage) : '—'}</td>
                <td>{tick ? changeCell(tick.day_change_absolute, tick.day_change_percentage) : '—'}</td>
                <td>{tick ? changeCell(tick.candle_change_absolute, tick.candle_change_percentage) : '—'}</td>
                <td><EventsCell instrumentId={row.id} /></td>
                <td><WatchSelectionCell instrumentId={row.id} elements={elements} strategies={strategies} /></td>
                <td>
                  {(() => {
                    const score = scoreByInstrument[row.id]
                    const direction = score && (score.bull_score > score.bear_score ? 'bull'
                      : score.bear_score > score.bull_score ? 'bear' : null)
                    if (!direction) return <span style={{ color: '#ccc' }}>—</span>
                    return (
                      <button
                        onClick={(e) => { e.stopPropagation(); setPopupInstrument(row) }}
                        style={{
                          color: '#fff', border: 'none', borderRadius: 4, padding: '3px 10px', cursor: 'pointer',
                          background: direction === 'bull' ? '#1b7a3d' : '#b3261e', fontWeight: 600,
                        }}
                      >
                        {direction === 'bull' ? 'Buy' : 'Sell'}
                      </button>
                    )
                  })()}
                </td>
                <td>
                  <button onClick={(e) => { e.stopPropagation(); onRemove(row.id) }}>Remove</button>
                </td>
              </tr>
            )
          })}
          {symbols.length === 0 && (
            <tr><td colSpan={13} style={{ textAlign: 'center', color: '#888' }}>No instruments registered yet</td></tr>
          )}
        </tbody>
      </table>
      {popupInstrument && (
        <OrderPopup
          instrument={popupInstrument}
          ltp={liveTicks[popupInstrument.symbol]?.ltp}
          onClose={() => setPopupInstrument(null)}
        />
      )}
    </div>
  )
}
