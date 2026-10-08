import { useEffect, useRef, useState } from 'react'
import { addSymbol, searchInstruments } from '../api/client.js'
import Button from './kit/Button.jsx'

const SEARCH_DEBOUNCE_MS = 150
const MIN_QUERY_LENGTH = 2

export default function SymbolRegisterForm({ onRegistered, children }) {
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
    <form onSubmit={handleSubmit} style={{ display: 'flex', flexWrap: 'wrap', gap: 8, margin: 0, alignItems: 'center' }}>
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
              position: 'absolute', top: '100%', left: 0, zIndex: 10, margin: '4px 0 0', padding: 4,
              listStyle: 'none', background: 'var(--surface)', border: '1px solid var(--border)',
              borderRadius: 'var(--radius-md)', boxShadow: 'var(--shadow-md)', width: 320,
              maxHeight: 220, overflowY: 'auto',
            }}
          >
            {suggestions.map((match, index) => (
              <li
                key={match.security_id}
                onMouseDown={() => selectSuggestion(match)}
                onMouseEnter={() => setHighlightedIndex(index)}
                style={{
                  padding: '6px 8px', cursor: 'pointer', borderRadius: 'var(--radius-sm)',
                  background: index === highlightedIndex ? 'var(--accent-soft)' : undefined,
                }}
              >
                <strong style={{ color: 'var(--text)' }}>{match.symbol}</strong>
                {match.custom_symbol && match.custom_symbol !== match.symbol && (
                  <span style={{ color: 'var(--text-faint)' }}> ({match.custom_symbol})</span>
                )}
                {match.company_name && (
                  <div style={{ fontSize: 12, color: 'var(--text-faint)' }}>{match.company_name}</div>
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
      <Button type="submit" disabled={submitting}>Add</Button>
      {error && <span style={{ color: 'var(--critical)', fontSize: 13 }}>{error}</span>}
      {children}
    </form>
  )
}
