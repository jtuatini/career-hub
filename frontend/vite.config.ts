import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Shared API token, injected server-side so the browser never sees it.
// The backend mints data/api_token at startup; we read it lazily per request
// (cached once found) so Vite starting before the backend — as start.sh does —
// can't wedge the proxy in a tokenless state.
const tokenUrl = new URL('../data/api_token', import.meta.url)
let apiToken = ''
function readToken(): string {
  if (!apiToken) {
    try {
      apiToken = readFileSync(fileURLToPath(tokenUrl), 'utf8').trim()
    } catch {
      // Backend not up yet — the next request retries.
    }
  }
  return apiToken
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8321',
        ws: true,
        configure(proxy) {
          proxy.on('proxyReq', (proxyReq) => {
            const token = readToken()
            if (token) proxyReq.setHeader('X-Copilot-Token', token)
          })
        },
      },
    },
  },
})
