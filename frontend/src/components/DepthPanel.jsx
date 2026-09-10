export default function DepthPanel({ depth }) {
  if (!depth) {
    return (
      <div style={{ minWidth: 260 }}>
        <h3>Depth</h3>
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
    ['Max bid concentration %', depth.maximum_bid_percentage],
    ['Max ask concentration %', depth.maximum_ask_percentage],
  ]

  return (
    <div style={{ minWidth: 260 }}>
      <h3>Depth — {depth.symbol}</h3>
      <table cellPadding="4">
        <tbody>
          {fields.map(([label, value]) => (
            <tr key={label}>
              <td>{label}</td>
              <td>{value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
