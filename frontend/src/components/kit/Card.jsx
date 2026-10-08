// Base surface for grouped content -- every panel/section across the app
// should use this instead of a hand-rolled bordered <div>, so a later
// token change (radius, shadow, border color) updates everywhere at once.
export default function Card({ title, subtitle, actions, children, style }) {
  return (
    <section
      style={{
        background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)',
        boxShadow: 'var(--shadow-sm)', padding: 'var(--space-4) var(--space-4) var(--space-3)',
        marginBottom: 'var(--space-4)', ...style,
      }}
    >
      {(title || actions) && (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, marginBottom: subtitle ? 2 : 10 }}>
          {title && <h2 style={{ fontSize: 15, fontWeight: 700, margin: 0, color: 'var(--text)' }}>{title}</h2>}
          {actions}
        </div>
      )}
      {subtitle && <div style={{ fontSize: 12.5, color: 'var(--text-faint)', marginBottom: 12 }}>{subtitle}</div>}
      {children}
    </section>
  )
}
