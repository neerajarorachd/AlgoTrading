export default function StrategyTable({ strategies, selectedId, onSelect, onDelete }) {
  return (
    <table border="1" cellPadding="6" style={{ borderCollapse: 'collapse', width: '100%' }}>
      <thead style={{ background: '#f2f2f2' }}>
        <tr>
          <th>Name</th><th>Type</th><th>Family</th><th>Description</th><th></th>
        </tr>
      </thead>
      <tbody>
        {strategies.map((s) => (
          <tr
            key={s.id}
            onClick={() => onSelect(s.id)}
            style={{ cursor: 'pointer', background: s.id === selectedId ? '#dfe8ff' : undefined }}
          >
            <td>{s.name}</td>
            <td>{s.strategy_type}</td>
            <td>{s.family || '—'}</td>
            <td>{s.description || '—'}</td>
            <td>
              <button onClick={(e) => { e.stopPropagation(); onDelete(s.id) }}>Delete</button>
            </td>
          </tr>
        ))}
        {strategies.length === 0 && (
          <tr><td colSpan={5} style={{ textAlign: 'center', color: '#888' }}>No strategies yet</td></tr>
        )}
      </tbody>
    </table>
  )
}
