import { io } from 'socket.io-client'

let socket = null

export function getSocket() {
  if (!socket) {
    socket = io('/', { transports: ['websocket'] })
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
