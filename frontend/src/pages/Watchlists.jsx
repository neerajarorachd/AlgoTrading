import { useCallback, useEffect, useState } from 'react'
import {
  addWatchlistInstrument, backfillHistoricalData, createWatchlist, deleteWatchlist,
  getHistoricalDataCoverage, getWatchlist, listSymbols, listWatchlists,
  removeWatchlistInstrument, subscribeWatchlist, updateWatchlist,
} from '../api/client.js'
import WatchlistInstrumentPicker from '../components/WatchlistInstrumentPicker.jsx'
import WatchlistTable from '../components/WatchlistTable.jsx'

const EMPTY_FORM = { name: '', description: '' }
const TIMEFRAMES = ['1min', '3min', '5min', '1day']

function todayIso() {
  return new Date().toISOString().slice(0, 10)
}

export default function Watchlists() {
  const [watchlists, setWatchlists] = useState([])
  const [symbols, setSymbols] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [detail, setDetail] = useState(null) // full watchlist + members, once selected
  const [form, setForm] = useState(EMPTY_FORM)
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)
  const [subscribing, setSubscribing] = useState(false)
  const [subscribeResult, setSubscribeResult] = useState(null)

  const refreshList = useCallback(() => {
    listWatchlists().then(setWatchlists).catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    refreshList()
    listSymbols().then(setSymbols).catch((e) => setError(e.message))
  }, [refreshList])

  function startNew() {
    setSelectedId(null)
    setDetail(null)
    setForm(EMPTY_FORM)
    setError(null)
  }

  function selectWatchlist(id) {
    setError(null)
    getWatchlist(id)
      .then((w) => {
        setSelectedId(w.id)
        setDetail(w)
        setForm({ name: w.name, description: w.description || '' })
      })
      .catch((e) => setError(e.message))
  }

  function refreshDetail(id) {
    getWatchlist(id).then(setDetail).catch((e) => setError(e.message))
  }

  function handleSave(e) {
    e.preventDefault()
    if (!form.name.trim()) {
      setError('Name is required')
      return
    }
    setSaving(true)
    setError(null)
    const req = selectedId ? updateWatchlist(selectedId, form) : createWatchlist(form)
    req
      .then((saved) => {
        refreshList()
        setSelectedId(saved.id)
        refreshDetail(saved.id)
      })
      .catch((e) => setError(e.message))
      .finally(() => setSaving(false))
  }

  function handleDelete(id) {
    deleteWatchlist(id)
      .then(() => {
        refreshList()
        if (id === selectedId) startNew()
      })
      .catch((e) => setError(e.message))
  }

  function handleAddInstrument(instrumentId) {
    addWatchlistInstrument(selectedId, instrumentId)
      .then(() => { refreshList(); refreshDetail(selectedId) })
      .catch((e) => setError(e.message))
  }

  function handleRemoveInstrument(instrumentId) {
    removeWatchlistInstrument(selectedId, instrumentId)
      .then(() => { refreshList(); refreshDetail(selectedId) })
      .catch((e) => setError(e.message))
  }

  function handleSubscribe() {
    setSubscribing(true)
    setError(null)
    setSubscribeResult(null)
    subscribeWatchlist(selectedId)
      .then((result) => {
        setSubscribeResult(result)
        refreshDetail(selectedId)
      })
      .catch((e) => setError(e.message))
      .finally(() => setSubscribing(false))
  }

  return (
    <div style={{ padding: 16, maxWidth: 1000, margin: '0 auto' }}>
      <h2>Watchlists</h2>

      <WatchlistTable
        watchlists={watchlists} selectedId={selectedId}
        onSelect={selectWatchlist} onDelete={handleDelete}
      />

      <div style={{ marginTop: 20, padding: 16, border: '1px solid #ccc', borderRadius: 6 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ margin: 0 }}>{selectedId ? `Edit watchlist #${selectedId}` : 'New watchlist'}</h3>
          {selectedId && <button type="button" onClick={startNew}>+ New watchlist</button>}
        </div>

        <form onSubmit={handleSave} style={{ marginTop: 12 }}>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
            <label>
              Name<br />
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
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

          {error && <div style={{ color: 'crimson', marginBottom: 8 }}>{error}</div>}

          <button type="submit" disabled={saving}>
            {saving ? 'Saving…' : selectedId ? 'Save changes' : 'Create watchlist'}
          </button>
        </form>

        {selectedId && detail && (
          <div style={{ marginTop: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h4 style={{ margin: 0 }}>Members</h4>
              {detail.members.length > 0 && (
                <button type="button" onClick={handleSubscribe} disabled={subscribing}>
                  {subscribing ? 'Registering…' : 'Register for Market Watch'}
                </button>
              )}
            </div>
            <ul style={{ paddingLeft: 18 }}>
              {detail.members.map((m) => (
                <li key={m.instrument_id}>
                  {m.symbol} ({m.exchange})
                  {' '}
                  <button onClick={() => handleRemoveInstrument(m.instrument_id)}>Remove</button>
                </li>
              ))}
              {detail.members.length === 0 && <li style={{ color: '#888', listStyle: 'none' }}>No members yet</li>}
            </ul>
            {subscribeResult && (
              <div style={{ fontSize: 14, color: '#444', marginBottom: 8 }}>
                {subscribeResult.subscribed.length > 0 && (
                  <div>Registered: {subscribeResult.subscribed.join(', ')}</div>
                )}
                {subscribeResult.already_active.length > 0 && (
                  <div>Already live: {subscribeResult.already_active.join(', ')}</div>
                )}
                {subscribeResult.failed.length > 0 && (
                  <div style={{ color: 'crimson' }}>
                    Failed: {subscribeResult.failed.map((f) => `${f.symbol} (${f.error})`).join(', ')}
                  </div>
                )}
              </div>
            )}
            <WatchlistInstrumentPicker
              symbols={symbols}
              excludeIds={detail.members.map((m) => m.instrument_id)}
              onPick={handleAddInstrument}
            />
          </div>
        )}
      </div>

      {selectedId && detail && detail.members.length > 0 && (
        // keyed on the watchlist id so switching watchlists resets the
        // panel's own instrument/timeframe selection instead of carrying
        // over a stale instrument_id from the previous one
        <HistoricalDataPanel key={detail.id} members={detail.members} />
      )}
    </div>
  )
}

// Manual verification tool for backend/historical_data_service.py's
// fetch-if-stale gate — the real caller (backtesting) doesn't exist yet, so
// this proves the logic works live against the real broker/VM. Scoped to
// the selected watchlist's own members, the same instruments backtesting
// will eventually need fresh data for.
function HistoricalDataPanel({ members }) {
  const [instrumentId, setInstrumentId] = useState(members[0].instrument_id)
  const [timeframe, setTimeframe] = useState('1day')
  const [startDate, setStartDate] = useState(todayIso())
  const [endDate, setEndDate] = useState(todayIso())
  const [coverage, setCoverage] = useState(null)
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    setCoverage(null)
    setResult(null)
    getHistoricalDataCoverage(instrumentId, timeframe)
      .then((c) => setCoverage(c.last_ts))
      .catch((e) => setError(e.message))
  }, [instrumentId, timeframe])

  function runBackfill() {
    setLoading(true)
    setError(null)
    setResult(null)
    backfillHistoricalData(instrumentId, timeframe, startDate, endDate)
      .then((r) => {
        setResult(r)
        return getHistoricalDataCoverage(instrumentId, timeframe)
      })
      .then((c) => setCoverage(c.last_ts))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }

  return (
    <div style={{ marginTop: 20, padding: 16, border: '1px solid #ccc', borderRadius: 6 }}>
      <h3 style={{ marginTop: 0 }}>Historical Data</h3>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <label>
          Instrument<br />
          <select value={instrumentId} onChange={(e) => setInstrumentId(Number(e.target.value))}>
            {members.map((m) => (
              <option key={m.instrument_id} value={m.instrument_id}>{m.symbol}</option>
            ))}
          </select>
        </label>
        <label>
          Timeframe<br />
          <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
            {TIMEFRAMES.map((tf) => <option key={tf} value={tf}>{tf}</option>)}
          </select>
        </label>
        <label>
          From<br />
          <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
        </label>
        <label>
          To<br />
          <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
        </label>
        <button type="button" onClick={runBackfill} disabled={loading}>
          {loading ? 'Backfilling…' : 'Backfill'}
        </button>
      </div>

      <div style={{ marginTop: 10, fontSize: 14, color: '#444' }}>
        Last available: {coverage || 'no data yet'}
      </div>
      {result && (
        <div style={{ marginTop: 6, fontSize: 14 }}>
          {result.already_covered
            ? 'Already up to date — nothing fetched.'
            : `Fetched ${result.fetched} candle(s), covering ${result.from} → ${result.to}.`}
        </div>
      )}
      {error && <div style={{ color: 'crimson', marginTop: 6 }}>{error}</div>}
    </div>
  )
}
