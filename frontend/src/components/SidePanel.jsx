import { useState } from 'react'

/**
 * A collapsible side window with a vertical button bar — one button per panel
 * (today just "Depth", built to take more later: walkthroughs, order book, etc.
 * without changing this component). Clicking the already-active tab collapses
 * it back down to just the button bar; clicking a different tab switches to it.
 *
 * `collapsed`/`onToggleCollapsed` are controlled by the parent rather than kept
 * as internal state, so the panel's open/closed state survives the card itself
 * being unmounted and remounted (e.g. single-graph mode swapping which
 * instrument occupies the one open card) instead of resetting every time.
 */
export default function SidePanel({ panels, collapsed, onToggleCollapsed }) {
  const [activeId, setActiveId] = useState(panels[0]?.id ?? null)

  if (panels.length === 0) return null
  const active = panels.find((p) => p.id === activeId) ?? panels[0]

  function handleTabClick(panel) {
    if (panel.id === activeId && !collapsed) {
      onToggleCollapsed(true)
    } else {
      setActiveId(panel.id)
      onToggleCollapsed(false)
    }
  }

  return (
    <div style={{ display: 'flex', flex: collapsed ? '0 0 auto' : '1 1 0%', minWidth: collapsed ? 'auto' : 150 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 3, borderLeft: '1px solid var(--border)', paddingLeft: 8, marginLeft: 4, flexShrink: 0 }}>
        {panels.map((panel) => {
          const active = panel.id === activeId && !collapsed
          return (
            <button
              key={panel.id}
              onClick={() => handleTabClick(panel)}
              title={panel.label}
              style={{
                fontSize: 12, fontWeight: 600, whiteSpace: 'nowrap', textAlign: 'left',
                border: 'none', borderRadius: 'var(--radius-sm)', padding: '5px 10px',
                color: active ? 'var(--accent-text)' : 'var(--text-dim)',
                background: active ? 'var(--accent-soft)' : 'transparent',
              }}
            >
              {panel.label}
            </button>
          )
        })}
      </div>
      {!collapsed && active && (
        <div style={{ flex: '1 1 auto', minWidth: 0, paddingLeft: 14 }}>{active.content}</div>
      )}
    </div>
  )
}
