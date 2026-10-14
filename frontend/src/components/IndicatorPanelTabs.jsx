// The chart's Indicators panel as three tabs (ALGO-34): Indicators (overlays,
// sub-panels and raw events -- passed in as children, unchanged), Patterns and
// Strategies. The two lists are the shared PatternPicker / StrategyPicker; this
// component only owns which tab is open and the chart's selection, remembered
// per viewer and reported through onPatternsChange/onStrategiesChange so the
// chart can mark them (pattern marks arrive with pattern evaluation, ALGO-32).
import { useEffect, useState } from 'react'
import PatternPicker, { Note } from './PatternPicker.jsx'
import StrategyPicker from './StrategyPicker.jsx'

const TABS = [
  { key: 'indicators', label: 'Indicators' },
  { key: 'patterns', label: 'Patterns' },
  { key: 'strategies', label: 'Strategies' },
]
const STORE = { tab: 'chart.panelTab', patterns: 'chart.selectedPatterns', strategies: 'chart.selectedStrategies' }

function load(key, fallback) {
  try { const v = localStorage.getItem(key); return v == null ? fallback : JSON.parse(v) } catch { return fallback }
}
function save(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* private mode etc. -- fine */ }
}
function toggled(list, id) {
  return list.includes(id) ? list.filter((x) => x !== id) : [...list, id]
}

export default function IndicatorPanelTabs({ children, onPatternsChange, onStrategiesChange }) {
  const [tab, setTab] = useState(() => load(STORE.tab, 'indicators'))
  const [patterns, setPatterns] = useState(() => load(STORE.patterns, []))
  const [strategies, setStrategies] = useState(() => load(STORE.strategies, []))

  useEffect(() => { save(STORE.tab, tab) }, [tab])
  useEffect(() => { save(STORE.patterns, patterns); onPatternsChange?.(patterns) }, [patterns]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { save(STORE.strategies, strategies); onStrategiesChange?.(strategies) }, [strategies]) // eslint-disable-line react-hooks/exhaustive-deps

  const count = { patterns: patterns.length, strategies: strategies.length }
  return (
    <div data-indicator-tabs style={{ display: 'flex', flexDirection: 'column', minHeight: 0, height: '100%' }}>
      <div role="tablist" aria-label="Chart marks" style={{ display: 'flex', gap: 2, borderBottom: '1px solid var(--border)', marginBottom: 10 }}>
        {TABS.map((t) => (
          <button
            key={t.key} type="button" role="tab" aria-selected={tab === t.key} data-panel-tab={t.key}
            onClick={() => setTab(t.key)}
            style={{
              flex: '1 1 0', border: 'none', background: 'transparent', cursor: 'pointer', padding: '6px 4px',
              fontSize: 12, fontWeight: tab === t.key ? 700 : 500,
              color: tab === t.key ? 'var(--text)' : 'var(--text-dim)',
              borderBottom: tab === t.key ? '2px solid var(--accent)' : '2px solid transparent', marginBottom: -1,
            }}
          >
            {t.label}
            {count[t.key] > 0 && <span style={{ marginLeft: 4, fontSize: 10.5, color: 'var(--accent-text)' }}>{count[t.key]}</span>}
          </button>
        ))}
      </div>

      <div role="tabpanel" data-panel={tab} style={{ overflowY: 'auto', minHeight: 0, flex: '1 1 auto' }}>
        {tab === 'indicators' && children}
        {tab === 'patterns' && (
          <>
            <PatternPicker selected={patterns} onToggle={(key) => setPatterns((prev) => toggled(prev, key))} />
            <Note>Ticked patterns are marked on the chart once pattern evaluation is live.</Note>
          </>
        )}
        {tab === 'strategies' && (
          <>
            <StrategyPicker selected={strategies} onToggle={(id) => setStrategies((prev) => toggled(prev, id))} />
            <Note>Strategy entries and exits are marked once strategies use patterns.</Note>
          </>
        )}
      </div>
    </div>
  )
}
