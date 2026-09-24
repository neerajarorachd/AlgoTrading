import { useEffect, useState } from 'react'
import {
  createStrategy, getStrategy, getStrategyFields, listStrategies, listStrategyElements, updateStrategy,
} from '../api/client.js'
import StrategyBuilder, { emptyGroup } from './StrategyBuilder.jsx'
import StrategyOrderManagementForm, {
  EMPTY_ORDER_MANAGEMENT_FORM, orderManagementValuesFromStrategy,
} from './StrategyOrderManagementForm.jsx'

// THE strategy designer — one component every screen that edits a Strategy
// uses (the Strategies page, and the Recommendation Systems rule editor), so
// a change here shows up everywhere. Screens differ only in `sections`:
//   meta            name / type / family / description
//   parent          parent-strategy picker (Strategy.parent_id)
//   orderManagement order sizing, SL/target, risk, liquidity
//   name            just name + description (rule editor)
//   patterns        which patterns this strategy covers (Strategy.pattern_filter)
//   conditions      the AND/OR condition tree (Event / Indicator / Formula)
// ruleMode = a recommendation-system rule: formula conditions only, no
// order_size constant (a recommendation has no order), at least one pattern.
export const ALL_SECTIONS = ['meta', 'parent', 'orderManagement', 'conditions']

const BASE_FORM = {
  name: '', strategy_type: 'entry', description: '', family: '', parent_id: null, pattern_filter: null,
}

function PatternPicker({ value, options, onChange }) {
  const selected = (value || '').split(',').map((p) => p.trim()).filter(Boolean)
  const set = (list) => onChange(list.length ? list.join(',') : null)
  return (
    <div style={{ marginBottom: 12 }}>
      Applies to patterns<br />
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center', marginTop: 4 }}>
        {selected.map((p) => (
          <span key={p} style={{ border: '1px solid #bbb', borderRadius: 12, padding: '1px 8px', fontSize: 13 }}>
            {p}{' '}
            <button
              type="button" title={`remove ${p}`} onClick={() => set(selected.filter((x) => x !== p))}
              style={{ border: 'none', background: 'none', cursor: 'pointer' }}
            >
              ×
            </button>
          </span>
        ))}
        <select value="" onChange={(e) => e.target.value && set([...selected, e.target.value])}>
          <option value="">+ add pattern</option>
          {options.filter((o) => !selected.includes(o)).map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      </div>
    </div>
  )
}

function newForm(defaults) {
  return { ...BASE_FORM, ...EMPTY_ORDER_MANAGEMENT_FORM, ...defaults }
}

// strategyId null = a new strategy (initial values from `defaults`).
// Remount with a different `key` to switch strategies.
export default function StrategyDesigner({
  strategyId = null, sections = ALL_SECTIONS, defaults = {}, onSaved, ruleMode = false,
}) {
  const [elements, setElements] = useState(null)
  const [grammar, setGrammar] = useState(null)
  const [parents, setParents] = useState([])
  const [form, setForm] = useState(() => newForm(defaults))
  const [tree, setTree] = useState(emptyGroup())
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)
  const has = (section) => sections.includes(section)

  useEffect(() => {
    Promise.all([listStrategyElements(), getStrategyFields()])
      .then(([els, g]) => { setElements(els); setGrammar(g) })
      .catch((e) => setError(e.message))
    if (sections.includes('parent')) listStrategies().then(setParents).catch((e) => setError(e.message))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (strategyId == null) return
    getStrategy(strategyId)
      .then((s) => {
        setForm({
          name: s.name, strategy_type: s.strategy_type, description: s.description || '',
          family: s.family || '', parent_id: s.parent_id ?? null, pattern_filter: s.pattern_filter ?? null,
          ...orderManagementValuesFromStrategy(s),
        })
        setTree(s.tree || emptyGroup())
      })
      .catch((e) => setError(e.message))
  }, [strategyId])

  function handleSave(e) {
    e.preventDefault()
    if (!form.name.trim() || !form.strategy_type.trim()) {
      setError('Name and type are required')
      return
    }
    if (ruleMode && !form.pattern_filter) {
      setError('Pick at least one pattern this rule applies to')
      return
    }
    setSaving(true)
    setError(null)
    const payload = { ...form, tree }
    const request = strategyId ? updateStrategy(strategyId, payload) : createStrategy(payload)
    request
      .then((saved) => onSaved?.(saved))
      .catch((e) => setError(e.message))
      .finally(() => setSaving(false))
  }

  if (elements === null || grammar === null) {
    return <div>{error ? <span style={{ color: 'crimson' }}>{error}</span> : 'Loading…'}</div>
  }

  return (
    <form onSubmit={handleSave} style={{ marginTop: 12 }}>
      {has('meta') && (
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
          <label>
            Name<br />
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
          </label>
          <label>
            Type<br />
            <input value={form.strategy_type} onChange={(e) => setForm({ ...form, strategy_type: e.target.value })} required />
          </label>
          <label>
            Family<br />
            <input value={form.family} onChange={(e) => setForm({ ...form, family: e.target.value })} />
          </label>
          <label style={{ flex: '1 1 240px' }}>
            Description<br />
            <input
              style={{ width: '100%' }} value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
            />
          </label>
        </div>
      )}

      {has('name') && (
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
          <label>
            Name<br />
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
          </label>
          <label style={{ flex: '1 1 240px' }}>
            Description<br />
            <input
              style={{ width: '100%' }} value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
            />
          </label>
        </div>
      )}

      {has('patterns') && (
        <PatternPicker
          value={form.pattern_filter}
          options={elements.filter((e) => e.element_type === 'event').map((e) => e.code)}
          onChange={(v) => setForm({ ...form, pattern_filter: v })}
        />
      )}

      {has('parent') && (
        <label style={{ display: 'block', marginBottom: 12 }}>
          Parent strategy<br />
          <select
            value={form.parent_id ?? ''}
            onChange={(e) => setForm({ ...form, parent_id: e.target.value ? Number(e.target.value) : null })}
          >
            <option value="">(none)</option>
            {parents.filter((p) => p.id !== strategyId).map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </label>
      )}

      {has('orderManagement') && (
        <>
          <h4>Order management</h4>
          <StrategyOrderManagementForm values={form} onChange={setForm} />
        </>
      )}

      {has('conditions') && (
        <>
          <h4>Conditions</h4>
          <StrategyBuilder
            tree={tree} onChange={setTree} elements={elements}
            grammar={ruleMode ? { ...grammar, constants: [] } : grammar}
            leafKinds={ruleMode ? ['formula'] : undefined}
          />
        </>
      )}

      {error && <div style={{ color: 'crimson', marginBottom: 8 }}>{error}</div>}

      <button type="submit" disabled={saving}>
        {saving ? 'Saving…' : strategyId ? 'Save changes' : 'Create strategy'}
      </button>
    </form>
  )
}
