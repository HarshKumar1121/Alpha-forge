/**
 * Every route mounts.
 *
 * This is a smoke test, not a feature test: it says nothing about what a screen shows, only
 * that rendering it does not throw. That is worth a test of its own because the router catches
 * a thrown render and swaps in `RouteError`, so a screen that crashes in the browser looks
 * like a screen with a message — the app does not go blank and nothing fails loudly. Here the
 * same substitution is the failure signal.
 *
 * It was written after a crash that no type could catch: `DataTable` rebuilds a column header's
 * children through `Children.map`, which returns an array, and the tooltip under it needs one
 * element. `/data` failed to mount, and `tsc` was happy.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { type AnyRoute, createMemoryHistory, RouterProvider } from '@tanstack/react-router'
import { act, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createAppRouter, routeTree } from '@/router'
import { installApiMock } from './api-mock'

/**
 * Every path the sidebar, the command menu or a link can reach, with ids filled in. A route
 * added without a line here is caught by `covers every route in the tree` below.
 */
const ROUTES = [
  '/dashboard',
  '/matrix',
  '/data',
  '/labs',
  '/labs/search',
  '/labs/template',
  '/labs/evolution',
  '/labs/power-pool',
  '/labs/super-alpha',
  '/tools',
  '/tools/settings-sampler',
  '/tools/correlation-breaker',
  '/tools/fields-sampler',
  '/forge/today',
  '/forge/gym',
  '/forge/templates',
  '/forge/fieldsets',
  '/forge/datasets/analyst4',
  '/competitions',
  '/tasks',
  '/tasks/1',
  '/pool',
  '/pool/submittable',
  '/portfolio',
  '/alpha/TEST001',
  '/ai',
  '/ai/settings',
  '/ai/assistant/1',
  '/pyramids',
  '/sync',
]

/**
 * What a caught render error puts on screen: the app's own `RouteError`, and the router's
 * built-in boundary, which catches anything thrown outside a route's own component.
 */
const CRASHED = /This screen failed to render|Something went wrong!/i

/** React logs the component stack through `console.error` before the boundary catches it. */
let errors: unknown[][] = []

beforeEach(() => {
  errors = []
  vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => {
    errors.push(args)
  })
  // Re-stubbed per test so each one counts its own requests. The platform stubs from
  // `setup.ts` are deliberately left in place: unstubbing globals would take those with them.
  installApiMock()
})

async function mount(path: string) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  })
  // A router per test: one carries its resolved location and loaded chunks into the next.
  // Built by the app's own factory, so the error and not-found screens are the real ones.
  const router = createAppRouter({ history: createMemoryHistory({ initialEntries: [path] }) })
  render(
    <QueryClientProvider client={client}>
      {/* biome-ignore lint/suspicious/noExplicitAny: the test router is not the app's registered one */}
      <RouterProvider router={router as any} />
    </QueryClientProvider>,
  )
  // The screen is a lazy chunk: wait until the router has it and has rendered it.
  await waitFor(() => expect(router.state.status).toBe('idle'), { timeout: 20_000 })
  // ...and until its queries have settled, so a render that only crashes with data is caught.
  await waitFor(() => expect(client.isFetching()).toBe(0), { timeout: 20_000 })
  client.clear()
  return router
}

describe('every route mounts', () => {
  it.each(ROUTES)('%s', async (path) => {
    await mount(path)

    expect(screen.queryByText(CRASHED), `${path} rendered the route error screen`).toBeNull()

    // A React render error reaches the boundary through console.error first. Warnings from
    // third-party components are not failures; a thrown render is.
    const thrown = errors.filter(([first]) => {
      const text = String(first)
      return (
        text.includes('The above error occurred') ||
        text.includes('Consider adding an error boundary') ||
        text.includes('Uncaught')
      )
    })
    expect(thrown, `${path} threw during render: ${JSON.stringify(thrown.slice(0, 1))}`).toEqual([])
  })
})

/**
 * The screens Phase 4 added put a tooltip in places the route sweep above visits in only one
 * state. These pin the other ones, because a tooltip trigger that is handed anything but a
 * single element takes the whole screen down rather than just itself.
 */
describe('the Forge data screens', () => {
  it('explains its columns on the Data Explorer', async () => {
    await mount('/data')
    // The headers that broke: a tooltip inside a column header, which the table re-clones.
    expect(screen.getByText('Crowding')).toBeTruthy()
    expect(screen.getByText('Mine')).toBeTruthy()
    expect(screen.queryByText(CRASHED)).toBeNull()
  })

  it('lists the datasets with their crowding', async () => {
    const router = await mount('/data')
    // The Datasets tab is the other half of the screen, behind the segmented control.
    const tab = screen.getByRole('radio', { name: 'Datasets' })
    await act(async () => {
      tab.click()
    })
    await waitFor(() => expect(screen.queryByText('analyst4 data')).toBeTruthy())
    expect(screen.queryByText(CRASHED)).toBeNull()
    expect(router.state.status).toBe('idle')
  })

  it('names the scope picker, with what BRAIN neutralizes against there', async () => {
    await mount('/forge/fieldsets')
    // The scope picker's own tooltip, and the field set the fixture holds.
    expect(screen.getByText('Neutralizations')).toBeTruthy()
    expect(screen.getByText('SUBINDUSTRY')).toBeTruthy()
    expect(screen.getByText('USA TOP3000 analyst')).toBeTruthy()
    expect(screen.queryByText(CRASHED)).toBeNull()
  })
})

describe('the list above stays honest', () => {
  it('covers every route in the tree', () => {
    const paths: string[] = []
    const walk = (route: AnyRoute) => {
      const full = route.fullPath ?? ''
      // Layout routes (`/labs`) and the index redirect carry no screen of their own.
      if (route.options?.component && full) paths.push(full)
      for (const child of (route.children ?? []) as AnyRoute[]) walk(child)
    }
    for (const child of (routeTree.children ?? []) as AnyRoute[]) walk(child)

    // `/pool/$tab` is covered by `/pool/submittable`, and so on for every id.
    const covered = (full: string) =>
      ROUTES.some((path) => {
        const pattern = new RegExp(
          `^${full.replace(/\/$/, '').replaceAll(/\$[a-zA-Z]+/g, '[^/]+')}$`,
        )
        return pattern.test(path)
      })

    const missed = paths.map((p) => p.replace(/\/$/, '')).filter((full) => !covered(full))
    expect(missed, `routes with no entry in ROUTES: ${missed.join(', ')}`).toEqual([])
  })
})
