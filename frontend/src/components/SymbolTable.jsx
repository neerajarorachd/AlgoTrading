import { useEffect, useRef, useState } from 'react'
import OrderPopup from './OrderPopup.jsx'
import { DIRECTION_COLOR, EventsCell, WatchSelectionCell, changeCell } from './symbolCells.jsx'

const VISIBLE_ROWS = 5
const ROW_HEIGHT_PX = 33
const MAX_OPEN_CHARTS = 4

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
        maxHeight: (VISIBLE_ROWS + 1) * ROW_HEIGHT_PX, overflowY: 'auto', background: 'var(--surface)',
        border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', outline: 'none',
      }}
    >
      <table>
        <thead style={{ position: 'sticky', top: 0, background: 'var(--surface-alt)', boxShadow: '0 1px 0 var(--border)' }}>
          <tr>
            {['Symbol', 'Exchange', 'LTP', 'Chg', 'Chg %', 'Dir', 'Gap', 'Day', 'Candle', 'Events', 'Watch', 'Trade', '', ''].map((h, i) => (
              <th key={h + i} style={{ padding: '8px 10px' }}>{h}</th>
            ))}
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
                <td style={{ padding: '7px 10px' }}>{tick ? changeCell(tick.gap_absolute, tick.gap_percentage) : '—'}</td>
                <td style={{ padding: '7px 10px' }}>{tick ? changeCell(tick.day_change_absolute, tick.day_change_percentage) : '—'}</td>
                <td style={{ padding: '7px 10px' }}>{tick ? changeCell(tick.candle_change_absolute, tick.candle_change_percentage) : '—'}</td>
                <td style={{ padding: '7px 10px' }}><EventsCell instrumentId={row.id} /></td>
                <td style={{ padding: '7px 10px' }}><WatchSelectionCell instrumentId={row.id} elements={elements} strategies={strategies} /></td>
                <td style={{ padding: '7px 10px' }}>
                  {(() => {
                    const score = scoreByInstrument[row.id]
                    const direction = score && (score.bull_score > score.bear_score ? 'bull'
                      : score.bear_score > score.bull_score ? 'bear' : null)
                    if (!direction) return <span style={{ color: 'var(--text-faint)' }}>—</span>
                    return (
                      <button
                        onClick={(e) => { e.stopPropagation(); setPopupInstrument(row) }}
                        style={{
                          color: '#fff', border: 'none', borderRadius: 'var(--radius-sm)', padding: '4px 12px',
                          background: direction === 'bull' ? 'var(--good)' : 'var(--critical)', fontWeight: 700, fontSize: 12,
                        }}
                      >
                        {direction === 'bull' ? 'Buy' : 'Sell'}
                      </button>
                    )
                  })()}
                </td>
                <td style={{ padding: '7px 10px' }}>
                  <button
                    onClick={(e) => { e.stopPropagation(); onToggleOpen(row) }}
                    title={`Open ${row.symbol}'s chart (several can be open at once)`}
                    style={{ background: 'transparent', border: 'none', color: 'var(--text-dim)', fontSize: 14 }}
                  >&#10530;</button>
                </td>
                <td style={{ padding: '7px 10px' }}>
                  <button
                    onClick={(e) => { e.stopPropagation(); onRemove(row.id) }}
                    style={{ background: 'transparent', border: 'none', color: 'var(--text-faint)', fontSize: 12, fontWeight: 600 }}
                  >Remove</button>
                </td>
              </tr>
            )
          })}
          {symbols.length === 0 && (
            <tr><td colSpan={14} style={{ textAlign: 'center', color: 'var(--text-faint)', padding: 'var(--space-4)' }}>No instruments registered yet</td></tr>
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
