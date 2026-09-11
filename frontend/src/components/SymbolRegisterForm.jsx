import { useEffect, useRef, useState } from 'react'
import { addSymbol, searchInstruments } from '../api/client.js'

const SEARCH_DEBOUNCE_MS = 150
const MIN_QUERY_LENGTH = 2

export default function SymbolRegisterForm({ onRegistered }) {
  const [symbol, setSymbol] = useState('')
  const [exchange, setExchange] = useState('NSE')
  const [segment, setSegment] = useState('EQUITY')
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const [suggestions, setSuggestions] = useState([])
  const [showSuggestions, setShowSuggestions] = useState(false)
  const [highlightedIndex, setHighlightedIndex] = useState(-1)
  const debounceRef = useRef(null)
  const listRef = useRef(null)

  useEffect(() => {
    clearTimeout(debounceRef.current)
    const query = symbol.trim()
    if (query.length < MIN_QUERY_LENGTH) {
      setSuggestions([])
      return
    }
    debounceRef.current = setTimeout(() => {
      searchInstruments(query, exchange, segment)
        .then((results) => {
          setSuggestions(results)
          setHighlightedIndex(results.length ? 0 : -1)
        })
        .catch(() => setSuggestions([]))
    }, SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(debounceRef.current)
  }, [symbol, exchange, segment])

  useEffect(() => {
    if (highlightedIndex < 0 || !listRef.current) return
    const item = listRef.current.children[highlightedIndex]
    item?.scrollIntoView({ block: 'nearest' })
  }, [highlightedIndex])

  function selectSuggestion(match) {
    setSymbol(match.symbol) // the real trading symbol, e.g. NATIONALUM — not whatever alias was typed
    setSuggestions([])
    setShowSuggestions(false)
    setHighlightedIndex(-1)
  }

  function handleKeyDown(e) {
    if (!showSuggestions || suggestions.length === 0) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHighlightedIndex((i) => (i + 1) % suggestions.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlightedIndex((i) => (i - 1 + suggestions.length) % suggestions.length)
    } else if (e.key === 'Enter' && highlightedIndex >= 0) {
      e.preventDefault()
      selectSuggestion(suggestions[highlightedIndex])
    } else if (e.key === 'Escape') {
      setShowSuggestions(false)
      setHighlightedIndex(-1)
    }
  }

  async function handleSubmit(e) {
    e.preventDefault()
    if (!symbol.trim()) return
    setSubmitting(true)
    setError(null)
    try {
      await addSymbol(symbol.trim().toUpperCase(), exchange, segment)
      setSymbol('')
      setSuggestions([])
      onRegistered()
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'flex-start' }}>
      <div style={{ position: 'relative' }}>
        <input
          placeholder="Symbol or company name (e.g. NALCO)"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          onFocus={() => setShowSuggestions(true)}
          onBlur={() => setTimeout(() => setShowSuggestions(false), 150)}
          onKeyDown={handleKeyDown}
        />
        {showSuggestions && suggestions.length > 0 && (
          <ul
            ref={listRef}
            style={{
              position: 'absolute', top: '100%', left: 0, zIndex: 10, margin: 0, padding: 4,
              listStyle: 'none', background: 'white', border: '1px solid #ccc', width: 320,
              maxHeight: 220, overflowY: 'auto',
            }}
          >
            {suggestions.map((match, index) => (
              <li
                key={match.security_id}
                onMouseDown={() => selectSuggestion(match)}
                onMouseEnter={() => setHighlightedIndex(index)}
                style={{
                  padding: '4px 6px', cursor: 'pointer',
                  background: index === highlightedIndex ? '#eef' : undefined,
                }}
              >
                <strong>{match.symbol}</strong>
                {match.custom_symbol && match.custom_symbol !== match.symbol && (
                  <span style={{ color: '#888' }}> ({match.custom_symbol})</span>
                )}
                {match.company_name && (
                  <div style={{ fontSize: 12, color: '#888' }}>{match.company_name}</div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
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
