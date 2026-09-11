import { useCallback, useEffect, useRef, useState } from 'react'
import { listSymbols, removeSymbol } from '../api/client.js'
import { getSocket, roomFor, subscribeRooms, unsubscribeRooms } from '../api/ws.js'
import SymbolRegisterForm from '../components/SymbolRegisterForm.jsx'
import SymbolTable from '../components/SymbolTable.jsx'
import DepthPanel from '../components/DepthPanel.jsx'
import CandleChart from '../components/CandleChart.jsx'
import SidePanel from '../components/SidePanel.jsx'

const MAX_OPEN_CHARTS = 4

export default function MarketWatch() {
  const [symbols, setSymbols] = useState([])
  const [liveTicks, setLiveTicks] = useState({}) // symbol -> tick payload
  const [liveDepth, setLiveDepth] = useState({}) // symbol -> depth payload
  const [backfillStatus, setBackfillStatus] = useState({}) // symbol -> {status, message}
  const [openSymbols, setOpenSymbols] = useState([]) // up to MAX_OPEN_CHARTS rows
  const [focusedIndex, setFocusedIndex] = useState(-1)
  const [checkedIds, setCheckedIds] = useState(new Set())
  const [listCollapsed, setListCollapsed] = useState(false)
  const [dragIndex, setDragIndex] = useState(null)
  const [multiOpenMode, setMultiOpenMode] = useState(true)
  const joinedRooms = useRef(new Set())

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
  }, [symbols])

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
      // single-graph mode: clicking any row always switches the one open chart
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
    setCheckedIds((prev) => {
      if (!prev.has(id)) return prev
      const next = new Set(prev)
      next.delete(id)
      return next
    })
    refresh()
  }

  function handleToggleChecked(id) {
    setCheckedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function handleToggleCheckAll() {
    setCheckedIds((prev) => {
      const allChecked = symbols.length > 0 && symbols.every((s) => prev.has(s.id))
      return allChecked ? new Set() : new Set(symbols.map((s) => s.id))
    })
  }

  function handleOpenSelected() {
    setOpenSymbols((prev) => {
      const next = [...prev]
      for (const row of symbols) {
        if (!checkedIds.has(row.id)) continue
        if (next.length >= MAX_OPEN_CHARTS) break
        if (!next.some((s) => s.id === row.id)) next.push(row)
      }
      return next
    })
  }

  async function handleRemoveSelected() {
    const ids = [...checkedIds]
    for (const id of ids) {
      await removeSymbol(id)
    }
    setOpenSymbols((prev) => prev.filter((s) => !checkedIds.has(s.id)))
    setCheckedIds(new Set())
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
          {checkedIds.size > 0 && (
            <div style={{ margin: '8px 0', display: 'flex', gap: 8, alignItems: 'center' }}>
              <span>{checkedIds.size} selected</span>
              <button onClick={handleOpenSelected}>Open selected</button>
              <button onClick={handleRemoveSelected}>Remove selected</button>
            </div>
          )}
          <SymbolTable
            symbols={symbols}
            liveTicks={liveTicks}
            backfillStatus={backfillStatus}
            openSymbols={openSymbols}
            onToggleOpen={handleToggleOpen}
            onRemove={handleRemove}
            focusedIndex={focusedIndex}
            onFocusedIndexChange={setFocusedIndex}
            checkedIds={checkedIds}
            onToggleChecked={handleToggleChecked}
            onToggleCheckAll={handleToggleCheckAll}
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
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                <strong style={{ cursor: 'grab' }} title="Drag to reorder">⠿ {instrument.symbol}</strong>
                <button onClick={() => handleToggleOpen(instrument)} title="Close chart">×</button>
              </div>
              <div style={{ display: 'flex', minWidth: 0 }}>
                <div style={{ flex: '1 1 auto', minWidth: 0 }}>
                  <CandleChart instrument={instrument} />
                </div>
                <SidePanel
                  defaultCollapsed
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
