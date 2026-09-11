import { useCallback, useEffect, useRef, useState } from 'react'
import { listSymbols, removeSymbol } from '../api/client.js'
import { getSocket, roomFor, subscribeRooms, unsubscribeRooms } from '../api/ws.js'
import SymbolRegisterForm from '../components/SymbolRegisterForm.jsx'
import SymbolTable from '../components/SymbolTable.jsx'
import DepthPanel from '../components/DepthPanel.jsx'
import CandleChart from '../components/CandleChart.jsx'
import SidePanel from '../components/SidePanel.jsx'

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

export default function MarketWatch() {
  const [symbols, setSymbols] = useState([])
  const [liveTicks, setLiveTicks] = useState({}) // symbol -> tick payload
  const [liveDepth, setLiveDepth] = useState({}) // symbol -> depth payload
  const [backfillStatus, setBackfillStatus] = useState({}) // symbol -> {status, message}
  const [openSymbols, setOpenSymbols] = useState([]) // up to MAX_OPEN_CHARTS rows
  const [focusedIndex, setFocusedIndex] = useState(-1)
  const [listCollapsed, setListCollapsed] = useState(false)
  const [dragIndex, setDragIndex] = useState(null)
  const [multiOpenMode, setMultiOpenMode] = useState(true)
  const [depthCollapsedById, setDepthCollapsedById] = useState({}) // instrument id -> collapsed
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

  if (fullscreenInstrument) {
    return (
      <div style={{
        position: 'fixed', inset: 0, background: 'white', zIndex: 1000,
        padding: 16, display: 'flex', flexDirection: 'column', boxSizing: 'border-box',
        fontFamily: 'sans-serif',
      }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
          <strong>{fullscreenInstrument.symbol}</strong>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              onClick={() => setFullscreenOrientation((o) => (o === 'horizontal' ? 'vertical' : 'horizontal'))}
            >
              {fullscreenOrientation === 'horizontal' ? 'Vertical layout' : 'Horizontal layout'}
            </button>
            <button onClick={() => setFullscreenId(null)}>← Back</button>
          </div>
        </div>
        <div style={{
          flex: '1 1 auto', minHeight: 0, display: 'flex',
          flexDirection: fullscreenOrientation === 'horizontal' ? 'row' : 'column',
        }}
        >
          <div style={{ flex: '1 1 auto', minWidth: 0, minHeight: 0 }}>
            <CandleChart instrument={fullscreenInstrument} fillHeight />
          </div>
          <SidePanel
            collapsed={depthCollapsedById[fullscreenInstrument.id] ?? true}
            onToggleCollapsed={(v) => setDepthCollapsedById((prev) => ({ ...prev, [fullscreenInstrument.id]: v }))}
            panels={[
              { id: 'depth', label: 'Depth', content: <DepthPanel depth={liveDepth[fullscreenInstrument.symbol]} /> },
            ]}
          />
        </div>
      </div>
    )
  }

  return (
    <div style={{ fontFamily: 'sans-serif', padding: 16, maxWidth: 1200, margin: '0 auto' }}>
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
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <h1>Market Watch</h1>
        <button onClick={() => setListCollapsed((c) => !c)}>
          {listCollapsed ? 'Show list' : 'Hide list'}
        </button>
      </div>
      {!listCollapsed && (
        <>
          <SymbolRegisterForm onRegistered={refresh} />
          <SymbolTable
            symbols={symbols}
            liveTicks={liveTicks}
            backfillStatus={backfillStatus}
            openSymbols={openSymbols}
            onToggleOpen={handleToggleOpen}
            onRemove={handleRemove}
            focusedIndex={focusedIndex}
            onFocusedIndexChange={setFocusedIndex}
          />
        </>
      )}
      {openSymbols.length > 0 && (
        <div style={{ marginTop: 24, display: 'flex', alignItems: 'center', gap: 6 }}>
          <label style={{ cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={multiOpenMode}
              onChange={handleMultiOpenModeChange}
            />{' '}
            Multiple charts
          </label>
        </div>
      )}
      {openSymbols.length > 0 && (
        <div className="chart-grid" style={{ marginTop: 8 }}>
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
                border: '1px solid #eee', padding: 12, minWidth: 0,
                opacity: dragIndex === index ? 0.5 : 1,
                // first open chart always gets its own full-width row; the
                // rest wrap 3-per-row below it (flex-basis 100% leaves no
                // room for a sibling beside it, forcing the wrap)
                flexBasis: index === 0 ? '100%' : undefined,
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                <strong style={{ cursor: 'grab' }} title="Drag to reorder">⠿ {instrument.symbol}</strong>
                <div style={{ display: 'flex', gap: 4 }}>
                  <button onClick={() => setFullscreenId(instrument.id)} title="Fullscreen">⛶</button>
                  <button onClick={() => handleToggleOpen(instrument)} title="Close chart">×</button>
                </div>
              </div>
              <div style={{ display: 'flex', minWidth: 0 }}>
                <div style={{ flex: '1 1 auto', minWidth: 0 }}>
                  <CandleChart instrument={instrument} />
                </div>
                <SidePanel
                  collapsed={depthCollapsedById[instrument.id] ?? true}
                  onToggleCollapsed={(v) => setDepthCollapsedById((prev) => ({ ...prev, [instrument.id]: v }))}
                  panels={[
                    { id: 'depth', label: 'Depth', content: <DepthPanel depth={liveDepth[instrument.symbol]} /> },
                  ]}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
