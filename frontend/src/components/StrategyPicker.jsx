// Selectable list of strategies (ALGO-34). Reused wherever strategies are
// picked: the chart's Indicators panel, Strategy Watch. The caller owns the
// selection (`selected`, an array of strategy ids).
import { useStrategies } from './patternData.js'
import { Note, Title } from './PatternPicker.jsx'

export default function StrategyPicker({ selected = [], onToggle, title = 'Strategies' }) {
  const strategies = useStrategies()
  if (strategies == null) return <Note>Loading strategies…</Note>
  if (!strategies.length) return <Note>No strategies yet.</Note>
  const picked = new Set(selected)
  return (
    <div data-strategy-picker>
      {title && <Title>{title}</Title>}
      {strategies.map((s) => (
        <label key={s.id} data-strategy-option={s.id} title={s.description || ''}
          style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 2px', cursor: 'pointer', fontSize: 12.5,
            color: picked.has(s.id) ? 'var(--text)' : 'var(--text-dim)' }}
        >
          <input type="checkbox" checked={picked.has(s.id)} onChange={() => onToggle?.(s.id)} />
          <span style={{ flex: '1 1 auto', minWidth: 0 }}>{s.name}</span>
        </label>
      ))}
    </div>
  )
}
