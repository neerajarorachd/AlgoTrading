// Selectable list of patterns, grouped by type (ALGO-34). Reused wherever
// patterns are picked: the chart's Indicators panel, Market Watch pattern
// selection, Strategy Watch. The caller owns the selection (`selected`, an
// array of pattern keys) and how it is stored.
import { PATTERN_TYPES, usePatterns } from './patternData.js'

export default function PatternPicker({ selected = [], onToggle, types = null, enabledOnly = true, showWeight = true }) {
  const patterns = usePatterns()
  if (patterns == null) return <Note>Loading patterns…</Note>
  const shown = patterns.filter((p) => (!enabledOnly || p.enabled) && (!types || types.includes(p.pattern_type)))
  if (!shown.length) return <Note>No patterns yet.</Note>
  const picked = new Set(selected)
  return (
    <div data-pattern-picker>
      {PATTERN_TYPES.map((g) => {
        const rows = shown.filter((p) => p.pattern_type === g.type)
        if (!rows.length) return null
        return (
          <div key={g.type} style={{ marginBottom: 12 }}>
            <Title>{g.label}</Title>
            {rows.map((p) => (
              <label key={p.id} data-pattern-option={p.key} title={p.description}
                style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 2px', cursor: 'pointer', fontSize: 12.5,
                  color: picked.has(p.key) ? 'var(--text)' : 'var(--text-dim)' }}
              >
                <input type="checkbox" checked={picked.has(p.key)} onChange={() => onToggle?.(p.key)} />
                <span style={{ width: 7, height: 7, borderRadius: '50%', flex: 'none', background: g.color }} />
                <span style={{ flex: '1 1 auto', minWidth: 0 }}>{p.name}{p.origin === 'user' ? ' · mine' : ''}</span>
                {showWeight && <span className="num" title="weightage" style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>{p.weight}</span>}
              </label>
            ))}
          </div>
        )
      })}
    </div>
  )
}

export function Title({ children }) {
  return <p style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.04em', color: 'var(--text-faint)', margin: '0 0 6px' }}>{children}</p>
}

export function Note({ children }) {
  return <p style={{ fontSize: 11.5, color: 'var(--text-faint)', margin: '6px 2px 0', lineHeight: 1.45 }}>{children}</p>
}
