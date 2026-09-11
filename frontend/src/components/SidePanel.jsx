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
    <div style={{ display: 'flex', flexShrink: 0 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2, borderLeft: '1px solid #eee', paddingLeft: 4 }}>
        {panels.map((panel) => (
          <button
            key={panel.id}
            onClick={() => handleTabClick(panel)}
            title={panel.label}
            style={{
              fontWeight: panel.id === activeId && !collapsed ? 'bold' : 'normal',
              background: panel.id === activeId && !collapsed ? '#dfe8ff' : undefined,
              whiteSpace: 'nowrap', textAlign: 'left', cursor: 'pointer',
            }}
          >
            {panel.label}
          </button>
        ))}
      </div>
      {!collapsed && active && (
        <div style={{ minWidth: 200, paddingLeft: 12 }}>{active.content}</div>
      )}
    </div>
  )
}
