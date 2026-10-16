// Raw signals to mark on the graph (2026-10-16): single events the engine
// detects -- candle formations (doji, hammer, engulfing...), indicator
// crossovers (MACD, Stoch, RSI...), price action, structure, chart
// formations. One checkbox each, grouped, with a group "all" switch and
// today's count. Patterns (combinations) are on the Patterns tab.
// Standalone -- the caller owns the selection (`selected`, signal codes).
import { useSignals, SIGNAL_GROUPS } from './patternData.js'
import { Note, Title } from './PatternPicker.jsx'

const label = (code) => code.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())

export default function SignalPicker({ selected = [], onChange, counts = {} }) {
  const signals = useSignals()
  if (signals == null) return <Note>Loading signals…</Note>
  if (!signals.length) return <Note>No signals.</Note>
  const picked = new Set(selected)
  const set = (codes, on) => {
    const next = new Set(picked)
    codes.forEach((c) => (on ? next.add(c) : next.delete(c)))
    onChange?.([...next])
  }
  return (
    <div data-signal-picker>
      {SIGNAL_GROUPS.map((g) => {
        const rows = signals.filter((s) => g.kinds.includes(s.kind))
        if (!rows.length) return null
        const codes = rows.map((r) => r.code)
        const allOn = codes.every((c) => picked.has(c))
        return (
          <div key={g.key} data-signal-group={g.key} style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <Title>{g.label}</Title>
              <label title={`Mark every ${g.label.toLowerCase()} signal`} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, color: 'var(--text-faint)', cursor: 'pointer', marginBottom: 6 }}>
                <input type="checkbox" data-signal-group-all={g.key} checked={allOn} onChange={() => set(codes, !allOn)} />
                all
              </label>
            </div>
            {rows.map((s) => (
              <label key={s.code} data-signal-option={s.code} title={s.description}
                style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 2px', cursor: 'pointer', fontSize: 12.5,
                  color: picked.has(s.code) ? 'var(--text)' : 'var(--text-dim)' }}
              >
                <input type="checkbox" checked={picked.has(s.code)} onChange={() => set([s.code], !picked.has(s.code))} />
                <span style={{ flex: '1 1 auto', minWidth: 0 }}>{label(s.code)}</span>
                <span className="num" title="today" style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>{counts[s.code] ?? 0}</span>
              </label>
            ))}
          </div>
        )
      })}
      <Note>Ticked signals get a dot where they occur. The numbers are today's count.</Note>
    </div>
  )
}
