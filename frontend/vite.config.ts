import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The backend runs as a separate process. Proxying /api and /ws in dev means the
// frontend never needs CORS or credentials of its own.
const BACKEND = 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, 'src') },
  },
  // forge: /forge/analytics is a lazy route that deep-imports four echarts subpaths and
  // mounts six EChart instances on first render. Left undeclared, Vite only discovers them
  // on first navigation to that route, mid-session — which can race into a
  // re-optimize/stale-module-URL reload loop ("Outdated Optimize Dep" 504s). Declaring them
  // up front makes cold start pre-bundle them instead.
  optimizeDeps: {
    include: ['echarts/core', 'echarts/charts', 'echarts/components', 'echarts/renderers'],
  },
  // Built into the backend package, which serves it: `uvx alpha-harness` needs no Node.
  build: {
    outDir: path.resolve(import.meta.dirname, '../backend/src/alpha_harness/web'),
    emptyOutDir: true,
    // Screens are already lazy. Splitting the framework out of the entry as well cost 24 kB
    // gzipped more on first load for chunks that cache across wheel upgrades — worth it
    // over a network, worth nothing from 127.0.0.1. The warning is written for a CDN.
    chunkSizeWarningLimit: 700,
  },
  server: {
    // The backend's WebSocket accepts this origin only; a silent move to 5174 would load
    // the app with its live updates refused.
    strictPort: true,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true },
      '/ws': { target: BACKEND, ws: true, changeOrigin: true },
    },
  },
})
