import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getWatchScores, listStrategies, listStrategyElements, listSymbols, removeSymbol } from '../api/client.js'
import { getSocket, roomFor, subscribeRooms, unsubscribeRooms } from '../api/ws.js'
import AddInstrumentButton from '../components/AddInstrumentButton.jsx'
import FeedStatusBadge, { useFeedStatus } from '../components/FeedStatusBadge.jsx'
import LiveEventsPanel, { isIndicatorSignal } from '../components/LiveEventsPanel.jsx'
import InstrumentGrid from '../components/InstrumentGrid.jsx'
import InstrumentDetail from '../components/InstrumentDetail.jsx'
import PageTabBar from '../components/PageTabBar.jsx'
import ScoreGrid, { BUCKET_ORDER } from '../components/ScoreGrid.jsx'
import GraphWindow from '../components/GraphWindow.jsx'
import Button from '../components/kit/Button.jsx'

const VISIBLE_ROWS = 5 // matches InstrumentGrid's own cap, for the footer's "Showing X of N"
const MAX_LIVE_EVENTS = 300
const SCORES_REFRESH_MS = 15000 // re-score at most this often while new activity is arriving

// The INSTRUMENTS card's Sort dropdown -- only the modes backed by real data.
// The preview's BB-width and day-volume modes are deliberately left out (no
// backend source yet, see market_watch_grid_redesign_plan.md).
const SORT_MODES = [
  { value: '', label: 'Sort: default' },
  { value: 'status', label: 'Sort: status color' },
  { value: 'selected', label: 'Sort: selected first' },
  { value: 'day_change', label: 'Sort: day change' },
]

function nullsLast(a, b) {
  return (a == null) - (b == null)
}

// Bull-bucket sorts by bull_score desc, bear-bucket by bear_score desc,
// choppy by total activity desc -- "quiet" has nothing to sort by (every
// row is 0-0), so it's left in the table's own existing order.
function scoreSortKey(bucket, row) {
  if (bucket === 'choppy') return row.bull_score + row.bear_score
  if (bucket?.endsWith('bear')) return row.bear_score
  return row.bull_score
}

const MAX_OPEN_CHARTS = 4
// Deliberately distinct from MarketWatchClassic.jsx's own
// OPEN_SYMBOL_IDS_KEY ('marketWatch.openSymbolIds') -- sharing one key
// across both pages let visiting one page silently restore/overwrite the
// other's open-chart selection (same localStorage origin). Found live
// 2026-10-05 while testing the merge: Classic's test opened a chart,
// which the new page then "restored" on load, so the test's own click on
// an already-open row toggled it closed instead of opening it.
const OPEN_SYMBOL_IDS_KEY = 'marketWatch.new.openSymbolIds'
const OPEN_TAB_IDS_KEY = 'marketWatch.openTabIds'

function loadStoredIds(key) {
  try {
    const raw = localStorage.getItem(key)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

export default function MarketWatch() {
  const [symbols, setSymbols] = useState([])
  const [elements, setElements] = useState([])
  const [strategies, setStrategies] = useState([])
  const [scores, setScores] = useState([])
  const [bucketColors, setBucketColors] = useState({})
  const [selectedBucket, setSelectedBucket] = useState(null)
  const [liveTicks, setLiveTicks] = useState({}) // symbol -> tick payload
  const [liveDepth, setLiveDepth] = useState({}) // symbol -> depth payload
  const [backfillStatus, setBackfillStatus] = useState({}) // symbol -> {status, message}
  const [openSymbols, setOpenSymbols] = useState([]) // below-grid chart cards, up to MAX_OPEN_CHARTS
  const [focusedIndex, setFocusedIndex] = useState(-1)
  const [listCollapsed, setListCollapsed] = useState(false) // the card's ▼/▲ (was the "Hide list" button)
  const [headerCollapsed, setHeaderCollapsed] = useState(false) // the page header's ▲/▼ -- hides eyebrow + params
  const [sortMode, setSortMode] = useState('')
  const [showAllRows, setShowAllRows] = useState(false)
  const [dragIndex, setDragIndex] = useState(null)
  // Off by default, as in the finalized preview: clicking a row replaces the
  // one chart below the grid; the card's "Multi" checkbox opts into up to 4.
  const [multiOpenMode, setMultiOpenMode] = useState(false)
  // instrument id -> 'hidden' | 'right' | 'below' -- the 3-state depth cycle,
  // persisted here (not inside GraphWindow) so it survives drag-reorder
  const [depthModeById, setDepthModeById] = useState({})
  // per-symbol detail tabs, independent of the below-grid chart cards above
  const [tabs, setTabs] = useState([])
  const [activeTabId, setActiveTabId] = useState(null) // null = the Market Watch grid tab
  const feed = useFeedStatus()
  const replayMode = feed?.mode === 'replay'
  const [liveEvents, setLiveEvents] = useState([]) // newest first, from the `activity` WS event
  // TEMPORARY (replay): each detected pattern adds 1 to a RANDOM color card --
  // the user's own placeholder until the pattern->color rule is decided
  const [randomCardCounts, setRandomCardCounts] = useState({})
  const lastScoresFetch = useRef(0)
  const joinedRooms = useRef(new Set())
  const restoredOpenSymbols = useRef(false)
  const restoredTabs = useRef(false)

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

  const refreshScores = useCallback(() => {
    lastScoresFetch.current = Date.now()
    getWatchScores().then((res) => { setScores(res.scores); setBucketColors(res.bucket_colors) }).catch(() => {})
  }, [])

  useEffect(() => {
    refreshScores()
  }, [symbols, refreshScores])

  useEffect(() => {
    const socket = getSocket()
    function onActivity(event) {
      setLiveEvents((prev) => [event, ...prev].slice(0, MAX_LIVE_EVENTS))
      if (!isIndicatorSignal(event)) {
        const bucket = BUCKET_ORDER[Math.floor(Math.random() * BUCKET_ORDER.length)]
        setRandomCardCounts((prev) => ({ ...prev, [bucket]: (prev[bucket] ?? 0) + 1 }))
      }
      // buckets (and so the Buy/Sell/Hold buttons) follow new activity
      if (Date.now() - lastScoresFetch.current > SCORES_REFRESH_MS) refreshScores()
    }
    socket.on('activity', onActivity)
    return () => socket.off('activity', onActivity)
  }, [refreshScores])

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

  // The Sort dropdown, applied on top of the bucket filter above. Stable:
  // equal values keep registration order; missing values always sort last.
  const sortedSymbols = useMemo(() => {
    if (!sortMode) return visibleSymbols
    const openIds = new Set(openSymbols.map((s) => s.id))
    const bucketRank = (s) => {
      const i = BUCKET_ORDER.indexOf(scoreByInstrument[s.id]?.bucket)
      return i === -1 ? null : i
    }
    const dayChange = (s) => liveTicks[s.symbol]?.day_change_percentage ?? null
    const compare = {
      status: (a, b) => nullsLast(bucketRank(a), bucketRank(b)) || (bucketRank(a) - bucketRank(b)),
      selected: (a, b) => Number(openIds.has(b.id)) - Number(openIds.has(a.id)),
      day_change: (a, b) => nullsLast(dayChange(a), dayChange(b)) || (dayChange(b) - dayChange(a)),
    }[sortMode]
    return visibleSymbols.map((s, i) => ({ s, i })).sort((x, y) => compare(x.s, y.s) || (x.i - y.i)).map((x) => x.s)
  }, [visibleSymbols, sortMode, openSymbols, scoreByInstrument, liveTicks])

  const exchanges = useMemo(() => [...new Set(symbols.map((s) => s.exchange))], [symbols])

  // keep WS room membership in sync with the registered-symbol list, without
  // restarting anything — each change is an incremental join/leave
  useEffect(() => {
    const desired = new Set(symbols.map((s) => roomFor(s.exchange, s.symbol)))
    const toJoin = [...desired].filter((room) => !joinedRooms.current.has(room))
    const toLeave = [...joinedRooms.current].filter((room) => !desired.has(room))
    subscribeRooms(toJoin)
    unsubscribeRooms(toLeave)
    joinedRooms.current = desired

    // drop any open charts / tabs / focus for symbols that got removed elsewhere
    const stillValidIds = new Set(symbols.map((s) => s.id))
    setOpenSymbols((prev) => prev.filter((s) => stillValidIds.has(s.id)))
    setTabs((prev) => prev.filter((s) => stillValidIds.has(s.id)))
    setFocusedIndex((prev) => (prev >= symbols.length ? symbols.length - 1 : prev))

    // restore the chart selection from a previous page load, once, after the
    // real symbol rows are available to match stored ids against
    if (!restoredOpenSymbols.current && symbols.length > 0) {
      restoredOpenSymbols.current = true
      const storedIds = loadStoredIds(OPEN_SYMBOL_IDS_KEY)
      if (storedIds.length > 0) {
        const byId = new Map(symbols.map((s) => [s.id, s]))
        const restored = storedIds.map((id) => byId.get(id)).filter(Boolean).slice(0, MAX_OPEN_CHARTS)
        if (restored.length > 0) setOpenSymbols(restored)
      }
    }
    if (!restoredTabs.current && symbols.length > 0) {
      restoredTabs.current = true
      const storedTabIds = loadStoredIds(OPEN_TAB_IDS_KEY)
      if (storedTabIds.length > 0) {
        const byId = new Map(symbols.map((s) => [s.id, s]))
        const restored = storedTabIds.map((id) => byId.get(id)).filter(Boolean)
        if (restored.length > 0) setTabs(restored)
      }
    }
  }, [symbols])

  // drop the active tab if what it points at is no longer open
  useEffect(() => {
    if (activeTabId !== null && !tabs.some((s) => s.id === activeTabId)) {
      setActiveTabId(null)
    }
  }, [tabs, activeTabId])

  // persist both the chart selection and the tab selection so they survive a
  // page refresh — gated on the restore attempt above having already run,
  // otherwise the empty initial state would immediately overwrite last
  // session's saved selection with []
  useEffect(() => {
    if (!restoredOpenSymbols.current) return
    try {
      localStorage.setItem(OPEN_SYMBOL_IDS_KEY, JSON.stringify(openSymbols.map((s) => s.id)))
    } catch {
      // ignore — e.g. private browsing with storage disabled
    }
  }, [openSymbols])

  useEffect(() => {
    if (!restoredTabs.current) return
    try {
      localStorage.setItem(OPEN_TAB_IDS_KEY, JSON.stringify(tabs.map((s) => s.id)))
    } catch {
      // ignore — e.g. private browsing with storage disabled
    }
  }, [tabs])

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

  // clear a "done" backfill status a few seconds after it lands, so the grid
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
      // Carry the outgoing stock's depth mode over to the incoming one, so
      // switching stocks doesn't reset Depth back to hidden.
      const previousId = openSymbols[0]?.id
      if (previousId !== undefined && previousId !== row.id) {
        setDepthModeById((prev) => ({ ...prev, [row.id]: prev[previousId] ?? 'hidden' }))
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
    setTabs((prev) => prev.filter((s) => s.id !== id))
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

  function handleOpenTab(row) {
    setTabs((prev) => (prev.some((s) => s.id === row.id) ? prev : [...prev, row]))
    setActiveTabId(row.id)
  }

  function handleCloseTab(id) {
    setTabs((prev) => prev.filter((s) => s.id !== id))
  }

  const activeTabInstrument = tabs.find((s) => s.id === activeTabId) ?? null

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
      <PageTabBar tabs={tabs} activeId={activeTabId} onSelect={setActiveTabId} onClose={handleCloseTab} />

      {activeTabInstrument ? (
        <InstrumentDetail
          instrument={activeTabInstrument}
          tick={liveTicks[activeTabInstrument.symbol]}
          depth={liveDepth[activeTabInstrument.symbol]}
          score={scoreByInstrument[activeTabInstrument.id]}
        />
      ) : (
        <>
          {/* Page header, as finalized in the preview: eyebrow, title + ▲
              collapse (hides eyebrow and params, keeps the title), params. */}
          <div style={{ marginBottom: 34 }}>
            {!headerCollapsed && (
              <div style={{ fontSize: 12, letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--text-faint)', fontWeight: 600, marginBottom: 3 }}>
                Live snapshot · Market Watch
              </div>
            )}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <h1 style={{ margin: 0, fontSize: 26, fontWeight: 700, letterSpacing: '-0.01em', color: 'var(--text)' }}>Market Watch</h1>
              <button
                type="button" onClick={() => setHeaderCollapsed((c) => !c)}
                title={headerCollapsed ? 'Expand header' : 'Collapse header'}
                style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', fontSize: 14, padding: '2px 6px' }}
              >{headerCollapsed ? '▼' : '▲'}</button>
            </div>
            {!headerCollapsed && (
              <div style={{ fontSize: 13.5, color: 'var(--text-dim)', display: 'flex', flexWrap: 'wrap', gap: '6px 18px', marginTop: 3 }}>
                <span><b style={{ color: 'var(--text)', fontWeight: 600 }}>{symbols.length}</b> instruments registered</span>
                {exchanges.length > 0 && <span>{exchanges.join(' · ')}</span>}
                <FeedStatusBadge status={feed} />
              </div>
            )}
          </div>

          {/* INSTRUMENTS card: head (Sort / + / Multi / collapse), color
              tiles, grid, "Showing X of N · Show all" footer. No
              overflow:hidden on purpose -- the "+" popover is absolutely
              positioned and would be clipped. */}
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', marginBottom: 'var(--space-5)' }}>
            <div style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap',
              padding: '10px var(--space-3)', borderBottom: listCollapsed ? 'none' : '1px solid var(--border)',
              fontSize: 12, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.03em', color: 'var(--text-faint)',
            }}
            >
              <span>Instruments</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, textTransform: 'none', letterSpacing: 0, fontWeight: 600 }}>
                <select
                  value={sortMode} onChange={(e) => setSortMode(e.target.value)} title="Sort instruments" aria-label="Sort instruments"
                  style={{ fontSize: 11.5, padding: '3px 6px', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', background: 'var(--surface)', color: 'var(--text-dim)' }}
                >
                  {SORT_MODES.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
                </select>
                <AddInstrumentButton onRegistered={refresh} compact />
                <label title="Show multiple instrument charts at once (up to 4)" style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11.5, color: 'var(--text-dim)', cursor: 'pointer', textTransform: 'uppercase' }}>
                  <input type="checkbox" checked={multiOpenMode} onChange={handleMultiOpenModeChange} />
                  Multi
                </label>
                <button
                  type="button" onClick={() => setListCollapsed((c) => !c)}
                  title={listCollapsed ? 'Expand' : 'Collapse'}
                  style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', fontSize: 12, padding: '2px 6px' }}
                >{listCollapsed ? '▲' : '▼'}</button>
              </div>
            </div>

            {!listCollapsed && (
              <>
                {scores.length > 0 && (
                  <div style={{ padding: 'var(--space-3) var(--space-3) 0' }}>
                    <ScoreGrid
                      scores={scores} bucketColors={bucketColors}
                      selectedBucket={selectedBucket} onSelectBucket={setSelectedBucket}
                      countsOverride={replayMode ? randomCardCounts : undefined}
                      overrideNote={replayMode ? 'replay placeholder: pattern counts assigned to random colors' : undefined}
                    />
                  </div>
                )}
                {selectedBucket && (
                  <div style={{ margin: '0 var(--space-3) var(--space-2)', fontSize: 13, color: 'var(--text-dim)', display: 'flex', alignItems: 'center', gap: 8 }}>
                    Showing <b style={{ color: 'var(--text)' }}>{visibleSymbols.length}</b> of {symbols.length}, sorted by {selectedBucket.replace('_', ' ')}
                    <Button variant="secondary" onClick={() => setSelectedBucket(null)}>Clear filter</Button>
                  </div>
                )}
                <InstrumentGrid
                  symbols={sortedSymbols}
                  liveTicks={liveTicks}
                  backfillStatus={backfillStatus}
                  openSymbols={openSymbols}
                  onToggleOpen={handleToggleOpen}
                  onRemove={handleRemove}
                  onOpenTab={handleOpenTab}
                  focusedIndex={focusedIndex}
                  onFocusedIndexChange={setFocusedIndex}
                  elements={elements}
                  strategies={strategies}
                  scoreByInstrument={scoreByInstrument}
                  bucketColors={bucketColors}
                  showAll={showAllRows}
                  embedded
                  externalSortMode={sortMode}
                  onColumnSort={() => setSortMode('')}
                />
                <div style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  padding: '8px var(--space-3)', borderTop: '1px solid var(--border)', fontSize: 12, color: 'var(--text-faint)',
                }}
                >
                  <span>Showing {showAllRows ? sortedSymbols.length : Math.min(VISIBLE_ROWS, sortedSymbols.length)} of {sortedSymbols.length}</span>
                  {sortedSymbols.length > VISIBLE_ROWS && (
                    <button
                      type="button" onClick={() => setShowAllRows((v) => !v)}
                      style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--accent-text)', fontSize: 12, fontWeight: 600 }}
                    >{showAllRows ? 'Show less' : 'Show all'}</button>
                  )}
                </div>
              </>
            )}
          </div>
          <LiveEventsPanel events={liveEvents} />
          {openSymbols.length > 0 && (
            <div className="chart-grid" style={{ marginTop: 'var(--space-5)' }}>
              {openSymbols.map((instrument, index) => (
                <ChartGridCard
                  key={instrument.id}
                  instrument={instrument}
                  index={index}
                  depth={liveDepth[instrument.symbol]}
                  depthMode={depthModeById[instrument.id] ?? 'hidden'}
                  onDepthModeChange={(next) => setDepthModeById((prev) => ({ ...prev, [instrument.id]: next }))}
                  dragIndex={dragIndex}
                  onDragStart={() => setDragIndex(index)}
                  onDrop={() => handleDrop(index)}
                  onDragEnd={() => setDragIndex(null)}
                  onClose={() => handleToggleOpen(instrument)}
                />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}

// One below-grid chart card -- owns its own GraphWindow ref so its
// fullscreen button can trigger that specific instance (refs can't be
// created inside the .map() above, hooks don't allow that).
function ChartGridCard({ instrument, index, depth, depthMode, onDepthModeChange, dragIndex, onDragStart, onDrop, onDragEnd, onClose }) {
  const graphRef = useRef(null)
  return (
    <div
      className="chart-card"
      draggable
      onDragStart={onDragStart}
      onDragOver={(e) => e.preventDefault()}
      onDrop={onDrop}
      onDragEnd={onDragEnd}
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
            onClick={() => graphRef.current?.requestFullscreen()} title="Fullscreen"
            style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', fontSize: 14 }}
          >⛶</button>
          <button
            onClick={onClose} title="Close chart"
            style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', fontSize: 16 }}
          >×</button>
        </div>
      </div>
      <GraphWindow
        ref={graphRef} instrument={instrument} depth={depth}
        depthMode={depthMode} onDepthModeChange={onDepthModeChange}
      />
    </div>
  )
}
