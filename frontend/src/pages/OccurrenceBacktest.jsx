import { useEffect, useMemo, useRef, useState } from 'react'
import {
  createOccurrenceBacktestRun, getIntensityAnalysis, getOccurrences, getWatchlist,
  listStrategyElements, listSymbols, listWatchlists,
} from '../api/client.js'

const TIMEFRAMES = ['1min', '3min', '5min', '1day']

function todayIso() {
  return new Date().toISOString().slice(0, 10)
}

function yearAgoIso() {
  const d = new Date()
  d.setFullYear(d.getFullYear() - 1)
  return d.toISOString().slice(0, 10)
}

// Combines an odd/even-length list of numbers into its median — plain JS
// equivalent of the backend's statistics.median, used here to recombine
// medians correctly across a watchlist's several instruments (concatenate
// the RAW per-occurrence values first, then take one median of the whole
// pool — averaging several already-computed per-instrument medians would
// quietly misweight instruments with fewer occurrences).
function median(values) {
  if (!values || values.length === 0) return null
  const sorted = [...values].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
}

function aggregateAcrossInstruments(perInstrumentResults) {
  const totals = { total: 0, bullish: 0, bearish: 0, neutral: 0 }
  const patternMap = new Map()
  for (const result of perInstrumentResults) {
    totals.total += result.total
    totals.bullish += result.bullish
    totals.bearish += result.bearish
    totals.neutral += result.neutral
    for (const p of result.patterns) {
      const existing = patternMap.get(p.pattern) || {
        pattern: p.pattern, activity_type: p.activity_type, direction: p.direction,
        expected_result: p.expected_result, count: 0,
        intensity_values: [], up_values: [], down_values: [], range_values: [],
        actual_result_matched: 0, actual_result_total: 0,
      }
      existing.count += p.count
      existing.intensity_values.push(...(p.intensity_values || []))
      existing.up_values.push(...(p.up_values || []))
      existing.down_values.push(...(p.down_values || []))
      existing.range_values.push(...(p.range_values || []))
      existing.actual_result_matched += p.actual_result_matched || 0
      existing.actual_result_total += p.actual_result_total || 0
      patternMap.set(p.pattern, existing)
    }
  }
  const patterns = [...patternMap.values()]
    .map((p) => ({
      ...p,
      intensity_median: median(p.intensity_values),
      up_median_pct: median(p.up_values),
      down_median_pct: median(p.down_values),
      range_median_pct: median(p.range_values),
      actual_result_pct: p.actual_result_total ? p.actual_result_matched / p.actual_result_total : null,
    }))
    .sort((a, b) => b.count - a.count)
  return { ...totals, patterns }
}

export default function OccurrenceBacktest() {
  const [elements, setElements] = useState([])
  const [symbols, setSymbols] = useState([])
  const [watchlists, setWatchlists] = useState([])
  const [scopeMode, setScopeMode] = useState('instrument')
  const [instrumentId, setInstrumentId] = useState('')
  const [watchlistId, setWatchlistId] = useState('')
  const [timeframes, setTimeframes] = useState(['1min'])
  const [selectedPatterns, setSelectedPatterns] = useState(new Set())
  const [patternFilter, setPatternFilter] = useState('')
  const [from, setFrom] = useState(yearAgoIso())
  const [to, setTo] = useState(todayIso())
  const [running, setRunning] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null) // { totals..., patterns, perInstrument? }
  const [recordedRunMasterId, setRecordedRunMasterId] = useState(null)
  const [recordingNote, setRecordingNote] = useState(null)

  useEffect(() => {
    listStrategyElements().then((rows) => setElements(rows.filter((r) => r.element_type === 'event'))).catch((e) => setError(e.message))
    listSymbols().then(setSymbols).catch((e) => setError(e.message))
    listWatchlists().then(setWatchlists).catch((e) => setError(e.message))
  }, [])

  const visibleElements = useMemo(() => {
    const q = patternFilter.trim().toLowerCase()
    return elements.filter((e) => !q || e.code.toLowerCase().includes(q))
  }, [elements, patternFilter])

  function toggleTimeframe(tf) {
    setTimeframes((cur) => (cur.includes(tf) ? cur.filter((t) => t !== tf) : [...cur, tf]))
  }

  function togglePattern(code) {
    setSelectedPatterns((cur) => {
      const next = new Set(cur)
      if (next.has(code)) next.delete(code)
      else next.add(code)
      return next
    })
  }

  function selectAllVisible() {
    setSelectedPatterns((cur) => new Set([...cur, ...visibleElements.map((e) => e.code)]))
  }

  function clearSelection() {
    setSelectedPatterns(new Set())
  }

  // Records this run as a real BacktestRun (mode="occurrence_count") so it
  // shows up in backtest history instead of being thrown away — closes
  // "occurance backtest is not added to the btrun" (2026-09-15). Scoped to
  // the common single-instrument, single-timeframe case (the vast majority
  // of runs) to keep this to one small session per click rather than
  // fanning out across a whole watchlist/multi-timeframe combination;
  // never blocks or fails the main occurrence display — recording is a
  // side effect, not the point of clicking Run.
  function recordAsBacktestRun(patterns, instrumentIdNum, timeframe) {
    if (scopeMode !== 'instrument' || timeframes.length !== 1 || patterns.length === 0) {
      setRecordedRunMasterId(null)
      setRecordingNote(null)
      return
    }
    const sessionName = `Occurrence backtest / ${timeframe} / ${patterns.length} pattern(s)`
    let chain = Promise.resolve(null)
    for (const p of patterns) {
      chain = chain.then((runMasterId) => (
        createOccurrenceBacktestRun(instrumentIdNum, timeframe, p.pattern, from, to, 'occurrence_backtest', runMasterId, sessionName)
          .then((res) => res.run_master_id)
      ))
    }
    chain
      .then((runMasterId) => {
        setRecordedRunMasterId(runMasterId)
        setRecordingNote(`Recorded as backtest run #${runMasterId} (${patterns.length} pattern${patterns.length === 1 ? '' : 's'})`)
      })
      .catch((e) => setRecordingNote(`Not recorded: ${e.message}`))
  }

  function handleRun(e) {
    e.preventDefault()
    if (timeframes.length === 0) { setError('Select at least one timeframe'); return }
    if (scopeMode === 'instrument' && !instrumentId) { setError('Select an instrument'); return }
    if (scopeMode === 'watchlist' && !watchlistId) { setError('Select a watchlist'); return }

    const patterns = selectedPatterns.size ? [...selectedPatterns] : null
    setRunning(true)
    setError(null)
    setResult(null)
    setRecordedRunMasterId(null)
    setRecordingNote(null)

    const run = scopeMode === 'instrument'
      ? getOccurrences(instrumentId, timeframes, from, to, patterns).then((r) => ({ perInstrument: [{ ...r, instrument_id: Number(instrumentId) }] }))
      : getWatchlist(watchlistId).then((wl) => (
        Promise.all(wl.members.map((m) => getOccurrences(m.instrument_id, timeframes, from, to, patterns)))
          .then((results) => ({ perInstrument: results }))
      ))

    run
      .then(({ perInstrument }) => {
        const aggregate = aggregateAcrossInstruments(perInstrument)
        setResult({ perInstrument, aggregate })
        recordAsBacktestRun(aggregate.patterns, Number(instrumentId), timeframes[0])
      })
      .catch((e2) => setError(e2.message))
      .finally(() => setRunning(false))
  }

  return (
    <div style={{ padding: 16, maxWidth: 1100, margin: '0 auto' }}>
      <h2>Occurrence Backtest</h2>
      <p style={{ color: '#666', marginTop: -8 }}>
        Counts how often a formation, candlestick pattern, or crossover already occurred over a period —
        no trade simulation, just frequency.
      </p>

      <form onSubmit={handleRun} style={{ padding: 16, border: '1px solid #ccc', borderRadius: 6, marginBottom: 20 }}>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 12 }}>
          <label>
            Scope<br />
            <select value={scopeMode} onChange={(e) => setScopeMode(e.target.value)}>
              <option value="instrument">Single instrument</option>
              <option value="watchlist">Watchlist</option>
            </select>
          </label>
          {scopeMode === 'instrument' ? (
            <label>
              Instrument<br />
              <select value={instrumentId} onChange={(e) => setInstrumentId(e.target.value)}>
                <option value="">Select…</option>
                {symbols.map((s) => <option key={s.id} value={s.id}>{s.symbol}</option>)}
              </select>
            </label>
          ) : (
            <label>
              Watchlist<br />
              <select value={watchlistId} onChange={(e) => setWatchlistId(e.target.value)}>
                <option value="">Select…</option>
                {watchlists.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
              </select>
            </label>
          )}
          <label>
            From<br />
            <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
          </label>
          <label>
            To<br />
            <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
          </label>
        </div>

        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 13, marginBottom: 4 }}>Timeframes</div>
          {TIMEFRAMES.map((tf) => (
            <label key={tf} style={{ marginRight: 12 }}>
              <input type="checkbox" checked={timeframes.includes(tf)} onChange={() => toggleTimeframe(tf)} /> {tf}
            </label>
          ))}
        </div>

        <div style={{ marginBottom: 12 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
            <span style={{ fontSize: 13 }}>
              Formations / crossovers / patterns ({selectedPatterns.size ? selectedPatterns.size : 'all'})
            </span>
            <input
              placeholder="filter…" value={patternFilter}
              onChange={(e) => setPatternFilter(e.target.value)} style={{ width: 160 }}
            />
            <button type="button" onClick={selectAllVisible}>Select shown</button>
            <button type="button" onClick={clearSelection}>Clear (= all)</button>
          </div>
          <div style={{
            display: 'flex', flexWrap: 'wrap', gap: '2px 14px', maxHeight: 160, overflowY: 'auto',
            border: '1px solid #eee', borderRadius: 4, padding: 8,
          }}>
            {visibleElements.map((el) => (
              <label key={el.code} style={{ fontSize: 13, whiteSpace: 'nowrap' }} title={el.description}>
                <input
                  type="checkbox" checked={selectedPatterns.has(el.code)}
                  onChange={() => togglePattern(el.code)}
                /> {el.code}
              </label>
            ))}
            {visibleElements.length === 0 && <span style={{ color: '#888' }}>No matches</span>}
          </div>
        </div>

        {error && <div style={{ color: 'crimson', marginBottom: 8 }}>{error}</div>}
        <button type="submit" disabled={running}>{running ? 'Counting…' : 'Run'}</button>
      </form>

      {recordingNote && (
        <div style={{ fontSize: 12, color: recordedRunMasterId ? '#1a7f37' : '#888', marginBottom: 8 }}>
          {recordingNote}
        </div>
      )}

      {result && (
        <OccurrenceResult
          result={result} scopeMode={scopeMode} symbols={symbols}
          timeframes={timeframes} from={from} to={to}
          instrumentId={scopeMode === 'instrument' ? Number(instrumentId) : null}
        />
      )}
    </div>
  )
}

function StatCard({ label, value, color }) {
  return (
    <div style={{ border: '1px solid #ddd', borderRadius: 6, padding: '10px 16px', minWidth: 100 }}>
      <div style={{ fontSize: 12, color: '#888' }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 600, color }}>{value}</div>
    </div>
  )
}

// Click-to-sort state for one table: tracks which column key is active and
// which direction, toggling asc -> desc -> asc on repeated clicks of the
// same column (switching to a different column always starts at asc).
function useSort(defaultKey = null) {
  const [key, setKey] = useState(defaultKey)
  const [dir, setDir] = useState('asc')
  function toggle(nextKey) {
    if (key === nextKey) setDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    else { setKey(nextKey); setDir('asc') }
  }
  return { key, dir, toggle }
}

function SortableTh({ label, sortKey, sort, ...rest }) {
  const active = sort.key === sortKey
  return (
    <th
      style={{ padding: 4, cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap' }}
      onClick={() => sort.toggle(sortKey)}
      {...rest}
    >
      {label}
      <span style={{
        display: 'inline-block', marginLeft: 5, fontSize: 11,
        color: active ? '#1a1a1a' : '#bbb', fontWeight: active ? 700 : 400,
      }}>
        {active ? (sort.dir === 'asc' ? '▲' : '▼') : '▲▼'}
      </span>
    </th>
  )
}

// Percent formatters — null means "no PatternOutcome analysis yet for this
// pattern/instrument" (pattern_outcome_analysis.py hasn't run), shown as
// an em dash rather than 0% (which would misleadingly claim "definitely
// didn't work" instead of "unknown").
function fmtPct(v, digits = 1) {
  return v == null ? '—' : `${(v * 100).toFixed(digits)}%`
}

function fmtIntensity(v) {
  return v == null ? '—' : v.toFixed(2)
}

function fmtActualResult(p) {
  if (!p.actual_result_total) return '—'
  return `${fmtPct(p.actual_result_pct, 0)} (${p.actual_result_matched}/${p.actual_result_total})`
}

function fmtRange(p) {
  if (p.range_median_pct == null) return '—'
  return `${fmtPct(p.range_median_pct)} (${fmtPct(p.up_median_pct)} / ${fmtPct(p.down_median_pct)})`
}

// Numeric sort keys need a null-safe accessor — a pattern with no outcome
// analysis yet (null medians) should sort as the lowest value, not throw
// off the comparison or crash on `null - null`.
const NUMERIC_SORT_ACCESSORS = {
  count: (p) => p.count,
  intensity_median: (p) => p.intensity_median ?? -Infinity,
  actual_result_pct: (p) => p.actual_result_pct ?? -Infinity,
  range_median_pct: (p) => p.range_median_pct ?? -Infinity,
}

// Once a row is opened, it stays MOUNTED and closing it only toggles CSS
// display — never unmounts it, so re-opening the same pattern's intensity
// panel doesn't re-fetch (same "don't reload what's already fetched"
// preference established on the Backtests page).
function useExpandableRows() {
  const [openId, setOpenId] = useState(null)
  const mountedIds = useRef(new Set())
  function toggle(id) {
    mountedIds.current.add(id)
    setOpenId((cur) => (cur === id ? null : id))
  }
  return { openId, isOpen: (id) => openId === id, isMounted: (id) => mountedIds.current.has(id), toggle }
}

function PatternTable({ patterns, instrumentId, timeframes, from, to }) {
  const sort = useSort()
  const expand = useExpandableRows()
  const canExpand = instrumentId != null

  const sorted = useMemo(() => {
    if (!sort.key) return patterns
    const factor = sort.dir === 'asc' ? 1 : -1
    if (sort.key === 'pattern') {
      return [...patterns].sort((a, b) => a.pattern.localeCompare(b.pattern) * factor)
    }
    const accessor = NUMERIC_SORT_ACCESSORS[sort.key]
    return [...patterns].sort((a, b) => (accessor(a) - accessor(b)) * factor)
  }, [patterns, sort.key, sort.dir])

  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
      <thead>
        <tr style={{ textAlign: 'left', borderBottom: '1px solid #ccc' }}>
          <SortableTh label="Pattern" sortKey="pattern" sort={sort} />
          <th style={{ padding: 4 }}>Kind</th>
          <th style={{ padding: 4 }}>Expected Result</th>
          <SortableTh label="Count" sortKey="count" sort={sort} />
          <SortableTh label="Intensity Median" sortKey="intensity_median" sort={sort} />
          <SortableTh label="Actual Result" sortKey="actual_result_pct" sort={sort} />
          <SortableTh label="Price Range" sortKey="range_median_pct" sort={sort} />
        </tr>
      </thead>
      <tbody>
        {sorted.map((p) => {
          const isOpen = canExpand && expand.isOpen(p.pattern)
          return [
            <tr
              key={p.pattern}
              onClick={canExpand ? () => expand.toggle(p.pattern) : undefined}
              style={{
                borderBottom: isOpen ? 'none' : '1px solid #f0f0f0',
                cursor: canExpand ? 'pointer' : undefined,
                background: isOpen ? '#eef3fc' : undefined,
              }}
              title={canExpand ? 'Click for this pattern\'s intensity-vs-outcome breakdown' : undefined}
            >
              <td style={{ padding: 4 }}>{canExpand ? (isOpen ? '▾ ' : '▸ ') : ''}{p.pattern}</td>
              <td style={{ padding: 4 }}>{p.activity_type}</td>
              <td style={{ padding: 4 }}>{p.expected_result ?? p.direction}</td>
              <td style={{ padding: 4 }}>{p.count}</td>
              <td style={{ padding: 4 }} title="Median of InstrumentActivity.intensity across occurrences">
                {fmtIntensity(p.intensity_median)}
              </td>
              <td style={{ padding: 4 }} title="Share of occurrences where price closed on the expected side of entry 20 candles later">
                {fmtActualResult(p)}
              </td>
              <td style={{ padding: 4 }} title="Median best/worst price seen in the next 20 candles, relative to entry">
                {fmtRange(p)}
              </td>
            </tr>,
            canExpand && expand.isMounted(p.pattern) && (
              <tr key={`${p.pattern}-detail`} style={{ display: isOpen ? undefined : 'none', borderBottom: '1px solid #f0f0f0' }}>
                <td colSpan={7} style={{ padding: '4px 4px 16px 20px', background: '#fafbfd' }}>
                  <IntensityAnalysisPanel
                    instrumentId={instrumentId} timeframes={timeframes} from={from} to={to} pattern={p.pattern}
                  />
                </td>
              </tr>
            ),
          ]
        })}
        {sorted.length === 0 && (
          <tr><td colSpan={7} style={{ padding: 4, color: '#888' }}>No occurrences in this period</td></tr>
        )}
      </tbody>
    </table>
  )
}

function fmtCheckpointCell(stats) {
  if (!stats || !stats.total) return '—'
  return `${fmtPct(stats.pct, 0)} (${stats.matched}/${stats.total})`
}

const EMPTY_INDICATOR_DIMENSION = { state: [], trend: [] }
const EMPTY_INDICATOR_STATES = { rsi: EMPTY_INDICATOR_DIMENSION, macd: EMPTY_INDICATOR_DIMENSION, stoch: EMPTY_INDICATOR_DIMENSION }

// "analyze 5, 10, 15, 20, 30 candles and share analysis of all of these
// candles linked with intensity" — one pattern's occurrences split into
// intensity bands (lowest to highest), each band's actual-result match
// rate shown at every checkpoint side by side, so a stronger/weaker
// occurrence's real follow-through is visible at a glance. Below that,
// "we will relate these with the state of other indicators like RSI,
// MACD etc." — the same per-checkpoint breakdown, banded by each
// indicator's STATE (oversold/neutral/overbought, bullish/bearish) and
// TREND (increasing/decreasing/flat over the last few candles) at the
// moment the pattern fired.
function IntensityAnalysisPanel({ instrumentId, timeframes, from, to, pattern }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    setData(null)
    setError(null)
    getIntensityAnalysis(instrumentId, timeframes, from, to, pattern)
      .then(setData)
      .catch((e) => setError(e.message))
  }, [instrumentId, timeframes, from, to, pattern])

  if (error) return <div style={{ color: 'crimson' }}>{error}</div>
  if (!data) return <div style={{ color: '#888' }}>Loading…</div>

  const states = data.indicator_states || EMPTY_INDICATOR_STATES
  const hasIndicatorData = ['rsi', 'macd', 'stoch'].some(
    (k) => states[k].state.length || states[k].trend.length,
  )
  if (data.bands.length === 0 && !hasIndicatorData) {
    return (
      <div style={{ color: '#888' }}>
        No PatternOutcome analysis for "{pattern}" on this instrument yet — run
        pattern_outcome_analysis.py for it first.
      </div>
    )
  }

  return (
    <div>
      {data.bands.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 12, color: '#888', marginBottom: 6 }}>
            Occurrences of "{pattern}" split into {data.bands.length} intensity bands (lowest to highest) —
            actual-result match rate at each checkpoint, per band.
          </div>
          <BandTable
            labelHeader="Intensity band"
            labelFor={(b) => `${b.intensity_min.toFixed(2)} – ${b.intensity_max.toFixed(2)}`}
            bands={data.bands} checkpoints={data.checkpoints}
          />
        </div>
      )}

      {hasIndicatorData && (
        <div>
          <div style={{ fontSize: 12, color: '#888', marginBottom: 6 }}>
            Indicator state and trend at the moment "{pattern}" fired.
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <IndicatorDimensionTables title="RSI" dims={states.rsi} checkpoints={data.checkpoints} />
            <IndicatorDimensionTables title="MACD (line vs. signal)" dims={states.macd} checkpoints={data.checkpoints} />
            <IndicatorDimensionTables title="Stochastic %K" dims={states.stoch} checkpoints={data.checkpoints} />
          </div>
        </div>
      )}
    </div>
  )
}

function capitalize(s) {
  return s.charAt(0).toUpperCase() + s.slice(1)
}

function IndicatorDimensionTables({ title, dims, checkpoints }) {
  if (dims.state.length === 0 && dims.trend.length === 0) return null
  return (
    <div>
      <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 4 }}>{title}</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {dims.state.length > 0 && (
          <BandTable
            labelHeader="State" labelFor={(b) => capitalize(b.label)}
            bands={dims.state} checkpoints={checkpoints}
          />
        )}
        {dims.trend.length > 0 && (
          <BandTable
            labelHeader="Trend (last few candles)" labelFor={(b) => capitalize(b.label)}
            bands={dims.trend} checkpoints={checkpoints}
          />
        )}
      </div>
    </div>
  )
}

function BandTable({ labelHeader, labelFor, bands, checkpoints }) {
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
      <thead>
        <tr style={{ textAlign: 'left', borderBottom: '1px solid #ccc' }}>
          <th style={{ padding: 4 }}>{labelHeader}</th>
          <th style={{ padding: 4 }}>Count</th>
          {checkpoints.map((cp) => <th key={cp} style={{ padding: 4 }}>{cp}c Actual</th>)}
          <th style={{ padding: 4 }}>Price Range</th>
        </tr>
      </thead>
      <tbody>
        {bands.map((band, i) => (
          <tr key={i} style={{ borderBottom: '1px solid #f0f0f0' }}>
            <td style={{ padding: 4 }}>{labelFor(band)}</td>
            <td style={{ padding: 4 }}>{band.count}</td>
            {checkpoints.map((cp) => (
              <td key={cp} style={{ padding: 4 }}>{fmtCheckpointCell(band.checkpoints[String(cp)])}</td>
            ))}
            <td style={{ padding: 4 }}>
              {band.range_median_pct == null
                ? '—'
                : `${fmtPct(band.range_median_pct)} (${fmtPct(band.up_median_pct)} / ${fmtPct(band.down_median_pct)})`}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function OccurrenceResult({ result, scopeMode, symbols, timeframes, from, to, instrumentId }) {
  const { aggregate, perInstrument } = result
  const symbolById = useMemo(() => Object.fromEntries(symbols.map((s) => [s.id, s.symbol])), [symbols])
  const [expandedIds, setExpandedIds] = useState(new Set())
  const instrumentSort = useSort()

  const sortedInstruments = useMemo(() => {
    if (!instrumentSort.key) return perInstrument
    const factor = instrumentSort.dir === 'asc' ? 1 : -1
    return [...perInstrument].sort((a, b) => {
      if (instrumentSort.key === 'symbol') {
        const nameA = a.symbol || symbolById[a.instrument_id] || String(a.instrument_id)
        const nameB = b.symbol || symbolById[b.instrument_id] || String(b.instrument_id)
        return nameA.localeCompare(nameB) * factor
      }
      return (a.total - b.total) * factor
    })
  }, [perInstrument, instrumentSort.key, instrumentSort.dir, symbolById])

  function toggleRow(id) {
    setExpandedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <StatCard label="Total occurrences" value={aggregate.total} />
        <StatCard label="Bullish" value={aggregate.bullish} color="#1a7f37" />
        <StatCard label="Bearish" value={aggregate.bearish} color="#c0392b" />
        <StatCard label="Neutral" value={aggregate.neutral} color="#888" />
      </div>

      <div style={{ padding: 16, border: '1px solid #ccc', borderRadius: 6, marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>By pattern{scopeMode === 'watchlist' ? ' (all instruments)' : ''}</h3>
        <PatternTable
          patterns={aggregate.patterns}
          instrumentId={instrumentId} timeframes={timeframes} from={from} to={to}
        />
      </div>

      {scopeMode === 'watchlist' && (
        <div style={{ padding: 16, border: '1px solid #ccc', borderRadius: 6 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', flexWrap: 'wrap', gap: 8 }}>
            <h3 style={{ margin: 0 }}>By instrument</h3>
            <div>
              <button type="button" onClick={() => setExpandedIds(new Set(perInstrument.map((r) => r.instrument_id)))}>
                Expand all
              </button>
              {' '}
              <button type="button" onClick={() => setExpandedIds(new Set())}>Collapse all</button>
            </div>
          </div>
          <div style={{ fontSize: 12, color: '#888', margin: '4px 0 8px' }}>
            Click a row to see that stock's own pattern breakdown.
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ textAlign: 'left', borderBottom: '1px solid #ccc' }}>
                <SortableTh label="Symbol" sortKey="symbol" sort={instrumentSort} />
                <SortableTh label="Total" sortKey="total" sort={instrumentSort} />
                <th style={{ padding: 4 }}>Bullish</th>
                <th style={{ padding: 4 }}>Bearish</th>
                <th style={{ padding: 4 }}>Neutral</th>
              </tr>
            </thead>
            <tbody>
              {sortedInstruments.map((r) => {
                const isOpen = expandedIds.has(r.instrument_id)
                const label = r.symbol || symbolById[r.instrument_id] || r.instrument_id
                return [
                  <tr
                    key={r.instrument_id}
                    onClick={() => toggleRow(r.instrument_id)}
                    style={{
                      borderBottom: isOpen ? 'none' : '1px solid #f0f0f0', cursor: 'pointer',
                      background: isOpen ? '#eef3fc' : undefined,
                    }}
                  >
                    <td style={{ padding: 4 }}>{isOpen ? '▾ ' : '▸ '}{label}</td>
                    <td style={{ padding: 4 }}>{r.total}</td>
                    <td style={{ padding: 4 }}>{r.bullish}</td>
                    <td style={{ padding: 4 }}>{r.bearish}</td>
                    <td style={{ padding: 4 }}>{r.neutral}</td>
                  </tr>,
                  isOpen && (
                    <tr key={`${r.instrument_id}-detail`} style={{ borderBottom: '1px solid #f0f0f0' }}>
                      <td colSpan={5} style={{ padding: '4px 4px 12px 20px', background: '#fafbfd' }}>
                        <PatternTable
                          patterns={r.patterns}
                          instrumentId={r.instrument_id} timeframes={timeframes} from={from} to={to}
                        />
                      </td>
                    </tr>
                  ),
                ]
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
