import { useEffect, useState } from 'react'
import { getFeedStatus } from '../api/client.js'

const POLL_MS = 15000
const STALE_AFTER_MS = 2 * 60 * 1000
const IST_OFFSET_MS = 5.5 * 60 * 60 * 1000

// NSE cash session, Mon-Fri 09:15-15:30 IST -- outside it, "no ticks" is
// expected, not a fault. (Exchange holidays aren't known here, so a holiday
// shows as "no data", which is still true.)
function isMarketHours(now = new Date()) {
  const ist = new Date(now.getTime() + IST_OFFSET_MS)
  const day = ist.getUTCDay()
  if (day === 0 || day === 6) return false
  const minutes = ist.getUTCHours() * 60 + ist.getUTCMinutes()
  return minutes >= 9 * 60 + 15 && minutes <= 15 * 60 + 30
}

function describe(status) {
  if (!status?.available) return null
  if (status.mode === 'replay') {
    const session = new Date(`${status.replay_date}T00:00:00`).toLocaleDateString('en-IN', { day: '2-digit', month: 'short' })
    const speed = status.interval_sec > 0 ? `${Math.round((status.batch_minutes * 60) / status.interval_sec)}×` : 'max speed'
    const where = status.state === 'finished' ? 'session finished'
      : status.state === 'failed' ? 'FAILED — see backend log'
        : status.sim_time ? `at ${status.sim_time}` : 'starting'
    return {
      tone: 'replay',
      text: `REPLAY · ${session} session · ${where} · ${speed}`,
      title: `Not live data: a recorded session (${status.replay_date}) played back as if live, shown as today `
        + `(${status.shown_as_date}). ${status.minutes_played}/${status.minutes_total} minutes played.`,
    }
  }
  const marketOpen = isMarketHours()
  if (!status.connected) {
    return marketOpen
      ? { tone: 'critical', text: 'Live feed disconnected', title: 'No connection to the broker feed; prices and signals are not updating.' }
      : { tone: 'faint', text: 'Feed offline · market closed', title: 'The feed connects at 08:50 IST on trading days.' }
  }
  const last = status.last_message_at ? new Date(status.last_message_at).getTime() : null
  const age = last ? Date.now() - last : null
  if (marketOpen && (age == null || age > STALE_AFTER_MS)) {
    const mins = age == null ? null : Math.round(age / 60000)
    return {
      tone: 'warning',
      text: mins == null ? 'Feed connected · no data yet' : `Feed connected · no data for ${mins} min`,
      title: `Connected to the broker but nothing received recently. Subscribed: ${status.subscribed}.`,
    }
  }
  return {
    tone: 'good',
    text: 'Live feed',
    title: `Connected since ${new Date(status.connected_at).toLocaleTimeString('en-IN')} · ${status.subscribed} instruments`
      + (status.disconnects ? ` · reconnected ${status.disconnects}x today` : ''),
  }
}

const TONE_COLOR = {
  good: 'var(--good)', warning: 'var(--warning)', critical: 'var(--critical)', faint: 'var(--text-faint)',
  replay: 'var(--accent)',
}

// Polls GET /api/feed/status -- shared so a page can both show the badge and
// react to the mode (Market Watch's replay-only behaviour).
export function useFeedStatus(enabled = true) {
  const [status, setStatus] = useState(null)
  useEffect(() => {
    if (!enabled) return undefined
    let cancelled = false
    function poll() {
      getFeedStatus().then((s) => { if (!cancelled) setStatus(s) }).catch(() => { if (!cancelled) setStatus(null) })
    }
    poll()
    const id = setInterval(poll, POLL_MS)
    return () => { cancelled = true; clearInterval(id) }
  }, [enabled])
  return status
}

// Small dot + label showing whether live prices are actually flowing. Added
// 2026-10-06: the feed had been silently disconnected for a whole session,
// with nothing on screen but "—" in every price column. Pass `status` from
// useFeedStatus() when the page already polls it; otherwise it polls itself.
export default function FeedStatusBadge({ status: given }) {
  const own = useFeedStatus(given === undefined)
  const status = given !== undefined ? given : own

  const info = describe(status)
  if (!info) return null
  if (info.tone === 'replay') {
    return (
      <span
        data-feed-status="replay" title={info.title}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 6, fontWeight: 700, fontSize: 12,
          color: 'var(--accent-text)', background: 'var(--accent-soft)', border: '1px solid var(--accent)',
          borderRadius: 'var(--radius-sm)', padding: '2px 8px',
        }}
      >
        <span style={{ width: 8, height: 8, borderRadius: '50%', background: TONE_COLOR.replay }} />
        {info.text}
      </span>
    )
  }
  return (
    <span data-feed-status={info.tone} title={info.title} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span style={{ width: 8, height: 8, borderRadius: '50%', background: TONE_COLOR[info.tone], flex: '0 0 auto' }} />
      <span style={{ color: info.tone === 'good' || info.tone === 'faint' ? 'var(--text-dim)' : TONE_COLOR[info.tone], fontWeight: info.tone === 'critical' ? 600 : undefined }}>
        {info.text}
      </span>
    </span>
  )
}
