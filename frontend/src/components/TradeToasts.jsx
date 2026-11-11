import { useEffect, useState } from 'react'
import { getSocket, subscribeRooms } from '../api/ws.js'
import { TRADES_ROOM, price, signedMoney, toneOf } from './tradeUtils.js'

// Short notices when a manual trade closes or a limit order fills (rulebook
// 3b), wherever the user is on the Workspace -- a stop or target closing a
// trade must never go unnoticed. Standalone: listens to `trade_update` only.
const REASON = { target: 'Target', stop: 'Stop', limit: 'Limit exit', manual: 'Exited', day_end: 'Day-end square-off', cancelled: 'Cancelled' }
const SHOW_MS = 8000

export default function TradeToasts() {
  const [toasts, setToasts] = useState([])
  const [, setSeen] = useState({}) // trade id -> last status, so one change = one notice

  useEffect(() => {
    const socket = getSocket()
    subscribeRooms([TRADES_ROOM])
    const onUpdate = (t) => {
      setSeen((prev) => {
        const before = prev[t.id]
        if (before !== t.status) {
          const filled = before === 'pending' && t.status === 'open'
          if (t.status === 'closed' || t.status === 'cancelled' || filled) {
            const key = `${t.id}:${t.status}`
            setToasts((cur) => [...cur.filter((x) => x.key !== key), { key, t, filled }])
            setTimeout(() => setToasts((cur) => cur.filter((x) => x.key !== key)), SHOW_MS)
          }
        }
        return { ...prev, [t.id]: t.status }
      })
    }
    socket.on('trade_update', onUpdate)
    return () => socket.off('trade_update', onUpdate)
  }, [])

  if (!toasts.length) return null
  return (
    <div data-trade-toasts style={{ position: 'fixed', bottom: 20, right: 20, zIndex: 700, display: 'flex', flexDirection: 'column', gap: 8 }}>
      {toasts.map(({ key, t, filled }) => {
        const side = t.direction === 'buy' ? 'BUY' : 'SELL'
        const text = filled
          ? `limit filled at ${price(t.entry_price)}`
          : t.status === 'cancelled'
            ? REASON[t.exit_reason] ?? 'Cancelled'
            : `${REASON[t.exit_reason] ?? 'Closed'} at ${price(t.exit_price)}`
        return (
          <div
            key={key} role="status" data-trade-toast={t.status}
            onClick={() => setToasts((cur) => cur.filter((x) => x.key !== key))}
            style={{
              minWidth: 260, padding: '10px 14px', borderRadius: 'var(--radius-md)', background: 'var(--surface)',
              border: '1px solid var(--border)', borderLeft: `4px solid ${t.direction === 'buy' ? 'var(--good)' : 'var(--critical)'}`,
              boxShadow: 'var(--shadow-md)', fontSize: 13, cursor: 'pointer', color: 'var(--text)',
            }}
          >
            <b>{t.symbol} {side}</b> <span style={{ color: 'var(--text-dim)' }}>· {text}</span>
            {t.status === 'closed' && t.net_pnl != null && (
              <span className="num" style={{ marginLeft: 6, fontWeight: 700, color: toneOf(t.net_pnl) }}>{signedMoney(t.net_pnl)}</span>
            )}
          </div>
        )
      })}
    </div>
  )
}
