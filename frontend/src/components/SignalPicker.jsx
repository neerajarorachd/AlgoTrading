// Raw signals to mark on the graph (2026-10-16): single events the engine
// detects -- candle formations (doji, hammer, engulfing...), indicator
// crossovers (MACD, Stoch, RSI...), price action, structure, chart
// formations. One checkbox each, grouped, with a group "all" switch and
// today's count. Patterns (combinations) are on the Patterns tab.
// Standalone -- the caller owns the selection (`selected`, signal codes).
import { useSignals, SIGNAL_GROUPS } from './patternData.js'
import { GroupAll, HitCount, Note, Title } from './PatternPicker.jsx'

const label = (code) => code.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())

export default function SignalPicker({ selected = [], onChange, counts = {}, disabled = false, onOpenReview }) {
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
        return (
          <div key={g.key} data-signal-group={g.key} style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <Title>{g.label}</Title>
              <GroupAll id={`signal-${g.key}`} label={g.label} keys={codes} picked={picked} disabled={disabled} onSet={(on) => set(codes, on)} />
            </div>
            {rows.map((s) => (
              <label key={s.code} data-signal-option={s.code} title={s.description}
                style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 2px', cursor: disabled ? 'default' : 'pointer', fontSize: 12.5,
                  color: picked.has(s.code) ? 'var(--text)' : 'var(--text-dim)', opacity: disabled ? 0.5 : 1 }}
              >
                <input type="checkbox" checked={picked.has(s.code)} disabled={disabled} onChange={() => set([s.code], !picked.has(s.code))} />
                <span style={{ flex: '1 1 auto', minWidth: 0 }}>{label(s.code)}</span>
                <HitCount id={`signal:${s.code}`} count={counts[s.code]} onOpen={onOpenReview && (() => onOpenReview('signal', s.code, label(s.code)))} />
              </label>
            ))}
          </div>
        )
      })}
      <Note>Ticked signals get a dot where they occur. The numbers are today's count on this timeframe; click one to see each occurrence.</Note>
    </div>
  )
}
