import { useState } from 'react'
import { cancelExitLimit, exitTrade } from '../api/client.js'
import Button from './kit/Button.jsx'
import { liveTradePnl, price, signedMoney, toneOf, tubelight, useDismiss } from './tradeUtils.js'

// Exit panel for one open trade (rulebook 3b, user 11-11): opens on Limit,
// pre-filled with the current price (editable); Market exits now at the
// current price. Limit sets a waiting exit at the price the user picks -- a buy closes when the price reaches
// it from below, a sell from above. A waiting limit exit can be changed or
// cancelled here. Standalone: trade + live price by props.

const INPUT = {
  padding: '7px 9px', fontSize: 13.5, border: '1px solid var(--border-strong)', borderRadius: 'var(--radius-sm)',
  background: 'var(--surface)', color: 'var(--text)', width: '100%', boxSizing: 'border-box', fontFamily: 'inherit',
}

export default function ExitPanel({ trade, ltp, onClose, onDone }) {
  // Limit first, at the current price (user, 11-11): a market exit can fill at
  // a swung price; Market stays one click away
  const [type, setType] = useState('limit')
  const [limit, setLimit] = useState(
    trade.exit_limit_price != null ? String(trade.exit_limit_price) : ltp != null ? ltp.toFixed(2) : '',
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  useDismiss(onClose)

  const buy = trade.direction === 'buy'
  const live = liveTradePnl(trade, ltp)
  const limN = limit === '' ? null : Number(limit)
  const atLimit = limN != null && trade.entry_price != null
    ? (buy ? 1 : -1) * (limN - trade.entry_price) * trade.quantity : null
  // a limit already reachable fills at once at the current price
  const marketable = limN != null && ltp != null && (buy ? ltp >= limN : ltp <= limN)

  async function run(fn) {
    setBusy(true); setError(null)
    try { const t = await fn(); onDone?.(t); onClose() } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  const submit = () => run(() => (type === 'market'
    ? exitTrade(trade.id, { type: 'market' })
    : exitTrade(trade.id, { type: 'limit', limit_price: limN })))

  const seg = (value, label) => (
    <button
      type="button" data-value={value} data-on={type === value ? '1' : '0'} onClick={() => setType(value)}
      style={{
        border: 'none', padding: '6px 14px', fontSize: 12.5, fontWeight: 600, cursor: 'pointer',
        background: type === value ? 'var(--accent)' : 'var(--surface)', color: type === value ? '#fff' : 'var(--text-dim)',
      }}
    >{label}</button>
  )

  return (
    <div
      role="dialog" aria-label={`Exit ${trade.symbol}`} data-exit-panel
      onMouseDown={(e) => e.stopPropagation()}
      style={{
        position: 'fixed', top: 72, right: 16, width: 340, maxWidth: 'calc(100vw - 32px)', zIndex: 650,
        border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow-md)',
        padding: 'var(--space-4)', display: 'flex', flexDirection: 'column', gap: 14,
        ...tubelight('var(--critical-soft)', 'var(--accent-soft)'),
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <div style={{ fontSize: 17, fontWeight: 700 }}>
            Exit {trade.symbol} <span style={{ color: buy ? 'var(--good)' : 'var(--critical)' }}>{buy ? 'BUY' : 'SELL'}</span>
          </div>
          <div className="num" style={{ fontSize: 12.5, color: 'var(--text-dim)' }}>
            {trade.quantity} @ {price(trade.entry_price)} · {trade.product}
          </div>
        </div>
        <button type="button" onClick={onClose} aria-label="Close" style={{ border: 'none', background: 'transparent', fontSize: 18, cursor: 'pointer', color: 'var(--text-faint)' }}>×</button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1, background: 'var(--border)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', overflow: 'hidden' }}>
        <div style={{ background: 'var(--surface)', padding: '8px 10px' }}>
          <div style={{ fontSize: 11.5, color: 'var(--text-faint)', fontWeight: 600 }}>Current price</div>
          <div className="num" data-testid="exit-ltp" style={{ fontSize: 18, fontWeight: 700 }}>{price(ltp)}</div>
        </div>
        <div style={{ background: 'var(--surface)', padding: '8px 10px' }}>
          <div style={{ fontSize: 11.5, color: 'var(--text-faint)', fontWeight: 600 }}>P&L at this price</div>
          <div className="num" style={{ fontSize: 18, fontWeight: 700, color: toneOf(live?.gross) }}>{live ? signedMoney(live.gross) : '—'}</div>
          {live && <div className="num" style={{ fontSize: 11.5, color: 'var(--text-dim)' }}>≈ {signedMoney(live.net)} after exp.</div>}
        </div>
      </div>

      <div data-testid="exit-type" style={{ display: 'inline-flex', alignSelf: 'flex-start', border: '1px solid var(--border-strong)', borderRadius: 'var(--radius-sm)', overflow: 'hidden' }}>
        {seg('market', 'Market')}
        {seg('limit', 'Limit')}
      </div>

      {type === 'limit' && (
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text-dim)', fontWeight: 600 }}>
          <span style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 6 }}>
            <span>
              Limit price
              {ltp != null && (
                <button
                  type="button" data-testid="exit-limit-reset" title="Set the limit to the current price"
                  onClick={(e) => { e.preventDefault(); setLimit(ltp.toFixed(2)) }}
                  style={{ marginLeft: 8, border: 'none', background: 'transparent', padding: 0, cursor: 'pointer', color: 'var(--accent-text)', fontSize: 11.5, fontWeight: 600 }}
                >↻ current price</button>
              )}
            </span>
            {atLimit != null && <span className="num" style={{ fontWeight: 500, color: toneOf(atLimit) }}>{signedMoney(atLimit)} before exp.</span>}
          </span>
          <input data-testid="exit-limit" type="number" step="0.05" value={limit} onChange={(e) => setLimit(e.target.value)} style={INPUT} />
          <span style={{ fontWeight: 500, color: 'var(--text-faint)' }}>
            {marketable
              ? 'Already reached: fills now at the current price.'
              : `Closes when the price reaches it (${buy ? 'at or above' : 'at or below'}).`}
          </span>
        </label>
      )}

      {trade.exit_limit_price != null && (
        <div data-testid="exit-waiting" style={{ fontSize: 12.5, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, padding: '8px 10px', borderRadius: 'var(--radius-sm)', background: 'var(--warning-soft)' }}>
          <span>Limit exit waiting at <b className="num">{price(trade.exit_limit_price)}</b></span>
          <Button variant="secondary" data-testid="exit-cancel-limit" disabled={busy} onClick={() => run(() => cancelExitLimit(trade.id))}
            style={{ padding: '4px 10px', fontSize: 12 }}>Cancel it</Button>
        </div>
      )}

      {error && <div data-testid="exit-error" style={{ fontSize: 12.5, color: 'var(--critical)' }}>{error}</div>}

      <Button
        data-testid="exit-submit" disabled={busy || (type === 'market' ? ltp == null : !(limN > 0))} onClick={submit}
        style={{ background: 'var(--critical)', color: '#fff', padding: '10px 14px', fontSize: 14 }}
      >
        {busy ? 'Working…' : type === 'market'
          ? `Exit at market${ltp != null ? ` (~${price(ltp)})` : ''}`
          : marketable ? `Exit now at ${price(ltp)}` : `${trade.exit_limit_price != null ? 'Change' : 'Place'} limit exit at ${price(limN)}`}
      </Button>
    </div>
  )
}
