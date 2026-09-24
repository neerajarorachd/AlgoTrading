import { useCallback, useEffect, useState } from 'react'
import { deleteStrategy, listStrategies } from '../api/client.js'
import StrategyDesigner from '../components/StrategyDesigner.jsx'
import StrategyTable from '../components/StrategyTable.jsx'

export default function Strategies() {
  const [strategies, setStrategies] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [error, setError] = useState(null)

  const refreshList = useCallback(() => {
    listStrategies().then(setStrategies).catch((e) => setError(e.message))
  }, [])

  useEffect(() => { refreshList() }, [refreshList])

  function handleDelete(id) {
    deleteStrategy(id)
      .then(() => {
        refreshList()
        if (id === selectedId) setSelectedId(null)
      })
      .catch((e) => setError(e.message))
  }

  return (
    <div style={{ padding: 16, maxWidth: 1000, margin: '0 auto' }}>
      <h2>Strategies</h2>

      <StrategyTable
        strategies={strategies} selectedId={selectedId}
        onSelect={setSelectedId} onDelete={handleDelete}
      />
      {error && <div style={{ color: 'crimson', marginTop: 8 }}>{error}</div>}

      <div style={{ marginTop: 20, padding: 16, border: '1px solid #ccc', borderRadius: 6 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ margin: 0 }}>{selectedId ? `Edit strategy #${selectedId}` : 'New strategy'}</h3>
          {selectedId && <button type="button" onClick={() => setSelectedId(null)}>+ New strategy</button>}
        </div>

        <StrategyDesigner
          key={selectedId ?? 'new'} strategyId={selectedId}
          onSaved={(saved) => { refreshList(); setSelectedId(saved.id) }}
        />
      </div>
    </div>
  )
}
