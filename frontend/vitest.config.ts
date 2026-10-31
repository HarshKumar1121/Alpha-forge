/**
 * The smoke test's runner. It reuses the app's own `@` alias and React plugin so a test
 * compiles the same sources the browser does; Tailwind is left out because no test reads a
 * computed style, and its scan is the slowest part of a cold build.
 */

import path from 'node:path'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, 'src') },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    // A screen that hangs is a failure, not a timeout the whole suite waits out.
    testTimeout: 30_000,
    restoreMocks: true,
  },
})
