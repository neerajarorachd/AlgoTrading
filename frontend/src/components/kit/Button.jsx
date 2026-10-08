// variant: 'primary' (filled accent, default) | 'secondary' (outlined) |
// 'danger' (destructive actions -- Remove, Delete).
const VARIANTS = {
  primary: { background: 'var(--accent)', color: 'var(--text-on-accent)', border: '1px solid transparent' },
  secondary: { background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border-strong)' },
  danger: { background: 'var(--critical-soft)', color: 'var(--critical)', border: '1px solid transparent' },
}

export default function Button({ variant = 'primary', style, children, ...props }) {
  return (
    <button
      {...props}
      style={{
        ...VARIANTS[variant], fontFamily: 'inherit', fontSize: 13, fontWeight: 600,
        padding: '7px 14px', borderRadius: 'var(--radius-sm)', cursor: 'pointer',
        opacity: props.disabled ? 0.55 : 1, ...style,
      }}
    >
      {children}
    </button>
  )
}
