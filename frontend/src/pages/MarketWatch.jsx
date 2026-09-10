import { useCallback, useEffect, useRef, useState } from 'react'
import { listSymbols, removeSymbol } from '../api/client.js'
import { getSocket, roomFor, subscribeRooms, unsubscribeRooms } from '../api/ws.js'
import SymbolRegisterForm from '../components/SymbolRegisterForm.jsx'
import SymbolTable from '../components/SymbolTable.jsx'
import DepthPanel from '../components/DepthPanel.jsx'
import CandleChart from '../components/CandleChart.jsx'

export default function MarketWatch() {
  const [symbols, setSymbols] = useState([])
  const [liveTicks, setLiveTicks] = useState({}) // symbol -> tick payload
  const [liveDepth, setLiveDepth] = useState({}) // symbol -> depth payload
  const [selected, setSelected] = useState(null)
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
  }, [symbols])

  useEffect(() => {
    const socket = getSocket()
    const onTick = (payload) => setLiveTicks((prev) => ({ ...prev, [payload.symbol]: payload }))
    const onDepth = (payload) => setLiveDepth((prev) => ({ ...prev, [payload.symbol]: payload }))
    socket.on('tick', onTick)
    socket.on('depth', onDepth)
    return () => {
      socket.off('tick', onTick)
      socket.off('depth', onDepth)
    }
  }, [])

  async function handleRemove(id) {
    await removeSymbol(id)
    if (selected?.id === id) setSelected(null)
    refresh()
  }

  return (
    <div style={{ fontFamily: 'sans-serif', padding: 16, maxWidth: 1000, margin: '0 auto' }}>
      <h1>Market Watch</h1>
      <SymbolRegisterForm onRegistered={refresh} />
      <SymbolTable
        symbols={symbols}
        liveTicks={liveTicks}
        selected={selected}
        onSelect={setSelected}
        onRemove={handleRemove}
      />
      {selected && (
        <div style={{ display: 'flex', gap: 24, marginTop: 24 }}>
          <DepthPanel depth={liveDepth[selected.symbol]} />
          <CandleChart instrument={selected} />
        </div>
      )}
    </div>
  )
}
