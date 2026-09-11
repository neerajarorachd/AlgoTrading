import { useEffect, useRef, useState } from 'react'
import { createChart } from 'lightweight-charts'
import { getCandles } from '../api/client.js'
import { getSocket } from '../api/ws.js'

const TIMEFRAMES = ['1min', '3min', '5min']
const CHART_HEIGHT = 280

export default function CandleChart({ instrument }) {
  const containerRef = useRef(null)
  const chartRef = useRef(null)
  const seriesRef = useRef(null)
  const [timeframe, setTimeframe] = useState('1min')

  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth, height: CHART_HEIGHT,
      timeScale: { timeVisible: true, secondsVisible: false },
    })
    const series = chart.addCandlestickSeries()
    chartRef.current = chart
    seriesRef.current = series

    // responsive: the chart's own canvas is fixed-size at creation, so it must
    // be told to resize whenever its container does (e.g. the grid reflowing
    // between 3-per-row on desktop and 1-per-row on mobile)
    const resizeObserver = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect?.width
      if (width) chart.applyOptions({ width })
    })
    resizeObserver.observe(containerRef.current)

    return () => {
      resizeObserver.disconnect()
      chart.remove()
    }
  }, [])

  useEffect(() => {
    if (!instrument || !seriesRef.current) return
    getCandles(instrument.symbol, instrument.exchange_segment, timeframe)
      .then((candles) => seriesRef.current.setData(candles.map(toBar)))
      .catch(() => {})
  }, [instrument, timeframe])

  useEffect(() => {
    if (!instrument) return
    const socket = getSocket()
    function onCandleClosed(msg) {
      if (msg.symbol !== instrument.symbol || msg.timeframe !== timeframe) return
      seriesRef.current?.update(toBar(msg.candle))
    }
    socket.on('candle_closed', onCandleClosed)
    return () => socket.off('candle_closed', onCandleClosed)
  }, [instrument, timeframe])

  return (
    <div style={{ width: '100%', minWidth: 0 }}>
      <div style={{ marginBottom: 8 }}>
        {TIMEFRAMES.map((tf) => (
          <button
            key={tf}
            onClick={() => setTimeframe(tf)}
            style={{ fontWeight: tf === timeframe ? 'bold' : 'normal', marginRight: 4 }}
          >
            {tf}
          </button>
        ))}
      </div>
      <div ref={containerRef} style={{ width: '100%' }} />
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
