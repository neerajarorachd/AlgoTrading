import { Fragment, useCallback, useEffect, useState } from 'react'
import {
  getPatternSummary, listPendingRecommendations, listRecommendationHistory, listSymbols,
} from '../api/client.js'
import { getSocket, roomFor, subscribeRooms, unsubscribeRooms } from '../api/ws.js'

const RECOMMENDATIONS_ROOM = 'recommendations'

function formatPct(value) {
  return value == null ? '—' : `${(value * 100).toFixed(1)}%`
}

// What actually happened after the signal: WIN/LOSS once its own checkpoint
// has passed (price moved with/against the predicted direction), else how
// far along it is. Rejected rows are graded too — that's how a rule's false
// negatives get measured.
function OutcomeCell({ outcome }) {
  if (!outcome) return <span style={{ color: '#999' }}>pending</span>
  // which of the recommendation's own suggested levels price reached first
  const hit = outcome.first_hit === 'target' ? 'target hit' : outcome.first_hit === 'sl' ? 'SL hit' : null
  const hitEl = hit && (
    <span style={{ color: outcome.first_hit === 'target' ? '#2f6f4f' : '#b03030', marginLeft: 6, fontSize: 12 }}>
      {hit}
    </span>
  )
  if (outcome.win == null) {
    return <span style={{ color: '#999' }}>{outcome.candles_observed}/{outcome.checkpoint} candles{hitEl}</span>
  }
  return (
    <span style={{ color: outcome.win ? '#2f6f4f' : '#b03030', fontWeight: 600 }}>
      {outcome.win ? 'WIN' : 'LOSS'} <span style={{ fontWeight: 400 }}>{formatPct(outcome.checkpoint_pct)}</span>
      {hitEl}
    </span>
  )
}

// Per-pattern history roll-up. "Win %" is over recommended rows whose
// checkpoint has passed; "Rejected win %" is the same over rows a rule
// rejected — high there means the rules are discarding winners.
function PatternSummaryTable({ rows, onSelect }) {
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
      <thead>
        <tr style={{ textAlign: 'left', borderBottom: '2px solid #ccc' }}>
          <th style={{ padding: 6 }}>Pattern</th>
          <th style={{ padding: 6 }}>Fired</th>
          <th style={{ padding: 6 }}>Recommended</th>
          <th style={{ padding: 6 }}>Rejected</th>
          <th style={{ padding: 6 }}>…by a rule</th>
          <th style={{ padding: 6 }}>Graded</th>
          <th style={{ padding: 6 }}>Win %</th>
          <th style={{ padding: 6 }}>Rejected win %</th>
          <th style={{ padding: 6 }}>Target hit</th>
          <th style={{ padding: 6 }}>SL hit</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((p) => (
          <tr
            key={p.pattern} onClick={() => onSelect(p.pattern)}
            style={{ cursor: 'pointer', borderBottom: '1px solid #eee' }}
          >
            <td style={{ padding: 6 }}>{p.pattern}</td>
            <td style={{ padding: 6 }}>{p.total}</td>
            <td style={{ padding: 6 }}>{p.recommended}</td>
            <td style={{ padding: 6 }}>{p.rejected}</td>
            <td style={{ padding: 6 }}>{p.rule_rejected}</td>
            <td style={{ padding: 6 }}>{p.graded}</td>
            <td style={{ padding: 6 }}>{formatPct(p.win_pct)}</td>
            <td style={{ padding: 6 }}>{formatPct(p.rejected_win_pct)} <span style={{ color: '#999' }}>({p.rejected_graded})</span></td>
            <td style={{ padding: 6 }}>{p.target_hits}</td>
            <td style={{ padding: 6 }}>{p.sl_hits}</td>
          </tr>
        ))}
        {rows.length === 0 && (
          <tr><td colSpan={10} style={{ padding: 6, color: '#888' }}>No history yet</td></tr>
        )}
      </tbody>
    </table>
  )
}

function formatTs(iso) {
  if (!iso) return '—'
  // Explicit Asia/Kolkata rather than the browser's own local timezone —
  // this is an NSE-only system, so times should always read as IST
  // regardless of where the page happens to be viewed from.
  return new Date(iso).toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true,
  }) + ' IST'
}

export default function Recommendations() {
  const [recommendations, setRecommendations] = useState([])
  const [symbolsById, setSymbolsById] = useState({}) // instrument id -> {symbol, exchange}
  const [liveTicks, setLiveTicks] = useState({}) // symbol -> tick payload (for LTP)
  const [error, setError] = useState(null)
  const [expandedId, setExpandedId] = useState(null)
  // History (every signal, any status) is the more useful default — a
  // recommendation's actionable window is often just ~1 candle, so
  // "Pending" alone looks empty most of the time even on an active day.
  const [view, setView] = useState('history')
  const [patternFilter, setPatternFilter] = useState(null)
  const [patternRows, setPatternRows] = useState([])

  const refresh = useCallback(() => {
    if (view === 'patterns') {
      getPatternSummary().then(setPatternRows).catch((e) => setError(e.message))
      return
    }
    const request = view === 'pending'
      ? listPendingRecommendations()
      : listRecommendationHistory(undefined, undefined, patternFilter)
    request.then(setRecommendations).catch((e) => setError(e.message))
  }, [view, patternFilter])

  useEffect(() => {
    refresh()
  }, [refresh])

  useEffect(() => {
    listSymbols()
      .then((symbols) => setSymbolsById(Object.fromEntries(symbols.map((s) => [s.id, s]))))
      .catch((e) => setError(e.message))
  }, [])

  // LTP for whichever instruments actually appear in the current list — joins
  // each one's own tick room (same room MarketWatch uses) so the price stays
  // live without a separate polling loop. Re-syncs room membership whenever
  // the visible instrument set changes (new symbol appears in the queue).
  useEffect(() => {
    const rooms = [...new Set(recommendations.map((r) => symbolsById[r.instrument_id]).filter(Boolean)
      .map((s) => roomFor(s.exchange, s.symbol)))]
    if (rooms.length === 0) return
    subscribeRooms(rooms)
    return () => unsubscribeRooms(rooms)
  }, [recommendations, symbolsById])

  useEffect(() => {
    const socket = getSocket()
    const onTick = (payload) => setLiveTicks((prev) => ({ ...prev, [payload.symbol]: payload }))
    socket.on('tick', onTick)
    return () => socket.off('tick', onTick)
  }, [])

  // Live updates over the shared "recommendations" WebSocket room (see
  // backend/api/ws_live.py's broadcast_recommendation_created) — a fixed
  // global room, not per-instrument like ticks/depth/candles, since this
  // page watches the whole queue across instruments. No polling needed:
  // recommendation_created events arrive the moment RS1 queues one.
  useEffect(() => {
    subscribeRooms([RECOMMENDATIONS_ROOM])
    const socket = getSocket()
    function onCreated(row) {
      setRecommendations((prev) => {
        if (prev.some((r) => r.id === row.id)) return prev
        return [row, ...prev]
      })
    }
    socket.on('recommendation_created', onCreated)
    return () => {
      socket.off('recommendation_created', onCreated)
      unsubscribeRooms([RECOMMENDATIONS_ROOM])
    }
  }, [])

  return (
    <div style={{ padding: 16, maxWidth: 1100, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', flexWrap: 'wrap', gap: 8 }}>
        <div>
          <h2 style={{ marginBottom: 0 }}>Recommendations</h2>
          <p style={{ color: '#666', marginTop: 4 }}>
            {view === 'pending'
              ? 'Still-actionable signals only — a recommendation\'s window is often just ~1 candle.'
              : 'Every signal detected, any status — updates live as new candles close.'}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 4 }}>
          <button
            onClick={() => setView('history')}
            style={{ fontWeight: view === 'history' ? 'bold' : 'normal' }}
          >
            History
          </button>
          <button
            onClick={() => setView('pending')}
            style={{ fontWeight: view === 'pending' ? 'bold' : 'normal' }}
          >
            Pending
          </button>
          <button
            onClick={() => setView('patterns')}
            style={{ fontWeight: view === 'patterns' ? 'bold' : 'normal' }}
          >
            By pattern
          </button>
        </div>
      </div>

      {error && <div style={{ color: 'crimson', marginBottom: 8 }}>{error}</div>}

      {view === 'history' && patternFilter && (
        <div style={{ marginBottom: 8 }}>
          Pattern: <b>{patternFilter}</b>{' '}
          <button onClick={() => setPatternFilter(null)}>Clear filter</button>
        </div>
      )}

      {view === 'patterns' && (
        <PatternSummaryTable
          rows={patternRows}
          onSelect={(pattern) => { setPatternFilter(pattern); setView('history') }}
        />
      )}

      {view !== 'patterns' && (
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ textAlign: 'left', borderBottom: '2px solid #ccc' }}>
            <th style={{ padding: 6 }}>Instrument</th>
            <th style={{ padding: 6 }}>Candle</th>
            <th style={{ padding: 6 }}>LTP</th>
            <th style={{ padding: 6 }}>Pattern</th>
            <th style={{ padding: 6 }}>Direction</th>
            <th style={{ padding: 6 }}>Window</th>
            <th style={{ padding: 6 }}>Win %</th>
            <th style={{ padding: 6 }}>Wilson score</th>
            <th style={{ padding: 6 }}>Status</th>
            <th style={{ padding: 6 }}>Outcome</th>
            <th style={{ padding: 6 }}>Expires</th>
          </tr>
        </thead>
        <tbody>
          {recommendations.map((r) => (
            <Fragment key={r.id}>
              <tr
                onClick={() => setExpandedId(expandedId === r.id ? null : r.id)}
                style={{
                  cursor: 'pointer', borderBottom: '1px solid #eee',
                  background: expandedId === r.id ? '#eef3fc' : undefined,
                }}
              >
                <td style={{ padding: 6 }}>{symbolsById[r.instrument_id]?.symbol || `#${r.instrument_id}`}</td>
                <td style={{ padding: 6 }}>{r.timeframe}</td>
                <td style={{ padding: 6, fontVariantNumeric: 'tabular-nums' }}>
                  {liveTicks[symbolsById[r.instrument_id]?.symbol]?.ltp?.toFixed(2) ?? '—'}
                </td>
                <td style={{ padding: 6 }}>{r.pattern}</td>
                <td style={{ padding: 6, color: r.direction === 'bull' ? '#2f6f4f' : '#b03030' }}>
                  {r.direction}
                </td>
                <td style={{ padding: 6 }}>{r.window_kind || '—'}</td>
                <td style={{ padding: 6 }}>{formatPct(r.win_pct)}</td>
                <td style={{ padding: 6 }}>{r.wilson_score?.toFixed(3) ?? '—'}</td>
                <td style={{ padding: 6 }}>{r.status}</td>
                <td style={{ padding: 6 }}><OutcomeCell outcome={r.outcome} /></td>
                <td style={{ padding: 6 }}>{formatTs(r.expires_at)}</td>
              </tr>
              {expandedId === r.id && (
                <tr style={{ background: '#fafafa' }}>
                  <td colSpan={11} style={{ padding: 12, fontSize: 13, color: '#444' }}>
                    <div>Entry price: {r.entry_price} · Detected: {formatTs(r.detected_ts)}</div>
                    {r.suggested_sl_price != null && (
                      <div>
                        Suggested: qty {r.suggested_quantity ?? '— (no capital to size against)'}
                        {' '}· SL {r.suggested_sl_price} · Target {r.suggested_target_price}
                      </div>
                    )}
                    {r.rule_note && <div>Rules: {r.rule_note}</div>}
                    {r.outcome && (
                      <div>
                        After {r.outcome.candles_observed} candles — moves at
                        {' '}{[5, 10, 15, 20, 30].map((n) => `${n}: ${formatPct(r.outcome.pct_change[n])}`).join(' · ')}
                        {' '}· best {formatPct(r.outcome.max_favorable_pct)} / worst {formatPct(r.outcome.max_adverse_pct)}
                      </div>
                    )}
                    <div>Sample: {r.win_count}/{r.sample_count} wins at checkpoint {r.qualifying_checkpoint}</div>
                    <div>
                      Signal context — RSI: {r.signal_rsi_state || '—'} ({r.signal_rsi_trend || '—'}),
                      {' '}MACD: {r.signal_macd_state || '—'} ({r.signal_macd_trend || '—'}),
                      {' '}Stoch: {r.signal_stoch_state || '—'} ({r.signal_stoch_trend || '—'})
                      {' '}— context only, never gates this recommendation
                    </div>
                    <div>Guiding scenario #{r.guiding_scenario_id ?? '—'}</div>
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
          {recommendations.length === 0 && (
            <tr>
              <td colSpan={11} style={{ padding: 6, color: '#888' }}>
                {view === 'pending' ? 'No pending recommendations' : 'No recommendations recorded yet'}
              </td>
            </tr>
          )}
        </tbody>
      </table>
      )}
    </div>
  )
}
