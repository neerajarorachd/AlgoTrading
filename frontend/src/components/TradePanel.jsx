import { useEffect, useMemo, useRef, useState } from 'react'
import { getTradeAccount, placeTrade } from '../api/client.js'
import { usePreference } from '../preferences.js'
import Button from './kit/Button.jsx'
import { DEFAULT_STOP_PCT, DEFAULT_TARGET_PCT, LEVEL_MODES, levelAt, levelFrom, money, price, tubelight, useDismiss } from './tradeUtils.js'

// Order panel for a manual trade (rulebook 3b), opened from a grid row's B / S
// (later also the chart and a stand-alone Buy/Sell ticket -- `source`).
// Standalone: everything comes in by props, the order goes to /api/trades.
// Live is a switch that does nothing yet: it shows "coming soon" and the
// order button stays off. Product / entry type are remembered per user.

const isProduct = (v) => v === 'intraday' || v === 'delivery'
const isEntryType = (v) => v === 'market' || v === 'limit'
const isLevelMode = (v) => LEVEL_MODES.some((m) => m.value === v)
const trim = (x, d) => String(Number(x.toFixed(d)))

function Segmented({ options, value, onChange, testid, fill = false }) {
  return (
    <div data-testid={testid} style={{ display: fill ? 'flex' : 'inline-flex', width: fill ? '100%' : undefined, border: '1px solid var(--border-strong)', borderRadius: 'var(--radius-sm)', overflow: 'hidden' }}>
      {options.map((o) => {
        const on = o.value === value
        return (
          <button
            key={o.value} type="button" disabled={o.disabled} title={o.title}
            data-value={o.value} data-on={on ? '1' : '0'}
            onClick={() => onChange(o.value)}
            style={{
              border: 'none', padding: '6px 12px', fontSize: 12.5, fontWeight: 600, cursor: o.disabled ? 'not-allowed' : 'pointer',
              flex: fill ? '1 1 0' : undefined,
              background: on ? (o.color ?? 'var(--accent)') : 'var(--surface)',
              color: on ? '#fff' : o.disabled ? 'var(--text-faint)' : 'var(--text-dim)',
            }}
          >{o.label}</button>
        )
      })}
    </div>
  )
}

function Field({ label, hint, children }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text-dim)', fontWeight: 600 }}>
      <span style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
        {label}
        {hint && <span className="num" style={{ fontWeight: 500, color: 'var(--text-faint)' }}>{hint}</span>}
      </span>
      {children}
    </label>
  )
}

const INPUT = {
  padding: '7px 9px', fontSize: 13.5, border: '1px solid var(--border-strong)', borderRadius: 'var(--radius-sm)',
  background: 'var(--surface)', color: 'var(--text)', width: '100%', boxSizing: 'border-box', fontFamily: 'inherit',
}

const num = (v) => (v === '' || v == null ? null : Number(v))

// under a stop / target box: the price it works out to (% / ₹ diff modes) or
// its % from the entry (price mode); data-price carries the leg price
function LegHint({ testid, mode, p, pct, side }) {
  const ok = p != null && !Number.isNaN(p)
  const color = side === 'stop' ? 'var(--critical)' : 'var(--good)'
  return (
    <span data-testid={testid} data-price={ok ? p.toFixed(2) : ''} className="num" style={{ minHeight: 18 }}>
      {!ok ? ' ' : mode === 'price'
        ? <span style={{ fontSize: 12.5, fontWeight: 700, color }}>{pct ?? ' '}</span>
        : (
          <>
            <span style={{ fontSize: 14, fontWeight: 800, color }}>{p.toFixed(2)}</span>
            {pct && <span style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--text-dim)', marginLeft: 6 }}>{pct}</span>}
          </>
        )}
    </span>
  )
}

export default function TradePanel({ instrument, direction: initialDirection = 'buy', ltp, screenSignal, source = 'grid', onClose, onPlaced }) {
  const [direction, setDirection] = useState(initialDirection)
  const [mode, setMode] = useState('paper')
  const [product, setProduct] = usePreference('trade.panel.product', 'intraday', isProduct)
  const [entryType, setEntryType] = usePreference('trade.panel.entryType', 'market', isEntryType)
  const [quantity, setQuantity] = useState('1')
  const [limitPrice, setLimitPrice] = useState(ltp != null ? ltp.toFixed(2) : '')
  // stop / target boxes, in the units of levelMode (user, 11-11):
  //   pct   -> % of the entry price      } the levels follow the entry: the live
  //   diff  -> rupees from the entry     } price (Market) or the limit price
  //   price -> fixed prices: pre-filled DEFAULT_STOP_PCT / DEFAULT_TARGET_PCT
  //            from the entry until the user types in that box, then fixed
  // The limit price stays at the price when B / S was clicked; the live price
  // is shown but never moves it.
  const [levelMode, setLevelMode] = usePreference('trade.panel.levelMode', 'pct', isLevelMode)
  const [stop, setStop] = useState(levelMode === 'pct' ? String(DEFAULT_STOP_PCT) : '')
  const [target, setTarget] = useState(levelMode === 'pct' ? String(DEFAULT_TARGET_PCT) : '')
  const edited = useRef({ stop: false, target: false }) // price mode only
  // each mode's last values in this panel: back to % shows the % you had (user, 11-11)
  const modeVals = useRef({})
  const clickLtp = useRef(ltp) // the price when B / S was clicked
  if (clickLtp.current == null && ltp != null) clickLtp.current = ltp
  // the live price's last move, for its colour
  const prevLtp = useRef(ltp)
  const [tickDir, setTickDir] = useState(null)
  useEffect(() => {
    if (ltp != null && prevLtp.current != null && ltp !== prevLtp.current) setTickDir(ltp > prevLtp.current ? 'up' : 'down')
    prevLtp.current = ltp
  }, [ltp])
  const [note, setNote] = useState('')
  const [account, setAccount] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => { setDirection(initialDirection) }, [initialDirection, instrument?.id])
  useEffect(() => {
    getTradeAccount('paper').then(setAccount).catch(() => setAccount(null))
  }, [instrument?.id])
  useDismiss(onClose)

  const buy = direction === 'buy'
  const basis = entryType === 'limit' ? num(limitPrice) : ltp
  // switching to Limit with no price yet: the price when B / S was clicked
  // (also when the panel opened on Limit before the first live price arrived)
  const haveClickPrice = clickLtp.current != null
  useEffect(() => {
    if (entryType === 'limit' && limitPrice === '' && clickLtp.current != null) setLimitPrice(clickLtp.current.toFixed(2))
  }, [entryType, haveClickPrice]) // eslint-disable-line react-hooks/exhaustive-deps
  // the defaults in the current mode's units
  function defaultsFor(m, at) {
    if (m === 'pct') return [String(DEFAULT_STOP_PCT), String(DEFAULT_TARGET_PCT)]
    if (at == null) return ['', '']
    if (m === 'diff') return [trim((at * DEFAULT_STOP_PCT) / 100, 2), trim((at * DEFAULT_TARGET_PCT) / 100, 2)]
    return [levelFrom(at, direction, DEFAULT_STOP_PCT, 'stop'), levelFrom(at, direction, DEFAULT_TARGET_PCT, 'target')]
  }
  function applyDefaults(at, force = false) {
    const [s0, t0] = defaultsFor(levelMode, at)
    if (force || !edited.current.stop) setStop(s0)
    if (force || !edited.current.target) setTarget(t0)
    if (force) edited.current = { stop: false, target: false }
  }
  // price mode: untyped boxes refill on open, on Buy/Sell switch and as the
  // entry moves (Market: every tick; Limit: when the limit price is changed).
  // pct / diff modes need nothing: their levels are worked out from the entry.
  useEffect(() => { if (levelMode === 'price') applyDefaults(basis) }, [direction, entryType, basis, levelMode]) // eslint-disable-line react-hooks/exhaustive-deps
  // diff mode opened before a price was known: fill it once the price arrives
  useEffect(() => { if (levelMode === 'diff' && stop === '' && target === '' && basis != null) applyDefaults(basis) }, [basis]) // eslint-disable-line react-hooks/exhaustive-deps
  // the leg prices sent with the order
  function legPrice(input, side) {
    const v = num(input)
    if (v == null) return null
    if (levelMode === 'price') return v
    if (!(v > 0)) return NaN
    const p = levelAt(basis, direction, levelMode === 'pct' ? ((basis ?? 0) * v) / 100 : v, side)
    return p === '' ? null : Number(p)
  }
  // switching mode converts the current levels into the new units
  function switchLevelMode(m) {
    if (m === levelMode) return
    const conv = (p) => {
      if (p == null || Number.isNaN(p) || basis == null) return ''
      if (m === 'price') return p.toFixed(2)
      const d = Math.abs(p - basis)
      return m === 'pct' ? trim((d / basis) * 100, 3) : trim(d, 2)
    }
    modeVals.current[levelMode] = { stop, target, edited: { ...edited.current } }
    const kept = modeVals.current[m]
    if (kept) {
      setStop(kept.stop)
      setTarget(kept.target)
      edited.current = { ...kept.edited }
    } else {
      setStop(conv(legPrice(stop, 'stop')))
      setTarget(conv(legPrice(target, 'target')))
      edited.current = { stop: m === 'price', target: m === 'price' }
    }
    setLevelMode(m)
  }
  // a sell-first trade must be intraday
  const effectiveProduct = buy ? product : 'intraday'
  const ref = entryType === 'limit' ? num(limitPrice) : ltp
  const qty = Math.max(0, Math.floor(Number(quantity) || 0))
  const value = ref != null ? ref * qty : null
  const stopN = legPrice(stop, 'stop')
  const targetN = legPrice(target, 'target')
  // each exit leg beyond the first blocks the value again (stop + target = 2x)
  const legs = (stopN != null ? 1 : 0) + (targetN != null ? 1 : 0)
  const needed = value != null ? value * (1 + Math.max(0, legs - 1)) : null
  const pctFrom = (p) => (p != null && ref ? `${(((p - ref) / ref) * 100).toFixed(2)}%` : null)
  const risk = stopN != null && ref != null ? Math.abs(ref - stopN) * qty : null
  const reward = targetN != null && ref != null ? Math.abs(targetN - ref) * qty : null

  const problem = useMemo(() => {
    if (mode === 'live') return 'Live trading coming soon'
    if (qty < 1) return 'Enter a quantity of at least 1'
    if (entryType === 'limit' && !(num(limitPrice) > 0)) return 'Enter a limit price'
    if (ref == null) return 'Waiting for a live price'
    if (Number.isNaN(stopN) || Number.isNaN(targetN)) return 'Stop / target distance must be above 0'
    if (stopN != null && (buy ? stopN >= ref : stopN <= ref)) return `Stop must be ${buy ? 'below' : 'above'} the entry price`
    if (targetN != null && (buy ? targetN <= ref : targetN >= ref)) return `Target must be ${buy ? 'above' : 'below'} the entry price`
    if (account && needed != null && needed > account.free) return `Not enough paper capital: needs ${money(needed, true)}${legs > 1 ? ' (2× for stop + target legs)' : ''}, free ${money(account.free, true)}`
    return null
  }, [mode, qty, entryType, limitPrice, ref, stopN, targetN, buy, account, needed, legs])

  async function submit() {
    if (problem || busy) return
    setBusy(true)
    setError(null)
    try {
      const trade = await placeTrade({
        instrument_id: instrument.id, direction, quantity: qty, product: effectiveProduct, entry_type: entryType,
        limit_price: entryType === 'limit' ? num(limitPrice) : null, stop_price: stopN, target_price: targetN,
        mode, source, note: note.trim() || null, screen_signal: screenSignal ?? null,
      })
      onPlaced?.(trade)
      onClose()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const sideColor = buy ? 'var(--good)' : 'var(--critical)'
  return (
    <div
      role="dialog" aria-label={`${buy ? 'Buy' : 'Sell'} ${instrument.symbol}`} data-trade-panel
      onMouseDown={(e) => e.stopPropagation()}
      style={{
        position: 'fixed', top: 'min(72px, 6vh)', right: 16, width: 360, maxWidth: 'calc(100vw - 32px)',
        maxHeight: 'calc(100vh - min(72px, 6vh) - 16px)', boxSizing: 'border-box',
        zIndex: 600, border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)',
        boxShadow: 'var(--shadow-md)', padding: 'var(--space-4)', display: 'flex', flexDirection: 'column', gap: 12,
        ...tubelight(buy ? 'var(--good-soft)' : 'var(--critical-soft)', 'var(--accent-soft)'),
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
        <div>
          <div style={{ fontSize: 17, fontWeight: 700, color: 'var(--text)' }}>{instrument.symbol}</div>
          <div className="num" style={{ fontSize: 12.5, color: 'var(--text-dim)' }}>
            <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '0.06em', color: 'var(--text-faint)' }}>LIVE</span>{' '}
            <b data-testid="trade-ltp" data-tick={tickDir ?? 'none'} style={{
              fontSize: 16, fontWeight: 800,
              color: tickDir === 'up' ? 'var(--good)' : tickDir === 'down' ? 'var(--critical)' : 'var(--accent-text)',
            }}>{price(ltp)}</b>
            {entryType === 'limit' && clickLtp.current != null && (
              <span style={{ marginLeft: 8, color: 'var(--text-faint)' }}>at click {price(clickLtp.current)}</span>
            )}
            {screenSignal && <span style={{ marginLeft: 8 }}>· screen says {screenSignal}</span>}
          </div>
        </div>
        <button type="button" onClick={onClose} aria-label="Close" style={{ border: 'none', background: 'transparent', fontSize: 18, cursor: 'pointer', color: 'var(--text-faint)' }}>×</button>
      </div>

      <div data-trade-panel-body style={{ flex: '1 1 auto', minHeight: 0, overflowY: 'auto', display: 'grid', gridTemplateColumns: 'minmax(0, 1fr)', gridAutoRows: 'max-content', alignContent: 'start', gap: 12, margin: '0 -6px', padding: '0 6px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
        <Segmented
          testid="trade-direction" value={direction} onChange={setDirection}
          options={[{ value: 'buy', label: 'Buy', color: 'var(--good)' }, { value: 'sell', label: 'Sell', color: 'var(--critical)' }]}
        />
        <Segmented
          testid="trade-mode" value={mode} onChange={setMode}
          options={[{ value: 'paper', label: 'Paper' }, { value: 'live', label: 'Live', title: 'Live trading coming soon' }]}
        />
      </div>
      {mode === 'live' && (
        <div data-testid="live-coming-soon" style={{ fontSize: 12.5, padding: '8px 10px', borderRadius: 'var(--radius-sm)', background: 'var(--warning-soft)', color: 'var(--warning)', fontWeight: 600 }}>
          Live trading coming soon. Switch back to Paper to place this order.
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <Field label="Product">
          <Segmented
            testid="trade-product" value={effectiveProduct} onChange={setProduct} fill
            options={[{ value: 'intraday', label: 'Intraday' },
              { value: 'delivery', label: 'Delivery', disabled: !buy, title: buy ? undefined : 'A sell-first trade must be intraday' }]}
          />
        </Field>
        <Field label="Entry">
          <Segmented
            testid="trade-entry-type" value={entryType} onChange={setEntryType} fill
            options={[{ value: 'market', label: 'Market' }, { value: 'limit', label: 'Limit' }]}
          />
        </Field>
        <Field label="Quantity" hint={value != null ? money(value, true) : null}>
          <input data-testid="trade-qty" type="number" min="1" step="1" value={quantity} onChange={(e) => setQuantity(e.target.value)} style={INPUT} />
        </Field>
        <Field label="Limit price">
          <input
            data-testid="trade-limit" type="number" step="0.05" value={entryType === 'limit' ? limitPrice : ''}
            placeholder={entryType === 'limit' ? '' : 'at market'} disabled={entryType !== 'limit'}
            onChange={(e) => setLimitPrice(e.target.value)} style={{ ...INPUT, opacity: entryType === 'limit' ? 1 : 0.6 }}
          />
        </Field>
        <div style={{ gridColumn: '1 / -1', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: -4 }}>
          <span style={{ fontSize: 12, color: 'var(--text-dim)', fontWeight: 600 }}>Stop / target set by</span>
          <Segmented testid="trade-level-mode" value={levelMode} onChange={switchLevelMode}
            options={LEVEL_MODES.map((m) => ({ value: m.value, label: m.label }))} />
        </div>
        <Field label={`Stop loss (${LEVEL_MODES.find((m) => m.value === levelMode).unit})`}>
          <input data-testid="trade-stop" type="number" step="0.05" value={stop} placeholder="optional"
            onChange={(e) => { edited.current.stop = true; setStop(e.target.value) }} style={INPUT} />
          <LegHint testid="trade-stop-price" side="stop" mode={levelMode} p={stopN} pct={pctFrom(stopN)} />
        </Field>
        <Field label={`Target (${LEVEL_MODES.find((m) => m.value === levelMode).unit})`}>
          <input data-testid="trade-target" type="number" step="0.05" value={target} placeholder="optional"
            onChange={(e) => { edited.current.target = true; setTarget(e.target.value) }} style={INPUT} />
          <LegHint testid="trade-target-price" side="target" mode={levelMode} p={targetN} pct={pctFrom(targetN)} />
        </Field>
        <div style={{ gridColumn: '1 / -1', marginTop: -6, fontSize: 11.5, color: 'var(--text-faint)' }}>
          Defaults {DEFAULT_STOP_PCT}% stop / {DEFAULT_TARGET_PCT}% target; clear a box for no leg.
          <button
            type="button" data-testid="trade-levels-reset"
            onClick={() => applyDefaults(entryType === 'limit' ? num(limitPrice) : ltp, true)}
            style={{ marginLeft: 6, border: 'none', background: 'transparent', padding: 0, cursor: 'pointer', color: 'var(--accent-text)', fontSize: 11.5, fontWeight: 600 }}
          >↻ {DEFAULT_STOP_PCT} / {DEFAULT_TARGET_PCT} %</button>
        </div>
      </div>

      <div className="num" data-trade-rr style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 1, background: 'var(--border)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', overflow: 'hidden', fontSize: 12 }}>
        {[['Risk', risk != null ? money(risk) : '—'], ['Reward', reward != null ? money(reward) : '—'],
          ['R : R', risk && reward ? `1 : ${(reward / risk).toFixed(2)}` : '—']].map(([k, v]) => (
          <div key={k} style={{ background: 'var(--surface)', padding: '7px 9px' }}>
            <div style={{ color: 'var(--text-faint)', fontWeight: 600 }}>{k}</div>
            <div style={{ color: 'var(--text)', fontWeight: 650, marginTop: 2 }}>{v}</div>
          </div>
        ))}
      </div>

      <Field label="Why this trade (note)">
        <textarea data-testid="trade-note" rows={1} value={note} onChange={(e) => setNote(e.target.value)} style={{ ...INPUT, resize: 'vertical' }} />
      </Field>
      </div>

      <div data-trade-panel-footer style={{ flex: '0 0 auto', display: 'flex', flexDirection: 'column', gap: 8, borderTop: '1px solid var(--border)', paddingTop: 10 }}>
      <div style={{ fontSize: 12, color: 'var(--text-faint)' }}>
        Capital needed: <b data-testid="trade-capital-needed" className="num" style={{ color: 'var(--text-dim)' }}>{needed != null ? money(needed, true) : '—'}</b>
        {legs > 1 && ' (2× : stop + target legs)'}
        {' · '}free: <b className="num" style={{ color: 'var(--text-dim)' }}>{account ? money(account.free, true) : '—'}</b>
        {effectiveProduct === 'intraday' && ' · squared off at 15:10'}
      </div>

      {(error || problem) && (
        <div data-testid="trade-error" style={{ fontSize: 12.5, color: error ? 'var(--critical)' : 'var(--text-dim)' }}>{error || problem}</div>
      )}

      <Button
        data-testid="trade-submit" disabled={!!problem || busy} onClick={submit}
        style={{ background: mode === 'live' ? 'var(--text-faint)' : sideColor, color: '#fff', padding: '10px 14px', fontSize: 14 }}
      >
        {busy ? 'Placing…' : `${buy ? 'Buy' : 'Sell'} ${qty || ''} ${instrument.symbol} · ${mode === 'live' ? 'Live' : 'Paper'}`}
      </Button>
      </div>
    </div>
  )
}
