async function request(path, options) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.error || `Request failed: ${res.status}`)
  }
  if (res.status === 204) return null
  return res.json()
}

export const listSymbols = () => request('/api/symbols')

export const addSymbol = (symbol, exchange, segment) =>
  request('/api/symbols', { method: 'POST', body: JSON.stringify({ symbol, exchange, segment }) })

export const removeSymbol = (id) => request(`/api/symbols/${id}`, { method: 'DELETE' })

export const getCandles = (symbol, exchangeSegment, timeframe, from, to) => {
  const params = new URLSearchParams({ symbol, exchange_segment: exchangeSegment, timeframe })
  if (from) params.set('from', from)
  if (to) params.set('to', to)
  return request(`/api/candles?${params.toString()}`)
}
