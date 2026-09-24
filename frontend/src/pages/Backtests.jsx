import { useCallback, useEffect, useRef, useState } from 'react'
import {
  createBacktestRun, getBacktestDayResults, getBacktestRunMaster, getBacktestTrades,
  listBacktestRunMasters, listStrategies, listSymbols, listWatchlists,
} from '../api/client.js'
import DayChart from '../components/DayChart.jsx'
import TradeExecutionPathChart from '../components/TradeExecutionPathChart.jsx'
import TradePathSparkline from '../components/TradePathSparkline.jsx'

const TIMEFRAMES = ['1min', '3min', '5min', '1day']

function todayIso() {
  return new Date().toISOString().slice(0, 10)
}

function monthAgoIso() {
  const d = new Date()
  d.setMonth(d.getMonth() - 1)
  return d.toISOString().slice(0, 10)
}

const EMPTY_FORM = {
  strategyId: '', scopeMode: 'instrument', instrumentId: '', watchlistId: '',
  timeframe: '3min', start: monthAgoIso(), end: todayIso(), sessionName: '', attachTo: '',
}

export default function Backtests() {
  const [sessions, setSessions] = useState([])
  const [strategies, setStrategies] = useState([])
  const [symbols, setSymbols] = useState([])
  const [watchlists, setWatchlists] = useState([])
  const [form, setForm] = useState(EMPTY_FORM)
  const [launching, setLaunching] = useState(false)
  const [error, setError] = useState(null)
  const [selectedMasterId, setSelectedMasterId] = useState(null)
  const [detail, setDetail] = useState(null)

  const refreshSessions = useCallback(() => {
    listBacktestRunMasters().then(setSessions).catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    refreshSessions()
    listStrategies().then(setStrategies).catch((e) => setError(e.message))
    listSymbols().then(setSymbols).catch((e) => setError(e.message))
    listWatchlists().then(setWatchlists).catch((e) => setError(e.message))
  }, [refreshSessions])

  function openSession(id) {
    setError(null)
    setSelectedMasterId(id)
    getBacktestRunMaster(id).then(setDetail).catch((e) => setError(e.message))
  }

  function handleLaunch(e) {
    e.preventDefault()
    if (!form.strategyId) { setError('Select a strategy'); return }
    if (!form.attachTo && form.scopeMode === 'instrument' && !form.instrumentId) {
      setError('Select an instrument'); return
    }
    if (!form.attachTo && form.scopeMode === 'watchlist' && !form.watchlistId) {
      setError('Select a watchlist'); return
    }

    const payload = {
      strategy_id: Number(form.strategyId), timeframe: form.timeframe,
      start: form.start, end: form.end,
    }
    if (form.attachTo) {
      payload.run_master_id = Number(form.attachTo)
    } else {
      payload.session_name = form.sessionName || undefined
      if (form.scopeMode === 'instrument') payload.instrument_id = Number(form.instrumentId)
      else payload.watchlist_id = Number(form.watchlistId)
    }

    setLaunching(true)
    setError(null)
    createBacktestRun(payload)
      .then((result) => {
        refreshSessions()
        openSession(result.run_master_id)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLaunching(false))
  }

  return (
    <div style={{ padding: 16, maxWidth: 1100, margin: '0 auto' }}>
      <h2>Backtests</h2>

      <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: 20 }}>
        <thead>
          <tr style={{ textAlign: 'left', borderBottom: '2px solid #ccc' }}>
            <th style={{ padding: 6 }}>Session</th>
            <th style={{ padding: 6 }}>Mode</th>
            <th style={{ padding: 6 }}>Status</th>
            <th style={{ padding: 6 }}>Runs</th>
            <th style={{ padding: 6 }}>Date range</th>
          </tr>
        </thead>
        <tbody>
          {sessions.map((s) => (
            <tr
              key={s.id} onClick={() => openSession(s.id)}
              style={{
                cursor: 'pointer', borderBottom: '1px solid #eee',
                background: s.id === selectedMasterId ? '#eef3fc' : undefined,
              }}
            >
              <td style={{ padding: 6 }}>{s.name || `#${s.id}`}</td>
              <td style={{ padding: 6 }}>{s.mode}</td>
              <td style={{ padding: 6 }}>{s.status}</td>
              <td style={{ padding: 6 }}>{s.run_count}</td>
              <td style={{ padding: 6 }}>{s.date_from.slice(0, 10)} → {s.date_to.slice(0, 10)}</td>
            </tr>
          ))}
          {sessions.length === 0 && (
            <tr><td colSpan={5} style={{ padding: 6, color: '#888' }}>No backtests run yet</td></tr>
          )}
        </tbody>
      </table>

      <div style={{ padding: 16, border: '1px solid #ccc', borderRadius: 6, marginBottom: 20 }}>
        <h3 style={{ marginTop: 0 }}>Launch a run</h3>
        <form onSubmit={handleLaunch}>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 10 }}>
            <label>
              Strategy<br />
              <select value={form.strategyId} onChange={(e) => setForm({ ...form, strategyId: e.target.value })}>
                <option value="">Select…</option>
                {strategies.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </label>
            <label>
              Timeframe<br />
              <select value={form.timeframe} onChange={(e) => setForm({ ...form, timeframe: e.target.value })}>
                {TIMEFRAMES.map((tf) => <option key={tf} value={tf}>{tf}</option>)}
              </select>
            </label>
            <label>
              From<br />
              <input type="date" value={form.start} onChange={(e) => setForm({ ...form, start: e.target.value })} />
            </label>
            <label>
              To<br />
              <input type="date" value={form.end} onChange={(e) => setForm({ ...form, end: e.target.value })} />
            </label>
          </div>

          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 10 }}>
            <label>
              Add to existing session<br />
              <select value={form.attachTo} onChange={(e) => setForm({ ...form, attachTo: e.target.value })}>
                <option value="">(new session)</option>
                {sessions.map((s) => <option key={s.id} value={s.id}>{s.name || `#${s.id}`}</option>)}
              </select>
            </label>

            {!form.attachTo && (
              <>
                <label>
                  Scope<br />
                  <select value={form.scopeMode} onChange={(e) => setForm({ ...form, scopeMode: e.target.value })}>
                    <option value="instrument">Single instrument</option>
                    <option value="watchlist">Watchlist (one run per stock)</option>
                  </select>
                </label>
                {form.scopeMode === 'instrument' ? (
                  <label>
                    Instrument<br />
                    <select value={form.instrumentId} onChange={(e) => setForm({ ...form, instrumentId: e.target.value })}>
                      <option value="">Select…</option>
                      {symbols.map((s) => <option key={s.id} value={s.id}>{s.symbol}</option>)}
                    </select>
                  </label>
                ) : (
                  <label>
                    Watchlist<br />
                    <select value={form.watchlistId} onChange={(e) => setForm({ ...form, watchlistId: e.target.value })}>
                      <option value="">Select…</option>
                      {watchlists.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                    </select>
                  </label>
                )}
                <label style={{ flex: '1 1 200px' }}>
                  Session name<br />
                  <input
                    style={{ width: '100%' }} placeholder="optional"
                    value={form.sessionName} onChange={(e) => setForm({ ...form, sessionName: e.target.value })}
                  />
                </label>
              </>
            )}
          </div>

          {error && <div style={{ color: 'crimson', marginBottom: 8 }}>{error}</div>}
          <button type="submit" disabled={launching}>{launching ? 'Running…' : 'Run backtest'}</button>
        </form>
      </div>

      {detail && (
        <SessionDetail
          key={detail.id} detail={detail}
          strategyById={Object.fromEntries(strategies.map((s) => [s.id, s]))}
        />
      )}
    </div>
  )
}

// scrollDetailIntoView: shared by every expandable-row table on this page
// (GroupedRuns, RunsTable, TradesTable) -- content revealed by expanding a
// row can end up below the fold, especially several drill-down levels deep
// (Session -> Strategy/Symbol -> Run -> Year -> Month -> Day -> Trade).
// Fires twice: immediately (most detail content is already in already-
// fetched data) and again after a short delay, in case the detail area
// itself fetches more and grows further (e.g. RunDetail's own trades/day-
// results, or TradeExecutionPathChart).
function scrollDetailIntoView(el) {
  if (!el) return undefined
  el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  const t = setTimeout(() => el.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 400)
  return () => clearTimeout(t)
}

// Once a row is opened, it stays MOUNTED forever (for this SessionDetail
// instance) and closing it only toggles CSS display — never unmounts it.
// Explicit request: "once details are fetched, should remain on page,
// should not load again if I come back on the same row." A plain useState
// Set would re-render on every mutation for no reason (isOpen already does
// that job); the mounted-ids set only needs to SURVIVE renders, not CAUSE
// them, so it's a ref, not state.
function useExpandableRows() {
  const [openId, setOpenId] = useState(null)
  const mountedIds = useRef(new Set())
  const detailRefs = useRef({})
  function toggle(id) {
    mountedIds.current.add(id)
    setOpenId((cur) => (cur === id ? null : id))
  }
  function registerDetailRef(id) {
    return (el) => { detailRefs.current[id] = el }
  }
  useEffect(() => {
    if (openId == null) return
    return scrollDetailIntoView(detailRefs.current[openId])
  }, [openId])
  return {
    openId, isOpen: (id) => openId === id, isMounted: (id) => mountedIds.current.has(id), toggle, registerDetailRef,
  }
}

function groupRunsBy(runs, key) {
  const map = new Map()
  for (const r of runs) {
    if (!map.has(r[key])) map.set(r[key], [])
    map.get(r[key]).push(r)
  }
  return map
}

function aggregateRunResults(runs) {
  return runs.reduce((acc, r) => {
    if (!r.result) return acc
    acc.total_trades += r.result.total_trades
    acc.wins += r.result.wins
    acc.total_net_pnl += r.result.total_net_pnl
    return acc
  }, { total_trades: 0, wins: 0, total_net_pnl: 0 })
}

// Top of the hierarchy adapts to what's actually being compared in this
// session (explicit instruction): a single-stock/single-strategy session
// just lists its run(s) directly; multiple stocks group by Symbol first;
// multiple strategies group by Strategy first (above Symbol, when both
// vary) — each group level expands to reveal the next, ending in the
// existing per-run Years/Months/Days/Trades drill-down (RunDetail).
function SessionDetail({ detail, strategyById }) {
  const distinctStrategies = new Set(detail.runs.map((r) => r.strategy_id))
  const distinctInstruments = new Set(detail.runs.map((r) => r.instrument_id))
  const groupKeys = []
  if (distinctStrategies.size > 1) groupKeys.push('strategy_id')
  if (distinctInstruments.size > 1) groupKeys.push('instrument_id')

  return (
    <div style={{ padding: 16, border: '1px solid #ccc', borderRadius: 6 }}>
      <h3 style={{ marginTop: 0 }}>{detail.name || `Session #${detail.id}`}</h3>
      {groupKeys.length === 0
        ? <RunsTable runs={detail.runs} runMasterId={detail.id} bestRunId={detail.best_run_id} />
        : (
          <GroupedRuns
            runs={detail.runs} groupKeys={groupKeys} runMasterId={detail.id}
            bestRunId={detail.best_run_id} strategyById={strategyById}
          />
        )}
    </div>
  )
}

function GroupedRuns({ runs, groupKeys, runMasterId, bestRunId, strategyById }) {
  const [key, ...restKeys] = groupKeys
  const groups = groupRunsBy(runs, key)
  const expand = useExpandableRows()
  const headerLabel = key === 'strategy_id' ? 'Strategy' : 'Symbol'

  function labelFor(value, groupRuns) {
    if (key === 'strategy_id') return strategyById[value]?.name || `Strategy #${value}`
    return groupRuns[0].symbol || value
  }

  return (
    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
      <thead>
        <tr style={{ textAlign: 'left', borderBottom: '2px solid #ccc' }}>
          <th style={{ padding: 6 }}>{headerLabel}</th>
          <th style={{ padding: 6 }}>Runs</th>
          <th style={{ padding: 6 }}>Trades</th>
          <th style={{ padding: 6 }}>Win ratio</th>
          <th style={{ padding: 6 }}>Net P&amp;L</th>
        </tr>
      </thead>
      <tbody>
        {[...groups.entries()].map(([value, groupRuns]) => {
          const isOpen = expand.isOpen(value)
          const agg = aggregateRunResults(groupRuns)
          const winRatio = agg.total_trades ? agg.wins / agg.total_trades : null
          return [
            <tr
              key={value} onClick={() => expand.toggle(value)}
              style={{
                cursor: 'pointer', background: isOpen ? '#eef3fc' : undefined,
                borderBottom: isOpen ? 'none' : '1px solid #eee',
              }}
            >
              <td style={{ padding: 6 }}>{isOpen ? '▾ ' : '▸ '}{labelFor(value, groupRuns)}</td>
              <td style={{ padding: 6 }}>{groupRuns.length}</td>
              <td style={{ padding: 6 }}>{agg.total_trades}</td>
              <td style={{ padding: 6 }}>{winRatio != null ? `${(winRatio * 100).toFixed(1)}%` : '—'}</td>
              <td style={{ padding: 6, color: agg.total_net_pnl < 0 ? 'crimson' : undefined }}>
                {agg.total_net_pnl.toLocaleString(undefined, { maximumFractionDigits: 2 })}
              </td>
            </tr>,
            expand.isMounted(value) && (
              <tr
                key={`${value}-detail`} ref={expand.registerDetailRef(value)}
                style={{ display: isOpen ? undefined : 'none', borderBottom: '1px solid #eee' }}
              >
                <td colSpan={5} style={{ padding: isOpen ? '0 6px 16px' : 0, background: '#fafbfd' }}>
                  {restKeys.length > 0
                    ? (
                      <GroupedRuns
                        runs={groupRuns} groupKeys={restKeys} runMasterId={runMasterId}
                        bestRunId={bestRunId} strategyById={strategyById}
                      />
                    )
                    : <RunsTable runs={groupRuns} runMasterId={runMasterId} bestRunId={bestRunId} />}
                </td>
              </tr>
            ),
          ]
        })}
      </tbody>
    </table>
  )
}

// Leaf level: the actual runs (differing only by timeframe once grouped by
// stock/strategy above, or the session's own single axis when nothing
// needed grouping) — each expands into RunDetail's Years/Months/Days/
// Trades drill-down.
function RunsTable({ runs, runMasterId, bestRunId }) {
  const expand = useExpandableRows()

  return (
    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
      <thead>
        <tr style={{ textAlign: 'left', borderBottom: '2px solid #ccc' }}>
          <th style={{ padding: 6 }}>Symbol</th>
          <th style={{ padding: 6 }}>Timeframe</th>
          <th style={{ padding: 6 }}>Status</th>
          <th style={{ padding: 6 }}>Trades</th>
          <th style={{ padding: 6 }}>Win ratio</th>
          <th style={{ padding: 6 }}>Net P&amp;L</th>
        </tr>
      </thead>
      <tbody>
        {runs.map((r) => {
          const isOpen = expand.isOpen(r.id)
          const clickable = r.status === 'completed'
          return [
            <tr
              key={r.id}
              onClick={clickable ? () => expand.toggle(r.id) : undefined}
              style={{
                borderBottom: isOpen ? 'none' : '1px solid #eee',
                cursor: clickable ? 'pointer' : undefined,
                background: isOpen ? '#eef3fc' : undefined,
              }}
            >
              <td style={{ padding: 6 }}>{isOpen ? '▾ ' : clickable ? '▸ ' : ''}{r.symbol || r.instrument_id}</td>
              <td style={{ padding: 6 }}>{r.timeframe}</td>
              <td style={{ padding: 6 }}>
                {r.status}{r.id === bestRunId && r.status === 'completed' ? ' ★' : ''}
              </td>
              <td style={{ padding: 6 }}>{r.result ? r.result.total_trades : '—'}</td>
              <td style={{ padding: 6 }}>
                {r.result && r.result.win_ratio != null ? `${(r.result.win_ratio * 100).toFixed(1)}%` : '—'}
              </td>
              <td style={{ padding: 6, color: r.result && r.result.total_net_pnl < 0 ? 'crimson' : undefined }}>
                {r.result ? r.result.total_net_pnl.toLocaleString(undefined, { maximumFractionDigits: 2 }) : '—'}
                {r.status === 'failed' && <span style={{ color: 'crimson', marginLeft: 8 }}>{r.error_message}</span>}
              </td>
            </tr>,
            expand.isMounted(r.id) && (
              <tr
                key={`${r.id}-detail`} ref={expand.registerDetailRef(r.id)}
                style={{ display: isOpen ? undefined : 'none', borderBottom: '1px solid #eee' }}
              >
                <td colSpan={6} style={{ padding: isOpen ? '0 6px 16px' : 0, background: '#fafbfd' }}>
                  <RunDetail runMasterId={runMasterId} runId={r.id} />
                </td>
              </tr>
            ),
          ]
        })}
      </tbody>
    </table>
  )
}

// Aggregates BacktestDayResult rows (already daily) up to whatever
// granularity keyFn groups by — used for the year/month levels of the
// drill-down; the day level just needs the rows filtered, not re-aggregated.
function aggregateDays(dayResults, keyFn) {
  const map = new Map()
  for (const d of dayResults) {
    const k = keyFn(d)
    const cur = map.get(k) || { key: k, trade_count: 0, win_count: 0, loss_count: 0, net_pnl: 0 }
    cur.trade_count += d.trade_count
    cur.win_count += d.win_count
    cur.loss_count += d.loss_count
    cur.net_pnl += d.net_pnl
    map.set(k, cur)
  }
  return [...map.values()].sort((a, b) => a.key.localeCompare(b.key))
}

function RollupTable({ rows, labelHeader, onRowClick }) {
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
      <thead>
        <tr style={{ textAlign: 'left', borderBottom: '1px solid #ccc' }}>
          <th style={{ padding: 4 }}>{labelHeader}</th>
          <th style={{ padding: 4 }}>Trades</th>
          <th style={{ padding: 4 }}>Wins</th>
          <th style={{ padding: 4 }}>Losses</th>
          <th style={{ padding: 4 }}>Net</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr
            key={r.key} onClick={() => onRowClick(r.key)}
            style={{ borderBottom: '1px solid #f0f0f0', cursor: 'pointer' }}
          >
            <td style={{ padding: 4 }}>{r.key}</td>
            <td style={{ padding: 4 }}>{r.trade_count}</td>
            <td style={{ padding: 4 }}>{r.win_count}</td>
            <td style={{ padding: 4 }}>{r.loss_count}</td>
            <td style={{ padding: 4, color: r.net_pnl < 0 ? 'crimson' : undefined }}>{r.net_pnl.toFixed(2)}</td>
          </tr>
        ))}
        {rows.length === 0 && <tr><td colSpan={5} style={{ padding: 4, color: '#888' }}>No data</td></tr>}
      </tbody>
    </table>
  )
}

const _INDICATOR_FIELDS = [
  ['rsi', 'RSI'], ['rsi_state', 'RSI state'], ['rsi_trend', 'RSI trend'],
  ['macd_line', 'MACD line'], ['macd_signal', 'MACD signal'], ['macd_state', 'MACD state'],
  ['macd_trend', 'MACD trend'],
  ['stoch_k', 'Stoch %K'], ['stoch_d', 'Stoch %D'], ['stoch_state', 'Stoch state'],
  ['stoch_trend', 'Stoch trend'],
  ['vwap', 'VWAP'], ['ma21', 'MA21'], ['ma50', 'MA50'], ['atr', 'ATR'],
  ['bb_upper', 'BB upper'], ['bb_middle', 'BB mid'], ['bb_lower', 'BB lower'],
]

const _TREND_ARROW = { increasing: '↑', decreasing: '↓', flat: '→' }

function fmtIndicator(v, key) {
  if (v == null) return '—'
  if (key.endsWith('_trend')) return `${_TREND_ARROW[v] || ''} ${v}`.trim()
  return typeof v === 'number' ? v.toFixed(2) : v
}

// entry_*/exit_* value+state snapshot (already on every trade row via
// routes_backtests.py's _serialize_trade -- no extra fetch needed). The
// sparkline used to be a column here too, but with 18 indicator columns it
// stretched the whole page horizontally -- moved out to sit beside the big
// chart instead (see TradesTable's detail row), and this table now scrolls
// within its own bounded container rather than growing the page.
function IndicatorSnapshotGrid({ trade }) {
  const hasAny = _INDICATOR_FIELDS.some(
    ([key]) => trade[`entry_${key}`] != null || trade[`exit_${key}`] != null,
  )
  if (!hasAny) {
    return (
      <div style={{ fontSize: 12, color: '#888', marginBottom: 10 }}>
        No indicator snapshot for this trade (CandleIndicators wasn't available at this timestamp).
      </div>
    )
  }
  return (
    <div style={{ overflowX: 'auto', marginBottom: 10 }}>
      <table style={{ borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr style={{ textAlign: 'left', borderBottom: '1px solid #ccc' }}>
            <th style={{ padding: '2px 8px' }} />
            {_INDICATOR_FIELDS.map(([key, label]) => <th key={key} style={{ padding: '2px 8px' }}>{label}</th>)}
          </tr>
        </thead>
        <tbody>
          <tr>
            <td style={{ padding: '2px 8px', fontWeight: 600 }}>Entry</td>
            {_INDICATOR_FIELDS.map(([key]) => (
              <td key={key} style={{ padding: '2px 8px' }}>{fmtIndicator(trade[`entry_${key}`], key)}</td>
            ))}
          </tr>
          <tr>
            <td style={{ padding: '2px 8px', fontWeight: 600 }}>Exit</td>
            {_INDICATOR_FIELDS.map(([key]) => (
              <td key={key} style={{ padding: '2px 8px' }}>{fmtIndicator(trade[`exit_${key}`], key)}</td>
            ))}
          </tr>
        </tbody>
      </table>
    </div>
  )
}

// entry_ts/exit_ts -> "45m" / "2h 15m" -- how long the position was open.
function formatDuration(entryTs, exitTs) {
  const totalMinutes = Math.round((new Date(exitTs) - new Date(entryTs)) / 60000)
  if (totalMinutes < 60) return `${totalMinutes}m`
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  return minutes ? `${hours}h ${minutes}m` : `${hours}h`
}

// Each row expands to the indicator state/value snapshot plus the
// execution-path chart (candle-by-candle price path between the SL/TG
// bars) -- the two additions from [[backtest_trade_drilldown_chart_plan]].
function TradesTable({ trades, runMasterId, runId }) {
  const expand = useExpandableRows()

  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
      <thead>
        <tr style={{ textAlign: 'left', borderBottom: '1px solid #ccc' }}>
          <th style={{ padding: 4 }}>Entry</th>
          <th style={{ padding: 4 }}>Pattern</th>
          <th style={{ padding: 4 }}>Dir</th>
          <th style={{ padding: 4 }}>Qty</th>
          <th style={{ padding: 4 }}>Entry px</th>
          <th style={{ padding: 4 }}>Exit px</th>
          <th style={{ padding: 4 }}>Reason</th>
          <th style={{ padding: 4 }}>Duration</th>
          <th style={{ padding: 4 }}>Net</th>
        </tr>
      </thead>
      <tbody>
        {trades.map((t) => {
          const isOpen = expand.isOpen(t.id)
          return [
            <tr
              key={t.id} onClick={() => expand.toggle(t.id)}
              style={{ cursor: 'pointer', background: isOpen ? '#eef3fc' : undefined }}
            >
              <td style={{ padding: 4 }}>{isOpen ? '▾ ' : '▸ '}{t.entry_ts.replace('T', ' ').slice(0, 16)}</td>
              <td style={{ padding: 4 }}>{t.pattern}</td>
              <td style={{ padding: 4 }}>{t.direction}</td>
              <td style={{ padding: 4 }}>{t.quantity}</td>
              <td style={{ padding: 4 }}>{t.entry_price}</td>
              <td style={{ padding: 4 }}>{t.exit_price}</td>
              <td style={{ padding: 4 }}>{t.exit_reason}</td>
              <td style={{ padding: 4 }}>{formatDuration(t.entry_ts, t.exit_ts)}</td>
              <td style={{ padding: 4, color: t.net_pnl < 0 ? 'crimson' : undefined }}>{t.net_pnl}</td>
            </tr>,
            expand.isMounted(t.id) && (
              <tr
                key={`${t.id}-detail`} ref={expand.registerDetailRef(t.id)}
                style={{ display: isOpen ? undefined : 'none' }}
              >
                <td colSpan={9} style={{ padding: isOpen ? '4px 8px 14px' : 0, background: '#fafbfd' }}>
                  <IndicatorSnapshotGrid trade={t} />
                  <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', maxWidth: 900 }}>
                    <div style={{ flex: '0 0 auto' }}>
                      <div style={{ fontSize: 11, color: '#888', marginBottom: 2 }}>Path</div>
                      <TradePathSparkline runMasterId={runMasterId} runId={runId} trade={t} />
                    </div>
                    <div style={{ flex: '1 1 auto', minWidth: 0 }}>
                      <TradeExecutionPathChart runMasterId={runMasterId} runId={runId} trade={t} />
                    </div>
                  </div>
                </td>
              </tr>
            ),
          ]
        })}
        {trades.length === 0 && <tr><td colSpan={9} style={{ padding: 4, color: '#888' }}>No trades this day</td></tr>}
      </tbody>
    </table>
  )
}

function Breadcrumb({ year, month, day, onBack, onYear, onMonth }) {
  return (
    <div style={{ fontSize: 13, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 10 }}>
      <button type="button" onClick={onBack} disabled={!year}>&larr; Back</button>
      <span>
        <button
          type="button" onClick={onYear} disabled={!year}
          style={{ fontWeight: year ? 400 : 700, border: 'none', background: 'none', cursor: year ? 'pointer' : 'default', padding: 0 }}
        >
          All years
        </button>
        {year && (
          <>
            <span style={{ margin: '0 6px', color: '#888' }}>/</span>
            <button
              type="button" onClick={onMonth} disabled={!month}
              style={{ fontWeight: month ? 400 : 700, border: 'none', background: 'none', cursor: month ? 'pointer' : 'default', padding: 0 }}
            >
              {year}
            </button>
          </>
        )}
        {month && (
          <>
            <span style={{ margin: '0 6px', color: '#888' }}>/</span>
            <span style={{ fontWeight: day ? 400 : 700 }}>{month}</span>
          </>
        )}
        {day && (
          <>
            <span style={{ margin: '0 6px', color: '#888' }}>/</span>
            <span style={{ fontWeight: 700 }}>{day}</span>
          </>
        )}
      </span>
    </div>
  )
}

// Years -> Months -> Days -> Trades drill-down. Year/month levels are
// aggregated client-side from the already-daily BacktestDayResult rows;
// the day level is just those rows filtered; only the last level needs the
// actual trades (filtered by entry date).
function RunDetail({ runMasterId, runId }) {
  const [trades, setTrades] = useState(null)
  const [dayResults, setDayResults] = useState(null)
  const [error, setError] = useState(null)
  const [year, setYear] = useState(null)
  const [month, setMonth] = useState(null) // "YYYY-MM"
  const [day, setDay] = useState(null) // "YYYY-MM-DD"
  const contentRef = useRef(null)

  useEffect(() => {
    Promise.all([getBacktestTrades(runMasterId, runId), getBacktestDayResults(runMasterId, runId)])
      .then(([t, d]) => { setTrades(t); setDayResults(d) })
      .catch((e) => setError(e.message))
  }, [runMasterId, runId])

  // Same "content moves below the screen" fix as the row-expand tables,
  // applied to this drill-down's own Year -> Month -> Day -> Trades levels
  // (explicit follow-up: "like month trade list etc") -- each level
  // REPLACES the one before it rather than stacking, but this drill-down
  // itself typically sits deep inside several already-expanded rows above
  // it, so clicking a Year/Month/Day can still land its new table below
  // the fold.
  useEffect(() => {
    return scrollDetailIntoView(contentRef.current)
  }, [year, month, day])

  if (error) return <div style={{ color: 'crimson', marginTop: 10 }}>{error}</div>
  if (!trades || !dayResults) return <div style={{ marginTop: 10 }}>Loading…</div>

  const years = aggregateDays(dayResults, (d) => d.date.slice(0, 4))
  const months = year
    ? aggregateDays(dayResults.filter((d) => d.date.slice(0, 4) === year), (d) => d.date.slice(0, 7))
    : []
  const days = month
    ? aggregateDays(dayResults.filter((d) => d.date.slice(0, 7) === month), (d) => d.date.slice(0, 10))
    : []
  const dayTrades = day ? trades.filter((t) => t.entry_ts.slice(0, 10) === day) : []

  return (
    <div style={{ marginTop: 12 }} ref={contentRef}>
      <Breadcrumb
        year={year} month={month} day={day}
        onBack={() => {
          if (day) setDay(null)
          else if (month) setMonth(null)
          else if (year) setYear(null)
        }}
        onYear={() => { setYear(null); setMonth(null); setDay(null) }}
        onMonth={() => { setMonth(null); setDay(null) }}
      />
      {!year && <RollupTable rows={years} labelHeader="Year" onRowClick={setYear} />}
      {year && !month && <RollupTable rows={months} labelHeader="Month" onRowClick={setMonth} />}
      {month && !day && <RollupTable rows={days} labelHeader="Date" onRowClick={setDay} />}
      {day && (
        <>
          <DayChart runMasterId={runMasterId} runId={runId} date={day} trades={dayTrades} />
          <TradesTable trades={dayTrades} runMasterId={runMasterId} runId={runId} />
        </>
      )}
    </div>
  )
}
