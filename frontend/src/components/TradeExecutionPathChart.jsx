import { useEffect, useRef, useState } from 'react'
import { createChart } from 'lightweight-charts'
import { getTradeExecutionPath } from '../api/client.js'

const CHART_HEIGHT = 220
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

// The chart the user sketched: a wavy price path between two horizontal
// SL/TG bars, with the entry/exit points marked -- meant to make it easy to
// see how far price actually traveled past (or short of) each level, to
// help tune where SL/TG should sit.
export default function TradeExecutionPathChart({ runMasterId, runId, trade }) {
  const containerRef = useRef(null)
  const chartRef = useRef(null)
  const seriesRef = useRef(null)
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
    if (!seriesRef.current) return
    getTradeExecutionPath(runMasterId, runId, trade.id)
      .then(({ candles }) => {
        const series = seriesRef.current
        if (!series || candles.length === 0) return
        series.setData(candles.map((c) => ({
          time: toTime(c.ts), open: c.open, high: c.high, low: c.low, close: c.close,
        })))

        series.createPriceLine({
          price: trade.stop_loss, color: '#d64545', lineWidth: 1, lineStyle: 2,
          axisLabelVisible: true, title: 'SL',
        })
        series.createPriceLine({
          price: trade.target, color: '#2e8b57', lineWidth: 1, lineStyle: 2,
          axisLabelVisible: true, title: 'TG',
        })

        const won = trade.net_pnl >= 0
        series.setMarkers([
          {
            time: toTime(trade.entry_ts), position: 'belowBar',
            color: trade.direction === 'bull' ? '#2e8b57' : '#d64545',
            shape: trade.direction === 'bull' ? 'arrowUp' : 'arrowDown',
            text: `Entry ${trade.entry_price}`,
          },
          {
            time: toTime(trade.exit_ts), position: 'aboveBar',
            color: won ? '#2e8b57' : '#d64545', shape: 'circle',
            text: `Exit ${trade.exit_price} (${trade.exit_reason})`,
          },
        ])

        chartRef.current?.timeScale().fitContent()
      })
      .catch((e) => setError(e.message))
  }, [runMasterId, runId, trade])

  return (
    <div>
      {error && <div style={{ color: 'crimson', fontSize: 12, marginBottom: 4 }}>{error}</div>}
      <div ref={containerRef} style={{ width: '100%' }} />
    </div>
  )
}
