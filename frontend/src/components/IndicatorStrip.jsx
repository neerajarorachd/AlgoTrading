// Indicators as a strip ABOVE the graph (2026-10-16): each indicator is an
// on/off chip with its current value -- overlays drawn on the price graph
// (Volume, VWAP, BB, EMAs, MAs, Pivots) and the RSI/MACD/Stoch panes. These
// are indicators, not signals: signals (doji, MACD crossover...) are marked
// from the Signals tab. Standalone -- the chart owns the state.
export default function IndicatorStrip({ groups, values = {} }) {
  return (
    <div data-indicator-strip role="group" aria-label="Indicators"
      style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6, marginBottom: 10 }}
    >
      {groups.map((g, gi) => (
        <div key={g.title} data-indicator-group={g.title} style={{ display: 'contents' }}>
          {gi > 0 && <span aria-hidden="true" style={{ width: 1, height: 18, background: 'var(--border)', margin: '0 4px' }} />}
          {g.defs.map((def) => {
            const on = g.isOn(def.key, def.defaultOn)
            const disabled = !!g.disabled
            return (
              <button
                key={def.key} type="button" data-indicator={def.key} aria-pressed={on} disabled={disabled}
                onClick={() => g.onToggle(def.key)}
                title={`${def.label}: ${on ? 'shown (click to hide)' : 'hidden (click to show)'}${disabled ? ' -- panes are hidden while Graph only is on' : ''}`}
                style={{
                  display: 'inline-flex', alignItems: 'baseline', gap: 6, cursor: disabled ? 'not-allowed' : 'pointer',
                  fontSize: 11.5, fontWeight: 600, padding: '3px 8px', borderRadius: 999,
                  border: `1px solid ${on ? 'var(--accent)' : 'var(--border)'}`,
                  background: on ? 'var(--accent-soft)' : 'transparent',
                  color: on ? 'var(--accent-text)' : 'var(--text-faint)', opacity: disabled ? 0.45 : 1,
                }}
              >
                <span>{def.short ?? def.label}</span>
                <span className="num" data-readout={def.key} style={{ fontWeight: 500, color: on ? 'var(--text-dim)' : 'var(--text-faint)' }}>
                  {values[def.key] ?? '—'}
                </span>
              </button>
            )
          })}
        </div>
      ))}
    </div>
  )
}
