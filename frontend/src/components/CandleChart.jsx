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
import { useEffect, useRef, useState } from 'react'
import { createChart, CrosshairMode, LineStyle } from 'lightweight-charts'
import {
  getCandles, getCandleIndicators, getCandleMarkers, getCandlePivots,
} from '../api/client.js'
import { getSocket } from '../api/ws.js'
import { useTheme } from '../ThemeContext.jsx'

const TIMEFRAMES = ['1min', '3min', '5min']
const MAIN_HEIGHT = 260
const SUB_HEIGHT = 70
const IST_OFFSET_SECONDS = 5.5 * 60 * 60

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

function toBar(candle) {
  return {
    time: Math.floor(new Date(candle.ts).getTime() / 1000),
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
    timeScale: { borderColor: tok('--chart-border'), timeVisible: true, secondsVisible: false, tickMarkFormatter: formatIST },
    localization: { timeFormatter: formatIST },
    crosshair: { mode: CrosshairMode.Normal },
  }
}

function pillButtonStyle(active) {
  return {
    fontSize: 12.5, fontWeight: 700, border: '1.5px solid var(--border)', borderRadius: 'var(--radius-md)',
    padding: '6px 16px', color: active ? 'var(--accent-text)' : 'var(--text-dim)',
    background: active ? 'var(--accent-soft)' : 'transparent',
    borderColor: active ? 'var(--accent)' : 'var(--border)',
  }
}

const OVERLAY_DEFS = [
  { key: 'volume', label: 'Volume', defaultOn: true },
  { key: 'vwap', label: 'VWAP', defaultOn: true },
  { key: 'bb', label: 'Bollinger Bands (20, 2σ)', defaultOn: true },
  { key: 'ema5', label: 'EMA 5', defaultOn: true },
  { key: 'ema14', label: 'EMA 14', defaultOn: true },
  { key: 'ema21', label: 'EMA 21', defaultOn: false },
  { key: 'ema50', label: 'EMA 50', defaultOn: false },
  { key: 'ma21', label: 'MA 21', defaultOn: false },
  { key: 'ma50', label: 'MA 50', defaultOn: false },
  { key: 'pivots', label: 'Pivot Points (classic)', defaultOn: true },
]
const PANEL_DEFS = [
  { key: 'rsi', label: 'RSI (14)' },
  { key: 'macd', label: 'MACD (12, 26, 9)' },
  { key: 'stoch', label: 'Stochastic (14, 3)' },
]
const MARKER_DEFS = [
  { key: 'patterns', label: 'Chart patterns', types: ['candle_pattern', 'graph_formation'] },
  { key: 'signals', label: 'Strategy signals', types: ['indicator'] },
]

export default function CandleChart({
  instrument, fillHeight = false, depthOpen, onToggleDepth, onControlsWidthChange,
  depthMode, onCycleDepth,
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
  const markersRef = useRef({ patterns: [], signals: [] })
  const toggleState = useRef({}) // key -> bool, read by the toggle*/refresh* functions below
  const lastPivots = useRef(null) // the raw pivots object, so re-enabling the toggle after turning it off doesn't need a re-fetch
  const lastCandles = useRef([]) // so a theme change can recolor the volume bars (their up/down color is baked in per-bar, not a series-level option)
  const rangeRef = useRef(null) // {from, to} of the fallback day being shown, or null when live (endpoints default to today)
  const [readout, setReadout] = useState({}) // latest value per picker row, shown beside each checkbox

  const [timeframe, setTimeframe] = useState('1min')
  const [asOfDate, setAsOfDate] = useState(null)
  const [indicatorsOpen, setIndicatorsOpen] = useState(false)
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
    c.candle = c.main.addCandlestickSeries({ upColor: tok('--up'), downColor: tok('--down'), borderVisible: false, wickUpColor: tok('--up'), wickDownColor: tok('--down') })
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

    // keep every pane's visible time range in lockstep
    const allPanes = [c.main, c.rsi, c.macd, c.stoch]
    let syncing = false
    allPanes.forEach((chart) => {
      chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (!range || syncing) return
        syncing = true
        allPanes.forEach((other) => { if (other !== chart) other.timeScale().setVisibleLogicalRange(range) })
        syncing = false
      })
    })

    const resizeObserver = new ResizeObserver(() => resizeCharts())
    resizeObserver.observe(containerRef.current)
    const railObserver = new ResizeObserver(() => syncRailHeight())
    if (stackRef.current) railObserver.observe(stackRef.current)

    function resizeCharts() {
      if (!containerRef.current) return
      const width = containerRef.current.clientWidth
      c.main.resize(width, fillHeight ? (containerRef.current.clientHeight || MAIN_HEIGHT) : MAIN_HEIGHT)
      ;[c.rsi, c.macd, c.stoch].forEach((chart) => chart.resize(width, SUB_HEIGHT))
    }
    syncRailHeight()
    updateTimeAxisVisibility()

    return () => {
      resizeObserver.disconnect()
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
      if (lastCandles.current.length) c.volume.setData(lastCandles.current.map(toVolumeBar)) // per-bar color is baked in, not a series option -- must be re-set, not applyOptions'd
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
    loadAll()
    return () => { cancelled = true }

    async function loadAll() {
      const [candlesRes, pivotsRes] = await Promise.all([
        getCandles(instrument.symbol, instrument.exchange_segment, timeframe).catch(() => null),
        getCandlePivots(instrument.symbol, instrument.exchange_segment).catch(() => null),
      ])
      if (cancelled) return
      if (candlesRes) {
        charts.current.candle.setData(candlesRes.candles.map(toBar))
        lastCandles.current = candlesRes.candles
        charts.current.volume.setData(candlesRes.candles.map(toVolumeBar))
        charts.current.spacers.forEach((s) => s.setData(toWhitespace(candlesRes.candles)))
        setAsOfDate(candlesRes.live ? null : candlesRes.as_of_date)
        charts.current.main.timeScale().fitContent()
      }
      if (pivotsRes) applyPivots(pivotsRes.pivots)
      // Indicators/markers must cover the SAME day the candles show -- when
      // there's no live data the candles fall back to a previous session,
      // and the endpoints' own "today" default would then return nothing.
      const shown = candlesRes?.candles ?? []
      rangeRef.current = candlesRes && !candlesRes.live && shown.length
        ? { from: shown[0].ts, to: shown[shown.length - 1].ts }
        : null
      await refreshIndicatorsAndMarkers(() => cancelled)
    }
  }, [instrument, timeframe])

  async function refreshIndicatorsAndMarkers(isCancelled) {
    const { from, to } = rangeRef.current ?? {}
    const [indicatorsRes, markersRes] = await Promise.all([
      getCandleIndicators(instrument.symbol, instrument.exchange_segment, timeframe, from, to).catch(() => null),
      getCandleMarkers(instrument.symbol, instrument.exchange_segment, timeframe, from, to).catch(() => null),
    ])
    if (isCancelled()) return
    if (indicatorsRes) applyIndicatorRows(indicatorsRes.indicators)
    if (markersRes) applyMarkers(markersRes.markers)
  }

  function applyIndicatorRows(rows) {
    const c = charts.current
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
    markersRef.current = {
      patterns: rows.filter((r) => MARKER_DEFS[0].types.includes(r.activity_type)).map(markerFor),
      signals: rows.filter((r) => MARKER_DEFS[1].types.includes(r.activity_type)).map(markerFor),
    }
    setReadout((prev) => ({
      ...prev,
      patterns: String(markersRef.current.patterns.length),
      signals: String(markersRef.current.signals.length),
    }))
    refreshMarkers()
  }

  function markerFor(row) {
    const bull = row.direction === 'bull'
    const bear = row.direction === 'bear'
    const color = bull ? tok('--good') : bear ? tok('--critical') : tok('--text-faint')
    const isSignal = row.activity_type === 'indicator'
    return {
      time: Math.floor(new Date(row.ts).getTime() / 1000),
      position: bear ? 'aboveBar' : 'belowBar',
      color,
      shape: isSignal ? (bear ? 'arrowDown' : 'arrowUp') : 'circle',
      text: row.activity,
    }
  }

  function refreshMarkers() {
    const active = []
    if (toggleState.current.patterns !== false) active.push(...markersRef.current.patterns)
    if (toggleState.current.signals !== false) active.push(...markersRef.current.signals)
    active.sort((a, b) => a.time - b.time)
    charts.current.candle?.setMarkers(active)
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
      charts.current.candle?.update(toBar(msg.candle))
      charts.current.volume?.update(toVolumeBar(msg.candle))
      charts.current.spacers?.forEach((s) => s.update(toWhitespace([msg.candle])[0]))
      const withoutDup = lastCandles.current.filter((c) => c.ts !== msg.candle.ts)
      lastCandles.current = [...withoutDup, msg.candle]
      setAsOfDate(null)
      rangeRef.current = null // a real live candle landed -- we're on today now
      refreshIndicatorsAndMarkers(() => cancelled)
    }
    function onBackfillStatus(status) {
      if (cancelled || status.symbol !== instrument.symbol || status.status !== 'done') return
      getCandles(instrument.symbol, instrument.exchange_segment, timeframe).catch(() => null).then((candlesRes) => {
        if (cancelled || !candlesRes) return
        charts.current.candle?.setData(candlesRes.candles.map(toBar))
        lastCandles.current = candlesRes.candles
        charts.current.volume?.setData(candlesRes.candles.map(toVolumeBar))
        charts.current.spacers?.forEach((s) => s.setData(toWhitespace(candlesRes.candles)))
        setAsOfDate(candlesRes.live ? null : candlesRes.as_of_date)
        const shown = candlesRes.candles
        rangeRef.current = !candlesRes.live && shown.length ? { from: shown[0].ts, to: shown[shown.length - 1].ts } : null
        refreshIndicatorsAndMarkers(() => cancelled)
      })
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
    railRef.current.style.height = `${stackRef.current.getBoundingClientRect().height}px`
  }
  useEffect(() => { if (indicatorsOpen) syncRailHeight() }, [indicatorsOpen])

  // ---- indicator picker ----
  function isOn(key, defaultOn = true) {
    return toggleState.current[key] !== undefined ? toggleState.current[key] : defaultOn
  }
  function toggleOverlay(key) {
    const next = !isOn(key, OVERLAY_DEFS.find((d) => d.key === key)?.defaultOn)
    toggleState.current[key] = next
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
    toggleState.current[key] = next
    const elRef = key === 'rsi' ? rsiElRef : key === 'macd' ? macdElRef : stochElRef
    const wrapper = elRef.current?.closest('[data-pane]')
    if (wrapper) wrapper.hidden = !next
    updateTimeAxisVisibility()
    syncRailHeight()
    forceRender((n) => n + 1)
  }
  function toggleMarkerGroup(key) {
    const next = !isOn(key, true)
    toggleState.current[key] = next
    refreshMarkers()
    forceRender((n) => n + 1)
  }
  function updateTimeAxisVisibility() {
    const c = charts.current
    const entries = [
      { chart: c.main, visible: true },
      { chart: c.rsi, visible: isOn('rsi', true) },
      { chart: c.macd, visible: isOn('macd', true) },
      { chart: c.stoch, visible: isOn('stoch', true) },
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
          {onCycleDepth ? (
            <button
              ref={depthBtnRef} onClick={onCycleDepth}
              title={depthMode === 'right' ? 'Depth: shown beside the chart (click to move below)' : depthMode === 'below' ? 'Depth: shown below the chart (click to hide)' : 'Depth: hidden (click to show beside the chart)'}
              style={pillButtonStyle(depthMode !== 'hidden')}
            >
              Depth{depthMode === 'right' ? ' →' : depthMode === 'below' ? ' ↓' : ''}
            </button>
          ) : onToggleDepth ? (
            <button ref={depthBtnRef} onClick={onToggleDepth} style={pillButtonStyle(depthOpen)}>Depth</button>
          ) : null}
          <button onClick={() => setIndicatorsOpen((v) => !v)} style={pillButtonStyle(indicatorsOpen)}>Indicators</button>
          <div ref={tfToggleRef} style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', overflow: 'hidden' }}>
            {TIMEFRAMES.map((tf) => (
              <button
                key={tf} onClick={() => setTimeframe(tf)}
                style={{ border: 'none', fontSize: 12, fontWeight: 600, padding: '5px 10px', color: tf === timeframe ? 'var(--text-on-accent)' : 'var(--text-dim)', background: tf === timeframe ? 'var(--accent)' : 'transparent' }}
              >
                {tf}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 14, flex: fillHeight ? '1 1 auto' : undefined, minHeight: fillHeight ? 0 : undefined }}>
        <div ref={stackRef} style={{ flex: '1 1 auto', minWidth: 0, border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', overflow: 'hidden', display: fillHeight ? 'flex' : undefined, flexDirection: fillHeight ? 'column' : undefined }}>
          <div style={{ flex: fillHeight ? '1 1 auto' : undefined, minHeight: fillHeight ? 0 : undefined }}>
            {/* height:100% in fullscreen so the price pane fills whatever the
                sub-panels leave -- without it this div only ever wraps the
                canvas's own 260px and resizeCharts() reads that back, so
                fullscreen never grew (caught by ui_test_suite.cjs). */}
            <div ref={containerRef} style={{ width: '100%', height: fillHeight ? '100%' : undefined, overflow: fillHeight ? 'hidden' : undefined }} />
          </div>
          <div data-pane style={{ borderTop: '1px solid var(--border)' }}><div ref={rsiElRef} style={{ width: '100%' }} /></div>
          <div data-pane style={{ borderTop: '1px solid var(--border)' }}><div ref={macdElRef} style={{ width: '100%' }} /></div>
          <div data-pane style={{ borderTop: '1px solid var(--border)' }}><div ref={stochElRef} style={{ width: '100%' }} /></div>
        </div>

        {indicatorsOpen && (
          <div
            ref={railRef}
            style={{
              flex: '0 0 240px', overflowY: 'auto', background: 'var(--surface)', border: '1px solid var(--border)',
              borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow-sm)', padding: 12,
            }}
          >
            <IndicatorGroup title="Overlays" defs={OVERLAY_DEFS} isOn={isOn} onToggle={toggleOverlay} values={readout} />
            <IndicatorGroup title="Sub-panels" defs={PANEL_DEFS} isOn={(k) => isOn(k, true)} onToggle={togglePanel} values={readout} />
            <IndicatorGroup title="Patterns & signals" defs={MARKER_DEFS} isOn={(k) => isOn(k, true)} onToggle={toggleMarkerGroup} values={readout} />
          </div>
        )}
      </div>
    </div>
  )
}

function IndicatorGroup({ title, defs, isOn, onToggle, values = {} }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <p style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.04em', color: 'var(--text-faint)', margin: '0 0 8px' }}>{title}</p>
      {defs.map((def) => {
        const on = isOn(def.key, def.defaultOn)
        return (
          <label key={def.key} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 2px', cursor: 'pointer', fontSize: 12.5, color: on ? 'var(--text)' : 'var(--text-faint)' }}>
            <input type="checkbox" checked={on} onChange={() => onToggle(def.key)} />
            <span style={{ flex: '1 1 auto', minWidth: 0 }}>{def.label}</span>
            <span className="num" data-readout={def.key} style={{ fontSize: 11.5, color: on ? 'var(--text-dim)' : 'var(--text-faint)', textAlign: 'right' }}>
              {values[def.key] ?? '—'}
            </span>
          </label>
        )
      })}
    </div>
  )
}
