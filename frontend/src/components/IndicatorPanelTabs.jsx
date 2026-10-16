// The chart's marks window as three tabs (ALGO-34; 2026-10-16): Signals (raw
// single signals -- passed in as children; the indicators moved to a strip
// above the graph), Patterns and Strategies. The two lists are the shared PatternPicker / StrategyPicker; this
// component only owns which tab is open and the chart's selection, remembered
// per viewer and reported through onPatternsChange/onStrategiesChange so the
// chart can mark them (pattern marks arrive with pattern evaluation, ALGO-32).
import { useEffect } from 'react'
import PatternPicker, { Note } from './PatternPicker.jsx'
import StrategyPicker from './StrategyPicker.jsx'
import { usePatterns } from './patternData.js'
import { usePreference } from '../preferences.js'

const TABS = [
  { key: 'signals', label: 'Signals' },
  { key: 'patterns', label: 'Patterns' },
  { key: 'strategies', label: 'Strategies' },
]
const isList = (v) => Array.isArray(v)
function toggled(list, id) {
  return list.includes(id) ? list.filter((x) => x !== id) : [...list, id]
}

export default function IndicatorPanelTabs({ children, onPatternsChange, onStrategiesChange }) {
  // the user's last choices (preferences.js). Patterns: every enabled pattern
  // is ticked until the user changes the selection (ALGO-34 default: the chart
  // shows pattern dots, raw events off).
  const [tab, setTab] = usePreference('chart.panelTab', 'signals', (v) => TABS.some((t) => t.key === v))
  const [storedPatterns, setPatterns] = usePreference('chart.selectedPatterns.v2', null, (v) => v === null || isList(v))
  const [strategies, setStrategies] = usePreference('chart.selectedStrategies', [], isList)
  const allPatterns = usePatterns()
  const patterns = storedPatterns ?? (allPatterns ? allPatterns.filter((p) => p.enabled).map((p) => p.key) : [])
  const patternsKey = patterns.join(',')

  useEffect(() => { onPatternsChange?.(patterns) }, [patternsKey]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { onStrategiesChange?.(strategies) }, [strategies]) // eslint-disable-line react-hooks/exhaustive-deps

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
        {tab === 'signals' && children}
        {tab === 'patterns' && (
          <>
            <PatternPicker selected={patterns} onToggle={(key) => setPatterns(toggled(patterns, key))} />
            <Note>Ticked patterns get a dot where they form. Raw signals (Signals tab) are off unless ticked there.</Note>
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
