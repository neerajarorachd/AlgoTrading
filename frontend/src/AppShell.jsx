import { NavLink, Outlet } from 'react-router-dom'

// Structural shell only — a working sidebar so pages are reachable via
// navigation instead of hardcoded, per explicit instruction: "generate UI
// as one single application, different pages will be accessible through
// menu or sidebar etc. design we will decide later but make them like one
// application." No visual polish yet, on purpose.
const NAV_ITEMS = [
  { to: '/', label: 'Market Watch', end: true },
  { to: '/strategies', label: 'Strategies' },
  { to: '/watchlists', label: 'Watchlists' },
  { to: '/backtests', label: 'Backtests' },
  { to: '/occurrence-backtest', label: 'Occurrence Backtest' },
  { to: '/recommendations', label: 'Recommendations' },
  { to: '/recommendation-systems', label: 'Recommendation Systems' },
]

const linkStyle = ({ isActive }) => ({
  display: 'block',
  padding: '10px 16px',
  color: isActive ? '#fff' : '#c7d2e0',
  background: isActive ? '#2f5fa8' : 'transparent',
  textDecoration: 'none',
  fontSize: 14,
})

export default function AppShell() {
  return (
    <div style={{ display: 'flex', minHeight: '100vh', fontFamily: 'sans-serif' }}>
      <nav style={{ width: 180, flex: '0 0 auto', background: '#1c2733', paddingBlock: 12 }}>
        <div style={{ color: '#fff', fontWeight: 600, padding: '0 16px 12px', fontSize: 15 }}>
          AlgoTrading
        </div>
        {NAV_ITEMS.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.end} style={linkStyle}>
            {item.label}
          </NavLink>
        ))}
      </nav>
      <main style={{ flex: '1 1 auto', minWidth: 0 }}>
        <Outlet />
      </main>
    </div>
  )
}
