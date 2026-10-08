import { useCallback, useRef, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import ThemeToggle from './components/kit/ThemeToggle.jsx'

const SIDEBAR_KEY = 'algotrading.sidebarWidth'
const COLLAPSED_KEY = 'algotrading.sidebarCollapsed'
const SIDEBAR_MIN = 160
const SIDEBAR_MAX = 340
const SIDEBAR_DEFAULT = 212
const COLLAPSED_WIDTH = 56
const CLICK_THRESHOLD = 4 // px of pointer movement below which a mouseup counts as a click, not a drag

function readStoredSidebarWidth() {
  try {
    const v = parseInt(localStorage.getItem(SIDEBAR_KEY), 10)
    return v >= SIDEBAR_MIN && v <= SIDEBAR_MAX ? v : SIDEBAR_DEFAULT
  } catch {
    return SIDEBAR_DEFAULT
  }
}

function readStoredCollapsed() {
  try {
    return localStorage.getItem(COLLAPSED_KEY) === '1'
  } catch {
    return false
  }
}

// First page restyled with the Phase 1 token system (theme.css) -- wraps
// every page, so this is the most-visible single change. Nav structure
// unchanged from the pre-Phase-1 shell; only the styling is new.
const NAV_ITEMS = [
  { to: '/', label: 'Market Watch', end: true, icon: '⌂' },
  { to: '/market-watch-classic', label: 'Market Watch (Classic)', icon: '⌁' },
  { to: '/strategies', label: 'Strategies', icon: '♟' },
  { to: '/watchlists', label: 'Watchlists', icon: '★' },
  { to: '/backtests', label: 'Backtests', icon: '◷' },
  { to: '/occurrence-backtest', label: 'Occurrence Backtest', icon: '⊚' },
  { to: '/recommendations', label: 'Recommendations', icon: '✦' },
  { to: '/recommendation-systems', label: 'Recommendation Systems', icon: '⟲' },
  { to: '/engine-settings', label: 'Engine Settings', icon: '⚙' },
]

export default function AppShell() {
  const [sidebarWidth, setSidebarWidth] = useState(readStoredSidebarWidth)
  const [collapsed, setCollapsedState] = useState(readStoredCollapsed)
  const [resizerActive, setResizerActive] = useState(false)
  const [resizerHover, setResizerHover] = useState(false)
  const draggingRef = useRef(false)
  const dragStartXRef = useRef(0)
  const dragMovedRef = useRef(false)

  const applyWidth = useCallback((px) => {
    const clamped = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, px))
    setSidebarWidth(clamped)
    try {
      localStorage.setItem(SIDEBAR_KEY, clamped)
    } catch {
      // ignore -- e.g. private browsing with storage disabled
    }
  }, [])

  const setCollapsed = useCallback((next) => {
    setCollapsedState(next)
    try {
      localStorage.setItem(COLLAPSED_KEY, next ? '1' : '0')
    } catch {
      // ignore -- e.g. private browsing with storage disabled
    }
  }, [])

  // Pointer capture (not a window-level mousemove listener) so the drag
  // keeps tracking even once the cursor crosses pages with their own
  // pointer handling (e.g. a lightweight-charts canvas, which registers
  // its own crosshair/scroll handlers that can otherwise swallow the move
  // event before it bubbles up to window).
  function handlePointerDown(e) {
    draggingRef.current = true
    dragStartXRef.current = e.clientX
    dragMovedRef.current = false
    setResizerActive(true)
    e.target.setPointerCapture(e.pointerId)
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'col-resize'
    e.preventDefault()
  }

  function handlePointerMove(e) {
    if (!draggingRef.current) return
    if (Math.abs(e.clientX - dragStartXRef.current) > CLICK_THRESHOLD) dragMovedRef.current = true
    if (!collapsed && dragMovedRef.current) applyWidth(e.clientX)
  }

  function handlePointerUp(e) {
    if (!draggingRef.current) return
    draggingRef.current = false
    setResizerActive(false)
    try {
      e.target.releasePointerCapture(e.pointerId)
    } catch {
      // ignore -- capture may already be released
    }
    document.body.style.userSelect = ''
    document.body.style.cursor = ''
    if (!dragMovedRef.current) setCollapsed(!collapsed) // a real click (no drag) toggles collapse
  }

  const effectiveWidth = collapsed ? COLLAPSED_WIDTH : sidebarWidth

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <nav
        style={{
          width: effectiveWidth, flex: '0 0 auto', background: 'var(--surface-alt)',
          paddingBlock: 16, overflow: 'hidden',
          display: 'flex', flexDirection: 'column',
        }}
      >
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8, padding: collapsed ? '0 0 16px' : '0 16px 16px',
          justifyContent: collapsed ? 'center' : 'flex-start', whiteSpace: 'nowrap',
        }}
        >
          <span style={{
            flex: '0 0 auto', width: 22, height: 22, borderRadius: 'var(--radius-sm)', background: 'var(--accent)',
            color: 'var(--text-on-accent)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, fontWeight: 700,
          }}
          >
            A
          </span>
          {!collapsed && (
            <span style={{ fontSize: 16, letterSpacing: '-0.01em', whiteSpace: 'nowrap' }}>
              <span style={{ color: 'var(--text)', fontWeight: 600 }}>Algo</span>
              <span style={{ color: 'var(--accent-text)', fontWeight: 700 }}>Parakh</span>
            </span>
          )}
        </div>
        <div style={{ flex: '1 1 auto', display: 'flex', flexDirection: 'column', gap: 2 }}>
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to} to={item.to} end={item.end} title={item.label}
              style={({ isActive }) => ({
                display: 'flex', alignItems: 'center', gap: 10,
                padding: collapsed ? '9px 0' : '9px 16px', justifyContent: collapsed ? 'center' : 'flex-start',
                marginInline: 8, borderRadius: 'var(--radius-sm)',
                color: isActive ? 'var(--accent-text)' : 'var(--text-dim)',
                background: isActive ? 'var(--accent-soft)' : 'transparent',
                fontWeight: isActive ? 600 : 500, textDecoration: 'none', fontSize: 13.5,
                whiteSpace: 'nowrap', overflow: 'hidden',
              })}
            >
              <span style={{ flex: '0 0 auto', width: 18, textAlign: 'center', fontSize: 14 }}>{item.icon}</span>
              {!collapsed && <span>{item.label}</span>}
            </NavLink>
          ))}
        </div>
        {!collapsed && (
          <div style={{ padding: '12px 16px 0', borderTop: '1px solid var(--border)', marginTop: 12 }}>
            <ThemeToggle />
          </div>
        )}
      </nav>
      <div
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
        onMouseEnter={() => setResizerHover(true)}
        onMouseLeave={() => setResizerHover(false)}
        title={collapsed ? 'Click to expand' : 'Drag to resize, click to collapse'}
        style={{
          flex: '0 0 auto', width: 7, cursor: 'col-resize', position: 'relative', touchAction: 'none',
        }}
      >
        <div
          style={
            resizerActive || resizerHover
              ? { position: 'absolute', top: 0, bottom: 0, left: 2, width: 3, background: 'var(--accent)' }
              : { position: 'absolute', top: 0, bottom: 0, left: 3, width: 1, background: 'var(--border)' }
          }
        />
      </div>
      <main style={{ flex: '1 1 auto', minWidth: 0, background: 'var(--bg)', color: 'var(--text)' }}>
        <Outlet />
      </main>
    </div>
  )
}
