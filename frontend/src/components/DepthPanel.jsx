// Field sizing/coloring matches the reference depth-ladder widget's own
// conventions (13px data rows, small uppercase-ish gray labels, a green/red
// bid-vs-offer pressure bar) -- we don't get a full 5-level order-book
// ladder from the backend (calculate_depth_metrics sends summary stats,
// not raw per-level rows), so this renders those summary fields in that
// same visual language rather than fabricating ladder rows we don't have.
export default function DepthPanel({ depth }) {
  if (!depth) {
    return (
      <div style={{ fontSize: 13, color: 'var(--text-faint)' }}>
        No depth data yet.
      </div>
    )
  }

  const fields = [
    ['Best bid', depth.best_bid],
    ['Best ask', depth.best_ask],
    ['Spread %', depth.spread_percentage],
    ['Nearest bid %', depth.nearest_bid_percentage],
    ['Nearest ask %', depth.nearest_ask_percentage],
    ['Max bid conc. %', depth.maximum_bid_percentage],
    ['Max ask conc. %', depth.maximum_ask_percentage],
  ]

  const bidPct = depth.bid_pressure_percentage
  const askPct = depth.ask_pressure_percentage

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {fields.map(([label, value]) => (
        <div key={label} style={{ display: 'flex', justifyContent: 'space-between', gap: 10, fontSize: 12.5 }}>
          <span style={{ color: 'var(--text-faint)' }}>{label}</span>
          <span className="num" style={{ color: 'var(--text)', fontWeight: 600, fontSize: 13 }}>{value ?? '—'}</span>
        </div>
      ))}
      {bidPct != null && askPct != null && (
        <div style={{ marginTop: 4 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
            <span style={{ color: 'var(--up)', fontWeight: 600 }}>Bids: {bidPct.toFixed(2)}%</span>
            <span style={{ color: 'var(--down)', fontWeight: 600 }}>Offers: {askPct.toFixed(2)}%</span>
          </div>
          <div style={{ display: 'flex', height: 7, borderRadius: 3, overflow: 'hidden', background: 'var(--border)' }}>
            <div style={{ width: `${bidPct}%`, background: 'var(--up)' }} />
            <div style={{ width: `${askPct}%`, background: 'var(--down)' }} />
          </div>
        </div>
      )}
    </div>
  )
}
