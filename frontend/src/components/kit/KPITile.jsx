// Matches the "RELIANCE Pattern Backtest" artifact's own tile shape --
// grid of same-edge, same-padding cells sharing one hairline border, not
// individually bordered cards (see artifact-design's "compose repeated
// things as one object"). `tone`: 'pos' | 'neg' | undefined colors the value.
const TONE_COLOR = { pos: 'var(--good)', neg: 'var(--critical)' }

export function KPITileGrid({ children }) {
  return (
    <div
      style={{
        display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
        gap: 1, background: 'var(--border)', border: '1px solid var(--border)',
        borderRadius: 'var(--radius-lg)', overflow: 'hidden', marginBottom: 'var(--space-4)',
      }}
    >
      {children}
    </div>
  )
}

export default function KPITile({ label, value, sub, tone }) {
  return (
    <div style={{ background: 'var(--surface)', padding: '12px 14px' }}>
      <div style={{ fontSize: 11, color: 'var(--text-faint)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
        {label}
      </div>
      <div className="num" style={{ fontSize: 21, fontWeight: 650, marginTop: 4, color: TONE_COLOR[tone] ?? 'var(--text)' }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}
