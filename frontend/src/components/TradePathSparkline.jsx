import { useEffect, useState } from 'react'
import { getTradeExecutionPath } from '../api/client.js'

const WIDTH = 150
const HEIGHT = 46
const PAD_X = 5
const PAD_Y = 5

// Compact "just like RSI value" cell version of the trade path -- entry/exit
// dots, the close-price path curve between them, and SL/TG lines, with no
// axes/candles/context padding (padding=0: the window is exactly entry_ts
// to exit_ts). The full candlestick chart (TradeExecutionPathChart) stays
// as its own separate thing below this grid -- this is an addition, not a
// replacement.
export default function TradePathSparkline({ runMasterId, runId, trade }) {
  const [candles, setCandles] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    getTradeExecutionPath(runMasterId, runId, trade.id, 0)
      .then(({ candles }) => { if (!cancelled) setCandles(candles) })
      .catch((e) => { if (!cancelled) setError(e.message) })
    return () => { cancelled = true }
  }, [runMasterId, runId, trade.id])

  if (error) return <span style={{ fontSize: 11, color: 'crimson' }}>err</span>
  if (!candles) return <span style={{ fontSize: 11, color: '#999' }}>…</span>
  if (candles.length === 0) return <span style={{ fontSize: 11, color: '#999' }}>—</span>

  const closes = candles.map((c) => c.close)
  const sl = trade.stop_loss
  const tg = trade.target
  const min = Math.min(...closes, sl, tg)
  const max = Math.max(...closes, sl, tg)
  const span = max - min || 1

  const x = (i) => PAD_X + (i / Math.max(1, closes.length - 1)) * (WIDTH - 2 * PAD_X)
  const y = (v) => HEIGHT - PAD_Y - ((v - min) / span) * (HEIGHT - 2 * PAD_Y)

  const points = closes.map((c, i) => `${x(i)},${y(c).toFixed(1)}`).join(' ')
  const won = trade.net_pnl >= 0
  const entryColor = trade.direction === 'bull' ? '#2e8b57' : '#d64545'
  const exitColor = won ? '#2e8b57' : '#d64545'

  return (
    <svg width={WIDTH} height={HEIGHT} style={{ display: 'block' }}>
      <title>
        {`SL ${sl}  TG ${tg}  Entry ${trade.entry_price}  Exit ${trade.exit_price} (${trade.exit_reason})`}
      </title>
      <line x1={PAD_X} x2={WIDTH - PAD_X} y1={y(sl)} y2={y(sl)} stroke="#d64545" strokeWidth={1} strokeDasharray="3,2" />
      <line x1={PAD_X} x2={WIDTH - PAD_X} y1={y(tg)} y2={y(tg)} stroke="#2e8b57" strokeWidth={1} strokeDasharray="3,2" />
      <polyline points={points} fill="none" stroke="#3b6ea5" strokeWidth={1.5} />
      <circle cx={x(0)} cy={y(closes[0])} r={2.5} fill={entryColor} />
      <circle cx={x(closes.length - 1)} cy={y(closes[closes.length - 1])} r={2.5} fill={exitColor} />
    </svg>
  )
}
