import { useCallback, useEffect, useState } from 'react'
import {
  createStrategy, deleteStrategy, getStrategy, listStrategies, listStrategyElements, updateStrategy,
} from '../api/client.js'
import StrategyBuilder, { emptyGroup } from '../components/StrategyBuilder.jsx'
import StrategyTable from '../components/StrategyTable.jsx'

const EMPTY_FORM = { name: '', strategy_type: 'entry', description: '', family: '' }

export default function Strategies() {
  const [elements, setElements] = useState(null) // null while loading
  const [strategies, setStrategies] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const [tree, setTree] = useState(emptyGroup())
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)

  const refreshList = useCallback(() => {
    listStrategies().then(setStrategies).catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    listStrategyElements().then(setElements).catch((e) => setError(e.message))
    refreshList()
  }, [refreshList])

  function startNew() {
    setSelectedId(null)
    setForm(EMPTY_FORM)
    setTree(emptyGroup())
    setError(null)
  }

  function selectStrategy(id) {
    setError(null)
    getStrategy(id)
      .then((s) => {
        setSelectedId(s.id)
        setForm({
          name: s.name, strategy_type: s.strategy_type,
          description: s.description || '', family: s.family || '',
        })
        setTree(s.tree || emptyGroup())
      })
      .catch((e) => setError(e.message))
  }

  function handleSave(e) {
    e.preventDefault()
    if (!form.name.trim() || !form.strategy_type.trim()) {
      setError('Name and type are required')
      return
    }
    setSaving(true)
    setError(null)
    const payload = { ...form, tree }
    const request = selectedId ? updateStrategy(selectedId, payload) : createStrategy(payload)
    request
      .then((saved) => {
        refreshList()
        setSelectedId(saved.id)
      })
      .catch((e) => setError(e.message))
      .finally(() => setSaving(false))
  }

  function handleDelete(id) {
    deleteStrategy(id)
      .then(() => {
        refreshList()
        if (id === selectedId) startNew()
      })
      .catch((e) => setError(e.message))
  }

  if (elements === null) {
    return <div style={{ padding: 16 }}>Loading…</div>
  }

  return (
    <div style={{ padding: 16, maxWidth: 1000, margin: '0 auto' }}>
      <h2>Strategies</h2>

      <StrategyTable
        strategies={strategies} selectedId={selectedId}
        onSelect={selectStrategy} onDelete={handleDelete}
      />

      <div style={{ marginTop: 20, padding: 16, border: '1px solid #ccc', borderRadius: 6 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ margin: 0 }}>{selectedId ? `Edit strategy #${selectedId}` : 'New strategy'}</h3>
          {selectedId && <button type="button" onClick={startNew}>+ New strategy</button>}
        </div>

        <form onSubmit={handleSave} style={{ marginTop: 12 }}>
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
                style={{ width: '100%' }}
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
              />
            </label>
          </div>

          <h4>Conditions</h4>
          <StrategyBuilder tree={tree} onChange={setTree} elements={elements} />

          {error && <div style={{ color: 'crimson', marginBottom: 8 }}>{error}</div>}

          <button type="submit" disabled={saving}>
            {saving ? 'Saving…' : selectedId ? 'Save changes' : 'Create strategy'}
          </button>
        </form>
      </div>
    </div>
  )
}
