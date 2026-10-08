import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getWatchScores, listStrategies, listStrategyElements, listSymbols, removeSymbol } from '../api/client.js'
import { getSocket, roomFor, subscribeRooms, unsubscribeRooms } from '../api/ws.js'
import SymbolRegisterForm from '../components/SymbolRegisterForm.jsx'
import SymbolTable from '../components/SymbolTable.jsx'
import ScoreGrid from '../components/ScoreGrid.jsx'
import DepthPanel from '../components/DepthPanel.jsx'
import CandleChart from '../components/CandleChart.jsx'
import Button from '../components/kit/Button.jsx'

// Bull-bucket sorts by bull_score desc, bear-bucket by bear_score desc,
// choppy by total activity desc -- "quiet" has nothing to sort by (every
// row is 0-0), so it's left in the table's own existing order.
function scoreSortKey(bucket, row) {
  if (bucket === 'choppy') return row.bull_score + row.bear_score
  if (bucket?.endsWith('bear')) return row.bear_score
  return row.bull_score
}

const MAX_OPEN_CHARTS = 4
const OPEN_SYMBOL_IDS_KEY = 'marketWatch.openSymbolIds'

function loadStoredOpenIds() {
  try {
    const raw = localStorage.getItem(OPEN_SYMBOL_IDS_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

// Frozen on purpose -- this is the pre-redesign Market Watch page, kept at
// its own route ('/market-watch-classic') as a side-by-side reference
// rather than deleted, per the standing "never delete pages" rule. The new
// design lives in MarketWatch.jsx; nothing here should change going
// forward unless this specific page is the one being asked for.
export default function MarketWatchClassic() {
  const [symbols, setSymbols] = useState([])
  // "what to watch" picker's data source -- global catalogs (every
  // formation/indicator/strategy), fetched once here rather than per row.
  const [elements, setElements] = useState([])
  const [strategies, setStrategies] = useState([])
  // Colored-cell grid state -- fetched once on mount/refresh (not yet
  // live-pushed, same "snapshot on load" convention as the Events popover).
  const [scores, setScores] = useState([])
  const [bucketColors, setBucketColors] = useState({})
  const [selectedBucket, setSelectedBucket] = useState(null)
  const [liveTicks, setLiveTicks] = useState({}) // symbol -> tick payload
  const [liveDepth, setLiveDepth] = useState({}) // symbol -> depth payload
  const [backfillStatus, setBackfillStatus] = useState({}) // symbol -> {status, message}
  const [openSymbols, setOpenSymbols] = useState([]) // up to MAX_OPEN_CHARTS rows
  const [focusedIndex, setFocusedIndex] = useState(-1)
  const [listCollapsed, setListCollapsed] = useState(false)
  const [dragIndex, setDragIndex] = useState(null)
  const [multiOpenMode, setMultiOpenMode] = useState(true)
  const [depthCollapsedById, setDepthCollapsedById] = useState({}) // instrument id -> collapsed
  // Depth+timeframe button group's own rendered width, reported by
  // CandleChart -- shared across cards since the buttons' content (and so
  // their width) is identical everywhere; used to size the depth panel so
  // its left edge lines up exactly under the Depth button (both right-
  // aligned to the same card edge, so matching widths align their edges).
  const [depthPanelWidth, setDepthPanelWidth] = useState(220)
  const [fullscreenId, setFullscreenId] = useState(null)
  const [fullscreenOrientation, setFullscreenOrientation] = useState('horizontal')
  const joinedRooms = useRef(new Set())
  const restoredOpenSymbols = useRef(false)

  const refresh = useCallback(() => {
    listSymbols().then(setSymbols).catch(() => {})
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  useEffect(() => {
    listStrategyElements().then(setElements).catch(() => {})
    listStrategies().then(setStrategies).catch(() => {})
  }, [])

  useEffect(() => {
    getWatchScores().then((res) => { setScores(res.scores); setBucketColors(res.bucket_colors) }).catch(() => {})
  }, [symbols])

  const scoreByInstrument = useMemo(
    () => Object.fromEntries(scores.map((r) => [r.instrument_id, r])),
    [scores],
  )

  const visibleSymbols = useMemo(() => {
    if (!selectedBucket) return symbols
    return symbols
      .filter((s) => scoreByInstrument[s.id]?.bucket === selectedBucket)
      .sort((a, b) => scoreSortKey(selectedBucket, scoreByInstrument[b.id]) - scoreSortKey(selectedBucket, scoreByInstrument[a.id]))
  }, [symbols, selectedBucket, scoreByInstrument])

  // keep WS room membership in sync with the registered-symbol list, without
  // restarting anything — each change is an incremental join/leave
  useEffect(() => {
    const desired = new Set(symbols.map((s) => roomFor(s.exchange, s.symbol)))
    const toJoin = [...desired].filter((room) => !joinedRooms.current.has(room))
    const toLeave = [...joinedRooms.current].filter((room) => !desired.has(room))
    subscribeRooms(toJoin)
    unsubscribeRooms(toLeave)
    joinedRooms.current = desired

    // drop any open charts / focus for symbols that got removed elsewhere
    const stillValidIds = new Set(symbols.map((s) => s.id))
    setOpenSymbols((prev) => prev.filter((s) => stillValidIds.has(s.id)))
    setFocusedIndex((prev) => (prev >= symbols.length ? symbols.length - 1 : prev))

    // restore the chart selection from a previous page load, once, after the
    // real symbol rows are available to match stored ids against
    if (!restoredOpenSymbols.current && symbols.length > 0) {
      restoredOpenSymbols.current = true
      const storedIds = loadStoredOpenIds()
      if (storedIds.length > 0) {
        const byId = new Map(symbols.map((s) => [s.id, s]))
        const restored = storedIds.map((id) => byId.get(id)).filter(Boolean).slice(0, MAX_OPEN_CHARTS)
        if (restored.length > 0) setOpenSymbols(restored)
      }
    }
  }, [symbols])

  // drop fullscreen if the card it points at is no longer open (closed,
  // removed, or swapped out by a single-graph-mode stock change)
  useEffect(() => {
    if (fullscreenId !== null && !openSymbols.some((s) => s.id === fullscreenId)) {
      setFullscreenId(null)
    }
  }, [openSymbols, fullscreenId])

  // persist the chart selection so it survives a page refresh — gated on the
  // restore attempt above having already run, otherwise the empty initial
  // state (before symbols load) would immediately overwrite last session's
  // saved selection with []
  useEffect(() => {
    if (!restoredOpenSymbols.current) return
    try {
      localStorage.setItem(OPEN_SYMBOL_IDS_KEY, JSON.stringify(openSymbols.map((s) => s.id)))
    } catch {
      // ignore — e.g. private browsing with storage disabled
    }
  }, [openSymbols])

  // server-side room membership is per-connection — a dropped/reconnected
  // socket (e.g. the backend restarting) loses it silently, and the
  // symbols-sync effect above only (re)subscribes on a *symbol list* change,
  // not on reconnect, so ticks/depth would otherwise just stop arriving until
  // a full page refresh. Re-join everything currently desired on every
  // (re)connect instead — join_room is idempotent, so this is a safe no-op
  // on the very first connect too (joinedRooms is still empty then).
  useEffect(() => {
    const socket = getSocket()
    function handleConnect() {
      subscribeRooms([...joinedRooms.current])
    }
    socket.on('connect', handleConnect)
    return () => socket.off('connect', handleConnect)
  }, [])

  useEffect(() => {
    const socket = getSocket()
    const onTick = (payload) => setLiveTicks((prev) => ({ ...prev, [payload.symbol]: payload }))
    const onDepth = (payload) => setLiveDepth((prev) => ({ ...prev, [payload.symbol]: payload }))
    const onBackfillStatus = (payload) =>
      setBackfillStatus((prev) => ({ ...prev, [payload.symbol]: payload }))
    socket.on('tick', onTick)
    socket.on('depth', onDepth)
    socket.on('backfill_status', onBackfillStatus)
    return () => {
      socket.off('tick', onTick)
      socket.off('depth', onDepth)
      socket.off('backfill_status', onBackfillStatus)
    }
  }, [])

  // clear a "done" backfill status a few seconds after it lands, so the table
  // doesn't permanently show a stale "Backfilled N candles" note
  useEffect(() => {
    const timers = Object.entries(backfillStatus)
      .filter(([, status]) => status.status === 'done')
      .map(([symbol]) =>
        setTimeout(() => {
          setBackfillStatus((prev) => {
            const next = { ...prev }
            delete next[symbol]
            return next
          })
        }, 4000),
      )
    return () => timers.forEach(clearTimeout)
  }, [backfillStatus])

  function handleToggleOpen(row) {
    if (!multiOpenMode) {
      // single-graph mode: clicking any row always switches the one open chart.
      // Carry the outgoing stock's depth-open/closed state over to the incoming
      // one, so switching stocks doesn't reset Depth back to collapsed.
      const previousId = openSymbols[0]?.id
      if (previousId !== undefined && previousId !== row.id) {
        setDepthCollapsedById((prev) => ({ ...prev, [row.id]: prev[previousId] ?? true }))
      }
      setOpenSymbols((prev) => (prev.length === 1 && prev[0].id === row.id ? prev : [row]))
      return
    }
    setOpenSymbols((prev) => {
      const isOpen = prev.some((s) => s.id === row.id)
      if (isOpen) return prev.filter((s) => s.id !== row.id)
      if (prev.length >= MAX_OPEN_CHARTS) return prev // silently ignore — row is shown as at-capacity
      return [...prev, row]
    })
  }

  function handleMultiOpenModeChange(e) {
    const checked = e.target.checked
    setMultiOpenMode(checked)
    if (!checked) {
      // collapsing down to single-graph mode: keep only the first open chart
      setOpenSymbols((prev) => (prev.length > 1 ? [prev[0]] : prev))
    }
  }

  // Rendered next to the Add button (inside SymbolRegisterForm) when the list
  // is visible, or as its own small row when the list is hidden — either way,
  // only shown once there's actually a chart open to apply it to.
  const multiOpenCheckbox = openSymbols.length > 0 && (
    <label style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--text-dim)' }}>
      <input type="checkbox" checked={multiOpenMode} onChange={handleMultiOpenModeChange} />
      Multiple charts
    </label>
  )

  async function handleRemove(id) {
    await removeSymbol(id)
    setOpenSymbols((prev) => prev.filter((s) => s.id !== id))
    refresh()
  }

  function handleDrop(dropIndex) {
    setOpenSymbols((prev) => {
      if (dragIndex === null || dragIndex === dropIndex) return prev
      const next = [...prev]
      const [moved] = next.splice(dragIndex, 1)
      next.splice(dropIndex, 0, moved)
      return next
    })
    setDragIndex(null)
  }

  const fullscreenInstrument = openSymbols.find((s) => s.id === fullscreenId) ?? null
  const fullscreenDepthOpen = fullscreenInstrument ? !(depthCollapsedById[fullscreenInstrument.id] ?? true) : false

  if (fullscreenInstrument) {
    return (
      <div style={{
        position: 'fixed', inset: 0, background: 'var(--bg)', zIndex: 1000,
        padding: 16, display: 'flex', flexDirection: 'column', boxSizing: 'border-box',
        color: 'var(--text)',
      }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
          <strong style={{ fontSize: 15 }}>{fullscreenInstrument.symbol}</strong>
          <div style={{ display: 'flex', gap: 8 }}>
            <Button
              variant="secondary"
              onClick={() => setFullscreenOrientation((o) => (o === 'horizontal' ? 'vertical' : 'horizontal'))}
            >
              {fullscreenOrientation === 'horizontal' ? 'Vertical layout' : 'Horizontal layout'}
            </Button>
            <Button variant="secondary" onClick={() => setFullscreenId(null)}>← Back</Button>
          </div>
        </div>
        <div style={{
          flex: '1 1 auto', minHeight: 0, display: 'flex',
          flexDirection: fullscreenOrientation === 'horizontal' ? 'row' : 'column',
        }}
        >
          <div style={{ flex: '1 1 auto', minWidth: 0, minHeight: 0 }}>
            <CandleChart
              instrument={fullscreenInstrument} fillHeight
              depthOpen={fullscreenDepthOpen}
              onToggleDepth={() => setDepthCollapsedById((prev) => ({ ...prev, [fullscreenInstrument.id]: !(prev[fullscreenInstrument.id] ?? true) }))}
              onControlsWidthChange={setDepthPanelWidth}
            />
          </div>
          {fullscreenDepthOpen && (
            <div style={{ flex: `0 0 ${depthPanelWidth}px`, minWidth: 0, paddingLeft: 14 }}>
              <DepthPanel depth={liveDepth[fullscreenInstrument.symbol]} />
            </div>
          )}
        </div>
      </div>
    )
  }

  return (
    <div style={{ paddingBlock: 32, paddingInline: 'var(--space-5)', maxWidth: 1200, margin: '0 auto' }}>
      <style>{`
        .chart-grid {
          display: flex;
          flex-wrap: wrap;
          gap: 24px;
        }
        .chart-card {
          flex: 1 1 340px;
          min-width: 0;
        }
        @media (max-width: 700px) {
          .chart-card {
            flex-basis: 100%;
          }
        }
      `}</style>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12, marginBottom: 'var(--space-4)' }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 700, color: 'var(--text)' }}>Market Watch (Classic)</h1>
        <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
          <SymbolRegisterForm onRegistered={refresh}>
            {multiOpenCheckbox}
          </SymbolRegisterForm>
          <Button variant="secondary" onClick={() => setListCollapsed((c) => !c)}>
            {listCollapsed ? 'Show list' : 'Hide list'}
          </Button>
        </div>
      </div>
      {!listCollapsed && scores.length > 0 && (
        <>
          <ScoreGrid
            scores={scores} bucketColors={bucketColors}
            selectedBucket={selectedBucket} onSelectBucket={setSelectedBucket}
          />
          {selectedBucket && (
            <div style={{ marginBottom: 'var(--space-2)', fontSize: 13, color: 'var(--text-dim)', display: 'flex', alignItems: 'center', gap: 8 }}>
              Showing <b style={{ color: 'var(--text)' }}>{visibleSymbols.length}</b> of {symbols.length}, sorted by {selectedBucket.replace('_', ' ')}
              <Button variant="secondary" onClick={() => setSelectedBucket(null)}>Clear filter</Button>
            </div>
          )}
        </>
      )}
      {!listCollapsed && (
        <SymbolTable
          symbols={visibleSymbols}
          liveTicks={liveTicks}
          backfillStatus={backfillStatus}
          openSymbols={openSymbols}
          onToggleOpen={handleToggleOpen}
          onRemove={handleRemove}
          focusedIndex={focusedIndex}
          onFocusedIndexChange={setFocusedIndex}
          elements={elements}
          strategies={strategies}
          scoreByInstrument={scoreByInstrument}
        />
      )}
      {openSymbols.length > 0 && (
        <div className="chart-grid" style={{ marginTop: 'var(--space-5)' }}>
          {openSymbols.map((instrument, index) => (
            <div
              key={instrument.id}
              className="chart-card"
              draggable
              onDragStart={() => setDragIndex(index)}
              onDragOver={(e) => e.preventDefault()}
              onDrop={() => handleDrop(index)}
              onDragEnd={() => setDragIndex(null)}
              style={{
                background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)',
                boxShadow: 'var(--shadow-sm)', padding: 'var(--space-3)', minWidth: 0,
                opacity: dragIndex === index ? 0.5 : 1,
                // first open chart always gets its own full-width row; the
                // rest wrap 3-per-row below it (flex-basis 100% leaves no
                // room for a sibling beside it, forcing the wrap)
                flexBasis: index === 0 ? '100%' : undefined,
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 }}>
                <strong style={{ cursor: 'grab', color: 'var(--text)' }} title="Drag to reorder">⠿ {instrument.symbol}</strong>
                <div style={{ display: 'flex', gap: 4 }}>
                  <button
                    onClick={() => setFullscreenId(instrument.id)} title="Fullscreen"
                    style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', fontSize: 14 }}
                  >⛶</button>
                  <button
                    onClick={() => handleToggleOpen(instrument)} title="Close chart"
                    style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', fontSize: 16 }}
                  >×</button>
                </div>
              </div>
              <div style={{ display: 'flex', minWidth: 0 }}>
                <div style={{ flex: '1 1 auto', minWidth: 0 }}>
                  <CandleChart
                    instrument={instrument}
                    depthOpen={!(depthCollapsedById[instrument.id] ?? true)}
                    onToggleDepth={() => setDepthCollapsedById((prev) => ({ ...prev, [instrument.id]: !(prev[instrument.id] ?? true) }))}
                    onControlsWidthChange={setDepthPanelWidth}
                  />
                </div>
                {!(depthCollapsedById[instrument.id] ?? true) && (
                  <div style={{ flex: `0 0 ${depthPanelWidth}px`, minWidth: 0, paddingLeft: 14 }}>
                    <DepthPanel depth={liveDepth[instrument.symbol]} />
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
