// "+" button for a tab bar: opens a list of the kinds of tab the user can
// add (2026-10-08). Standalone -- it knows nothing about the page it sits
// in; the page passes `options` and gets `onPick(key)` back.
//
// First pass shows the list only: picking an option just closes it. The
// options themselves (Watchlist -- one of several watchlist sets; Sector
// Watch -- tickers per sector, itself a standalone component) come later.
import { useEffect, useRef, useState } from 'react'

export const DEFAULT_ADD_TAB_OPTIONS = [
  { key: 'watchlist', label: 'Watchlist', hint: 'Instruments from one of your watchlists' },
  { key: 'sector-watch', label: 'Sector Watch', hint: 'Tickers grouped by sector' },
]

export default function AddTabMenu({ options = DEFAULT_ADD_TAB_OPTIONS, onPick }) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef(null)

  useEffect(() => {
    if (!open) return
    function onDown(e) { if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false) }
    function onKey(e) { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  function pick(key) {
    setOpen(false)
    onPick?.(key)
  }

  return (
    <div ref={wrapRef} style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
      <button
        type="button" data-add-tab aria-label="Add tab" title="Add tab"
        aria-haspopup="menu" aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        style={{
          width: 28, height: 28, display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 18, fontWeight: 600, lineHeight: 1, border: '1px solid transparent', borderRadius: 'var(--radius-sm)',
          color: open ? 'var(--accent-text)' : 'var(--text-dim)',
          background: open ? 'var(--accent-soft)' : 'transparent',
          borderColor: open ? 'var(--accent)' : 'transparent',
        }}
      >
        +
      </button>
      {open && (
        <div
          role="menu" data-add-tab-menu
          style={{
            position: 'absolute', top: 'calc(100% + 6px)', left: 0, zIndex: 500, minWidth: 240,
            background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)',
            boxShadow: 'var(--shadow-md)', padding: 6,
          }}
        >
          <p style={{ margin: '4px 8px 6px', fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.04em', color: 'var(--text-faint)' }}>
            Add a tab
          </p>
          {options.map((opt) => (
            <button
              key={opt.key} type="button" role="menuitem" data-add-tab-option={opt.key}
              onClick={() => pick(opt.key)}
              style={{
                display: 'block', width: '100%', textAlign: 'left', border: 'none', background: 'transparent',
                borderRadius: 'var(--radius-sm)', padding: '7px 8px', cursor: 'pointer',
              }}
              onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--surface-alt)' }}
              onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
            >
              <span style={{ display: 'block', fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{opt.label}</span>
              {opt.hint && <span style={{ display: 'block', fontSize: 11.5, color: 'var(--text-faint)', marginTop: 1 }}>{opt.hint}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
