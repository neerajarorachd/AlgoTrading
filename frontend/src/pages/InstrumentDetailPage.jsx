import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getWatchScores, listSymbols } from '../api/client.js'
import { getSocket, roomFor, subscribeRooms, unsubscribeRooms } from '../api/ws.js'
import InstrumentDetail from '../components/InstrumentDetail.jsx'

// Thin route wrapper so "open in new tab" (InstrumentDetail's own ⧉ button)
// is a real navigation to /instrument/:exchange/:symbol instead of a
// routing-less Blob-document hack -- resolves the URL params against the
// real symbol list, then renders the same InstrumentDetail component the
// Market Watch page's tabs use inline.
export default function InstrumentDetailPage() {
  const { exchange, symbol } = useParams()
  const [instrument, setInstrument] = useState(null)
  const [notFound, setNotFound] = useState(false)
  const [scoreByInstrument, setScoreByInstrument] = useState({})
  const [tick, setTick] = useState(null)
  const [depth, setDepth] = useState(null)

  useEffect(() => {
    listSymbols()
      .then((rows) => {
        const match = rows.find((s) => s.symbol === symbol && s.exchange === exchange)
        if (match) setInstrument(match)
        else setNotFound(true)
      })
      .catch(() => setNotFound(true))
    getWatchScores()
      .then((res) => setScoreByInstrument(Object.fromEntries(res.scores.map((r) => [r.instrument_id, r]))))
      .catch(() => {})
  }, [exchange, symbol])

  useEffect(() => {
    if (!instrument) return
    const room = roomFor(instrument.exchange, instrument.symbol)
    subscribeRooms([room])
    const socket = getSocket()
    const onTick = (payload) => { if (payload.symbol === instrument.symbol) setTick(payload) }
    const onDepth = (payload) => { if (payload.symbol === instrument.symbol) setDepth(payload) }
    socket.on('tick', onTick)
    socket.on('depth', onDepth)
    return () => {
      socket.off('tick', onTick)
      socket.off('depth', onDepth)
      unsubscribeRooms([room])
    }
  }, [instrument])

  if (notFound) {
    return <div style={{ padding: 'var(--space-5)', color: 'var(--text-faint)' }}>Instrument not found.</div>
  }
  if (!instrument) {
    return <div style={{ padding: 'var(--space-5)', color: 'var(--text-faint)' }}>Loading…</div>
  }
  return (
    <div style={{ paddingBlock: 32, paddingInline: 'var(--space-5)', maxWidth: 1200, margin: '0 auto' }}>
      <InstrumentDetail instrument={instrument} tick={tick} depth={depth} score={scoreByInstrument[instrument.id]} />
    </div>
  )
}
