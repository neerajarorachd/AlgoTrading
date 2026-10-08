import { useEffect, useRef, useState } from 'react'
import SymbolRegisterForm from './SymbolRegisterForm.jsx'

// Compact "+" trigger for the new Market Watch page, wrapping the real
// SymbolRegisterForm (unchanged, still the real backend search) inside a
// popover instead of the inline search-bar+dropdowns row Classic shows
// directly in its toolbar -- Classic keeps using SymbolRegisterForm inline,
// untouched; this is purely a different presentation for the new page.
// `compact` renders just the bare "+" icon used in Market Watch's
// INSTRUMENTS card header (the finalized preview design); default keeps
// the full accent "+ Add" button.
export default function AddInstrumentButton({ onRegistered, compact = false }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    function closeOnOutsideClick(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', closeOnOutsideClick)
    return () => document.removeEventListener('mousedown', closeOnOutsideClick)
  }, [open])

  return (
    <span ref={ref} style={{ position: 'relative', display: 'inline-flex' }}>
      {compact ? (
        <button
          onClick={() => setOpen((v) => !v)}
          title="Add instrument"
          aria-label="Add instrument"
          style={{
            background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-dim)',
            fontSize: 16, lineHeight: 1, padding: '2px 6px', borderRadius: 'var(--radius-sm)',
          }}
        >+</button>
      ) : (
        <button
          onClick={() => setOpen((v) => !v)}
          title="Add an instrument"
          style={{
            display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, fontWeight: 600,
            padding: '8px 16px', borderRadius: 'var(--radius-md)', border: 'none', cursor: 'pointer',
            color: 'var(--text-on-accent)', background: 'var(--accent)',
          }}
        >
          <span style={{ fontSize: 15, lineHeight: 1 }}>+</span> Add
        </button>
      )}
      {open && (
        <div
          style={{
            position: 'absolute', top: '100%', right: 0, marginTop: 6, zIndex: 20,
            background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)',
            boxShadow: 'var(--shadow-md)', padding: 14, width: 360,
          }}
        >
          <SymbolRegisterForm onRegistered={() => { onRegistered(); setOpen(false) }} />
        </div>
      )}
    </span>
  )
}
