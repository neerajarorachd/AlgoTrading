import { useState } from 'react'
import { addSymbol } from '../api/client.js'

export default function SymbolRegisterForm({ onRegistered }) {
  const [symbol, setSymbol] = useState('')
  const [exchange, setExchange] = useState('NSE')
  const [segment, setSegment] = useState('EQUITY')
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    if (!symbol.trim()) return
    setSubmitting(true)
    setError(null)
    try {
      await addSymbol(symbol.trim().toUpperCase(), exchange, segment)
      setSymbol('')
      onRegistered()
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'center' }}>
      <input
        placeholder="Symbol (e.g. RELIANCE)"
        value={symbol}
        onChange={(e) => setSymbol(e.target.value)}
      />
      <select value={exchange} onChange={(e) => setExchange(e.target.value)}>
        <option value="NSE">NSE</option>
        <option value="BSE">BSE</option>
      </select>
      <select value={segment} onChange={(e) => setSegment(e.target.value)}>
        <option value="EQUITY">EQUITY</option>
        <option value="INDEX">INDEX</option>
      </select>
      <button type="submit" disabled={submitting}>Add</button>
      {error && <span style={{ color: 'crimson' }}>{error}</span>}
    </form>
  )
}
