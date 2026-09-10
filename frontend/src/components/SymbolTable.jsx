const DIRECTION_COLOR = { up: 'green', down: 'crimson', flat: undefined }

export default function SymbolTable({ symbols, liveTicks, selected, onSelect, onRemove }) {
  return (
    <table border="1" cellPadding="6" style={{ borderCollapse: 'collapse', width: '100%' }}>
      <thead>
        <tr>
          <th>Symbol</th><th>Exchange</th><th>LTP</th><th>Chg</th><th>Chg %</th><th>Dir</th><th></th>
        </tr>
      </thead>
      <tbody>
        {symbols.map((row) => {
          const tick = liveTicks[row.symbol]
          const isSelected = selected?.id === row.id
          return (
            <tr
              key={row.id}
              onClick={() => onSelect(row)}
              style={{ cursor: 'pointer', background: isSelected ? '#eef' : undefined }}
            >
              <td>{row.symbol}</td>
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
              <td>
                <button onClick={(e) => { e.stopPropagation(); onRemove(row.id) }}>Remove</button>
              </td>
            </tr>
          )
        })}
        {symbols.length === 0 && (
          <tr><td colSpan={7} style={{ textAlign: 'center', color: '#888' }}>No instruments registered yet</td></tr>
        )}
      </tbody>
    </table>
  )
}
