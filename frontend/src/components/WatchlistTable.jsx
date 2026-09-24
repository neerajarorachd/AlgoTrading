export default function WatchlistTable({ watchlists, selectedId, onSelect, onDelete }) {
  return (
    <table border="1" cellPadding="6" style={{ borderCollapse: 'collapse', width: '100%' }}>
      <thead style={{ background: '#f2f2f2' }}>
        <tr>
          <th>Name</th><th>Description</th><th>Members</th><th></th>
        </tr>
      </thead>
      <tbody>
        {watchlists.map((w) => (
          <tr
            key={w.id}
            onClick={() => onSelect(w.id)}
            style={{ cursor: 'pointer', background: w.id === selectedId ? '#dfe8ff' : undefined }}
          >
            <td>{w.name}</td>
            <td>{w.description || '—'}</td>
            <td>{w.member_count}</td>
            <td>
              <button onClick={(e) => { e.stopPropagation(); onDelete(w.id) }}>Delete</button>
            </td>
          </tr>
        ))}
        {watchlists.length === 0 && (
          <tr><td colSpan={4} style={{ textAlign: 'center', color: '#888' }}>No watchlists yet</td></tr>
        )}
      </tbody>
    </table>
  )
}
