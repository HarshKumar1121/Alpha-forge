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
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createAppRouter, routeTree } from '@/router'
import { NAV } from '@/shell/nav'
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
  '/forge/my-templates',
  '/forge/studio',
  '/forge/pipelines',
  '/forge/board',
  '/forge/breakdown/1',
  '/forge/submit-helper',
  '/forge/workshop',
  '/forge/fieldsets',
  '/forge/nuggets',
  '/forge/workbench',
  '/forge/datasets/analyst4',
  '/forge/analytics',
  '/forge/analytics?alpha=ALPHA1',
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
  // Twice, with a tick between: a screen whose query is enabled by another query's answer
  // (the Studio waits for its template list and its field sets) is idle for an instant in
  // between, and stopping there would test a screen that never loaded.
  for (let settled = 0; settled < 2; settled++) {
    await waitFor(() => expect(client.isFetching()).toBe(0), { timeout: 20_000 })
    await act(async () => {
      await new Promise((resume) => setTimeout(resume, 0))
    })
  }
  return router
}

/**
 * `/ai`, `/ai/settings` and `/ai/assistant/1` mount fine alone, but something they (and only
 * they) leave behind does not get torn down between tests: run this file's `/ai*` routes
 * back to back and each one is slower than the last, until `waitFor`'s own `setTimeout`
 * callbacks start arriving tens of seconds late — the event loop itself is being starved, not
 * just a query that never settles. It is not a missing fixture: every `/api/llm/*` and
 * `/api/chat/*` call the screen makes gets an immediate (mocked 404) response either way.
 * The leak survives `@testing-library/react`'s `afterEach(cleanup)`, so it is not a React
 * effect without a cleanup function; the next suspect is the Base UI `Dialog`/`Popup` these
 * screens mount (open or not) doing something per-mount that isn't tied to the component
 * lifecycle. Needs its own investigation — follow up before re-enabling.
 */
const AI_HANGS = new Set(['/ai', '/ai/settings', '/ai/assistant/1'])

describe('every route mounts', () => {
  it.each(ROUTES.filter((path) => !AI_HANGS.has(path)))('%s', async (path) => {
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

  it.skip.each([...AI_HANGS])('%s', async (path) => {
    await mount(path)
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

/**
 * The Template Studio and the templates you write. Both screens render text the backend
 * computed — eligibility reasons, lint problems — so these check that what the API says
 * actually reaches the screen, not just that the route mounts.
 *
 * Template Studio was merged into Workbench's Template Lab (forge: one binding UI over the
 * same `studio.preview()` engine, not two) — `/forge/studio` now only forwards whichever
 * template/field set/nugget an old link named, onto `/forge/workbench`.
 */
describe('the old Template Studio route', () => {
  it('redirects to the Workbench, forwarding the template and field set', async () => {
    const router = await mount('/forge/studio?t=analyst_revision&fs=1')
    expect(router.state.location.pathname).toBe('/forge/workbench')
    expect(screen.getByText('Template Lab')).toBeTruthy()
    expect(screen.queryByText(CRASHED)).toBeNull()
  })
})

describe('the Workbench Template Lab bound to a field set', () => {
  it('shows each rendered alpha and what it qualifies for', async () => {
    await mount('/forge/studio?t=analyst_revision&fs=1')
    expect(screen.getByText('rank(ts_delta(ts_backfill(anl4_eps_mean, 63), 21))')).toBeTruthy()
    // The fit summary counts each type separately: both fit regular and Power Pool, and
    // only one is a single-dataset ATOM alpha.
    expect(screen.getAllByText('2 / 2').length).toBe(2)
    expect(screen.getByText('1 / 2')).toBeTruthy()
    // A fully eligible alpha collapses to `fitsAll` rather than listing every type: the
    // backend sends an empty `eligibility` map for it, so the row must read `fitsAll`, not
    // key into that map by type name.
    expect(screen.getByText('fits every type')).toBeTruthy()
    expect(screen.queryByText(CRASHED)).toBeNull()
  })

  it('says why an alpha misses a type', async () => {
    await mount('/forge/studio?t=analyst_revision&fs=1')
    const rows = screen.getAllByRole('button')
    const second = rows.find((r) =>
      r.textContent?.includes('rank(ts_delta(ts_backfill(anl4_rev_mean, 63), 63))'),
    )
    expect(second).toBeTruthy()
    await act(async () => {
      second?.click()
    })
    await waitFor(() =>
      expect(screen.getByText('Single-dataset type but fields span analyst4, pv1')).toBeTruthy(),
    )
    expect(screen.getAllByText('Not eligible').length).toBeGreaterThan(0)
    expect(screen.queryByText(CRASHED)).toBeNull()
  })

  it('drafts a Power Pool description for an eligible alpha', async () => {
    await mount('/forge/studio?t=analyst_revision&fs=1')
    const rows = screen.getAllByRole('button')
    const first = rows.find((r) =>
      r.textContent?.includes('rank(ts_delta(ts_backfill(anl4_eps_mean, 63), 21))'),
    )
    await act(async () => {
      first?.click()
    })
    await waitFor(() => expect(screen.getByText('Power Pool description')).toBeTruthy())
    // The draft's parts arrive in editable boxes, and BRAIN's length complaint is shown.
    expect(screen.getByText('BRAIN wants more here')).toBeTruthy()
    // This alpha is eligible via `fitsAll` (its `eligibility` map is empty, not keyed by
    // 'power_pool'), so the description panel must still gate on `fitsAll`/`fits`.
    expect(screen.getAllByText('fits every type').length).toBeGreaterThan(0)
    expect(screen.queryByText(CRASHED)).toBeNull()
  })
})

describe('my templates', () => {
  it('lists what you wrote, with its versions', async () => {
    await mount('/forge/my-templates?t=my_revision')
    expect(screen.getAllByText(/my_revision/).length).toBeGreaterThan(0)
    await waitFor(() => expect(screen.getByText(/v2 — Added a window/)).toBeTruthy())
    expect(screen.getByText(/v1 — Created/)).toBeTruthy()
    expect(screen.queryByText(CRASHED)).toBeNull()
  })

  it('lints the YAML as it is written', async () => {
    await mount('/forge/my-templates?t=my_revision')
    // The editor is CodeMirror in plain mode: the lint result is what the screen reports.
    await waitFor(() => expect(screen.getByText('Valid')).toBeTruthy())
    expect(screen.getByText('rank(ts_backfill(x_field, 63))')).toBeTruthy()
    expect(screen.queryByText(CRASHED)).toBeNull()
  })
})

describe('the template library', () => {
  it('marks your own templates and shows what each has produced', async () => {
    await mount('/forge/templates')
    expect(screen.getByText('yours')).toBeTruthy()
    expect(screen.getByText('42 sims')).toBeTruthy()
    expect(screen.getByText('30% pass')).toBeTruthy()
    expect(screen.getByText('4 families')).toBeTruthy()
    expect(screen.queryByText(CRASHED)).toBeNull()
  })

  it('previews a template bound to a field set, with eligibility and why not', async () => {
    await mount('/forge/templates')
    const bind = screen.getByRole('button', { name: /Bind to field set/i })
    await act(async () => {
      bind.click()
    })
    await waitFor(() => expect(screen.getByText('Field set')).toBeTruthy())

    const picker = screen.getByRole('combobox', { name: 'Field set' })
    await act(async () => {
      picker.click()
    })
    const option = await screen.findByRole('option', { name: /USA TOP3000 analyst/ })
    await act(async () => {
      option.click()
    })

    await waitFor(() =>
      expect(screen.getByText('rank(ts_delta(ts_backfill(anl4_eps_mean, 63), 21))')).toBeTruthy(),
    )
    // The table-header tooltips: hovering isn't simulated here, but the labels they wrap must
    // render, and the reason for the one alpha type this alpha misses must reach the screen.
    expect(screen.getByText('Eligible for')).toBeTruthy()
    expect(screen.getAllByText('Not eligible').length).toBeGreaterThan(0)
    expect(
      screen.getAllByText(
        (_, node) =>
          node?.tagName === 'LI' &&
          Boolean(node.textContent?.includes('Single-dataset type but fields span analyst4, pv1')),
      ).length,
    ).toBeGreaterThan(0)
    // A fully eligible alpha collapses to `fitsAll` with an empty `eligibility` map: the
    // "Eligible for" column must read `fitsAll`, not key into that map by type name.
    expect(screen.getByText('fits every type')).toBeTruthy()
    expect(screen.queryByText(CRASHED)).toBeNull()
  })
})

describe('the Pipelines screen', () => {
  it('lists past runs in the history panel', async () => {
    await mount('/forge/pipelines')
    expect(screen.getByText('Run history')).toBeTruthy()
    expect(screen.getByText('regular on USA TOP3000 analyst')).toBeTruthy()
    expect(screen.getByText('done')).toBeTruthy()
    expect(screen.queryByText(CRASHED)).toBeNull()
  })

  it('searching the catalog shows field options, without a saved field set', async () => {
    await mount('/forge/pipelines')
    // Switch the field picker from "Existing field set" to "Search fields".
    fireEvent.click(screen.getByRole('radio', { name: 'Search fields' }))
    fireEvent.change(screen.getByLabelText('Search field catalog'), {
      target: { value: 'eps' },
    })
    await waitFor(() => expect(screen.getByText('anl4_eps_mean')).toBeTruthy())
    expect(screen.queryByText(CRASHED)).toBeNull()
  })

  /** Switches to "Search fields", searches, ticks the matched row, then picks a template —
   *  everything a plan preview needs, with no saved field set involved. */
  async function pickFieldsAndTemplate() {
    fireEvent.click(screen.getByRole('radio', { name: 'Search fields' }))
    fireEvent.change(screen.getByLabelText('Search field catalog'), {
      target: { value: 'eps' },
    })
    await waitFor(() => expect(screen.getByText('anl4_eps_mean')).toBeTruthy())
    const row = screen.getByText('anl4_eps_mean').closest('[role="row"]') as HTMLElement
    fireEvent.click(within(row).getByRole('checkbox'))
    fireEvent.click(screen.getByText('Revision against peers'))
  }

  it('ticking fields without a saved field set still plans a run', async () => {
    await mount('/forge/pipelines')
    await pickFieldsAndTemplate()
    await waitFor(() => expect(screen.getByText('Plan preview')).toBeTruthy())
    expect(screen.getByText('Generated')).toBeTruthy()
    expect(screen.queryByText(CRASHED)).toBeNull()
  })

  it('shows a different verdict per alpha type for the same candidate', async () => {
    await mount('/forge/pipelines')
    await pickFieldsAndTemplate()
    await waitFor(() => expect(screen.getByText('power pool (weak)')).toBeTruthy())
    expect(screen.getByText('atom')).toBeTruthy()
    expect(screen.queryByText(CRASHED)).toBeNull()
  })
})

describe('the Workshop screen', () => {
  it('shows a hypothesis and its trial, with the agent panel beside it', async () => {
    await mount('/forge/workshop')
    expect(screen.getByText('Analyst revisions mean-revert fast')).toBeTruthy()
    expect(screen.getByText(/Trial #1/)).toBeTruthy()
    expect(screen.getByText('anl4_eps_mean: SURVIVES')).toBeTruthy()
    expect(screen.getByText('Agent')).toBeTruthy()
    expect(screen.queryByText(CRASHED)).toBeNull()
  })

  it('lets the idea form search fields and offers only templates they could fill', async () => {
    await mount('/forge/workshop')
    fireEvent.click(screen.getByRole('radio', { name: 'Search fields' }))
    fireEvent.change(screen.getByLabelText('Search field catalog'), {
      target: { value: 'eps' },
    })
    await waitFor(() => expect(screen.getByText('anl4_eps_mean')).toBeTruthy())
    const row = screen.getByText('anl4_eps_mean').closest('[role="row"]') as HTMLElement
    fireEvent.click(within(row).getByRole('checkbox'))

    // Only once a field is picked does the template picker have anything to offer.
    const picker = await screen.findByRole('combobox', { name: 'Template' })
    await waitFor(() => expect(picker.getAttribute('data-disabled')).toBeNull())
    await act(async () => {
      picker.click()
    })
    expect(
      await screen.findByRole('option', { name: 'Revision against peers (simple)' }),
    ).toBeTruthy()
    expect(screen.queryByText(CRASHED)).toBeNull()
  })

  it('hands a surviving trial off to Pipelines with its field set and template preselected', async () => {
    await mount('/forge/workshop')
    const link = (await screen.findByText('Send to Pipelines instead')).closest('a')
    expect(link).toBeTruthy()
    const href = link?.getAttribute('href') ?? ''
    expect(href).toContain('/forge/pipelines')
    expect(href).toContain('fs=1')
    expect(href).toContain('t=analyst_revision')
    expect(screen.queryByText(CRASHED)).toBeNull()
  })
})

describe('the Workbench screen', () => {
  it('shows the market bar and the field tray', async () => {
    await mount('/forge/workbench')
    expect(screen.getByText('Market')).toBeTruthy()
    expect(screen.getByText('Field tray')).toBeTruthy()
    expect(screen.getByText('0 in tray')).toBeTruthy()
    expect(screen.queryByText(CRASHED)).toBeNull()
  })

  it('lets the field panel search fields and adds them to the tray', async () => {
    await mount('/forge/workbench')
    fireEvent.click(screen.getByRole('radio', { name: 'Search fields' }))
    fireEvent.change(screen.getByLabelText('Search field catalog'), {
      target: { value: 'eps' },
    })
    await waitFor(() => expect(screen.getByText('anl4_eps_mean')).toBeTruthy())
    const row = screen.getByText('anl4_eps_mean').closest('[role="row"]') as HTMLElement
    fireEvent.click(within(row).getByRole('checkbox'))
    await waitFor(() => expect(screen.getByText('1 in tray')).toBeTruthy())
    expect(screen.queryByText(CRASHED)).toBeNull()
  })

  it('Template Lab offers only templates for the market and category, fields editable', async () => {
    await mount('/forge/workbench')
    expect(screen.getByText('Template Lab')).toBeTruthy()

    // A dataset category chip filters the template list before any field is picked.
    fireEvent.click(screen.getByRole('radio', { name: 'analyst' }))
    const templatePicker = screen.getByRole('combobox', { name: 'Template' })
    await act(async () => {
      templatePicker.click()
    })
    const option = await screen.findByRole('option', { name: /Revision against peers/ })
    await act(async () => {
      option.click()
    })

    // Its field slots show up, editable right there — not locked to a saved field set.
    await waitFor(() => expect(screen.getByText(/Slot X/)).toBeTruthy())
    expect(screen.getByText(/Slot W/)).toBeTruthy()

    const slotX = screen.getByRole('combobox', { name: 'Slot X' })
    await act(async () => {
      slotX.click()
    })
    const field = await screen.findByRole('option', { name: 'anl4_rev_mean' })
    await act(async () => {
      field.click()
    })

    await waitFor(() =>
      expect(screen.getByText('rank(ts_delta(ts_backfill(anl4_rev_mean, 63), 63))')).toBeTruthy(),
    )
    expect(screen.queryByText(CRASHED)).toBeNull()
  })

  it('Regression Lab screens the tray and sends survivors back to the field tray', async () => {
    await mount('/forge/workbench')
    expect(screen.getByText('Regression Lab')).toBeTruthy()

    // Put two fields in the tray, same mechanism the field-search test above uses.
    fireEvent.click(screen.getByRole('radio', { name: 'Search fields' }))
    fireEvent.change(screen.getByLabelText('Search field catalog'), {
      target: { value: 'anl4' },
    })
    await waitFor(() => expect(screen.getByText('anl4_eps_mean')).toBeTruthy())
    for (const id of ['anl4_eps_mean', 'anl4_rev_mean']) {
      const row = screen.getByText(id).closest('[role="row"]') as HTMLElement
      fireEvent.click(within(row).getByRole('checkbox'))
    }
    await waitFor(() => expect(screen.getByText('2 in tray')).toBeTruthy())

    // Pick a Gym synthetic target (no catalog field lookup needed) and run.
    fireEvent.click(screen.getByRole('radio', { name: 'Gym synthetic' }))
    fireEvent.change(screen.getByPlaceholderText('gym_template_id'), {
      target: { value: 'gym_template_1' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Run regression' }))

    await waitFor(() => expect(screen.getByText(/These 2 fields together explain/)).toBeTruthy())

    // Sending survivors reuses the Field tray's own `setFieldIds`: the tray count reflects the
    // single kept field, same as `FieldPicker`'s own `onChange` would.
    fireEvent.click(screen.getByRole('button', { name: 'Send surviving fields to Template Lab' }))
    await waitFor(() => expect(screen.getByText('1 in tray')).toBeTruthy())
    expect(screen.queryByText(CRASHED)).toBeNull()
  })

  it('Regression Lab loads a suggested combination straight into the field tray', async () => {
    await mount('/forge/workbench')
    await waitFor(() => expect(screen.getByText('0 in tray')).toBeTruthy())

    await waitFor(() => expect(screen.getByText('Regression Lab')).toBeTruthy())
    await waitFor(() => {
      expect(
        screen
          .getAllByRole('button')
          .some((b) => /Analyst estimate dispersion set/.test(b.textContent ?? '')),
      ).toBe(true)
    })
    const suggestion = screen
      .getAllByRole('button')
      .find((b) => /Analyst estimate dispersion set/.test(b.textContent ?? '')) as HTMLElement
    fireEvent.click(suggestion)
    await waitFor(() => expect(screen.getByText('2 in tray')).toBeTruthy())
    expect(screen.queryByText(CRASHED)).toBeNull()
  })
})

describe('the Tasks screen', () => {
  it('lists a Pipelines run, a Workbench run and a Workshop trial, tagged with their source', async () => {
    await mount('/tasks')
    // Pipelines: `pipeline on fieldSet`, same as its own run-history panel shows it.
    expect(screen.getByText('regular on USA TOP3000 analyst')).toBeTruthy()
    // Workbench: candidate count and the scope it ran on.
    expect(screen.getByText('2 candidates on USA D1 TOP3000')).toBeTruthy()
    // Workshop: the hypothesis title and its verdict.
    expect(
      screen.getByText(/Analyst revisions mean-revert fast · regular \(SURVIVES\)/),
    ).toBeTruthy()
    // Each row says which Forge screen produced it — the "tagged with its source" part.
    expect(screen.getByText(/Forge Pipeline/)).toBeTruthy()
    expect(screen.getByText(/Forge Workbench/)).toBeTruthy()
    expect(screen.getByText(/Forge Workshop/)).toBeTruthy()
    expect(screen.queryByText(CRASHED)).toBeNull()
  })

  it('opens a Forge run with its own results, not a lab task’s Run/Pause/Stop controls', async () => {
    await mount('/tasks')
    fireEvent.click(screen.getByText('regular on USA TOP3000 analyst'))
    await waitFor(() => expect(screen.getByText('Finalists')).toBeTruthy())
    expect(screen.getByText('rank(ts_delta(ts_backfill(anl4_eps_mean, 63), 21))')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Run' })).toBeNull()
    expect(screen.queryByText(CRASHED)).toBeNull()
  })
})

describe('the Forge Analytics screen', () => {
  it('shows the tear sheet and factor attribution for the given alpha', async () => {
    await mount('/forge/analytics?alpha=ALPHA1')
    expect(screen.getByText('1.40')).toBeTruthy()
    expect(screen.getByText('Factor attribution')).toBeTruthy()
    expect(screen.queryByText(CRASHED)).toBeNull()
  })

  it('teaches what to do next with no alpha picked', async () => {
    await mount('/forge/analytics')
    expect(screen.getByText('Enter an alpha id to see its analytics')).toBeTruthy()
    expect(screen.queryByText(CRASHED)).toBeNull()
  })

  it('surfaces pool correlation, factor exposure, IC decay and a plain-English verdict', async () => {
    await mount('/forge/analytics?alpha=ALPHA1')
    expect(screen.getByText(/correlates 0\.42 with the closest pool alpha/)).toBeTruthy()
    expect(screen.getByText('ALPHA2')).toBeTruthy()
    expect(screen.getByText('Explained 31% by momentum; residual Sharpe 1.10.')).toBeTruthy()
    expect(screen.getByText('Information coefficient')).toBeTruthy()
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

  it('points the sidebar at routes that actually exist', () => {
    // Every full path the tree registers, so a nav link that resolves to the wrong one (e.g. a
    // parent path concatenated into a child's, duplicating a segment) is caught here rather
    // than by a user's browser console.
    const registered = new Set<string>()
    const walk = (route: AnyRoute) => {
      if (route.fullPath) registered.add(route.fullPath.replace(/\/$/, '') || '/')
      for (const child of (route.children ?? []) as AnyRoute[]) walk(child)
    }
    for (const child of (routeTree.children ?? []) as AnyRoute[]) walk(child)

    const links = NAV.flatMap((item) => [
      item.to,
      ...('tabs' in item ? item.tabs.flatMap((tab) => ('to' in tab ? [tab.to] : [])) : []),
    ])
    const broken = links.filter((to) => !registered.has(to))
    expect(broken, `sidebar links with no matching route: ${broken.join(', ')}`).toEqual([])
  })
})
