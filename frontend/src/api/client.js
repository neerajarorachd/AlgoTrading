async function request(path, options) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.error || `Request failed: ${res.status}`)
  }
  if (res.status === 204) return null
  return res.json()
}

export const listSymbols = () => request('/api/symbols')

// Recent events for one instrument (doji formed, MACD crossover, etc, newest
// first) -- the Market Watch per-row events list.
export const getRecentActivities = (instrumentId, limit) => {
  const params = new URLSearchParams({ instrument_id: instrumentId })
  if (limit) params.set('limit', limit)
  return request(`/api/activities/recent?${params.toString()}`)
}

// Today's event counts by category ({candle_pattern, indicator}) -- the
// Market Watch row's "5 candle formations, 3 indicator crossovers" badges.
export const getActivityCounts = (instrumentId) =>
  request(`/api/activities/counts?instrument_id=${instrumentId}`)

export const searchInstruments = (query, exchange, segment) => {
  const params = new URLSearchParams({ q: query, exchange, segment })
  return request(`/api/instruments/search?${params.toString()}`)
}

export const addSymbol = (symbol, exchange, segment) =>
  request('/api/symbols', { method: 'POST', body: JSON.stringify({ symbol, exchange, segment }) })

export const removeSymbol = (id) => request(`/api/symbols/${id}`, { method: 'DELETE' })

export const getCandles = (symbol, exchangeSegment, timeframe, from, to) => {
  const params = new URLSearchParams({ symbol, exchange_segment: exchangeSegment, timeframe })
  if (from) params.set('from', from)
  if (to) params.set('to', to)
  return request(`/api/candles?${params.toString()}`)
}

export const listStrategyElements = () => request('/api/strategy-elements')

export const getStrategyFields = () => request('/api/strategy-fields')

export const listRecommendationSystems = () => request('/api/recommendation-systems')

export const getRecommendationSystem = (id) => request(`/api/recommendation-systems/${id}`)

export const getPatternDefaults = (id) => request(`/api/recommendation-systems/${id}/pattern-defaults`)

export const putPatternDefaults = (id, defaults) =>
  request(`/api/recommendation-systems/${id}/pattern-defaults`, { method: 'PUT', body: JSON.stringify({ defaults }) })

export const updateRecommendationSystem = (id, fields) =>
  request(`/api/recommendation-systems/${id}`, { method: 'PUT', body: JSON.stringify(fields) })

export const listStrategies = () => request('/api/strategies')

export const getStrategy = (id) => request(`/api/strategies/${id}`)

export const createStrategy = (strategy) =>
  request('/api/strategies', { method: 'POST', body: JSON.stringify(strategy) })

export const updateStrategy = (id, strategy) =>
  request(`/api/strategies/${id}`, { method: 'PUT', body: JSON.stringify(strategy) })

export const deleteStrategy = (id) => request(`/api/strategies/${id}`, { method: 'DELETE' })

export const listWatchlists = () => request('/api/watchlists')

export const getWatchlist = (id) => request(`/api/watchlists/${id}`)

export const createWatchlist = (watchlist) =>
  request('/api/watchlists', { method: 'POST', body: JSON.stringify(watchlist) })

export const updateWatchlist = (id, watchlist) =>
  request(`/api/watchlists/${id}`, { method: 'PUT', body: JSON.stringify(watchlist) })

export const deleteWatchlist = (id) => request(`/api/watchlists/${id}`, { method: 'DELETE' })

export const addWatchlistInstrument = (watchlistId, instrumentId) =>
  request(`/api/watchlists/${watchlistId}/instruments`, {
    method: 'POST', body: JSON.stringify({ instrument_id: instrumentId }),
  })

export const removeWatchlistInstrument = (watchlistId, instrumentId) =>
  request(`/api/watchlists/${watchlistId}/instruments/${instrumentId}`, { method: 'DELETE' })

// Registers every member of this watchlist into Market Watch's live feed --
// reactivates any member whose SubscribedSymbol was later unregistered, then
// backfills+subscribes it. Same SubscribedSymbol table Market Watch itself
// reads from, so a member shows up there automatically.
export const subscribeWatchlist = (watchlistId) =>
  request(`/api/watchlists/${watchlistId}/subscribe`, { method: 'POST' })

export const getHistoricalDataCoverage = (instrumentId, timeframe) => {
  const params = new URLSearchParams({ instrument_id: instrumentId, timeframe })
  return request(`/api/historical-data/coverage?${params.toString()}`)
}

export const backfillHistoricalData = (instrumentId, timeframe, startDate, endDate) =>
  request('/api/historical-data/backfill', {
    method: 'POST',
    body: JSON.stringify({
      instrument_id: instrumentId, timeframe, start_date: startDate, end_date: endDate,
    }),
  })

export const listBacktestRunMasters = () => request('/api/backtests/runs')

export const getBacktestRunMaster = (runMasterId) => request(`/api/backtests/runs/${runMasterId}`)

export const createBacktestRun = (payload) =>
  request('/api/backtests/runs', { method: 'POST', body: JSON.stringify(payload) })

export const getBacktestTrades = (runMasterId, runId) =>
  request(`/api/backtests/runs/${runMasterId}/runs/${runId}/trades`)

export const getBacktestDayResults = (runMasterId, runId) =>
  request(`/api/backtests/runs/${runMasterId}/runs/${runId}/day-results`)

export const getTradeExecutionPath = (runMasterId, runId, tradeId, padding) => {
  const suffix = padding != null ? `?padding=${padding}` : ''
  return request(`/api/backtests/runs/${runMasterId}/runs/${runId}/trades/${tradeId}/path${suffix}`)
}

export const getRunDayChart = (runMasterId, runId, date) =>
  request(`/api/backtests/runs/${runMasterId}/runs/${runId}/day-chart?date=${date}`)

export const getOccurrences = (instrumentId, timeframes, from, to, patterns) => {
  const params = new URLSearchParams({
    instrument_id: instrumentId, timeframes: timeframes.join(','), from, to,
  })
  if (patterns && patterns.length) params.set('patterns', patterns.join(','))
  return request(`/api/activities/occurrences?${params.toString()}`)
}

export const getIntensityAnalysis = (instrumentId, timeframes, from, to, pattern, bands = 3) => {
  const params = new URLSearchParams({
    instrument_id: instrumentId, timeframes: timeframes.join(','), from, to, pattern, bands,
  })
  return request(`/api/activities/intensity-analysis?${params.toString()}`)
}

// Records an occurrence backtest as a real BacktestRun (closes the "occurance
// backtest is not added to the btrun" gap, 2026-09-15) -- pass runMasterId to
// chain additional (instrument, timeframe, pattern) runs into the SAME
// session, same convention as createBacktestRun's own run_master_id.
export const createOccurrenceBacktestRun = (instrumentId, timeframe, pattern, from, to, source, runMasterId, sessionName) =>
  request('/api/backtests/occurrence-runs', {
    method: 'POST',
    body: JSON.stringify({
      instrument_id: instrumentId, timeframe, pattern, start: from, end: to,
      source, run_master_id: runMasterId, session_name: sessionName,
    }),
  })

export const listPendingRecommendations = (instrumentId, timeframe) => {
  const params = new URLSearchParams()
  if (instrumentId) params.set('instrument_id', instrumentId)
  if (timeframe) params.set('timeframe', timeframe)
  const qs = params.toString()
  return request(`/api/recommendations/pending${qs ? `?${qs}` : ''}`)
}

// Every recommendation regardless of status/expiry — see
// LibRecommendations.list_recent's docstring: most of a day's signals are
// correctly already expired by the time anyone looks, so this is the view
// for "what fired today" rather than "what's still actionable right now".
export const listRecommendationHistory = (instrumentId, timeframe, pattern) => {
  const params = new URLSearchParams()
  if (instrumentId) params.set('instrument_id', instrumentId)
  if (timeframe) params.set('timeframe', timeframe)
  if (pattern) params.set('pattern', pattern)
  const qs = params.toString()
  return request(`/api/recommendations/history${qs ? `?${qs}` : ''}`)
}

// Per-pattern roll-up: counts by status, rule rejections, and win% of
// recommended vs rejected rows (recommendation_outcomes.pattern_summary).
export const getPatternSummary = () => request('/api/recommendations/pattern-summary')

export const getBestPreview = (slots = 5, instrumentId, timeframe) => {
  const params = new URLSearchParams({ slots })
  if (instrumentId) params.set('instrument_id', instrumentId)
  if (timeframe) params.set('timeframe', timeframe)
  return request(`/api/recommendations/best-preview?${params.toString()}`)
}

export const generateBestPatternsStrategy = (instrumentId, timeframe, strategyName, topN, strategyType) =>
  request('/api/strategies/generate-best-patterns', {
    method: 'POST',
    body: JSON.stringify({
      instrument_id: instrumentId, timeframe, strategy_name: strategyName,
      top_n: topN, strategy_type: strategyType,
    }),
  })

// Engine Settings admin -- promotes a backtest-proven ActivityEngine
// parameter (e.g. swing_lookback) into the one value live trading's shared
// engine actually reads.
export const listEngineSettings = () => request('/api/engine-settings')

export const updateEngineSetting = (key, value) =>
  request(`/api/engine-settings/${key}`, { method: 'PUT', body: JSON.stringify({ value }) })

export const resetEngineSetting = (key) => request(`/api/engine-settings/${key}`, { method: 'DELETE' })

// Per-instrument "what to watch" on Market Watch -- only EXCLUSIONS are
// stored server-side, so a fresh instrument's `excluded` list is empty
// (watches everything) until the user turns something off.
export const getWatchSelection = (instrumentId) => request(`/api/instruments/${instrumentId}/watch-selection`)

export const putWatchSelection = (instrumentId, excluded) =>
  request(`/api/instruments/${instrumentId}/watch-selection`, { method: 'PUT', body: JSON.stringify({ excluded }) })

// Colored-cell grid: weighted bull/bear momentum score per instrument from
// pattern activity in the last `window` candles -- see backend/watch_scoring.py.
export const getWatchScores = (timeframe = '3min', window) => {
  const params = new URLSearchParams({ timeframe })
  if (window) params.set('window', window)
  return request(`/api/watch-scores?${params.toString()}`)
}

// Buy/sell suggestion popover -- display-only, no order placement (see
// backend/watch_order_popup.py's own module docstring).
export const getOrderPopup = (instrumentId, timeframe = '3min') =>
  request(`/api/instruments/${instrumentId}/order-popup?timeframe=${timeframe}`)
