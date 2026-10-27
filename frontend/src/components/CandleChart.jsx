// Live chart widget -- candles + VWAP/Bollinger Bands/EMA(5,14,21,50)/
// MA(21,50) overlays, a Volume histogram squeezed into the price pane's own
// floor, classic pivot points, RSI/MACD/Stochastic sub-panels sharing one
// timescale (only the bottom-most VISIBLE one shows time labels), today's
// fired patterns/signals as price-pane markers, and a per-graph indicator
// picker -- all backed by real data (backend/api/routes_candles.py's
// get_candle_indicators/get_candle_pivots/get_candle_markers). See memory:
// live_indicators_phase1_priority. Ported from a Claude Artifact preview
// (iterated and screenshot-reviewed there first, per this project's
// artifact-first workflow) -- CandleChart_old.jsx is the frozen pre-port
// widget, kept for reference, not wired into anything.
//
// Depth button/props (depthOpen/onToggleDepth/depthMode/onCycleDepth)
// unchanged from before -- GraphWindow.jsx still owns the actual
// DepthPanel's rendering/positioning. The new Indicators picker is
// self-contained here instead: it needs direct access to this chart's own
// series objects to toggle visibility, so it doesn't make sense split
// across components the way Depth (a plain data-display panel) does.
// Known follow-up, not done in this pass: stacking the Indicators rail
// vertically with GraphWindow's own Depth panel into one shared column
// (validated in the preview) needs GraphWindow restructuring -- out of
// scope here, which focused on the chart's actual data being real.
//
// Multi-day (2026-10-08, step 4; ALGO-3): opens on the last INITIAL_DAYS sessions
// (scrolled to the current one) and loads MORE_DAYS older sessions each time
// the view nears the left edge. Past days' indicators/markers come from the
// DB (filled by backend/feed/chart_history_fill.py); the current session's
// are re-fetched on every closed candle, older ones only once. Faint
// vertical lines mark day boundaries; marker names show on hover.
import { useEffect, useRef, useState } from 'react'
import { createChart, CrosshairMode, LineStyle } from 'lightweight-charts'
import {
  getCandleSessions, getCandleIndicators, getCandleMarkers, getCandlePivots,
} from '../api/client.js'
import { getSocket } from '../api/ws.js'
import { useTheme } from '../ThemeContext.jsx'
import ChartToolButton from './ChartToolButton.jsx'
import { DEFAULT_BODY_RATIO, ThinCandleSeries, markBoxes, markWidth } from './thinCandleSeries.js'
import { getPref, setPref } from '../preferences.js'
import { openReview } from '../api/client.js'
import IndicatorPanelTabs from './IndicatorPanelTabs.jsx'
import IndicatorStrip from './IndicatorStrip.jsx'
import SignalPicker from './SignalPicker.jsx'
import { prefetchPatterns, prefetchSignals, prefetchStrategies, usePatterns, useSignals } from './patternData.js'
import { eventTime, humanize, indexData, shapesForEvents, shapesToSvg, snapToCandles } from './chartDiagrams.js'

const TIMEFRAMES = ['1min', '3min', '5min']
const MAIN_HEIGHT = 260
const SUB_HEIGHT = 70
const IST_OFFSET_SECONDS = 5.5 * 60 * 60
const INITIAL_DAYS = 21 // previous 20 sessions + today (ALGO-3)
const MORE_DAYS = 10
const DIAGRAM_SECONDS = 5 // a clicked dot's diagrams stay this long (ALGO-25)
// Zoom limit (user, 2026-10-16): fully zoomed in, the x-axis shows one time
// label per candle -- 10:12, 10:15, 10:18 on 3-min candles, every minute on
// 1-min -- as on Dhan (same TradingView engine; ~61 px slots there). This
// library needs a 66 px slot to label every candle (61 still skips every
// other one -- checked on screenshots). Each body fills CANDLE_BODY_RATIO of
// its slot (the library's own candles fill ~80%).
const MAX_BAR_SPACING = 66
const CANDLE_BODY_RATIO = DEFAULT_BODY_RATIO
const TF_MINUTES = { '1min': 1, '3min': 3, '5min': 5 }
const RIGHT_COLUMN_WIDTH = 260 // Depth (top) + Indicators window (below) share this column
const INDICATOR_MODES = ['hidden', 'right', 'below']
const BOTTOM_ROW_HEIGHT = 320 // Depth + Indicators window under the chart
const LOAD_MORE_EDGE_BARS = 30 // load older sessions when the view's left edge is this close to the first bar
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function tok(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

// NSE always trades in IST, regardless of the viewing browser's own
// timezone -- same reasoning as the pre-port widget's own formatIST.
function formatIST(unixSeconds) {
  const shifted = new Date((unixSeconds + IST_OFFSET_SECONDS) * 1000)
  const hh = String(shifted.getUTCHours()).padStart(2, '0')
  const mm = String(shifted.getUTCMinutes()).padStart(2, '0')
  return `${hh}:${mm}`
}

function formatISTDate(unixSeconds) {
  const shifted = new Date((unixSeconds + IST_OFFSET_SECONDS) * 1000)
  return `${shifted.getUTCDate()} ${MONTHS[shifted.getUTCMonth()]}`
}

// day boundaries (lightweight-charts tick types 0-2: year/month/day) show
// the date, intraday ticks the time
function tickFormatter(unixSeconds, tickMarkType) {
  return tickMarkType < 3 ? formatISTDate(unixSeconds) : formatIST(unixSeconds)
}

function crosshairFormatter(unixSeconds) {
  return `${formatISTDate(unixSeconds)} ${formatIST(unixSeconds)}`
}

function toUnix(ts) {
  return Math.floor(new Date(ts).getTime() / 1000)
}

// IST calendar day of a unix time, as a sortable number (days since epoch)
function istDay(unixSeconds) {
  return Math.floor((unixSeconds + IST_OFFSET_SECONDS) / 86400)
}

// 00:00 IST of the candle's IST day, as an ISO UTC string
function istDayStartIso(ts) {
  return new Date((istDay(toUnix(ts)) * 86400 - IST_OFFSET_SECONDS) * 1000).toISOString()
}

function toBar(candle) {
  const time = Math.floor(new Date(candle.ts).getTime() / 1000)
  return {
    time, t: time, // t: the candle series' own copy (the library strips `time` before draw)
    open: candle.open, high: candle.high, low: candle.low, close: candle.close,
  }
}

function toVolumeBar(candle) {
  return {
    time: Math.floor(new Date(candle.ts).getTime() / 1000),
    value: candle.volume,
    color: candle.close >= candle.open ? tok('--up') + '55' : tok('--down') + '55',
  }
}

// Null values become whitespace points ({time} with no value), not skipped:
// the panes are synced by LOGICAL (bar-index) range, so every pane must
// carry the same timestamps -- skipping warm-up nulls left MACD (34-candle
// warm-up) with fewer bars than the price pane, shifting it sideways.
function toLineSeries(rows, field) {
  return rows.map((row) => {
    const time = Math.floor(new Date(row.ts).getTime() / 1000)
    const value = row[field]
    return value != null ? { time, value } : { time }
  })
}

function toWhitespace(candles) {
  return candles.map((c) => ({ time: Math.floor(new Date(c.ts).getTime() / 1000) }))
}

function sharedChartOptions() {
  return {
    layout: { background: { color: tok('--chart-bg') }, textColor: tok('--chart-text'), fontFamily: 'inherit', attributionLogo: false },
    grid: { vertLines: { color: tok('--chart-grid') }, horzLines: { color: tok('--chart-grid') } },
    rightPriceScale: { borderColor: tok('--chart-border') },
    // labels are "10:06" or "12 Oct" (<= 6 chars): telling the library so lets it
    // fit one label per candle at full zoom, as on Dhan (default reserves 8)
    timeScale: { borderColor: tok('--chart-border'), timeVisible: true, secondsVisible: false, tickMarkFormatter: tickFormatter, tickMarkMaxCharacterLength: 6 },
    localization: { timeFormatter: crosshairFormatter },
    crosshair: { mode: CrosshairMode.Normal },
  }
}

const OVERLAY_DEFS = [
  { key: 'volume', label: 'Volume', defaultOn: true },
  { key: 'vwap', label: 'VWAP', defaultOn: true },
  { key: 'bb', label: 'Bollinger Bands (20, 2σ)', short: 'BB', defaultOn: true },
  { key: 'ema5', label: 'EMA 5', defaultOn: true },
  { key: 'ema14', label: 'EMA 14', defaultOn: true },
  { key: 'ema21', label: 'EMA 21', defaultOn: false },
  { key: 'ema50', label: 'EMA 50', defaultOn: false },
  { key: 'ma21', label: 'MA 21', defaultOn: false },
  { key: 'ma50', label: 'MA 50', defaultOn: false },
  { key: 'pivots', label: 'Pivot Points (classic)', short: 'Pivots', defaultOn: true },
]
const PANEL_DEFS = [
  { key: 'rsi', label: 'RSI (14)', short: 'RSI' },
  { key: 'macd', label: 'MACD (12, 26, 9)', short: 'MACD' },
  { key: 'stoch', label: 'Stochastic (14, 3)', short: 'Stoch' },
]

// pattern dots by type (ALGO-34) -- same pink/red as the colour scale
const PATTERN_DOT_COLOR = { bullish: '#1f9d55', bearish: '#d64545', caution: '#e48aae', avoid: '#8e1b1b' }

export default function CandleChart({
  instrument, fillHeight = false, depthOpen, onToggleDepth, onControlsWidthChange,
  depthMode, onCycleDepth, screenTimeframe, sidePanel, bottomPanel,
}) {
  const containerRef = useRef(null)
  const stackRef = useRef(null)
  const railRef = useRef(null)
  const rsiElRef = useRef(null)
  const macdElRef = useRef(null)
  const stochElRef = useRef(null)
  const depthBtnRef = useRef(null)
  const tfToggleRef = useRef(null)
  const charts = useRef({}) // every chart/series instance, keyed by name -- see the build effect below
  const toggleState = useRef({ ...getPref('chart.toggles', {}) }) // key -> bool, the user's last choices (preferences.js)
  const lastPivots = useRef(null) // the raw pivots object, so re-enabling the toggle after turning it off doesn't need a re-fetch
  const lastCandles = useRef([]) // so a theme change can recolor the volume bars (their up/down color is baked in per-bar, not a series-level option)
  const sessionStartRef = useRef(null) // ISO 00:00 IST of the current (newest) session shown -- its indicators/markers are re-fetched per candle
  const histRows = useRef({ indicators: [], markers: [] }) // older sessions' rows, fetched once
  const currentRows = useRef({ indicators: [], markers: [] }) // the current session's rows, re-fetched per candle
  const hasMoreRef = useRef(false)
  const loadingOlderRef = useRef(false)
  const readyForMoreRef = useRef(false) // scroll-back only after the chart has opened on the current session
  const loadTokenRef = useRef(0) // bumped per full load, so a scroll-back response for an old instrument/timeframe is dropped
  const loadOlderRef = useRef(null) // latest loadOlder closure, called from the chart's range subscription
  const markerEventsRef = useRef(new Map()) // bar time -> that candle's shown events (one dot each), for hover/click
  const allEventsRef = useRef([]) // every loaded event row -- diagrams need swing points even when their dots are filtered out
  const snappedEventsRef = useRef([]) // allEventsRef placed on the nearest candle (see refreshMarkers)
  const activeDotRef = useRef({ time: null, timer: null }) // the clicked dot whose diagrams are drawn
  const daySepRef = useRef(null)
  const diagramRef = useRef(null)
  const tooltipRef = useRef(null)
  const marksVersionRef = useRef(0) // bumped to repaint after the marks change
  const selectedLineRef = useRef(null) // the clicked line: { key, label, series, width }
  const selectedLabelRef = useRef(null)
  const priceZoomRef = useRef(0) // ALGO-26 vertical zoom in whole wheel steps; 0 = auto-fit (factor 0.9^steps)
  const selectedPatternsRef = useRef(getPref('chart.selectedPatterns.v2', null) ?? []) // pattern keys ticked on the Patterns tab (all enabled until changed)
  const selectedStrategiesRef = useRef([]) // strategy ids ticked on the Strategies tab
  const userPaneRef = useRef({ chart: null, at: 0 }) // the sub-pane the viewer is scrolling right now (see the pane sync)
  const [readout, setReadout] = useState({}) // latest value per picker row, shown beside each checkbox
  // Raw signals ticked on the Signals tab (2026-10-16), saved per user. Until
  // the user picks, a saved old group switch ("Patterns, price action &
  // structure" / "Strategy signals") stands for every signal in it.
  const [selectedSignals, setSelectedSignalsState] = useState(() => getPref('chart.selectedSignals', null))
  const [signalCounts, setSignalCounts] = useState({}) // today's count per signal code
  const [patternCounts, setPatternCounts] = useState({}) // today's count per pattern key
  // a count's click: open the hit review for this stock as a Workspace tab
  const reviewFor = (kind, key, name) => openReview({ kind, key, name, symbol: instrument.symbol, exchange_segment: instrument.exchange_segment })
  const allSignals = useSignals()
  const effectiveSignals = selectedSignals ?? (allSignals ?? [])
    .filter((sig) => (sig.kind === 'indicator' ? isOn('signals', false) : isOn('patterns', false)))
    .map((sig) => sig.code)
  // each tab's "Show on graph" box (2026-10-16): off hides its marks, keeps its ticks
  const [showMarks, setShowMarks] = useState(() => ({
    signals: getPref('chart.showSignals', true) !== false,
    patterns: getPref('chart.showPatterns', true) !== false,
    strategies: getPref('chart.showStrategies', true) !== false,
  }))
  const showMarksRef = useRef(showMarks)
  showMarksRef.current = showMarks
  const setShowMark = (tab, on) => {
    setPref(`chart.show${tab[0].toUpperCase()}${tab.slice(1)}`, on)
    setShowMarks((prev) => ({ ...prev, [tab]: on }))
  }
  useEffect(() => { refreshMarkers() }, [showMarks.signals, showMarks.patterns]) // eslint-disable-line react-hooks/exhaustive-deps
  const shownSignalsRef = useRef(new Set())
  shownSignalsRef.current = new Set(effectiveSignals)
  const setSelectedSignals = (codes) => {
    setPref('chart.selectedSignals', codes)
    setSelectedSignalsState(codes)
  }
  const signalsKey = effectiveSignals.join(',')
  useEffect(() => { refreshMarkers() }, [signalsKey]) // eslint-disable-line react-hooks/exhaustive-deps

  const savedTf = getPref('chart.timeframe', '1min')
  const [syncTf, setSyncTfState] = useState(() => getPref('chart.syncTimeframe', true) !== false)
  const [timeframe, setTimeframe] = useState(() => (
    screenTimeframe !== undefined && syncTf && TIMEFRAMES.includes(screenTimeframe) ? screenTimeframe
      : TIMEFRAMES.includes(savedTf) ? savedTf : '1min'))
  // Sync with the screen's timeframe bar (2026-10-14): on by default where a
  // screen timeframe exists; picking a timeframe on the chart itself turns it
  // off. "All" on the screen leaves the chart where it is.
  const setSyncTf = (next) => setSyncTfState((prev) => {
    const v = typeof next === 'function' ? next(prev) : next
    setPref('chart.syncTimeframe', v)
    return v
  })
  const canSync = screenTimeframe !== undefined
  useEffect(() => {
    if (canSync && syncTf && TIMEFRAMES.includes(screenTimeframe)) setTimeframe(screenTimeframe)
  }, [canSync, syncTf, screenTimeframe])
  const timeframeRef = useRef(timeframe) // for the chart's own callbacks (built once)
  timeframeRef.current = timeframe
  const [asOfDate, setAsOfDate] = useState(null)
  // Indicators window, 3 states like Depth (2026-10-15): hidden -> right -> below
  const [indicatorsMode, setIndicatorsModeState] = useState(() => {
    const v = getPref('chart.indicatorsMode', getPref('chart.indicatorsOpen', false) ? 'right' : 'hidden')
    return INDICATOR_MODES.includes(v) ? v : 'hidden'
  })
  const cycleIndicatorsMode = () => setIndicatorsModeState((prev) => {
    const v = INDICATOR_MODES[(INDICATOR_MODES.indexOf(prev) + 1) % INDICATOR_MODES.length]
    setPref('chart.indicatorsMode', v)
    return v
  })
  const indicatorsOpen = indicatorsMode !== 'hidden'
  // "Graph only" (2026-10-15): hides the panels -- RSI/MACD/Stoch panes, Depth,
  // the Indicators window -- WITHOUT touching their own settings, so turning it
  // off brings back exactly the layout before. The price graph is unchanged.
  const [graphOnly, setGraphOnlyState] = useState(() => !!getPref('chart.graphOnly', false))
  const graphOnlyRef = useRef(graphOnly)
  const [reloadKey, setReloadKey] = useState(0)
  const [, forceRender] = useState(0) // bumped after a toggle so the picker's checked state re-renders
  const { theme } = useTheme()

  useEffect(() => {
    if (!onControlsWidthChange || !depthBtnRef.current || !tfToggleRef.current) return
    function measure() {
      const left = depthBtnRef.current.getBoundingClientRect().left
      const right = tfToggleRef.current.getBoundingClientRect().right
      onControlsWidthChange(right - left)
    }
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [onControlsWidthChange, depthOpen, depthMode])

  // the Patterns/Strategies tabs' lists load with the chart, not when the panel opens
  useEffect(() => { prefetchPatterns(); prefetchStrategies(); prefetchSignals() }, [])
  // default marks with the panel closed: every enabled pattern, until the user
  // picks on the Patterns tab (that choice is saved and wins)
  const allPatterns = usePatterns()
  useEffect(() => {
    if (!allPatterns || getPref('chart.selectedPatterns.v2', null) !== null) return
    selectedPatternsRef.current = allPatterns.filter((p) => p.enabled).map((p) => p.key)
    refreshMarkers()
  }, [allPatterns]) // eslint-disable-line react-hooks/exhaustive-deps

  // ---- build every chart/series once, on mount ----
  useEffect(() => {
    if (!containerRef.current) return
    const c = charts.current

    c.main = createChart(containerRef.current, { ...sharedChartOptions(), height: MAIN_HEIGHT })
    // BB fill, added BEFORE the candlestick series so candles always paint
    // on top of it (see memory: lightweight_charts_cdn_note's "BB band
    // shading" note for why this two-Area-series trick needs that order).
    c.bbFillUpper = c.main.addAreaSeries({ lineVisible: false, topColor: tok('--ind-bb') + '22', bottomColor: tok('--ind-bb') + '22', priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })
    c.bbFillLower = c.main.addAreaSeries({ lineVisible: false, topColor: tok('--chart-bg'), bottomColor: tok('--chart-bg'), priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })
    // thin-bodied candles (custom series): body ~60% of its slot, like Dhan (2026-10-16)
    c.candleView = new ThinCandleSeries() // also draws the event marks, at candle-body width
    c.candle = c.main.addCustomSeries(c.candleView, { upColor: tok('--up'), downColor: tok('--down'), bodyRatio: CANDLE_BODY_RATIO })
    c.volume = c.main.addHistogramSeries({ priceScaleId: 'volume', priceFormat: { type: 'volume' }, priceLineVisible: false, lastValueVisible: false })
    c.volume.priceScale().applyOptions({ scaleMargins: { top: 0.88, bottom: 0 } })
    c.vwap = c.main.addLineSeries({ color: tok('--ind-vwap'), lineWidth: 2, priceLineVisible: false, lastValueVisible: false })
    c.bbUpper = c.main.addLineSeries({ color: tok('--ind-bb'), lineWidth: 1, lineStyle: LineStyle.Dashed, priceLineVisible: false, lastValueVisible: false })
    c.bbMiddle = c.main.addLineSeries({ color: tok('--ind-bb'), lineWidth: 1, priceLineVisible: false, lastValueVisible: false })
    c.bbLower = c.main.addLineSeries({ color: tok('--ind-bb'), lineWidth: 1, lineStyle: LineStyle.Dashed, priceLineVisible: false, lastValueVisible: false })
    c.ema5 = c.main.addLineSeries({ color: tok('--ind-ema5'), lineWidth: 1, priceLineVisible: false, lastValueVisible: false })
    c.ema14 = c.main.addLineSeries({ color: tok('--ind-ema14'), lineWidth: 1, priceLineVisible: false, lastValueVisible: false })
    c.ema21 = c.main.addLineSeries({ color: tok('--ind-ema21'), lineWidth: 1, priceLineVisible: false, lastValueVisible: false, visible: false })
    c.ema50 = c.main.addLineSeries({ color: tok('--ind-ema50'), lineWidth: 1, priceLineVisible: false, lastValueVisible: false, visible: false })
    c.ma21 = c.main.addLineSeries({ color: tok('--ind-ma21'), lineWidth: 1, lineStyle: LineStyle.Dotted, priceLineVisible: false, lastValueVisible: false, visible: false })
    c.ma50 = c.main.addLineSeries({ color: tok('--ind-ma50'), lineWidth: 1, lineStyle: LineStyle.Dotted, priceLineVisible: false, lastValueVisible: false, visible: false })
    c.pivotLines = {}

    // ALGO-26: the mouse wheel over the price axis zooms the price range
    // (the graph's height); over the chart it keeps zooming time (width).
    // priceZoomRef < 1 = taller candles. Double-click the axis to reset.
    const overlaySeries = [c.bbFillUpper, c.bbFillLower, c.vwap, c.bbUpper, c.bbMiddle, c.bbLower, c.ema5, c.ema14, c.ema21, c.ema50, c.ma21, c.ma50]
    function applyPriceZoom() {
      const z = priceZoomRef.current === 0 ? 1 : 0.9 ** priceZoomRef.current
      c.candle.applyOptions({
        autoscaleInfoProvider: (original) => {
          const res = original()
          if (!res || z === 1 || !res.priceRange) return res
          const { minValue, maxValue } = res.priceRange
          const mid = (minValue + maxValue) / 2
          const half = ((maxValue - minValue) / 2) * z
          return { ...res, priceRange: { minValue: mid - half, maxValue: mid + half } }
        },
      })
      // with a manual zoom only the candles set the range -- the overlays
      // (BB, EMAs, VWAP) would otherwise widen it straight back out
      overlaySeries.forEach((s) => s.applyOptions({ autoscaleInfoProvider: (original) => (z === 1 ? original() : null) }))
      c.main.priceScale('right').applyOptions({ autoScale: true })
      requestAnimationFrame(() => { recordView(); drawDiagrams(); drawDaySeparators() })
    }
    function overPriceAxis(e) {
      const rect = containerRef.current.getBoundingClientRect()
      return e.clientX >= rect.right - c.main.priceScale('right').width() && e.clientX <= rect.right
    }
    function onAxisWheel(e) {
      if (!overPriceAxis(e)) return
      e.preventDefault()
      e.stopPropagation() // keep the chart's own wheel (time zoom) out of it
      priceZoomRef.current = Math.min(Math.max(priceZoomRef.current + (e.deltaY < 0 ? 1 : -1), -28), 28)
      applyPriceZoom()
    }
    function onAxisDblClick(e) {
      if (!overPriceAxis(e)) return
      priceZoomRef.current = 0
      applyPriceZoom()
    }
    const host = containerRef.current
    host.addEventListener('wheel', onAxisWheel, { capture: true, passive: false })
    host.addEventListener('dblclick', onAxisDblClick, { capture: true })
    applyPriceZoom()

    c.rsi = createChart(rsiElRef.current, { ...sharedChartOptions(), height: SUB_HEIGHT })
    c.rsiLine = c.rsi.addLineSeries({ color: tok('--ind-rsi'), lineWidth: 2, priceLineVisible: false, lastValueVisible: false })
    c.rsiOversold30 = c.rsi.addLineSeries({ color: tok('--chart-border'), lineWidth: 1, lineStyle: LineStyle.Dashed, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })
    c.rsiOverbought70 = c.rsi.addLineSeries({ color: tok('--chart-border'), lineWidth: 1, lineStyle: LineStyle.Dashed, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })

    c.macd = createChart(macdElRef.current, { ...sharedChartOptions(), height: SUB_HEIGHT })
    c.macdHist = c.macd.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false })
    c.macdLine = c.macd.addLineSeries({ color: tok('--ind-macd'), lineWidth: 2, priceLineVisible: false, lastValueVisible: false })
    c.macdSignal = c.macd.addLineSeries({ color: tok('--ind-macd-signal'), lineWidth: 1, priceLineVisible: false, lastValueVisible: false })

    c.stoch = createChart(stochElRef.current, { ...sharedChartOptions(), height: SUB_HEIGHT })
    c.stochK = c.stoch.addLineSeries({ color: tok('--ind-stoch-k'), lineWidth: 2, priceLineVisible: false, lastValueVisible: false })
    c.stochD = c.stoch.addLineSeries({ color: tok('--ind-stoch-d'), lineWidth: 1, priceLineVisible: false, lastValueVisible: false })

    // One whitespace-only series per sub-pane carrying every CANDLE
    // timestamp, so each pane has exactly the price pane's bars even when
    // the engine has indicator rows for fewer candles (e.g. after a
    // mid-session backend restart). Draws nothing.
    const spacerOptions = { priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }
    c.spacers = [c.rsi, c.macd, c.stoch].map((chart) => chart.addLineSeries(spacerOptions))
    // no dot riding each line under the mouse (user, 2026-10-27: "always moves
    // with the mouse pointer, which is annoying"); crosshair lines + labels stay
    ;[c.vwap, c.bbUpper, c.bbMiddle, c.bbLower, c.ema5, c.ema14, c.ema21, c.ema50, c.ma21, c.ma50,
      c.rsiLine, c.macdLine, c.macdSignal, c.stochK, c.stochD].forEach((s) => s?.applyOptions({ crosshairMarkerVisible: false }))

    // keep every pane's visible time range in lockstep
    const allPanes = [c.main, c.rsi, c.macd, c.stoch]
    // A sub-pane drives the others only while the viewer is scrolling it --
    // otherwise its own setData (indicator rows arriving seconds after the
    // candles on live) re-synced the price pane to the sub-pane's default
    // range, jumping the view and loading older sessions on open (ALGO-25).
    // The price pane always drives; resyncSubPanes() realigns after data.
    const subEls = [[rsiElRef, c.rsi], [macdElRef, c.macd], [stochElRef, c.stoch]]
    const markUser = (chart) => () => { userPaneRef.current = { chart, at: Date.now() } }
    const listeners = subEls.map(([ref, chart]) => {
      const fn = markUser(chart)
      ;['pointerdown', 'pointermove', 'wheel', 'touchstart', 'touchmove'].forEach((ev) => ref.current?.addEventListener(ev, fn, { passive: true }))
      return [ref.current, fn]
    })
    let syncing = false
    allPanes.forEach((chart) => {
      chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (!range || syncing) return
        if (chart !== c.main) {
          const u = userPaneRef.current
          if (u.chart !== chart || Date.now() - u.at > 1000) return
        }
        syncing = true
        allPanes.forEach((other) => { if (other !== chart) other.timeScale().setVisibleLogicalRange(range) })
        syncing = false
      })
    })

    c.main.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (capBarSpacing(range)) return // re-fires with the capped range
      recordView()
      drawDaySeparators()
      if (range && range.from < LOAD_MORE_EDGE_BARS) loadOlderRef.current?.()
    })
    c.main.timeScale().subscribeVisibleLogicalRangeChange(() => drawDiagrams())
    // Click (2026-10-16): ON a mark -> that candle's diagrams; on a line ->
    // select it (thicker + name/value label); on empty chart -> clear. Before,
    // any click in a marked candle's column drew diagrams, so a line next to
    // the marks could not be picked.
    c.main.subscribeClick((param) => {
      if (param.point && param.time != null && markHit(param.time, param.point)) {
        showDotDiagrams(param.time)
        return
      }
      const line = lineAt(param) ?? lineFor(param.hoveredSeries)
      if (line) selectLine(line, param)
      else clearSelectedLine()
    })
    c.main.subscribeCrosshairMove((param) => {
      const tip = tooltipRef.current
      if (!tip) return
      const events = param.time != null && param.point ? markerEventsRef.current.get(param.time) : null
      if (!events || !events.length) { tip.style.display = 'none'; return }
      renderEventList(tip, events)
      tip.style.display = 'block'
      const width = containerRef.current?.clientWidth ?? 0
      tip.style.left = `${Math.min(Math.max(param.point.x + 12, 0), Math.max(width - tip.offsetWidth - 70, 0))}px`
      tip.style.top = `${Math.max(param.point.y - 34, 4)}px`
    })

    const onKey = (e) => { if (e.key === 'Escape') clearSelectedLine() }
    document.addEventListener('keydown', onKey)
    const resizeObserver = new ResizeObserver(() => resizeCharts())
    resizeObserver.observe(containerRef.current)
    const railObserver = new ResizeObserver(() => syncRailHeight())
    if (stackRef.current) railObserver.observe(stackRef.current)

    function resizeCharts() {
      if (!containerRef.current) return
      const width = containerRef.current.clientWidth
      c.main.resize(width, fillHeight ? (containerRef.current.clientHeight || MAIN_HEIGHT) : MAIN_HEIGHT)
      ;[c.rsi, c.macd, c.stoch].forEach((chart) => chart.resize(width, SUB_HEIGHT))
      drawDaySeparators()
      drawDiagrams()
    }
    applySavedVisibility()
    syncRailHeight()
    updateTimeAxisVisibility()

    return () => {
      resizeObserver.disconnect()
      document.removeEventListener('keydown', onKey)
      host.removeEventListener('wheel', onAxisWheel, { capture: true })
      host.removeEventListener('dblclick', onAxisDblClick, { capture: true })
      listeners.forEach(([el, fn]) => ['pointerdown', 'pointermove', 'wheel', 'touchstart', 'touchmove'].forEach((ev) => el?.removeEventListener(ev, fn)))
      railObserver.disconnect()
      Object.values(c).forEach((v) => { if (v && typeof v.remove === 'function') v.remove() })
      charts.current = {}
    }
  }, [fillHeight])

  // ---- recolor everything when the theme actually changes ----
  useEffect(() => {
    function repaint() {
      const c = charts.current
      if (!c.main) return
      const opts = sharedChartOptions()
      ;[c.main, c.rsi, c.macd, c.stoch].forEach((chart) => chart?.applyOptions(opts))
      c.candle.applyOptions({ upColor: tok('--up'), downColor: tok('--down'), borderUpColor: tok('--up'), borderDownColor: tok('--down'), wickUpColor: tok('--up'), wickDownColor: tok('--down') })
      c.vwap.applyOptions({ color: tok('--ind-vwap') })
      ;[c.bbUpper, c.bbMiddle, c.bbLower].forEach((s) => s.applyOptions({ color: tok('--ind-bb') }))
      c.bbFillUpper.applyOptions({ topColor: tok('--ind-bb') + '22', bottomColor: tok('--ind-bb') + '22' })
      c.bbFillLower.applyOptions({ topColor: tok('--chart-bg'), bottomColor: tok('--chart-bg') })
      c.ema5.applyOptions({ color: tok('--ind-ema5') })
      c.ema14.applyOptions({ color: tok('--ind-ema14') })
      c.ema21.applyOptions({ color: tok('--ind-ema21') })
      c.ema50.applyOptions({ color: tok('--ind-ema50') })
      c.ma21.applyOptions({ color: tok('--ind-ma21') })
      c.ma50.applyOptions({ color: tok('--ind-ma50') })
      c.rsiLine.applyOptions({ color: tok('--ind-rsi') })
      c.macdLine.applyOptions({ color: tok('--ind-macd') })
      c.macdSignal.applyOptions({ color: tok('--ind-macd-signal') })
      c.stochK.applyOptions({ color: tok('--ind-stoch-k') })
      c.stochD.applyOptions({ color: tok('--ind-stoch-d') })
      if (lastCandles.current.length) c.volume.setData(lastCandles.current.map(toVolumeBar))
      colourLastPrice(lastCandles.current[lastCandles.current.length - 1]) // per-bar color is baked in, not a series option -- must be re-set, not applyOptions'd
    }
    // Deferred, not called synchronously: ThemeProvider's own effect (the
    // ancestor that actually stamps data-theme on <html>) is ALSO triggered
    // by this same theme change, and React doesn't guarantee it runs before
    // this descendant's effect in the same commit -- reading
    // getComputedStyle synchronously here could see the OLD theme's values.
    // One rAF lands safely after every effect from this commit has flushed.
    requestAnimationFrame(repaint)
    if (theme !== 'system') return
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    media.addEventListener('change', repaint)
    return () => media.removeEventListener('change', repaint)
  }, [theme])

  // ---- data: candles + indicators + pivots + markers, on instrument/timeframe change ----
  useEffect(() => {
    if (!instrument || !charts.current.main) return
    let cancelled = false
    const token = ++loadTokenRef.current
    hasMoreRef.current = false
    readyForMoreRef.current = false
    histRows.current = { indicators: [], markers: [] }
    currentRows.current = { indicators: [], markers: [] }
    loadAll()
    return () => { cancelled = true }

    async function loadAll() {
      const [candlesRes, pivotsRes] = await Promise.all([
        getCandleSessions(instrument.symbol, instrument.exchange_segment, timeframe, INITIAL_DAYS).catch(() => null),
        getCandlePivots(instrument.symbol, instrument.exchange_segment).catch(() => null),
      ])
      if (cancelled) return
      if (pivotsRes) applyPivots(pivotsRes.pivots)
      if (!candlesRes) return
      const candles = candlesRes.candles
      applyCandles(candles)
      setAsOfDate(candlesRes.live ? null : candlesRes.as_of_date)
      hasMoreRef.current = Boolean(candlesRes.has_more)
      sessionStartRef.current = candles.length ? istDayStartIso(candles[candles.length - 1].ts) : null
      // Scroll to today as soon as the candles are in -- not after the
      // indicator/event fetches, which can be slow (a fresh dev page queues
      // them behind its module loads), and until then the chart sat on its
      // default view (found while verifying ALGO-25).
      showCurrentSession(candles)
      // Arm scroll-back only once the panes have settled: the sub-panes' own
      // setData fires range-sync events a frame later that briefly pull the
      // view toward the first bar, which used to load older sessions on open.
      setTimeout(() => {
        if (cancelled || token !== loadTokenRef.current) return
        showCurrentSession(candles)
        readyForMoreRef.current = true
      }, 300)
      // Today's indicators/markers first (small, and re-fetched per closed
      // candle), then the older sessions' once (large).
      await refreshIndicatorsAndMarkers(() => cancelled)
      const start = sessionStartRef.current
      if (start && candles.length && candles[0].ts < start) {
        const to = new Date(new Date(start).getTime() - 1000).toISOString()
        const [ind, mk] = await Promise.all([
          getCandleIndicators(instrument.symbol, instrument.exchange_segment, timeframe, candles[0].ts, to).catch(() => null),
          getCandleMarkers(instrument.symbol, instrument.exchange_segment, timeframe, candles[0].ts, to).catch(() => null),
        ])
        if (cancelled || token !== loadTokenRef.current) return
        histRows.current = { indicators: ind?.indicators ?? [], markers: mk?.markers ?? [] }
        applyIndicatorRows([...histRows.current.indicators, ...currentRows.current.indicators])
        applyMarkers([...histRows.current.markers, ...currentRows.current.markers])
      }
    }
  }, [instrument, timeframe, reloadKey])

  // opens scrolled to the newest session, with a little of the previous one in view
  function showCurrentSession(candles) {
    if (!candles.length || !charts.current.main) return
    const start = sessionStartRef.current
    const firstCurrent = candles.findIndex((x) => x.ts >= start)
    const from = Math.max((firstCurrent < 0 ? 0 : firstCurrent) - 15, 0)
    charts.current.main.timeScale().setVisibleLogicalRange({ from, to: candles.length + 3 })
  }

  function applyCandles(candles) {
    const c = charts.current
    if (!c.main) return
    lastCandles.current = candles
    c.candle.setData(candles.map(toBar))
    colourLastPrice(candles[candles.length - 1])
    c.volume.setData(candles.map(toVolumeBar))
    c.spacers.forEach((s) => s.setData(toWhitespace(candles)))
    drawDaySeparators()
    resyncSubPanes()
  }

  // after new data, line the sub-panes up with the price pane again (they no
  // longer pull the price pane along -- see the pane sync in the build effect)
  function resyncSubPanes() {
    requestAnimationFrame(() => {
      const c = charts.current
      const range = c.main?.timeScale().getVisibleLogicalRange()
      if (range) [c.rsi, c.macd, c.stoch].forEach((chart) => chart?.timeScale().setVisibleLogicalRange(range))
    })
  }

  async function refreshIndicatorsAndMarkers(isCancelled) {
    const start = sessionStartRef.current
    const [indicatorsRes, markersRes] = await Promise.all([
      getCandleIndicators(instrument.symbol, instrument.exchange_segment, timeframe, start).catch(() => null),
      getCandleMarkers(instrument.symbol, instrument.exchange_segment, timeframe, start).catch(() => null),
    ])
    if (isCancelled()) return
    if (indicatorsRes) currentRows.current.indicators = indicatorsRes.indicators
    if (markersRes) currentRows.current.markers = markersRes.markers
    applyIndicatorRows([...histRows.current.indicators, ...currentRows.current.indicators])
    applyMarkers([...histRows.current.markers, ...currentRows.current.markers])
  }

  // scroll-back: prepend MORE_DAYS older sessions, keeping the view where it was
  async function loadOlder() {
    if (!readyForMoreRef.current || loadingOlderRef.current || !hasMoreRef.current || !lastCandles.current.length || !instrument) return
    // the range event that called us can be stale (setData queues one from
    // before showCurrentSession moved the view) -- check where the view is now
    const now = charts.current.main?.timeScale().getVisibleLogicalRange()
    if (!now || now.from >= LOAD_MORE_EDGE_BARS) return
    loadingOlderRef.current = true
    const token = loadTokenRef.current
    const stale = () => token !== loadTokenRef.current
    try {
      const first = lastCandles.current[0].ts
      const res = await getCandleSessions(instrument.symbol, instrument.exchange_segment, timeframe, MORE_DAYS, first).catch(() => null)
      if (stale() || !res) return
      hasMoreRef.current = Boolean(res.has_more)
      const older = res.candles
      if (!older.length) return
      const to = new Date(new Date(first).getTime() - 1000).toISOString()
      const [ind, mk] = await Promise.all([
        getCandleIndicators(instrument.symbol, instrument.exchange_segment, timeframe, older[0].ts, to).catch(() => null),
        getCandleMarkers(instrument.symbol, instrument.exchange_segment, timeframe, older[0].ts, to).catch(() => null),
      ])
      if (stale() || !charts.current.main) return
      const timeScale = charts.current.main.timeScale()
      const range = timeScale.getVisibleLogicalRange()
      histRows.current = {
        indicators: [...(ind?.indicators ?? []), ...histRows.current.indicators],
        markers: [...(mk?.markers ?? []), ...histRows.current.markers],
      }
      applyCandles([...older, ...lastCandles.current])
      applyIndicatorRows([...histRows.current.indicators, ...currentRows.current.indicators])
      applyMarkers([...histRows.current.markers, ...currentRows.current.markers])
      if (range) timeScale.setVisibleLogicalRange({ from: range.from + older.length, to: range.to + older.length })
    } finally {
      loadingOlderRef.current = false
    }
  }
  loadOlderRef.current = loadOlder

  // the view as data attributes on the diagram layer -- read by the UI suite
  // (lightweight-charts draws its axes on canvas, so there is nothing else to read)
  // the last-price label/line in the last candle's colour (a custom series
  // has one colour of its own, the built-in candles did this by themselves)
  function colourLastPrice(candle) {
    if (!candle || !charts.current.candle) return
    const colour = candle.close >= candle.open ? tok('--up') : tok('--down')
    charts.current.candle.applyOptions({ color: colour, priceLineColor: colour })
  }

  // ---- line selection + mark hit-testing (2026-10-16) ----
  function lineSeries() {
    const c = charts.current
    return [
      ['vwap', 'VWAP', c.vwap], ['ema5', 'EMA 5', c.ema5], ['ema14', 'EMA 14', c.ema14], ['ema21', 'EMA 21', c.ema21],
      ['ema50', 'EMA 50', c.ema50], ['ma21', 'MA 21', c.ma21], ['ma50', 'MA 50', c.ma50],
      ['bbUpper', 'BB upper', c.bbUpper], ['bbMiddle', 'BB middle', c.bbMiddle], ['bbLower', 'BB lower', c.bbLower],
    ].filter(([, , s]) => s)
  }
  function lineFor(series) {
    if (!series) return null
    const hit = lineSeries().find(([, , s]) => s === series)
    return hit ? { key: hit[0], label: hit[1], series: hit[2] } : null
  }
  // The visible line nearest the click, within 6 px of the line AS DRAWN:
  // the segment between its two data points either side of the click, so a
  // click anywhere along a line counts, not only at a candle (2026-10-27).
  // Our own hit test -- the library's hover detection stopped finding lines
  // once their crosshair dots were off.
  function lineAt(param) {
    const c = charts.current
    if (!param?.point || !c.main) return null
    const ts = c.main.timeScale()
    const logical = ts.coordinateToLogical(param.point.x)
    if (logical == null) return null
    const i0 = Math.floor(logical)
    let best = null
    for (const [key, label, series] of lineSeries()) {
      if (series.options().visible === false) continue
      const pts = []
      for (const i of [i0 - 1, i0, i0 + 1, i0 + 2]) {
        const row = series.dataByIndex?.(i)
        const x = ts.logicalToCoordinate(i)
        const y = row?.value != null ? series.priceToCoordinate(row.value) : null
        if (x != null && y != null) pts.push({ x, y })
      }
      for (let k = 0; k + 1 < pts.length; k++) {
        const d = distToSegment(param.point, pts[k], pts[k + 1])
        if (d <= 6 && (!best || d < best.d)) best = { key, label, series, d }
      }
    }
    return best && { key: best.key, label: best.label, series: best.series }
  }
  function distToSegment(p, a, b) {
    const dx = b.x - a.x, dy = b.y - a.y
    const len2 = dx * dx + dy * dy
    const t = len2 ? Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2)) : 0
    return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy))
  }
  function selectLine(line, param) {
    clearSelectedLine()
    const width = line.series.options().lineWidth ?? 1
    selectedLineRef.current = { ...line, width }
    line.series.applyOptions({ lineWidth: width + 2 })
    const value = param?.seriesData?.get(line.series)?.value
    const el = selectedLabelRef.current
    if (el) {
      el.textContent = `${line.label}${value != null ? ` · ${Number(value).toFixed(2)}` : ''}`
      el.style.color = line.series.options().color
      el.dataset.selectedLine = line.key
      el.hidden = false
    }
  }
  function clearSelectedLine() {
    const sel = selectedLineRef.current
    if (sel) sel.series.applyOptions({ lineWidth: sel.width })
    selectedLineRef.current = null
    const el = selectedLabelRef.current
    if (el) { el.hidden = true; delete el.dataset.selectedLine }
  }
  // is `point` (CSS px in the price pane) on one of this candle's marks?
  function markHit(time, point) {
    const c = charts.current
    const marks = c.candleView?._renderer?._marks?.get(time)
    const bar = lastCandles.current.find((k) => toUnix(k.ts) === time)
    if (!marks?.length || !bar || !c.main) return false
    const cx = c.main.timeScale().timeToCoordinate(time)
    const hy = c.candle.priceToCoordinate(bar.high)
    const ly = c.candle.priceToCoordinate(bar.low)
    if (cx == null || hy == null || ly == null) return false
    const range = c.main.timeScale().getVisibleLogicalRange()
    const w = markWidth(range ? plotWidth() / (range.to - range.from) : 6, CANDLE_BODY_RATIO)
    const pad = 2
    return markBoxes(marks, cx, hy, ly, w).some((b) => point.x >= b.x0 - pad && point.x <= b.x1 + pad && point.y >= b.y0 - pad && point.y <= b.y1 + pad)
  }

  // the price plot's width (the chart minus its right-hand price axis)
  function plotWidth() {
    const c = charts.current
    const w = containerRef.current?.clientWidth ?? 0
    return Math.max(0, w - (c.main?.priceScale('right').width() ?? 0))
  }
  // Candle slots never wider than MAX_BAR_SPACING (2026-10-16). lightweight-charts 4.x has no maxBarSpacing option (it came
  // in 5.x), so a too-narrow visible range is widened to the left, keeping the
  // right edge (the latest candle) where it is. Returns true when it changed
  // the range.
  function maxBarSpacing() {
    return MAX_BAR_SPACING
  }
  function capBarSpacing(range) {
    const c = charts.current
    const w = plotWidth()
    if (!range || !c.main || !w) return false
    const minBars = w / maxBarSpacing()
    if (range.to - range.from >= minBars - 0.01) return false
    c.main.timeScale().setVisibleLogicalRange({ from: range.to - minBars, to: range.to })
    return true
  }

  function recordView() {
    const c = charts.current
    const layer = diagramRef.current
    if (!layer || !c.main || !c.candle) return
    const range = c.main.timeScale().getVisibleLogicalRange()
    if (range) layer.dataset.barsVisible = (range.to - range.from).toFixed(1)
    if (range) layer.dataset.rangeFrom = range.from.toFixed(1)
    if (range) layer.dataset.barSpacing = (plotWidth() / (range.to - range.from)).toFixed(2)
    layer.dataset.marksDrawn = String(c.candleView?._renderer?.marksDrawn ?? 0)
    if (range) layer.dataset.markWidth = markWidth(plotWidth() / (range.to - range.from), CANDLE_BODY_RATIO).toFixed(2)
    if (range) layer.dataset.candleBody = (plotWidth() / (range.to - range.from) * CANDLE_BODY_RATIO).toFixed(2)
    layer.dataset.bodyRatio = String(c.candle.options().bodyRatio)
    if (range) layer.dataset.pxPerMinute = (plotWidth() / (range.to - range.from) / (TF_MINUTES[timeframeRef.current] ?? 1)).toFixed(2)
    // scroll-back state, for the UI suite: why older sessions do or don't load
    layer.dataset.olderState = `ready=${readyForMoreRef.current} loading=${loadingOlderRef.current} hasMore=${hasMoreRef.current}`
    const top = c.candle.coordinateToPrice(0)
    const bottom = c.candle.coordinateToPrice(layer.clientHeight)
    if (top != null && bottom != null) layer.dataset.priceSpan = (top - bottom).toFixed(3)
    layer.dataset.priceZoom = String(priceZoomRef.current === 0 ? 1 : 0.9 ** priceZoomRef.current)
    layer.measureView = () => { recordView(); return { ...layer.dataset } } // fresh read for the UI suite
    // for the UI suite: a point on a line / on a mark, in the pane's CSS px
    layer.linePoint = (key) => {
      const s = lineSeries().find(([k]) => k === key)?.[2]
      const range = c.main.timeScale().getVisibleLogicalRange()
      if (!s || !range) return null
      for (let i = Math.floor(range.to) - 3; i > range.from; i--) {
        const row = s.dataByIndex?.(i)
        if (row?.value == null) continue
        const x = c.main.timeScale().logicalToCoordinate(i)
        const y = s.priceToCoordinate(row.value)
        if (x != null && y != null && !markHit(row.time, { x, y })) return { x, y }
      }
      return null
    }
    layer.lineMidPoint = (key) => {
      const s = lineSeries().find(([k]) => k === key)?.[2]
      const range = c.main.timeScale().getVisibleLogicalRange()
      if (!s || !range) return null
      for (let i = Math.floor(range.to) - 3; i > range.from + 1; i--) {
        const a = s.dataByIndex?.(i - 1), b = s.dataByIndex?.(i)
        if (a?.value == null || b?.value == null) continue
        const x0 = c.main.timeScale().logicalToCoordinate(i - 1), x1 = c.main.timeScale().logicalToCoordinate(i)
        const y0 = s.priceToCoordinate(a.value), y1 = s.priceToCoordinate(b.value)
        if ([x0, x1, y0, y1].some((v) => v == null)) continue
        const p = { x: (x0 + x1) / 2, y: (y0 + y1) / 2 }
        if (!markHit(a.time, p) && !markHit(b.time, p)) return p
      }
      return null
    }
    layer.markPoint = () => {
      const range = c.main.timeScale().getVisibleLogicalRange()
      const w = range ? markWidth(plotWidth() / (range.to - range.from), CANDLE_BODY_RATIO) : 6
      for (const [time, marks] of [...(c.candleView?._renderer?._marks ?? [])].reverse()) {
        const bar = lastCandles.current.find((k) => toUnix(k.ts) === time)
        const cx = c.main.timeScale().timeToCoordinate(time)
        if (!bar || cx == null || cx < 10 || cx > plotWidth() - 10) continue
        const b = markBoxes(marks, cx, c.candle.priceToCoordinate(bar.high), c.candle.priceToCoordinate(bar.low), w)[0]
        if (b && b.y0 > 5) return { x: (b.x0 + b.x1) / 2, y: (b.y0 + b.y1) / 2 }
      }
      return null
    }
  }

  // faint vertical line + date at the first bar of each IST day
  function drawDaySeparators() {
    const layer = daySepRef.current
    const c = charts.current
    if (!layer || !c.main) return
    const candles = lastCandles.current
    const timeScale = c.main.timeScale()
    const parts = []
    for (let i = 1; i < candles.length; i++) {
      const t = toUnix(candles[i].ts)
      if (istDay(t) === istDay(toUnix(candles[i - 1].ts))) continue
      const x = timeScale.timeToCoordinate(t)
      if (x == null) continue
      parts.push(`<div style="position:absolute;top:0;bottom:0;left:${Math.round(x) - 4}px;border-left:1px dashed var(--chart-border)"></div>`
        + `<div style="position:absolute;top:2px;left:${Math.round(x)}px;font-size:10.5px;color:var(--text-faint)">${formatISTDate(t)}</div>`)
    }
    layer.innerHTML = parts.join('')
  }

  function applyIndicatorRows(rows) {
    const c = charts.current
    if (!c.main) return
    c.vwap.setData(toLineSeries(rows, 'vwap'))
    c.bbUpper.setData(toLineSeries(rows, 'bb_upper'))
    c.bbMiddle.setData(toLineSeries(rows, 'bb_middle'))
    c.bbLower.setData(toLineSeries(rows, 'bb_lower'))
    c.bbFillUpper.setData(toLineSeries(rows, 'bb_upper'))
    c.bbFillLower.setData(toLineSeries(rows, 'bb_lower'))
    c.ema5.setData(toLineSeries(rows, 'ema5'))
    c.ema14.setData(toLineSeries(rows, 'ema14'))
    c.ema21.setData(toLineSeries(rows, 'ema21'))
    c.ema50.setData(toLineSeries(rows, 'ema50'))
    c.ma21.setData(toLineSeries(rows, 'ma21'))
    c.ma50.setData(toLineSeries(rows, 'ma50'))
    c.rsiLine.setData(toLineSeries(rows, 'rsi'))
    const times = rows.map((r) => Math.floor(new Date(r.ts).getTime() / 1000))
    c.rsiOversold30.setData(times.map((time) => ({ time, value: 30 })))
    c.rsiOverbought70.setData(times.map((time) => ({ time, value: 70 })))
    c.macdHist.setData(rows.map((r) => {
      const time = Math.floor(new Date(r.ts).getTime() / 1000)
      if (r.macd_line == null || r.macd_signal == null) return { time }
      return { time, value: r.macd_line - r.macd_signal, color: r.macd_line >= r.macd_signal ? tok('--good') : tok('--critical') }
    }))
    c.macdLine.setData(toLineSeries(rows, 'macd_line'))
    c.macdSignal.setData(toLineSeries(rows, 'macd_signal'))
    c.stochK.setData(toLineSeries(rows, 'stoch_k'))
    c.stochD.setData(toLineSeries(rows, 'stoch_d'))

    // latest non-null value of each field, for the picker's readout column
    const last = (field) => {
      for (let i = rows.length - 1; i >= 0; i--) if (rows[i][field] != null) return rows[i][field]
      return null
    }
    const f2 = (v) => (v == null ? '—' : v.toFixed(2))
    const lastVolume = lastCandles.current.length ? lastCandles.current[lastCandles.current.length - 1].volume : null
    setReadout((prev) => ({
      ...prev,
      volume: lastVolume == null ? '—' : lastVolume.toLocaleString('en-IN'),
      vwap: f2(last('vwap')),
      bb: last('bb_upper') == null ? '—' : `${f2(last('bb_upper'))} / ${f2(last('bb_lower'))}`,
      ema5: f2(last('ema5')), ema14: f2(last('ema14')), ema21: f2(last('ema21')), ema50: f2(last('ema50')),
      ma21: f2(last('ma21')), ma50: f2(last('ma50')),
      rsi: last('rsi') == null ? '—' : last('rsi').toFixed(1),
      macd: f2(last('macd_line')),
      stoch: last('stoch_k') == null ? '—' : last('stoch_k').toFixed(1),
    }))
  }

  function applyPivots(pivots) {
    lastPivots.current = pivots
    setReadout((prev) => ({ ...prev, pivots: pivots ? `P ${pivots.p.toFixed(2)}` : '—' }))
    const c = charts.current
    Object.values(c.pivotLines).forEach((line) => c.candle.removePriceLine(line))
    c.pivotLines = {}
    // Price lines have no visible/hidden option of their own (unlike a
    // series) -- "off" means simply not creating them, not creating then
    // immediately destroying, which would leave stale refs in c.pivotLines.
    if (!pivots || toggleState.current.pivots === false) return
    ;['r3', 'r2', 'r1', 'p', 's1', 's2', 's3'].forEach((key) => {
      c.pivotLines[key] = c.candle.createPriceLine({
        price: pivots[key],
        color: key === 'p' ? tok('--ind-vwap') : (key[0] === 'r' ? tok('--critical') : tok('--good')),
        lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: true, title: key.toUpperCase(),
      })
    })
  }

  function applyMarkers(rows) {
    allEventsRef.current = rows
    // readout counts: the current session only
    const current = currentRows.current.markers
    const counts = {}
    const pcounts = {}
    for (const r of current) {
      if (r.activity_type === 'pattern') pcounts[r.activity] = (pcounts[r.activity] ?? 0) + 1
      else counts[r.activity] = (counts[r.activity] ?? 0) + 1
    }
    setSignalCounts(counts)
    setPatternCounts(pcounts)
    refreshMarkers()
  }

  // ONE dot per candle with any shown event (ALGO-25), coloured by the net
  // direction of its events; names and details live in the hover list.
  function refreshMarkers() {
    const shownSignals = showMarksRef.current.signals ? shownSignalsRef.current : new Set()
    const shownPatterns = new Set(showMarksRef.current.patterns ? selectedPatternsRef.current : [])
    // every event on the nearest candle of this timeframe (a 3/5-min event
    // can fall where no candle exists); re-snapped whenever candles change
    snappedEventsRef.current = snapToCandles(allEventsRef.current, lastCandles.current)
    const byTime = new Map()
    for (const r of snappedEventsRef.current) {
      const shown = r.activity_type === 'pattern' ? shownPatterns.has(r.activity) : shownSignals.has(r.activity)
      if (!shown) continue
      const t = eventTime(r)
      byTime.set(t, [...(byTime.get(t) ?? []), r])
    }
    markerEventsRef.current = byTime
    const dots = []
    if (isOn('events', true)) {
      // one mark per KIND on a candle (user, 2026-10-16): circle = raw signal,
      // square = pattern, triangle = strategy (the library's arrow shapes: up
      // below the bar for buy, down above it for sell). Hover/click stay per candle.
      for (const [time, events] of byTime) {
        const strategies = events.filter((e) => e.activity_type === 'strategy')
        const patterns = events.filter((e) => e.activity_type === 'pattern')
        const signals = events.filter((e) => e.activity_type !== 'pattern' && e.activity_type !== 'strategy')
        if (signals.length) {
          const net = signals.reduce((n, e) => n + (e.direction === 'bull' ? 1 : e.direction === 'bear' ? -1 : 0), 0)
          dots.push({ time, shape: 'circle', color: net > 0 ? tok('--good') : net < 0 ? tok('--critical') : tok('--accent') })
        }
        if (patterns.length) {
          // the top pattern's colour -- green bullish, red bearish, pink caution, dark red avoid
          const top = [...patterns].sort((x, y) => (y.score ?? 0) - (x.score ?? 0))[0]
          dots.push({ time, shape: 'square', color: PATTERN_DOT_COLOR[top.pattern_type] ?? tok('--accent') })
        }
        if (strategies.length) {
          const bear = strategies.some((e) => e.direction === 'bear')
          dots.push({ time, shape: bear ? 'triangleDown' : 'triangleUp', color: bear ? tok('--critical') : tok('--good') })
        }
      }
      dots.sort((x, y) => x.time - y.time)
    }
    // drawn by the candle series itself, at the candle body's width (3-12 px)
    const byCandle = new Map()
    for (const d of dots) byCandle.set(d.time, [...(byCandle.get(d.time) ?? []), d])
    charts.current.candleView?.setMarks(byCandle)
    marksVersionRef.current += 1
    charts.current.candle?.applyOptions({ marksVersion: marksVersionRef.current }) // repaint
    const layer = diagramRef.current
    // candles marked (one hover list each); the marks per kind for the UI suite
    if (layer) {
      layer.dataset.dotCount = String(new Set(dots.map((d) => d.time)).size)
      layer.dataset.markShapes = JSON.stringify(dots.reduce((m, d) => ({ ...m, [d.shape]: (m[d.shape] ?? 0) + 1 }), {}))
    }
    drawDiagrams()
  }

  function renderEventList(tip, events) {
    tip.replaceChildren()
    const head = document.createElement('div')
    head.textContent = `${events.length} event${events.length > 1 ? 's' : ''} · ${formatIST(eventTime(events[0]))}`
    head.style.cssText = 'font-size:10.5px;font-weight:700;color:var(--text-faint);margin-bottom:3px;text-transform:uppercase;letter-spacing:.04em'
    tip.append(head)
    const ordered = [...events].sort((a, b) => (b.activity_type === 'pattern') - (a.activity_type === 'pattern'))
    for (const e of ordered) {
      const isPattern = e.activity_type === 'pattern'
      const row = document.createElement('div')
      row.setAttribute('data-event-row', '')
      if (isPattern) row.setAttribute('data-pattern-row', e.activity)
      row.style.cssText = 'display:flex;align-items:center;gap:6px;white-space:nowrap'
      const dot = document.createElement('span')
      dot.style.cssText = `width:7px;height:7px;border-radius:${isPattern ? '1px' : '50%'};flex:none;background:${isPattern && PATTERN_DOT_COLOR[e.pattern_type] ? PATTERN_DOT_COLOR[e.pattern_type] : e.direction === 'bull' ? 'var(--good)' : e.direction === 'bear' ? 'var(--critical)' : 'var(--text-faint)'}`
      const name = document.createElement('span')
      name.textContent = isPattern ? (e.name ?? humanize(e.activity)) : humanize(e.activity)
      if (isPattern) name.style.fontWeight = '700'
      const kind = document.createElement('span')
      kind.textContent = isPattern ? `Pattern${e.score != null ? ` · ${Number(e.score).toFixed(1)}` : ''}` : humanize(e.activity_type)
      kind.style.cssText = 'color:var(--text-faint);font-weight:500;margin-left:auto;padding-left:10px'
      row.append(dot, name, kind)
      tip.append(row)
    }
    const hint = document.createElement('div')
    hint.textContent = 'Click to draw'
    hint.style.cssText = 'font-size:10.5px;font-weight:500;color:var(--text-faint);margin-top:3px'
    tip.append(hint)
  }

  // click a dot: draw its diagrams for DIAGRAM_SECONDS, or until another dot is clicked
  function showDotDiagrams(time) {
    const active = activeDotRef.current
    clearTimeout(active.timer)
    active.time = time
    active.timer = setTimeout(() => { active.time = null; drawDiagrams() }, DIAGRAM_SECONDS * 1000)
    drawDiagrams()
  }

  // The diagram layer: the clicked dot's shapes, plus -- with the Diagrams
  // toggle on -- every shown event's shapes in the visible range.
  function drawDiagrams() {
    const layer = diagramRef.current
    const c = charts.current
    if (!layer || !c.main || !c.candle) return
    const candles = lastCandles.current
    const byTime = markerEventsRef.current
    const events = []
    if (isOn('diagrams', false)) {
      const range = c.main.timeScale().getVisibleRange()
      if (range) for (const [t, evs] of byTime) if (t >= range.from && t <= range.to) events.push(...evs)
    } else {
      const active = activeDotRef.current.time
      if (active != null && byTime.has(active)) events.push(...byTime.get(active))
    }
    layer.dataset.diagramCount = '0'
    if (!events.length || !candles.length) { layer.innerHTML = ''; return }
    const shapes = shapesForEvents(events, indexData(candles, snappedEventsRef.current))
    layer.dataset.diagramCount = String(shapes.length)
    const ts = c.main.timeScale()
    layer.innerHTML = shapesToSvg(
      shapes,
      (t) => ts.timeToCoordinate(t),
      (p) => c.candle.priceToCoordinate(p),
      layer.clientWidth, layer.clientHeight,
      { bull: tok('--good'), bear: tok('--critical'), neutral: tok('--accent') },
      ts.options().barSpacing ?? 6,
      isOn('diagrams', false),
    )
  }

  // ---- live updates: incremental candle+volume, full re-fetch for the rest
  // (indicator values/markers aren't pushed over the socket per candle --
  // see this file's own header comment) ----
  useEffect(() => {
    if (!instrument) return
    let cancelled = false
    const socket = getSocket()
    function onCandleClosed(msg) {
      if (cancelled || msg.symbol !== instrument.symbol || msg.timeframe !== timeframe) return
      // a candle from a newer day than the current session (a new day, or
      // live data after a not-live fallback): reload everything
      if (!sessionStartRef.current || istDayStartIso(msg.candle.ts) > sessionStartRef.current) {
        setReloadKey((k) => k + 1)
        return
      }
      charts.current.candle?.update(toBar(msg.candle))
      colourLastPrice(msg.candle)
      charts.current.volume?.update(toVolumeBar(msg.candle))
      charts.current.spacers?.forEach((s) => s.update(toWhitespace([msg.candle])[0]))
      const withoutDup = lastCandles.current.filter((c) => c.ts !== msg.candle.ts)
      lastCandles.current = [...withoutDup, msg.candle]
      setAsOfDate(null)
      refreshIndicatorsAndMarkers(() => cancelled)
    }
    function onBackfillStatus(status) {
      if (cancelled || status.symbol !== instrument.symbol || status.status !== 'done') return
      setReloadKey((k) => k + 1)
    }
    socket.on('candle_closed', onCandleClosed)
    socket.on('backfill_status', onBackfillStatus)
    return () => {
      cancelled = true
      socket.off('candle_closed', onCandleClosed)
      socket.off('backfill_status', onBackfillStatus)
    }
  }, [instrument, timeframe])

  // Caps the indicator rail to the chart stack's real height (never lets
  // the picker grow the chart) -- called on mount, whenever the stack's
  // own height changes (the railObserver in the build effect above), and
  // whenever the rail itself first mounts (indicatorsOpen effect below,
  // since opening the panel doesn't change the STACK's size, so the
  // ResizeObserver alone never fires for that transition).
  function syncRailHeight() {
    if (!stackRef.current || !railRef.current) return
    // exactly the chart's height -- never taller (maxHeight too, in case content grows first)
    const h = `${stackRef.current.getBoundingClientRect().height}px`
    railRef.current.style.height = h
    railRef.current.style.maxHeight = h
  }
  useEffect(() => { syncRailHeight() }, [indicatorsMode, !!sidePanel]) // eslint-disable-line react-hooks/exhaustive-deps

  // ---- indicator picker ----
  function isOn(key, defaultOn = true) {
    return toggleState.current[key] !== undefined ? toggleState.current[key] : defaultOn
  }
  function setToggle(key, value) {
    toggleState.current[key] = value
    setPref('chart.toggles', { ...toggleState.current })
  }
  // after a (re)build: the user's saved overlay/panel choices, not the defaults
  function applySavedVisibility() {
    const c = charts.current
    for (const d of OVERLAY_DEFS) {
      const on = isOn(d.key, d.defaultOn)
      if (d.key === 'pivots') continue // applyPivots reads toggleState itself
      if (d.key === 'bb') [c.bbUpper, c.bbMiddle, c.bbLower, c.bbFillUpper, c.bbFillLower].forEach((s) => s?.applyOptions({ visible: on }))
      else c[d.key]?.applyOptions({ visible: on })
    }
    for (const d of PANEL_DEFS) {
      const elRef = d.key === 'rsi' ? rsiElRef : d.key === 'macd' ? macdElRef : stochElRef
      const wrapper = elRef.current?.closest('[data-pane]')
      if (wrapper) wrapper.hidden = !paneShown(d.key)
    }
  }
  function toggleOverlay(key) {
    const next = !isOn(key, OVERLAY_DEFS.find((d) => d.key === key)?.defaultOn)
    setToggle(key, next)
    const c = charts.current
    if (key === 'volume') c.volume.applyOptions({ visible: next })
    else if (key === 'vwap') c.vwap.applyOptions({ visible: next })
    else if (key === 'bb') [c.bbUpper, c.bbMiddle, c.bbLower, c.bbFillUpper, c.bbFillLower].forEach((s) => s.applyOptions({ visible: next }))
    else if (key === 'pivots') applyPivots(lastPivots.current) // price lines have no visible toggle of their own -- rebuild instead (applyPivots reads toggleState.current.pivots, just set above)
    else c[key]?.applyOptions({ visible: next })
    forceRender((n) => n + 1)
  }
  function togglePanel(key) {
    const next = !isOn(key, true)
    setToggle(key, next)
    const elRef = key === 'rsi' ? rsiElRef : key === 'macd' ? macdElRef : stochElRef
    const wrapper = elRef.current?.closest('[data-pane]')
    if (wrapper) wrapper.hidden = !paneShown(key)
    updateTimeAxisVisibility()
    syncRailHeight()
    forceRender((n) => n + 1)
  }
  // toolbar toggles (ALGO-25): event dots on/off, all diagrams on/off
  function toggleEvents() {
    setToggle('events', !isOn('events', true))
    refreshMarkers()
    forceRender((n) => n + 1)
  }
  function toggleDiagrams() {
    setToggle('diagrams', !isOn('diagrams', false))
    drawDiagrams()
    forceRender((n) => n + 1)
  }
  function paneShown(key) {
    return !graphOnlyRef.current && isOn(key, true)
  }
  function applyPaneVisibility() {
    for (const d of PANEL_DEFS) {
      const elRef = d.key === 'rsi' ? rsiElRef : d.key === 'macd' ? macdElRef : stochElRef
      const wrapper = elRef.current?.closest('[data-pane]')
      if (wrapper) wrapper.hidden = !paneShown(d.key)
    }
    updateTimeAxisVisibility()
    syncRailHeight()
  }
  function toggleGraphOnly() {
    const v = !graphOnlyRef.current
    graphOnlyRef.current = v
    setPref('chart.graphOnly', v)
    setGraphOnlyState(v)
    applyPaneVisibility()
  }
  function updateTimeAxisVisibility() {
    const c = charts.current
    const entries = [
      { chart: c.main, visible: true },
      { chart: c.rsi, visible: paneShown('rsi') },
      { chart: c.macd, visible: paneShown('macd') },
      { chart: c.stoch, visible: paneShown('stoch') },
    ]
    const visible = entries.filter((e) => e.visible)
    const last = visible[visible.length - 1]
    entries.forEach((e) => e.chart?.applyOptions({ timeScale: { visible: e === last } }))
  }

  return (
    <div style={{
      width: '100%', minWidth: 0,
      height: fillHeight ? '100%' : undefined,
      display: fillHeight ? 'flex' : undefined,
      flexDirection: fillHeight ? 'column' : undefined,
    }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12, justifyContent: onCycleDepth ? 'flex-start' : 'space-between' }}>
        {asOfDate ? (
          <span style={{
            fontSize: 11.5, fontWeight: 600, color: 'var(--text-faint)', background: 'var(--surface-alt)',
            border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', padding: '3px 8px',
          }}
          >
            Showing {asOfDate} data — not live
          </span>
        ) : <span />}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {/* Icon toolbar (ALGO-25): each icon has a tooltip and shows its on/off state. */}
          <div role="toolbar" aria-label="Chart tools" data-chart-toolbar style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            {onCycleDepth ? (
              <ChartToolButton
                ref={depthBtnRef} tool="depth" onClick={onCycleDepth} on={depthMode !== 'hidden'} disabled={graphOnly}
                badge={depthMode === 'right' ? '→' : depthMode === 'below' ? '↓' : null}
                label={depthMode === 'right' ? 'Depth: shown beside the chart (click to move below)' : depthMode === 'below' ? 'Depth: shown below the chart (click to hide)' : 'Depth: hidden (click to show beside the chart)'}
              />
            ) : onToggleDepth ? (
              <ChartToolButton ref={depthBtnRef} tool="depth" onClick={onToggleDepth} on={!!depthOpen} label={depthOpen ? 'Depth: shown (click to hide)' : 'Depth: hidden (click to show)'} />
            ) : null}
            <ChartToolButton
              tool="indicators" onClick={cycleIndicatorsMode} on={indicatorsOpen} disabled={graphOnly}
              badge={indicatorsMode === 'right' ? '→' : indicatorsMode === 'below' ? '↓' : null}
              label={indicatorsMode === 'right' ? 'Signals, patterns & strategies: beside the chart (click to move below)' : indicatorsMode === 'below' ? 'Signals, patterns & strategies: below the chart (click to hide)' : 'Signals, patterns & strategies: hidden (click to show beside the chart)'}
            />
            <span aria-hidden="true" style={{ width: 1, height: 20, background: 'var(--border)', margin: '0 2px' }} />
            <ChartToolButton tool="events" onClick={toggleEvents} on={isOn('events', true)} label={isOn('events', true) ? 'Event marking: on (click to hide the dots)' : 'Event marking: off (click to show the dots)'} />
            <ChartToolButton tool="diagrams" onClick={toggleDiagrams} on={isOn('diagrams', false)} label={isOn('diagrams', false) ? 'Diagrams: all shown (click to hide)' : 'Diagrams: show all (or click a dot to draw just its own)'} />
            <span aria-hidden="true" style={{ width: 1, height: 20, background: 'var(--border)', margin: '0 2px' }} />
            {PANEL_DEFS.map((d) => (
              <ChartToolButton key={d.key} tool={d.key} onClick={() => togglePanel(d.key)} on={isOn(d.key, true)} disabled={graphOnly} label={`${d.label} panel: ${isOn(d.key, true) ? 'shown (click to hide)' : 'hidden (click to show)'}`} />
            ))}
            {!onToggleDepth && (
              <>
                <span aria-hidden="true" style={{ width: 1, height: 20, background: 'var(--border)', margin: '0 2px' }} />
                <ChartToolButton
                  tool="graphonly" onClick={toggleGraphOnly} on={graphOnly}
                  label={graphOnly ? 'Graph only: on -- panels hidden (click to bring them back as they were)' : 'Graph only: off (click to hide the RSI/MACD/Stoch panes, Depth and the Indicators window)'}
                />
              </>
            )}
          </div>
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          {canSync && (
            <ChartToolButton
              tool="sync" on={syncTf} onClick={() => setSyncTf((v) => !v)}
              label={syncTf ? 'Timeframe synced with the screen (click to choose it here instead)' : 'Timeframe set on this chart (click to follow the screen timeframe)'}
            />
          )}
          <div ref={tfToggleRef} style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', overflow: 'hidden' }}>
            {TIMEFRAMES.map((tf) => (
              <button
                key={tf} onClick={() => { setTimeframe(tf); setPref('chart.timeframe', tf); if (canSync && tf !== screenTimeframe) setSyncTf(false) }}
                data-chart-tf={tf} aria-pressed={tf === timeframe}
                style={{ border: 'none', fontSize: 12, fontWeight: 600, padding: '5px 10px', color: tf === timeframe ? 'var(--text-on-accent)' : 'var(--text-dim)', background: tf === timeframe ? 'var(--accent)' : 'transparent' }}
              >
                {tf}
              </button>
            ))}
          </div>
          </div>
        </div>
      </div>

      {/* Indicators above the graph (2026-10-16): overlays + panes, each an on/off chip with its value */}
      <IndicatorStrip
        values={readout}
        groups={[
          { title: 'Overlays', defs: OVERLAY_DEFS, isOn, onToggle: toggleOverlay },
          { title: 'Panes', defs: PANEL_DEFS, isOn: (k) => isOn(k, true), onToggle: togglePanel, disabled: graphOnly },
        ]}
      />

      {/* flex-start, not stretch: a right-hand column that renders before
          it is sized (Indicators saved as "right" on page load) used to
          stretch the chart frame to its own content height, and the
          column was then sized to that stretched frame (2026-10-15).
          Fullscreen still stretches -- the chart fills the screen there. */}
      <div style={{ display: 'flex', gap: 14, alignItems: fillHeight ? 'stretch' : 'flex-start', flex: fillHeight ? '1 1 auto' : undefined, minHeight: fillHeight ? 0 : undefined }}>
        <div ref={stackRef} data-chart-stack style={{ flex: '1 1 auto', minWidth: 0, border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', overflow: 'hidden', display: fillHeight ? 'flex' : undefined, flexDirection: fillHeight ? 'column' : undefined }}>
          <div style={{ position: 'relative', flex: fillHeight ? '1 1 auto' : undefined, minHeight: fillHeight ? 0 : undefined }}>
            {/* height:100% in fullscreen so the price pane fills whatever the
                sub-panels leave -- without it this div only ever wraps the
                canvas's own 260px and resizeCharts() reads that back, so
                fullscreen never grew (caught by ui_test_suite.cjs). */}
            <div ref={containerRef} style={{ width: '100%', height: fillHeight ? '100%' : undefined, overflow: fillHeight ? 'hidden' : undefined }} />
            <div ref={daySepRef} data-day-separators aria-hidden="true" style={{ position: 'absolute', inset: 0, zIndex: 3, pointerEvents: 'none', overflow: 'hidden' }} />
            <div ref={selectedLabelRef} data-selected-line-label hidden
              style={{ position: 'absolute', top: 6, left: 8, zIndex: 4, fontSize: 12, fontWeight: 700, padding: '2px 8px', borderRadius: 'var(--radius-sm)', background: 'var(--surface)', border: '1px solid var(--border)', pointerEvents: 'none' }} />
            <div ref={diagramRef} data-diagrams aria-hidden="true" style={{ position: 'absolute', inset: 0, zIndex: 3, pointerEvents: 'none', overflow: 'hidden' }} />
            <div
              ref={tooltipRef} data-marker-tooltip role="tooltip"
              style={{
                display: 'none', position: 'absolute', pointerEvents: 'none', zIndex: 4, maxWidth: 360, lineHeight: 1.5,
                fontSize: 11.5, fontWeight: 600, color: 'var(--text)', background: 'var(--surface)',
                border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', boxShadow: 'var(--shadow-sm)', padding: '4px 8px',
              }}
            />
          </div>
          <div data-pane style={{ borderTop: '1px solid var(--border)' }}><div ref={rsiElRef} style={{ width: '100%' }} /></div>
          <div data-pane style={{ borderTop: '1px solid var(--border)' }}><div ref={macdElRef} style={{ width: '100%' }} /></div>
          <div data-pane style={{ borderTop: '1px solid var(--border)' }}><div ref={stochElRef} style={{ width: '100%' }} /></div>
        </div>

        {/* One right-hand column: the caller's side panel (Depth, from
            GraphWindow) on top, the Indicators window stacked under it --
            they used to sit side by side as two columns (2026-10-15). */}
        {!graphOnly && (indicatorsMode === 'right' || sidePanel) && (
        <div
          ref={railRef} data-right-column
          style={{ flex: `0 0 ${RIGHT_COLUMN_WIDTH}px`, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 12, overflow: 'hidden' }}
        >
        {sidePanel && <div data-side-panel style={{ flex: indicatorsMode === 'right' ? '0 0 auto' : '1 1 auto', minHeight: 0, overflowY: 'auto' }}>{sidePanel}</div>}
        {indicatorsMode === 'right' && renderIndicatorsWindow({ flex: '1 1 auto', minHeight: 0 })}
        </div>
        )}
      </div>
      {/* below the chart: Depth first, then the Indicators window beside it */}
      {!graphOnly && (indicatorsMode === 'below' || bottomPanel) && (
        <div data-bottom-row style={{ display: 'flex', flexWrap: 'wrap', gap: 14, marginTop: 14, alignItems: 'stretch' }}>
          {bottomPanel && <div data-side-panel style={{ flex: `0 0 ${RIGHT_COLUMN_WIDTH}px`, minWidth: 0, maxHeight: BOTTOM_ROW_HEIGHT, overflowY: 'auto' }}>{bottomPanel}</div>}
          {indicatorsMode === 'below' && renderIndicatorsWindow({ flex: '1 1 320px', minWidth: 0, height: BOTTOM_ROW_HEIGHT })}
        </div>
      )}
    </div>
  )

  function renderIndicatorsWindow(layout) {
    return (
      <div
        data-indicators-window={indicatorsMode}
        style={{
          ...layout, overflowY: 'auto', background: 'var(--surface)', border: '1px solid var(--border)',
          borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow-sm)', padding: 12, boxSizing: 'border-box',
        }}
      >
        {/* ALGO-34: three tabs -- Indicators (unchanged content), Patterns, Strategies */}
        <IndicatorPanelTabs show={showMarks} onShowChange={setShowMark} patternCounts={patternCounts} onOpenReview={reviewFor} onPatternsChange={(keys) => { selectedPatternsRef.current = keys; refreshMarkers() }} onStrategiesChange={(ids) => { selectedStrategiesRef.current = ids }}>
          <SignalPicker selected={effectiveSignals} onChange={setSelectedSignals} counts={signalCounts} disabled={!showMarks.signals} onOpenReview={reviewFor} />
        </IndicatorPanelTabs>
      </div>
    )
  }
}

