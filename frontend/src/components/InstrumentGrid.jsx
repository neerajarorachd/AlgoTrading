import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import OrderPopup from './OrderPopup.jsx'
import { DIRECTION_COLOR, EventsCell, WatchSelectionCell } from './symbolCells.jsx'

const VISIBLE_ROWS = 5
const ROW_HEIGHT_PX = 33
const MAX_OPEN_CHARTS = 4

const BUCKET_DOT_FALLBACK = 'var(--text-faint)'
const BUCKET_LABEL = {
  strong_bull: 'Strong bull', mild_bull: 'Mild bull', quiet: 'Quiet',
  choppy: 'Choppy', mild_bear: 'Mild bear', strong_bear: 'Strong bear',
}

// One action per row, driven entirely by the bucket -- as finalized in the
// preview: the bucket IS the recommendation. Strong -> Buy/Sell (opens the
// order popup), mild -> Hold (informational only), choppy/quiet/no data ->
// no button. Replaced 2026-10-06 the earlier "whichever score is higher"
// rule, which showed Buy/Sell even on choppy rows (both sides active).
function rowActionFor(bucket) {
  if (bucket === 'strong_bull') return { label: 'Buy', kind: 'buy' }
  if (bucket === 'strong_bear') return { label: 'Sell', kind: 'sell' }
  if (bucket === 'mild_bull' || bucket === 'mild_bear') return { label: 'Hold', kind: 'hold' }
  return null
}

const ACTION_STYLE = {
  buy: { background: 'var(--good)', color: '#fff' },
  sell: { background: 'var(--critical)', color: '#fff' },
  hold: { background: 'var(--surface-alt)', color: 'var(--text-dim)', border: '1px solid var(--border)' },
}

const ICON_BUTTON = {
  background: 'transparent', border: 'none', color: 'var(--text-dim)', fontSize: 14, cursor: 'pointer', padding: '0 3px',
}

// Gap/Day/Candle lead with the percentage, not the absolute value -- the
// opposite order from the frozen MarketWatchClassic/SymbolTable's
// `changeCell`, by explicit request ("percentage leads"). Kept local to
// this component rather than folded into symbolCells.jsx since the two
// pages deliberately differ here.
function changePercentFirst(absolute, percentage) {
  if (absolute == null) return '—'
  const color = absolute > 0 ? 'var(--up)' : absolute < 0 ? 'var(--down)' : undefined
  const pctText = percentage != null ? `${percentage.toFixed(2)}%` : '—'
  return <span className="num" style={{ color }}>{pctText} ({absolute.toFixed(2)})</span>
}

// Column definitions for click-to-sort headers. Each getter reads off the
// real live tick -- no fabricated fields -- so only columns the backend
// already populates are sortable. BB-width/day-volume sorting is
// deliberately not here yet; see market_watch_grid_redesign_plan.md.
const COLUMNS = [
  { key: 'symbol', label: 'Symbol', get: (row) => row.symbol },
  { key: 'exchange', label: 'Exchange', get: (row) => row.exchange },
  { key: 'ltp', label: 'LTP', get: (row, tick) => tick?.ltp },
  { key: 'chg', label: 'Chg', get: (row, tick) => tick?.absolute_change },
  { key: 'chgPct', label: 'Chg %', get: (row, tick) => tick?.percentage_change },
  { key: 'dir', label: 'Dir', get: (row, tick) => tick?.direction },
  { key: 'gap', label: 'Gap', get: (row, tick) => tick?.gap_percentage },
  { key: 'day', label: 'Day', get: (row, tick) => tick?.day_change_percentage },
  { key: 'candle', label: 'Candle', get: (row, tick) => tick?.candle_change_percentage },
]

function compareValues(a, b) {
  if (a == null && b == null) return 0
  if (a == null) return 1
  if (b == null) return -1
  if (typeof a === 'string') return a.localeCompare(b)
  return a - b
}

// showAll: lift the 5-visible-rows cap (the card footer's "Show all").
// embedded: rendered inside Market Watch's INSTRUMENTS card, which owns the
// border -- no border/radius of its own then. externalSortMode +
// onColumnSort: the card's Sort dropdown and these column headers are
// mutually exclusive (one active sort at a time, as in the preview) --
// a dropdown change clears the column sort here, a header click tells the
// parent to reset its dropdown.
export default function InstrumentGrid({
  symbols, liveTicks, backfillStatus, openSymbols, onToggleOpen, onRemove, onOpenTab,
  focusedIndex, onFocusedIndexChange, elements = [], strategies = [], scoreByInstrument = {}, bucketColors = {},
  showAll = false, embedded = false, externalSortMode = '', onColumnSort,
}) {
  const containerRef = useRef(null)
  const [popupInstrument, setPopupInstrument] = useState(null)
  const [holdPopover, setHoldPopover] = useState(null) // { symbol, top, left } -- fixed-position, so the scrolling grid can't clip it
  const [sort, setSort] = useState(null) // { key, dir: 'asc' | 'desc' } | null

  useEffect(() => {
    if (!holdPopover) return
    const close = () => setHoldPopover(null)
    document.addEventListener('mousedown', close)
    containerRef.current?.addEventListener('scroll', close)
    const el = containerRef.current
    return () => { document.removeEventListener('mousedown', close); el?.removeEventListener('scroll', close) }
  }, [holdPopover])

  function openHold(e, row) {
    e.stopPropagation()
    const rect = e.currentTarget.getBoundingClientRect()
    setHoldPopover((prev) => (prev?.symbol === row.symbol ? null : { symbol: row.symbol, top: rect.bottom + 4, left: Math.max(8, rect.right - 230) }))
  }

  useEffect(() => {
    if (externalSortMode) setSort(null)
  }, [externalSortMode])

  function handleSortClick(key) {
    onColumnSort?.()
    setSort((prev) => {
      if (!prev || prev.key !== key) return { key, dir: 'desc' }
      if (prev.dir === 'desc') return { key, dir: 'asc' }
      return null // third click clears back to the incoming (bucket-filtered) order
    })
  }

  const rows = useMemo(() => {
    if (!sort) return symbols
    const col = COLUMNS.find((c) => c.key === sort.key)
    if (!col) return symbols
    const sign = sort.dir === 'asc' ? 1 : -1
    return [...symbols].sort((a, b) => sign * compareValues(col.get(a, liveTicks[a.symbol]), col.get(b, liveTicks[b.symbol])))
  }, [symbols, sort, liveTicks])

  // Cap = header + the real heights of the first VISIBLE_ROWS rows, so
  // exactly 5 rows show (matching the card footer's "Showing 5 of N").
  // A fixed 33px/row assumption showed only ~4.5 once rows carry a
  // Buy/Sell button, which makes them taller.
  const [measuredCap, setMeasuredCap] = useState(null)
  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return
    const head = el.querySelector('thead')?.offsetHeight ?? 0
    const firstRows = [...el.querySelectorAll('tbody tr')].slice(0, VISIBLE_ROWS)
    if (firstRows.length === 0) return
    setMeasuredCap(head + firstRows.reduce((sum, tr) => sum + tr.offsetHeight, 0))
  })

  function handleKeyDown(e) {
    if (symbols.length === 0) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      onFocusedIndexChange((focusedIndex + 1 + rows.length) % rows.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      onFocusedIndexChange((focusedIndex - 1 + rows.length) % rows.length)
    } else if ((e.key === 'Enter' || e.key === ' ') && focusedIndex >= 0) {
      e.preventDefault()
      onToggleOpen(rows[focusedIndex])
    }
  }

  return (
    <div
      ref={containerRef}
      tabIndex={0}
      onKeyDown={handleKeyDown}
      style={{
        maxHeight: showAll ? 'none' : (measuredCap ?? (VISIBLE_ROWS + 1) * ROW_HEIGHT_PX), overflowY: 'auto', background: 'var(--surface)',
        border: embedded ? 'none' : '1px solid var(--border)', borderRadius: embedded ? 0 : 'var(--radius-md)', outline: 'none',
      }}
    >
      <table>
        <thead style={{ position: 'sticky', top: 0, zIndex: 2, background: 'var(--surface-alt)', boxShadow: '0 1px 0 var(--border)' }}>
          <tr>
            {COLUMNS.map((col) => {
              const active = sort?.key === col.key
              return (
                <th
                  key={col.key}
                  onClick={() => handleSortClick(col.key)}
                  title={`Sort by ${col.label}`}
                  style={{ padding: '8px 10px', cursor: 'pointer', userSelect: 'none', color: active ? 'var(--text)' : 'var(--text-dim)' }}
                >
                  {col.label}{active ? (sort.dir === 'desc' ? ' ▼' : ' ▲') : ''}
                </th>
              )
            })}
            <th style={{ padding: '8px 10px' }}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const tick = liveTicks[row.symbol]
            const isOpen = openSymbols.some((s) => s.id === row.id)
            const isFocused = index === focusedIndex
            const atCapacity = !isOpen && openSymbols.length >= MAX_OPEN_CHARTS
            const status = backfillStatus[row.symbol]
            const score = scoreByInstrument[row.id]
            const action = rowActionFor(score?.bucket)
            const dotColor = score?.bucket ? (bucketColors[score.bucket] ?? BUCKET_DOT_FALLBACK) : BUCKET_DOT_FALLBACK
            return (
              <tr
                key={row.id}
                onClick={() => onToggleOpen(row)}
                onMouseEnter={() => onFocusedIndexChange(index)}
                title={atCapacity ? `Max ${MAX_OPEN_CHARTS} charts open at once` : undefined}
                style={{
                  cursor: 'pointer', borderTop: '1px solid var(--border)',
                  background: isOpen ? 'var(--accent-soft)' : isFocused ? 'var(--surface-alt)' : undefined,
                  opacity: atCapacity ? 0.55 : 1,
                }}
              >
                <td style={{ padding: '7px 10px', fontWeight: 600 }}>
                  {row.symbol}
                  {status && status.status !== 'done' && (
                    <div style={{ fontSize: 11, color: status.status === 'failed' ? 'var(--critical)' : 'var(--text-faint)', fontWeight: 400 }}>
                      {status.message}
                    </div>
                  )}
                </td>
                <td style={{ padding: '7px 10px', color: 'var(--text-dim)' }}>{row.exchange}</td>
                <td className="num" style={{ padding: '7px 10px', color: tick && DIRECTION_COLOR[tick.direction], fontWeight: 600 }}>
                  {tick ? tick.ltp.toFixed(2) : '—'}
                </td>
                <td className="num" style={{ padding: '7px 10px', color: tick && DIRECTION_COLOR[tick.direction] }}>
                  {tick ? tick.absolute_change.toFixed(2) : '—'}
                </td>
                <td className="num" style={{ padding: '7px 10px', color: tick && DIRECTION_COLOR[tick.direction] }}>
                  {tick ? `${tick.percentage_change.toFixed(2)}%` : '—'}
                </td>
                <td style={{ padding: '7px 10px', color: 'var(--text-dim)' }}>{tick ? tick.direction : '—'}</td>
                <td style={{ padding: '7px 10px' }}>{tick ? changePercentFirst(tick.gap_absolute, tick.gap_percentage) : '—'}</td>
                <td style={{ padding: '7px 10px' }}>{tick ? changePercentFirst(tick.day_change_absolute, tick.day_change_percentage) : '—'}</td>
                <td style={{ padding: '7px 10px' }}>{tick ? changePercentFirst(tick.candle_change_absolute, tick.candle_change_percentage) : '—'}</td>
                <td style={{ padding: '7px 10px' }}>
                  {/* Order as finalized in the preview: dot, action, badges + ☰, ⚙, open-in-tab, × */}
                  <div style={{ display: 'flex', flexWrap: 'nowrap', alignItems: 'center', gap: 6 }}>
                    <span
                      title={score?.bucket ? BUCKET_LABEL[score.bucket] : 'No activity yet'}
                      style={{ width: 8, height: 8, borderRadius: '50%', background: dotColor, flex: '0 0 auto' }}
                    />
                    {action && (
                      <button
                        data-row-action={action.kind}
                        title={action.kind === 'hold' ? `${row.symbol}: no active buy/sell signal` : `${action.label} ${row.symbol}`}
                        onClick={(e) => (action.kind === 'hold' ? openHold(e, row) : (e.stopPropagation(), setPopupInstrument(row)))}
                        style={{
                          border: 'none', borderRadius: 'var(--radius-sm)', padding: '4px 12px', fontWeight: 700, fontSize: 12,
                          cursor: 'pointer', ...ACTION_STYLE[action.kind],
                        }}
                      >
                        {action.label}
                      </button>
                    )}
                    <EventsCell instrumentId={row.id} compact={!!action} />
                    <WatchSelectionCell instrumentId={row.id} elements={elements} strategies={strategies} />
                    <button
                      onClick={(e) => { e.stopPropagation(); onOpenTab(row) }}
                      title={`Open ${row.symbol} in its own tab`}
                      style={ICON_BUTTON}
                    >&#10530;</button>
                    <button
                      onClick={(e) => { e.stopPropagation(); onRemove(row.id) }}
                      title={`Remove ${row.symbol} from Market Watch`}
                      aria-label={`Remove ${row.symbol}`}
                      style={{ ...ICON_BUTTON, color: 'var(--text-faint)', fontSize: 16 }}
                    >&times;</button>
                  </div>
                </td>
              </tr>
            )
          })}
          {rows.length === 0 && (
            <tr><td colSpan={COLUMNS.length + 1} style={{ textAlign: 'center', color: 'var(--text-faint)', padding: 'var(--space-4)' }}>No instruments registered yet</td></tr>
          )}
        </tbody>
      </table>
      {holdPopover && (
        <div
          role="dialog" data-hold-popover
          onMouseDown={(e) => e.stopPropagation()}
          style={{
            position: 'fixed', top: holdPopover.top, left: holdPopover.left, width: 230, zIndex: 500,
            background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)',
            boxShadow: 'var(--shadow-md)', padding: 10, fontSize: 12.5, color: 'var(--text)',
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 4 }}>{holdPopover.symbol} — Hold</div>
          <div style={{ color: 'var(--text-faint)', fontSize: 12 }}>
            No active buy/sell signal right now. Informational only — not an order action.
          </div>
        </div>
      )}
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
