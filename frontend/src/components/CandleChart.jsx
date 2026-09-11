import { useEffect, useRef, useState } from 'react'
import { createChart } from 'lightweight-charts'
import { getCandles } from '../api/client.js'
import { getSocket } from '../api/ws.js'

const TIMEFRAMES = ['1min', '3min', '5min']
const CHART_HEIGHT = 280
const IST_OFFSET_SECONDS = 5.5 * 60 * 60

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

export default function CandleChart({ instrument, fillHeight = false }) {
  const containerRef = useRef(null)
  const chartRef = useRef(null)
  const seriesRef = useRef(null)
  const [timeframe, setTimeframe] = useState('1min')

  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: fillHeight ? containerRef.current.clientHeight || CHART_HEIGHT : CHART_HEIGHT,
      timeScale: { timeVisible: true, secondsVisible: false, tickMarkFormatter: formatIST },
      localization: { timeFormatter: formatIST },
    })
    const series = chart.addCandlestickSeries()
    chartRef.current = chart
    seriesRef.current = series

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
    <div style={{
      width: '100%', minWidth: 0,
      height: fillHeight ? '100%' : undefined,
      display: fillHeight ? 'flex' : undefined,
      flexDirection: fillHeight ? 'column' : undefined,
    }}
    >
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
