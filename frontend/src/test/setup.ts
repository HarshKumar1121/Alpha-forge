/**
 * The browser pieces jsdom does not implement, stubbed just far enough for a screen to mount.
 *
 * None of this fakes application behaviour: it fakes the *platform*. A screen that measures
 * itself, opens a socket or paints a canvas must still run its own code; it simply gets a
 * window that answers instead of one that throws.
 */

import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

afterEach(cleanup)

// Media queries: `useMediaQuery` decides how many table columns fit. Nothing matches, so the
// narrow layout is the one under test — the one most likely to drop a column.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }),
})

// Zustand persists the per-screen scope here. Node's own experimental localStorage is off
// without a backing file, and the store reads it at import time, so give it a real one.
if (!globalThis.localStorage) {
  const store = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, String(value)),
    removeItem: (key: string) => void store.delete(key),
    clear: () => store.clear(),
    key: (index: number) => [...store.keys()][index] ?? null,
    get length() {
      return store.size
    },
  })
}

class StubObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return []
  }
}
vi.stubGlobal('ResizeObserver', StubObserver)
vi.stubGlobal('IntersectionObserver', StubObserver)

// jsdom lays nothing out, so every element measures zero. The virtualiser then renders no
// rows at all and a table's cells would never be exercised; a non-zero height gives it a
// viewport to fill.
Object.defineProperty(HTMLElement.prototype, 'getBoundingClientRect', {
  writable: true,
  value() {
    return { x: 0, y: 0, top: 0, left: 0, right: 1280, bottom: 800, width: 1280, height: 800 }
  },
})
Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, value: 800 })
Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, value: 1280 })
Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, value: 800 })
Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, value: 1280 })
HTMLElement.prototype.scrollTo = () => {}
HTMLElement.prototype.scrollIntoView = () => {}

// CodeMirror measures a character by putting a Range round it, from a frame callback that can
// outlive the test. jsdom's Range has neither method, and the throw lands with no test to
// attach it to, which fails the run.
const EMPTY_RECTS = Object.assign([] as DOMRect[], { item: () => null }) as unknown as DOMRectList
Range.prototype.getClientRects = () => EMPTY_RECTS
Range.prototype.getBoundingClientRect = () =>
  ({ x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 }) as DOMRect

// Base UI positions popups with the Popover API; jsdom has neither it nor the full-screen API.
HTMLElement.prototype.showPopover ??= function showPopover(this: HTMLElement) {}
HTMLElement.prototype.hidePopover ??= function hidePopover(this: HTMLElement) {}
HTMLElement.prototype.requestFullscreen ??= () => Promise.resolve()

/**
 * A 2-D context that answers every call. `color()` in the PnL chart paints one pixel and reads
 * it back to turn an `oklch()` token into `rgba()`, and the chart libraries ask for far more,
 * so the stub returns a plausible value rather than undefined for the few reads that matter.
 */
const context = new Proxy(
  {
    canvas: null,
    getImageData: () => ({ data: new Uint8ClampedArray([17, 17, 17, 255]) }),
    measureText: () => ({ width: 0, actualBoundingBoxAscent: 0, actualBoundingBoxDescent: 0 }),
    createLinearGradient: () => ({ addColorStop: () => {} }),
    createRadialGradient: () => ({ addColorStop: () => {} }),
    createPattern: () => null,
    getLineDash: () => [],
  } as Record<string, unknown>,
  {
    get(target, property) {
      if (property in target) return target[property as string]
      // Everything else is a drawing call whose return value nothing reads.
      return () => undefined
    },
    set(target, property, value) {
      target[property as string] = value
      return true
    },
  },
)
HTMLCanvasElement.prototype.getContext = (() =>
  context) as unknown as HTMLCanvasElement['getContext']
HTMLCanvasElement.prototype.toDataURL = () => 'data:image/png;base64,'

// The live-telemetry socket: a screen subscribes on mount, and jsdom's WebSocket would try to
// open a real connection to an address nothing is listening on.
vi.stubGlobal(
  'WebSocket',
  class {
    static readonly CONNECTING = 0
    static readonly OPEN = 1
    static readonly CLOSING = 2
    static readonly CLOSED = 3
    readyState = 0
    onopen: (() => void) | null = null
    onclose: (() => void) | null = null
    onerror: (() => void) | null = null
    onmessage: (() => void) | null = null
    send() {}
    close() {}
    addEventListener() {}
    removeEventListener() {}
  },
)
