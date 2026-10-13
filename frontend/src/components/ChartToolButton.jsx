// Icon toggle for the chart toolbar (ALGO-25): an icon, a tooltip, and an
// on/off state that reads at a glance (filled accent when on). Standalone --
// the chart passes `on` and `onClick`.
import { forwardRef } from 'react'

const ICONS = {
  // order-book bars
  depth: <><rect x="2" y="3" width="9" height="2.2" rx="1" /><rect x="5" y="7" width="9" height="2.2" rx="1" /><rect x="3" y="11" width="7" height="2.2" rx="1" /></>,
  // sliders
  indicators: <><path d="M2 4h12M2 8h12M2 12h12" stroke="currentColor" strokeWidth="1.4" fill="none" /><circle cx="5" cy="4" r="1.8" /><circle cx="10.5" cy="8" r="1.8" /><circle cx="7" cy="12" r="1.8" /></>,
  // a single dot
  events: <circle cx="8" cy="8" r="3.2" />,
  // circle + square outlines
  diagrams: <><circle cx="6" cy="7" r="3.6" fill="none" stroke="currentColor" strokeWidth="1.4" /><rect x="7.5" y="7.5" width="6" height="6" rx="1" fill="none" stroke="currentColor" strokeWidth="1.4" /></>,
  // oscillator line between two guide lines
  rsi: <><path d="M1.5 4.5h13M1.5 11.5h13" stroke="currentColor" strokeWidth="0.9" strokeDasharray="1.6 1.4" fill="none" /><path d="M1.5 9.5 4 5.5 6.5 10 9.5 4 12 9 14.5 6.5" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinejoin="round" /></>,
  // histogram + signal line
  macd: <><rect x="2" y="8" width="2" height="5" /><rect x="5.2" y="6" width="2" height="7" /><rect x="8.4" y="9" width="2" height="4" /><rect x="11.6" y="7" width="2" height="6" /><path d="M1.5 6.5 5 3.5 9 5 14.5 2.5" stroke="currentColor" strokeWidth="1.3" fill="none" /></>,
  // two crossing lines (%K / %D)
  stoch: <><path d="M1.5 12 5 5 8.5 9 14.5 3" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinejoin="round" /><path d="M1.5 6 5 9.5 9 4.5 14.5 10" stroke="currentColor" strokeWidth="1.1" fill="none" opacity=".7" /></>,
}

const ChartToolButton = forwardRef(function ChartToolButton({ tool, label, on, onClick, badge }, ref) {
  return (
    <button
      ref={ref} type="button" onClick={onClick}
      data-chart-tool={tool} aria-label={label} aria-pressed={on} title={label}
      style={{
        position: 'relative', width: 32, height: 30, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        border: '1.5px solid', borderRadius: 'var(--radius-md)', cursor: 'pointer', padding: 0,
        borderColor: on ? 'var(--accent)' : 'var(--border)',
        background: on ? 'var(--accent-soft)' : 'transparent',
        color: on ? 'var(--accent-text)' : 'var(--text-faint)',
      }}
    >
      <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">{ICONS[tool]}</svg>
      {badge && (
        <span style={{ position: 'absolute', right: 2, bottom: 0, fontSize: 9, fontWeight: 700, lineHeight: 1, color: 'inherit' }}>{badge}</span>
      )}
    </button>
  )
})

export default ChartToolButton
