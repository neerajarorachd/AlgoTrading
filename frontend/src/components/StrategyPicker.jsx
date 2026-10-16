// Selectable list of strategies (ALGO-34). Reused wherever strategies are
// picked: the chart's Indicators panel, Strategy Watch. The caller owns the
// selection (`selected`, an array of strategy ids).
import { useStrategies } from './patternData.js'
import { GroupAll, Note, Title, setMany } from './PatternPicker.jsx'

// onChange(ids) (optional) turns on the "all" box; disabled keeps the ticks
// but locks them (the tab's Show is off, 2026-10-16)
export default function StrategyPicker({ selected = [], onToggle, onChange, title = 'Strategies', disabled = false }) {
  const strategies = useStrategies()
  if (strategies == null) return <Note>Loading strategies…</Note>
  if (!strategies.length) return <Note>No strategies yet.</Note>
  const picked = new Set(selected)
  return (
    <div data-strategy-picker>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        {title && <Title>{title}</Title>}
        {onChange && <GroupAll id="strategies" label="strategy" keys={strategies.map((s) => s.id)} picked={picked} disabled={disabled}
          onSet={(on) => onChange(setMany(selected, strategies.map((s) => s.id), on))} />}
      </div>
      {strategies.map((s) => (
        <label key={s.id} data-strategy-option={s.id} title={s.description || ''}
          style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 2px', cursor: disabled ? 'default' : 'pointer', fontSize: 12.5,
            color: picked.has(s.id) ? 'var(--text)' : 'var(--text-dim)', opacity: disabled ? 0.5 : 1 }}
        >
          <input type="checkbox" checked={picked.has(s.id)} disabled={disabled} onChange={() => onToggle?.(s.id)} />
          <span style={{ flex: '1 1 auto', minWidth: 0 }}>{s.name}</span>
          {/* strategies don't mark the chart yet, so there is nothing to count */}
          <span className="num" data-hit-count={`strategy:${s.id}`} title="strategy marks come later" style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>—</span>
        </label>
      ))}
    </div>
  )
}
