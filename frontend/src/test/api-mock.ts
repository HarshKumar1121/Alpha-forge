/**
 * A backend that answers from fixtures, so a screen can be rendered without one running.
 *
 * Only the endpoints a screen needs to reach its *loaded* state are fixtured. Anything else
 * answers 404, which `http.ts` turns into an `ApiError` and screens show as a notice — a path
 * they are written for. Returning a plausible-looking but wrong-shaped body instead would
 * fail screens for the mock's sloppiness rather than their own.
 */

import { vi } from 'vitest'

const field = (id: string, dataset: string, category: string) => ({
  field_id: id,
  dataset_id: dataset,
  category_id: category,
  category_name: category,
  subcategory_id: `${category}1`,
  subcategory_name: category,
  description: `${id} described`,
  field_type: 'MATRIX',
  coverage: 0.84,
  date_coverage: 0.91,
  user_count: 120,
  alpha_count: 4200,
  pyramid_multiplier: 1.2,
  themes: '[]',
  date_created: '2024-02-01',
})

const FIELDS = [
  field('anl4_eps_mean', 'analyst4', 'analyst'),
  field('anl4_rev_mean', 'analyst4', 'analyst'),
  field('close', 'pv1', 'pv'),
]

const dataset = (id: string, category: string) => ({
  dataset_id: id,
  name: `${id} data`,
  description: `${id} described`,
  category_id: category,
  category_name: category,
  subcategory_id: `${category}1`,
  subcategory_name: category,
  coverage: 0.8,
  value_score: 2.4,
  user_count: 900,
  alpha_count: 6000,
  field_count: 2,
  pyramid_multiplier: 1.2,
})

const mine = { sims: 3, alphas: 2, checked: 2, passed: 1, passRate: 0.5 }

const forgeDataset = (id: string, category: string) => ({
  datasetId: id,
  name: `${id} data`,
  description: `${id} described`,
  categoryId: category,
  categoryName: category,
  subcategoryName: category,
  coverage: 0.8,
  valueScore: 2.4,
  userCount: 900,
  alphaCount: 6000,
  fieldCount: 2,
  pyramidMultiplier: 1.2,
  accountMultiplier: 1.6,
  pyramidLit: false,
  crowding: 0.42,
  mine,
})

const choice = (value: string | number) => ({ value, label: String(value) })

/** What `GET /api/forge/scopes` serves: one instrument type, two markets, one synced. */
const SCOPE_TREE = {
  instrumentTypes: [
    {
      value: 'EQUITY',
      label: 'Equity',
      regions: [
        {
          value: 'USA',
          label: 'USA',
          regionAgnostic: false,
          delays: [
            {
              value: 1,
              label: '1',
              universes: [
                {
                  value: 'TOP3000',
                  label: 'TOP3000',
                  synced: true,
                  fields: 3,
                  neutralizations: [choice('SUBINDUSTRY'), choice('NONE')],
                },
              ],
            },
          ],
        },
        {
          value: 'GLB',
          label: 'GLB',
          regionAgnostic: false,
          delays: [
            {
              value: 1,
              label: '1',
              universes: [
                {
                  value: 'MINVOL1M',
                  label: 'MINVOL1M',
                  synced: false,
                  fields: 0,
                  neutralizations: [choice('COUNTRY')],
                },
              ],
            },
          ],
        },
      ],
    },
  ],
}

const SETTINGS_FIELD = (name: string, choices: { value: string | number; label: string }[]) => ({
  name,
  label: name,
  type: 'choice',
  required: true,
  readOnly: false,
  choices,
  dependsOn: [],
  blocked: false,
  min: null,
  max: null,
})

const ENGINE = {
  awake: 'idle',
  dailyLimitHit: false,
  inFlight: {},
  lastPause: null,
  maxBatch: 10,
  minutesLeft: null,
  queued: {},
  queuedTotal: 0,
  quickAllowed: true,
  quotas: {},
  sessionLost: false,
  slots: 4,
  slotsFree: 4,
  slotsUsed: 0,
}

/** Keyed by pathname; a value is the JSON body served for any method. */
export const FIXTURES: Record<string, unknown> = {
  // --- shell ---------------------------------------------------------------
  // `Shell` swaps the whole workspace for "the backend is not answering" until this answers,
  // so without it every route in this file would be the same empty screen.
  '/api/today': {
    step: 'ready',
    you: {
      signedIn: true,
      userId: 'TESTUSER',
      fullName: 'Test User',
      email: 'test@example.invalid',
      permissions: ['MULTI_SIMULATION', 'REGION_AGNOSTIC'],
      canRunTenAtOnce: true,
      verificationUrl: null,
    },
    simulations: {
      engine: ENGINE,
      exact: false,
      headline: '120 of 5,000 used',
      limit: 5000,
      queued: 0,
      remaining: 4880,
      resetsAt: '2026-11-01T04:00:00Z',
      resetsInSeconds: 3600,
      unspoken: 0,
      used: 120,
    },
    catalog: {
      anySynced: true,
      fields: 3,
      running: null,
      scope: 'EQUITY/USA/D1/TOP3000',
      synced: true,
    },
    assistant: {
      budget: [],
      enabledKeys: 0,
      headline: 'No key yet',
      keys: 0,
      requestsRemainingToday: 0,
      resetsInSeconds: null,
    },
  },
  '/api/today/bar': {
    expiresInSeconds: 7200,
    fullName: 'Test User',
    resetsInSeconds: 3600,
    signedIn: true,
    simulations: { exact: false, limit: 5000, queued: 0, remaining: 4880 },
  },
  '/api/health': { ok: true, version: 'test', database: true },
  '/api/preferences': {
    autoUpdate: false,
    defaultCores: 4,
    keepAwake: false,
    lendIdleCores: false,
    pnlDownload: false,
    updateCheckHours: 24,
  },
  '/api/tasks': { busy: false, failed: 0, progress: null, running: 0, tasks: [] },
  '/api/simulations/active': [],
  '/api/simulations/engine': ENGINE,
  '/api/update': { current: 'test', latest: null, updating: false },
  '/api/competitions': { competitions: [] },
  '/api/auth/settings-options': {
    fields: {
      region: SETTINGS_FIELD('region', [choice('USA'), choice('GLB')]),
      delay: SETTINGS_FIELD('delay', [choice(0), choice(1)]),
      universe: SETTINGS_FIELD('universe', [choice('TOP3000')]),
      neutralization: SETTINGS_FIELD('neutralization', [choice('SUBINDUSTRY')]),
    },
    problems: [],
    missing: [],
  },

  // --- Data Explorer -------------------------------------------------------
  '/api/catalog/scopes': [
    {
      instrument_type: 'EQUITY',
      region: 'USA',
      delay: 1,
      universe: 'TOP3000',
      fields: 3,
      synced_at: '2026-10-30T12:00:00Z',
    },
  ],
  '/api/catalog/counts': { fields: 3, datasets: 2, categories: 2, subcategories: 2 },
  '/api/catalog/size': { used_bytes: 4096 },
  '/api/catalog/stats': {
    coverage_min: 0.1,
    coverage_max: 1,
    coverage_median: 0.8,
    alpha_count_min: 0,
    alpha_count_max: 9000,
    user_count_min: 0,
    user_count_max: 900,
    pyramid_multiplier_min: 1,
    pyramid_multiplier_max: 2,
    date_coverage_min: 0.2,
    date_coverage_max: 1,
    date_added: [{ month: '2024-02-01', fields: 3 }],
  },
  '/api/catalog/fields': { total: FIELDS.length, limit: 100, offset: 0, results: FIELDS },
  '/api/catalog/facets': {
    categories: [{ id: 'analyst', name: 'Analyst', n: 2 }],
    subcategories: [{ id: 'analyst1', name: 'Analyst', category_id: 'analyst', n: 2 }],
    datasets: [{ id: 'analyst4', category_id: 'analyst', subcategory_id: 'analyst1', n: 2 }],
    types: [{ id: 'MATRIX', n: 3 }],
    availability: { region_agnostic: 0, region_exclusive: 0 },
    date_added: [{ month: '2024-02-01', fields: 3 }],
  },
  '/api/catalog/datasets': [dataset('analyst4', 'analyst'), dataset('pv1', 'pv')],
  '/api/catalog/markets': [
    { instrument_type: 'EQUITY', region: 'USA', delay: 1, universe: 'TOP3000' },
  ],
  '/api/catalog/pyramids': {
    columns: [{ region: 'USA', delay: 1 }],
    categories: [{ id: 'analyst', name: 'Analyst' }],
    cells: [
      {
        category_id: 'analyst',
        region: 'USA',
        delay: 1,
        multiplier: 1.6,
        alpha_count: 1,
        lit: false,
        synced: true,
      },
    ],
    quarter: { start: '2026-10-01', end: '2027-01-01', today: '2026-10-31' },
    alphas_per_pyramid: 3,
  },

  // --- Forge ---------------------------------------------------------------
  '/api/forge/scopes': SCOPE_TREE,
  '/api/forge/data/datasets': {
    datasets: [forgeDataset('analyst4', 'analyst'), forgeDataset('pv1', 'pv')],
    unparsed: 0,
    pyramidsLive: true,
  },
  '/api/forge/data/datasets/analyst4': {
    scope: 'EQUITY/USA/D1/TOP3000',
    dataset: forgeDataset('analyst4', 'analyst'),
    coverageBins: Array.from({ length: 10 }, (_, i) => ({
      low: i / 10,
      high: (i + 1) / 10,
      fields: i,
    })),
    fieldTypes: [{ fieldType: 'MATRIX', fields: 2 }],
    scatter: [
      {
        datasetId: 'analyst4',
        name: 'analyst4 data',
        categoryId: 'analyst',
        crowding: 0.42,
        multiplier: 1.6,
        fieldCount: 2,
        current: true,
      },
      {
        datasetId: 'pv1',
        name: 'pv1 data',
        categoryId: 'pv',
        crowding: 0.99,
        multiplier: 1.1,
        fieldCount: 2,
        current: false,
      },
    ],
    history: {
      ...mine,
      structuralFamilies: 2,
      byUniverse: [{ universe: 'TOP3000', ...mine }],
    },
    pyramid: {
      categoryId: 'analyst',
      multiplier: 1.6,
      alphaCount: 1,
      needed: 3,
      lit: false,
    },
  },
  '/api/forge/data/fields/extras': {
    fields: Object.fromEntries(
      FIELDS.map((f) => [f.field_id, { crowding: 0.5, mine: { ...mine } }]),
    ),
  },
  '/api/forge/fieldsets': [
    {
      id: 1,
      name: 'USA TOP3000 analyst',
      scope: { instrumentType: 'EQUITY', region: 'USA', delay: 1, universe: 'TOP3000' },
      kind: 'manual',
      fieldIds: ['anl4_eps_mean', 'anl4_rev_mean'],
      filter: null,
      notes: 'revision ideas',
      createdAt: '2026-10-30T12:00:00Z',
      updatedAt: '2026-10-30T12:00:00Z',
    },
  ],
  '/api/forge/fieldsets/1': {
    id: 1,
    name: 'USA TOP3000 analyst',
    scope: { instrumentType: 'EQUITY', region: 'USA', delay: 1, universe: 'TOP3000' },
    kind: 'manual',
    fieldIds: ['anl4_eps_mean', 'anl4_rev_mean'],
    filter: null,
    notes: 'revision ideas',
    createdAt: '2026-10-30T12:00:00Z',
    updatedAt: '2026-10-30T12:00:00Z',
    fields: FIELDS.slice(0, 2),
    total: 2,
    missing: [],
  },
  '/api/forge/quota': {
    day: '2026-10-31',
    resetsInSeconds: 3600,
    limit: 5000,
    safetyMargin: 200,
    budget: 4800,
    spent: 120,
    unrecorded: 0,
    cached: 4,
    queued: 0,
    remaining: 4680,
    exact: false,
    brainRemaining: null,
    marginWarning: false,
    dayElapsed: 0.5,
    timezone: 'Asia/Kolkata',
    pacing: { maxPerHour: 400, lastHour: 10, quietNow: false, quietHours: [] },
    lanes: [
      {
        lane: 'explore',
        about: 'probes',
        share: 0.5,
        planned: 2400,
        effectiveCap: 2400,
        spent: 120,
        queued: 0,
        cached: 4,
        remaining: 2280,
      },
    ],
    hourly: [{ hour: '09:00', spent: 12 }],
  },
  '/api/forge/templates': { templates: [], alphaTypes: [], errors: [] },
  '/api/forge/gym/catalog': { fields: [], operators: [] },
}

/** Paths whose prefix is enough, for the ids a route carries. */
const PREFIXES: [string, unknown][] = [
  [
    '/api/catalog/fields/',
    {
      ...FIELDS[0],
      instrument_type: 'EQUITY',
      region: 'USA',
      delay: 1,
      universe: 'TOP3000',
      synced_at: null,
    },
  ],
]

export function bodyFor(pathname: string): unknown | undefined {
  if (pathname in FIXTURES) return FIXTURES[pathname]
  for (const [prefix, body] of PREFIXES) if (pathname.startsWith(prefix)) return body
  return undefined
}

/** Replace `fetch` for the test run. Returns the paths that were asked for. */
export function installApiMock(): { asked: string[]; unmocked: string[] } {
  const asked: string[] = []
  const unmocked: string[] = []
  vi.stubGlobal('fetch', async (input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    const { pathname } = new URL(url, 'http://localhost')
    asked.push(pathname)
    const body = bodyFor(pathname)
    if (body === undefined) {
      unmocked.push(pathname)
      return new Response(
        JSON.stringify({ detail: { code: 'not_mocked', message: `No fixture for ${pathname}` } }),
        { status: 404, headers: { 'content-type': 'application/json' } },
      )
    }
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })
  })
  return { asked, unmocked }
}
