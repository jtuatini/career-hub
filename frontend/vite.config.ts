import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Shared API token, injected server-side so the browser never sees it.
// Created by the backend on first request — if this is a brand-new setup,
// start the backend once, then restart `npm run dev`.
let apiToken = ''
try {
  apiToken = readFileSync(
    fileURLToPath(new URL('../data/api_token', import.meta.url)),
    'utf8',
  ).trim()
} catch {
  console.warn('[copilot] data/api_token not found — start the backend, then restart vite')
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8321',
        ws: true,
        headers: apiToken ? { 'X-Copilot-Token': apiToken } : {},
      },
    },
  },
})
