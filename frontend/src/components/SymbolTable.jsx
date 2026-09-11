import { useEffect, useRef } from 'react'

const DIRECTION_COLOR = { up: 'green', down: 'crimson', flat: undefined }
const VISIBLE_ROWS = 7
const ROW_HEIGHT_PX = 33
const MAX_OPEN_CHARTS = 4

function changeCell(absolute, percentage) {
  if (absolute == null) return '—'
  const color = absolute > 0 ? 'green' : absolute < 0 ? 'crimson' : undefined
  const pct = percentage != null ? ` (${percentage.toFixed(2)}%)` : ''
  return <span style={{ color }}>{absolute.toFixed(2)}{pct}</span>
}

export default function SymbolTable({
  symbols, liveTicks, backfillStatus, openSymbols, onToggleOpen, onRemove,
  focusedIndex, onFocusedIndexChange, checkedIds, onToggleChecked, onToggleCheckAll,
}) {
  const containerRef = useRef(null)
  const headerCheckboxRef = useRef(null)

  useEffect(() => {
    if (focusedIndex < 0 || !containerRef.current) return
    const row = containerRef.current.querySelectorAll('tbody tr')[focusedIndex]
    row?.scrollIntoView({ block: 'nearest' })
  }, [focusedIndex])

  const allChecked = symbols.length > 0 && symbols.every((s) => checkedIds.has(s.id))
  const someChecked = symbols.some((s) => checkedIds.has(s.id))

  useEffect(() => {
    if (headerCheckboxRef.current) {
      headerCheckboxRef.current.indeterminate = someChecked && !allChecked
    }
  }, [someChecked, allChecked])

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
            <th>
              <input
                ref={headerCheckboxRef}
                type="checkbox"
                checked={allChecked}
                onChange={onToggleCheckAll}
                title="Select all"
              />
            </th>
            <th>Symbol</th><th>Exchange</th><th>LTP</th><th>Chg</th><th>Chg %</th><th>Dir</th>
            <th>Gap</th><th>Day</th><th>Candle</th><th></th>
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
                  <input
                    type="checkbox"
                    checked={checkedIds.has(row.id)}
                    onChange={() => onToggleChecked(row.id)}
                    onClick={(e) => e.stopPropagation()}
                  />
                </td>
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
                <td>
                  <button onClick={(e) => { e.stopPropagation(); onRemove(row.id) }}>Remove</button>
                </td>
              </tr>
            )
          })}
          {symbols.length === 0 && (
            <tr><td colSpan={11} style={{ textAlign: 'center', color: '#888' }}>No instruments registered yet</td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
