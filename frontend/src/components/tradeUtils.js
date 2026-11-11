// Shared helpers for the manual trade screens (rulebook 3b).
import { useEffect, useRef } from 'react'

/** Close a floating panel on a click outside it or on Esc (11-11: an open
 * panel was covering the grid's Exit / B / S buttons). The panel's own root
 * must stop mousedown propagation. */
export function useDismiss(onClose) {
  const latest = useRef(onClose)
  latest.current = onClose // inline handlers change every render (ticks); register once
  useEffect(() => {
    const onDown = () => latest.current()
    const onKey = (e) => { if (e.key === 'Escape') latest.current() }
    // registered after the opening click has finished
    const t = setTimeout(() => document.addEventListener('mousedown', onDown), 0)
    window.addEventListener('keydown', onKey)
    return () => { clearTimeout(t); document.removeEventListener('mousedown', onDown); window.removeEventListener('keydown', onKey) }
  }, [])
}

export const TRADES_ROOM = 'trades' // backend ws_live.TRADES_ROOM

const inr = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2, minimumFractionDigits: 2 })
const inr0 = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 })

export function money(v, whole = false) {
  if (v == null || Number.isNaN(v)) return '—'
  return `₹${(whole ? inr0 : inr).format(v)}`
}

export function signedMoney(v) {
  if (v == null || Number.isNaN(v)) return '—'
  return `${v > 0 ? '+' : v < 0 ? '−' : ''}₹${inr.format(Math.abs(v))}`
}

export function price(v) {
  return v == null ? '—' : Number(v).toFixed(2)
}

export function toneOf(v) {
  if (v == null || v === 0) return undefined
  return v > 0 ? 'var(--good)' : 'var(--critical)'
}

/** Live P&L from a tick: gross from the price, net = gross - est_costs (worked
 * out once at entry by the backend), so nothing heavy runs per tick. */
export function liveTradePnl(trade, ltp) {
  if (trade.status !== 'open' || trade.entry_price == null || ltp == null) return null
  const sign = trade.direction === 'buy' ? 1 : -1
  const gross = sign * (ltp - trade.entry_price) * trade.quantity
  return { ltp, gross, net: gross - (trade.est_costs ?? 0) }
}

/** Room of a trade's stock (NSE_EQ -> "NSE:SYMBOL"), see api/ws.js roomFor. */
export function exchangeOf(trade) {
  return (trade.exchange_segment || 'NSE_EQ').split('_')[0]
}

export function istTime(iso, withDate = false) {
  if (!iso) return '—'
  const d = new Date(iso)
  const opts = { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }
  if (withDate) Object.assign(opts, { day: '2-digit', month: 'short' })
  return d.toLocaleString('en-IN', opts)
}

/** The "tubelight" panel background (rulebook 4): a 2-colour band in the top
 * ~14% fading into a soft tint. */
export function tubelight(a, b) {
  return {
    background: `linear-gradient(180deg, transparent 0%, var(--surface) 22%), linear-gradient(90deg, ${a}, ${b}), var(--surface)`,
  }
}

// Default stop / target distance for a new order, % of the entry price (user,
// 11-11). A per-user, per-stock ratios table replaces these later.
export const DEFAULT_STOP_PCT = 0.2
export const DEFAULT_TARGET_PCT = 0.5

/** The stop / target price `dist` rupees away from `ref`, on the right side
 * for the direction (side: 'stop' | 'target'), rounded AWAY from the entry to
 * the 0.05 tick so a leg is never on the entry price (cheap stocks). '' when
 * there is no entry price yet. */
export function levelAt(ref, direction, dist, side) {
  if (ref == null || !(ref > 0) || !(dist > 0)) return ''
  const up = (direction === 'buy') === (side === 'target')
  const v = ref + (up ? 1 : -1) * dist
  // nearest 0.05 tick (keeps 0.2 % reading as ~0.20 %); only if that lands on
  // or past the entry (cheap stocks) is it moved one tick away from the entry
  let ticks = Math.round(v / 0.05)
  const entryTicks = ref / 0.05
  if (up && ticks <= entryTicks + 1e-9) ticks = Math.floor(entryTicks + 1e-9) + 1
  if (!up && ticks >= entryTicks - 1e-9) ticks = Math.ceil(entryTicks - 1e-9) - 1
  return (ticks * 0.05).toFixed(2)
}

/** Same, `pct` % of `ref` away. */
export function levelFrom(ref, direction, pct, side) {
  if (ref == null || !(ref > 0)) return ''
  return levelAt(ref, direction, (ref * pct) / 100, side)
}

// how the stop / target boxes are set (user, 11-11): % of the entry, a rupee
// distance from the entry, or a fixed price
export const LEVEL_MODES = [
  { value: 'pct', label: '%', unit: '%' },
  { value: 'diff', label: '₹ diff', unit: '₹ away' },
  { value: 'price', label: 'Price', unit: 'price' },
]
