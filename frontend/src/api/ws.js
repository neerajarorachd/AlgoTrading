import { io } from 'socket.io-client'

let socket = null
// every room this page has joined, re-joined after any reconnect (a new
// connection is a new server session with no rooms)
const joined = new Set()

export function getSocket() {
  if (!socket) {
    // WebSocket first (ALGO-36, 2026-10-14): long-polling held browser
    // connections open (6 per server), so ordinary API calls queued behind
    // hung polls for 6-18 s. The backend stays in Flask-SocketIO's threading
    // mode (see ws_live.py); `simple-websocket` gives it real WebSocket
    // support on the Werkzeug server -- verified on the test stack, direct
    // and through Vite's proxy. Polling remains as the fallback if a
    // WebSocket can't be opened (tryAllTransports).
    //
    // History: on 2026-09-17 forcing ['websocket'] failed with HTTP 400 in a
    // retry loop, before simple-websocket was installed; this client then
    // ran polling-only with upgrade disabled.
    socket = io('/', { transports: ['websocket', 'polling'], tryAllTransports: true })
    socket.on('connect', () => {
      if (joined.size) socket.emit('subscribe_ticks', { rooms: [...joined] })
    })
  }
  return socket
}

// Must match backend/api/ws_live.py's room_for() exactly — one room per
// instrument, shared by tick/depth/candle_closed events.
export function roomFor(exchange, symbol) {
  return `${exchange}:${symbol}`
}

export function subscribeRooms(rooms) {
  rooms.forEach((r) => joined.add(r))
  if (rooms.length) getSocket().emit('subscribe_ticks', { rooms })
}

export function unsubscribeRooms(rooms) {
  rooms.forEach((r) => joined.delete(r))
  if (rooms.length) getSocket().emit('unsubscribe_ticks', { rooms })
}

/** Which transport the live connection is using ('websocket' | 'polling' | null). */
export function socketTransport() {
  return socket?.io?.engine?.transport?.name ?? null
}
