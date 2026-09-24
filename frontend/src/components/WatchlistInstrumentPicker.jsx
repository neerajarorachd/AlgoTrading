import { useMemo, useState } from 'react'

// Typeahead over already-registered instruments (listSymbols()), not a new
// broker search — a watchlist can only hold instruments that already exist
// as SubscribedSymbol rows (register new ones via Market Watch first). The
// list is small, so a client-side filter on every keystroke is enough; no
// debounce/server round-trip needed here, unlike SymbolRegisterForm.jsx's
// broker-backed search.
export default function WatchlistInstrumentPicker({ symbols, excludeIds, onPick }) {
  const [query, setQuery] = useState('')
  const [showSuggestions, setShowSuggestions] = useState(false)
  const [highlightedIndex, setHighlightedIndex] = useState(-1)

  const suggestions = useMemo(() => {
    const excluded = new Set(excludeIds)
    const q = query.trim().toLowerCase()
    return symbols
      .filter((s) => !excluded.has(s.id))
      .filter((s) => !q || s.symbol.toLowerCase().includes(q))
      .slice(0, 10)
  }, [symbols, excludeIds, query])

  function pick(symbol) {
    onPick(symbol.id)
    setQuery('')
    setShowSuggestions(false)
    setHighlightedIndex(-1)
  }

  function handleKeyDown(e) {
    if (!showSuggestions || suggestions.length === 0) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHighlightedIndex((i) => Math.min(i + 1, suggestions.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlightedIndex((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter' && highlightedIndex >= 0) {
      e.preventDefault()
      pick(suggestions[highlightedIndex])
    } else if (e.key === 'Escape') {
      setShowSuggestions(false)
    }
  }

  return (
    <div style={{ position: 'relative', display: 'inline-block' }}>
      <input
        placeholder="Add instrument…"
        value={query}
        onChange={(e) => { setQuery(e.target.value); setShowSuggestions(true); setHighlightedIndex(-1) }}
        onFocus={() => setShowSuggestions(true)}
        onBlur={() => setTimeout(() => setShowSuggestions(false), 150)}
        onKeyDown={handleKeyDown}
      />
      {showSuggestions && suggestions.length > 0 && (
        <ul style={{
          position: 'absolute', zIndex: 10, top: '100%', left: 0, minWidth: 180,
          margin: 0, padding: 4, listStyle: 'none', background: '#fff',
          border: '1px solid #ccc', borderRadius: 4, boxShadow: '0 2px 6px rgba(0,0,0,0.15)',
        }}>
          {suggestions.map((s, i) => (
            <li
              key={s.id}
              onMouseDown={() => pick(s)}
              style={{
                padding: '4px 8px', cursor: 'pointer',
                background: i === highlightedIndex ? '#dfe8ff' : undefined,
              }}
            >
              {s.symbol} <span style={{ color: '#888' }}>({s.exchange})</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
