import { useEffect, useRef, useState } from 'react'
import { createChart } from 'lightweight-charts'
import { getRunDayChart } from '../api/client.js'

const CHART_HEIGHT = 320
const IST_OFFSET_SECONDS = 5.5 * 60 * 60

function formatIST(unixSeconds) {
  const shifted = new Date((unixSeconds + IST_OFFSET_SECONDS) * 1000)
  const hh = String(shifted.getUTCHours()).padStart(2, '0')
  const mm = String(shifted.getUTCMinutes()).padStart(2, '0')
  return `${hh}:${mm}`
}

function toTime(iso) {
  return Math.floor(new Date(iso).getTime() / 1000)
}

// The WHOLE trading day's candles (not one trade's entry->exit window),
// with every trade that day marked on it, plus VWAP/Bollinger Bands when
// CandleIndicators has them -- explicit instruction, 2026-09-18: "I want
// full day chart, not the trade chart... also wants BB and vwap."
export default function DayChart({ runMasterId, runId, date, trades }) {
  const containerRef = useRef(null)
  const chartRef = useRef(null)
  const seriesRef = useRef(null)
  const overlaysRef = useRef([])
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: CHART_HEIGHT,
      timeScale: { timeVisible: true, secondsVisible: false, tickMarkFormatter: formatIST },
      localization: { timeFormatter: formatIST },
    })
    const series = chart.addCandlestickSeries()
    chartRef.current = chart
    seriesRef.current = series

    const resizeObserver = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect
      if (rect) chart.applyOptions({ width: rect.width })
    })
    resizeObserver.observe(containerRef.current)

    return () => {
      resizeObserver.disconnect()
      chart.remove()
    }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    const series = seriesRef.current
    if (!chart || !series || !date) return

    // A fetch in flight when `date` changes again (fast day-switching) or
    // the component unmounts must not touch the chart/series once they're
    // stale or disposed -- real bug hit live: "Cannot read properties of
    // undefined (reading '_internal_addDataSource')" from exactly this,
    // lightweight-charts objects that chart.remove() had already torn down.
    let cancelled = false

    getRunDayChart(runMasterId, runId, date)
      .then(({ candles }) => {
        if (cancelled || candles.length === 0) return

        series.setData(candles.map((c) => ({
          time: toTime(c.ts), open: c.open, high: c.high, low: c.low, close: c.close,
        })))

        // clear any overlay series from a previous date before adding this one's
        overlaysRef.current.forEach((s) => chart.removeSeries(s))
        overlaysRef.current = []

        const addLine = (field, color, lineWidth = 1, lineStyle = 0) => {
          const points = candles
            .filter((c) => c[field] != null)
            .map((c) => ({ time: toTime(c.ts), value: c[field] }))
          if (points.length === 0) return
          const line = chart.addLineSeries({ color, lineWidth, lineStyle, priceLineVisible: false, lastValueVisible: false })
          line.setData(points)
          overlaysRef.current.push(line)
        }
        addLine('vwap', '#c98a1f', 1.5)
        addLine('bb_upper', '#8a63d2', 1, 2)
        addLine('bb_middle', '#8a63d2', 1)
        addLine('bb_lower', '#8a63d2', 1, 2)

        const markers = []
        for (const t of trades || []) {
          const won = t.net_pnl >= 0
          markers.push({
            time: toTime(t.entry_ts), position: 'belowBar',
            color: t.direction === 'bull' ? '#2e8b57' : '#d64545',
            shape: t.direction === 'bull' ? 'arrowUp' : 'arrowDown',
            text: `${t.pattern} entry ${t.entry_price}`,
          })
          markers.push({
            time: toTime(t.exit_ts), position: 'aboveBar',
            color: won ? '#2e8b57' : '#d64545', shape: 'circle',
            text: `exit ${t.exit_price} (${t.exit_reason})`,
          })
        }
        markers.sort((a, b) => a.time - b.time)
        series.setMarkers(markers)

        chart.timeScale().fitContent()
      })
      .catch((e) => { if (!cancelled) setError(e.message) })

    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runMasterId, runId, date, trades])

  return (
    <div style={{ marginBottom: 12 }}>
      {error && <div style={{ color: 'crimson', fontSize: 12, marginBottom: 4 }}>{error}</div>}
      <div ref={containerRef} style={{ width: '100%' }} />
    </div>
  )
}
