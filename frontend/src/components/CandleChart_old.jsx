// FROZEN 2026-10-06 -- pre-indicator-overlay version of this widget, kept
// as a reference/rollback point rather than deleted (same "never delete
// pages, rename + add new one alongside" discipline as
// MarketWatchClassic.jsx). CandleChart.jsx is now the active widget
// (VWAP/BB/EMA/MA overlays, RSI/MACD/Stochastic sub-panels, volume, pivot
// points, pattern/signal markers, indicator picker -- see memory:
// live_indicators_phase1_priority). Not imported/wired into anything;
// this file is inert unless a caller is deliberately pointed at it.
import { useEffect, useRef, useState } from 'react'
import { createChart } from 'lightweight-charts'
import { getCandles } from '../api/client.js'
import { getSocket } from '../api/ws.js'
import { useTheme } from '../ThemeContext.jsx'

const TIMEFRAMES = ['1min', '3min', '5min']
const CHART_HEIGHT = 190
const IST_OFFSET_SECONDS = 5.5 * 60 * 60

// lightweight-charts takes literal color strings, not CSS var() references,
// so theme.css's --chart-* tokens (already defined for exactly this) have to
// be read off the live DOM and pushed into the chart imperatively -- applied
// once at creation and again on every theme change (see the effect below).
function readChartColors() {
  const style = getComputedStyle(document.documentElement)
  const get = (name) => style.getPropertyValue(name).trim()
  return {
    background: get('--chart-bg'), grid: get('--chart-grid'),
    text: get('--chart-text'), border: get('--chart-border'),
    up: get('--up'), down: get('--down'),
  }
}

function applyChartTheme(chart, series) {
  const c = readChartColors()
  chart.applyOptions({
    layout: { background: { color: c.background }, textColor: c.text },
    grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
    rightPriceScale: { borderColor: c.border },
    timeScale: { borderColor: c.border },
  })
  series.applyOptions({
    upColor: c.up, downColor: c.down, borderUpColor: c.up, borderDownColor: c.down,
    wickUpColor: c.up, wickDownColor: c.down,
  })
}

// NSE always trades in IST, regardless of the viewing browser's own
// timezone — lightweight-charts otherwise formats time labels using the
// browser's local timezone (via native Date getters), which showed raw
// UTC hours here since this environment's browser timezone isn't IST.
// Shifting by the fixed IST offset and reading back with UTC getters makes
// the display deterministic instead of viewer-dependent.
function formatIST(unixSeconds) {
  const shifted = new Date((unixSeconds + IST_OFFSET_SECONDS) * 1000)
  const hh = String(shifted.getUTCHours()).padStart(2, '0')
  const mm = String(shifted.getUTCMinutes()).padStart(2, '0')
  return `${hh}:${mm}`
}

export default function CandleChart({
  instrument, fillHeight = false, depthOpen, onToggleDepth, onControlsWidthChange,
  depthMode, onCycleDepth,
}) {
  const containerRef = useRef(null)
  const chartRef = useRef(null)
  const seriesRef = useRef(null)
  const depthBtnRef = useRef(null)
  const tfToggleRef = useRef(null)
  const [timeframe, setTimeframe] = useState('1min')
  // null = today's live candles (the normal case). Set to a date string
  // whenever the backend had no candles_today rows yet and fell back to the
  // most recent day with historical data instead -- surfaced so stale data
  // never looks indistinguishable from a live feed on a trading screen.
  const [asOfDate, setAsOfDate] = useState(null)
  const { theme } = useTheme()

  // Reports the Depth+timeframe button group's own rendered width (its
  // left-to-right extent, not the full-width flex row it sits in) so the
  // sibling depth panel can be given that exact same width -- both are
  // right-aligned to the same card edge, so matching widths makes their
  // left edges coincide, putting the panel directly under the Depth button.
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

  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: fillHeight ? containerRef.current.clientHeight || CHART_HEIGHT : CHART_HEIGHT,
      timeScale: { timeVisible: true, secondsVisible: false, tickMarkFormatter: formatIST },
      localization: { timeFormatter: formatIST },
      layout: { attributionLogo: false }, // removes the "TV" (TradingView) watermark
    })
    const series = chart.addCandlestickSeries()
    chartRef.current = chart
    seriesRef.current = series
    applyChartTheme(chart, series)

    // responsive: the chart's own canvas is fixed-size at creation, so it must
    // be told to resize whenever its container does (e.g. the grid reflowing
    // between 3-per-row on desktop and 1-per-row on mobile, or entering/leaving
    // fullscreen where fillHeight lets it track the container's actual height
    // instead of the fixed CHART_HEIGHT)
    const resizeObserver = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect
      if (!rect) return
      const options = { width: rect.width }
      if (fillHeight && rect.height) options.height = rect.height
      chart.applyOptions(options)
    })
    resizeObserver.observe(containerRef.current)

    return () => {
      resizeObserver.disconnect()
      chart.remove()
    }
  }, [fillHeight])

  // Re-push chart colors whenever the theme actually changes: an explicit
  // toggle (theme itself changes) or, in 'system' mode, the OS preference
  // flipping underneath an unchanged 'system' value -- ThemeContext doesn't
  // re-render on that, so it needs its own matchMedia listener here.
  useEffect(() => {
    if (!chartRef.current || !seriesRef.current) return
    applyChartTheme(chartRef.current, seriesRef.current)
    if (theme !== 'system') return
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => applyChartTheme(chartRef.current, seriesRef.current)
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [theme])

  useEffect(() => {
    if (!instrument || !seriesRef.current) return
    // `cancelled` guards against this resolving after the chart was
    // unmounted/disposed (e.g. closing a tab, exiting fullscreen, or just
    // switching instrument/timeframe again before the fetch lands) --
    // seriesRef.current itself isn't cleared on unmount, and
    // lightweight-charts throws "Object is disposed" rather than silently
    // no-op'ing when a disposed series is touched. Found live 2026-10-05:
    // the quick tab/fullscreen-switching flow in the new Market Watch
    // design hits this far more than the old single-chart page did.
    let cancelled = false
    getCandles(instrument.symbol, instrument.exchange_segment, timeframe)
      .then((res) => {
        if (cancelled) return
        seriesRef.current.setData(res.candles.map(toBar))
        setAsOfDate(res.live ? null : res.as_of_date)
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [instrument, timeframe])

  useEffect(() => {
    if (!instrument) return
    let cancelled = false // see the data-fetch effect above for why this is needed
    const socket = getSocket()
    function onCandleClosed(msg) {
      if (cancelled || msg.symbol !== instrument.symbol || msg.timeframe !== timeframe) return
      seriesRef.current?.update(toBar(msg.candle))
      setAsOfDate(null) // a real live candle just landed -- no longer showing fallback data
    }
    // a background backfill (historical catch-up) doesn't push candle_closed
    // per candle — it's a bulk REST-shaped update, not a live one — so this
    // chart does one clean full re-fetch when it sees the matching "done"
    // status instead, rather than staying stale until the user switches
    // timeframe or reopens the chart
    function onBackfillStatus(status) {
      if (cancelled || status.symbol !== instrument.symbol || status.status !== 'done') return
      getCandles(instrument.symbol, instrument.exchange_segment, timeframe)
        .then((res) => {
          if (cancelled) return
          seriesRef.current?.setData(res.candles.map(toBar))
          setAsOfDate(res.live ? null : res.as_of_date)
        })
        .catch(() => {})
    }
    socket.on('candle_closed', onCandleClosed)
    socket.on('backfill_status', onBackfillStatus)
    return () => {
      cancelled = true
      socket.off('candle_closed', onCandleClosed)
      socket.off('backfill_status', onBackfillStatus)
    }
  }, [instrument, timeframe])

  return (
    <div style={{
      width: '100%', minWidth: 0,
      height: fillHeight ? '100%' : undefined,
      display: fillHeight ? 'flex' : undefined,
      flexDirection: fillHeight ? 'column' : undefined,
    }}
    >
      <div
        style={{
          display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12,
          // Legacy (Classic page, binary depthOpen/onToggleDepth): controls
          // right-aligned via space-between, unchanged. New 3-state cycle
          // path (onCycleDepth): left-aligned instead -- this container's
          // own right edge moves depending on depth mode (narrower when a
          // side depth panel is showing, full width otherwise), which made
          // space-between visibly shift the Depth/timeframe buttons left
          // and right as the mode cycled. The container's LEFT edge never
          // moves, so anchoring there keeps the controls stationary. Found
          // live 2026-10-05 (screenshot: "Depth + candle buttons are
          // shifting right instead of staying there").
          justifyContent: onCycleDepth ? 'flex-start' : 'space-between',
        }}
      >
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
              ref={depthBtnRef}
              onClick={onCycleDepth}
              title={
                depthMode === 'right' ? 'Depth: shown beside the chart (click to move below)'
                  : depthMode === 'below' ? 'Depth: shown below the chart (click to hide)'
                    : 'Depth: hidden (click to show beside the chart)'
              }
              style={{
                fontSize: 12.5, fontWeight: 700, border: '1.5px solid var(--border)', borderRadius: 'var(--radius-md)',
                padding: '6px 16px', color: depthMode !== 'hidden' ? 'var(--accent-text)' : 'var(--text-dim)',
                background: depthMode !== 'hidden' ? 'var(--accent-soft)' : 'transparent',
                borderColor: depthMode !== 'hidden' ? 'var(--accent)' : 'var(--border)',
              }}
            >
              Depth{depthMode === 'right' ? ' →' : depthMode === 'below' ? ' ↓' : ''}
            </button>
          ) : onToggleDepth ? (
            <button
              ref={depthBtnRef}
              onClick={onToggleDepth}
              style={{
                fontSize: 12.5, fontWeight: 700, border: '1.5px solid var(--border)', borderRadius: 'var(--radius-md)',
                padding: '6px 16px', color: depthOpen ? 'var(--accent-text)' : 'var(--text-dim)',
                background: depthOpen ? 'var(--accent-soft)' : 'transparent',
                borderColor: depthOpen ? 'var(--accent)' : 'var(--border)',
              }}
            >
              Depth
            </button>
          ) : null}
          <div ref={tfToggleRef} style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', overflow: 'hidden' }}>
            {TIMEFRAMES.map((tf) => {
              const active = tf === timeframe
              return (
                <button
                  key={tf}
                  onClick={() => setTimeframe(tf)}
                  style={{
                    border: 'none', fontSize: 12, fontWeight: 600, padding: '5px 10px',
                    color: active ? 'var(--text-on-accent)' : 'var(--text-dim)',
                    background: active ? 'var(--accent)' : 'transparent',
                  }}
                >
                  {tf}
                </button>
              )
            })}
          </div>
        </div>
      </div>
      <div
        ref={containerRef}
        style={{ width: '100%', flex: fillHeight ? '1 1 auto' : undefined, minHeight: fillHeight ? 0 : undefined }}
      />
    </div>
  )
}

function toBar(candle) {
  return {
    time: Math.floor(new Date(candle.ts).getTime() / 1000),
    open: candle.open,
    high: candle.high,
    low: candle.low,
    close: candle.close,
  }
}
