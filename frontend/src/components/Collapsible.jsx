// Minimal collapsible section -- no library, matching this project's plain
// inline-style convention (see StrategyBuilder.jsx's own header comment).
// `boxed` gives it a fieldset-like border, for use as a legend replacement;
// without it, it's just a clickable heading (for wrapping a whole section).
import { useState } from 'react'

export default function Collapsible({ title, defaultOpen = true, boxed = false, children }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div style={boxed ? { border: '1px solid #ddd', borderRadius: 6, padding: 12, marginBottom: 12 } : { marginBottom: 12 }}>
      <div
        role="button" tabIndex={0} onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && setOpen((o) => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', userSelect: 'none',
          fontWeight: boxed ? 600 : undefined, marginBottom: open ? 8 : 0,
        }}
      >
        <span style={{
          display: 'inline-block', transition: 'transform 0.15s',
          transform: open ? 'rotate(90deg)' : 'rotate(0deg)', fontSize: 11,
        }}>
          ▶
        </span>
        {title}
      </div>
      {open && children}
    </div>
  )
}
