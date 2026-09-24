import { useCallback, useEffect, useState } from 'react'
import {
  deleteStrategy, getPatternDefaults, getRecommendationSystem, listRecommendationSystems, listStrategyElements,
  listWatchlists, putPatternDefaults, updateRecommendationSystem,
} from '../api/client.js'
import StrategyDesigner from '../components/StrategyDesigner.jsx'

const TIMEFRAMES = ['1min', '3min', '5min']
const boxStyle = { padding: 16, border: '1px solid #ccc', borderRadius: 6, marginBottom: 16 }
const th = { padding: 6 }

function Chips({ items }) {
  return (
    <span style={{ display: 'inline-flex', flexWrap: 'wrap', gap: 4 }}>
      {items.map((p) => (
        <span key={p} style={{ border: '1px solid #bbb', borderRadius: 12, padding: '0 8px', fontSize: 12 }}>{p}</span>
      ))}
    </span>
  )
}

// What actually gates each pattern, computed from the rules (read-only) so
// overlapping rules are never a guessing game.
function CoverageTable({ rules, combineMode }) {
  const patterns = [...new Set(rules.flatMap((r) => r.patterns))].sort()
  const join = combineMode === 'any' ? ' OR ' : ' AND '
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
      <thead>
        <tr style={{ textAlign: 'left', borderBottom: '2px solid #ccc' }}>
          <th style={th}>Pattern</th><th style={th}>Rules applied</th><th style={th}>Effective gate</th>
        </tr>
      </thead>
      <tbody>
        {patterns.map((p) => {
          const applied = rules.filter((r) => r.patterns.includes(p)).map((r) => r.name)
          const gate = applied.length > 1 ? `(${applied.join(join)})` : applied[0]
          return (
            <tr key={p} style={{ borderBottom: '1px solid #eee' }}>
              <td style={th}>{p}</td><td style={th}>{applied.join(' + ')}</td>
              <td style={th}>guiding scenario AND {gate}</td>
            </tr>
          )
        })}
        <tr>
          <td style={{ ...th, color: '#888' }}>every other pattern</td>
          <td style={{ ...th, color: '#888' }}>(none)</td>
          <td style={{ ...th, color: '#888' }}>guiding scenario only</td>
        </tr>
      </tbody>
    </table>
  )
}

// Per-pattern order defaults: how big, and where SL/target sit (percent off
// the entry price, direction-aware). The "(all other patterns)" row is the
// mandatory fallback and can't be removed.
function DefaultsEditor({ rows, patterns, onChange, onSave, saved }) {
  const update = (i, patch) => onChange(rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)))
  const num = (v) => (v === '' ? null : Number(v))
  const unused = patterns.filter((p) => !rows.some((r) => r.pattern === p))
  return (
    <>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ textAlign: 'left', borderBottom: '2px solid #ccc' }}>
            <th style={th}>Pattern</th><th style={th}>Order size</th>
            <th style={th}>SL %</th><th style={th}>Target %</th><th style={th} />
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.pattern} style={{ borderBottom: '1px solid #eee' }}>
              <td style={th}>{r.pattern === '*' ? '(all other patterns)' : r.pattern}</td>
              <td style={th}>
                <select
                  value={r.size_mode}
                  onChange={(e) => update(i, e.target.value === 'max_qty'
                    ? { size_mode: 'max_qty', max_qty: 1, fund_pct: null }
                    : { size_mode: 'available_fund', fund_pct: 10, max_qty: null })}
                >
                  <option value="max_qty">Max quantity</option>
                  <option value="available_fund">% of available fund</option>
                </select>{' '}
                {r.size_mode === 'max_qty'
                  ? <input type="number" min="1" style={{ width: 80 }} value={r.max_qty ?? ''}
                      onChange={(e) => update(i, { max_qty: num(e.target.value) })} />
                  : <><input type="number" min="0" max="100" style={{ width: 70 }} value={r.fund_pct ?? ''}
                      onChange={(e) => update(i, { fund_pct: num(e.target.value) })} /> %</>}
              </td>
              <td style={th}>
                <input type="number" step="0.05" style={{ width: 70 }} value={r.sl_pct ?? ''}
                  onChange={(e) => update(i, { sl_pct: num(e.target.value) })} />
              </td>
              <td style={th}>
                <input type="number" step="0.05" style={{ width: 70 }} value={r.target_pct ?? ''}
                  onChange={(e) => update(i, { target_pct: num(e.target.value) })} />
              </td>
              <td style={th}>
                {r.pattern !== '*' && (
                  <button type="button" onClick={() => onChange(rows.filter((_, idx) => idx !== i))}>Delete</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ display: 'flex', gap: 8, marginTop: 8, alignItems: 'center' }}>
        <select
          value="" onChange={(e) => e.target.value && onChange([...rows, {
            pattern: e.target.value, size_mode: 'max_qty', max_qty: 1, fund_pct: null, sl_pct: 0.4, target_pct: 0.8,
          }])}
        >
          <option value="">+ add pattern defaults</option>
          {unused.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <button type="button" onClick={onSave}>Save defaults</button>
        {saved && <span style={{ color: '#2f6f4f' }}>Saved</span>}
      </div>
    </>
  )
}

export default function RecommendationSystems() {
  const [defaults, setDefaults] = useState([])
  const [defaultsSaved, setDefaultsSaved] = useState(false)
  const [patternNames, setPatternNames] = useState([])
  const [systems, setSystems] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [form, setForm] = useState(null)
  const [watchlists, setWatchlists] = useState([])
  const [editingRule, setEditingRule] = useState(undefined) // undefined = closed, null = new, number = edit
  const [error, setError] = useState(null)
  const [saved, setSaved] = useState(false)

  const loadDetail = useCallback((id) => {
    getRecommendationSystem(id)
      .then((d) => { setDetail(d); setForm(d) })
      .catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    listStrategyElements()
      .then((els) => setPatternNames(els.filter((e) => e.element_type === 'event').map((e) => e.code).sort()))
      .catch((e) => setError(e.message))
    listWatchlists().then(setWatchlists).catch((e) => setError(e.message))
    listRecommendationSystems()
      .then((rows) => { setSystems(rows); if (rows.length) setSelectedId(rows[0].id) })
      .catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    if (selectedId == null) return
    loadDetail(selectedId)
    getPatternDefaults(selectedId).then((rows) => { setDefaults(rows); setDefaultsSaved(false) })
      .catch((e) => setError(e.message))
  }, [selectedId, loadDetail])

  function saveDefaults() {
    setError(null)
    putPatternDefaults(selectedId, defaults)
      .then((rows) => { setDefaults(rows); setDefaultsSaved(true) })
      .catch((err) => setError(err.message))
  }

  function saveSettings(e) {
    e.preventDefault()
    setError(null)
    setSaved(false)
    const fields = {
      name: form.name, is_active: form.is_active, watchlist_id: form.watchlist_id, timeframe: form.timeframe,
      top_n_per_run: form.top_n_per_run, rule_combine_mode: form.rule_combine_mode,
      fallback_capital: form.fallback_capital,
      win_pct_threshold_override: form.win_pct_threshold_override,
      min_sample_size_override: form.min_sample_size_override,
    }
    updateRecommendationSystem(selectedId, fields)
      .then((d) => { setDetail(d); setForm(d); setSaved(true) })
      .catch((err) => setError(err.message))
  }

  function removeRule(rule) {
    if (!window.confirm(`Delete rule "${rule.name}"?`)) return
    deleteStrategy(rule.id).then(() => loadDetail(selectedId)).catch((err) => setError(err.message))
  }

  if (!detail || !form) {
    return <div style={{ padding: 16 }}>{error ? <span style={{ color: 'crimson' }}>{error}</span> : 'Loading…'}</div>
  }

  const numOrNull = (v) => (v === '' ? null : Number(v))

  return (
    <div style={{ padding: 16, maxWidth: 1000, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <h2>Recommendation Systems</h2>
        <label>
          System{' '}
          <select value={selectedId ?? ''} onChange={(e) => setSelectedId(Number(e.target.value))}>
            {systems.map((s) => <option key={s.id} value={s.id}>{s.code}</option>)}
          </select>
        </label>
      </div>
      {error && <div style={{ color: 'crimson', marginBottom: 8 }}>{error}</div>}

      <form onSubmit={saveSettings} style={boxStyle}>
        <h3 style={{ marginTop: 0 }}>Scan settings — {detail.code}</h3>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <label>Name<br />
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} style={{ width: 260 }} />
          </label>
          <label>Watchlist<br />
            <select
              value={form.watchlist_id ?? ''}
              onChange={(e) => setForm({ ...form, watchlist_id: e.target.value ? Number(e.target.value) : null })}
            >
              <option value="">(none)</option>
              {watchlists.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
            </select>
          </label>
          <label>Timeframe<br />
            <select value={form.timeframe} onChange={(e) => setForm({ ...form, timeframe: e.target.value })}>
              {TIMEFRAMES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
          <label>Top-N per run<br />
            <input
              type="number" min="1" style={{ width: 70 }} value={form.top_n_per_run}
              onChange={(e) => setForm({ ...form, top_n_per_run: Number(e.target.value) })}
            />
          </label>
          <label>
            <input
              type="checkbox" checked={!!form.is_active}
              onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
            />{' '}Active
          </label>
        </div>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end', marginTop: 10 }}>
          <label>Win % threshold (blank = inherit)<br />
            <input
              type="number" step="0.01" style={{ width: 90 }} value={form.win_pct_threshold_override ?? ''}
              onChange={(e) => setForm({ ...form, win_pct_threshold_override: numOrNull(e.target.value) })}
            />
          </label>
          <label>Min sample (blank = inherit)<br />
            <input
              type="number" style={{ width: 90 }} value={form.min_sample_size_override ?? ''}
              onChange={(e) => setForm({ ...form, min_sample_size_override: numOrNull(e.target.value) })}
            />
          </label>
          <label>When a pattern has several rules<br />
            <select
              value={form.rule_combine_mode}
              onChange={(e) => setForm({ ...form, rule_combine_mode: e.target.value })}
            >
              <option value="all">all must pass</option>
              <option value="any">any one may pass</option>
            </select>
          </label>
          <label>Capital for "% of fund" sizing (₹)<br />
            <input
              type="number" style={{ width: 120 }} value={form.fallback_capital ?? ''}
              onChange={(e) => setForm({ ...form, fallback_capital: numOrNull(e.target.value) })}
            />
          </label>
          <button type="submit">Save settings</button>
          {saved && <span style={{ color: '#2f6f4f' }}>Saved</span>}
        </div>
        <p style={{ color: '#666', fontSize: 13, marginBottom: 0 }}>
          How a recommendation is decided: pattern fires → guiding scenario matches (pattern + intensity band, win % and
          sample-size gate) <b>AND</b> the rules covering that pattern pass → queued. A rule whose data isn't available
          yet is skipped (noted on the recommendation). A pattern with no rule is gated by the guiding scenario alone.
        </p>
      </form>

      <div style={boxStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
          <h3 style={{ marginTop: 0 }}>Rules</h3>
          <button type="button" onClick={() => setEditingRule(null)}>+ Add rule</button>
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ textAlign: 'left', borderBottom: '2px solid #ccc' }}>
              <th style={th}>Rule</th><th style={th}>Applies to</th><th style={th}>Conditions</th><th style={th} />
            </tr>
          </thead>
          <tbody>
            {detail.rules.map((r) => (
              <tr key={r.id} style={{ borderBottom: '1px solid #eee' }}>
                <td style={th}>{r.name}</td>
                <td style={th}><Chips items={r.patterns} /></td>
                <td style={{ ...th, fontFamily: 'monospace', fontSize: 12 }}>{r.summary || '—'}</td>
                <td style={{ ...th, whiteSpace: 'nowrap' }}>
                  <button type="button" onClick={() => setEditingRule(r.id)}>Edit</button>{' '}
                  <button type="button" onClick={() => removeRule(r)}>Delete</button>
                </td>
              </tr>
            ))}
            {detail.rules.length === 0 && (
              <tr><td colSpan={4} style={{ ...th, color: '#888' }}>
                No rules yet — every pattern is gated by its guiding scenario alone.
              </td></tr>
            )}
          </tbody>
        </table>

        {editingRule !== undefined && (
          <div style={{ marginTop: 16, padding: 12, border: '1px solid #bcd', borderRadius: 6, background: '#f7fafd' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <b>{editingRule === null ? 'New rule' : `Edit rule #${editingRule}`}</b>
              <button type="button" onClick={() => setEditingRule(undefined)}>Cancel</button>
            </div>
            <StrategyDesigner
              key={editingRule ?? 'new'} strategyId={editingRule} ruleMode
              sections={['name', 'patterns', 'conditions']}
              defaults={{ strategy_type: 'recommendation_rule', parent_id: detail.strategy_id }}
              onSaved={() => { setEditingRule(undefined); loadDetail(selectedId) }}
            />
          </div>
        )}
      </div>

      <div style={boxStyle}>
        <h3 style={{ marginTop: 0 }}>Pattern defaults</h3>
        <p style={{ color: '#666', fontSize: 13, marginTop: 0 }}>
          Every recommendation carries a suggested quantity, SL and target from its pattern's row (or the fallback).
          "% of available fund" is sized against the capital above until strategy accounts exist.
        </p>
        <DefaultsEditor
          rows={defaults} patterns={patternNames} onChange={(rows) => { setDefaults(rows); setDefaultsSaved(false) }}
          onSave={saveDefaults} saved={defaultsSaved}
        />
      </div>

      <div style={boxStyle}>
        <h3 style={{ marginTop: 0 }}>Coverage by pattern</h3>
        <CoverageTable rules={detail.rules} combineMode={detail.rule_combine_mode} />
      </div>
    </div>
  )
}
