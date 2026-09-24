import { io } from 'socket.io-client'

let socket = null

export function getSocket() {
  if (!socket) {
    // Backend runs Flask-SocketIO's `threading` async_mode (kept deliberately
    // to avoid monkey-patch conflicts with DhanBroker's own OS-thread feed
    // client — see ws_live.py) which, on Werkzeug's dev server, only serves
    // Socket.IO over long-polling — it cannot complete a real WebSocket
    // upgrade. Forcing transports: ['websocket'] here (found 2026-09-17) made
    // every connection attempt fail with HTTP 400 in a tight retry loop.
    // Just adding 'polling' back wasn't enough either: the client's default
    // upgrade:true still fires an automatic websocket probe alongside the
    // working polling transport, and that failed probe was corrupting the
    // polling session server-side (its next POST came back "400 session
    // unknown"). upgrade: false stops the probe entirely so the connection
    // stays on the one transport that actually works here.
    socket = io('/', { transports: ['polling'], upgrade: false })
  }
  return socket
}

// Must match backend/api/ws_live.py's room_for() exactly — one room per
// instrument, shared by tick/depth/candle_closed events.
export function roomFor(exchange, symbol) {
  return `${exchange}:${symbol}`
}

export function subscribeRooms(rooms) {
  if (rooms.length) getSocket().emit('subscribe_ticks', { rooms })
}

export function unsubscribeRooms(rooms) {
  if (rooms.length) getSocket().emit('unsubscribe_ticks', { rooms })
}
