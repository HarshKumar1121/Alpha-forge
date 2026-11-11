/**
 * Code-based route tree. Screens are lazy chunks; sub-tabs and ids are typed path params.
 */

import {
  createRootRoute,
  createRoute,
  createRouter,
  type ErrorComponentProps,
  lazyRouteComponent,
  type RouterHistory,
  redirect,
} from '@tanstack/react-router'
import { INSTRUMENT_TYPE } from '@/api/platform' // forge: the dataset route's scope defaults
import { DEFAULT_SCOPE } from '@/lib/scope' // forge: same
import { MOD } from '@/shell/desktop'
import { Shell } from '@/shell/shell'
import { Button, Empty, ErrorNotice, Page, Panel } from '@/ui/kit'

const root = createRootRoute({ component: Shell })

const index = createRoute({
  getParentRoute: () => root,
  path: '/',
  beforeLoad: () => {
    throw redirect({ to: '/forge/today' }) // forge: Today is home (playbook v2 Phase 3)
  },
})

const dashboard = createRoute({
  getParentRoute: () => root,
  path: '/dashboard',
  component: lazyRouteComponent(() => import('@/screens/dashboard'), 'DashboardScreen'),
})

const matrix = createRoute({
  getParentRoute: () => root,
  path: '/matrix',
  component: lazyRouteComponent(() => import('@/screens/matrix'), 'MatrixScreen'),
})

const data = createRoute({
  getParentRoute: () => root,
  path: '/data',
  component: lazyRouteComponent(() => import('@/screens/data'), 'DataScreen'),
})

const labs = createRoute({ getParentRoute: () => root, path: '/labs' })
const labsIndex = createRoute({
  getParentRoute: () => labs,
  path: '/',
  component: lazyRouteComponent(() => import('@/screens/research-labs'), 'ResearchLabsScreen'),
})
const searchLab = createRoute({
  getParentRoute: () => labs,
  path: 'search',
  component: lazyRouteComponent(() => import('@/screens/research-labs/search'), 'SearchLabScreen'),
})
const templateLab = createRoute({
  getParentRoute: () => labs,
  path: 'template',
  component: lazyRouteComponent(
    () => import('@/screens/research-labs/template'),
    'TemplateLabScreen',
  ),
})
const evolutionLab = createRoute({
  getParentRoute: () => labs,
  path: 'evolution',
  component: lazyRouteComponent(
    () => import('@/screens/research-labs/evolution'),
    'EvolutionLabScreen',
  ),
})
const powerPoolLab = createRoute({
  getParentRoute: () => labs,
  path: 'power-pool',
  component: lazyRouteComponent(
    () => import('@/screens/research-labs/power-pool'),
    'PowerPoolLabScreen',
  ),
})
const superAlphaLab = createRoute({
  getParentRoute: () => labs,
  path: 'super-alpha',
  component: lazyRouteComponent(
    () => import('@/screens/research-labs/super-alpha'),
    'SuperAlphaLabScreen',
  ),
})
const tools = createRoute({ getParentRoute: () => root, path: '/tools' })
const toolsIndex = createRoute({
  getParentRoute: () => tools,
  path: '/',
  component: lazyRouteComponent(() => import('@/screens/tools'), 'ToolsScreen'),
})
const settingsSampler = createRoute({
  getParentRoute: () => tools,
  path: 'settings-sampler',
  // Tools take typed search params: an id belongs in the URL so a link from the Alpha screen
  // is shareable, unlike the multi-item picks that use a handoff store.
  validateSearch: (search: Record<string, unknown>) => ({
    alpha: typeof search['alpha'] === 'string' ? search['alpha'] : undefined,
  }),
  component: lazyRouteComponent(
    () => import('@/screens/tools/settings-sampler'),
    'SettingsSamplerScreen',
  ),
})

const correlationBreaker = createRoute({
  getParentRoute: () => tools,
  path: 'correlation-breaker',
  validateSearch: (search: Record<string, unknown>) => ({
    alpha: typeof search['alpha'] === 'string' ? search['alpha'] : undefined,
  }),
  component: lazyRouteComponent(
    () => import('@/screens/tools/correlation-breaker'),
    'CorrelationBreakerScreen',
  ),
})

const fieldsSampler = createRoute({
  getParentRoute: () => tools,
  path: 'fields-sampler',
  validateSearch: (search: Record<string, unknown>) => ({
    alpha: typeof search['alpha'] === 'string' ? search['alpha'] : undefined,
  }),
  component: lazyRouteComponent(
    () => import('@/screens/tools/fields-sampler'),
    'FieldsSamplerScreen',
  ),
})

// forge: the Forge section's routes; screens live in screens/forge/. No route-registry seam exists.
const forge = createRoute({ getParentRoute: () => root, path: '/forge' })
const forgeToday = createRoute({
  getParentRoute: () => forge,
  path: 'today',
  component: lazyRouteComponent(() => import('@/screens/forge/today'), 'TodayScreen'),
})
const forgeGym = createRoute({
  getParentRoute: () => forge,
  path: 'gym',
  // An expression to start from, e.g. a template sent by the Template Library's "Try on Gym".
  validateSearch: (search: Record<string, unknown>) => ({
    expr: typeof search['expr'] === 'string' ? search['expr'] : undefined,
  }),
  component: lazyRouteComponent(() => import('@/screens/forge/gym'), 'GymPlaygroundScreen'),
})
const forgeTemplates = createRoute({
  getParentRoute: () => forge,
  path: 'templates',
  validateSearch: (search: Record<string, unknown>) => ({
    t: typeof search['t'] === 'string' ? search['t'] : undefined,
  }),
  component: lazyRouteComponent(() => import('@/screens/forge/templates'), 'TemplateLibraryScreen'),
})

// forge: Template Studio merged into Workbench's Template Lab (same `studio.preview()` engine,
// one UI instead of two) — this route only forwards whichever template/field set/nugget an old
// link named, so nothing bookmarked or linked from Workshop breaks.
const forgeStudio = createRoute({
  getParentRoute: () => forge,
  path: 'studio',
  validateSearch: (search: Record<string, unknown>) => ({
    t: typeof search['t'] === 'string' ? search['t'] : undefined,
    fs: typeof search['fs'] === 'number' ? search['fs'] : undefined,
    n: typeof search['n'] === 'string' ? search['n'] : undefined,
  }),
  beforeLoad: ({ search }) => {
    throw redirect({ to: '/forge/workbench', search: { t: search.t, fs: search.fs, n: search.n } })
  },
})
const forgeMyTemplates = createRoute({
  getParentRoute: () => forge,
  path: 'my-templates',
  validateSearch: (search: Record<string, unknown>) => ({
    t: typeof search['t'] === 'string' ? search['t'] : undefined,
  }),
  component: lazyRouteComponent(() => import('@/screens/forge/my-templates'), 'MyTemplatesScreen'),
})
const forgePipelines = createRoute({
  getParentRoute: () => forge,
  path: 'pipelines',
  // Which template and field set to preselect, e.g. sent by Template Studio's "Send to Pipelines".
  validateSearch: (search: Record<string, unknown>) => ({
    t: typeof search['t'] === 'string' ? search['t'] : undefined,
    fs: typeof search['fs'] === 'number' ? search['fs'] : undefined,
  }),
  component: lazyRouteComponent(() => import('@/screens/forge/pipelines'), 'PipelinesScreen'),
})
const forgeBoard = createRoute({
  getParentRoute: () => forge,
  path: 'board',
  component: lazyRouteComponent(() => import('@/screens/forge/board'), 'BoardScreen'),
})
const forgeBreakdown = createRoute({
  getParentRoute: () => forge,
  path: 'breakdown/$taskId',
  component: lazyRouteComponent(
    () => import('@/screens/forge/results-breakdown'),
    'ResultsBreakdownScreen',
  ),
})
const forgeSubmitHelper = createRoute({
  getParentRoute: () => forge,
  path: 'submit-helper',
  component: lazyRouteComponent(
    () => import('@/screens/forge/submit-helper'),
    'SubmitHelperScreen',
  ),
})
const forgeWorkshop = createRoute({
  getParentRoute: () => forge,
  path: 'workshop',
  component: lazyRouteComponent(() => import('@/screens/forge/workshop'), 'WorkshopScreen'),
})
const forgeFieldSets = createRoute({
  getParentRoute: () => forge,
  path: 'fieldsets',
  component: lazyRouteComponent(() => import('@/screens/forge/fieldsets'), 'FieldSetsScreen'),
})
const forgeNuggets = createRoute({
  getParentRoute: () => forge,
  path: 'nuggets',
  component: lazyRouteComponent(() => import('@/screens/forge/nuggets'), 'NuggetsScreen'),
})
const forgeWorkbench = createRoute({
  getParentRoute: () => forge,
  path: 'workbench',
  // forge: a template/field set/nugget to preselect in the Template Lab below — what a redirect
  // from the old Template Studio route, or a link such as Workshop's, forwards.
  validateSearch: (search: Record<string, unknown>) => ({
    t: typeof search['t'] === 'string' ? search['t'] : undefined,
    fs: typeof search['fs'] === 'number' ? search['fs'] : undefined,
    n: typeof search['n'] === 'string' ? search['n'] : undefined,
  }),
  component: lazyRouteComponent(() => import('@/screens/forge/workbench'), 'WorkbenchScreen'),
})
const forgeAnalytics = createRoute({
  getParentRoute: () => forge,
  path: 'analytics',
  // The alpha id travels in the URL, so a link from the Board or a reload lands on the same one.
  validateSearch: (search: Record<string, unknown>) => ({
    alpha: typeof search['alpha'] === 'string' ? search['alpha'] : undefined,
  }),
  component: lazyRouteComponent(() => import('@/screens/forge/analytics'), 'AnalyticsScreen'),
})
const forgeDataset = createRoute({
  getParentRoute: () => forge,
  path: 'datasets/$datasetId',
  // The dataset is only meaningful in one market, so the scope travels in the URL.
  validateSearch: (search: Record<string, unknown>) => ({
    instrumentType:
      typeof search['instrumentType'] === 'string' ? search['instrumentType'] : INSTRUMENT_TYPE,
    region: typeof search['region'] === 'string' ? search['region'] : DEFAULT_SCOPE.region,
    delay: typeof search['delay'] === 'number' ? search['delay'] : DEFAULT_SCOPE.delay,
    universe: typeof search['universe'] === 'string' ? search['universe'] : DEFAULT_SCOPE.universe,
  }),
  component: lazyRouteComponent(() => import('@/screens/forge/datasets'), 'DatasetScreen'),
})

const tasks = createRoute({ getParentRoute: () => root, path: '/tasks' })
const tasksIndex = createRoute({
  getParentRoute: () => tasks,
  path: '/',
  // In the address, so a reload or the way back from a task's results lands where it was.
  // forge: a task id is a Study id (number) for a lab task, or a Forge run's own string id —
  // see screens/forge/tasks-bridge.ts.
  validateSearch: (
    search: Record<string, unknown>,
  ): { view?: 'submittable'; task?: number | string } => ({
    ...(search['view'] === 'submittable' ? { view: 'submittable' as const } : {}),
    ...(typeof search['task'] === 'number' || typeof search['task'] === 'string'
      ? { task: search['task'] }
      : {}),
  }),
  component: lazyRouteComponent(() => import('@/screens/tasks'), 'TasksScreen'),
})
/** One task's whole result set, with room for the statistics the card cannot hold. */
const taskResults = createRoute({
  getParentRoute: () => tasks,
  path: '$taskId',
  component: lazyRouteComponent(() => import('@/screens/tasks/results'), 'TaskResultsScreen'),
})

const pool = createRoute({ getParentRoute: () => root, path: '/pool' })
const poolIndex = createRoute({
  getParentRoute: () => pool,
  path: '/',
  beforeLoad: () => {
    throw redirect({ to: '/pool/$tab', params: { tab: 'stored' } })
  },
})
const poolTab = createRoute({
  getParentRoute: () => pool,
  path: '$tab',
  component: lazyRouteComponent(() => import('@/screens/pool'), 'PoolScreen'),
})

const portfolio = createRoute({
  getParentRoute: () => root,
  path: '/portfolio',
  component: lazyRouteComponent(() => import('@/screens/portfolio'), 'PortfolioScreen'),
})

const alpha = createRoute({
  getParentRoute: () => root,
  path: '/alpha/$alphaId',
  component: lazyRouteComponent(() => import('@/screens/alpha'), 'AlphaScreen'),
})

// The screen belongs to ``/ai`` itself; the two routes below only carry the params it reads.
// Mounting it on each of them instead remounts the screen when a reply moves the URL to its
// new thread, losing what that reply just said.
const ai = createRoute({
  getParentRoute: () => root,
  path: '/ai',
  component: lazyRouteComponent(() => import('@/screens/ai'), 'AiScreen'),
})
const aiIndex = createRoute({
  getParentRoute: () => ai,
  path: '/',
  beforeLoad: () => {
    throw redirect({ to: '/ai/$tab', params: { tab: 'providers' } })
  },
})
const aiTab = createRoute({ getParentRoute: () => ai, path: '$tab' })
const aiThread = createRoute({ getParentRoute: () => ai, path: 'assistant/$threadId' })

const competitionsRoute = createRoute({
  getParentRoute: () => root,
  path: '/competitions',
  component: lazyRouteComponent(() => import('@/screens/competitions'), 'CompetitionsScreen'),
})

const sync = createRoute({
  getParentRoute: () => root,
  path: '/sync',
  component: lazyRouteComponent(() => import('@/screens/sync'), 'SyncScreen'),
})

const pyramids = createRoute({
  getParentRoute: () => root,
  path: '/pyramids',
  component: lazyRouteComponent(() => import('@/screens/pyramids'), 'PyramidsScreen'),
})

// forge: exported so the smoke test can mount each route on a memory history of its own.
export const routeTree = root.addChildren([
  index,
  dashboard,
  matrix,
  data,
  labs.addChildren([labsIndex, searchLab, templateLab, evolutionLab, powerPoolLab, superAlphaLab]),
  tools.addChildren([toolsIndex, settingsSampler, correlationBreaker, fieldsSampler]),
  forge.addChildren([
    forgeToday,
    forgeGym,
    forgeTemplates,
    forgeMyTemplates,
    forgeStudio,
    forgePipelines,
    forgeBoard,
    forgeBreakdown,
    forgeSubmitHelper,
    forgeWorkshop,
    forgeFieldSets,
    forgeNuggets,
    forgeWorkbench,
    forgeDataset,
    forgeAnalytics,
  ]), // forge: Forge section
  competitionsRoute,
  tasks.addChildren([tasksIndex, taskResults]),
  pool.addChildren([poolIndex, poolTab]),
  portfolio,
  alpha,
  ai.addChildren([aiIndex, aiTab, aiThread]),
  pyramids,
  sync,
])

/** A screen that throws says so instead of going blank (CLAUDE.md anti-goal 3), and offers
 * the way back: a render that failed on a half-loaded query succeeds on a second try. */
function RouteError({ error, reset }: ErrorComponentProps) {
  return (
    <Page>
      <ErrorNotice error={error} title="This screen failed to render" />
      <Button onClick={reset}>Try again</Button>
    </Page>
  )
}

function NotFound() {
  return (
    <Page>
      <Panel>
        <Empty title="There is no screen here">
          Use the sidebar, or press {MOD}K to go to a screen or lab.
        </Empty>
      </Panel>
    </Page>
  )
}

// forge: a factory so the smoke test mounts the same router the app does — same error and
// not-found components — on a memory history of its own. The app's router is the default one.
export const createAppRouter = (options: { history?: RouterHistory } = {}) =>
  createRouter({
    routeTree,
    defaultPreload: 'intent',
    defaultErrorComponent: RouteError,
    defaultNotFoundComponent: NotFound,
    ...options,
  })

export const router = createAppRouter()

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
