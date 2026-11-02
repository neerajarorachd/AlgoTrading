// "Last 10 candles / Whole day" (ALGO-37): which part of today the signal
// counts (and the chart's marks / the grid's signal filter) look at.
// Standalone -- the caller owns the value ('last10' | 'day').
export const MARK_WINDOWS = [
  { value: 'last10', label: 'Last 10 candles' },
  { value: 'day', label: 'Whole day' },
]
export const isMarkWindow = (v) => MARK_WINDOWS.some((w) => w.value === v)
// what a count means under each window, for the count tooltips
export const windowUnit = (value, prefix = '') => `${prefix}${value === 'last10' ? 'in the last 10 candles' : 'today'}`

export default function MarkWindowToggle({ value, onChange, id }) {
  return (
    <div role="radiogroup" aria-label="Signal window" data-mark-window={id}
      style={{ display: 'flex', flex: 'none', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', overflow: 'hidden', marginBottom: 10 }}
    >
      {MARK_WINDOWS.map((w) => {
        const on = w.value === value
        return (
          <button
            key={w.value} type="button" role="radio" aria-checked={on} data-mark-window-option={w.value}
            onClick={() => onChange(w.value)}
            title={w.value === 'last10' ? "Only the last 10 candles of each timeframe" : "All of today's session"}
            style={{
              flex: '1 1 0', border: 'none', cursor: 'pointer', fontSize: 11.5, fontWeight: 600, padding: '5px 8px',
              color: on ? 'var(--text-on-accent)' : 'var(--text-dim)', background: on ? 'var(--accent)' : 'transparent',
            }}
          >{w.label}</button>
        )
      })}
    </div>
  )
}
