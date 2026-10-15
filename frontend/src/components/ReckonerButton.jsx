// "Ready reckoner" button for the condition maker (ALGO-34): opens a side
// panel with the condition language's reference, served from the one doc
// (docs/condition_ready_reckoner.md via /api/condition-reckoner). Standalone --
// any condition editor (strategies, rules, the pattern editor) drops it in.
import { useEffect, useState } from 'react'
import { getConditionReckoner } from '../api/client.js'
import MiniMarkdown from './MiniMarkdown.jsx'

export default function ReckonerButton() {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!open || text !== null) return
    getConditionReckoner().then((r) => setText(r.markdown)).catch((e) => setError(e.message))
  }, [open, text])

  useEffect(() => {
    if (!open) return
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open])

  return (
    <>
      <button
        type="button" data-reckoner-button onClick={() => setOpen(true)}
        title="Every keyword, function and time word the condition maker accepts, with examples"
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer',
          padding: '4px 10px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)',
          background: 'var(--surface)', color: 'var(--accent-text)',
        }}
      >
        <svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M2.5 3.5h4a2 2 0 0 1 1.5.7 2 2 0 0 1 1.5-.7h4v9h-4a2 2 0 0 0-1.5.7 2 2 0 0 0-1.5-.7h-4z" /><path d="M8 4.2v9.5" />
        </svg>
        Ready reckoner
      </button>
      {open && (
        <div role="presentation" onClick={() => setOpen(false)} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.25)', zIndex: 1100 }}>
          <aside
            role="dialog" aria-label="Condition maker: ready reckoner" data-reckoner-panel
            onClick={(e) => e.stopPropagation()}
            style={{
              position: 'absolute', top: 0, right: 0, bottom: 0, width: 'min(640px, 100vw)', overflowY: 'auto',
              background: 'var(--surface)', color: 'var(--text)', borderLeft: '1px solid var(--border)',
              boxShadow: 'var(--shadow-md)', padding: '16px 22px 32px', boxSizing: 'border-box',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'flex-end', position: 'sticky', top: -16, background: 'var(--surface)', paddingTop: 4 }}>
              <button type="button" onClick={() => setOpen(false)} aria-label="Close" title="Close (Esc)"
                style={{ background: 'transparent', border: 'none', cursor: 'pointer', fontSize: 18, color: 'var(--text-dim)' }}
              >×</button>
            </div>
            {error && <p style={{ color: 'var(--critical)' }}>{error}</p>}
            {!error && text === null && <p style={{ color: 'var(--text-faint)' }}>Loading…</p>}
            {text && <MiniMarkdown text={text} />}
          </aside>
        </div>
      )}
    </>
  )
}
