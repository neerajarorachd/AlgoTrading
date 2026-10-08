import { useEffect, useState } from 'react'
import { getOrderPopup } from '../api/client.js'
import { KPITileGrid } from './kit/KPITile.jsx'

// Buy/sell suggestion popup -- explicit instruction, 2026-10-04: "show LTP,
// average vol for the day and last 15 min, for single or multi candle
// patterns, should relate the intesity with the range." Display-only --
// no order placement here, that stays in the separate Trading system (the
// user's own explicit scope from the original Watch-page request).
function prettyPattern(code) {
  return code.replace(/_/g, ' ')
}

function Row({ label, children }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, padding: '3px 0' }}>
      <span style={{ color: 'var(--text-faint)' }}>{label}</span>
      <span className="num" style={{ color: 'var(--text)', fontWeight: 600, textAlign: 'right' }}>{children}</span>
    </div>
  )
}

function Section({ children, style }) {
  return (
    <div style={{ borderTop: '1px solid var(--border)', paddingTop: 10, marginTop: 10, ...style }}>
      {children}
    </div>
  )
}

function volText(value) {
  return value == null ? '—' : Math.round(value).toLocaleString('en-IN')
}

function priceSub(price, ltp) {
  const pct = price != null && ltp ? ((price - ltp) / ltp) * 100 : null
  return pct != null ? `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%` : null
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
  const directionColor = direction === 'bull' ? 'var(--good)' : direction === 'bear' ? 'var(--critical)' : 'var(--text-faint)'

  const volumePickingUp = data && data.avg_volume_day && data.avg_volume_last_15min > data.avg_volume_day

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(8, 14, 18, 0.55)', zIndex: 1000,
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)',
          padding: 'var(--space-5)', width: 380, maxHeight: '85vh', overflowY: 'auto',
          boxShadow: 'var(--shadow-md)', fontSize: 13, color: 'var(--text)',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 2 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <strong style={{ fontSize: 17, fontWeight: 700 }}>{instrument.symbol}</strong>
            {directionLabel && (
              <span style={{
                display: 'inline-block', padding: '2px 10px', borderRadius: 999, color: '#fff',
                background: directionColor, fontWeight: 700, fontSize: 11.5, letterSpacing: '0.03em',
              }}>
                {directionLabel}
              </span>
            )}
          </div>
          <button
            onClick={onClose} title="Close"
            style={{ border: 'none', background: 'transparent', color: 'var(--text-faint)', fontSize: 18, lineHeight: 1, padding: 2 }}
          >×</button>
        </div>

        {error && <div style={{ color: 'var(--critical)', marginTop: 10 }}>{error}</div>}
        {notFound && <div style={{ color: 'var(--text-faint)', marginTop: 10 }}>No directional signal for this instrument yet.</div>}
        {!error && !notFound && data === null && <div style={{ color: 'var(--text-faint)', marginTop: 10 }}>Loading…</div>}

        {data && (
          <>
            <div style={{ marginTop: 14 }}>
              <KPITileGrid>
                <div style={{ background: 'var(--surface)', padding: '10px 12px' }}>
                  <div style={{ fontSize: 10.5, color: 'var(--text-faint)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em' }}>LTP</div>
                  <div className="num" style={{ fontSize: 17, fontWeight: 650, marginTop: 3 }}>{data.ltp != null ? data.ltp.toFixed(2) : '—'}</div>
                </div>
                <div style={{ background: 'var(--surface)', padding: '10px 12px' }}>
                  <div style={{ fontSize: 10.5, color: 'var(--text-faint)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em' }}>Stop loss</div>
                  <div className="num" style={{ fontSize: 17, fontWeight: 650, marginTop: 3, color: 'var(--critical)' }}>{data.sl_price != null ? data.sl_price.toFixed(2) : '—'}</div>
                  {priceSub(data.sl_price, data.ltp) && <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 1 }}>{priceSub(data.sl_price, data.ltp)}</div>}
                </div>
                <div style={{ background: 'var(--surface)', padding: '10px 12px' }}>
                  <div style={{ fontSize: 10.5, color: 'var(--text-faint)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em' }}>Target</div>
                  <div className="num" style={{ fontSize: 17, fontWeight: 650, marginTop: 3, color: 'var(--good)' }}>{data.target_price != null ? data.target_price.toFixed(2) : '—'}</div>
                  {priceSub(data.target_price, data.ltp) && <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 1 }}>{priceSub(data.target_price, data.ltp)}</div>}
                </div>
                <div style={{ background: 'var(--surface)', padding: '10px 12px' }}>
                  <div style={{ fontSize: 10.5, color: 'var(--text-faint)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em' }}>Quantity</div>
                  <div className="num" style={{ fontSize: 17, fontWeight: 650, marginTop: 3 }}>{volText(data.suggested_quantity)}</div>
                </div>
              </KPITileGrid>
              {data.target_source && (
                <div style={{ color: 'var(--text-faint)', fontSize: 11, marginTop: -6, marginBottom: 2 }}>
                  Target via {TARGET_SOURCE_LABEL[data.target_source] ?? data.target_source}
                </div>
              )}
            </div>

            <Section>
              <Row label="Pattern">{prettyPattern(data.pattern)}{data.intensity != null ? ` · intensity ${data.intensity.toFixed(2)}` : ''}</Row>
            </Section>

            <Section>
              <Row label="Avg volume (today)">{volText(data.avg_volume_day)}</Row>
              <Row label="Avg volume (last 15 min)">{volText(data.avg_volume_last_15min)}</Row>
              {volumePickingUp && <div style={{ color: 'var(--good)', fontSize: 12, fontWeight: 600, marginTop: 4 }}>↑ volume picking up vs. today's average</div>}
            </Section>

            {data.intensity_band ? (
              <Section>
                <div style={{ color: 'var(--text-faint)', marginBottom: 3, fontSize: 12 }}>
                  Historical range (similar intensity, {data.intensity_band.count} occurrences)
                </div>
                <Row label="Favorable move" >{data.intensity_band.up_median_pct != null ? `${(data.intensity_band.up_median_pct * 100).toFixed(2)}%` : '—'}</Row>
                <Row label="Adverse move">{data.intensity_band.down_median_pct != null ? `${(data.intensity_band.down_median_pct * 100).toFixed(2)}%` : '—'}</Row>
              </Section>
            ) : (
              <Section style={{ color: 'var(--text-faint)', fontSize: 12 }}>
                No historical range available yet for this pattern/intensity.
              </Section>
            )}

            <Section style={{ color: 'var(--text-faint)', fontSize: 11 }}>
              Informational only — place orders from the Trading system.
            </Section>
          </>
        )}
      </div>
    </div>
  )
}
