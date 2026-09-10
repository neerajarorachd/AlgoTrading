import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Backend runs at localhost:5000 (Flask dev server) — proxy REST + WS so the
// browser only ever talks to one origin during local dev.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:5000',
      '/socket.io': { target: 'http://localhost:5000', ws: true },
    },
  },
})
