import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  cancelOrder, exitTrade, getTrade, getTradeAccount, getTradeSnapshots, getTradeSummary, listOrders, listTrades, modifyTrade,
} from '../api/client.js'
import { getSocket, roomFor, subscribeRooms } from '../api/ws.js'
import { usePreference } from '../preferences.js'
import Button from './kit/Button.jsx'
import ExitPanel from './ExitPanel.jsx'
import KPITile, { KPITileGrid } from './kit/KPITile.jsx'
import {
  TRADES_ROOM, exchangeOf, istTime, liveTradePnl, money, price, signedMoney, toneOf, tubelight, useDismiss,
} from './tradeUtils.js'

// The Trades tab (rulebook 3b, layout per user 11-11):
//   Current    -> the "Current" grid: open positions, P&L moving on every tick
//   All trades -> the "Pending" grid (limit orders waiting for their price) and
//                 the "Completed" grid (closed trades + cancelled orders, faded)
//                 with a period filter and the expiry-bucket summary
// "Before exp." = price only; "after exp." = minus charges and slippage. For an
// open trade that is an estimate worked out once at entry (marked ≈); the
// exact figure is in the detail panel (click any row).
// Standalone: live ticks come in by props (`liveTicks`, symbol -> tick).

const isScope = (v) => v === 'open' || v === 'all'
const isDays = (v) => [1, 7, 30, 3650].includes(v)
const DAY_OPTIONS = [[1, 'Today'], [7, '7 days'], [30, '30 days'], [3650, 'All']]
const REASON = { target: 'Target', stop: 'Stop', limit: 'Limit exit', manual: 'Manual', day_end: 'Day end', cancelled: 'Cancelled' }

function Chip({ on, onClick, children, testid }) {
  return (
    <button
      type="button" data-testid={testid} data-on={on ? '1' : '0'} onClick={onClick}
      style={{
        border: '1px solid ' + (on ? 'var(--accent)' : 'var(--border-strong)'), borderRadius: 999, padding: '4px 12px',
        fontSize: 12.5, fontWeight: 600, cursor: 'pointer', background: on ? 'var(--accent-soft)' : 'var(--surface)',
        color: on ? 'var(--accent-text)' : 'var(--text-dim)',
      }}
    >{children}</button>
  )
}

const newestFirst = (key) => (a, b) => ((a[key] ?? a.placed_ts) < (b[key] ?? b.placed_ts) ? 1 : -1)
const TH = { padding: '8px 10px', textAlign: 'left', color: 'var(--text-dim)', fontWeight: 600, whiteSpace: 'nowrap' }
const TD = { padding: '8px 10px', whiteSpace: 'nowrap' }
const R = { textAlign: 'right' }
const side = (t) => (
  <span style={{ fontWeight: 700, color: t.direction === 'buy' ? 'var(--good)' : 'var(--critical)' }}>{t.direction === 'buy' ? 'BUY' : 'SELL'}</span>
)
const product = (t) => <span style={{ color: 'var(--text-dim)' }}>{t.product}{t.is_expiry_day ? ' · expiry' : ''}</span>

const KIND = { entry: 'Entry', stop: 'Stop leg', target: 'Target leg', exit: 'Limit exit' }
const OTYPE = { market: 'market', limit: 'limit', sl_m: 'SL-M' }
const CANCEL_HINT = {
  entry: 'Cancel this order (the trade is not opened)',
  stop: 'Remove the stop (the trade stays open)',
  target: 'Remove the target (the trade stays open)',
  exit: 'Cancel the limit exit (the trade stays open)',
}
function OrderLabel({ o }) {
  const buy = o.side === 'buy'
  return (
    <span data-order-label>
      <b style={{ color: o.kind === 'stop' ? 'var(--critical)' : o.kind === 'target' ? 'var(--good)' : 'var(--text)' }}>{KIND[o.kind] ?? o.kind}</b>
      <span style={{ color: 'var(--text-dim)' }}> · </span>
      <span style={{ fontWeight: 700, color: buy ? 'var(--good)' : 'var(--critical)' }}>{buy ? 'BUY' : 'SELL'}</span>
      <span style={{ color: 'var(--text-dim)' }}> {OTYPE[o.order_type] ?? o.order_type}</span>
    </span>
  )
}

// one grid: a titled card with a count and optional header controls; rows clickable
function TradeGrid({ id, title, accent, count, right, columns, rows, empty, selectedId, onSelect, rowStyle }) {
  return (
    <section data-trades-grid={id} style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', borderBottom: '1px solid var(--border)', borderLeft: `4px solid ${accent}`, flexWrap: 'wrap' }}>
        <h3 style={{ margin: 0, fontSize: 13, fontWeight: 700, letterSpacing: '0.04em', textTransform: 'uppercase', color: 'var(--text)' }}>{title}</h3>
        <span data-grid-count className="num" style={{ fontSize: 12, fontWeight: 700, padding: '1px 8px', borderRadius: 999, background: 'var(--surface-alt)', color: 'var(--text-dim)' }}>{count}</span>
        <span style={{ flex: 1 }} />
        {right}
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table data-trades-table={id} style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead style={{ background: 'var(--surface-alt)' }}>
            <tr>{columns.map((c) => <th key={c.label} style={{ ...TH, ...(c.right ? R : null) }}>{c.label}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={r.key ?? r.t.id} {...(r.attrs ?? { 'data-trade-row': r.t.id, 'data-status': r.t.status })}
                onClick={() => onSelect(r.selectId ?? r.t.id)}
                style={{ borderTop: '1px solid var(--border)', cursor: 'pointer', background: (r.selectId ?? r.t.id) === selectedId ? 'var(--accent-soft)' : undefined, ...rowStyle?.(r) }}
              >
                {columns.map((c) => <td key={c.label} className={c.num ? 'num' : undefined} style={{ ...TD, ...(c.right ? R : null), ...c.style?.(r) }}>{c.render(r)}</td>)}
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={columns.length} style={{ padding: 'var(--space-4)', textAlign: 'center', color: 'var(--text-faint)', fontSize: 12.5 }}>{empty}</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export default function TradesView({ liveTicks = {} }) {
  const [scope, setScope] = usePreference('trades.scope', 'open', isScope)
  const [days, setDays] = usePreference('trades.days', 1, isDays) // the Completed grid's period
  const [active, setActive] = useState([])       // open + pending
  const [completed, setCompleted] = useState([]) // closed + cancelled in the period
  const [orders, setOrders] = useState([])       // pending orders: entries, stop / target legs, limit exits
  const [account, setAccount] = useState(null)
  const [summary, setSummary] = useState(null)
  const [selectedId, setSelectedId] = useState(null)
  const [exitId, setExitId] = useState(null) // the open trade whose Exit panel is showing
  const [error, setError] = useState(null)

  const load = useCallback(() => {
    Promise.all([listTrades('active'), listTrades('all', days), getTradeAccount('paper'), getTradeSummary(days),
      listOrders({ status: 'pending' })])
      .then(([act, recent, a, s, o]) => {
        setActive(act); setOrders(o)
        setCompleted(recent.filter((t) => t.status === 'closed' || t.status === 'cancelled'))
        setAccount(a); setSummary(s); setError(null)
      }).catch((e) => setError(e.message))
  }, [days])

  useEffect(() => { load() }, [load])

  // placed / filled / modified / closed: move the trade to its grid, refresh the totals
  useEffect(() => {
    const socket = getSocket()
    subscribeRooms([TRADES_ROOM])
    const onUpdate = (t) => {
      const live = t.status === 'open' || t.status === 'pending'
      setActive((prev) => (live ? [t, ...prev.filter((x) => x.id !== t.id)] : prev.filter((x) => x.id !== t.id)))
      setCompleted((prev) => (live ? prev.filter((x) => x.id !== t.id) : [t, ...prev.filter((x) => x.id !== t.id)]))
      getTradeAccount('paper').then(setAccount).catch(() => {})
      listOrders({ status: 'pending' }).then(setOrders).catch(() => {})
      if (!live) getTradeSummary(days).then(setSummary).catch(() => {})
    }
    socket.on('trade_update', onUpdate)
    return () => socket.off('trade_update', onUpdate)
  }, [days])

  // ticks for every open / pending trade's stock, also one that is not on the grid
  const rooms = useMemo(() => [...new Set(active.map((t) => roomFor(exchangeOf(t), t.symbol)))], [active])
  useEffect(() => { if (rooms.length) subscribeRooms(rooms) }, [rooms])

  const ltpOf = (t) => liveTicks[t.symbol]?.ltp ?? t.running?.ltp
  const openRows = active.filter((t) => t.status === 'open').sort(newestFirst('entry_ts')).map((t) => {
    const ltp = ltpOf(t)
    const live = liveTradePnl(t, ltp)
    return { t, ltp, gross: live?.gross ?? null, net: live?.net ?? null }
  })
  // one row per pending order; click opens its trade, Cancel cancels just that order
  const pendingRows = [...orders].sort(newestFirst('placed_ts')).map((o) => ({
    t: o, ltp: liveTicks[o.symbol]?.ltp ?? active.find((x) => x.id === o.trade_id)?.running?.ltp,
    key: `o${o.id}`, selectId: o.trade_id, attrs: { 'data-order-row': o.id, 'data-order-kind': o.kind, 'data-trade-id': o.trade_id },
  }))
  async function cancelOne(id) {
    try { await cancelOrder(id); setOrders((prev) => prev.filter((o) => o.id !== id)) } catch (e) { setError(e.message) }
  }
  const doneRows = [...completed].sort(newestFirst('exit_ts')).map((t) => ({ t }))
  const openGross = openRows.reduce((s, r) => s + (r.gross ?? 0), 0)
  const openNet = openRows.reduce((s, r) => s + (r.net ?? 0), 0)
  const todayNet = account ? (account.realised_today_pnl ?? 0) + openNet : null
  const todayGross = account ? (account.realised_today_gross ?? 0) + openGross : null
  const selected = [...active, ...completed].find((t) => t.id === selectedId) ?? null
  const exiting = active.find((t) => t.id === exitId && t.status === 'open') ?? null
  const withDate = days > 1
  const money2 = (v) => signedMoney(v)

  return (
    <div data-trades-view style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <KPITileGrid>
        <KPITile label="Paper capital" value={account ? money(account.starting_capital, true) : '—'} />
        <KPITile label="Used" value={account ? money(account.used, true) : '—'} sub={`${openRows.length} open · ${pendingRows.length} pending orders`} />
        <KPITile label="Free" value={account ? money(account.free, true) : '—'} />
        {/* order (user, 11-11): Realised - Open - Today's P&L */}
        {/* realised = closed trades only, all time, after expenses */}
        <KPITile label="Realised (after exp.)" value={account ? signedMoney(account.realised_pnl) : '—'}
          tone={account?.realised_pnl > 0 ? 'pos' : account?.realised_pnl < 0 ? 'neg' : undefined} />
        <KPITile label="Open P&L" value={signedMoney(openGross)} sub={`≈ ${signedMoney(openNet)} after exp.`}
          tone={openGross > 0 ? 'pos' : openGross < 0 ? 'neg' : undefined} />
        {/* today = booked today + open now; after expenses (open part ≈) */}
        <KPITile label="Today's P&L (after exp.)" value={todayNet == null ? '—' : `${openRows.length ? '≈ ' : ''}${signedMoney(todayNet)}`}
          sub={todayGross == null ? null : `${signedMoney(todayGross)} before exp.`}
          tone={todayNet > 0 ? 'pos' : todayNet < 0 ? 'neg' : undefined} />
      </KPITileGrid>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <Chip testid="trades-scope-open" on={scope === 'open'} onClick={() => setScope('open')}>Current</Chip>
        <Chip testid="trades-scope-all" on={scope === 'all'} onClick={() => setScope('all')}>All trades</Chip>
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>≈ after-expenses figure is an estimate; click a trade for the exact one</span>
      </div>

      {error && <div style={{ color: 'var(--critical)', fontSize: 13 }}>{error}</div>}

      {scope === 'open' && (
        <TradeGrid
          id="current" title="Current" accent="var(--accent)" count={openRows.length} rows={openRows}
          selectedId={selectedId} onSelect={setSelectedId}
          empty="No open trades. Use B / S on a Market Watch row to place one."
          columns={[
            { label: 'Time', render: ({ t }) => istTime(t.entry_ts), style: () => ({ color: 'var(--text-dim)' }) },
            { label: 'Stock', render: ({ t }) => t.symbol, style: () => ({ fontWeight: 650 }) },
            { label: 'Side', render: ({ t }) => side(t) },
            { label: 'Product', render: ({ t }) => product(t) },
            { label: 'Qty', right: true, num: true, render: ({ t }) => t.quantity },
            { label: 'Entry', right: true, num: true, render: ({ t }) => price(t.entry_price) },
            { label: 'LTP', right: true, num: true, render: ({ ltp }) => price(ltp), style: () => ({ fontWeight: 600 }) },
            { label: 'Stop', right: true, num: true, render: ({ t }) => price(t.stop_price), style: () => ({ color: 'var(--text-dim)' }) },
            { label: 'Target', right: true, num: true, render: ({ t }) => price(t.target_price), style: () => ({ color: 'var(--text-dim)' }) },
            { label: 'P&L before exp.', right: true, num: true, render: ({ gross }) => money2(gross), style: ({ gross }) => ({ fontWeight: 650, color: toneOf(gross) }) },
            { label: 'P&L after exp.', right: true, num: true, render: ({ net }) => (net == null ? '—' : `≈ ${money2(net)}`), style: ({ net }) => ({ color: toneOf(net) }) },
            {
              label: 'Exit',
              render: ({ t }) => (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  {t.exit_limit_price != null && (
                    <span data-exit-waiting className="num" title="Limit exit waiting" style={{ fontSize: 11.5, fontWeight: 600, padding: '2px 6px', borderRadius: 'var(--radius-sm)', background: 'var(--warning-soft)', color: 'var(--warning)' }}>
                      lim {price(t.exit_limit_price)}
                    </span>
                  )}
                  <button
                    type="button" data-row-exit={t.id}
                    onClick={(e) => { e.stopPropagation(); setSelectedId(null); setExitId(t.id) }}
                    style={{ border: '1.5px solid var(--critical)', color: 'var(--critical)', background: 'var(--surface)', borderRadius: 'var(--radius-sm)', padding: '3px 12px', fontWeight: 700, fontSize: 12, cursor: 'pointer' }}
                  >Exit</button>
                </span>
              ),
            },
          ]}
        />
      )}

      {scope === 'all' && (
        <>
          <TradeGrid
            id="pending" title="Pending" accent="var(--warning)" count={pendingRows.length} rows={pendingRows}
            selectedId={selectedId} onSelect={setSelectedId}
            empty="No pending orders. Limit entries, stop legs and target legs wait here."
            columns={[
              { label: 'Placed', render: ({ t }) => istTime(t.placed_ts), style: () => ({ color: 'var(--text-dim)' }) },
              { label: 'Stock', render: ({ t }) => t.symbol, style: () => ({ fontWeight: 650 }) },
              { label: 'Order', render: ({ t }) => <OrderLabel o={t} /> },
              { label: 'Product', render: ({ t }) => <span style={{ color: 'var(--text-dim)' }}>{t.product}</span> },
              { label: 'Qty', right: true, num: true, render: ({ t }) => t.quantity },
              { label: 'Price', right: true, num: true, render: ({ t }) => price(t.price), style: () => ({ fontWeight: 600 }) },
              { label: 'LTP', right: true, num: true, render: ({ ltp }) => price(ltp) },
              { label: 'Away', right: true, num: true, render: ({ t, ltp }) => (ltp && t.price ? `${(((t.price - ltp) / ltp) * 100).toFixed(2)}%` : '—'), style: () => ({ color: 'var(--text-dim)' }) },
              { label: 'Trade', right: true, num: true, render: ({ t }) => `#${t.trade_id}`, style: () => ({ color: 'var(--text-faint)' }) },
              {
                label: 'Cancel',
                render: ({ t }) => (
                  <button
                    type="button" data-order-cancel={t.id} title={CANCEL_HINT[t.kind]}
                    onClick={(e) => { e.stopPropagation(); cancelOne(t.id) }}
                    style={{ border: '1.5px solid var(--text-faint)', color: 'var(--text-dim)', background: 'var(--surface)', borderRadius: 'var(--radius-sm)', padding: '3px 10px', fontWeight: 700, fontSize: 12, cursor: 'pointer' }}
                  >Cancel</button>
                ),
              },
            ]}
          />

          <TradeGrid
            id="completed" title="Completed" accent="var(--text-faint)" count={doneRows.filter((r) => r.t.status === 'closed').length} rows={doneRows}
            selectedId={selectedId} onSelect={setSelectedId}
            empty="No completed trades in this period."
            rowStyle={({ t }) => (t.status === 'cancelled' ? { opacity: 0.6 } : null)}
            right={(
              <span style={{ display: 'inline-flex', gap: 6, flexWrap: 'wrap' }}>
                {DAY_OPTIONS.map(([d, label]) => <Chip key={d} testid={`trades-days-${d}`} on={days === d} onClick={() => setDays(d)}>{label}</Chip>)}
              </span>
            )}
            columns={[
              { label: 'Entry time', render: ({ t }) => (t.entry_ts ? istTime(t.entry_ts, withDate) : '—'), style: () => ({ color: 'var(--text-dim)' }) },
              { label: 'Exit time', render: ({ t }) => istTime(t.exit_ts, withDate), style: () => ({ color: 'var(--text-dim)' }) },
              { label: 'Stock', render: ({ t }) => t.symbol, style: () => ({ fontWeight: 650 }) },
              { label: 'Side', render: ({ t }) => side(t) },
              { label: 'Product', render: ({ t }) => product(t) },
              { label: 'Qty', right: true, num: true, render: ({ t }) => t.quantity },
              { label: 'Entry', right: true, num: true, render: ({ t }) => (t.entry_price != null ? price(t.entry_price) : t.limit_price != null ? `lim ${price(t.limit_price)}` : '—') },
              { label: 'Exit', right: true, num: true, render: ({ t }) => price(t.exit_price), style: () => ({ fontWeight: 600 }) },
              { label: 'Reason', render: ({ t }) => REASON[t.exit_reason] ?? t.exit_reason ?? t.status, style: () => ({ color: 'var(--text-dim)' }) },
              { label: 'P&L before exp.', right: true, num: true, render: ({ t }) => money2(t.gross_pnl), style: ({ t }) => ({ fontWeight: 650, color: toneOf(t.gross_pnl) }) },
              { label: 'Expenses', right: true, num: true, render: ({ t }) => (t.costs != null ? money(t.costs) : '—'), style: () => ({ color: 'var(--text-dim)' }) },
              { label: 'P&L after exp.', right: true, num: true, render: ({ t }) => money2(t.net_pnl), style: ({ t }) => ({ color: toneOf(t.net_pnl) }) },
            ]}
          />

          {summary && summary.all.trades > 0 && (
            <div data-trades-summary style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12 }}>
              {[['All completed', summary.all], ['Expiry day', summary.by_expiry.expiry_day],
                ['Rest of expiry week', summary.by_expiry.expiry_week], ['Other days', summary.by_expiry.other]].map(([label, s]) => (
                <div key={label} style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', padding: '10px 12px', fontSize: 12.5 }}>
                  <div style={{ fontWeight: 700, marginBottom: 4 }}>{label}</div>
                  <div style={{ color: 'var(--text-dim)' }}>{s.trades} trades · {s.wins} wins</div>
                  <div className="num">Before exp. <b style={{ color: toneOf(s.gross_pnl) }}>{signedMoney(s.gross_pnl)}</b></div>
                  <div className="num">Expenses {money(s.costs)}</div>
                  <div className="num">After exp. <b style={{ color: toneOf(s.net_pnl) }}>{signedMoney(s.net_pnl)}</b></div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {exiting && (
        <ExitPanel key={exiting.id} trade={exiting} ltp={ltpOf(exiting)} onClose={() => setExitId(null)} />
      )}

      {selected && !exiting && (
        <TradeDetailPanel key={selected.id} trade={selected} ltp={liveTicks[selected.symbol]?.ltp}
          onClose={() => setSelectedId(null)} onChanged={load} />
      )}
    </div>
  )
}

function Row({ k, v, tone }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, padding: '3px 0', fontSize: 12.5 }}>
      <span style={{ color: 'var(--text-faint)' }}>{k}</span>
      <span className="num" style={{ fontWeight: 600, color: tone ?? 'var(--text)', textAlign: 'right' }}>{v}</span>
    </div>
  )
}

const SNAP_COLS = [['rsi', 'RSI'], ['macd_hist', 'MACD hist'], ['stoch_k', 'Stoch K'], ['adx', 'ADX'], ['vwap', 'VWAP'],
  ['ema21', 'EMA21'], ['ma50', 'MA50'], ['bb_width_pct', 'BB width %'], ['atr', 'ATR'], ['volume', 'Volume'], ['avg_volume_20', 'Avg vol 20'], ['colour', 'Colour']]

function TradeDetailPanel({ trade, ltp, onClose, onChanged }) {
  const [detail, setDetail] = useState(null)
  const [snaps, setSnaps] = useState([])
  const [book, setBook] = useState([]) // this trade's orders: entry, legs, exits
  const [stop, setStop] = useState(trade.stop_price ?? '')
  const [target, setTarget] = useState(trade.target_price ?? '')
  const [msg, setMsg] = useState(null)
  const active = trade.status === 'open' || trade.status === 'pending'
  useDismiss(onClose)

  const refresh = useCallback(() => {
    getTrade(trade.id).then(setDetail).catch((e) => setMsg(e.message))
    getTradeSnapshots(trade.id).then(setSnaps).catch(() => setSnaps([]))
    listOrders({ trade_id: trade.id }).then(setBook).catch(() => setBook([]))
  }, [trade.id])
  useEffect(() => { refresh() }, [refresh, trade.status])

  async function save() {
    setMsg(null)
    try {
      await modifyTrade(trade.id, { stop_price: stop === '' ? null : Number(stop), target_price: target === '' ? null : Number(target) })
      setMsg('Saved'); refresh(); onChanged()
    } catch (e) { setMsg(e.message) }
  }
  async function exitNow() {
    setMsg(null)
    try { await exitTrade(trade.id); refresh(); onChanged() } catch (e) { setMsg(e.message) }
  }

  const d = detail ?? trade
  const exact = d.exact // open trade: real expenses at the current price
  const buy = d.direction === 'buy'
  const byEvent = (ev) => Object.fromEntries(snaps.filter((s) => s.event === ev).map((s) => [s.timeframe, s]))
  const entrySnaps = byEvent('entry')
  const exitSnaps = byEvent('exit')
  const fmt = (v, key) => {
    if (v == null) return '—'
    if (typeof v !== 'number') return v
    if (key === 'volume' || key === 'avg_volume_20' || Math.abs(v) >= 1000) return Math.round(v).toLocaleString('en-IN')
    return v.toFixed(2)
  }

  return (
    <div
      role="dialog" aria-label={`Trade ${trade.id}`} data-trade-detail
      onMouseDown={(e) => e.stopPropagation()}
      style={{
        position: 'fixed', top: 72, right: 16, width: 420, maxWidth: 'calc(100vw - 32px)', maxHeight: 'calc(100vh - 96px)',
        overflowY: 'auto', zIndex: 600, border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow-md)',
        padding: 'var(--space-4)', display: 'flex', flexDirection: 'column', gap: 12,
        ...tubelight(buy ? 'var(--good-soft)' : 'var(--critical-soft)', 'var(--accent-soft)'),
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <div style={{ fontSize: 17, fontWeight: 700 }}>{d.symbol} <span style={{ color: buy ? 'var(--good)' : 'var(--critical)' }}>{buy ? 'BUY' : 'SELL'}</span></div>
          <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>#{d.id} · {d.mode} · {d.product} · {d.entry_type} · from {d.source}</div>
        </div>
        <button type="button" onClick={onClose} aria-label="Close" style={{ border: 'none', background: 'transparent', fontSize: 18, cursor: 'pointer', color: 'var(--text-faint)' }}>×</button>
      </div>

      <div>
        <Row k="Status" v={`${d.status}${d.exit_reason ? ` · ${REASON[d.exit_reason] ?? d.exit_reason}` : ''}`} />
        <Row k="Placed" v={istTime(d.placed_ts, true)} />
        <Row k="Quantity" v={d.quantity} />
        {d.limit_price != null && <Row k="Limit price" v={price(d.limit_price)} />}
        <Row k="Entry" v={d.entry_price != null ? `${price(d.entry_price)} at ${istTime(d.entry_ts)}` : '—'} />
        {d.exit_price != null && <Row k="Exit" v={`${price(d.exit_price)} at ${istTime(d.exit_ts)}`} />}
        {d.status === 'open' && <Row k="LTP" v={price(ltp ?? exact?.ltp)} />}
        <Row k="Best / worst price" v={`${price(d.best_price)} / ${price(d.worst_price)}`} />
        <Row k="Expiry" v={d.expiry_date ? `${d.expiry_date} (${d.days_to_expiry} trading days${d.is_expiry_day ? ', expiry day' : d.is_expiry_week ? ', expiry week' : ''})` : '—'} />
        {d.screen_signal && <Row k="Screen said" v={d.screen_signal} />}
        {d.note && <Row k="Note" v={d.note} />}
      </div>

      <div data-testid="trade-detail-pnl" style={{ borderTop: '1px solid var(--border)', paddingTop: 10 }}>
        {exact ? (
          <>
            <Row k="P&L before expenses" v={signedMoney(exact.gross_pnl)} tone={toneOf(exact.gross_pnl)} />
            <Row k="Expenses (charges + slippage)" v={money(exact.costs)} />
            <Row k="P&L after expenses" v={signedMoney(exact.net_pnl)} tone={toneOf(exact.net_pnl)} />
            <div style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>Exact at {price(exact.ltp)}, if exited now at market.</div>
          </>
        ) : d.status === 'closed' ? (
          <>
            <Row k="P&L before expenses" v={signedMoney(d.gross_pnl)} tone={toneOf(d.gross_pnl)} />
            <Row k="Expenses (charges + slippage)" v={money(d.costs)} />
            <Row k="P&L after expenses" v={signedMoney(d.net_pnl)} tone={toneOf(d.net_pnl)} />
          </>
        ) : <Row k="P&L" v="—" />}
      </div>

      {active && (
        <div style={{ borderTop: '1px solid var(--border)', paddingTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            <label style={{ fontSize: 12, color: 'var(--text-dim)', fontWeight: 600 }}>Stop
              <input data-testid="detail-stop" type="number" step="0.05" value={stop} onChange={(e) => setStop(e.target.value)}
                style={{ display: 'block', width: '100%', boxSizing: 'border-box', marginTop: 4, padding: '6px 8px', border: '1px solid var(--border-strong)', borderRadius: 'var(--radius-sm)', background: 'var(--surface)', color: 'var(--text)' }} />
            </label>
            <label style={{ fontSize: 12, color: 'var(--text-dim)', fontWeight: 600 }}>Target
              <input data-testid="detail-target" type="number" step="0.05" value={target} onChange={(e) => setTarget(e.target.value)}
                style={{ display: 'block', width: '100%', boxSizing: 'border-box', marginTop: 4, padding: '6px 8px', border: '1px solid var(--border-strong)', borderRadius: 'var(--radius-sm)', background: 'var(--surface)', color: 'var(--text)' }} />
            </label>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <Button variant="secondary" data-testid="detail-save" onClick={save}>Save stop / target</Button>
            <Button variant="danger" data-testid="detail-exit" onClick={exitNow}>{trade.status === 'pending' ? 'Cancel order' : 'Exit now'}</Button>
          </div>
        </div>
      )}
      {msg && <div data-testid="detail-msg" style={{ fontSize: 12.5, color: msg === 'Saved' ? 'var(--good)' : 'var(--critical)' }}>{msg}</div>}

      {book.length > 0 && (
        <div style={{ borderTop: '1px solid var(--border)', paddingTop: 10 }}>
          <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>Orders</div>
          <table data-trade-orders style={{ borderCollapse: 'collapse', fontSize: 11.5, width: '100%' }}>
            <tbody>
              {book.map((o) => (
                <tr key={o.id} data-order-status={o.status} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '4px 6px' }}><OrderLabel o={o} /></td>
                  <td className="num" style={{ padding: '4px 6px', textAlign: 'right' }}>{o.price != null ? price(o.price) : 'mkt'}</td>
                  <td style={{ padding: '4px 6px', fontWeight: 600, color: o.status === 'filled' ? 'var(--good)' : o.status === 'pending' ? 'var(--warning)' : 'var(--text-faint)' }}>
                    {o.status}{o.fill_price != null ? ` ${price(o.fill_price)}` : ''}
                  </td>
                  <td style={{ padding: '4px 6px', color: 'var(--text-faint)' }}>{o.cancel_reason ?? istTime(o.done_ts ?? o.placed_ts)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div style={{ borderTop: '1px solid var(--border)', paddingTop: 10 }}>
        <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>Indicators at entry{d.status === 'closed' ? ' / exit' : ''}</div>
        {snaps.length === 0 ? (
          <div style={{ fontSize: 12, color: 'var(--text-faint)' }}>No indicator values stored (no candle had closed yet for this stock).</div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table data-trade-snapshots style={{ borderCollapse: 'collapse', fontSize: 11.5, width: '100%' }}>
              <thead>
                <tr>
                  <th style={{ ...TH, padding: '4px 6px' }} />
                  {['1min', '3min', '5min'].map((tf) => (
                    <th key={tf} colSpan={d.status === 'closed' ? 2 : 1} style={{ ...TH, padding: '4px 6px', textAlign: 'right' }}>{tf.replace('min', 'm')}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {SNAP_COLS.map(([key, label]) => (
                  <tr key={key} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '3px 6px', color: 'var(--text-faint)' }}>{label}</td>
                    {['1min', '3min', '5min'].flatMap((tf) => {
                      const cells = [<td key={`${tf}e`} className="num" style={{ padding: '3px 6px', textAlign: 'right' }}>{fmt(entrySnaps[tf]?.[key], key)}</td>]
                      if (d.status === 'closed') cells.push(<td key={`${tf}x`} className="num" style={{ padding: '3px 6px', textAlign: 'right', color: 'var(--text-dim)' }}>{fmt(exitSnaps[tf]?.[key], key)}</td>)
                      return cells
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
            {d.status === 'closed' && <div style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 4 }}>Each timeframe: entry, then exit (grey).</div>}
          </div>
        )}
      </div>
    </div>
  )
}
