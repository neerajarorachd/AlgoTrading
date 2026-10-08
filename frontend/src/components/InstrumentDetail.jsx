import { useRef, useState } from 'react'
import GraphWindow from './GraphWindow.jsx'
import OrderPopup from './OrderPopup.jsx'
import { DIRECTION_COLOR } from './symbolCells.jsx'

function ParamsRow({ tick }) {
  const fields = [
    ['LTP', tick ? tick.ltp.toFixed(2) : '—'],
    ['Gap %', tick?.gap_percentage != null ? `${tick.gap_percentage.toFixed(2)}%` : '—'],
    ['Day %', tick?.day_change_percentage != null ? `${tick.day_change_percentage.toFixed(2)}%` : '—'],
    ['Candle %', tick?.candle_change_percentage != null ? `${tick.candle_change_percentage.toFixed(2)}%` : '—'],
  ]
  return (
    <div style={{ display: 'flex', gap: 'var(--space-5)', marginBottom: 'var(--space-3)', flexWrap: 'wrap' }}>
      {fields.map(([label, value]) => (
        <div key={label}>
          <div style={{ fontSize: 11, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.03em' }}>{label}</div>
          <div className="num" style={{ fontSize: 16, fontWeight: 700, color: tick && DIRECTION_COLOR[tick.direction] }}>{value}</div>
        </div>
      ))}
    </div>
  )
}

// Full single-instrument view -- used both inline as a Market Watch tab and
// standalone at /instrument/:exchange/:symbol (for "open in new tab", via
// InstrumentDetailPage.jsx). Deliberately has no Fundamentals section: no
// real backend data source exists for it yet (see
// market_watch_grid_redesign_plan.md's effort estimate for what that would
// take) -- shipping a fabricated one here would be worse than shipping none.
export default function InstrumentDetail({ instrument, tick, depth, score }) {
  const [showOrderPopup, setShowOrderPopup] = useState(false)
  const graphRef = useRef(null)

  const direction = score && (score.bull_score > score.bear_score ? 'bull'
    : score.bear_score > score.bull_score ? 'bear' : null)

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 'var(--space-3)' }}>
        <h2 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: 'var(--text)' }}>{instrument.symbol}</h2>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {direction && (
            <button
              onClick={() => setShowOrderPopup(true)}
              style={{
                color: '#fff', border: 'none', borderRadius: 'var(--radius-sm)', padding: '6px 16px',
                background: direction === 'bull' ? 'var(--good)' : 'var(--critical)', fontWeight: 700, fontSize: 13,
              }}
            >
              {direction === 'bull' ? 'Buy' : 'Sell'}
            </button>
          )}
          <button
            onClick={() => graphRef.current?.requestFullscreen()} title="Fullscreen"
            style={{ background: 'transparent', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', padding: '5px 10px', color: 'var(--text-dim)', fontSize: 14 }}
          >⛶</button>
          <button
            onClick={() => window.open(`/instrument/${instrument.exchange}/${instrument.symbol}`, '_blank')}
            title="Open in a new browser tab"
            style={{ background: 'transparent', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', padding: '5px 10px', color: 'var(--text-dim)', fontSize: 14 }}
          >⧉</button>
        </div>
      </div>
      <ParamsRow tick={tick} />
      <GraphWindow ref={graphRef} instrument={instrument} depth={depth} />
      {showOrderPopup && (
        <OrderPopup instrument={instrument} ltp={tick?.ltp} onClose={() => setShowOrderPopup(false)} />
      )}
    </div>
  )
}
