import { useEffect, useState } from 'react'
import { getOrderPopup } from '../api/client.js'

// Buy/sell suggestion popup -- explicit instruction, 2026-10-04: "show LTP,
// average vol for the day and last 15 min, for single or multi candle
// patterns, should relate the intesity with the range." Display-only --
// no order placement here, that stays in the separate Trading system (the
// user's own explicit scope from the original Watch-page request).
function prettyPattern(code) {
  return code.replace(/_/g, ' ')
}

function volRow(label, value) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
      <span style={{ color: '#666' }}>{label}</span>
      <span>{value == null ? '—' : Math.round(value).toLocaleString('en-IN')}</span>
    </div>
  )
}

function priceRow(label, price, ltp) {
  const pct = price != null && ltp ? ((price - ltp) / ltp) * 100 : null
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
      <span style={{ color: '#666' }}>{label}</span>
      <span>{price != null ? `${price.toFixed(2)}${pct != null ? ` (${pct > 0 ? '+' : ''}${pct.toFixed(2)}%)` : ''}` : '—'}</span>
    </div>
  )
}

const TARGET_SOURCE_LABEL = {
  neckline: 'formation measured-move', geometric: 'chart level',
  backtested: 'historical median move', atr_fallback: 'ATR estimate',
}

export default function OrderPopup({ instrument, ltp, onClose }) {
  const [data, setData] = useState(null) // undefined-ish states: null = loading
  const [notFound, setNotFound] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    getOrderPopup(instrument.id, '3min', ltp)
      .then(setData)
      .catch((err) => (err.message?.includes('no directional signal') ? setNotFound(true) : setError(err.message)))
  }, [instrument.id, ltp])

  const direction = data?.direction
  const directionLabel = direction === 'bull' ? 'BUY' : direction === 'bear' ? 'SELL' : null
  const directionColor = direction === 'bull' ? '#1b7a3d' : direction === 'bear' ? '#b3261e' : '#888'

  const volumePickingUp = data && data.avg_volume_day && data.avg_volume_last_15min > data.avg_volume_day

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.35)', zIndex: 1000,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: 'white', borderRadius: 8, padding: 20, width: 340,
          boxShadow: '0 4px 20px rgba(0,0,0,0.25)', fontSize: 13,
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10 }}>
          <strong style={{ fontSize: 16 }}>{instrument.symbol}</strong>
          <button onClick={onClose} title="Close" style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 16 }}>×</button>
        </div>

        {error && <div style={{ color: 'crimson' }}>{error}</div>}
        {notFound && <div style={{ color: '#888' }}>No directional signal for this instrument yet.</div>}

        {!error && !notFound && data === null && <div style={{ color: '#888' }}>Loading…</div>}

        {data && (
          <>
            {directionLabel && (
              <div style={{
                display: 'inline-block', padding: '3px 10px', borderRadius: 4, color: '#fff',
                background: directionColor, fontWeight: 700, marginBottom: 10,
              }}>
                {directionLabel}
              </div>
            )}

            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: 10 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: '#666' }}>LTP</span>
                <span>{data.ltp != null ? data.ltp.toFixed(2) : '—'}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: '#666' }}>Pattern</span>
                <span>{prettyPattern(data.pattern)}{data.intensity != null ? ` (intensity ${data.intensity.toFixed(2)})` : ''}</span>
              </div>
            </div>

            <div style={{ borderTop: '1px solid #eee', paddingTop: 8, marginBottom: 10 }}>
              {priceRow('Suggested SL', data.sl_price, data.ltp)}
              {priceRow('Suggested target', data.target_price, data.ltp)}
              {data.target_source && (
                <div style={{ color: '#888', fontSize: 11 }}>via {TARGET_SOURCE_LABEL[data.target_source] ?? data.target_source}</div>
              )}
              {volRow('Suggested quantity', data.suggested_quantity)}
            </div>

            <div style={{ borderTop: '1px solid #eee', paddingTop: 8, marginBottom: 10 }}>
              {volRow('Avg volume (today)', data.avg_volume_day)}
              {volRow('Avg volume (last 15 min)', data.avg_volume_last_15min)}
              {volumePickingUp && <div style={{ color: '#1b7a3d', fontSize: 12 }}>↑ volume picking up vs. today's average</div>}
            </div>

            {data.intensity_band ? (
              <div style={{ borderTop: '1px solid #eee', paddingTop: 8, marginBottom: 10 }}>
                <div style={{ color: '#666', marginBottom: 2 }}>
                  Historical range (similar intensity, {data.intensity_band.count} occurrences)
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span>Favorable move</span>
                  <span>{data.intensity_band.up_median_pct != null ? `${(data.intensity_band.up_median_pct * 100).toFixed(2)}%` : '—'}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span>Adverse move</span>
                  <span>{data.intensity_band.down_median_pct != null ? `${(data.intensity_band.down_median_pct * 100).toFixed(2)}%` : '—'}</span>
                </div>
              </div>
            ) : (
              <div style={{ color: '#888', fontSize: 12, marginBottom: 10 }}>
                No historical range available yet for this pattern/intensity.
              </div>
            )}

            <div style={{ color: '#888', fontSize: 11, borderTop: '1px solid #eee', paddingTop: 8 }}>
              Informational only — place orders from the Trading system.
            </div>
          </>
        )}
      </div>
    </div>
  )
}
