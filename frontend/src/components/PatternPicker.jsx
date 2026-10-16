// Selectable list of patterns, grouped by type (ALGO-34). Reused wherever
// patterns are picked: the chart's Indicators panel, Market Watch pattern
// selection, Strategy Watch. The caller owns the selection (`selected`, an
// array of pattern keys) and how it is stored.
import { PATTERN_TYPES, usePatterns } from './patternData.js'

// onChange(keys) (optional): the whole selection at once -- turns on each
// group's "all" box. disabled: the list keeps its ticks but can't be changed
// (the tab's Show is off, 2026-10-16).
export default function PatternPicker({ selected = [], onToggle, onChange, types = null, enabledOnly = true, showWeight = true, disabled = false, counts, onOpenReview }) {
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
        const keys = rows.map((p) => p.key)
        return (
          <div key={g.type} data-pattern-group={g.type} style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <Title>{g.label}</Title>
              {onChange && <GroupAll id={`pattern-${g.type}`} label={g.label} keys={keys} picked={picked} disabled={disabled}
                onSet={(on) => onChange(setMany(selected, keys, on))} />}
            </div>
            {rows.map((p) => (
              <label key={p.id} data-pattern-option={p.key} title={p.description}
                style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 2px', cursor: disabled ? 'default' : 'pointer', fontSize: 12.5,
                  color: picked.has(p.key) ? 'var(--text)' : 'var(--text-dim)', opacity: disabled ? 0.5 : 1 }}
              >
                <input type="checkbox" checked={picked.has(p.key)} disabled={disabled} onChange={() => onToggle?.(p.key)} />
                <span style={{ width: 7, height: 7, borderRadius: '50%', flex: 'none', background: g.color }} />
                <span style={{ flex: '1 1 auto', minWidth: 0 }}>{p.name}{p.origin === 'user' ? ' · mine' : ''}</span>
                {showWeight && <span className="num" title="weightage" style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>{p.weight}</span>}
                {counts && <HitCount id={`pattern:${p.key}`} count={counts[p.key]} onOpen={onOpenReview && (() => onOpenReview('pattern', p.key, p.name))} />}
              </label>
            ))}
          </div>
        )
      })}
    </div>
  )
}

// the selection with `keys` all added (on) or all removed (off)
export function setMany(selected, keys, on) {
  const next = new Set(selected)
  keys.forEach((k) => (on ? next.add(k) : next.delete(k)))
  return [...next]
}

// today's count beside an item; clicking it opens the hit review (2026-10-16)
export function HitCount({ id, count, onOpen }) {
  const n = count ?? 0
  const clickable = !!onOpen && n > 0
  return (
    <button
      type="button" data-hit-count={id} disabled={!clickable}
      onClick={(e) => { e.preventDefault(); e.stopPropagation(); if (clickable) onOpen() }}
      title={clickable ? `${n} today -- click to see why each one fired` : 'none today'}
      className="num"
      style={{ minWidth: 24, textAlign: 'right', fontSize: 11.5, border: 'none', background: 'transparent', padding: '0 2px',
        cursor: clickable ? 'pointer' : 'default', color: clickable ? 'var(--accent-text)' : 'var(--text-faint)',
        textDecoration: clickable ? 'underline' : 'none', textUnderlineOffset: 2 }}
    >{n}</button>
  )
}

// a group's "all" box: ticked when every item in the group is (2026-10-16)
export function GroupAll({ id, label, keys, picked, onSet, disabled = false }) {
  const allOn = keys.length > 0 && keys.every((k) => picked.has(k))
  return (
    <label title={`Mark every ${String(label).toLowerCase()} item`}
      style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, color: 'var(--text-faint)', cursor: disabled ? 'default' : 'pointer', marginBottom: 6, opacity: disabled ? 0.5 : 1 }}
    >
      <input type="checkbox" data-group-all={id} checked={allOn} disabled={disabled} onChange={() => onSet(!allOn)} />
      all
    </label>
  )
}

// a tab's "Show" box: off = its marks are hidden from the graph, its ticks kept
export function ShowToggle({ id, on, onChange }) {
  return (
    <label data-tab-show={id} title={on ? 'Shown on the graph (untick to hide these marks but keep the ticks)' : 'Hidden from the graph -- the ticks below are kept (tick to show them again)'}
      style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 700, color: 'var(--text)', cursor: 'pointer', padding: '2px 2px 8px', borderBottom: '1px solid var(--border)', marginBottom: 8 }}
    >
      <input type="checkbox" checked={on} onChange={() => onChange(!on)} />
      Show on graph
    </label>
  )
}

export function Title({ children }) {
  return <p style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.04em', color: 'var(--text-faint)', margin: '0 0 6px' }}>{children}</p>
}

export function Note({ children }) {
  return <p style={{ fontSize: 11.5, color: 'var(--text-faint)', margin: '6px 2px 0', lineHeight: 1.45 }}>{children}</p>
}
