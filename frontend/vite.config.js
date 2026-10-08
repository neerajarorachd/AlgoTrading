import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Backend runs at localhost:5000 (Flask dev server) — proxy REST + WS so the
// browser only ever talks to one origin during local dev. BACKEND_URL
// overrides it so a separate test/preview stack (its own backend + its own
// throwaway DB) can run beside the live one without sharing port 5000, e.g.
//   BACKEND_URL=http://127.0.0.1:5055 npx vite --port 5174
const backend = process.env.BACKEND_URL || 'http://localhost:5000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': backend,
      '/socket.io': { target: backend, ws: true },
    },
  },
})
