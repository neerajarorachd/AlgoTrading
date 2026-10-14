// Screen-wide timeframe choice (2026-10-14): 1 / 3 / 5 / All, just above the
// instruments grid. Drives the colour tiles, row colours and Buy/Sell/Hold
// buttons, and the open charts while their sync toggle is on. Standalone --
// the page owns the value.
export const SCREEN_TIMEFRAMES = [
  { value: '1min', label: '1m' },
  { value: '3min', label: '3m' },
  { value: '5min', label: '5m' },
  { value: 'all', label: 'All' },
]

export default function TimeframeBar({ value, onChange, options = SCREEN_TIMEFRAMES }) {
  return (
    <div data-timeframe-bar style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ fontSize: 11.5, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.04em', color: 'var(--text-faint)' }}>
        Timeframe
      </span>
      <div role="radiogroup" aria-label="Screen timeframe" style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', overflow: 'hidden' }}>
        {options.map((o) => {
          const on = o.value === value
          return (
            <button
              key={o.value} type="button" role="radio" aria-checked={on} data-timeframe={o.value}
              onClick={() => onChange(o.value)}
              title={o.value === 'all' ? 'All timeframes: each colour tile shows 1m · 3m · 5m counts' : `Colours and charts on ${o.label}`}
              style={{
                border: 'none', cursor: 'pointer', fontSize: 12, fontWeight: 600, padding: '5px 12px',
                color: on ? 'var(--text-on-accent)' : 'var(--text-dim)', background: on ? 'var(--accent)' : 'transparent',
              }}
            >{o.label}</button>
          )
        })}
      </div>
    </div>
  )
}
