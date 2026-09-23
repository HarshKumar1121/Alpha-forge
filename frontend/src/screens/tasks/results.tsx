/**
 * One task's whole result set, with the statistics the task card has no room for.
 *
 * Built for a Settings Sampler sweep, which runs the same expression across many markets: the
 * question there is not "which Alpha is best" but "where does this idea work", and that is a
 * comparison across regions rather than a leaderboard.
 */

import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from '@tanstack/react-router'
import { ArrowLeftIcon } from 'lucide-react'
import { useMemo, useState } from 'react'
import { cn } from '@/lib/cn'
import { DASH, fmt } from '@/lib/format'
import { useRefetchOn } from '@/lib/ws'
import { AstInspector } from '@/screens/pool/shared'
import { AlphaPane } from '@/screens/tasks/alpha-pane'
import { labTasks, type RankedAlpha } from '@/screens/tasks/api'
import {
  AFTER_COST_HEADER,
  DELAY,
  FAILED_CHECKS,
  INVESTABILITY,
  investability,
} from '@/screens/tasks/columns'
import {
  Button,
  Empty,
  ErrorNotice,
  KV,
  Metric,
  Page,
  PageHeader,
  Panel,
  Skeleton,
  signTone,
  TEXT_TONE,
} from '@/ui/kit'
import { type Column, DataTable, type Sort } from '@/ui/table'

/** The sweep's whole result set, not a page of it: the comparison needs every row. */
const LIMIT = 2000

const setting = (r: RankedAlpha, key: string) => String(r.settings?.[key] ?? '')
const market = (r: RankedAlpha) => setting(r, 'region') || DASH

/** Sorted client-side: the rows are already here, and a sweep is thousands at most. */
function compare(a: RankedAlpha, b: RankedAlpha, sort: Sort): number {
  const pick = (r: RankedAlpha): string | number | null => {
    switch (sort.key) {
      case 'region':
        return market(r)
      case 'universe':
        return setting(r, 'universe')
      case 'neutralization':
        return setting(r, 'neutralization')
      case 'failed':
        return r.failedChecks.length
      case 'investability':
        return investability(r)
      case 'delay':
        return (r.settings?.['delay'] as number | undefined) ?? null
      default:
        return (r as unknown as Record<string, number | null>)[sort.key] ?? null
    }
  }
  const x = pick(a)
  const y = pick(b)
  if (x == null) return y == null ? 0 : 1
  if (y == null) return -1
  const order = typeof x === 'string' ? String(x).localeCompare(String(y)) : Number(x) - Number(y)
  return sort.desc ? -order : order
}

interface MetricColumn {
  key: keyof RankedAlpha
  label: string
  show: (v: number | null) => string
  /** Which end of the range is the good one, for the across-regions "best" columns. */
  best: 'max' | 'min'
}

const METRICS: MetricColumn[] = [
  { key: 'sharpe', label: 'Sharpe', show: (v) => fmt.ratio(v), best: 'max' },
  // Less trading is better: every submittable Alpha already clears BRAIN's 1% floor, so the
  // smallest turnover among them is the cheapest to hold, not an Alpha that barely trades.
  { key: 'turnover', label: 'Turnover', show: (v) => fmt.pct(v, 2), best: 'min' },
  { key: 'fitness', label: 'Fitness', show: (v) => fmt.ratio(v), best: 'max' },
  { key: 'returns', label: 'Returns', show: (v) => fmt.pct(v, 2), best: 'max' },
  { key: 'drawdown', label: 'Drawdown', show: (v) => fmt.pct(v, 2), best: 'min' },
  { key: 'margin', label: 'Margin', show: (v) => fmt.bps(v, 2), best: 'max' },
  { key: 'afterCostSharpe', label: 'After-Cost Sharpe', show: (v) => fmt.ratio(v), best: 'max' },
]

const SIGNED = new Set(['sharpe', 'fitness', 'returns', 'margin', 'afterCostSharpe'])

function columns(): Column<RankedAlpha>[] {
  return [
    {
      key: 'region',
      header: 'Region',
      width: 'minmax(70px,0.7fr)',
      sortable: true,
      cell: (r) => <span className="num">{market(r)}</span>,
    },
    DELAY,
    {
      key: 'universe',
      header: 'Universe',
      width: 'minmax(84px,0.9fr)',
      sortable: true,
      cell: (r) => <span className="num truncate">{setting(r, 'universe') || DASH}</span>,
    },
    {
      key: 'neutralization',
      header: 'Neutralization',
      width: 'minmax(96px,1fr)',
      sortable: true,
      cell: (r) => <span className="num truncate">{setting(r, 'neutralization') || DASH}</span>,
    },
    INVESTABILITY,
    FAILED_CHECKS,
    ...METRICS.map(
      (m): Column<RankedAlpha> => ({
        key: String(m.key),
        header: m.key === 'afterCostSharpe' ? AFTER_COST_HEADER : m.label,
        width: 'minmax(84px,0.8fr)',
        align: 'right',
        sortable: true,
        cell: (r) => {
          const value = r[m.key] as number | null
          return (
            <span className={cn('num', SIGNED.has(String(m.key)) && TEXT_TONE[signTone(value)])}>
              {m.show(value)}
            </span>
          )
        },
      }),
    ),
  ]
}

/**
 * What a reader can narrow the table by. Three independent questions, each answerable on its
 * own, so any combination of them is a valid thing to ask: the useful one is all three at
 * once, but "which of my refused Alphas still survive costs" is a real question too and a
 * fixed set of presets could not express it.
 */
/** One row of the across-regions table. */
interface RegionRow {
  region: string
  alphas: number
  submittable: number
  failed: number
  /** Per metric, the best any *submittable* Alpha here reached, keyed by its column key. */
  best: Record<string, number | null>
}

/** One distinct set of failed checks, and how much of the sweep landed on exactly that set. */
interface CheckSet {
  key: string
  checks: string[]
  alphas: number
  share: number
}

/**
 * What is actually stopping this idea, counted by the *combination* of checks rather than by
 * check. One Alpha usually trips several at once, so a per-check tally double-counts and
 * makes every sweep look like the same wall of Sharpe failures. The sets separate "only the
 * turnover is wrong", which a decay or a gate can fix, from "the signal is weak everywhere".
 */
function CheckSets({ rows }: { rows: RankedAlpha[] }) {
  const [sort, setSort] = useState<Sort>({ key: 'alphas', desc: true })

  const sets = useMemo(() => {
    const seen = new Map<string, CheckSet>()
    for (const r of rows) {
      const checks = [...r.refusedBy].sort()
      const key = checks.join(' + ')
      const found = seen.get(key) ?? { key, checks, alphas: 0, share: 0 }
      found.alphas += 1
      seen.set(key, found)
    }
    const total = rows.length || 1
    return [...seen.values()].map((s) => ({ ...s, share: s.alphas / total }))
  }, [rows])

  const sorted = useMemo(() => {
    const pick = (s: CheckSet): string | number =>
      sort.key === 'key' ? s.key : sort.key === 'count' ? s.checks.length : s.alphas
    return [...sets].sort((a, b) => {
      const x = pick(a)
      const y = pick(b)
      const order = typeof x === 'string' ? x.localeCompare(String(y)) : Number(x) - Number(y)
      return sort.desc ? -order : order
    })
  }, [sets, sort])

  return (
    <Panel
      title="Checks Failed Together"
      description="Every distinct set of refusing checks, and how many Alphas hit exactly that set. Sort by Checks to find the ones a single problem is holding back."
      actions={<span className="num text-ink-subtle">{fmt.int(sets.length)} sets</span>}
    >
      <DataTable
        label="Failed check sets"
        rows={sorted}
        rowKey={(s) => s.key || 'none'}
        sort={sort}
        onSort={setSort}
        columns={[
          {
            key: 'key',
            header: 'Checks Failed',
            width: 'minmax(260px,4fr)',
            sortable: true,
            cell: (s) =>
              s.checks.length === 0 ? (
                <span className="text-ink-subtle">{DASH}</span>
              ) : (
                <span className="num truncate text-pnl-negative" title={s.checks.join(', ')}>
                  {s.checks.join(', ')}
                </span>
              ),
          },
          {
            key: 'count',
            header: 'Checks',
            width: 'minmax(72px,0.6fr)',
            align: 'right',
            sortable: true,
            cell: (s) => <span className="num">{fmt.int(s.checks.length)}</span>,
          },
          {
            key: 'alphas',
            header: 'Alphas',
            width: 'minmax(76px,0.6fr)',
            align: 'right',
            sortable: true,
            cell: (s) => (
              <span className={cn('num', s.checks.length === 0 && 'text-pnl-positive')}>
                {fmt.int(s.alphas)}
              </span>
            ),
          },
          {
            key: 'share',
            header: 'Share',
            width: 'minmax(76px,0.6fr)',
            align: 'right',
            cell: (s) => <span className="num text-ink-subtle">{fmt.pct(s.share, 1)}</span>,
          },
        ]}
        maxHeight="40vh"
        empty="No Alphas back yet."
      />
    </Panel>
  )
}

export function TaskResultsScreen() {
  const { taskId } = useParams({ from: '/tasks/$taskId' })
  const id = Number(taskId)
  const [sort, setSort] = useState<Sort>({ key: 'sharpe', desc: true })

  const tasks = useQuery({ queryKey: ['tasks'], queryFn: labTasks.list })
  const task = tasks.data?.tasks.find((t) => t.id === id)

  const top = useQuery({
    queryKey: ['tasks', id, 'results'],
    queryFn: () => labTasks.top(id, LIMIT),
    enabled: Number.isFinite(id),
  })
  const rows = useMemo(() => top.data ?? [], [top.data])

  const byRegion = useMemo(() => {
    const seen = new Map<string, RegionRow>()
    for (const r of rows) {
      const key = market(r)
      const row = seen.get(key) ?? {
        region: key,
        alphas: 0,
        submittable: 0,
        failed: 0,
        best: {},
      }
      row.alphas += 1
      const green = r.submittable || r.pending
      if (green) row.submittable += 1
      else row.failed += 1
      // Only Alphas nothing refused: a best that BRAIN will not take is not a best, it is a
      // reason to look in this region and be disappointed.
      if (green) {
        for (const m of METRICS) {
          const value = r[m.key] as number | null
          if (value == null) continue
          const held = row.best[m.key]
          const better = held == null || (m.best === 'max' ? value > held : value < held)
          if (better) row.best[m.key] = value
        }
      }
      seen.set(key, row)
    }
    return [...seen.values()].sort((a, b) => b.submittable - a.submittable || b.alphas - a.alphas)
  }, [rows])

  // Shown only when there is one expression to show. A Settings Sampler sweep is the same
  // expression everywhere, which is the whole point of it; a Search Lab task is a thousand
  // different ones and has no single answer to put here.
  // Read off the task, not the results: a Settings Sampler sweep was *given* an expression
  // and the four settings it holds still, so those are facts about the task. Recovering them
  // by scanning a thousand rows for whatever happens to agree is a guess that looks like one.
  const held: [string, string][] = task
    ? (
        [
          ['Decay', task.decay],
          ['Truncation', task.truncation],
          ['NaN Handling', task.nanHandling],
          ['Test Period', task.testPeriod],
        ].filter(([, v]) => v != null && v !== '') as [string, string | number][]
      ).map(([label, value]) => [label, String(value)])
    : []

  // The download reports progress through the task registry, so the rows refresh as its
  // broadcasts land rather than on a timer of their own.
  useRefetchOn('tasks', ['tasks', id, 'results'], 5000)

  const green = rows.filter((r) => r.submittable || r.pending).length
  const pending = rows.filter((r) => r.pending).length

  return (
    <Page>
      <PageHeader
        title={task?.templateName || task?.labName || `Task ${taskId}`}
        // The lab is the title already; repeating it here spent the one line that says
        // where this task got to.
        description={
          task
            ? `${task.status}${task.alphaId ? ` · ${task.alphaId}` : ''}`
            : 'Every Alpha this task produced'
        }
        actions={
          <Button variant="ghost" render={<Link to="/tasks" />}>
            <ArrowLeftIcon />
            Back to Tasks
          </Button>
        }
      />

      {top.isError && <ErrorNotice error={top.error} title="Could not read this task's results" />}

      {task?.expression && (
        <Panel
          title="Alpha Expression"
          description="What every simulation in this task ran, and the settings it held still."
        >
          <div className="flex flex-col gap-4">
            <AstInspector expression={task.expression} />
            {held.length > 0 && (
              <KV
                items={held}
                className="border-t border-hairline pt-4 sm:grid-cols-[repeat(2,auto_minmax(0,1fr))] lg:grid-cols-[repeat(4,auto_minmax(0,1fr))]"
              />
            )}
          </div>
        </Panel>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Metric boxed label="Alphas" value={fmt.int(rows.length)} />
        <Metric
          boxed
          tone="profit"
          label="Submittable"
          value={`${pending > 0 ? '~' : ''}${fmt.int(green)}`}
        />
        <Metric
          boxed
          tone={rows.length - green > 0 ? 'loss' : 'neutral'}
          label="Refused"
          value={fmt.int(rows.length - green)}
        />
        <Metric boxed label="Regions" value={fmt.int(byRegion.length)} />
      </div>

      <Panel
        title="Across Regions"
        description="Where the same expression is worth submitting, and the best each metric reaches there. Refused Alphas are counted, never counted as a best."
      >
        {top.isPending ? (
          <Skeleton className="h-40" />
        ) : byRegion.length === 0 ? (
          <Empty title="No Alphas back yet." />
        ) : (
          <DataTable
            label="Results by region"
            rows={byRegion}
            rowKey={(r) => r.region}
            columns={[
              {
                key: 'region',
                header: 'Region',
                width: 'minmax(80px,1fr)',
                cell: (r) => <span className="num text-ink">{r.region}</span>,
              },
              {
                key: 'alphas',
                header: 'Alphas',
                width: 'minmax(80px,0.8fr)',
                align: 'right',
                cell: (r) => fmt.int(r.alphas),
              },
              {
                key: 'submittable',
                header: 'Submittable',
                width: 'minmax(96px,1fr)',
                align: 'right',
                cell: (r) => (
                  <span className={cn('num', r.submittable > 0 && 'text-pnl-positive')}>
                    {fmt.int(r.submittable)}
                  </span>
                ),
              },
              {
                key: 'failed',
                header: 'Refused',
                width: 'minmax(84px,0.8fr)',
                align: 'right',
                cell: (r) => (
                  <span className={cn('num', r.failed > 0 && 'text-pnl-negative')}>
                    {fmt.int(r.failed)}
                  </span>
                ),
              },
              ...METRICS.map(
                (m): Column<RegionRow> => ({
                  key: String(m.key),
                  header: m.key === 'afterCostSharpe' ? AFTER_COST_HEADER : m.label,
                  width: 'minmax(78px,0.8fr)',
                  align: 'right',
                  cell: (r) => {
                    const value = r.best[m.key] ?? null
                    return (
                      <span
                        className={cn(
                          'num',
                          SIGNED.has(String(m.key)) && TEXT_TONE[signTone(value)],
                        )}
                      >
                        {m.show(value)}
                      </span>
                    )
                  },
                }),
              ),
            ]}
            maxHeight="40vh"
            empty="No Alphas back yet."
          />
        )}
      </Panel>

      <AlphaPane
        title="Every Alpha"
        rows={rows}
        columns={columns}
        compare={compare}
        poolColumnAfter="investability"
        sort={sort}
        onSort={setSort}
        loading={top.isPending}
        error={top.error}
        onRefresh={() => top.refetch()}
      />

      <CheckSets rows={rows} />
    </Page>
  )
}
