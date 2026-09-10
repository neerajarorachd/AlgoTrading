import { useEffect, useRef, useState } from 'react'
import { createChart } from 'lightweight-charts'
import { getCandles } from '../api/client.js'
import { getSocket } from '../api/ws.js'

const TIMEFRAMES = ['1min', '3min', '5min']

export default function CandleChart({ instrument }) {
  const containerRef = useRef(null)
  const seriesRef = useRef(null)
  const [timeframe, setTimeframe] = useState('1min')

  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, { width: 600, height: 320 })
    const series = chart.addCandlestickSeries()
    seriesRef.current = series
    return () => chart.remove()
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
    <div>
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
      <div ref={containerRef} />
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
