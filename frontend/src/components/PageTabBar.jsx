// Tab bar for the new Market Watch page -- a pinned "Market Watch" grid tab
// plus one tab per instrument the user has opened into its own detail view.
// `tabs` is the list of instrument tabs only (the grid tab is implicit and
// always first); `activeId` is null for the grid tab or an instrument id.
export default function PageTabBar({ tabs, activeId, onSelect, onClose }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 4, borderBottom: '1px solid var(--border)', marginBottom: 'var(--space-4)' }}>
      <TabButton active={activeId === null} onClick={() => onSelect(null)}>
        Market Watch
      </TabButton>
      {tabs.map((tab) => (
        <TabButton key={tab.id} active={activeId === tab.id} onClick={() => onSelect(tab.id)}>
          {tab.symbol}
          <span
            onClick={(e) => { e.stopPropagation(); onClose(tab.id) }}
            title={`Close ${tab.symbol} tab`}
            style={{ marginLeft: 8, color: 'var(--text-faint)', fontWeight: 700 }}
          >
            ×
          </span>
        </TabButton>
      ))}
    </div>
  )
}

function TabButton({ active, onClick, children }) {
  return (
    <button
      onClick={onClick}
      style={{
        display: 'flex', alignItems: 'center', fontSize: 13.5, fontWeight: active ? 700 : 500,
        padding: '8px 14px', border: 'none', borderBottom: active ? '2px solid var(--accent)' : '2px solid transparent',
        color: active ? 'var(--text)' : 'var(--text-dim)', background: 'transparent', marginBottom: -1,
      }}
    >
      {children}
    </button>
  )
}
