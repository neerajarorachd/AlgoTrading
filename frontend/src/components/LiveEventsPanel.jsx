import { useState } from 'react'

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'patterns', label: 'Patterns' },
  { key: 'indicators', label: 'Indicators' },
]

export const isIndicatorSignal = (e) => e.activity_type === 'indicator'

function formatTimeIST(iso) {
  return new Date(iso).toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: false })
}

const DIRECTION = {
  bull: { label: 'Bull', color: 'var(--good)', bg: 'var(--good-soft)' },
  bear: { label: 'Bear', color: 'var(--critical)', bg: 'var(--critical-soft)' },
}

// Every pattern / indicator signal the engine detects, newest first, as it's
// detected (the `activity` WebSocket event) -- added 2026-10-07 with the
// replay feed: "generate on go the list of generated patterns and
// indicators, show in list". Works the same against the live feed.
export default function LiveEventsPanel({ events }) {
  const [filter, setFilter] = useState('all')
  const shown = events.filter((e) => filter === 'all' || (filter === 'indicators') === isIndicatorSignal(e))
  const patternCount = events.filter((e) => !isIndicatorSignal(e)).length

  return (
    <div data-live-events style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', marginBottom: 'var(--space-5)' }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap',
        padding: '10px var(--space-3)', borderBottom: '1px solid var(--border)',
      }}
      >
        <span style={{ fontSize: 12, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.03em', color: 'var(--text-faint)' }}>
          Live patterns &amp; indicators
          <span style={{ marginLeft: 10, textTransform: 'none', letterSpacing: 0, fontWeight: 600, color: 'var(--text-dim)' }}>
            {patternCount} patterns · {events.length - patternCount} indicator signals
          </span>
        </span>
        <div role="tablist" style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', overflow: 'hidden' }}>
          {FILTERS.map((f) => (
            <button
              key={f.key} role="tab" aria-selected={filter === f.key} onClick={() => setFilter(f.key)}
              style={{
                border: 'none', fontSize: 11.5, fontWeight: 600, padding: '4px 10px', cursor: 'pointer',
                color: filter === f.key ? 'var(--text-on-accent)' : 'var(--text-dim)',
                background: filter === f.key ? 'var(--accent)' : 'transparent',
              }}
            >{f.label}</button>
          ))}
        </div>
      </div>
      <div style={{ maxHeight: 280, overflowY: 'auto' }}>
        {shown.length === 0 ? (
          <div style={{ padding: 'var(--space-4)', textAlign: 'center', color: 'var(--text-faint)', fontSize: 13 }}>
            Nothing detected yet — new patterns and indicator signals appear here as candles close.
          </div>
        ) : (
          <table>
            <tbody>
              {shown.map((e) => {
                const dir = DIRECTION[e.direction]
                return (
                  <tr key={`${e.symbol}-${e.timeframe}-${e.ts}-${e.activity}`} data-live-event style={{ borderTop: '1px solid var(--border)' }}>
                    <td className="num" style={{ padding: '5px 12px', color: 'var(--text-faint)', width: 60 }}>{formatTimeIST(e.ts)}</td>
                    <td style={{ padding: '5px 8px', fontWeight: 600, width: 120 }}>{e.symbol}</td>
                    <td style={{ padding: '5px 8px', color: 'var(--text-faint)', width: 50 }}>{e.timeframe}</td>
                    <td style={{ padding: '5px 8px' }}>{e.activity.replaceAll('_', ' ')}</td>
                    <td style={{ padding: '5px 8px', color: 'var(--text-faint)', width: 110 }}>
                      {isIndicatorSignal(e) ? 'indicator' : e.activity_type.replace('_', ' ')}
                    </td>
                    <td style={{ padding: '5px 12px', width: 60 }}>
                      {dir ? (
                        <span style={{ fontSize: 11, fontWeight: 700, color: dir.color, background: dir.bg, borderRadius: 10, padding: '1px 8px' }}>{dir.label}</span>
                      ) : <span style={{ color: 'var(--text-faint)', fontSize: 11 }}>—</span>}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
