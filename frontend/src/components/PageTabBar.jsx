// Tab bar for the Workspace page -- a pinned "Market Watch" grid tab, one tab
// per instrument the user has opened into its own detail view, and a "+"
// (AddTabMenu) for adding a tab by hand.
// `tabs` is the list of instrument tabs only (the grid tab is implicit and
// always first); `activeId` is null for the grid tab or an instrument id.
import AddTabMenu from './AddTabMenu.jsx'

// `pinned` (optional): extra fixed tabs right after Market Watch, never closable
// -- e.g. the Trades tab (rulebook 3b): [{ id: 'trades', label: 'Trades' }].
export default function PageTabBar({ tabs, activeId, onSelect, onClose, onAddTab, pinned = [] }) {
  return (
    <div data-tab-pane style={{ display: 'flex', alignItems: 'center', gap: 4, borderBottom: '1px solid var(--border)', marginBottom: 'var(--space-4)' }}>
      <TabButton active={activeId === null} onClick={() => onSelect(null)}>
        Market Watch
      </TabButton>
      {pinned.map((p) => (
        <TabButton key={p.id} active={activeId === p.id} onClick={() => onSelect(p.id)} testid={`tab-${p.id}`}>
          {p.label}
        </TabButton>
      ))}
      {tabs.map((tab) => (
        <TabButton key={tab.id} active={activeId === tab.id} onClick={() => onSelect(tab.id)}>
          {tab.label ?? tab.symbol}
          <span
            onClick={(e) => { e.stopPropagation(); onClose(tab.id) }}
            title={`Close ${tab.label ?? tab.symbol} tab`}
            style={{ marginLeft: 8, color: 'var(--text-faint)', fontWeight: 700 }}
          >
            ×
          </span>
        </TabButton>
      ))}
      <AddTabMenu onPick={onAddTab} />
    </div>
  )
}

function TabButton({ active, onClick, children, testid }) {
  return (
    <button
      onClick={onClick} data-testid={testid}
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
