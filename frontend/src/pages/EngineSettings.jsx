import { useEffect, useState } from 'react'
import { listEngineSettings, resetEngineSetting, updateEngineSetting } from '../api/client.js'

// Promotes a backtest-proven ActivityEngine parameter (e.g. swing_lookback,
// compared per-run via Strategy.swing_lookback in a backtest) into the one
// value live trading's single shared ActivityEngine actually reads --
// previously only possible by hand-writing SQL against engine_settings.
const th = { padding: '6px 10px', textAlign: 'left' }
const td = { padding: '6px 10px', verticalAlign: 'top' }

export default function EngineSettings() {
  const [rows, setRows] = useState(null)
  const [drafts, setDrafts] = useState({})
  const [error, setError] = useState(null)
  const [savingKey, setSavingKey] = useState(null)

  const load = () => listEngineSettings().then((data) => {
    setRows(data)
    setDrafts(Object.fromEntries(data.map((r) => [r.key, String(r.value)])))
  }).catch((e) => setError(e.message))

  useEffect(() => { load() }, [])

  function save(key) {
    const value = Number(drafts[key])
    if (Number.isNaN(value)) {
      setError(`${key}: value must be numeric`)
      return
    }
    setSavingKey(key)
    setError(null)
    updateEngineSetting(key, value).then(setRows).catch((e) => setError(e.message)).finally(() => setSavingKey(null))
  }

  function reset(key) {
    setSavingKey(key)
    setError(null)
    resetEngineSetting(key)
      .then((data) => {
        setRows(data)
        setDrafts((d) => ({ ...d, [key]: String(data.find((r) => r.key === key).value) }))
      })
      .catch((e) => setError(e.message))
      .finally(() => setSavingKey(null))
  }

  if (rows === null) {
    return <div>{error ? <span style={{ color: 'crimson' }}>{error}</span> : 'Loading…'}</div>
  }

  return (
    <div style={{ padding: 16 }}>
      <h2>Engine settings</h2>
      <p style={{ color: '#666', maxWidth: 720 }}>
        Calibrated overrides for the live pattern-detection engine. A backtest can already compare
        candidate values per strategy run — once one wins, set it here to make live trading (and
        every future backtest that doesn't override it) use that value too.
      </p>
      {error && <div style={{ color: 'crimson', marginBottom: 8 }}>{error}</div>}
      <table style={{ borderCollapse: 'collapse', width: '100%', maxWidth: 900 }}>
        <thead>
          <tr style={{ borderBottom: '2px solid #ccc' }}>
            <th style={th}>Key</th>
            <th style={th}>Description</th>
            <th style={th}>Default</th>
            <th style={th}>Value</th>
            <th style={th}>Status</th>
            <th style={th} />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key} style={{ borderBottom: '1px solid #eee' }}>
              <td style={{ ...td, fontFamily: 'monospace' }}>{row.key}</td>
              <td style={{ ...td, color: '#555', maxWidth: 360 }}>{row.description}</td>
              <td style={td}>{row.default_value}</td>
              <td style={td}>
                <input
                  type="number" step="any" style={{ width: 90 }}
                  value={drafts[row.key] ?? ''}
                  onChange={(e) => setDrafts((d) => ({ ...d, [row.key]: e.target.value }))}
                />
              </td>
              <td style={td}>
                {row.is_override
                  ? <span style={{ color: '#a35b00' }}>overridden (live)</span>
                  : <span style={{ color: '#888' }}>default, not calibrated</span>}
              </td>
              <td style={td}>
                <button type="button" disabled={savingKey === row.key} onClick={() => save(row.key)}>
                  Save
                </button>{' '}
                <button
                  type="button" disabled={savingKey === row.key || !row.is_override}
                  onClick={() => reset(row.key)} title="Revert to the module default"
                >
                  Reset
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
