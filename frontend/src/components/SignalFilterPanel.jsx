// The Watch grid's signal filter panel (ALGO-37): the chart's Signals /
// Patterns / Strategies lists, one item at a time. Picking an item keeps
// only the stocks where it formed (the page does the filtering); the numbers
// are how many stocks have it in the chosen window (last 10 candles / whole
// day). Standalone -- the page owns the selection, window and counts.
import PatternPicker, { Note } from './PatternPicker.jsx'
import SignalPicker from './SignalPicker.jsx'
import StrategyPicker from './StrategyPicker.jsx'
import MarkWindowToggle, { windowUnit } from './MarkWindowToggle.jsx'
import { usePreference } from '../preferences.js'

const TABS = [
  { key: 'signal', label: 'Signals' },
  { key: 'pattern', label: 'Patterns' },
  { key: 'strategy', label: 'Strategies' },
]

// selected: { kind: 'signal' | 'pattern' | 'strategy', key } or null
// counts: { signal: {code: n}, pattern: {key: n}, strategy: {id: n} } -- stocks per item
export default function SignalFilterPanel({ selected, onSelect, window, onWindowChange, counts = {}, onClose }) {
  const [tab, setTab] = usePreference('workspace.signalFilter.tab', 'signal', (v) => TABS.some((t) => t.key === v))
  const pickedIn = (kind) => (selected?.kind === kind ? [selected.key] : [])
  // one at a time: picking the ticked item again clears the filter
  const toggle = (kind, key) => onSelect(selected?.kind === kind && String(selected.key) === String(key) ? null : { kind, key })
  const unit = windowUnit(window, 'stocks ')

  return (
    <div data-signal-filter-panel style={{ display: 'flex', flexDirection: 'column', minHeight: 0, height: '100%' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <span style={{ fontSize: 11.5, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.04em', color: 'var(--text-faint)' }}>Filter grid by signal</span>
        {onClose && (
          <button type="button" onClick={onClose} title="Close the panel (the filter stays on)" aria-label="Close signal filter panel"
            style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', fontSize: 15, padding: '0 4px' }}
          >×</button>
        )}
      </div>
      <MarkWindowToggle id="grid" value={window} onChange={onWindowChange} />
      <div role="tablist" aria-label="Signal filter" style={{ display: 'flex', gap: 2, borderBottom: '1px solid var(--border)', marginBottom: 10 }}>
        {TABS.map((t) => (
          <button
            key={t.key} type="button" role="tab" aria-selected={tab === t.key} data-filter-tab={t.key}
            onClick={() => setTab(t.key)}
            style={{
              flex: '1 1 0', border: 'none', background: 'transparent', cursor: 'pointer', padding: '6px 4px',
              fontSize: 12, fontWeight: tab === t.key ? 700 : 500,
              color: tab === t.key ? 'var(--text)' : 'var(--text-dim)',
              borderBottom: tab === t.key ? '2px solid var(--accent)' : '2px solid transparent', marginBottom: -1,
            }}
          >
            {t.label}
            {selected?.kind === t.key && <span style={{ marginLeft: 4, fontSize: 10.5, color: 'var(--accent-text)' }}>1</span>}
          </button>
        ))}
      </div>
      <div role="tabpanel" data-filter-panel={tab} style={{ overflowY: 'auto', minHeight: 0, flex: '1 1 auto' }}>
        {tab === 'signal' && (
          <SignalPicker
            selected={pickedIn('signal')} groupAll={false} counts={counts.signal} countUnit={unit}
            onChange={(codes) => {
              const added = codes.find((c) => !pickedIn('signal').includes(c))
              onSelect(added ? { kind: 'signal', key: added } : null)
            }}
            note="Pick one signal: the grid keeps only the stocks where it formed. The numbers are how many stocks have it."
          />
        )}
        {tab === 'pattern' && (
          <>
            <PatternPicker selected={pickedIn('pattern')} onToggle={(key) => toggle('pattern', key)} counts={counts.pattern ?? {}} countUnit={unit} />
            <Note>Pick one pattern: the grid keeps only the stocks where it formed. The numbers are how many stocks have it.</Note>
          </>
        )}
        {tab === 'strategy' && (
          <>
            <StrategyPicker selected={pickedIn('strategy')} onToggle={(id) => toggle('strategy', id)} counts={counts.strategy ?? {}} countUnit={unit} />
            <Note>Strategies don't record entries yet, so no stock matches a strategy until they do.</Note>
          </>
        )}
      </div>
    </div>
  )
}
