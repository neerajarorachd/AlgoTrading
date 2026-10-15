// On/off pill for showing a screen panel (2026-10-15: the general events
// panel, beside the timeframe bar). Matches TimeframeBar's look. Standalone --
// the page owns the value.
export default function PanelToggle({ label, on, onToggle, title, id }) {
  return (
    <button
      type="button" onClick={() => onToggle(!on)} aria-pressed={on} data-panel-toggle={id}
      title={title ?? `${label}: ${on ? 'shown (click to hide)' : 'hidden (click to show)'}`}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer',
        fontSize: 12, fontWeight: 600, padding: '5px 12px', borderRadius: 'var(--radius-sm)',
        border: '1px solid var(--border)',
        color: on ? 'var(--text-on-accent)' : 'var(--text-dim)', background: on ? 'var(--accent)' : 'transparent',
      }}
    >
      <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true" fill="currentColor">
        <circle cx="3" cy="4" r="1.6" /><rect x="6" y="3.2" width="8" height="1.6" rx=".8" />
        <circle cx="3" cy="8" r="1.6" /><rect x="6" y="7.2" width="8" height="1.6" rx=".8" />
        <circle cx="3" cy="12" r="1.6" /><rect x="6" y="11.2" width="8" height="1.6" rx=".8" />
      </svg>
      {label}
    </button>
  )
}
