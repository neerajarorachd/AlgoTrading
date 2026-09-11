export default function DepthPanel({ depth }) {
  if (!depth) {
    return (
      <div style={{ minWidth: 200, fontSize: 13 }}>
        <p style={{ color: '#888' }}>No depth data yet.</p>
      </div>
    )
  }

  const fields = [
    ['Best bid', depth.best_bid],
    ['Best ask', depth.best_ask],
    ['Spread %', depth.spread_percentage],
    ['Bid pressure %', depth.bid_pressure_percentage],
    ['Ask pressure %', depth.ask_pressure_percentage],
    ['Nearest bid %', depth.nearest_bid_percentage],
    ['Nearest ask %', depth.nearest_ask_percentage],
    ['Max bid conc. %', depth.maximum_bid_percentage],
    ['Max ask conc. %', depth.maximum_ask_percentage],
  ]

  return (
    <div style={{ minWidth: 200, fontSize: 13 }}>
      <table cellPadding="3">
        <tbody>
          {fields.map(([label, value]) => (
            <tr key={label}>
              <td style={{ color: '#666' }}>{label}</td>
              <td>{value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
